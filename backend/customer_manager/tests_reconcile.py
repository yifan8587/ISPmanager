"""S7：支付掉单对账。查单成功才入账，查单失败不得过期。"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager import payment_gateways, payment_service
from customer_manager.models import Customer, CustomerBilling, PaymentTransaction
from system_manager.models import PaymentChannel


class PaymentReconcileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('recon-admin', 'recon@example.test', 'password')
        self.customer = Customer.objects.create(
            name='对账客户',
            contact_name='联系人',
            status='active',
        )
        self.billing = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('100.00'),
            currency='CNY',
            due_date='2026-08-31',
        )
        self.channel = PaymentChannel(provider='alipay', name='支付宝', enabled=True)
        self.channel.set_config({
            'app_id': '2026000000000001',
            'app_private_key': 'private-key',
            'alipay_public_key': 'public-key',
        })
        self.channel.save()

    def _pending(self, *, expired=True, status=PaymentTransaction.Status.PENDING):
        payment = PaymentTransaction.objects.create(
            billing=self.billing,
            channel=self.channel,
            provider='alipay',
            amount=Decimal('100.00'),
            currency='CNY',
            billing_amount=Decimal('100.00'),
            billing_currency='CNY',
            status=status,
            provider_order_id='provider-order-1',
            initiated_by=self.user,
            expires_at=timezone.now() - timedelta(minutes=5) if expired else timezone.now() + timedelta(hours=1),
        )
        return payment

    def _paid_result(self, payment, **overrides):
        data = dict(
            transaction_no=payment.transaction_no,
            provider_order_id='provider-order-1',
            provider_payment_id='alipay-trade-1',
            paid_amount=Decimal('100.00'),
            paid_currency='CNY',
            succeeded=True,
            response={'trade_status': 'TRADE_SUCCESS'},
        )
        data.update(overrides)
        return payment_gateways.GatewayResult(**data)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_expired_paid_at_gateway_is_settled(self, query_payment):
        payment = self._pending(expired=True)
        query_payment.return_value = self._paid_result(payment)

        result = payment_service.reconcile_payment_transaction(payment)
        payment.refresh_from_db()
        self.billing.refresh_from_db()

        self.assertEqual(result['action'], 'paid')
        self.assertEqual(payment.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)
        self.assertEqual(self.billing.paid_amount, Decimal('100.00'))
        self.assertEqual(payment.last_reconcile_status, 'paid')
        self.assertTrue(payment.last_reconciled_at)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_expired_failed_at_gateway_is_failed(self, query_payment):
        payment = self._pending(expired=True)
        query_payment.return_value = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            failed=True,
            failure_reason='支付宝交易已关闭',
        )

        result = payment_service.reconcile_payment_transaction(payment)
        payment.refresh_from_db()
        self.billing.refresh_from_db()

        self.assertEqual(result['action'], 'failed')
        self.assertEqual(payment.status, PaymentTransaction.Status.FAILED)
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)
        self.assertEqual(self.billing.paid_amount, Decimal('0.00'))

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_expired_still_processing_is_expired(self, query_payment):
        payment = self._pending(expired=True)
        query_payment.return_value = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            processing=True,
        )

        result = payment_service.reconcile_payment_transaction(payment)
        payment.refresh_from_db()

        self.assertEqual(result['action'], 'expired')
        self.assertEqual(payment.status, PaymentTransaction.Status.EXPIRED)
        self.assertEqual(payment.last_reconcile_status, 'expired')

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_query_error_does_not_expire(self, query_payment):
        payment = self._pending(expired=True)
        query_payment.side_effect = payment_gateways.PaymentGatewayError('支付宝查单失败：系统繁忙')

        result = payment_service.reconcile_payment_transaction(payment)
        payment.refresh_from_db()
        self.billing.refresh_from_db()

        self.assertEqual(result['action'], 'error')
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)
        self.assertEqual(payment.last_reconcile_status, 'error')
        self.assertIn('系统繁忙', payment.last_reconcile_error)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_succeeded_reconcile_is_idempotent(self, query_payment):
        payment = self._pending(expired=True)
        result = self._paid_result(payment)
        query_payment.return_value = result
        payment_service.reconcile_payment_transaction(payment)
        payment_service.reconcile_payment_transaction(payment)
        payment.refresh_from_db()
        self.billing.refresh_from_db()

        self.assertEqual(payment.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(self.billing.paid_amount, Decimal('100.00'))
        self.assertEqual(
            PaymentTransaction.objects.filter(
                billing=self.billing,
                status=PaymentTransaction.Status.SUCCEEDED,
            ).count(),
            1,
        )

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_mark_expired_queries_gateway_first(self, query_payment):
        paid = self._pending(expired=True)
        unpaid = PaymentTransaction.objects.create(
            billing=self.billing,
            channel=self.channel,
            provider='alipay',
            amount=Decimal('100.00'),
            currency='CNY',
            billing_amount=Decimal('100.00'),
            billing_currency='CNY',
            status=PaymentTransaction.Status.PENDING,
            initiated_by=self.user,
            expires_at=timezone.now() - timedelta(minutes=3),
        )

        def _query(transaction, channel):
            if transaction.pk == paid.pk:
                return self._paid_result(transaction)
            return payment_gateways.GatewayResult(
                transaction_no=transaction.transaction_no,
                processing=True,
            )

        query_payment.side_effect = _query
        expired_count = payment_service.mark_expired_transactions()
        paid.refresh_from_db()
        unpaid.refresh_from_db()
        self.billing.refresh_from_db()

        self.assertEqual(expired_count, 1)
        self.assertEqual(paid.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(unpaid.status, PaymentTransaction.Status.EXPIRED)
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_recent_reconcile_is_skipped_unless_force(self, query_payment):
        payment = self._pending(expired=False)
        query_payment.return_value = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            processing=True,
        )
        first = payment_service.reconcile_payment_transaction(payment)
        second = payment_service.reconcile_payment_transaction(payment)
        forced = payment_service.reconcile_payment_transaction(payment, force=True)

        self.assertEqual(first['action'], 'pending')
        self.assertEqual(second['action'], 'skipped')
        self.assertEqual(second['reason'], 'recently_reconciled')
        self.assertEqual(forced['action'], 'pending')
        self.assertEqual(query_payment.call_count, 2)


class PaymentReconcileApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('recon-api-admin', 'a@example.test', 'password')
        self.user = User.objects.create_user('recon-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='门户对账客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.billing = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('80.00'),
            currency='CNY',
            due_date='2026-08-31',
        )
        self.channel = PaymentChannel(provider='alipay', name='支付宝', enabled=True)
        self.channel.set_config({
            'app_id': '2026000000000001',
            'app_private_key': 'private-key',
            'alipay_public_key': 'public-key',
        })
        self.channel.save()
        self.payment = PaymentTransaction.objects.create(
            billing=self.billing,
            channel=self.channel,
            provider='alipay',
            amount=Decimal('80.00'),
            currency='CNY',
            billing_amount=Decimal('80.00'),
            billing_currency='CNY',
            status=PaymentTransaction.Status.PENDING,
            provider_order_id='provider-order-api',
            initiated_by=self.user,
            expires_at=timezone.now() - timedelta(minutes=2),
        )
        self.portal = APIClient()
        self.portal.force_authenticate(self.user)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_retrieve_auto_reconciles_paid_drop(self, query_payment):
        query_payment.return_value = payment_gateways.GatewayResult(
            transaction_no=self.payment.transaction_no,
            provider_order_id='provider-order-api',
            provider_payment_id='alipay-trade-api',
            paid_amount=Decimal('80.00'),
            paid_currency='CNY',
            succeeded=True,
        )
        resp = self.portal.get(f'/api/customer/payment-transactions/{self.payment.transaction_no}/')
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], PaymentTransaction.Status.SUCCEEDED)
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_customer_cannot_force_reconcile(self, query_payment):
        query_payment.return_value = payment_gateways.GatewayResult(processing=True)
        resp = self.portal.post(
            f'/api/customer/payment-transactions/{self.payment.transaction_no}/reconcile/',
        )
        self.assertEqual(resp.status_code, 403)
        query_payment.assert_not_called()

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    def test_admin_can_force_reconcile(self, query_payment):
        query_payment.return_value = payment_gateways.GatewayResult(
            transaction_no=self.payment.transaction_no,
            provider_order_id='provider-order-api',
            provider_payment_id='alipay-trade-admin',
            paid_amount=Decimal('80.00'),
            paid_currency='CNY',
            succeeded=True,
        )
        resp = self.admin_client.post(
            f'/api/customer/payment-transactions/{self.payment.transaction_no}/reconcile/',
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data['status'], PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(resp.data['last_reconcile_status'], 'paid')
