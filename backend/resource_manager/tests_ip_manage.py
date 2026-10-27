"""IP 管理：排除隧道段、同 POP 同 CIDR 合并统计、隧道用量带接口/客户。"""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from customer_manager.models import Customer
from product_manager.models import Product
from product_manager.serializers import ProductListSerializer
from resource_manager.ip_net import expand_cidr_hosts, gateway_in_cidr, netmask_from_prefix
from resource_manager.ip_scope import TUNNEL_BLOCK_TAG, group_ip_blocks
from resource_manager.models import (
    City,
    Country,
    EdgeAcceleration,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    TunnelIPPool,
)
from resource_manager.serializers import IPAddressSerializer, IPBlockSerializer, PopPointListSerializer
from resource_manager.tunnel_ip import usage
from resource_manager.views import IPAddressViewSet, IPBlockViewSet


class BusinessIPScopeTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='ip-scope', name='IP范围区')
        country = Country.objects.create(region=region, name='IP范围国')
        city = City.objects.create(country=country, name='IP范围市')
        self.pop = PopPoint.objects.create(name='业务IP POP', city=city)
        self.biz = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        self.tunnel = IPBlock.objects.create(
            pop_point=self.pop, cidr='10.21.0.0/24',
            description=f'{TUNNEL_BLOCK_TAG} 隧道/边缘 IP 段',
        )
        self.biz_ip = IPAddress.objects.create(ip_block=self.biz, address='203.0.113.10')
        self.tun_ip = IPAddress.objects.create(
            ip_block=self.tunnel, address='10.21.0.1',
            interface_code='gre-cust1',
        )

    def test_ip_block_queryset_excludes_tunnel_segments(self):
        ids = list(IPBlockViewSet().get_queryset().values_list('id', flat=True))
        self.assertIn(self.biz.id, ids)
        self.assertNotIn(self.tunnel.id, ids)

    def test_ip_address_queryset_excludes_tunnel_addresses(self):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        view = IPAddressViewSet()
        view.request = Request(APIRequestFactory().get('/resource/ip-addresses/'))
        view.request.user = None
        ids = list(view.get_queryset().values_list('id', flat=True))
        self.assertIn(self.biz_ip.id, ids)
        self.assertNotIn(self.tun_ip.id, ids)


class CidrMergeTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='cidr-merge', name='CIDR合并区')
        country = Country.objects.create(region=region, name='CIDR合并国')
        city = City.objects.create(country=country, name='CIDR合并市')
        self.pop = PopPoint.objects.create(name='CIDR POP', city=city)
        self.block_a = IPBlock.objects.create(
            pop_point=self.pop, cidr='198.51.100.0/24', description='手工录入',
        )
        self.block_b = IPBlock.objects.create(
            pop_point=self.pop, cidr='198.51.100.0/24', description='节点同步导入',
        )
        IPAddress.objects.create(
            ip_block=self.block_a, address='198.51.100.10',
            status=IPAddress.IPStatus.AVAILABLE,
        )
        IPAddress.objects.create(
            ip_block=self.block_b, address='198.51.100.20',
            status=IPAddress.IPStatus.ASSIGNED,
        )

    def test_same_pop_same_cidr_stats_are_merged(self):
        grouped = group_ip_blocks(list(IPBlockViewSet().get_queryset()))
        self.assertEqual(len(grouped), 1)
        row = grouped[0]
        self.assertEqual(row.cidr, '198.51.100.0/24')
        self.assertEqual(row.merged_count, 2)
        self.assertEqual(set(row.member_ids), {self.block_a.id, self.block_b.id})
        data = IPBlockSerializer(row).data
        self.assertEqual(data['ip_stats']['total'], 2)
        self.assertEqual(data['ip_stats']['available'], 1)
        self.assertEqual(data['ip_stats']['assigned'], 1)
        self.assertEqual(data['merged_count'], 2)

    def test_create_rejects_duplicate_business_cidr(self):
        ser = IPBlockSerializer(data={
            'pop_point': self.pop.id,
            'cidr': '198.51.100.0/24',
            'ip_version': 'v4',
        })
        self.assertFalse(ser.is_valid())
        self.assertIn('cidr', ser.errors)


class TunnelUsageCustomerTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='tun-usage', name='隧道用量区')
        country = Country.objects.create(region=region, name='隧道用量国')
        city = City.objects.create(country=country, name='隧道用量市')
        self.pop = PopPoint.objects.create(name='隧道用量 POP', city=city)
        self.customer = Customer.objects.create(name='隧道客户', contact_name='联系人')
        self.pool = TunnelIPPool.objects.create(
            pop_point=self.pop, name='tokyo-gre', cidr='10.21.0.0/24',
            point_prefixlen=30, customer=self.customer,
        )
        self.edge = EdgeAcceleration.objects.create(
            name='客户隧道',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre-cust1',
        )
        block = IPBlock.objects.create(
            pop_point=self.pop, cidr='10.21.0.0/24',
            description=f'{TUNNEL_BLOCK_TAG} 隧道/边缘 IP 段',
        )
        IPAddress.objects.create(
            ip_block=block, address='10.21.0.1',
            status=IPAddress.IPStatus.ASSIGNED,
            interface_code='gre-cust1',
            assigned_edge=self.edge,
        )
        IPAddress.objects.create(
            ip_block=block, address='10.21.0.2',
            status=IPAddress.IPStatus.RESERVED,
            interface_code='gre-cust1',
            assigned_edge=self.edge,
        )

    def test_usage_entries_include_interface_and_customer(self):
        data = usage(self.pool)
        self.assertGreaterEqual(data['used_blocks'], 1)
        entry = data['used'][0]['entries'][0]
        self.assertEqual(entry['interface_code'], 'gre-cust1')
        self.assertEqual(entry['customer_name'], '隧道客户')
        self.assertEqual(entry['customer_id'], self.customer.id)
        self.assertEqual(data['used'][0]['interface_code'], 'gre-cust1')
        self.assertEqual(data['used'][0]['customer_name'], '隧道客户')


