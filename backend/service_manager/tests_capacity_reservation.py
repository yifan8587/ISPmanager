"""C1：POP 容量账本与下单预占。"""
from datetime import timedelta
from decimal import Decimal

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customer_manager.models import Customer
from portal.serializers import PortalOrderCreateSerializer
from product_manager import services as product_services
from product_manager.models import Product
from resource_manager.models import City, Country, IPAddress, IPBlock, PopPoint, Region
from service_manager.capacity_reservation import (
    CapacityReservationError,
    consume_reservation,
    current_held,
    pin_reservation,
    process_expired_reservations,
    release_reservation,
    reserve_for_order,
    spec_for_order,
)
from service_manager.models import PopCapacityReservation, ServiceOrder


class CapacityReservationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cap-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='容量预占客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        region = Region.objects.create(code='cap', name='容量区')
        country = Country.objects.create(region=region, name='容量国')
        city = City.objects.create(country=country, name='容量市')
        self.pop = PopPoint.objects.create(
            name='容量 POP',
            city=city,
            cpu_cores=1,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='容量 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('10.00'),
            currency='CNY',
            cpu_cores=2,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=0,
        )

    def _portal_order(self, **extra):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={'product_id': self.product.pk, 'quantity': extra.pop('quantity', 1), **extra},
            context={'request': request, 'customer': self.customer},
        )
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def test_portal_order_holds_capacity_and_second_order_waits_manual(self):
        first = self._portal_order()
        held = current_held(first)
        self.assertIsNotNone(held)
        self.assertEqual(held.status, PopCapacityReservation.Status.HELD)
        self.assertEqual(held.cpu_cores, 2)
        avail = product_services.pop_available(self.pop)
        self.assertEqual(avail['cpu_cores'], 0)
        self.assertFalse((first.request_snapshot or {}).get('delivery', {}).get('await_manual_stock'))

        second = self._portal_order()
        self.assertTrue((second.request_snapshot or {}).get('delivery', {}).get('await_manual_stock'))
        self.assertEqual(PopCapacityReservation.objects.filter(status='held').count(), 2)
        self.assertEqual(current_held(second).cpu_cores, 2)

    def test_allow_oversell_still_records_hold(self):
        self._portal_order()
        self.pop.allow_oversell = True
        self.pop.save(update_fields=['allow_oversell', 'updated_at'])
        second = self._portal_order()
        self.assertEqual(
            PopCapacityReservation.objects.filter(
                service_order=second, status=PopCapacityReservation.Status.HELD,
            ).count(),
            1,
        )
        self.assertEqual(product_services.pop_held(self.pop)['cpu_cores'], 4)

    def test_check_excludes_own_hold(self):
        order = self._portal_order()
        check_others = product_services.check_spec_against_pop(
            self.pop, {'cpu_cores': 2, 'ram_mb': 0, 'disk_gb': 0, 'bandwidth_mbps': 0, 'ip_count': 0},
        )
        self.assertFalse(check_others['ok'])
        check_own = product_services.check_spec_against_pop(
            self.pop,
            {'cpu_cores': 2, 'ram_mb': 0, 'disk_gb': 0, 'bandwidth_mbps': 0, 'ip_count': 0},
            exclude_order=order,
        )
        self.assertTrue(check_own['ok'])

    def test_consume_then_available_follows_usage_not_hold(self):
        order = self._portal_order()
        consume_reservation(order)
        self.assertIsNone(current_held(order))
        latest = order.capacity_reservations.order_by('-id').first()
        self.assertEqual(latest.status, PopCapacityReservation.Status.CONSUMED)
        avail = product_services.pop_available(self.pop)
        self.assertEqual(avail['cpu_cores'], 2)

    def test_reject_releases_hold(self):
        admin = User.objects.create_superuser('cap-admin', 'cap@example.test', 'password')
        order = self._portal_order()
        client = APIClient()
        client.force_authenticate(admin)
        response = client.post(
            f'/api/service/orders/{order.pk}/approve/',
            {'approved': False, 'reject_reason': '库存改期'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(current_held(order))
        latest = order.capacity_reservations.order_by('-id').first()
        self.assertEqual(latest.status, PopCapacityReservation.Status.RELEASED)
        self.assertEqual(product_services.pop_available(self.pop)['cpu_cores'], 2)

    def test_unpaid_expire_releases_but_paid_is_pinned(self):
        unpaid = self._portal_order()
        self.pop.allow_oversell = True
        self.pop.save(update_fields=['allow_oversell', 'updated_at'])
        paid = self._portal_order()
        pin_reservation(paid)
        unpaid_hold = current_held(unpaid)
        unpaid_hold.expires_at = timezone.now() - timedelta(hours=1)
        unpaid_hold.save(update_fields=['expires_at', 'updated_at'])
        paid_hold = current_held(paid)
        self.assertIsNone(paid_hold.expires_at)

        stats = process_expired_reservations(now=timezone.now())
        self.assertEqual(stats['released'], 1)
        self.assertIsNone(current_held(unpaid))
        self.assertIsNotNone(current_held(paid))

    def test_renew_does_not_reserve(self):
        renew_order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.RENEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.PENDING,
            requested_vcpu=2,
            quoted_price=Decimal('10.00'),
        )
        self.assertIsNone(reserve_for_order(renew_order))
        self.assertFalse(renew_order.capacity_reservations.exists())

    def test_quantity_multiplies_hold(self):
        self.pop.cpu_cores = 4
        self.pop.save(update_fields=['cpu_cores', 'updated_at'])
        order = self._portal_order(quantity=2)
        self.assertEqual(current_held(order).cpu_cores, 4)
        self.assertEqual(spec_for_order(order)['cpu_cores'], 4)

    def test_delivery_options_returns_stock_and_eta(self):
        client = APIClient()
        response = client.get(f'/api/portal/products/{self.product.pk}/delivery-options/')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data['pop']['id'], self.pop.pk)
        self.assertTrue(data['can_deliver'])
        self.assertEqual(data['stock'], 1)
        self.assertEqual(data['estimated_provision_minutes'], 24 * 60)
        self.assertIn('held', data)

    def test_ip_pool_is_counted_when_present(self):
        self.pop.cpu_cores = None
        self.pop.save(update_fields=['cpu_cores', 'updated_at'])
        self.product.cpu_cores = 0
        self.product.ip_count = 1
        self.product.save(update_fields=['cpu_cores', 'ip_count', 'updated_at'])
        block = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/30')
        IPAddress.objects.create(ip_block=block, address='198.51.100.1', prefix_len=30)
        first = self._portal_order()
        self.assertEqual(current_held(first).ip_count, 1)
        second = self._portal_order()
        self.assertTrue((second.request_snapshot or {}).get('delivery', {}).get('await_manual_stock'))
        self.assertEqual(current_held(second).ip_count, 1)

    def test_reserve_is_idempotent(self):
        order = self._portal_order()
        first = current_held(order)
        again = reserve_for_order(order)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(
            PopCapacityReservation.objects.filter(service_order=order, status='held').count(),
            1,
        )

    @patch('service_manager.provisioning.notify_managers')
    def test_paid_short_stock_order_does_not_auto_start(self, _notify):
        self._portal_order()
        second = self._portal_order()
        self.assertTrue((second.request_snapshot or {}).get('delivery', {}).get('await_manual_stock'))
        self.pop.config_mode = PopPoint.ConfigMode.AUTO
        self.pop.save(update_fields=['config_mode', 'updated_at'])
        second.preferred_pop = PopPoint.objects.get(pk=self.pop.pk)
        second.status = ServiceOrder.OrderStatus.APPROVED
        second.save(update_fields=['preferred_pop', 'status', 'updated_at'])
        from service_manager.provisioning import auto_provision
        result = auto_provision(second, operator=self.user)
        second.refresh_from_db()
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'stock_manual')
        self.assertEqual(second.auto_provision_status, ServiceOrder.AutoProvisionStatus.INSUFFICIENT)
        self.assertFalse(second.instances.exists())
        self.assertEqual(second.manual_delivery.reason, 'stock')
        self.assertEqual(second.manual_delivery.status, 'open')

    def test_short_stock_order_is_kept_for_manual_delivery(self):
        self._portal_order()
        before = ServiceOrder.objects.count()
        second = self._portal_order()
        self.assertEqual(ServiceOrder.objects.count(), before + 1)
        self.assertTrue((second.request_snapshot or {}).get('delivery', {}).get('await_manual_stock'))
