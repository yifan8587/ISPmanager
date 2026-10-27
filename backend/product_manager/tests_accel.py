"""边缘加速类型多选：GRE / WireGuard。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.accel import (
    accel_types_label,
    default_edge_business_type,
    join_accel_types,
    parse_accel_types,
)
from product_manager.models import Product
from product_manager.serializers import ProductListSerializer, ProductWriteSerializer
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region


class AccelHelperTests(SimpleTestCase):
    def test_parse_multi_and_aliases(self):
        self.assertEqual(parse_accel_types('gre,wireguard'), ['gre', 'wireguard'])
        self.assertEqual(parse_accel_types(['wg', 'GRE']), ['wireguard', 'gre'])
        self.assertEqual(parse_accel_types('gre/wireguard'), ['gre', 'wireguard'])

    def test_drop_socks5(self):
        self.assertEqual(parse_accel_types('proxy'), [])
        self.assertEqual(parse_accel_types(['socks5', 'gre', 'socks']), ['gre'])

    def test_label_and_join(self):
        self.assertEqual(join_accel_types(['gre', 'wireguard']), 'gre,wireguard')
        self.assertEqual(accel_types_label('gre,wireguard'), 'GRE / WireGuard')
        self.assertEqual(accel_types_label(''), '-')

    def test_default_business_type(self):
        self.assertEqual(default_edge_business_type('wireguard'), 'edge_wg')
        self.assertEqual(default_edge_business_type('gre,wireguard'), 'edge_gre')
        self.assertEqual(default_edge_business_type('gre'), 'edge_gre')


class AccelSerializerTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='acc', name='加速区')
        country = Country.objects.create(region=region, name='加速国')
        city = City.objects.create(country=country, name='加速市')
        self.pop = PopPoint.objects.create(
            name='加速 POP',
            city=city,
            cpu_cores=1,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )

    def test_write_multi_accel_types(self):
        serializer = ProductWriteSerializer(data={
            'name': '边缘多协议',
            'category': 'edge',
            'pop_point': self.pop.pk,
            'bandwidth_mbps': 100,
            'ip_count': 1,
            'accel_types': ['gre', 'wireguard'],
            'monthly_price': '19.90',
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        product = serializer.save()
        self.assertEqual(product.accel_type, 'gre,wireguard')
        data = ProductListSerializer(product).data
        self.assertEqual(data['accel_types'], ['gre', 'wireguard'])
        self.assertEqual(data['accel_type_label'], 'GRE / WireGuard')

    def test_edge_requires_at_least_one_type(self):
        serializer = ProductWriteSerializer(data={
            'name': '无类型边缘',
            'category': 'edge',
            'pop_point': self.pop.pk,
            'bandwidth_mbps': 100,
            'ip_count': 1,
            'accel_types': ['socks5'],
            'monthly_price': '9.90',
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('accel_type', serializer.errors)


class PortalAccelProtocolSwitchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('accel-buyer', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='加速客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        region = Region.objects.create(code='accp', name='门户加速区')
        country = Country.objects.create(region=region, name='门户加速国')
        city = City.objects.create(country=country, name='门户加速市')
        self.pop = PopPoint.objects.create(
            name='门户加速 POP',
            city=city,
            cpu_cores=1,
            config_mode=PopPoint.ConfigMode.MANUAL,
        )
        self.product = Product.objects.create(
            name='边缘 GRE/WG',
            category='edge',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            accel_type='gre,wireguard',
            bandwidth_mbps=100,
            ip_count=1,
            monthly_price=Decimal('19.90'),
        )
        self.edge = EdgeAcceleration.objects.create(
            name='门户边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            bandwidth_mbps=100,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_edge_detail_exposes_allowed_protocols(self):
        response = self.client.get(f'/api/portal/edge/{self.edge.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data.get('data') or response.data
        self.assertEqual(data.get('allowed_protocols'), ['gre', 'wireguard'])

    def test_switch_protocol_without_config(self):
        response = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.edge.refresh_from_db()
        self.assertEqual(self.edge.protocol, 'wireguard')

    def test_reject_protocol_not_on_product(self):
        self.product.accel_type = 'gre'
        self.product.save(update_fields=['accel_type'])
        response = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.edge.refresh_from_db()
        self.assertEqual(self.edge.protocol, 'gre')
