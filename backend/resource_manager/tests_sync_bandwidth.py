"""节点带宽分配同步：VM 网卡 + 隧道接口。"""
from django.test import TestCase
from django.utils import timezone

from resource_manager.models import (
    BandwidthAllocation,
    City,
    Country,
    EdgeAcceleration,
    PopPoint,
    Region,
    VPS,
)
from resource_manager.sync_service import _sync_bandwidth


class SyncBandwidthFromNodeTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='bw-sync', name='同步带宽区')
        country = Country.objects.create(region=region, name='同步带宽国')
        city = City.objects.create(country=country, name='同步带宽市')
        self.pop = PopPoint.objects.create(name='同步带宽 POP', city=city)
        self.vps = VPS.objects.create(
            name='同步 VPS',
            pop_point=self.pop,
            is_synced=True,
            source_ref='9',
            bandwidth_mbps=0,
        )
        self.edge = EdgeAcceleration.objects.create(
            name='同步隧道',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            node_ifname='gre-cust1',
            bandwidth_mbps=0,
        )

    def test_imports_vm_and_tunnel_allocations(self):
        now = timezone.now()
        summary = _sync_bandwidth(self.pop, [
            {'interface_code': 'vm-9-net0', 'allocated_mbps': 200},
            {'interface_code': 'gre-cust1', 'allocated_mbps': 500},
            {'interface_code': 'wg-orphan', 'allocated_mbps': 100},
        ], now)

        self.assertEqual(summary['imported'], 3)
        self.assertEqual(summary['skipped'], 0)
        self.vps.refresh_from_db()
        self.edge.refresh_from_db()
        self.assertEqual(self.vps.bandwidth_mbps, 200)
        self.assertEqual(self.edge.bandwidth_mbps, 500)

        tunnel = BandwidthAllocation.objects.get(
            pop_point=self.pop, source_interface_code='gre-cust1', is_synced=True,
        )
        self.assertEqual(tunnel.target_type, BandwidthAllocation.AllocTarget.EDGE)
        self.assertEqual(tunnel.edge_id, self.edge.id)
        self.assertEqual(tunnel.allocated_mbps, 500)

        orphan = BandwidthAllocation.objects.get(
            pop_point=self.pop, source_interface_code='wg-orphan', is_synced=True,
        )
        self.assertEqual(orphan.target_type, BandwidthAllocation.AllocTarget.EDGE)
        self.assertIsNone(orphan.edge_id)
        self.assertEqual(orphan.allocated_mbps, 100)

    def test_removes_stale_tunnel_allocation(self):
        now = timezone.now()
        _sync_bandwidth(self.pop, [
            {'interface_code': 'gre-cust1', 'allocated_mbps': 300},
        ], now)
        _sync_bandwidth(self.pop, [
            {'interface_code': 'vm-9-net0', 'allocated_mbps': 50},
        ], now)
        self.assertFalse(
            BandwidthAllocation.objects.filter(
                pop_point=self.pop, source_interface_code='gre-cust1',
            ).exists()
        )
        self.edge.refresh_from_db()
        self.assertEqual(self.edge.bandwidth_mbps, 0)
