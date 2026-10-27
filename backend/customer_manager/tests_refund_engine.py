from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.models import ServiceChangeRecord, ServiceInstance, ServiceOrder
from service_manager.payment_fulfillment import fulfill_paid_billing_safely
from system_manager.models import PaymentChannel

from customer_manager import payment_gateways, payment_service
from customer_manager.models import Customer, CustomerBilling, PaymentTransaction
from customer_manager.refund_engine import RefundError, request_refund_for_billing


class RefundEngineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('refund-admin', 'refund@example.test', 'password')
        self.customer = Customer.objects.create(
            name='退款测试客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='rf', name='退款区')
        country = Country.objects.create(region=region, name='退款国')
        city = City.objects.create(country=country, name='退款市')
        self.pop = PopPoint.objects.create(name='退款 POP', city=city)
        self.product = Product.objects.create(
            name='退款产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('100.00'),
            currency='CNY',
        )
        self.stripe = PaymentChannel(provider='stripe', name='Stripe', enabled=True)
        self.stripe.set_config({'secret_key': 'sk_test', 'webhook_secret': 'whsec_test'})
        self.stripe.save()
        self.alipay = PaymentChannel(provider='alipay', name='支付宝', enabled=True)
        self.alipay.set_config({
            'app_id': '2026000000000001',
            'app_private_key': 'private-key',
            'alipay_public_key': 'public-key',
        })
        self.alipay.save()
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _order(self, *, order_type=ServiceOrder.OrderType.NEW, provision=ServiceOrder.AutoProvisionStatus.FAILED):
        return ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=order_type,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.APPROVED,
            quoted_price=Decimal('100.00'),
            auto_provision_status=provision,
            created_by=self.admin,
        )

    def _billing(self, order, *, status=CustomerBilling.BillingStatus.PAID, paid='100.00'):
        return CustomerBilling.objects.create(
            customer=self.customer,
            service_order=order,
            bill_month='2026-08',
            amount=Decimal('100.00'),
            paid_amount=Decimal(paid),
            currency='CNY',
            status=status,
            due_date='2026-08-31',
        )

    def _payment(self, billing, channel, *, status=PaymentTransaction.Status.SUCCEEDED, provider=None):
        return PaymentTransaction.objects.create(
            billing=billing,
            channel=channel,
            provider=provider or channel.provider,
            amount=Decimal('100.00'),
            currency='CNY',
            billing_amount=Decimal('100.00'),
            billing_currency='CNY',
            status=status,
            provider_payment_id='pi_test_1',
            initiated_by=self.admin,
        )

    def _instance(self, order):
        return ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
            monthly_fee=Decimal('100.00'),
            fee_currency='CNY',
            billing_start_date=timezone.localdate().replace(day=1),
        )

    def _change(self, instance, order, *, status=ServiceChangeRecord.ChangeStatus.IN_PROGRESS, executed=False):
        return ServiceChangeRecord.objects.create(
            service_instance=instance,
            related_order=order,
            change_type=ServiceChangeRecord.ChangeType.UPGRADE,
            status=status,
            change_reason='升配测试',
            executed_at=timezone.now() if executed else None,
        )

    @patch('customer_manager.refund_engine.refund_payment')
    def test_new_failed_without_instance_refunds_stripe(self, refund_payment):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_1', 'response': {'id': 're_1'}}
        order = self._order()
        billing = self._billing(order)
        payment = self._payment(billing, self.stripe)

        result = request_refund_for_billing(billing.pk)
        billing.refresh_from_db()
        payment.refresh_from_db()

        self.assertTrue(result['triggered'])
        self.assertEqual(result['status'], PaymentTransaction.Status.REFUNDED)
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(billing.refunded_amount, Decimal('100.00'))
        self.assertEqual(billing.paid_amount, Decimal('100.00'))
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(payment.provider_refund_id, 're_1')
        refund_payment.assert_called_once()

    @patch('customer_manager.refund_engine.refund_payment')
    def test_same_billing_refund_is_idempotent(self, refund_payment):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_1', 'response': {}}
        order = self._order()
        billing = self._billing(order)
        self._payment(billing, self.stripe)

        request_refund_for_billing(billing.pk)
        second = request_refund_for_billing(billing.pk)
        self.assertFalse(second['triggered'])
        self.assertEqual(second['reason'], 'already_refunded')
        self.assertEqual(refund_payment.call_count, 1)

    @patch('customer_manager.refund_engine.refund_payment')
    def test_upgrade_unexecuted_is_refundable(self, refund_payment):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_up', 'response': {}}
        base = self._order(provision=ServiceOrder.AutoProvisionStatus.SUCCESS)
        instance = self._instance(base)
        upgrade = self._order(order_type=ServiceOrder.OrderType.UPGRADE)
        upgrade.related_instance = instance
        upgrade.save(update_fields=['related_instance', 'updated_at'])
        self._change(instance, upgrade)
        billing = self._billing(upgrade)
        payment = self._payment(billing, self.stripe)

        result = request_refund_for_billing(billing.pk)
        billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertTrue(result['triggered'])
        self.assertEqual(result['reason'], PaymentTransaction.RefundReason.UPGRADE_UNEXECUTED)
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)

    def test_upgrade_completed_is_rejected(self):
        base = self._order(provision=ServiceOrder.AutoProvisionStatus.SUCCESS)
        instance = self._instance(base)
        upgrade = self._order(
            order_type=ServiceOrder.OrderType.UPGRADE,
            provision=ServiceOrder.AutoProvisionStatus.SUCCESS,
        )
        upgrade.related_instance = instance
        upgrade.save(update_fields=['related_instance', 'updated_at'])
        self._change(
            instance, upgrade,
            status=ServiceChangeRecord.ChangeStatus.COMPLETED,
            executed=True,
        )
        billing = self._billing(upgrade)
        self._payment(billing, self.stripe)
        with self.assertRaises(RefundError):
            request_refund_for_billing(billing.pk)

    def test_new_success_or_existing_instance_is_rejected(self):
        success = self._order(provision=ServiceOrder.AutoProvisionStatus.SUCCESS)
        billing = self._billing(success)
        self._payment(billing, self.stripe)
        with self.assertRaises(RefundError):
            request_refund_for_billing(billing.pk)

        failed = self._order()
        self._instance(failed)
        billing2 = self._billing(failed)
        self._payment(billing2, self.stripe)
        with self.assertRaises(RefundError):
            request_refund_for_billing(billing2.pk)

    @patch('customer_manager.refund_engine.refund_payment')
    def test_insufficient_needs_force(self, refund_payment):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_force', 'response': {}}
        order = self._order(provision=ServiceOrder.AutoProvisionStatus.INSUFFICIENT)
        billing = self._billing(order)
        payment = self._payment(billing, self.stripe)
        with self.assertRaises(RefundError):
            request_refund_for_billing(billing.pk)
        result = request_refund_for_billing(billing.pk, force=True)
        billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertTrue(result['triggered'])
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)

    def test_fulfilled_renew_is_rejected(self):
        base = self._order(provision=ServiceOrder.AutoProvisionStatus.SUCCESS)
        instance = self._instance(base)
        renew = self._order(
            order_type=ServiceOrder.OrderType.RENEW,
            provision=ServiceOrder.AutoProvisionStatus.SUCCESS,
        )
        renew.related_instance = instance
        renew.save(update_fields=['related_instance', 'updated_at'])
        billing = self._billing(renew)
        self._payment(billing, self.stripe)
        with self.assertRaises(RefundError):
            request_refund_for_billing(billing.pk)

    def test_alipay_and_cash_go_pending_manual(self):
        order = self._order()
        billing = self._billing(order)
        payment = self._payment(billing, self.alipay)
        result = request_refund_for_billing(billing.pk)
        billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertTrue(result['manual'])
        self.assertEqual(result['status'], PaymentTransaction.Status.PENDING_MANUAL)
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDING)
        self.assertEqual(billing.paid_amount, Decimal('100.00'))
        self.assertEqual(payment.status, PaymentTransaction.Status.PENDING_MANUAL)

        cash_order = self._order()
        cash_billing = self._billing(cash_order)
        cash_result = request_refund_for_billing(cash_billing.pk)
        cash_billing.refresh_from_db()
        self.assertTrue(cash_result['manual'])
        self.assertEqual(cash_billing.status, CustomerBilling.BillingStatus.REFUNDING)
        self.assertEqual(cash_billing.paid_amount, Decimal('100.00'))

    @patch('service_manager.provisioning.auto_provision', side_effect=RuntimeError('node failed'))
    @patch('customer_manager.refund_engine.refund_payment')
    def test_fulfill_failure_triggers_auto_refund(self, refund_payment, _auto_provision):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_auto', 'response': {}}
        order = self._order(provision=ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING)
        billing = self._billing(order)
        payment = self._payment(billing, self.stripe)
        fulfill_paid_billing_safely(billing.pk)
        billing.refresh_from_db()
        payment.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.FAILED)
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(billing.paid_amount, Decimal('100.00'))

    def test_gateway_result_on_refunded_does_not_increase_paid_amount(self):
        order = self._order()
        billing = self._billing(order, status=CustomerBilling.BillingStatus.REFUNDED)
        billing.refunded_amount = Decimal('100.00')
        billing.save(update_fields=['refunded_amount', 'updated_at'])
        payment = self._payment(billing, self.stripe, status=PaymentTransaction.Status.REFUNDED)
        result = payment_gateways.GatewayResult(
            transaction_no=payment.transaction_no,
            provider_payment_id='pi_test_1',
            paid_amount=Decimal('100.00'),
            paid_currency='CNY',
            succeeded=True,
        )
        payment_service.apply_gateway_result(payment, result, callback=True)
        billing.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(billing.paid_amount, Decimal('100.00'))
        self.assertEqual(billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)

    @patch('customer_manager.refund_engine.refund_payment')
    def test_admin_refund_api_and_complete_manual(self, refund_payment):
        refund_payment.return_value = {'ok': True, 'provider_refund_id': 're_api', 'response': {}}
        order = self._order()
        billing = self._billing(order)
        self._payment(billing, self.stripe)
        response = self.client.post(f'/api/customer/billings/{billing.pk}/request-refund/', {}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['billing']['status'], CustomerBilling.BillingStatus.REFUNDED)

        cash_order = self._order()
        cash_billing = self._billing(cash_order)
        pending = self.client.post(
            f'/api/customer/billings/{cash_billing.pk}/request-refund/',
            {},
            format='json',
        )
        self.assertEqual(pending.status_code, 200, pending.data)
        self.assertEqual(pending.data['billing']['status'], CustomerBilling.BillingStatus.REFUNDING)
        done = self.client.post(
            f'/api/customer/billings/{cash_billing.pk}/complete-refund/',
            {},
            format='json',
        )
        self.assertEqual(done.status_code, 200, done.data)
        cash_billing.refresh_from_db()
        self.assertEqual(cash_billing.status, CustomerBilling.BillingStatus.REFUNDED)
        self.assertEqual(cash_billing.refunded_amount, Decimal('100.00'))
        self.assertEqual(cash_billing.paid_amount, Decimal('100.00'))
