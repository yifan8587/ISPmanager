"""N1：中心 ↔ 节点契约（鉴权、apply 观测、工单 IP 下发）。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import transaction
from django.test import SimpleTestCase, TestCase

from customer_manager.models import Customer
from resource_manager.deploy_service import push_pop_to_node
from resource_manager.models import (
    City,
    Country,
    EdgeAcceleration,
    GREConfig,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    VPS,
)
from resource_manager.pemanager_client import (
    PemanagerClient,
    PemanagerError,
    extract_node_error_detail,
    interpret_node_write,
    pop_has_api_config,
)
from service_manager.models import ServiceInstance, ServiceOrder
from service_manager.provisioning import _allocate_order_ips


class _FakeResp:
    def __init__(self, status_code, payload, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload


class InterpretNodeWriteTests(SimpleTestCase):
    def test_empty_body_is_apply_ok(self):
        interp = interpret_node_write({})
        self.assertTrue(interp['rest_ok'])
        self.assertTrue(interp['apply_ok'])
        self.assertEqual(interp['errors'], [])

    def test_http200_write_disabled_warning_is_not_apply_ok(self):
        interp = interpret_node_write({
            'ok': True,
            'warning': '系统写入未启用（INTERFACEMANAGE_NETPLAN_WRITE_ENABLED=False），本次仅更新数据库配置',
        })
        self.assertTrue(interp['rest_ok'])
        self.assertFalse(interp['apply_ok'])
        self.assertTrue(any('WRITE_ENABLED' in e or '写入' in e for e in interp['errors']))

    def test_ok_false_with_write_enabled_error(self):
        interp = interpret_node_write({
            'ok': False,
            'error': '路由 netplan 写入已关闭（settings.ROUTEMANAGE_NETPLAN_WRITE_ENABLED）',
            'steps': [],
        })
        self.assertFalse(interp['apply_ok'])
        self.assertTrue(interp['errors'])

    def test_failed_step_marks_apply_not_ok(self):
        interp = interpret_node_write({
            'ok': True,
            'steps': [{'ok': False, 'stderr': 'netplan generate failed'}],
        })
        self.assertFalse(interp['apply_ok'])
        self.assertIn('netplan generate failed', interp['errors'])

    def test_qos_not_applied(self):
        interp = interpret_node_write({
            'ok': True,
            'qos': {'applied': False, 'error': 'QOSMANAGE_APPLY_ENABLED=0'},
        })
        self.assertFalse(interp['apply_ok'])
        self.assertTrue(interp['errors'])

    def test_extract_nested_failed_result(self):
        detail = extract_node_error_detail({
            'error': '新接口下发失败, 已回滚数据库',
            'failed_result': {
                'error': 'netplan generate 失败',
                'steps': [{
                    'ok': False,
                    'step': 'netplan generate',
                    'stderr': (
                        '/etc/netplan/99-vpopmanager-routes.yaml:11:14:\n'
                        'Error in network definition: IP family mismatch in route to fe80::1\n'
                        '        via: fe80::1'
                    ),
                }],
            },
        })
        self.assertIn('已回滚', detail)
        self.assertIn('netplan generate', detail)
        self.assertIn('IP family mismatch', detail)


class PemanagerClientAuthTests(SimpleTestCase):
    def test_from_pop_uses_mgmt_token_without_password(self):
        pop = SimpleNamespace(
            name='tok-pop',
            api_base_url='http://node.example',
            api_username='',
            api_password='',
            mgmt_username='',
            mgmt_password='',
            mgmt_ip=None,
            mgmt_token='pem_abc.secret',
        )
        self.assertTrue(pop_has_api_config(pop))
        client = PemanagerClient.from_pop(pop)
        user = client.authenticate()
        self.assertEqual(user.get('auth'), 'token')
        self.assertEqual(client.session.headers['Authorization'], 'Bearer pem_abc.secret')
        self.assertEqual(client.token, 'pem_abc.secret')

    def test_from_pop_requires_token_or_password(self):
        pop = SimpleNamespace(
            name='bare',
            api_base_url='http://node.example',
            api_username='admin',
            api_password='',
            mgmt_username='',
            mgmt_password='',
            mgmt_ip=None,
            mgmt_token='',
        )
        self.assertFalse(pop_has_api_config(pop))
        with self.assertRaises(PemanagerError):
            PemanagerClient.from_pop(pop)

    def test_authenticate_login_flow(self):
        client = PemanagerClient('http://node.example', 'admin', 'secret')
        posts = []

        def fake_post(url, json=None, timeout=None, verify=None):
            posts.append((url, json))
            return _FakeResp(200, {'access': 'jwt-access', 'user': {'username': 'admin'}})

        with patch.object(client.session, 'post', side_effect=fake_post):
            user = client.authenticate()
        self.assertEqual(user.get('username'), 'admin')
        self.assertEqual(client.token, 'jwt-access')
        self.assertTrue(posts[0][0].endswith('/api/accountmanage/auth/login/'))

    def test_desired_tunnel_apply_contract(self):
        client = PemanagerClient('http://node.example', 'admin', 'secret')

        def fake_post(url, json=None, timeout=None, verify=None):
            if url.endswith('/auth/login/'):
                return _FakeResp(200, {'access': 'jwt-access', 'user': {'username': 'admin'}})
            if url.endswith('/db/desired-tunnels/'):
                return _FakeResp(201, {'id': 'tun-1', 'ifname': 'gre1'})
            if url.endswith('/desired-tunnels/apply-system/'):
                self.assertEqual(json.get('ids'), ['tun-1'])
                return _FakeResp(200, {
                    'ok': True,
                    'steps': [{'ok': True, 'step': 'netplan'}],
                })
            return _FakeResp(404, {})

        with patch.object(client.session, 'post', side_effect=fake_post):
            client.authenticate()
            obj = client.create_desired_tunnel({
                'ifname': 'gre1',
                'kind': 'gre',
                'spec': {'type': 'gre', 'netplan_tunnel': {'mode': 'gre'}},
            })
            result = client.apply_desired_tunnels(ids=[obj['id']])
        interp = interpret_node_write(result)
        self.assertTrue(interp['rest_ok'])
        self.assertTrue(interp['apply_ok'])


class PushPopApplyObservationTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='apac', name='亚太')
        country = Country.objects.create(region=region, name='中国')
        city = City.objects.create(country=country, name='上海')
        self.pop = PopPoint.objects.create(
            name='观测 POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_test.secret',
            config_dirty=True,
        )

    def test_apply_not_ok_keeps_config_dirty(self):
        def fake_handler(pop, client, dry_run=True):
            return {
                'ok': False,
                'apply_interp': interpret_node_write({
                    'ok': True,
                    'warning': '系统写入未启用（INTERFACEMANAGE_NETPLAN_WRITE_ENABLED=False）',
                }),
            }

        with patch(
            'resource_manager.deploy_service.PemanagerClient.connect_pop',
            return_value=(MagicMock(), {'auth': 'token'}),
        ), patch.dict(
            'resource_manager.deploy_service._SECTION_HANDLERS',
            {'edge_tunnels': fake_handler},
            clear=True,
        ):
            result = push_pop_to_node(self.pop, sections=['edge_tunnels'], dry_run=False)

        self.assertTrue(result['rest_ok'])
        self.assertFalse(result['apply_ok'])
        self.assertFalse(result['ok'])
        self.pop.refresh_from_db()
        self.assertTrue(self.pop.config_dirty)

    def test_apply_ok_clears_config_dirty(self):
        def fake_handler(pop, client, dry_run=True):
            return {
                'ok': True,
                'apply_interp': interpret_node_write({'ok': True, 'steps': []}),
            }

        with patch(
            'resource_manager.deploy_service.PemanagerClient.connect_pop',
            return_value=(MagicMock(), {'auth': 'token'}),
        ), patch.dict(
            'resource_manager.deploy_service._SECTION_HANDLERS',
            {'edge_tunnels': fake_handler},
            clear=True,
        ):
            result = push_pop_to_node(self.pop, sections=['edge_tunnels'], dry_run=False)

        self.assertTrue(result['ok'])
        self.assertTrue(result['apply_ok'])
        self.pop.refresh_from_db()
        self.assertFalse(self.pop.config_dirty)


class OrderIpPushTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name='节点 IP 客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='europe', name='欧洲')
        country = Country.objects.create(region=region, name='德国')
        city = City.objects.create(country=country, name='法兰克福')
        self.pop = PopPoint.objects.create(
            name='IP POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_ip.secret',
        )
        self.block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        self.ip = IPAddress.objects.create(
            ip_block=self.block,
            address='203.0.113.20',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        from decimal import Decimal

        from product_manager.models import Product
        self.product = Product.objects.create(
            name='IP 产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('10.00'),
            currency='USD',
        )
        self.order = ServiceOrder.objects.create(
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            requested_ip_count=1,
            status=ServiceOrder.OrderStatus.APPROVED,
            quoted_price=Decimal('10.00'),
        )
        self.vps = VPS.objects.create(name='契约 VPS', pop_point=self.pop, customer=self.customer)
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=ServiceInstance.InstanceStatus.PROVISIONING,
            vps=self.vps,
        )

    def _mock_node(self):
        client = MagicMock()
        client.session.headers = {'Authorization': 'Bearer pem_ip.secret'}
        client.customers.return_value = [{'code': self.customer.customer_no, 'id': 1}]
        client.bulk_create_ips.return_value = {
            'created': ['203.0.113.20'], 'skipped': [], 'errors': [],
        }
        client.allocate_ip.return_value = {'address': '203.0.113.20'}
        return client

    def test_allocate_pushes_vps_ip_to_node(self):
        client = self._mock_node()
        with patch(
            'resource_manager.ip_ops.PemanagerClient.connect_pop',
            return_value=(client, {'auth': 'token'}),
        ):
            allocated = _allocate_order_ips(
                self.order, self.pop, self.instance, vps=self.vps,
            )
        self.assertEqual([item.pk for item in allocated], [self.ip.pk])
        client.allocate_ip.assert_called()
        payload = client.allocate_ip.call_args[0][0]
        self.assertEqual(payload['address'], '203.0.113.20')
        self.assertEqual(payload['customer_code'], self.customer.customer_no)

    def test_node_failure_rolls_back_local_assignment(self):
        client = self._mock_node()
        client.allocate_ip.side_effect = PemanagerError('节点分配失败')
        with patch(
            'resource_manager.ip_ops.PemanagerClient.connect_pop',
            return_value=(client, {'auth': 'token'}),
        ):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    _allocate_order_ips(self.order, self.pop, self.instance, vps=self.vps)
        self.ip.refresh_from_db()
        self.assertEqual(self.ip.status, IPAddress.IPStatus.AVAILABLE)

    def test_no_api_config_skips_node_push(self):
        self.pop.api_base_url = ''
        self.pop.mgmt_token = ''
        self.pop.mgmt_ip = None
        self.pop.save(update_fields=['api_base_url', 'mgmt_token', 'mgmt_ip'])
        allocated = _allocate_order_ips(self.order, self.pop, self.instance, vps=self.vps)
        self.assertEqual([item.pk for item in allocated], [self.ip.pk])
        self.ip.refresh_from_db()
        self.assertEqual(self.ip.status, IPAddress.IPStatus.ASSIGNED)


class CrossFamilyRouteRepairTests(SimpleTestCase):
    def test_mismatch_detection(self):
        from resource_manager.deploy_service import route_dest_via_family_mismatch

        self.assertTrue(route_dest_via_family_mismatch({
            'dest_cidr': '0.0.0.0/0', 'gateway': 'fe80::1',
        }))
        self.assertFalse(route_dest_via_family_mismatch({
            'dest_cidr': '0.0.0.0/0', 'gateway': '192.168.1.1',
        }))
        self.assertFalse(route_dest_via_family_mismatch({
            'dest_cidr': '::/0', 'gateway': 'fe80::1',
        }))

    def test_reconfigure_retries_after_deleting_bad_route(self):
        from resource_manager.deploy_service import reconfigure_tunnel_with_route_repair

        client = MagicMock()
        first = PemanagerError(
            '操作 /reconfigure/ 失败（HTTP 400）：新接口下发失败；netplan generate 失败；'
            'IP family mismatch in route to fe80::1',
            payload={'error': '新接口下发失败, 已回滚数据库'},
        )
        client.reconfigure_desired_tunnel.side_effect = [first, {'ok': True, 'steps': []}]
        client.list_desired_routes.side_effect = [
            [
                {
                    'id': 'bad',
                    'interface_name': 'ens33',
                    'dest_cidr': '0.0.0.0/0',
                    'gateway': 'fe80::1',
                },
                {
                    'id': 'good',
                    'interface_name': 'ens33',
                    'dest_cidr': '0.0.0.0/0',
                    'gateway': '192.168.1.1',
                },
            ],
            [
                {
                    'id': 'good',
                    'interface_name': 'ens33',
                    'dest_cidr': '0.0.0.0/0',
                    'gateway': '192.168.1.1',
                },
            ],
        ]
        client.apply_desired_routes.return_value = {'ok': True}

        result = reconfigure_tunnel_with_route_repair(client, 'tid', {'ifname': 'gre12'})
        self.assertTrue(result['ok'])
        client.delete_desired_route.assert_called_once_with('bad')
        client.apply_desired_routes.assert_called_once()
        self.assertEqual(
            client.apply_desired_routes.call_args.kwargs.get('persist_to_netplan'),
            True,
        )
        self.assertEqual(client.reconfigure_desired_tunnel.call_count, 2)

    def test_write_includes_nested_generate_stderr(self):
        client = PemanagerClient('http://node.example', '', '', api_token='pem_x')
        payload = {
            'error': '新接口下发失败, 已回滚数据库',
            'failed_result': {
                'error': 'netplan generate 失败',
                'steps': [{
                    'ok': False,
                    'stderr': 'IP family mismatch in route to fe80::1',
                }],
            },
        }

        def fake_post(url, json=None, timeout=None, verify=None):
            return _FakeResp(400, payload)

        with patch.object(client.session, 'post', side_effect=fake_post):
            with self.assertRaises(PemanagerError) as ctx:
                client.reconfigure_desired_tunnel('85322c67-76d3-4403-a6e2-5d3d3c9a8379', {})
        self.assertIn('IP family mismatch', str(ctx.exception))
        self.assertIn('netplan generate', str(ctx.exception))
        self.assertEqual(ctx.exception.payload.get('error'), '新接口下发失败, 已回滚数据库')


class EdgeIpRouteSyncTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name='边缘 IP 同步客户',
            contact_name='联系人',
            status='active',
        )
        region = Region.objects.create(code='eirs', name='同步区')
        country = Country.objects.create(region=region, name='同步国')
        city = City.objects.create(country=country, name='同步市')
        self.pop = PopPoint.objects.create(
            name='同步 POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_sync.secret',
        )
        self.edge = EdgeAcceleration.objects.create(
            name='同步边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre12',
            node_pushed=True,
        )
        GREConfig.objects.create(
            edge=self.edge,
            local_ip='192.0.2.1',
            remote_ip='198.51.100.1',
            tunnel_ip='10.0.0.1/30',
        )
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        self.ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.20',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_edge=self.edge,
            interface_code='gre12',
        )

    def _client(self):
        client = MagicMock()
        client.session.headers = {'Authorization': 'Bearer x'}
        client.customers.return_value = [{'code': self.customer.customer_no}]
        client.bulk_create_ips.return_value = {
            'created': [], 'skipped': ['203.0.113.20'], 'errors': [],
        }
        return client

    def test_already_allocated_reuses_route_and_saves_ref(self):
        from resource_manager.ip_ops import push_order_ips_to_node

        client = self._client()
        client.allocate_ip_with_route.side_effect = PemanagerError(
            "操作 /allocate-with-route/ 失败（HTTP 400）："
            "[\"['IP 203.0.113.20 当前状态不允许分配']\"]"
        )
        client.ip_addresses.return_value = [{
            'id': 20,
            'address': '203.0.113.20',
            'state': 'allocated',
            'interface_code': 'gre12',
        }]
        client.list_desired_routes.return_value = [{
            'id': 'route-20',
            'interface_name': 'gre12',
            'dest_cidr': '203.0.113.20/32',
            'ip_address': '203.0.113.20',
        }]
        with patch(
            'resource_manager.ip_ops.PemanagerClient.connect_pop',
            return_value=(client, {'auth': 'token'}),
        ):
            result = push_order_ips_to_node(
                self.pop, [self.ip], customer=self.customer, edge=self.edge,
            )
        self.assertEqual(result['pushed'], 1)
        self.ip.refresh_from_db()
        self.assertEqual(self.ip.node_route_ref, 'route-20')
        client.create_desired_route.assert_not_called()

    def test_replaces_route_left_on_previous_gre_interface(self):
        from resource_manager.ip_ops import push_order_ips_to_node

        old_if = f'gre{self.edge.pk}'[:15]
        self.edge.protocol = EdgeAcceleration.Protocol.WIREGUARD
        self.edge.node_ifname = 'wg12'
        self.edge.save(update_fields=['protocol', 'node_ifname'])
        self.ip.interface_code = old_if
        self.ip.save(update_fields=['interface_code'])

        client = self._client()
        client.allocate_ip_with_route.side_effect = PemanagerError(
            "操作 /allocate-with-route/ 失败（HTTP 400）：当前状态不允许分配"
        )
        client.ip_addresses.return_value = [{
            'id': 20,
            'address': '203.0.113.20',
            'state': 'allocated',
        }]
        client.list_desired_routes.return_value = [{
            'id': 'route-old',
            'interface_name': old_if,
            'dest_cidr': '203.0.113.20/32',
        }]
        client.list_desired_tunnels.return_value = [{
            'id': 'tun-old',
            'ifname': old_if,
        }]
        client.create_desired_route.return_value = {'id': 'route-new'}

        with patch(
            'resource_manager.ip_ops.PemanagerClient.connect_pop',
            return_value=(client, {'auth': 'token'}),
        ):
            result = push_order_ips_to_node(
                self.pop, [self.ip], customer=self.customer, edge=self.edge,
            )

        self.assertEqual(result['pushed'], 0)
        self.assertEqual(result['route_mode'], 'wireguard_allowed_ips')
        client.delete_desired_route.assert_any_call('route-old')
        client.delete_desired_tunnel.assert_any_call('tun-old')
        client.apply_desired_routes.assert_called()
        client.apply_desired_tunnels.assert_called()
        client.create_desired_route.assert_not_called()
        client.allocate_ip_with_route.assert_not_called()

    def test_switch_to_gre_reconfigures_existing_wireguard_tunnel(self):
        from resource_manager.deploy_service import _push_edge_tunnels

        old_if = f'wg{self.edge.pk}'[:15]
        new_if = f'gre{self.edge.pk}'[:15]
        self.edge.node_ifname = ''
        self.edge.save(update_fields=['node_ifname'])
        client = self._client()
        client.list_desired_routes.return_value = []
        client.list_desired_tunnels.return_value = [{
            'id': 'tun-wg',
            'ifname': old_if,
            'kind': 'wireguard',
        }]
        client.reconfigure_desired_tunnel.return_value = {'ok': True, 'steps': []}
        client.monitor_targets.return_value = []
        client.monitor_interfaces.return_value = [{
            'id': 7,
            'interface_name': old_if,
            'enabled': True,
        }]
        client.list_bandwidth_pools.return_value = [{'name': 'ispmanager'}]

        result = _push_edge_tunnels(self.pop, client, dry_run=False)

        self.assertEqual(result.get('reconfigured'), 1)
        self.assertEqual(result.get('created'), 0)
        client.create_desired_tunnel.assert_not_called()
        client.delete_desired_tunnel.assert_not_called()
        client.reconfigure_desired_tunnel.assert_called_once()
        re_id, payload = client.reconfigure_desired_tunnel.call_args.args
        self.assertEqual(re_id, 'tun-wg')
        self.assertEqual(payload['ifname'], new_if)
        self.assertEqual(payload['kind'], 'gre')
        self.assertTrue(payload['apply_system'])
        client.delete_bandwidth_allocation.assert_called_once_with({
            'pool_name': 'ispmanager',
            'interface_code': old_if,
        })
        client.patch_monitor_interface.assert_called_once()
        self.assertFalse(client.patch_monitor_interface.call_args.args[1]['enabled'])

    def test_business_ip_rows_reconcile_from_node(self):
        from resource_manager.edge_provision import edge_business_ip_rows

        client = self._client()
        client.list_desired_routes.return_value = [{
            'id': 'route-20',
            'interface_name': 'gre12',
            'dest_cidr': '203.0.113.20/32',
            'ip_address': '203.0.113.20',
        }]
        with patch(
            'resource_manager.ip_ops.PemanagerClient.connect_pop',
            return_value=(client, {'auth': 'token'}),
        ):
            rows = edge_business_ip_rows(self.edge)
        self.assertEqual(rows[0]['route_status'], 'pushed')
        self.assertEqual(rows[0]['route_status_label'], '已下发')
        self.ip.refresh_from_db()
        self.assertEqual(self.ip.node_route_ref, 'route-20')
