"""N4：静态/策略路由保存时按需下发到节点。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from resource_manager.deploy_service import (
    push_policy_route_to_node,
    push_static_route_to_node,
)
from resource_manager.models import (
    City,
    Country,
    NetworkInterface,
    PolicyRoute,
    PopPoint,
    Region,
    RouteTable,
    StaticRoute,
)


class PushRouteHelperTests(SimpleTestCase):
    def test_static_skip_when_pop_has_no_api(self):
        pop = SimpleNamespace(
            name='bare', api_base_url='', mgmt_ip=None, mgmt_token='',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
        )
        table = SimpleNamespace(pop_point=pop)
        iface = SimpleNamespace(ifname='eth0', interface_type='physical')
        rt = SimpleNamespace(
            route_table=table, interface=iface, is_active=True,
            destination='0.0.0.0/0', gateway='10.0.0.1',
        )
        result = push_static_route_to_node(rt)
        self.assertTrue(result['skipped'])
        self.assertTrue(result['ok'])

    def test_static_skip_without_interface(self):
        pop = SimpleNamespace(
            name='tok', api_base_url='http://node.example', mgmt_token='pem_x.y',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
            mgmt_ip=None,
        )
        table = SimpleNamespace(pop_point=pop)
        rt = SimpleNamespace(
            route_table=table, interface=None, is_active=True,
            destination='10.0.0.0/8', gateway=None,
        )
        result = push_static_route_to_node(rt)
        self.assertTrue(result['skipped'])

    def test_policy_skip_without_match(self):
        pop = SimpleNamespace(
            name='tok', api_base_url='http://node.example', mgmt_token='pem_x.y',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
            mgmt_ip=None,
        )
        pr = SimpleNamespace(
            pop_point=pop, is_active=True, src_prefix='', dst_prefix='',
            fwmark='', action='lookup', lookup_table=None, priority=100,
        )
        result = push_policy_route_to_node(pr)
        self.assertTrue(result['skipped'])


class RoutePushApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('n4-admin', 'n4@example.test', 'password')
        region = Region.objects.create(code='n4r', name='路由区')
        country = Country.objects.create(region=region, name='路由国')
        city = City.objects.create(country=country, name='路由市')
        self.pop = PopPoint.objects.create(
            name='路由 POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_rt.secret',
        )
        self.table = RouteTable.objects.create(
            owner_type=RouteTable.RouteTableOwner.POP,
            pop_point=self.pop,
            table_id=100,
            name='custom',
        )
        self.iface = NetworkInterface.objects.create(
            pop_point=self.pop,
            ifname='eth0',
            interface_type=NetworkInterface.InterfaceType.PHYSICAL,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_static_without_api_skips_node(self):
        self.pop.api_base_url = ''
        self.pop.mgmt_token = ''
        self.pop.save(update_fields=['api_base_url', 'mgmt_token'])
        with patch('resource_manager.deploy_service.PemanagerClient.connect_pop') as connect:
            response = self.client.post(
                '/api/resource/static-routes/',
                {
                    'route_table': self.table.pk,
                    'destination': '192.0.2.0/24',
                    'interface': self.iface.pk,
                    'is_active': True,
                },
                format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        connect.assert_not_called()

    def test_create_static_without_interface_skips_node(self):
        with patch('resource_manager.deploy_service.PemanagerClient.connect_pop') as connect:
            response = self.client.post(
                '/api/resource/static-routes/',
                {
                    'route_table': self.table.pk,
                    'destination': '198.51.100.0/24',
                    'is_active': True,
                },
                format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        connect.assert_not_called()

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_create_static_with_interface_applies(self, connect):
        client = MagicMock()
        client.list_desired_routes.return_value = []
        client.create_desired_route.return_value = {'id': 'dr-1'}
        client.apply_desired_routes.return_value = {'ok': True}
        connect.return_value = (client, {'auth': 'token'})
        response = self.client.post(
            '/api/resource/static-routes/',
            {
                'route_table': self.table.pk,
                'destination': '203.0.113.0/24',
                'gateway': '10.0.0.1',
                'interface': self.iface.pk,
                'is_active': True,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        client.create_desired_route.assert_called()
        payload = client.create_desired_route.call_args[0][0]
        self.assertEqual(payload['interface_name'], 'eth0')
        self.assertEqual(payload['dest_cidr'], '203.0.113.0/24')
        client.apply_desired_routes.assert_called()

    @patch('resource_manager.deploy_service.push_static_route_to_node')
    def test_static_push_to_node_false_skips(self, push):
        response = self.client.post(
            '/api/resource/static-routes/?push_to_node=false',
            {
                'route_table': self.table.pk,
                'destination': '10.8.0.0/16',
                'interface': self.iface.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push.assert_not_called()

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_static_node_failure_rolls_back(self, connect):
        client = MagicMock()
        client.list_desired_routes.return_value = []
        client.create_desired_route.return_value = {'id': 'dr-fail'}
        client.apply_desired_routes.return_value = {'ok': False, 'error': '写入关闭'}
        connect.return_value = (client, {'auth': 'token'})
        response = self.client.post(
            '/api/resource/static-routes/',
            {
                'route_table': self.table.pk,
                'destination': '172.16.0.0/12',
                'interface': self.iface.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            StaticRoute.objects.filter(destination='172.16.0.0/12').exists()
        )

    @patch('resource_manager.deploy_service.remove_static_route_from_node')
    def test_delete_static_calls_node_remove(self, remove):
        route = StaticRoute.objects.create(
            route_table=self.table,
            destination='10.9.0.0/16',
            interface=self.iface,
        )
        remove.return_value = {'ok': True}
        response = self.client.delete(f'/api/resource/static-routes/{route.pk}/')
        self.assertEqual(response.status_code, 204)
        remove.assert_called()
        self.assertFalse(StaticRoute.objects.filter(pk=route.pk).exists())

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_create_policy_applies(self, connect):
        client = MagicMock()
        client.list_policy_rules.return_value = []
        client.create_policy_rule.return_value = {'id': 'pr-1'}
        client.apply_policy_rules.return_value = {'ok': True}
        connect.return_value = (client, {'auth': 'token'})
        response = self.client.post(
            '/api/resource/policy-routes/',
            {
                'pop_point': self.pop.pk,
                'priority': 120,
                'src_prefix': '192.0.2.0/24',
                'action': 'lookup',
                'lookup_table': self.table.pk,
                'is_active': True,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        client.create_policy_rule.assert_called()
        payload = client.create_policy_rule.call_args[0][0]
        self.assertEqual(payload['from_cidr'], '192.0.2.0/24')
        self.assertEqual(payload['table_id'], 100)
        client.apply_policy_rules.assert_called()

    @patch('resource_manager.deploy_service.push_policy_route_to_node')
    def test_policy_push_to_node_false_skips(self, push):
        response = self.client.post(
            '/api/resource/policy-routes/?push_to_node=false',
            {
                'pop_point': self.pop.pk,
                'priority': 130,
                'src_prefix': '198.51.100.0/24',
                'action': 'lookup',
                'lookup_table': self.table.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push.assert_not_called()

    @patch('resource_manager.deploy_service.remove_policy_route_from_node')
    def test_delete_policy_keeps_local_on_node_failure(self, remove):
        rule = PolicyRoute.objects.create(
            pop_point=self.pop,
            priority=140,
            src_prefix='203.0.113.0/24',
            action='lookup',
            lookup_table=self.table,
        )
        remove.return_value = {'ok': False, 'detail': '节点拒绝删除'}
        response = self.client.delete(f'/api/resource/policy-routes/{rule.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(PolicyRoute.objects.filter(pk=rule.pk).exists())