class IPNetHelperTests(SimpleTestCase):
    def test_netmask_from_prefix(self):
        self.assertEqual(netmask_from_prefix(24), '255.255.255.0')
        self.assertEqual(netmask_from_prefix(28), '255.255.255.240')
        self.assertEqual(netmask_from_prefix(32), '255.255.255.255')

    def test_gateway_in_cidr(self):
        self.assertTrue(gateway_in_cidr('203.0.113.1', '203.0.113.0/24'))
        self.assertFalse(gateway_in_cidr('198.51.100.1', '203.0.113.0/24'))

    def test_expand_cidr_hosts_excludes_network_broadcast(self):
        hosts, usable = expand_cidr_hosts('203.0.113.16/28')
        self.assertEqual(usable, 14)
        self.assertEqual(hosts[0], '203.0.113.17')
        self.assertEqual(hosts[-1], '203.0.113.30')
        self.assertNotIn('203.0.113.16', hosts)
        self.assertNotIn('203.0.113.31', hosts)

    def test_expand_cidr_hosts_does_not_materialize_huge_v6(self):
        hosts, usable = expand_cidr_hosts('2001:db8::/64', limit=4)
        self.assertGreater(usable, 4)
        self.assertEqual(len(hosts), 4)


class IPGatewayAndCidrTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('ip-gw', 'ip-gw@example.test', 'password')
        region = Region.objects.create(code='ip-gw', name='网关区')
        country = Country.objects.create(region=region, name='网关国')
        city = City.objects.create(country=country, name='网关市')
        self.pop = PopPoint.objects.create(
            name='网关 POP', city=city,
            ip_delivery_mode=PopPoint.IPDeliveryMode.NAT,
        )
        self.block = IPBlock.objects.create(
            pop_point=self.pop, cidr='203.0.113.0/24', gateway='203.0.113.1',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _bulk(self, payload):
        return self.client.post(
            '/api/resource/ip-addresses/bulk_create/',
            {'push_to_node': False, **payload},
            format='json',
        )

    def test_ip_inherits_block_gateway_and_netmask(self):
        ip = IPAddress.objects.create(
            ip_block=self.block, address='203.0.113.10', prefix_len=None,
        )
        self.assertEqual(ip.effective_gateway, '203.0.113.1')
        self.assertEqual(ip.effective_prefix_len, 24)
        data = IPAddressSerializer(ip).data
        self.assertEqual(data['effective_gateway'], '203.0.113.1')
        self.assertEqual(data['netmask'], '255.255.255.0')
        self.assertEqual(data['ip_delivery_mode'], 'nat')

    def test_block_serializer_rejects_gateway_outside_cidr(self):
        ser = IPBlockSerializer(data={
            'pop_point': self.pop.id,
            'cidr': '198.51.100.0/24',
            'ip_version': 'v4',
            'gateway': '203.0.113.1',
        })
        self.assertFalse(ser.is_valid())
        self.assertIn('gateway', ser.errors)

    def test_bulk_single_and_list(self):
        res = self._bulk({
            'ip_block': self.block.id,
            'mode': 'single',
            'address': '203.0.113.8',
            'gateway': '203.0.113.1',
            'prefix_len': 24,
        })
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['created'], ['203.0.113.8'])
        ip = IPAddress.objects.get(address='203.0.113.8')
        self.assertEqual(ip.prefix_len, 24)
        self.assertEqual(str(ip.gateway), '203.0.113.1')

        res = self._bulk({
            'ip_block': self.block.id,
            'mode': 'list',
            'addresses': ['203.0.113.21', '203.0.113.33'],
        })
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(sorted(res.data['created']), ['203.0.113.21', '203.0.113.33'])

    def test_bulk_cidr_must_belong_to_block(self):
        res = self._bulk({
            'ip_block': self.block.id,
            'mode': 'cidr',
            'cidr': '198.51.100.0/28',
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('必须属于', res.data['detail'])

    def test_bulk_cidr_writes_prefix_and_gateway(self):
        res = self._bulk({
            'ip_block': self.block.id,
            'mode': 'cidr',
            'cidr': '203.0.113.16/28',
            'gateway': '203.0.113.17',
        })
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['prefix_len'], 28)
        self.assertEqual(res.data['netmask'], '255.255.255.240')
        created = IPAddress.objects.filter(address='203.0.113.17').first()
        self.assertIsNotNone(created)
        self.assertEqual(created.prefix_len, 28)
        self.assertEqual(str(created.gateway), '203.0.113.17')

    def test_bulk_rejects_address_outside_block(self):
        res = self._bulk({
            'ip_block': self.block.id,
            'mode': 'single',
            'address': '8.8.8.8',
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('不属于', res.data['detail'])

    def test_pop_and_product_expose_nat_delivery(self):
        pop_data = PopPointListSerializer(self.pop).data
        self.assertEqual(pop_data['ip_delivery_mode'], 'nat')
        self.assertEqual(pop_data['ip_delivery_mode_label'], 'NAT 交付')
        product = Product.objects.create(
            name='NAT VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=1,
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=1,
        )
        pdata = ProductListSerializer(product).data
        self.assertEqual(pdata['ip_delivery_mode'], 'nat')
        self.assertEqual(pdata['ip_delivery_mode_label'], 'NAT 交付')
