"""N3：带宽分配保存时按需下发到节点。"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from resource_manager.deploy_service import push_bandwidth_allocation_to_node
from resource_manager.models import (
    BandwidthAllocation,
    City,
    Country,
    EdgeAcceleration,
    PopPoint,
    Region,
    VPS,
)


class PushBandwidthAllocationHelperTests(SimpleTestCase):
    def test_skip_when_pop_has_no_api(self):
        pop = SimpleNamespace(
            name='bare', api_base_url='', mgmt_ip=None, mgmt_token='',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
        )
        alloc = SimpleNamespace(pop_point=pop, vps_id=None, edge_id=None)
        result = push_bandwidth_allocation_to_node(alloc)
        self.assertTrue(result['skipped'])
        self.assertTrue(result['ok'])

    def test_skip_unsynced_vps(self):
        pop = SimpleNamespace(
            name='tok', api_base_url='http://node.example', mgmt_token='pem_x.y',
            api_username='', api_password='', mgmt_username='', mgmt_password='',
            mgmt_ip=None,
        )
        vps = SimpleNamespace(is_synced=False, source_ref='')
        alloc = SimpleNamespace(pop_point=pop, vps_id=1, vps=vps, edge_id=None)
        result = push_bandwidth_allocation_to_node(alloc)
        self.assertTrue(result['skipped'])


class BandwidthAllocationPushApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('n3-admin', 'n3@example.test', 'password')
        region = Region.objects.create(code='n3a', name='带宽区')
        country = Country.objects.create(region=region, name='带宽国')
        city = City.objects.create(country=country, name='带宽市')
        self.pop = PopPoint.objects.create(
            name='带宽 POP',
            city=city,
            api_base_url='http://node.example',
            mgmt_token='pem_bw.secret',
        )
        self.vps = VPS.objects.create(
            name='带宽 VPS',
            pop_point=self.pop,
            is_synced=True,
            source_ref='42',
            bandwidth_mbps=50,
        )
        self.edge = EdgeAcceleration.objects.create(
            name='带宽边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            node_ifname='gre-n3',
            bandwidth_mbps=50,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_create_without_api_skips_node(self):
        self.pop.api_base_url = ''
        self.pop.mgmt_token = ''
        self.pop.save(update_fields=['api_base_url', 'mgmt_token'])
        with patch(
            'resource_manager.deploy_service.push_vps_bandwidth_to_node',
        ) as push_vps:
            response = self.client.post(
                '/api/resource/bandwidth-allocations/',
                {
                    'pop_point': self.pop.pk,
                    'target_type': 'vps',
                    'vps': self.vps.pk,
                    'allocated_mbps': 200,
                },
                format='json',
            )
        self.assertEqual(response.status_code, 201, response.data)
        push_vps.assert_not_called()
        self.assertTrue(
            BandwidthAllocation.objects.filter(vps=self.vps, allocated_mbps=200).exists()
        )

    @patch('resource_manager.deploy_service.push_vps_bandwidth_to_node')
    def test_create_synced_vps_pushes_node(self, push_vps):
        push_vps.return_value = {'ok': True, 'apply_ok': True}
        response = self.client.post(
            '/api/resource/bandwidth-allocations/',
            {
                'pop_point': self.pop.pk,
                'target_type': 'vps',
                'vps': self.vps.pk,
                'allocated_mbps': 300,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push_vps.assert_called()

    @patch('resource_manager.deploy_service.push_bandwidth_allocation_to_node')
    def test_push_to_node_false_skips(self, push_alloc):
        response = self.client.post(
            '/api/resource/bandwidth-allocations/?push_to_node=false',
            {
                'pop_point': self.pop.pk,
                'target_type': 'vps',
                'vps': self.vps.pk,
                'allocated_mbps': 120,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push_alloc.assert_not_called()

    @patch('resource_manager.deploy_service.push_vps_bandwidth_to_node')
    def test_node_failure_rolls_back_local_row(self, push_vps):
        push_vps.return_value = {'ok': False, 'detail': '节点拒绝'}
        response = self.client.post(
            '/api/resource/bandwidth-allocations/',
            {
                'pop_point': self.pop.pk,
                'target_type': 'vps',
                'vps': self.vps.pk,
                'allocated_mbps': 400,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            BandwidthAllocation.objects.filter(vps=self.vps, allocated_mbps=400).exists()
        )

    @patch('resource_manager.deploy_service.PemanagerClient.connect_pop')
    def test_edge_allocation_upserts_qos(self, connect):
        client = MagicMock()
        client.list_bandwidth_pools.return_value = [{'name': 'ispmanager'}]
        client.upsert_bandwidth_allocation.return_value = {
            'ok': True, 'qos': {'applied': True},
        }
        connect.return_value = (client, {'auth': 'token'})
        response = self.client.post(
            '/api/resource/bandwidth-allocations/',
            {
                'pop_point': self.pop.pk,
                'target_type': 'edge',
                'edge': self.edge.pk,
                'allocated_mbps': 80,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        client.upsert_bandwidth_allocation.assert_called()
        payload = client.upsert_bandwidth_allocation.call_args[0][0]
        self.assertEqual(payload['interface_code'], 'gre-n3')
        self.assertTrue(payload.get('apply_qos'))

    @patch('resource_manager.deploy_service.push_vps_bandwidth_to_node')
    def test_unsynced_vps_creates_without_node_call(self, push_vps):
        self.vps.is_synced = False
        self.vps.source_ref = ''
        self.vps.save(update_fields=['is_synced', 'source_ref'])
        response = self.client.post(
            '/api/resource/bandwidth-allocations/',
            {
                'pop_point': self.pop.pk,
                'target_type': 'vps',
                'vps': self.vps.pk,
                'allocated_mbps': 90,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        push_vps.assert_not_called()

    @patch('resource_manager.deploy_service.push_vps_bandwidth_to_node')
    def test_update_synced_vps_pushes_node(self, push_vps):
        alloc = BandwidthAllocation.objects.create(
            pop_point=self.pop,
            target_type=BandwidthAllocation.AllocTarget.VPS,
            vps=self.vps,
            allocated_mbps=50,
        )
        push_vps.return_value = {'ok': True, 'apply_ok': True}
        response = self.client.patch(
            f'/api/resource/bandwidth-allocations/{alloc.pk}/',
            {'allocated_mbps': 180},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        push_vps.assert_called()

    @patch('resource_manager.deploy_service.remove_bandwidth_allocation_from_node')
    def test_delete_calls_node_remove(self, remove):
        alloc = BandwidthAllocation.objects.create(
            pop_point=self.pop,
            target_type=BandwidthAllocation.AllocTarget.VPS,
            vps=self.vps,
            allocated_mbps=50,
        )
        remove.return_value = {'ok': True}
        response = self.client.delete(f'/api/resource/bandwidth-allocations/{alloc.pk}/')
        self.assertEqual(response.status_code, 204)
        remove.assert_called()
        self.assertFalse(BandwidthAllocation.objects.filter(pk=alloc.pk).exists())

    @patch('resource_manager.deploy_service.remove_bandwidth_allocation_from_node')
    def test_delete_node_failure_keeps_local(self, remove):
        alloc = BandwidthAllocation.objects.create(
            pop_point=self.pop,
            target_type=BandwidthAllocation.AllocTarget.VPS,
            vps=self.vps,
            allocated_mbps=50,
        )
        remove.return_value = {'ok': False, 'detail': '节点拒绝删除'}
        response = self.client.delete(f'/api/resource/bandwidth-allocations/{alloc.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(BandwidthAllocation.objects.filter(pk=alloc.pk).exists())
