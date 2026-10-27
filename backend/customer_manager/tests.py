import datetime
from decimal import Decimal
import json
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from system_manager.models import PaymentChannel
from accounts.models import UserProfile

from . import billing_engine, payment_gateways, payment_service
from .models import (
    Customer,
    CustomerBilling,
    BillingItem,
    ChannelCommission,
    ChannelFinanceAccount,
    ChannelFinanceLedger,
    ChannelPayoutProfile,
    ChannelSettlement,
    PaymentTransaction,
    PaymentWebhookEvent,
)


class OnlinePaymentSettlementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        self.customer = Customer.objects.create(
            name='测试客户',
            contact_name='测试联系人',
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

    def _pending_payment(self, amount='100.00'):
        return PaymentTransaction.objects.create(
            billing=self.billing,
            channel=self.channel,
            provider='alipay',
            amount=Decimal(amount),
            currency='CNY',
            status=PaymentTransaction.Status.PENDING,
            provider_order_id='provider-order-1',
            initiated_by=self.user,
        )

    def test_successful_gateway_result_is_idempotent_and_updates_bill(self):
        payment = self._pending_payment()
        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            provider_order_id='provider-order-1',
            provider_payment_id='alipay-trade-1',
            paid_amount=Decimal('100.00'),
            paid_currency='CNY',
            succeeded=True,
            response={'trade_status': 'TRADE_SUCCESS'},
        )

        payment_service.apply_gateway_result(payment, result, callback=True)
        self.billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('100.00'))
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)
        self.assertEqual(self.billing.payment_method, 'alipay')
        self.assertEqual(payment.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertEqual(payment.callback_count, 1)

        payment_service.apply_gateway_result(payment, result, callback=True)
        self.billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('100.00'))
        self.assertEqual(payment.callback_count, 2)

    def test_mismatched_amount_never_updates_bill(self):
        payment = self._pending_payment()
        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            provider_order_id='provider-order-1',
            paid_amount=Decimal('99.99'),
            paid_currency='CNY',
            succeeded=True,
        )

        with self.assertRaises(payment_service.PaymentServiceError):
            payment_service.apply_gateway_result(payment, result, callback=True)
        self.billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('0.00'))
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)

    def test_available_channels_respects_enablement_and_currency(self):
        usd = PaymentChannel(
            provider='stripe',
            name='美元 Stripe',
            enabled=True,
            allowed_currencies=['USD'],
            settlement_currency='USD',
        )
        usd.set_config({'secret_key': 'sk_test', 'webhook_secret': 'whsec_test'})
        usd.save()
        disabled = PaymentChannel(provider='paypal', name='停用 PayPal', enabled=False)
        disabled.set_config({'client_id': 'id', 'client_secret': 'secret'})
        disabled.save()

        channels = payment_service.available_channels('CNY')
        self.assertEqual(
            [channel.id for channel in channels],
            [self.channel.id, usd.id],
        )

    def test_billing_methods_api_only_returns_enabled_currency_compatible_channels(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get(f'/api/customer/billings/{self.billing.id}/online-payment-methods/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['id'], self.channel.id)
        self.assertNotIn('app_private_key', str(response.data))

    def test_invalid_webhook_signature_cannot_settle_bill(self):
        payment = self._pending_payment()
        client = APIClient()
        response = client.post(
            f'/api/customer/payments/webhooks/{self.channel.id}/',
            {
                'out_trade_no': payment.transaction_no,
                'trade_status': 'TRADE_SUCCESS',
                'total_amount': '100.00',
                'sign': 'forged',
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)
        self.billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('0.00'))
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)

    def test_billing_patch_cannot_bypass_payment_state_machine(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.patch(
            f'/api/customer/billings/{self.billing.id}/',
            {'paid_amount': '100.00', 'status': 'paid', 'payment_method': 'alipay'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('0.00'))
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)

    @patch('customer_manager.billing_engine.compute_customer_month')
    def test_regeneration_is_blocked_while_payment_is_pending(self, compute_customer_month):
        billing = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-09',
            amount=Decimal('100.00'),
            currency='CNY',
            auto_generated=True,
            due_date='2026-09-30',
        )
        PaymentTransaction.objects.create(
            billing=billing,
            channel=self.channel,
            provider='alipay',
            amount=Decimal('100.00'),
            currency='CNY',
            status=PaymentTransaction.Status.PENDING,
            initiated_by=self.user,
        )
        compute_customer_month.return_value = {
            'currency': 'CNY',
            'billable_total': '120.00',
            'items': [],
        }

        with self.assertRaises(billing_engine.BillingGenerationBlocked):
            billing_engine.generate_bill(self.customer, '2026-09')
        billing.refresh_from_db()
        self.assertEqual(billing.amount, Decimal('100.00'))

    @patch('customer_manager.payment_service.payment_gateways.query_payment')
    @patch('customer_manager.payment_service.payment_gateways.create_checkout')
    def test_start_payment_api_creates_persistent_transaction(self, create_checkout, query_payment):
        create_checkout.return_value = {
            'mode': 'redirect',
            'url': 'https://checkout.example.test/session',
            'provider_order_id': 'provider-order-start',
            'response': {'id': 'provider-order-start'},
        }
        query_payment.return_value = payment_gateways.GatewayResult(processing=True)
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            f'/api/customer/billings/{self.billing.id}/start-online-payment/',
            {'channel_id': self.channel.id, 'amount': '100.00'},
            format='json',
            HTTP_ORIGIN='https://admin.example.test',
        )
        self.assertEqual(response.status_code, 201, response.data)
        transaction_no = response.data['transaction']['transaction_no']
        payment = PaymentTransaction.objects.get(transaction_no=transaction_no)
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(payment.provider_order_id, 'provider-order-start')
        self.assertEqual(
            response.data['checkout']['transaction_no'],
            payment.transaction_no,
        )

        status_response = client.get(f'/api/customer/payment-transactions/{transaction_no}/')
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.data['status'], PaymentTransaction.Status.PENDING)

    def test_channel_currency_conversion_uses_rate_snapshot_and_bill_amount(self):
        usd = PaymentChannel(
            provider='stripe',
            name='美元 Stripe',
            enabled=True,
            allowed_currencies=['USD'],
            settlement_currency='USD',
        )
        usd.set_config({'secret_key': 'sk_test', 'webhook_secret': 'whsec_test'})
        usd.save()
        payment = payment_service.create_payment_transaction(
            billing=self.billing,
            channel=usd,
            amount='72.00',
            operator=self.user,
        )
        self.assertEqual(payment.amount, Decimal('10.00'))
        self.assertEqual(payment.currency, 'USD')
        self.assertEqual(payment.billing_amount, Decimal('72.00'))
        self.assertEqual(payment.billing_currency, 'CNY')
        self.assertEqual(payment.exchange_rate, Decimal('0.13888889'))

        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            paid_amount=Decimal('10.00'),
            paid_currency='USD',
            succeeded=True,
        )
        payment_service.apply_gateway_result(payment, result)
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('72.00'))
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)

    def test_channel_without_configured_exchange_rate_is_hidden(self):
        eur = PaymentChannel(
            provider='stripe',
            name='欧元 Stripe',
            enabled=True,
            allowed_currencies=['EUR'],
            settlement_currency='EUR',
        )
        eur.set_config({'secret_key': 'sk_test', 'webhook_secret': 'whsec_test'})
        eur.save()
        channels = payment_service.available_channels('CNY')
        self.assertNotIn(eur.id, [channel.id for channel in channels])


class PayPalContractTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name='PayPal 客户',
            contact_name='联系人',
            status='active',
        )
        self.billing = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('20.00'),
            currency='USD',
            due_date='2026-08-31',
        )
        self.channel = PaymentChannel(provider='paypal', name='PayPal', enabled=True)
        self.channel.set_config({
            'client_id': 'client-id',
            'client_secret': 'client-secret',
            'webhook_id': 'webhook-id',
        })
        self.channel.save()
        self.payment = PaymentTransaction.objects.create(
            billing=self.billing,
            channel=self.channel,
            provider='paypal',
            amount=Decimal('20.00'),
            currency='USD',
            status=PaymentTransaction.Status.PENDING,
            provider_order_id='ORDER-1',
        )

    @staticmethod
    def _response(payload):
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    @patch('customer_manager.payment_gateways.requests.post')
    def test_create_order_uses_invoice_and_stable_request_id(self, post):
        post.side_effect = [
            self._response({'access_token': 'token'}),
            self._response({
                'id': 'ORDER-1',
                'status': 'CREATED',
                'links': [{'rel': 'approve', 'href': 'https://paypal.test/approve'}],
            }),
        ]
        result = payment_gateways._paypal_checkout(
            self.payment,
            self.channel,
            'https://api.test/webhook',
            'https://admin.test/customer/billings',
        )
        request = post.call_args_list[1]
        purchase = request.kwargs['json']['purchase_units'][0]
        self.assertEqual(purchase['invoice_id'], self.billing.bill_no)
        self.assertEqual(purchase['custom_id'], self.payment.transaction_no)
        self.assertEqual(request.kwargs['headers']['Prefer'], 'return=representation')
        self.assertLessEqual(len(request.kwargs['headers']['PayPal-Request-Id']), 38)
        self.assertEqual(result['provider_order_id'], 'ORDER-1')

    @patch('customer_manager.payment_gateways.requests.post')
    def test_capture_success_requires_completed_capture(self, post):
        post.side_effect = [
            self._response({'access_token': 'token'}),
            self._response({
                'id': 'ORDER-1',
                'status': 'COMPLETED',
                'purchase_units': [{
                    'reference_id': self.payment.transaction_no,
                    'payments': {'captures': [{
                        'id': 'CAPTURE-1',
                        'status': 'PENDING',
                        'amount': {'value': '20.00', 'currency_code': 'USD'},
                    }]},
                }],
            }),
        ]
        result = payment_gateways.capture_paypal_order(
            self.payment,
            self.channel,
            'ORDER-1',
        )
        self.assertFalse(result.succeeded)
        self.assertTrue(result.processing)
        capture_headers = post.call_args_list[1].kwargs['headers']
        self.assertNotEqual(
            capture_headers['PayPal-Request-Id'],
            payment_gateways._paypal_request_id(self.payment.transaction_no, 'create'),
        )

    @patch('customer_manager.payment_gateways.requests.post')
    def test_invalid_paypal_webhook_signature_is_rejected(self, post):
        post.side_effect = [
            self._response({'access_token': 'token'}),
            self._response({'verification_status': 'FAILURE'}),
        ]
        event = {
            'id': 'WH-INVALID',
            'event_type': 'PAYMENT.CAPTURE.COMPLETED',
            'resource': {},
        }
        with self.assertRaises(payment_gateways.PaymentGatewayError):
            payment_gateways.process_webhook(
                self.channel,
                json.dumps(event).encode(),
                {
                    'PayPal-Transmission-Id': 'transmission',
                    'PayPal-Transmission-Time': 'time',
                    'PayPal-Transmission-Sig': 'signature',
                    'PayPal-Cert-Url': 'https://paypal.test/cert',
                    'PayPal-Auth-Algo': 'SHA256withRSA',
                },
            )
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)

    @patch('customer_manager.payment_service.payment_gateways.process_webhook')
    def test_duplicate_completed_event_settles_only_once(self, process_webhook):
        process_webhook.return_value = payment_gateways.GatewayResult(
            transaction_no=self.payment.transaction_no,
            provider_order_id='ORDER-1',
            provider_payment_id='CAPTURE-1',
            paid_amount=Decimal('20.00'),
            paid_currency='USD',
            succeeded=True,
            webhook_event_id='WH-1',
            webhook_event_type='PAYMENT.CAPTURE.COMPLETED',
            webhook_payload={'id': 'WH-1'},
        )
        payment_service.process_webhook(channel=self.channel, raw_body=b'{}')
        payment_service.process_webhook(channel=self.channel, raw_body=b'{}')
        self.billing.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.billing.paid_amount, Decimal('20.00'))
        self.assertEqual(self.payment.callback_count, 1)
        self.assertEqual(PaymentWebhookEvent.objects.count(), 1)
        self.assertEqual(
            PaymentWebhookEvent.objects.get().status,
            PaymentWebhookEvent.ProcessingStatus.PROCESSED,
        )

    @patch('customer_manager.payment_service.payment_gateways.process_webhook')
    def test_reversed_after_completion_is_audited_without_automatic_unpay(self, process_webhook):
        completed = payment_gateways.GatewayResult(
            transaction_no=self.payment.transaction_no,
            provider_order_id='ORDER-1',
            provider_payment_id='CAPTURE-1',
            paid_amount=Decimal('20.00'),
            paid_currency='USD',
            succeeded=True,
        )
        payment_service.apply_gateway_result(self.payment, completed)
        process_webhook.return_value = payment_gateways.GatewayResult(
            provider_order_id='ORDER-1',
            provider_payment_id='CAPTURE-1',
            failed=True,
            failure_reason='PayPal 事件 PAYMENT.CAPTURE.REVERSED',
            webhook_event_id='WH-REVERSED',
            webhook_event_type='PAYMENT.CAPTURE.REVERSED',
            webhook_payload={'id': 'WH-REVERSED'},
        )
        payment_service.process_webhook(channel=self.channel, raw_body=b'{}')
        self.billing.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)
        self.assertEqual(self.payment.status, PaymentTransaction.Status.SUCCEEDED)
        self.assertIn('人工核对', self.payment.failure_reason)

    @patch('customer_manager.payment_service.payment_gateways.capture_paypal_order')
    @patch('customer_manager.payment_service.payment_gateways.process_webhook')
    def test_approved_event_uses_server_capture_fallback(self, process_webhook, capture):
        process_webhook.return_value = payment_gateways.GatewayResult(
            provider_order_id='ORDER-1',
            processing=True,
            webhook_event_id='WH-APPROVED',
            webhook_event_type='CHECKOUT.ORDER.APPROVED',
            webhook_payload={'id': 'WH-APPROVED'},
        )
        capture.return_value = payment_gateways.GatewayResult(
            transaction_no=self.payment.transaction_no,
            provider_order_id='ORDER-1',
            provider_payment_id='CAPTURE-2',
            paid_amount=Decimal('20.00'),
            paid_currency='USD',
            succeeded=True,
        )
        payment_service.process_webhook(channel=self.channel, raw_body=b'{}')
        capture.assert_called_once()
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.PAID)

    @patch('system_manager.im_services.notify')
    def test_overdue_billings_create_auditable_overdue_notification(self, notify):
        overdue = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-07',
            amount=Decimal('88.00'),
            currency='CNY',
            due_date=timezone.localdate() - datetime.timedelta(days=1),
        )

        with self.captureOnCommitCallbacks(execute=True):
            count = billing_engine.mark_overdue_billings(timezone.localdate())

        overdue.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(overdue.status, CustomerBilling.BillingStatus.OVERDUE)
        self.assertEqual(notify.call_args.args[0], 'billing_overdue')


