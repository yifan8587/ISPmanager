"""服务变更报价与升降配计费单测。"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import City, Country, IPAddress, IPBlock, PopPoint, Region, VPS
from service_manager.change_billing import (
    ChangeBillingError,
    quote_service_change,
    submit_service_change,
)
from service_manager.change_provisioning import apply_paid_change, deliver_change_order
from service_manager.models import ServiceChangeRecord, ServiceInstance, ServiceOrder


class ServiceChangeBillingTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='chg-bill', name='变更计费区')
        country = Country.objects.create(region=region, name='变更计费国')
        city = City.objects.create(country=country, name='变更计费市')
        self.pop = PopPoint.objects.create(name='变更计费 POP', city=city)
        self.user = User.objects.create_user(username='chg-bill-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='变更计费客户',
            contact_name='联系人',
            account=self.user,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.save(update_fields=['account_type'])
        self.product = Product.objects.create(
            name='变更计费产品',
            category='vps',
            status='online',
            pop_point=self.pop,
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=1,
            monthly_price=Decimal('100.00'),
            setup_price=Decimal('0.00'),
            currency='CNY',
        )
        self.order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.ACTIVE,
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            quoted_price=Decimal('100.00'),
        )
        self.vps = VPS.objects.create(
            name='变更 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            status='running',
        )
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type='vps',
            status='active',
            vps=self.vps,
            monthly_fee=Decimal('100.00'),
            fee_currency='CNY',
            billing_start_date=timezone.localdate().replace(day=1),
        )

    def test_quote_upgrade_requires_prorated_due_now(self):
        with patch(
            'service_manager.change_billing.compute_monthly_for_spec',
            return_value=(Decimal('180.00'), 'CNY', {'breakdown': [], 'monthly_price': '180.00'}),
        ):
            quote = quote_service_change(self.instance, {
                'requested_vcpu': 2,
                'requested_ram_mb': 2048,
                'requested_bandwidth_mbps': 50,
                'requested_ip_count': 1,
            })
        self.assertEqual(quote['direction'], 'upgrade')
        self.assertEqual(quote['old_monthly_fee'], '100.00')
        self.assertEqual(quote['new_monthly_fee'], '180.00')
        self.assertGreater(Decimal(quote['due_now']), Decimal('0'))
        self.assertEqual(quote['effective_policy'], 'pay_then_apply')

    def test_quote_downgrade_immediate_credit(self):
        self.instance.monthly_fee = Decimal('200.00')
        self.instance.save(update_fields=['monthly_fee'])
        with patch(
            'service_manager.change_billing.compute_monthly_for_spec',
            return_value=(Decimal('80.00'), 'CNY', {'breakdown': []}),
        ):
            quote = quote_service_change(self.instance, {
                'requested_bandwidth_mbps': 5,
            })
        self.assertEqual(quote['direction'], 'downgrade')
        self.assertEqual(quote['due_now'], '0.00')
        self.assertEqual(quote['effective_policy'], 'immediate_credit')
        self.assertGreater(Decimal(quote['credit_now']), Decimal('0'))
        self.assertEqual(quote['old_monthly_fee'], '200.00')
        self.assertEqual(quote['new_monthly_fee'], '80.00')

    def test_quote_ip_replace_is_manual(self):
        quote = quote_service_change(self.instance, {
            'replace_ips': True,
            'requested_ip_count': 1,
        })
        self.assertEqual(quote['direction'], 'ip_replace')
        self.assertEqual(quote['effective_policy'], 'manual_confirm')
        self.assertEqual(quote['due_now'], '0.00')

    def test_submit_downgrade_schedules_without_payment(self):
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'downgrade',
                'current_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 1, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 5, 'ip_count': 1, 'nic_count': 0,
                },
                'old_monthly_fee': '200.00',
                'new_monthly_fee': '80.00',
                'monthly_delta': '-120.00',
                'due_now': '0.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-09-01',
                'effective_policy': 'next_cycle',
                'policy_text': '降配本月费用保持不变',
                'price_breakdown': [],
                'has_product_pricing': True,
            },
        ):
            result = submit_service_change(
                self.instance,
                requested={'requested_bandwidth_mbps': 5},
                user=self.user,
            )
        self.assertFalse(result['payment_required'])
        self.assertIsNone(result['billing'])
        self.assertEqual(result['change'].status, 'approved')
        self.assertIsNotNone(result['change'].scheduled_at)
        self.assertEqual(result['order'].order_type, 'downgrade')

    def test_submit_immediate_downgrade_applies_now(self):
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'downgrade',
                'current_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 0, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 5, 'ip_count': 0, 'nic_count': 0,
                },
                'old_monthly_fee': '100.00',
                'new_monthly_fee': '80.00',
                'monthly_delta': '-20.00',
                'due_now': '0.00',
                'credit_now': '8.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-08-31',
                'effective_policy': 'immediate_credit',
                'policy_text': '降配立即生效',
                'price_breakdown': [],
                'has_product_pricing': True,
            },
        ):
            result = submit_service_change(
                self.instance,
                requested={'requested_bandwidth_mbps': 5},
                user=self.user,
            )
        self.assertFalse(result['payment_required'])
        self.vps.refresh_from_db()
        self.instance.refresh_from_db()
        result['change'].refresh_from_db()
        self.assertEqual(self.vps.bandwidth_mbps, 5)
        self.assertEqual(result['change'].status, ServiceChangeRecord.ChangeStatus.COMPLETED)
        self.assertIsNone(result['change'].scheduled_at)

    def test_submit_ip_replace_stays_manual(self):
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'ip_replace',
                'current_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 1, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 1, 'nic_count': 0,
                },
                'old_monthly_fee': '100.00',
                'new_monthly_fee': '100.00',
                'monthly_delta': '0.00',
                'due_now': '0.00',
                'credit_now': '0.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-08-31',
                'effective_policy': 'manual_confirm',
                'policy_text': '替换 IP 需人工确认',
                'price_breakdown': [],
                'has_product_pricing': True,
            },
        ):
            result = submit_service_change(
                self.instance,
                requested={'replace_ips': True},
                user=self.user,
            )
        self.assertFalse(result['payment_required'])
        self.assertEqual(result['change'].change_type, ServiceChangeRecord.ChangeType.IP_CHANGE)
        self.assertEqual(result['change'].status, ServiceChangeRecord.ChangeStatus.PENDING)
        self.assertEqual(result['order'].status, ServiceOrder.OrderStatus.PENDING)

    def test_submit_unchanged_spec_raises(self):
        with self.assertRaises(ChangeBillingError):
            submit_service_change(self.instance, requested={}, user=self.user)

    def _approved_upgrade(self):
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'upgrade',
                'current_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 0, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 2, 'ram_mb': 2048, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 0, 'nic_count': 0,
                },
                'old_monthly_fee': '100.00',
                'new_monthly_fee': '180.00',
                'monthly_delta': '80.00',
                'due_now': '0.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-08-22',
                'effective_policy': 'pay_then_apply',
                'policy_text': '无需补差价',
                'price_breakdown': [],
                'has_product_pricing': True,
            },
        ):
            return submit_service_change(
                self.instance,
                requested={'requested_vcpu': 2, 'requested_ram_mb': 2048},
                user=self.user,
            )

    @patch('service_manager.change_provisioning._check_resources', return_value={
        'ok': False, 'shortages': ['CPU 不足：需 1，剩余 0'],
    })
    def test_paid_change_waits_when_capacity_insufficient(self, _check):
        result = self._approved_upgrade()
        order = result['order']
        change = result['change']
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.save(update_fields=['status'])
        outcome = apply_paid_change(order, change, operator=self.user)
        self.assertFalse(outcome['triggered'])
        self.assertEqual(outcome['reason'], 'insufficient')
        order.refresh_from_db()
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.INSUFFICIENT)
        change.refresh_from_db()
        self.assertNotEqual(change.status, ServiceChangeRecord.ChangeStatus.COMPLETED)

    @patch('service_manager.change_provisioning._check_resources', return_value={'ok': True, 'shortages': []})
    def test_paid_change_auto_provisions_when_capacity_ok(self, _check):
        result = self._approved_upgrade()
        order = result['order']
        change = result['change']
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.save(update_fields=['status'])
        outcome = apply_paid_change(order, change, operator=self.user)
        self.assertTrue(outcome.get('triggered'), outcome)
        order.refresh_from_db()
        self.assertEqual(order.status, ServiceOrder.OrderStatus.ACTIVE)
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.SUCCESS)
        self.vps.refresh_from_db()
        self.assertEqual(self.vps.vcpu, 2)
        self.assertEqual(self.vps.ram_mb, 2048)

    @patch('service_manager.change_provisioning._check_resources', return_value={
        'ok': False, 'shortages': ['IP 数量 不足：需 1，剩余 0'],
    })
    def test_manual_delivery_completes_insufficient_change(self, _check):
        result = self._approved_upgrade()
        order = result['order']
        change = result['change']
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.save(update_fields=['status'])
        apply_paid_change(order, change, operator=self.user)
        delivered = deliver_change_order(order, ip_ids=[], operator=self.user, remark='资源已补齐')
        self.assertEqual(delivered['change_no'], change.change_no)
        order.refresh_from_db()
        self.assertEqual(order.auto_provision_status, ServiceOrder.AutoProvisionStatus.SUCCESS)

    def test_quote_includes_allocated_ips_and_rejects_foreign_release(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.8',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_vps=self.vps,
        )
        self.instance.ip_addresses.add(ip)
        quote = quote_service_change(self.instance, {})
        self.assertEqual(len(quote['allocated_ips']), 1)
        self.assertEqual(quote['allocated_ips'][0]['address'], '203.0.113.8')
        with self.assertRaises(ChangeBillingError):
            quote_service_change(self.instance, {'release_ip_ids': [ip.id + 999]})

    def test_quote_release_and_add_updates_ip_count(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.9',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_vps=self.vps,
        )
        self.instance.ip_addresses.add(ip)
        quote = quote_service_change(self.instance, {
            'release_ip_ids': [ip.id],
            'add_ip_count': 2,
            'requested_bandwidth_mbps': 10,
        })
        self.assertEqual(quote['release_ip_ids'], [ip.id])
        self.assertEqual(quote['add_ip_count'], 2)
        self.assertEqual(quote['target_spec']['ip_count'], 2)

    def test_apply_releases_selected_ip(self):
        from service_manager.change_provisioning import release_selected_ips

        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.11',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_vps=self.vps,
        )
        self.instance.ip_addresses.add(ip)
        result = release_selected_ips(self.instance, [ip.id], push_to_node=False)
        self.assertEqual(result['released'], ['203.0.113.11'])
        ip.refresh_from_db()
        self.assertEqual(ip.status, IPAddress.IPStatus.AVAILABLE)
        self.assertFalse(self.instance.ip_addresses.filter(pk=ip.pk).exists())

    def test_quote_without_ip_request_keeps_current_ip_count(self):
        quote = quote_service_change(self.instance, {'requested_bandwidth_mbps': 10})
        self.assertEqual(quote['target_spec']['ip_count'], quote['current_spec']['ip_count'])
        self.assertEqual(quote['direction'], 'unchanged')
        self.assertEqual(quote['bandwidth_action'], 'keep')

    def test_quote_includes_vps_assigned_ip_not_on_instance(self):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.21',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_vps=self.vps,
        )
        quote = quote_service_change(self.instance, {})
        self.assertEqual([row['address'] for row in quote['allocated_ips']], ['203.0.113.21'])
        self.assertEqual(quote['current_spec']['ip_count'], 1)
        from service_manager.change_provisioning import release_selected_ips
        result = release_selected_ips(self.instance, [ip.id], push_to_node=False)
        self.assertEqual(result['released'], ['203.0.113.21'])
        ip.refresh_from_db()
        self.assertEqual(ip.status, IPAddress.IPStatus.AVAILABLE)
        self.assertIsNone(ip.assigned_vps_id)

    @patch('service_manager.change_provisioning._check_resources', return_value={'ok': True, 'shortages': []})
    def test_apply_paid_change_reports_progress_steps_for_selected_release(self, _check):
        block = IPBlock.objects.create(pop_point=self.pop, cidr='203.0.113.0/24')
        ip = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.31',
            prefix_len=24,
            status=IPAddress.IPStatus.ASSIGNED,
            assigned_vps=self.vps,
        )
        spare = IPAddress.objects.create(
            ip_block=block,
            address='203.0.113.32',
            prefix_len=24,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        self.instance.ip_addresses.add(ip)
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'change',
                'current_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 10, 'ip_count': 1, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 15, 'ip_count': 1, 'nic_count': 0,
                },
                'old_monthly_fee': '100.00',
                'new_monthly_fee': '100.00',
                'monthly_delta': '0.00',
                'due_now': '0.00',
                'credit_now': '0.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-08-31',
                'effective_policy': 'immediate_apply',
                'policy_text': '规格有调整但月费不变',
                'price_breakdown': [],
                'has_product_pricing': True,
                'release_ip_ids': [ip.id],
                'add_ip_count': 1,
            },
        ):
            result = submit_service_change(
                self.instance,
                requested={
                    'requested_bandwidth_mbps': 15,
                    'release_ip_ids': [ip.id],
                    'add_ip_count': 1,
                },
                user=self.user,
            )
        applied = result['applied']
        self.assertTrue(applied.get('triggered'), applied)
        self.assertEqual(applied.get('released_ips'), ['203.0.113.31'])
        self.assertEqual(applied.get('added_ips'), [str(spare.address)])
        keys = [step['key'] for step in applied.get('progress_steps') or []]
        self.assertIn('billing', keys)
        self.assertIn('allocate', keys)
        self.assertIn('release', keys)
        self.assertIn('spec', keys)
        self.assertIn('activate', keys)
        ip.refresh_from_db()
        spare.refresh_from_db()
        self.assertEqual(ip.status, IPAddress.IPStatus.AVAILABLE)
        self.assertEqual(spare.status, IPAddress.IPStatus.ASSIGNED)
        self.assertEqual(result['message'][:4], '变更成功')
