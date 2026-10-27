"""C2：产品计费模式字段。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from customer_manager.models import Customer
from portal.serializers import PortalOrderCreateSerializer
from product_manager.billing_mode import billing_profile, billing_profile_from_order
from product_manager.models import Product
from product_manager.serializers import ProductWriteSerializer
from product_manager.services import compute_price
from resource_manager.models import City, Country, PopPoint, Region


class ProductBillingModeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('bill-mode', 'bm@example.test', 'password')
        self.customer = Customer.objects.create(
            name='计费模式客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        region = Region.objects.create(code='bm', name='计费区')
        country = Country.objects.create(region=region, name='计费国')
        city = City.objects.create(country=country, name='计费市')
        self.pop = PopPoint.objects.create(name='计费 POP', city=city)
        self.product = Product.objects.create(
            name='包月 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('88.00'),
            currency='CNY',
            cpu_cores=1,
            bandwidth_mbps=10,
        )

    def test_new_product_defaults_to_flat(self):
        self.assertEqual(self.product.billing_mode, Product.BillingMode.FLAT)
        profile = billing_profile(self.product)
        self.assertEqual(profile['billing_mode'], 'flat')
        self.assertEqual(profile['billing_mode_label'], '包月')

    def test_compute_price_includes_mode_and_keeps_monthly(self):
        self.product.billing_mode = Product.BillingMode.P95
        self.product.overage_unit_price = Decimal('1.2500')
        self.product.save(update_fields=['billing_mode', 'overage_unit_price', 'updated_at'])
        price = compute_price(self.product)
        self.assertEqual(price['monthly_price'], '88.00')
        self.assertEqual(price['billing_mode'], '95th')
        self.assertEqual(price['overage_unit_price'], '1.2500')
        self.assertEqual(price['overage_unit_label'], 'Mbps')
        self.assertEqual(price['commit_mbps'], 10)

    def test_transfer_quota_requires_quota(self):
        serializer = ProductWriteSerializer(data={
            'name': '流量包产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'cpu_cores': 1,
            'ram_mb': 1024,
            'disk_gb': 20,
            'nic_count': 1,
            'bandwidth_mbps': 10,
            'ip_count': 1,
            'monthly_price': '10.00',
            'billing_mode': 'transfer_quota',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('transfer_quota_gb', serializer.errors)

    def test_p95_requires_commit_bandwidth(self):
        serializer = ProductWriteSerializer(data={
            'name': '95 产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'cpu_cores': 1,
            'ram_mb': 1024,
            'disk_gb': 20,
            'nic_count': 1,
            'bandwidth_mbps': 0,
            'ip_count': 1,
            'monthly_price': '10.00',
            'billing_mode': '95th',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('bandwidth_mbps', serializer.errors)

    def test_portal_order_snapshots_billing_profile(self):
        self.product.billing_mode = Product.BillingMode.TRANSFER_QUOTA
        self.product.transfer_quota_gb = 1024
        self.product.overage_unit_price = Decimal('0.0200')
        self.product.save(update_fields=[
            'billing_mode', 'transfer_quota_gb', 'overage_unit_price', 'updated_at',
        ])
        request = RequestFactory().post('/api/portal/orders/')
        request.user = self.user
        serializer = PortalOrderCreateSerializer(
            data={'product_id': self.product.pk, 'quantity': 1},
            context={'request': request, 'customer': self.customer},
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        billing = billing_profile_from_order(order)
        self.assertEqual(billing['billing_mode'], 'transfer_quota')
        self.assertEqual(billing['transfer_quota_gb'], 1024)
        self.assertEqual(order.request_snapshot['quote']['billing']['overage_unit_label'], 'GB')

    def test_list_api_returns_billing_mode(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.get(f'/api/product/products/{self.product.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['billing_mode'], 'flat')
        self.assertEqual(response.data['billing_mode_label'], '包月')