class ChannelPromotionCommissionTests(TestCase):
    def setUp(self):
        from .channel_services import configure_channel_profile

        self.admin = User.objects.create_superuser('channel-admin', 'channel-admin@example.com', 'password')
        self.channel = Customer.objects.create(
            name='推广渠道 A',
            customer_type='channel',
            status='active',
            contact_name='渠道负责人',
        )
        self.profile = configure_channel_profile(
            self.channel,
            promotion_code='PARTNER2026',
            promotion_discount_rate='10.00',
            commission_rate='15.00',
        )

    def test_registration_billing_discount_and_settlement_flow(self):
        from .channel_services import (
            confirm_channel_settlement,
            create_channel_settlement,
            pay_channel_settlement,
        )

        client = APIClient()
        response = client.post('/api/portal/register/', {
            'username': 'referral-customer',
            'password': 'SafePass_2026!',
            'email': 'referral@example.com',
            'contact_name': '推荐客户',
            'customer_type': 'personal',
            'promotion_code': 'partner2026',
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        customer = Customer.objects.get(account__username='referral-customer')
        referral = customer.channel_referral
        self.assertEqual(referral.channel_id, self.channel.id)
        self.assertEqual(referral.promotion_code, 'PARTNER2026')
        self.assertEqual(referral.promotion_discount_rate, Decimal('10.00'))
        self.assertEqual(referral.commission_rate, Decimal('15.00'))

        client.force_authenticate(self.admin)
        response = client.post(
            '/api/customer/billings/',
            {
                'customer': customer.id,
                'bill_month': '2026-08',
                'amount': '100.00',
                'currency': 'CNY',
                'due_date': '2026-08-31',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        billing = CustomerBilling.objects.get(pk=response.data['id'])
        BillingItem.objects.create(
            billing=billing,
            description='VPS 月租',
            service_type='vps',
            amount=Decimal('90.00'),
            currency='CNY',
        )
        self.assertEqual(billing.gross_amount, Decimal('100.00'))
        self.assertEqual(billing.promotion_discount_amount, Decimal('10.00'))
        self.assertEqual(billing.amount, Decimal('90.00'))
        self.assertEqual(billing.promotion_code, 'PARTNER2026')

        from portal.payment_service import apply_bill_payment

        billing = apply_bill_payment(
            billing,
            Decimal('90.00'),
            'bank_transfer',
            customer.account,
        )
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.PAID)
        commission = ChannelCommission.objects.get(billing=billing)
        self.assertEqual(commission.channel_id, self.channel.id)
        self.assertEqual(commission.commission_base_amount, Decimal('90.00'))
        self.assertEqual(commission.commission_amount, Decimal('13.50'))
        self.assertEqual(commission.status, ChannelCommission.Status.PENDING)
        self.assertEqual(commission.billing_amount, Decimal('90.00'))
        self.assertEqual(commission.gross_amount, Decimal('100.00'))
        self.assertEqual(commission.promotion_discount_amount, Decimal('10.00'))
        self.assertEqual(commission.customer_no, customer.customer_no)
        self.assertEqual(commission.customer_name, customer.name)
        self.assertEqual(commission.billing_period, '2026-08')
        self.assertEqual(commission.service_items_snapshot[0]['service_type'], 'vps')
        self.assertEqual(commission.payment_summary_snapshot['payment_method'], 'bank_transfer')
        account = ChannelFinanceAccount.objects.get(channel=self.channel, currency='CNY')
        self.assertEqual(account.balance, Decimal('13.50'))

        settlement = create_channel_settlement([commission.id], operator=self.admin)
        self.assertEqual(settlement.status, ChannelSettlement.Status.DRAFT)
        self.assertEqual(settlement.channel_no, self.channel.customer_no)
        self.assertEqual(settlement.channel_name, self.channel.name)
        commission.refresh_from_db()
        self.assertEqual(commission.status, ChannelCommission.Status.SETTLING)

        settlement = confirm_channel_settlement(settlement, operator=self.admin)
        self.assertEqual(settlement.status, ChannelSettlement.Status.CONFIRMED)
        settlement = pay_channel_settlement(
            settlement,
            operator=self.admin,
            payment_method='bank_transfer',
            payment_reference='BANK-TEST-001',
        )
        self.assertEqual(settlement.status, ChannelSettlement.Status.PAID)
        commission.refresh_from_db()
        self.assertEqual(commission.status, ChannelCommission.Status.PAID)
        account.refresh_from_db()
        self.assertEqual(account.balance, Decimal('0.00'))
        self.assertEqual(
            list(ChannelFinanceLedger.objects.filter(account=account).order_by('created_at').values_list(
                'entry_type', 'delta', 'balance_after',
            )),
            [
                ('accrual', Decimal('13.50'), Decimal('13.50')),
                ('settlement_created', Decimal('0.00'), Decimal('13.50')),
                ('payout', Decimal('-13.50'), Decimal('0.00')),
            ],
        )
        detail = client.get(f'/api/customer/channel-settlements/{settlement.id}/')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['customer_subtotals'][0]['customer_no'], customer.customer_no)
        self.assertEqual(detail.data['service_type_subtotals'][0]['service_type'], 'vps')
        self.assertEqual(
            [item['event'] for item in detail.data['timeline']],
            ['created', 'confirmed', 'paid'],
        )

    def test_payout_profile_encrypts_account_and_api_only_returns_mask(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            '/api/customer/channel-payout-profiles/',
            {
                'channel': self.channel.id,
                'entity_name': '推广渠道 A 有限公司',
                'account_type': 'bank',
                'bank_name': '测试银行',
                'bank_branch': '上海支行',
                'recipient_name': '渠道财务',
                'tax_id': '91310000TEST',
                'account': '6222021234567890',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        profile = ChannelPayoutProfile.objects.get(channel=self.channel)
        self.assertNotIn('6222021234567890', profile.account_encrypted)
        self.assertEqual(profile.get_account(), '6222021234567890')
        self.assertTrue(response.data['is_active'])
        self.assertEqual(response.data['masked_account'], '************7890')
        self.assertNotIn('account_encrypted', response.data)
        self.assertNotIn('6222021234567890', str(response.data))

    def test_adjustment_is_idempotent_and_ledger_is_append_only(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        payload = {
            'channel': self.channel.id,
            'currency': 'cny',
            'delta': '25.00',
            'reason': '期初余额修正',
            'reference': 'ADJ-202608-001',
            'evidence': '财务审批单附件 1',
            'idempotency_key': 'opening-202608',
        }
        first = client.post(
            '/api/customer/channel-finance-ledgers/adjustment/',
            payload,
            format='json',
        )
        second = client.post(
            '/api/customer/channel-finance-ledgers/adjustment/',
            payload,
            format='json',
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data['id'], second.data['id'])
        account = ChannelFinanceAccount.objects.get(channel=self.channel, currency='CNY')
        self.assertEqual(account.balance, Decimal('25.00'))
        ledger = account.ledgers.get()
        self.assertEqual(ledger.snapshot['reference'], 'ADJ-202608-001')
        self.assertEqual(ledger.snapshot['evidence'], '财务审批单附件 1')
        with self.assertRaises(ValidationError):
            ledger.save()
        with self.assertRaises(ValidationError):
            ChannelFinanceLedger.objects.filter(pk=ledger.pk).update(delta=Decimal('99.00'))
        with self.assertRaises(ValidationError):
            ledger.delete()
        summary = client.get('/api/customer/channel-finance-ledgers/summary/')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.data['total_balance'], Decimal('25.00'))
        self.assertEqual(summary.data['by_currency'][0]['adjustment'], Decimal('25.00'))
        self.assertIn('settlement_totals', summary.data)
        self.assertIn('currency', summary.data['settlement_totals'])
        self.assertIn('balance', summary.data['settlement_totals'])
        listed = client.get(
            f'/api/customer/channel-finance-ledgers/?channel={self.channel.id}'
            '&currency=CNY&start_date=2026-01-01&end_date=2026-12-31',
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['results'][0]['id'], ledger.id)

    def test_cancel_void_and_multi_currency_accounts_remain_reconcilable(self):
        from .channel_services import (
            adjust_channel_balance,
            bind_customer_referral,
            cancel_channel_settlement,
            create_channel_settlement,
            ensure_paid_billing_commission,
            void_channel_commission,
        )

        payout = ChannelPayoutProfile(
            channel=self.channel,
            entity_name='推广渠道 A 有限公司',
            account_type='bank',
            bank_name='测试银行',
            recipient_name='渠道财务',
        )
        payout.set_account('6222021234567890')
        payout.save()
        customer = Customer.objects.create(
            name='待结算推荐客户',
            customer_type='personal',
            contact_name='推荐客户',
        )
        bind_customer_referral(customer, 'PARTNER2026', bound_by=self.admin)
        billing = CustomerBilling.objects.create(
            customer=customer,
            channel_referral=customer.channel_referral,
            bill_month='2026-08',
            gross_amount='100.00',
            promotion_discount_amount='10.00',
            promotion_code='PARTNER2026',
            amount='90.00',
            paid_amount='90.00',
            currency='CNY',
            status=CustomerBilling.BillingStatus.PAID,
            payment_method='bank_transfer',
            paid_at=timezone.now(),
            due_date=datetime.date(2026, 8, 31),
        )
        commission = ensure_paid_billing_commission(billing, operator=self.admin)
        settlement = create_channel_settlement([commission.pk], operator=self.admin)
        self.assertEqual(settlement.payout_profile_snapshot['masked_account'], '************7890')

        cancel_channel_settlement(settlement, operator=self.admin, reason='资料待核对')
        commission.refresh_from_db()
        self.assertEqual(commission.status, ChannelCommission.Status.PENDING)
        void_channel_commission(commission, operator=self.admin, reason='账单退款')

        cny_account = ChannelFinanceAccount.objects.get(channel=self.channel, currency='CNY')
        self.assertEqual(cny_account.balance, Decimal('0.00'))
        self.assertEqual(
            list(cny_account.ledgers.values_list('entry_type', 'delta')),
            [
                ('void', Decimal('-13.50')),
                ('settlement_cancelled', Decimal('0.00')),
                ('settlement_created', Decimal('0.00')),
                ('accrual', Decimal('13.50')),
            ],
        )

        adjust_channel_balance(
            channel=self.channel,
            currency='EUR',
            delta='5.00',
            reason='欧元期初余额',
            reference='EUR-OPENING',
            evidence='财务审批记录',
            operator=self.admin,
            idempotency_key='eur-opening',
        )
        eur_account = ChannelFinanceAccount.objects.get(channel=self.channel, currency='EUR')
        self.assertEqual(eur_account.balance, Decimal('5.00'))
        cny_account.refresh_from_db()
        self.assertEqual(cny_account.balance, Decimal('0.00'))

    def test_customer_account_can_read_own_finance_but_cannot_mutate(self):
        from .channel_services import adjust_channel_balance

        adjust_channel_balance(
            channel=self.channel,
            currency='CNY',
            delta='1.00',
            reason='测试期初',
            reference='TEST-001',
            evidence='测试凭证',
            operator=self.admin,
            idempotency_key='customer-read-scope',
        )
        customer_user = User.objects.create_user('channel-portal', password='password')
        profile, _ = UserProfile.objects.get_or_create(user=customer_user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.READONLY
        profile.save()
        self.channel.account = customer_user
        self.channel.save(update_fields=['account', 'updated_at'])

        client = APIClient()
        client.force_authenticate(customer_user)
        self.assertEqual(client.get('/api/customer/channel-finance-ledgers/').status_code, 200)
        self.assertEqual(client.get('/api/customer/channel-payout-profiles/').status_code, 200)
        self.assertEqual(
            client.post(
                '/api/customer/channel-finance-ledgers/adjustment/',
                {
                    'channel': self.channel.id,
                    'currency': 'CNY',
                    'delta': '999.00',
                    'reason': '越权',
                    'reference': 'BAD',
                    'evidence': 'BAD',
                    'idempotency_key': 'bad',
                },
                format='json',
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post('/api/customer/channel-payout-profiles/', {}, format='json').status_code,
            403,
        )
        self.assertEqual(
            client.post('/api/customer/channel-commissions/999/void/', {'reason': '越权'}).status_code,
            403,
        )
        for action in ('confirm', 'mark-paid', 'cancel'):
            self.assertEqual(
                client.post(f'/api/customer/channel-settlements/999/{action}/', {}, format='json').status_code,
                403,
            )
