"""资源分配 / 释放操作日志。"""
from django.contrib.auth.models import User
from django.test import TestCase

from customer_manager.models import Customer
from resource_manager.ip_ops import assign_ip_local_and_node, release_ip_local_and_node
from resource_manager.models import (
    BandwidthAllocation,
    City,
    Country,
    IPAddress,
    IPBlock,
    PopPoint,
    Region,
    ResourceOperationLog,
    VPS,
)
from resource_manager.resource_audit import log_bandwidth_change, snapshot_bandwidth


class ResourceAuditLogTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='aud', name='审计区')
        country = Country.objects.create(region=region, name='审计国')
        city = City.objects.create(country=country, name='审计市')
        self.pop = PopPoint.objects.create(name='审计 POP', city=city)
        self.user = User.objects.create_user('audit-op', password='SafePass_2026!')
        self.customer = Customer.objects.create(name='审计客户', contact_name='联系人')
        self.vps = VPS.objects.create(
            name='审计 VPS',
            pop_point=self.pop,
            customer=self.customer,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            status='running',
        )
        self.block = IPBlock.objects.create(pop_point=self.pop, cidr='198.51.100.0/24')
        self.ip = IPAddress.objects.create(
            ip_block=self.block,
            address='198.51.100.10',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )

    def test_assign_and_release_write_detailed_logs(self):
        assign_ip_local_and_node(
            self.ip,
            target_type='vps',
            vps=self.vps,
            push_to_node=False,
            operator=self.user,
            source='单测分配',
        )
        assign_log = ResourceOperationLog.objects.filter(
            action_type=ResourceOperationLog.ActionType.IP_ASSIGN,
            target_id=self.ip.id,
        ).first()
        self.assertIsNotNone(assign_log)
        self.assertIn('198.51.100.10', assign_log.description)
        self.assertEqual(assign_log.old_value['status'], 'available')
        self.assertEqual(assign_log.new_value['status'], 'assigned')
        self.assertEqual(assign_log.new_value['assigned_vps_id'], self.vps.id)
        self.assertEqual(assign_log.vps_id, self.vps.id)

        release_ip_local_and_node(
            self.ip, push_to_node=False, operator=self.user, source='单测释放',
        )
        release_log = ResourceOperationLog.objects.filter(
            action_type=ResourceOperationLog.ActionType.IP_RELEASE,
            target_id=self.ip.id,
        ).first()
        self.assertIsNotNone(release_log)
        self.assertEqual(release_log.old_value['status'], 'assigned')
        self.assertEqual(release_log.new_value['status'], 'available')
        self.assertEqual(release_log.vps_id, self.vps.id)

    def test_bandwidth_alloc_and_release_logs(self):
        alloc = BandwidthAllocation.objects.create(
            pop_point=self.pop,
            target_type=BandwidthAllocation.AllocTarget.VPS,
            vps=self.vps,
            allocated_mbps=50,
            is_active=True,
        )
        log_bandwidth_change(alloc, operator=self.user, source='单测带宽')
        created = ResourceOperationLog.objects.filter(
            action_type=ResourceOperationLog.ActionType.BW_ALLOC,
            target_id=alloc.id,
        ).first()
        self.assertIsNotNone(created)
        self.assertIn('50', created.description)

        old = snapshot_bandwidth(alloc)
        alloc.allocated_mbps = 80
        alloc.save(update_fields=['allocated_mbps'])
        log_bandwidth_change(alloc, old_value=old, operator=self.user, source='单测调带宽')
        updated = ResourceOperationLog.objects.filter(
            description__contains='50→80',
        ).first()
        self.assertIsNotNone(updated)

        log_bandwidth_change(
            alloc, action='release', old_value=snapshot_bandwidth(alloc),
            operator=self.user, source='单测回收',
        )
        released = ResourceOperationLog.objects.filter(
            action_type=ResourceOperationLog.ActionType.DELETE,
            target_id=alloc.id,
        ).first()
        self.assertIsNotNone(released)
        self.assertIn('回收带宽', released.description)
