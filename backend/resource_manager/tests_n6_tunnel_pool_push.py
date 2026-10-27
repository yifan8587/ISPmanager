"""N6：隧道 IP 地址段保存时按需下发到节点。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from resource_manager.deploy_service import push_tunnel_ip_pool_to_node
from resource_manager.models import City, Country, PopPoint, Region, TunnelIPPool


class PushTunnelPoolHelperTests(SimpleTestCase):
    def test_skip_when_pop_has_no_api(self):
        pop = SimpleNamespace(
            name='bare', api_base_url='', mgmt_ip=None, mgmt_token='',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
        )
        pool = SimpleNamespace(pop_point=pop, name='p2p', cidr='10.21.0.0/24',
                               point_prefixlen=30, is_active=True, remark='', customer=None)
        result = push_tunnel_ip_pool_to_node(pool, dry_run=False)
        self.assertTrue(result['skipped'])
        self.assertTrue(result['ok'])

    def test_dry_run_does_not_need_api(self):
        pop = SimpleNamespace(name='bare', api_base_url='')
        pool = SimpleNamespace(pop_point=pop, name='p2p', cidr='10.21.0.0/24',
                               point_prefixlen=30, is_active=True, remark='', customer=None)
        result = push_tunnel_ip_pool_to_node(pool, dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['payload']['cidr'], '10.21.0.0/24')


class TunnelPoolPushApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('n6-admin', 'n6@example.test', 'password')
        region = Region.objects.create(code='n6t', name='隧道区')
        country = Country.objects.create(region=region, name='隧道国')
        city = City.objects.create(country=country, name='隧道市')
        self.pop = PopPoint.objects.create(
            name='隧道 POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_tp.secret',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_without_api_skips_node(self):
        self.pop.api_base_url = ''
        self.pop.mgmt_token = ''
        self.pop.save(update_fields=['api_base_url', 'mgmt_token'])
        with patch('resource_manager.deploy_service.PemanagerClient.connect_pop') as connect:
            response = self.client.post(
                '/api/resource/tunnel-ip-pools/',
                {'pop_point': self.pop.pk, 'name': 'n6-bare', 'cidr': '10.30.0.0/24'},
                format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        connect.assert_not_called()

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_create_upserts_node_pool(self, connect):
        client = MagicMock()
        client.tunnel_ip_pools.return_value = []
        client.create_tunnel_ip_pool.return_value = {'id': 'np-1'}
        connect.return_value = (client, {'auth': 'token'})
        response = self.client.post(
            '/api/resource/tunnel-ip-pools/',
            {'pop_point': self.pop.pk, 'name': 'n6-push', 'cidr': '10.31.0.0/24'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        client.create_tunnel_ip_pool.assert_called()
        payload = client.create_tunnel_ip_pool.call_args[0][0]
        self.assertEqual(payload['name'], 'n6-push')
        self.assertEqual(payload['cidr'], '10.31.0.0/24')
        pool = TunnelIPPool.objects.get(name='n6-push')
        self.assertTrue(pool.is_synced)
        self.assertEqual(pool.source_ref, 'np-1')

    @patch('resource_manager.deploy_service.push_tunnel_ip_pool_to_node')
    def test_push_to_node_false_skips(self, push):
        response = self.client.post(
            '/api/resource/tunnel-ip-pools/?push_to_node=false',
            {'pop_point': self.pop.pk, 'name': 'n6-local', 'cidr': '10.32.0.0/24'},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push.assert_not_called()

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_node_failure_rolls_back(self, connect):
        from resource_manager.pemanager_client import PemanagerError
        connect.side_effect = PemanagerError('节点拒绝')
        response = self.client.post(
            '/api/resource/tunnel-ip-pools/',
            {'pop_point': self.pop.pk, 'name': 'n6-fail', 'cidr': '10.33.0.0/24'},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(TunnelIPPool.objects.filter(name='n6-fail').exists())

    @patch('resource_manager.deploy_service.remove_tunnel_ip_pool_from_node')
    def test_delete_calls_node_remove(self, remove):
        pool = TunnelIPPool.objects.create(
            pop_point=self.pop, name='n6-del', cidr='10.34.0.0/24',
        )
        remove.return_value = {'ok': True}
        response = self.client.delete(f'/api/resource/tunnel-ip-pools/{pool.pk}/')
        self.assertEqual(response.status_code, 204)
        remove.assert_called()
        self.assertFalse(TunnelIPPool.objects.filter(pk=pool.pk).exists())

    @patch('resource_manager.deploy_service.remove_tunnel_ip_pool_from_node')
    def test_delete_node_failure_keeps_local(self, remove):
        pool = TunnelIPPool.objects.create(
            pop_point=self.pop, name='n6-keep', cidr='10.35.0.0/24',
        )
        remove.return_value = {'ok': False, 'detail': '节点拒绝删除'}
        response = self.client.delete(f'/api/resource/tunnel-ip-pools/{pool.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(TunnelIPPool.objects.filter(pk=pool.pk).exists())
