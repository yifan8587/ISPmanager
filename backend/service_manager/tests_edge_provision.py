"""边缘加速：支付后预分配待配置，客户补全后再下发。"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.ip_ops import _edge_route_spec
from resource_manager.ip_scope import TUNNEL_BLOCK_TAG
from resource_manager.models import (
    BandwidthAllocation,
    City,
    Country,
    EdgeAcceleration,
    GREConfig,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    TunnelIPPool,
    WireguardConfig,
)
from service_manager.models import ServiceInstance, ServiceOrder
from service_manager.provisioning import auto_provision


class EdgeAutoProvisionPendingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('edge-admin', 'edge@example.test', 'password')
        self.customer = Customer.objects.create(
            name='边缘客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='edg', name='边缘区')
        country = Country.objects.create(region=region, name='边缘国')
        city = City.objects.create(country=country, name='边缘市')
        self.pop = PopPoint.objects.create(
            name='自动边缘 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
            mgmt_ip='192.0.2.10',
            total_bandwidth_mbps=10000,
        )
        self.product = Product.objects.create(
            name='边缘 GRE',
            category='edge',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            accel_type='gre,wireguard',
            bandwidth_mbps=200,
            ip_count=1,
            monthly_price=Decimal('88.00'),
        )
        self.pool = TunnelIPPool.objects.create(
            pop_point=self.pop,
            name='隧道池',
            cidr='10.21.0.0/24',
            point_prefixlen=30,
        )
        self.block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        self.ip = IPAddress.objects.create(
            ip_block=self.block,
            address='203.0.113.20',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )

    def _order(self, **kwargs):
        defaults = {
            'customer': self.customer,
            'product': self.product,
            'preferred_pop': self.pop,
            'order_type': ServiceOrder.OrderType.NEW,
            'business_type': ServiceOrder.BusinessType.EDGE_GRE,
            'status': ServiceOrder.OrderStatus.APPROVED,
            'quoted_price': Decimal('88.00'),
            'requested_bandwidth_mbps': 200,
            'requested_ip_count': 1,
            'created_by': self.user,
        }
        defaults.update(kwargs)
        return ServiceOrder.objects.create(**defaults)

    def test_auto_provision_preallocates_without_pushing_tunnel(self):
        order = self._order()
        with patch('resource_manager.deploy_service.push_pop_to_node') as push:
            result = auto_provision(order, operator=self.user)
        push.assert_not_called()
        self.assertTrue(result['auto'])
        self.assertTrue(result['ok'])
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.ACTIVE)
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.SUCCESS)

        instance = ServiceInstance.objects.get(service_order=order)
        self.assertEqual(instance.status, ServiceInstance.InstanceStatus.PROVISIONING)
        self.assertEqual(int(instance.spec_snapshot.get('ram_mb') or 0), 0)
        self.assertEqual(int(instance.spec_snapshot.get('cpu_cores') or 0), 0)
        self.assertEqual(int(instance.spec_snapshot.get('bandwidth_mbps') or 0), 200)
        edge = instance.edge
        self.assertEqual(edge.status, EdgeAcceleration.EdgeStatus.PENDING)
        self.assertFalse(edge.node_pushed)
        self.assertEqual(edge.bandwidth_mbps, 200)
        self.assertTrue(
            BandwidthAllocation.objects.filter(edge=edge, allocated_mbps=200, is_synced=False).exists()
        )

        gre = edge.gre_config
        self.assertEqual(str(gre.local_ip), '192.0.2.10')
        self.assertIsNone(gre.remote_ip)
        self.assertEqual(gre.tunnel_ip, '10.21.0.1/30')

        self.ip.refresh_from_db()
        self.assertEqual(self.ip.status, IPAddress.IPStatus.ASSIGNED)
        self.assertEqual(self.ip.assigned_edge, edge)
        self.assertTrue(instance.ip_addresses.filter(pk=self.ip.pk).exists())

        tunnel_ips = IPAddress.objects.filter(
            assigned_edge=edge,
            ip_block__description__startswith=TUNNEL_BLOCK_TAG,
        )
        self.assertEqual(tunnel_ips.count(), 2)
        self.assertEqual(
            set(tunnel_ips.values_list('address', flat=True)),
            {'10.21.0.1', '10.21.0.2'},
        )

    def test_build_spec_zeros_edge_compute_fields(self):
        from service_manager.provisioning import build_spec
        order = self._order(requested_ram_mb=1024, requested_vcpu=4, requested_disk_gb=80)
        spec = build_spec(order)
        self.assertEqual(spec['ram_mb'], 0)
        self.assertEqual(spec['cpu_cores'], 0)
        self.assertEqual(spec['disk_gb'], 0)
        self.assertEqual(spec['bandwidth_mbps'], 200)
        self.assertEqual(spec['ip_count'], 1)

    def test_missing_mgmt_ip_fails_provision(self):
        self.pop.mgmt_ip = None
        self.pop.save(update_fields=['mgmt_ip'])
        order = self._order()
        result = auto_provision(order, operator=self.user)
        self.assertTrue(result['auto'])
        self.assertFalse(result['ok'])
        order.refresh_from_db()
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.FAILED)
        self.assertIn('管理 IP', order.auto_provision_message)
        self.assertFalse(EdgeAcceleration.objects.filter(customer=self.customer).exists())

    def test_missing_tunnel_pool_fails_provision(self):
        self.pool.delete()
        order = self._order()
        result = auto_provision(order, operator=self.user)
        self.assertFalse(result['ok'])
        order.refresh_from_db()
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.FAILED)
        self.assertIn('隧道 IP', order.auto_provision_message)

    def test_edge_route_spec_always_uses_host_32(self):
        edge = EdgeAcceleration.objects.create(
            name='路由测试',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre-edge-1',
        )
        GREConfig.objects.create(
            edge=edge,
            local_ip='192.0.2.10',
            remote_ip='198.51.100.8',
            tunnel_ip='10.21.0.1/30',
        )
        spec = _edge_route_spec(edge, self.ip)
        self.assertEqual(spec['dest_cidr'], '203.0.113.20/32')


class PortalEdgeCompleteAndPushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('edge-buyer', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='门户边缘客户',
            contact_name='联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        region = Region.objects.create(code='edp', name='门户边缘区')
        country = Country.objects.create(region=region, name='门户边缘国')
        city = City.objects.create(country=country, name='门户边缘市')
        self.pop = PopPoint.objects.create(
            name='门户边缘 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
            mgmt_ip='192.0.2.20',
        )
        self.product = Product.objects.create(
            name='门户边缘',
            category='edge',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            accel_type='gre,wireguard',
            bandwidth_mbps=100,
            ip_count=1,
            monthly_price=Decimal('19.90'),
        )
        TunnelIPPool.objects.create(
            pop_point=self.pop, name='门户隧道池', cidr='10.31.0.0/24',
        )
        self.edge = EdgeAcceleration.objects.create(
            name='待配置边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            status=EdgeAcceleration.EdgeStatus.PENDING,
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            bandwidth_mbps=100,
        )
        GREConfig.objects.create(
            edge=self.edge,
            local_ip='192.0.2.20',
            remote_ip=None,
            tunnel_ip='10.31.0.1/30',
        )
        self.order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.EDGE_GRE,
            status=ServiceOrder.OrderStatus.ACTIVE,
            quoted_price=Decimal('19.90'),
        )
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type=ServiceInstance.ServiceType.EDGE_ACCELERATION,
            status=ServiceInstance.InstanceStatus.PROVISIONING,
            edge=self.edge,
        )
        block = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/24')
        self.biz_ip = IPAddress.objects.create(
            ip_block=block,
            address='198.51.100.30',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_edge=self.edge,
        )
        self.instance.ip_addresses.add(self.biz_ip)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_detail_exposes_checklist_and_host_routes(self):
        response = self.client.get(f'/api/portal/edge/{self.edge.pk}/')
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data.get('data') or response.data
        self.assertFalse(data.get('config_ready'))
        self.assertTrue(data.get('needs_protocol_config'))
        keys = {item['key']: item for item in data.get('config_checklist') or []}
        self.assertTrue(keys['local_ip']['complete'])
        self.assertFalse(keys['remote_ip']['complete'])
        self.assertEqual(keys['tunnel_ip']['value'], '10.31.0.1/30')
        self.assertEqual(keys['tunnel_peer']['value'], '10.31.0.2/30')
        self.assertEqual(data['ip_list'][0]['route_cidr'], '198.51.100.30/32')
        self.assertEqual(data['ip_list'][0]['route_status'], 'pending')

    def test_lock_allocated_local_and_tunnel_ip(self):
        response = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {
                'local_ip': '203.0.113.1',
                'tunnel_ip': '10.99.0.1/30',
                'remote_ip': '198.51.100.80',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.edge.gre_config.refresh_from_db()
        self.assertEqual(str(self.edge.gre_config.local_ip), '192.0.2.20')
        self.assertEqual(self.edge.gre_config.tunnel_ip, '10.31.0.1/30')
        self.assertEqual(str(self.edge.gre_config.remote_ip), '198.51.100.80')

    def test_push_rejected_until_remote_filled(self):
        response = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('远端 IP', response.data.get('message') or '')

    @patch('resource_manager.deploy_service.push_pop_to_node')
    def test_save_and_push_activates_instance(self, push):
        def _push(pop, sections=None, dry_run=False):
            if not dry_run:
                self.edge.node_pushed = True
                self.edge.node_ifname = 'gre-edge-ok'
                self.edge.is_synced = True
                self.edge.save(update_fields=[
                    'node_pushed', 'node_ifname', 'is_synced', 'updated_at',
                ])
            return {
                'ok': True,
                'apply_ok': True,
                'dry_run': dry_run,
                'summary': {'edge_tunnels': {'created': 1}},
            }

        push.side_effect = _push
        self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'remote_ip': '198.51.100.80'},
            format='json',
        )
        response = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.edge.refresh_from_db()
        self.instance.refresh_from_db()
        self.assertEqual(self.edge.status, EdgeAcceleration.EdgeStatus.ACTIVE)
        self.assertTrue(self.edge.node_pushed)
        self.assertEqual(self.instance.status, ServiceInstance.InstanceStatus.ACTIVE)

    def test_switch_to_wireguard_keeps_tunnel_cidr(self):
        response = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.edge.refresh_from_db()
        self.assertEqual(self.edge.protocol, 'wireguard')
        self.assertFalse(GREConfig.objects.filter(edge=self.edge).exists())
        wg = WireguardConfig.objects.get(edge=self.edge)
        self.assertEqual(wg.interface_ip, '10.31.0.1/30')
        self.assertGreaterEqual(wg.listen_port, 51820)
        self.assertTrue(wg.public_key)
        self.assertTrue(wg.private_key)
        data = response.data.get('data') or response.data
        self.assertIsNone(data.get('gre_config'))
        self.assertEqual(data.get('wg_config', {}).get('interface_ip'), '10.31.0.1/30')
        self.assertGreaterEqual(data.get('wg_config', {}).get('listen_port'), 51820)

    def test_switch_back_to_gre_deletes_wireguard(self):
        self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        response = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'gre'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.edge.refresh_from_db()
        self.assertEqual(self.edge.protocol, 'gre')
        self.assertFalse(WireguardConfig.objects.filter(edge=self.edge).exists())
        gre = GREConfig.objects.get(edge=self.edge)
        self.assertEqual(gre.tunnel_ip, '10.31.0.1/30')
        self.assertEqual(str(gre.local_ip), '192.0.2.20')

    def test_wireguard_port_increments_and_locked_fields(self):
        first = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        self.assertEqual(first.status_code, 200, first.data)
        other = EdgeAcceleration.objects.create(
            name='另一客户边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            status=EdgeAcceleration.EdgeStatus.PENDING,
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            bandwidth_mbps=100,
        )
        GREConfig.objects.create(
            edge=other,
            local_ip='192.0.2.20',
            tunnel_ip='10.31.0.5/30',
        )
        second = self.client.patch(
            f'/api/portal/edge/{other.pk}/protocol_config/',
            {'protocol': 'wireguard', 'listen_port': 1000, 'interface_ip': '10.9.9.9/30'},
            format='json',
        )
        self.assertEqual(second.status_code, 200, second.data)
        first_wg = WireguardConfig.objects.get(edge=self.edge)
        second_wg = WireguardConfig.objects.get(edge=other)
        self.assertGreaterEqual(first_wg.listen_port, 51820)
        self.assertEqual(second_wg.listen_port, first_wg.listen_port + 1)
        self.assertEqual(second_wg.interface_ip, '10.31.0.5/30')
        self.assertNotEqual(second_wg.listen_port, 1000)

    def test_reject_peer_public_key_same_as_local(self):
        switched = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'protocol': 'wireguard'},
            format='json',
        )
        self.assertEqual(switched.status_code, 200, switched.data)
        wg = WireguardConfig.objects.get(edge=self.edge)
        saved = self.client.patch(
            f'/api/portal/edge/{self.edge.pk}/protocol_config/',
            {'peer_public_key': wg.public_key, 'peer_endpoint': '198.51.100.80:51820'},
            format='json',
        )
        self.assertEqual(saved.status_code, 400, saved.data)
        self.assertIn('不能与本端公钥相同', saved.data.get('message') or '')
        push = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {},
            format='json',
        )
        self.assertEqual(push.status_code, 400, push.data)
        self.assertIn('对端公钥', push.data.get('message') or '')
