from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from resource_manager.pve_ops import (
    locate_template_volume,
    resolve_template_create_payload,
    sanitize_vm_name,
    _merge_template_nodes,
    _pick_image_storage,
)
from resource_manager.deploy_service import (
    _build_tunnel_spec,
    _disable_orphaned_edge_telemetry_targets,
    _edge_telemetry_target_address,
    _sync_edge_telemetry,
)
from resource_manager.models import (
    AvailabilityZone,
    BandwidthAllocation,
    City,
    Country,
    EdgeAcceleration,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    TunnelIPPool,
    VPS,
)
from resource_manager.serializers import (
    AvailabilityZoneSerializer,
    EdgeAccelerationListSerializer,
    IPAddressSerializer,
    IPBlockSerializer,
    PopPointWriteSerializer,
    TunnelIPPoolSerializer,
    VPSListSerializer,
)
from resource_manager.views import (
    BandwidthAllocationViewSet,
    EdgeAccelerationViewSet,
    IPAddressViewSet,
    IPBlockViewSet,
    PopPointViewSet,
    TunnelIPPoolViewSet,
    VPSViewSet,
)


class EdgeTunnelMtuPayloadTests(SimpleTestCase):
    def test_gre_payload_contains_mtu(self):
        edge = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.GRE,
            gre_config=SimpleNamespace(
                local_ip='192.0.2.1',
                remote_ip='198.51.100.1',
                tunnel_ip='10.0.0.1/30',
                ttl=64,
                key='',
                mtu=1400,
            ),
        )

        kind, spec = _build_tunnel_spec(edge)

        self.assertEqual(kind, 'gre')
        self.assertEqual(spec['netplan_tunnel']['mtu'], 1400)

    def test_incomplete_gre_is_not_pushed(self):
        edge = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.GRE,
            gre_config=SimpleNamespace(
                local_ip='192.0.2.1',
                remote_ip=None,
                tunnel_ip='10.0.0.1/30',
                ttl=None,
                key='',
                mtu=1400,
            ),
        )
        self.assertIsNone(_build_tunnel_spec(edge))

    def test_vxlan_payload_contains_mtu(self):
        edge = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.VXLAN,
            vxlan_config=SimpleNamespace(
                vni=100,
                local_ip='192.0.2.1',
                remote_ip='198.51.100.1',
                tunnel_ip='10.0.0.1/30',
                port=8472,
                ttl=255,
                mtu=1450,
            ),
        )

        kind, spec = _build_tunnel_spec(edge)

        self.assertEqual(kind, 'vxlan')
        self.assertEqual(spec['netplan_tunnel']['mtu'], 1450)

    def test_wireguard_payload_defaults_mtu_to_1400(self):
        edge = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.WIREGUARD,
            wg_config=SimpleNamespace(
                interface_ip='10.0.0.1/30',
                private_key='private',
                public_key='public',
                listen_port=51820,
                fwmark=None,
                mtu=None,
                dns='',
                peer_public_key='peerpub',
                peer_endpoint='',
                peer_allowed_ips='',
                peer_preshared_key='',
                persistent_keepalive=25,
            ),
        )

        kind, spec = _build_tunnel_spec(edge)

        self.assertEqual(kind, 'wireguard')
        self.assertEqual(spec['netplan_tunnel']['mtu'], 1400)


