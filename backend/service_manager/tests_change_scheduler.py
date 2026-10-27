"""S4：降配到期执行、超卖与开通共用规则、变更跟进工单。"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import UserProfile
from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region, VPS
from service_manager.change_billing import submit_service_change
from service_manager.change_provisioning import apply_paid_change
from service_manager.change_scheduler import (
    ensure_change_followup_ticket,
    process_due_scheduled_changes,
)
from service_manager.models import ServiceChangeRecord, ServiceInstance, ServiceOrder, SupportTicket
from service_manager.provisioning import capacity_allows


class CapacityAllowsTests(TestCase):
    def test_ok_is_allowed(self):
        self.assertTrue(capacity_allows(None, {'ok': True}))

    def test_shortage_blocked_without_oversell(self):
        pop = type('P', (), {'allow_oversell': False})()
        self.assertFalse(capacity_allows(pop, {'ok': False, 'shortages': ['CPU 不足']}))

    def test_shortage_allowed_when_oversell(self):
        pop = type('P', (), {'allow_oversell': True})()
        self.assertTrue(capacity_allows(pop, {'ok': False, 'shortages': ['CPU 不足']}))

    def test_check_error_never_oversells(self):
        pop = type('P', (), {'allow_oversell': True})()
        self.assertFalse(capacity_allows(pop, {'ok': False, 'error': True}))


class ScheduledChangeAndOversellTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='s4-chg', name='S4 区')
        country = Country.objects.create(region=region, name='S4 国')
        city = City.objects.create(country=country, name='S4 市')
        self.pop = PopPoint.objects.create(name='S4 POP', city=city)
        self.user = User.objects.create_user(username='s4-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='S4 客户',
            contact_name='联系人',
            account=self.user,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.save(update_fields=['account_type'])
        self.product = Product.objects.create(
            name='S4 产品',
            category='vps',
            status='online',
            pop_point=self.pop,
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=10,
            ip_count=1,
            monthly_price=Decimal('200.00'),
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
            quoted_price=Decimal('200.00'),
        )
        self.vps = VPS.objects.create(
            name='S4 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=2,
            ram_mb=2048,
            disk_gb=20,
            bandwidth_mbps=20,
            status='running',
        )
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type='vps',
            status='active',
            vps=self.vps,
            monthly_fee=Decimal('200.00'),
            fee_currency='CNY',
            billing_start_date=timezone.localdate().replace(day=1),
        )

    def _submit_downgrade(self):
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'downgrade',
                'current_spec': {
                    'cpu_cores': 2, 'ram_mb': 2048, 'disk_gb': 20,
                    'bandwidth_mbps': 20,                 'ip_count': 0, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 1, 'ram_mb': 1024, 'disk_gb': 20,
                    'bandwidth_mbps': 5, 'ip_count': 0, 'nic_count': 0,
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
            return submit_service_change(
                self.instance,
                requested={'requested_vcpu': 1, 'requested_ram_mb': 1024, 'requested_bandwidth_mbps': 5},
                user=self.user,
            )

    def test_future_scheduled_change_is_not_executed(self):
        result = self._submit_downgrade()
        change = result['change']
        change.scheduled_at = timezone.now() + timedelta(days=10)
        change.save(update_fields=['scheduled_at', 'updated_at'])
        stats = process_due_scheduled_changes(now=timezone.now(), dry_run=False)
        self.assertEqual(stats['scanned'], 0)
        change.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.APPROVED)
        self.vps.refresh_from_db()
        self.assertEqual(self.vps.vcpu, 2)

    def test_due_downgrade_applies_spec_and_fee(self):
        result = self._submit_downgrade()
        change = result['change']
        change.scheduled_at = timezone.now() - timedelta(minutes=1)
        change.save(update_fields=['scheduled_at', 'updated_at'])
        stats = process_due_scheduled_changes(now=timezone.now(), dry_run=False)
        self.assertEqual(stats['executed'], 1)
        change.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.COMPLETED)
        self.vps.refresh_from_db()
        self.instance.refresh_from_db()
        self.assertEqual(self.vps.vcpu, 1)
        self.assertEqual(self.vps.ram_mb, 1024)
        self.assertEqual(self.vps.bandwidth_mbps, 5)
        self.assertEqual(self.instance.monthly_fee, Decimal('80.00'))
        result['order'].refresh_from_db()
        self.assertEqual(result['order'].status, ServiceOrder.OrderStatus.ACTIVE)

    def test_dry_run_does_not_write(self):
        result = self._submit_downgrade()
        change = result['change']
        change.scheduled_at = timezone.now() - timedelta(minutes=1)
        change.save(update_fields=['scheduled_at', 'updated_at'])
        stats = process_due_scheduled_changes(now=timezone.now(), dry_run=True)
        self.assertEqual(stats['scanned'], 1)
        self.assertEqual(stats['executed'], 0)
        change.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.APPROVED)

    def test_management_command_runs(self):
        result = self._submit_downgrade()
        change = result['change']
        change.scheduled_at = timezone.now() - timedelta(minutes=1)
        change.save(update_fields=['scheduled_at', 'updated_at'])
        out = StringIO()
        call_command('process_scheduled_changes', stdout=out)
        change.refresh_from_db()
        self.assertEqual(change.status, ServiceChangeRecord.ChangeStatus.COMPLETED)
        self.assertIn('执行 1', out.getvalue())

    def test_followup_ticket_is_idempotent(self):
        result = self._submit_downgrade()
        change = result['change']
        first = ensure_change_followup_ticket(change, operator=self.user, enabled=True)
        second = ensure_change_followup_ticket(change, operator=self.user, enabled=True)
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            SupportTicket.objects.filter(related_instance=self.instance).count(),
            1,
        )

    @patch('service_manager.change_provisioning._check_resources', return_value={
        'ok': False, 'shortages': ['CPU 不足：需 1，剩余 0'],
    })
    def test_oversell_pop_allows_paid_upgrade(self, _check):
        self.pop.allow_oversell = True
        self.pop.save(update_fields=['allow_oversell', 'updated_at'])
        with patch(
            'service_manager.change_billing.quote_service_change',
            return_value={
                'instance_no': self.instance.instance_no,
                'service_type': 'vps',
                'currency': 'CNY',
                'direction': 'upgrade',
                'current_spec': {
                    'cpu_cores': 2, 'ram_mb': 2048, 'disk_gb': 20,
                    'bandwidth_mbps': 20, 'ip_count': 0, 'nic_count': 0,
                },
                'target_spec': {
                    'cpu_cores': 4, 'ram_mb': 4096, 'disk_gb': 20,
                    'bandwidth_mbps': 20, 'ip_count': 0, 'nic_count': 0,
                },
                'old_monthly_fee': '200.00',
                'new_monthly_fee': '360.00',
                'monthly_delta': '160.00',
                'due_now': '0.00',
                'remaining_days': 10,
                'days_in_month': 30,
                'effective_at': '2026-08-23',
                'effective_policy': 'pay_then_apply',
                'policy_text': '无需补差价',
                'price_breakdown': [],
                'has_product_pricing': True,
            },
        ), patch('service_manager.capacity_reservation.reserve_for_order'):
            submitted = submit_service_change(
                self.instance,
                requested={'requested_vcpu': 4, 'requested_ram_mb': 4096},
                user=self.user,
            )
        change = submitted['change']
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.save(update_fields=['status'])
        outcome = apply_paid_change(submitted['order'], change, operator=self.user)
        self.assertTrue(outcome.get('triggered'), outcome)
        self.vps.refresh_from_db()
        self.assertEqual(self.vps.vcpu, 4)
        self.assertEqual(self.vps.ram_mb, 4096)
        submitted['order'].refresh_from_db()
        self.assertIn('超卖', submitted['order'].auto_provision_message)
