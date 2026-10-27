"""B4：下单汇率快照。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone

from customer_manager import payment_service
from customer_manager.billing_engine import compute_customer_month
from customer_manager.models import Customer, CustomerBilling
from portal.serializers import PortalOrderCreateSerializer
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.fx_snapshot import lock_order_fx
from service_manager.models import ServiceInstance, ServiceOrder
from service_manager.renew_billing import submit_instance_renewal
from system_manager.models import PaymentChannel
from system_manager.services import set_setting


class OrderFxSnapshotTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('fx-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='汇率快照客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        region = Region.objects.create(code='fx', name='汇率区')
        country = Country.objects.create(region=region, name='汇率国')
        city = City.objects.create(country=country, name='汇率市')
        self.pop = PopPoint.objects.create(name='汇率 POP', city=city)
        self.product = Product.objects.create(
            name='USD 产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('100.00'),
            currency='USD',
        )
        set_setting('exchange_rate_usd_cny', '7.20')
        self.usd_channel = PaymentChannel(
            provider='stripe',
            name='美元 Stripe',
            enabled=True,
            allowed_currencies=['USD'],
            settlement_currency='USD',
        )
        self.usd_channel.set_config({'secret_key': 'sk_test', 'webhook_secret': 'whsec_test'})
        self.usd_channel.save()

    def _order(self, *, lock=True):
        order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.APPROVED,
            quoted_price=Decimal('100.00'),
            created_by=self.user,
        )
        if lock:
            lock_order_fx(order, quote_currency='USD')
        return order

    def test_portal_order_locks_current_rates(self):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={'product_id': self.product.pk, 'quantity': 1},
            context={'request': request, 'customer': self.customer},
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        self.assertTrue(order.fx_locked_at)
        self.assertEqual(order.fx_quote_currency, 'USD')
        self.assertEqual(Decimal(order.fx_snapshot['to_cny']['USD']), Decimal('7.20'))

    def test_payment_uses_locked_rate_after_live_rate_changes(self):
        order = self._order()
        billing = CustomerBilling.objects.create(
            customer=self.customer,
            service_order=order,
            bill_month='2026-08',
            amount=Decimal('72.00'),
            currency='CNY',
            due_date='2026-08-31',
        )
        set_setting('exchange_rate_usd_cny', '8.00')
        payment = payment_service.create_payment_transaction(
            billing=billing,
            channel=self.usd_channel,
            amount='72.00',
            operator=self.user,
        )
        self.assertEqual(payment.amount, Decimal('10.00'))
        self.assertEqual(payment.currency, 'USD')
        self.assertEqual(payment.exchange_rate, Decimal('0.13888889'))

    def test_payment_without_snapshot_follows_live_rate(self):
        order = self._order(lock=False)
        billing = CustomerBilling.objects.create(
            customer=self.customer,
            service_order=order,
            bill_month='2026-08',
            amount=Decimal('72.00'),
            currency='CNY',
            due_date='2026-08-31',
        )
        set_setting('exchange_rate_usd_cny', '8.00')
        payment = payment_service.create_payment_transaction(
            billing=billing,
            channel=self.usd_channel,
            amount='72.00',
            operator=self.user,
        )
        self.assertEqual(payment.amount, Decimal('9.00'))

    def test_monthly_bill_uses_order_snapshot(self):
        order = self._order()
        ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
            monthly_fee=Decimal('100.00'),
            fee_currency='USD',
            billing_start_date=timezone.localdate().replace(day=1),
        )
        set_setting('exchange_rate_usd_cny', '8.00')
        month = timezone.localdate().strftime('%Y-%m')
        result = compute_customer_month(self.customer, month, 'CNY')
        item = result['items'][0]
        self.assertEqual(item['source_currency'], 'USD')
        self.assertEqual(Decimal(item['amount']), Decimal('720.00'))
        self.assertEqual(Decimal(item['exchange_rate']), Decimal('7.20'))

    def test_renew_locks_snapshot(self):
        order = self._order()
        instance = ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.ACTIVE,
            monthly_fee=Decimal('100.00'),
            fee_currency='USD',
            billing_start_date=timezone.localdate().replace(day=1),
            expire_time=timezone.now(),
        )
        result = submit_instance_renewal(instance, billing_cycle='monthly', user=self.user)
        renew = result['order']
        self.assertTrue(renew.fx_locked_at)
        self.assertEqual(renew.fx_quote_currency, 'USD')
        self.assertIn('USD', renew.fx_snapshot.get('to_cny') or {})
