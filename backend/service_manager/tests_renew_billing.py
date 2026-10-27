"""门户一键续费报价、出账与履约单测。"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from customer_manager.models import Customer, CustomerBilling
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region, VPS
from service_manager.models import ServiceExpiryAction, ServiceInstance, ServiceOrder
from service_manager.payment_fulfillment import fulfill_paid_billing
from service_manager.renew_billing import (
    RenewBillingError,
    quote_instance_renewal,
    quote_trial_conversion,
    submit_instance_renewal,
    submit_trial_conversion,
)


class InstanceRenewBillingTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='rnw-bill', name='续费区')
        country = Country.objects.create(region=region, name='续费国')
        city = City.objects.create(country=country, name='续费市')
        self.pop = PopPoint.objects.create(name='续费 POP', city=city)
        self.user = User.objects.create_user(username='rnw-bill-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='续费客户',
            contact_name='联系人',
            account=self.user,
        )
        self.product = Product.objects.create(
            name='续费产品',
            category='vps',
            status='online',
            pop_point=self.pop,
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=1,
            monthly_price=Decimal('100.00'),
            setup_price=Decimal('0.00'),
            currency='CNY',
        )
        self.order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.ACTIVE,
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            quoted_price=Decimal('100.00'),
            billing_cycle=ServiceOrder.BillingCycle.MONTHLY,
        )
        self.vps = VPS.objects.create(
            name='续费 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            status='running',
        )
        expire = self._at(10)
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type='vps',
            status='active',
            vps=self.vps,
            monthly_fee=Decimal('100.00'),
            fee_currency='CNY',
            expire_time=expire,
        )

    def _at(self, days_from_today, hour=12):
        day = timezone.localdate() + timedelta(days=days_from_today)
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def test_quarterly_quote_is_three_months(self):
        quote = quote_instance_renewal(self.instance, 'quarterly')
        self.assertEqual(quote['months'], 3)
        self.assertEqual(quote['due_now'], '300.00')
        self.assertEqual(quote['billing_cycle'], 'quarterly')
        self.assertIn('支付成功后到期日延长', quote['policy_text'])

    def test_zero_fee_cannot_renew(self):
        self.instance.monthly_fee = Decimal('0.00')
        self.instance.save(update_fields=['monthly_fee'])
        self.vps.product = None
        self.vps.save(update_fields=['product'])
        self.order.quoted_price = Decimal('0.00')
        self.order.product = None
        self.order.save(update_fields=['quoted_price', 'product'])
        instance = ServiceInstance.objects.select_related(
            'vps', 'service_order',
        ).get(pk=self.instance.pk)
        with self.assertRaises(RenewBillingError):
            quote_instance_renewal(instance, 'monthly')

    def test_terminated_cannot_renew(self):
        self.instance.status = 'terminated'
        self.instance.save(update_fields=['status'])
        with self.assertRaises(RenewBillingError):
            quote_instance_renewal(self.instance, 'monthly')

    @patch('service_manager.renew_billing.notify_order_event')
    def test_submit_is_idempotent_for_unpaid_bill(self, _notify):
        first = submit_instance_renewal(
            self.instance, billing_cycle='monthly', user=self.user,
        )
        second = submit_instance_renewal(
            self.instance, billing_cycle='annual', user=self.user,
        )
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['order'].pk, second['order'].pk)
        self.assertEqual(
            CustomerBilling.objects.filter(
                service_order__related_instance=self.instance,
                service_order__order_type='renew',
            ).count(),
            1,
        )
        self.assertEqual(second['billing'].amount, Decimal('1200.00'))
        self.assertEqual(second['order'].billing_cycle, 'annual')

    @patch('service_manager.renew_billing.notify_service_event')
    @patch('service_manager.renew_billing.notify_order_event')
    def test_fulfill_extends_expire_and_schedule(self, _order_notify, _event_notify):
        result = submit_instance_renewal(
            self.instance, billing_cycle='quarterly', user=self.user,
        )
        billing = result['billing']
        old_expire = self.instance.expire_time
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.paid_at = timezone.now()
        billing.save(update_fields=['status', 'paid_amount', 'paid_at', 'updated_at'])

        fulfillment = fulfill_paid_billing(billing.pk, operator=self.user)
        self.assertTrue(fulfillment['triggered'])

        self.instance.refresh_from_db()
        self.vps.refresh_from_db()
        result['order'].refresh_from_db()
        self.assertEqual(self.instance.status, 'active')
        self.assertGreater(self.instance.expire_time, old_expire)
        self.assertEqual(self.instance.suspend_at, self.instance.expire_time)
        self.assertEqual(self.vps.expire_time, self.instance.expire_time)
        self.assertEqual(result['order'].auto_provision_status, 'success')
        self.assertEqual(result['order'].status, 'active')

        again = fulfill_paid_billing(billing.pk, operator=self.user)
        self.assertFalse(again['triggered'])
        self.assertEqual(again['reason'], 'already_fulfilled')

    @patch('service_manager.renew_billing.notify_service_event')
    @patch('service_manager.renew_billing.notify_order_event')
    def test_fulfill_resumes_expired_instance(self, _order_notify, _event_notify):
        self.instance.status = 'expired'
        self.instance.expire_time = self._at(-2)
        self.instance.save(update_fields=['expire_time', 'status', 'suspend_at', 'recycle_at'])
        result = submit_instance_renewal(
            self.instance, billing_cycle='monthly', user=self.user,
        )
        billing = result['billing']
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.save(update_fields=['status', 'paid_amount', 'updated_at'])
        fulfill_paid_billing(billing.pk, operator=self.user)
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.status, 'active')
        self.assertGreater(self.instance.expire_time, timezone.now())
        self.assertFalse(
            ServiceExpiryAction.objects.filter(instance=self.instance).exists(),
        )

    @patch('service_manager.renew_billing.notify_service_event')
    @patch('service_manager.renew_billing.notify_order_event')
    def test_trial_conversion_sets_official_start_and_expire(self, _order_notify, _event_notify):
        self.instance.is_trial = True
        self.instance.trial_start = timezone.localdate()
        self.instance.expire_time = self._at(1)
        self.instance.save(update_fields=['is_trial', 'trial_start', 'expire_time', 'updated_at'])
        quote = quote_trial_conversion(self.instance, 'monthly')
        self.assertEqual(quote['billing_kind'], 'trial_convert')
        result = submit_trial_conversion(self.instance, billing_cycle='monthly', user=self.user)
        billing = result['billing']
        billing.status = CustomerBilling.BillingStatus.PAID
        billing.paid_amount = billing.amount
        billing.paid_at = timezone.now()
        billing.save(update_fields=['status', 'paid_amount', 'paid_at', 'updated_at'])
        fulfillment = fulfill_paid_billing(billing.pk, operator=self.user)
        self.assertEqual(fulfillment['reason'], 'converted')
        self.instance.refresh_from_db()
        self.assertFalse(self.instance.is_trial)
        self.assertEqual(self.instance.billing_start_date, timezone.localdate())
        self.assertGreater(self.instance.expire_time, timezone.now() + timedelta(days=27))