class EdgeTelemetryTargetTests(SimpleTestCase):
    def test_peer_address_uses_protocol_specific_remote_endpoint(self):
        gre = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.GRE,
            gre_config=SimpleNamespace(remote_ip='198.51.100.10'),
        )
        wireguard = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.WIREGUARD,
            wg_config=SimpleNamespace(peer_endpoint='[2001:db8::10]:51820'),
        )
        vxlan = SimpleNamespace(
            protocol=EdgeAcceleration.Protocol.VXLAN,
            vxlan_config=SimpleNamespace(remote_ip='203.0.113.10'),
        )

        self.assertEqual(_edge_telemetry_target_address(gre), '198.51.100.10')
        self.assertEqual(_edge_telemetry_target_address(wireguard), '2001:db8::10')
        self.assertEqual(_edge_telemetry_target_address(vxlan), '203.0.113.10')

    def test_sync_creates_target_with_central_edge_identity_and_interface(self):
        class NodeClient:
            def __init__(self):
                self.created_target = None
                self.created_interface = None

            def monitor_targets(self):
                return []

            def monitor_interfaces(self):
                return []

            def create_monitor_target(self, payload):
                self.created_target = payload
                return {'id': 'target-1'}

            def create_monitor_interface(self, payload):
                self.created_interface = payload
                return {'id': 'interface-1'}

        client = NodeClient()
        edge = SimpleNamespace(
            pk=42,
            edge_no='EDGE-42',
            customer=SimpleNamespace(customer_no='C-42'),
            protocol=EdgeAcceleration.Protocol.GRE,
            gre_config=SimpleNamespace(remote_ip='198.51.100.42'),
        )

        result = _sync_edge_telemetry(client, edge, 'gre42')

        self.assertEqual(result['errors'], [])
        self.assertEqual(client.created_target['name'], 'isp-edge-quality-42')
        self.assertEqual(client.created_target['source_interface'], 'gre42')
        self.assertEqual(client.created_target['address'], '198.51.100.42')
        self.assertEqual(client.created_target['customer'], 'C-42')
        self.assertTrue(client.created_target['telemetry_enabled'])
        self.assertEqual(client.created_target['central_edge_id'], 42)
        self.assertEqual(client.created_interface['interface_name'], 'gre42')

    def test_orphan_cleanup_only_disables_isp_managed_targets(self):
        class NodeClient:
            def __init__(self):
                self.patches = []

            def monitor_targets(self):
                return [
                    {'id': 1, 'name': 'isp-edge-quality-42'},
                    {'id': 2, 'name': 'isp-edge-quality-removed'},
                    {'id': 3, 'name': '人工 DNS 探针'},
                ]

            def patch_monitor_target(self, target_id, payload):
                self.patches.append((target_id, payload))

        client = NodeClient()

        result = _disable_orphaned_edge_telemetry_targets(
            client,
            [SimpleNamespace(pk=42)],
        )

        self.assertEqual(result['errors'], [])
        self.assertEqual(result['disabled'], ['isp-edge-quality-removed'])
        self.assertEqual(len(client.patches), 1)
        self.assertEqual(client.patches[0][0], 2)
        self.assertFalse(client.patches[0][1]['enabled'])


