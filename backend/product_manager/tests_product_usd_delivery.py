"""产品美元计价、示例 IP 展示与库存不足预估开通。"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from customer_manager.models import Customer
from portal.serializers import PortalOrderCreateSerializer
from product_manager.models import Product
from product_manager.serializers import ProductWriteSerializer
from product_manager.services import compute_price, resolve_provision_estimate
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.capacity_reservation import current_held
from service_manager.models import PopCapacityReservation


class ProductUsdDeliveryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('usd-admin', 'usd@example.test', 'password')
        self.user = User.objects.create_user('usd-buyer', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='美元计价客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        region = Region.objects.create(code='usd', name='美元区')
        country = Country.objects.create(region=region, name='美元国')
        city = City.objects.create(country=country, name='美元市')
        self.pop = PopPoint.objects.create(
            name='美元 POP',
            city=city,
            cpu_cores=1,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.portal_client = APIClient()

    def _product(self, **extra):
        defaults = dict(
            name='美元 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('19.90'),
            cpu_cores=8,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=0,
        )
        defaults.update(extra)
        return Product.objects.create(**defaults)

    def _portal_order(self, product, **extra):
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={'product_id': product.pk, 'quantity': extra.pop('quantity', 1), **extra},
            context={'request': request, 'customer': self.customer},
        )
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def test_new_product_defaults_to_usd(self):
        product = self._product()
        self.assertEqual(product.currency, 'USD')
        price = compute_price(product)
        self.assertEqual(price['currency'], 'USD')

    def test_write_serializer_defaults_currency_usd(self):
        serializer = ProductWriteSerializer(data={
            'name': '新建美元产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'cpu_cores': 1,
            'ram_mb': 1024,
            'disk_gb': 20,
            'nic_count': 1,
            'bandwidth_mbps': 10,
            'ip_count': 1,
            'monthly_price': '9.90',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        product = serializer.save()
        self.assertEqual(product.currency, 'USD')

    def test_sample_ip_required_when_visible(self):
        serializer = ProductWriteSerializer(data={
            'name': '示例 IP 产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'cpu_cores': 1,
            'ram_mb': 1024,
            'disk_gb': 20,
            'nic_count': 1,
            'bandwidth_mbps': 10,
            'ip_count': 1,
            'monthly_price': '9.90',
            'sample_ip_visible': True,
            'sample_ip': '',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('sample_ip', serializer.errors)

    def test_publish_requires_estimated_hours_when_stock_insufficient(self):
        product = self._product(status=Product.Status.DRAFT)
        response = self.admin_client.post(
            f'/api/product/products/{product.pk}/publish/',
            {'action': 'online'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(response.data.get('requires_estimated_provision'))
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.DRAFT)

    def test_publish_with_estimated_hours_allows_backorder(self):
        product = self._product(status=Product.Status.DRAFT)
        response = self.admin_client.post(
            f'/api/product/products/{product.pk}/publish/',
            {'action': 'online', 'estimated_provision_hours': 36},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ONLINE)
        self.assertTrue(product.allow_backorder)
        self.assertEqual(product.estimated_provision_hours, 36)

    def test_portal_hides_sample_ip_when_not_visible(self):
        product = self._product(
            sample_ip='203.0.113.10',
            sample_ip_visible=False,
            cpu_cores=1,
        )
        response = self.portal_client.get(f'/api/portal/products/{product.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data.get('sample_ip'), '')
        self.assertFalse(response.data.get('sample_ip_visible'))

    def test_portal_shows_sample_ip_when_visible(self):
        product = self._product(
            sample_ip='203.0.113.10, 203.0.113.11',
            sample_ip_visible=True,
            cpu_cores=1,
        )
        response = self.portal_client.get(f'/api/portal/products/{product.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data.get('sample_ip'), '203.0.113.10, 203.0.113.11')
        self.assertTrue(response.data.get('sample_ip_visible'))

    def test_portal_order_writes_expected_delivery_at(self):
        product = self._product(
            estimated_provision_hours=48,
            allow_backorder=True,
        )
        before = timezone.now()
        order = self._portal_order(product)
        self.assertIsNotNone(order.expected_delivery_at)
        delta = order.expected_delivery_at - before
        self.assertGreater(delta, timedelta(hours=47))
        self.assertLess(delta, timedelta(hours=49))
        self.assertTrue(order.request_snapshot.get('delivery', {}).get('is_backorder'))
        self.assertEqual(order.request_snapshot['quote']['currency'], 'USD')

    def test_allow_backorder_does_not_block_capacity_reserve(self):
        product = self._product(
            cpu_cores=2,
            estimated_provision_hours=24,
            allow_backorder=True,
        )
        first = self._portal_order(product)
        self.assertEqual(current_held(first).status, PopCapacityReservation.Status.HELD)
        second = self._portal_order(product)
        self.assertIsNotNone(current_held(second))
        self.assertEqual(
            PopCapacityReservation.objects.filter(status=PopCapacityReservation.Status.HELD).count(),
            2,
        )

    def test_delivery_options_returns_backorder_estimate(self):
        product = self._product(
            estimated_provision_hours=12,
            allow_backorder=True,
        )
        estimate = resolve_provision_estimate(product)
        self.assertEqual(estimate['estimated_provision_minutes'], 12 * 60)
        self.assertTrue(estimate['is_backorder'] or product.allow_backorder)
        response = self.portal_client.get(f'/api/portal/products/{product.pk}/delivery-options/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data.get('estimated_provision_hours'), 12)
        self.assertTrue(response.data.get('is_backorder'))
        self.assertTrue(response.data.get('allow_backorder'))
        self.assertIsNotNone(response.data.get('expected_delivery_at'))
