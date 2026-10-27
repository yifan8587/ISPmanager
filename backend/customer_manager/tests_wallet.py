"""S6：客户余额充值、抵扣、幂等入账与退回。"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager import payment_gateways, payment_service
from customer_manager.models import (
    Customer,
    CustomerBalanceLedger,
    CustomerBilling,
    ChannelCommission,
    CustomerReferral,
    PaymentTransaction,
)
from customer_manager.refund_engine import mark_manual_refund_completed
from customer_manager.wallet import (
    WalletError,
    apply_balance_to_billing,
    create_recharge_billing,
    credit_paid_recharge,
    debit_refunded_recharge,
)
from system_manager.models import PaymentChannel


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('wallet-owner', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='余额客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )

    def test_create_recharge_billing(self):
        billing = create_recharge_billing(self.customer, amount='50.00', operator=self.user)
        self.assertEqual(billing.billing_kind, CustomerBilling.BillingKind.WALLET_RECHARGE)
        self.assertEqual(billing.amount, Decimal('50.00'))
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.UNPAID)
        self.assertFalse(billing.auto_generated)

    def test_credit_paid_recharge_is_idempotent(self):
        billing = create_recharge_billing(self.customer, amount='80.00', operator=self.user)
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = Decimal('80.00')
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])

        first = credit_paid_recharge(billing, operator=self.user)
        second = credit_paid_recharge(billing, operator=self.user)
        self.customer.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.customer.balance, Decimal('80.00'))
        self.assertEqual(
            CustomerBalanceLedger.objects.filter(
                customer=self.customer,
                entry_type=CustomerBalanceLedger.EntryType.RECHARGE,
            ).count(),
            1,
        )

    def test_apply_balance_and_forbid_recharge_loop(self):
        self.customer.balance = Decimal('40.00')
        self.customer.save(update_fields=['balance'])
        monthly = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('100.00'),
            currency='CNY',
            due_date=date(2026, 8, 31),
        )
        monthly = apply_balance_to_billing(monthly, operator=self.user)
        self.assertEqual(monthly.paid_amount, Decimal('40.00'))
        self.assertEqual(monthly.status, CustomerBilling.BillingStatus.UNPAID)
        self.assertEqual(monthly.payment_method, CustomerBilling.PaymentMethod.BALANCE)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))

        recharge = create_recharge_billing(self.customer, amount='20.00')
        with self.assertRaises(WalletError):
            apply_balance_to_billing(recharge, operator=self.user)

    def test_reject_foreign_currency_bill(self):
        self.customer.balance = Decimal('10.00')
        self.customer.save(update_fields=['balance'])
        usd = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('10.00'),
            currency='USD',
            due_date=date(2026, 8, 31),
        )
        with self.assertRaises(WalletError):
            apply_balance_to_billing(usd, operator=self.user)

    def test_refund_claws_back_once(self):
        billing = create_recharge_billing(self.customer, amount='30.00')
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = Decimal('30.00')
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        credit_paid_recharge(billing)
        billing.status = CustomerBilling.BillingStatus.REFUNDED
        billing.refunded_amount = Decimal('30.00')
        billing.save(update_fields=['status', 'refunded_amount', 'updated_at'])

        first = debit_refunded_recharge(billing, operator=self.user)
        second = debit_refunded_recharge(billing, operator=self.user)
        self.customer.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.customer.balance, Decimal('0.00'))

    def test_gateway_success_credits_wallet(self):
        billing = create_recharge_billing(self.customer, amount='60.00')
        channel = PaymentChannel(provider='alipay', name='支付宝', enabled=True)
        channel.set_config({
            'app_id': '2026000000000001',
            'app_private_key': 'private-key',
            'alipay_public_key': 'public-key',
        })
        channel.save()
        payment = PaymentTransaction.objects.create(
            billing=billing,
            channel=channel,
            provider='alipay',
            amount=Decimal('60.00'),
            currency='CNY',
            status=PaymentTransaction.Status.PENDING,
            provider_order_id='recharge-order-1',
            initiated_by=self.user,
        )
        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            provider_order_id='recharge-order-1',
            provider_payment_id='alipay-recharge-1',
            paid_amount=Decimal('60.00'),
            paid_currency='CNY',
            succeeded=True,
            response={'trade_status': 'TRADE_SUCCESS'},
        )
        payment_service.apply_gateway_result(payment, result, callback=True)
        self.customer.refresh_from_db()
        billing.refresh_from_db()
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.PAID)
        self.assertEqual(self.customer.balance, Decimal('60.00'))
        payment_service.apply_gateway_result(payment, result, callback=True)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('60.00'))

    def test_recharge_skips_commission(self):
        channel_user = User.objects.create_user('channel-wallet', password='SafePass_2026!')
        channel = Customer.objects.create(
            name='渠道客户',
            contact_name='渠道',
            account=channel_user,
            customer_type='channel',
            status='active',
        )
        from customer_manager.models import ChannelProfile
        ChannelProfile.objects.create(
            customer=channel,
            promotion_code='WALLETCH',
            commission_rate=Decimal('15.00'),
        )
        CustomerReferral.objects.create(
            customer=self.customer,
            channel=channel,
            promotion_code='WALLETCH',
            commission_rate=Decimal('15.00'),
        )
        billing = create_recharge_billing(self.customer, amount='100.00')
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = Decimal('100.00')
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        from customer_manager.channel_services import ensure_paid_billing_commission
        self.assertIsNone(ensure_paid_billing_commission(billing, operator=self.user))
        self.assertFalse(ChannelCommission.objects.filter(billing=billing).exists())


class WalletPortalApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('portal-wallet', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='门户余额客户',
            contact_name='联系人',
            account=self.user,
            status='active',
            balance=Decimal('25.00'),
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_recharge_and_wallet_endpoints(self):
        resp = self.client.post(
            '/api/portal/bills/recharge/',
            {'amount': '12.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data['data']['billing_kind'], 'wallet_recharge')
        recharge_id = resp.data['data']['id']

        monthly = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('10.00'),
            currency='CNY',
            due_date=date(2026, 8, 31),
        )
        pay = self.client.post(f'/api/portal/bills/{monthly.pk}/pay-with-balance/', format='json')
        self.assertEqual(pay.status_code, 200, pay.data)
        monthly.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(monthly.status, CustomerBilling.BillingStatus.PAID)
        self.assertEqual(self.customer.balance, Decimal('15.00'))

        blocked = self.client.post(
            f'/api/portal/bills/{recharge_id}/pay-with-balance/',
            format='json',
        )
        self.assertEqual(blocked.status_code, 400, blocked.data)

        wallet = self.client.get('/api/portal/bills/wallet/')
        self.assertEqual(wallet.status_code, 200, wallet.data)
        self.assertEqual(Decimal(str(wallet.data['data']['balance'])), Decimal('15.00'))
        self.assertTrue(wallet.data['data']['ledgers'])


class WalletAdminApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('wallet-admin', 'a@example.com', 'password')
        self.customer = Customer.objects.create(
            name='后台调账客户',
            contact_name='联系人',
            status='active',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_adjust_and_ledger(self):
        resp = self.client.post(
            f'/api/customer/customers/{self.customer.pk}/adjust-balance/',
            {'delta': '33.50', 'remark': '测试调增'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Decimal(str(resp.data['balance'])), Decimal('33.50'))
        ledger = self.client.get(f'/api/customer/customers/{self.customer.pk}/wallet-ledger/')
        self.assertEqual(ledger.status_code, 200, ledger.data)
        rows = ledger.data['results'] if isinstance(ledger.data, dict) and 'results' in ledger.data else ledger.data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['entry_type'], 'adjust')

    def test_cannot_patch_balance(self):
        resp = self.client.patch(
            f'/api/customer/customers/{self.customer.pk}/',
            {'balance': '999.00', 'name': self.customer.name, 'contact_name': '联系人'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))

    def test_manual_refund_debits_wallet(self):
        billing = create_recharge_billing(self.customer, amount='20.00')
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = Decimal('20.00')
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        credit_paid_recharge(billing)
        billing.status = CustomerBilling.BillingStatus.REFUNDING
        billing.save(update_fields=['status', 'updated_at'])
        mark_manual_refund_completed(billing.pk, operator=self.admin)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.balance, Decimal('0.00'))