class ResourceLocationFilterTests(TestCase):
    """区域筛选要在所有主要资源类型上保持相同语义。"""

    def setUp(self):
        self.apac = Region.objects.create(code='apac', name='亚太')
        self.europe = Region.objects.create(code='europe', name='欧洲')
        apac_city = City.objects.create(
            country=Country.objects.create(region=self.apac, name='日本'),
            name='东京',
        )
        eu_city = City.objects.create(
            country=Country.objects.create(region=self.europe, name='德国'),
            name='法兰克福',
        )
        self.apac_pop = PopPoint.objects.create(name='Tokyo POP', city=apac_city)
        self.eu_pop = PopPoint.objects.create(name='Frankfurt POP', city=eu_city)

        self.apac_block = IPBlock.objects.create(pop_point=self.apac_pop, cidr='203.0.113.0/24')
        eu_block = IPBlock.objects.create(pop_point=self.eu_pop, cidr='198.51.100.0/24')
        self.apac_address = IPAddress.objects.create(ip_block=self.apac_block, address='203.0.113.10')
        IPAddress.objects.create(ip_block=eu_block, address='198.51.100.10')

        self.apac_pool = TunnelIPPool.objects.create(
            pop_point=self.apac_pop, name='tokyo-tunnel', cidr='10.0.0.0/24',
        )
        TunnelIPPool.objects.create(
            pop_point=self.eu_pop, name='frankfurt-tunnel', cidr='10.1.0.0/24',
        )
        self.apac_vps = VPS.objects.create(name='tokyo-vps', pop_point=self.apac_pop)
        VPS.objects.create(name='frankfurt-vps', pop_point=self.eu_pop)
        self.apac_edge = EdgeAcceleration.objects.create(
            name='tokyo-edge',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.apac_pop,
        )
        eu_edge = EdgeAcceleration.objects.create(
            name='frankfurt-edge',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.eu_pop,
        )
        self.apac_bandwidth = BandwidthAllocation.objects.create(
            pop_point=self.apac_pop,
            target_type=BandwidthAllocation.AllocTarget.VPS,
            vps=self.apac_vps,
            allocated_mbps=100,
        )
        BandwidthAllocation.objects.create(
            pop_point=self.eu_pop,
            target_type=BandwidthAllocation.AllocTarget.EDGE,
            edge=eu_edge,
            allocated_mbps=100,
        )
        self.factory = APIRequestFactory()

    def _filter(self, view_class, queryset, **params):
        view = view_class()
        view.request = Request(self.factory.get('/resource/', params))
        view.args = ()
        view.kwargs = {}
        view.action = 'list'
        return view.filter_queryset(queryset)

    def test_region_filter_limits_every_resource_to_its_pop(self):
        cases = (
            (PopPointViewSet, PopPoint.objects.all(), self.apac_pop.pk),
            (IPBlockViewSet, IPBlock.objects.all(), self.apac_block.pk),
            (IPAddressViewSet, IPAddress.objects.all(), self.apac_address.pk),
            (TunnelIPPoolViewSet, TunnelIPPool.objects.all(), self.apac_pool.pk),
            (VPSViewSet, VPS.objects.all(), self.apac_vps.pk),
            (EdgeAccelerationViewSet, EdgeAcceleration.objects.all(), self.apac_edge.pk),
            (BandwidthAllocationViewSet, BandwidthAllocation.objects.all(), self.apac_bandwidth.pk),
        )
        for view_class, queryset, expected_pk in cases:
            with self.subTest(view=view_class.__name__):
                result = self._filter(view_class, queryset, region=self.apac.pk)
                self.assertEqual(list(result.values_list('pk', flat=True)), [expected_pk])

    def test_pop_filter_and_ip_location_fields_follow_ip_block(self):
        result = self._filter(
            IPAddressViewSet,
            IPAddress.objects.all(),
            pop_point=self.apac_pop.pk,
        )
        self.assertEqual(list(result.values_list('pk', flat=True)), [self.apac_address.pk])

        payload = IPAddressSerializer(
            IPAddress.objects.select_related(
                'ip_block__pop_point__city__country__region',
            ).get(pk=self.apac_address.pk),
        ).data
        self.assertEqual(payload['region_id'], self.apac.pk)
        self.assertEqual(payload['region_name'], '亚太')
        self.assertEqual(payload['country_name'], '日本')
        self.assertEqual(payload['city_name'], '东京')
        self.assertEqual(payload['pop_name'], 'Tokyo POP')
        self.assertIn('zone_name', payload)

    def test_availability_zone_filter_and_city_constraint(self):
        tokyo = self.apac_pop.city
        zone = AvailabilityZone.objects.create(city=tokyo, name='东京 A', code='tokyo-a')
        other = AvailabilityZone.objects.create(city=self.eu_pop.city, name='法兰克福 A', code='fra-a')
        self.apac_pop.availability_zone = zone
        self.apac_pop.save(update_fields=['availability_zone'])

        result = self._filter(
            PopPointViewSet,
            PopPoint.objects.all(),
            availability_zone=zone.pk,
        )
        self.assertEqual(list(result.values_list('pk', flat=True)), [self.apac_pop.pk])
        ip_result = self._filter(
            IPAddressViewSet,
            IPAddress.objects.all(),
            availability_zone=zone.pk,
        )
        self.assertEqual(list(ip_result.values_list('pk', flat=True)), [self.apac_address.pk])

        serializer = PopPointWriteSerializer(
            self.apac_pop,
            data={'availability_zone': other.pk},
            partial=True,
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('availability_zone', serializer.errors)

        az_ser = AvailabilityZoneSerializer(data={
            'city': tokyo.pk, 'name': '东京 B', 'code': 'tokyo-a',
        })
        self.assertFalse(az_ser.is_valid())
        self.assertIn('code', az_ser.errors)

    def test_blank_pop_secrets_keep_saved_values(self):
        pop = self.apac_pop
        pop.mgmt_password = 'mgmt-secret'
        pop.api_password = 'api-secret'
        pop.mgmt_token = 'pem_keep.secret'
        pop.api_base_url = 'http://10.0.0.8'
        pop.api_username = 'nodeadmin'
        pop.mgmt_username = 'root'
        pop.save()

        serializer = PopPointWriteSerializer(pop, data={
            'mgmt_password': '',
            'api_password': '   ',
            'mgmt_token': '',
            'remark': '仅改备注',
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        pop.refresh_from_db()
        self.assertEqual(pop.mgmt_password, 'mgmt-secret')
        self.assertEqual(pop.api_password, 'api-secret')
        self.assertEqual(pop.mgmt_token, 'pem_keep.secret')
        self.assertEqual(pop.api_username, 'nodeadmin')
        self.assertEqual(pop.api_base_url, 'http://10.0.0.8')
        self.assertEqual(pop.remark, '仅改备注')

        serializer = PopPointWriteSerializer(pop, data={
            'api_password': 'new-api',
            'api_base_url': 'https://node.example',
            'mgmt_username': 'ubuntu',
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        pop.refresh_from_db()
        self.assertEqual(pop.api_password, 'new-api')
        self.assertEqual(pop.mgmt_password, 'mgmt-secret')
        self.assertEqual(pop.mgmt_token, 'pem_keep.secret')
        self.assertEqual(pop.mgmt_username, 'ubuntu')
        self.assertEqual(pop.api_base_url, 'https://node.example')

    def test_resource_list_serializers_reuse_prefetched_ip_addresses(self):
        vps_queryset = VPS.objects.select_related(
            'pop_point__city__country__region',
            'pop_point__availability_zone',
            'customer',
        ).prefetch_related('ip_addresses')
        edge_queryset = EdgeAcceleration.objects.select_related(
            'pop_point__city__country__region',
            'pop_point__availability_zone',
            'customer',
        ).prefetch_related('ip_addresses')

        with self.assertNumQueries(2):
            list(VPSListSerializer(vps_queryset, many=True).data)
        with self.assertNumQueries(2):
            list(EdgeAccelerationListSerializer(edge_queryset, many=True).data)

    def test_tunnel_pool_list_prefetches_taken_ips_without_per_pool_queries(self):
        queryset = TunnelIPPoolViewSet().get_queryset()
        with self.assertNumQueries(4):
            list(TunnelIPPoolSerializer(queryset, many=True).data)

    def test_ip_block_list_uses_annotated_usage_counts(self):
        queryset = IPBlockViewSet().get_queryset()
        with self.assertNumQueries(1):
            payload = list(IPBlockSerializer(queryset, many=True).data)
        self.assertEqual(payload[0]['ip_stats']['total'], 1)


class SanitizeVmNameTests(SimpleTestCase):
    def test_keeps_valid_hostname(self):
        self.assertEqual(sanitize_vm_name('tokyo-web-01'), 'tokyo-web-01')

    def test_prefixes_leading_digit_and_strips_invalid(self):
        self.assertEqual(sanitize_vm_name('12号机'), 'vm-12')


class CreateFromTemplateAsyncTests(SimpleTestCase):
    def test_queued_job_is_returned_without_refresh(self):
        from unittest.mock import patch

        from resource_manager import pve_ops

        pop = SimpleNamespace()
        client = SimpleNamespace()
        client.create_vm_from_template = lambda payload: {
            'async': True, 'job_id': 'job-1', 'status': 'queued', 'progress': '已排队',
        }
        with patch.object(pve_ops, '_connect', return_value=(client, 'node-admin')), \
             patch.object(pve_ops, '_refresh_vms') as refresh:
            result = pve_ops.create_from_template(
                pop, {'newname': '12号机', 'template_id': 1, 'vmid': 200}, dry_run=False,
            )
        self.assertTrue(result['async'])
        self.assertEqual(result['job_id'], 'job-1')
        self.assertEqual(result['status'], 'queued')
        refresh.assert_not_called()

    def test_provision_vps_uses_node_orchestration_api(self):
        from unittest.mock import patch

        from resource_manager import pve_ops

        pop = SimpleNamespace()
        captured = {}

        def fake_provision(payload):
            captured.update(payload)
            return {
                'async': True, 'job_id': 'job-vps', 'status': 'queued',
                'vmid': payload.get('vmid'), 'os_code': payload.get('os_code'),
            }

        client = SimpleNamespace(vps_provision=fake_provision)
        with patch.object(pve_ops, '_connect', return_value=(client, 'node-admin')), \
             patch.object(pve_ops, '_refresh_vms') as refresh:
            result = pve_ops.provision_vps(pop, {
                'name': '12号机',
                'os_code': 'ubuntu-24.04',
                'memory': 2048,
                'vmid': 201,
            }, dry_run=False)
        self.assertTrue(result['async'])
        self.assertEqual(result['job_id'], 'job-vps')
        self.assertEqual(captured['name'], 'vm-12')
        self.assertEqual(captured['os_code'], 'ubuntu-24.04')
        refresh.assert_not_called()

    def test_poll_succeeded_job_refreshes_vms(self):
        from unittest.mock import patch

        from resource_manager import pve_ops

        pop = SimpleNamespace()
        client = SimpleNamespace()
        client.get_pve_job = lambda job_id: {
            'job_id': job_id, 'status': 'succeeded',
            'result': {'vmid': 200, 'name': 'vm-ok'},
        }
        with patch.object(pve_ops, '_connect', return_value=(client, 'node-admin')), \
             patch.object(pve_ops, '_refresh_vms', return_value={'created': 1}) as refresh:
            result = pve_ops.poll_pve_job(pop, 'job-1')
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['refreshed'], {'created': 1})
        refresh.assert_called_once()


class ResolveTemplateCreatePayloadTests(SimpleTestCase):
    def _template(self, **overrides):
        data = {
            'id': 7,
            'host': 3,
            'name': 'Debian12',
            'source_type': 'cloud',
            'node': 's79157',
            'storage': 'local',
            'volume': 'local:iso/debian-12-genericcloud-amd64.qcow2',
            'filename': 'debian-12-genericcloud-amd64.qcow2',
            'download_url': 'https://example.com/debian-12-genericcloud-amd64.qcow2',
        }
        data.update(overrides)
        return data

    def test_backup_uses_template_node_instead_of_host_alias(self):
        client = SimpleNamespace(
            list_templates=lambda: [self._template()],
            list_pve_hosts=lambda: [],
            host_storages=lambda host_id: [
                {'storage': 'local', 'content': 'backup,iso', 'node': 's79157', 'type': 'dir'},
                {'storage': 'local-lvm', 'content': 'images', 'node': 's79157', 'type': 'lvmthin'},
            ],
            host_storage_content=lambda *args, **kwargs: [],
            host_backups=lambda *args, **kwargs: [],
        )
        payload = resolve_template_create_payload(client, {
            'template_id': 7,
            'host_id': 3,
            'node': 'singapore-pve1',
            'storage': 'local',
            'newname': 'test123',
            'vmid': 201,
        })
        self.assertEqual(payload['node'], 's79157')
        self.assertEqual(payload['download_url'], 'https://example.com/debian-12-genericcloud-amd64.qcow2')
        self.assertEqual(payload['storage'], 'local-lvm')

    def test_locate_volume_overrides_stale_template_node(self):
        client = SimpleNamespace()
        client.host_storage_content = lambda host_id, node, storage, content=None: (
            [{'volid': 'local:iso/debian-12-genericcloud-amd64.qcow2'}]
            if node == 'pve-real' else []
        )
        found = locate_template_volume(
            client, 3, self._template(node='old-node'),
            ['singapore-pve1', 'pve-real'],
        )
        self.assertEqual(found['node'], 'pve-real')

    def test_template_vm_follows_template_node(self):
        client = SimpleNamespace(
            list_templates=lambda: [self._template(source_type='template_vm', node='s79157')],
            list_pve_hosts=lambda: [],
            host_storages=lambda host_id: [],
        )
        payload = resolve_template_create_payload(client, {
            'template_id': 7, 'host_id': 3, 'node': 'singapore-pve1',
        })
        self.assertEqual(payload['node'], 's79157')

    def test_merge_template_nodes_adds_missing_cluster_name(self):
        hosts = [{
            'id': 3,
            'default_node': 'singapore-pve1',
            'nodes': [{'node': 'singapore-pve1', 'status': 'unknown'}],
        }]
        _merge_template_nodes(hosts, [self._template()])
        names = [item['node'] for item in hosts[0]['nodes']]
        self.assertIn('s79157', names)
        self.assertEqual(hosts[0]['preferred_node'], 's79157')

    def test_pick_image_storage_skips_backup_only(self):
        storage = _pick_image_storage(
            [
                {'storage': 'local', 'content': 'backup,iso', 'type': 'dir', 'node': 's79157'},
                {'storage': 'local-lvm', 'content': 'images', 'type': 'lvmthin', 'node': 's79157'},
            ],
            's79157',
            'local',
        )
        self.assertEqual(storage, 'local-lvm')
