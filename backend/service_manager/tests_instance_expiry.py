"""实例到期催缴 / 停机 / 回收骨架单测。"""
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from customer_manager.models import Customer
from resource_manager.models import City, Country, PopPoint, Region, VPS
from service_manager.expiry_engine import apply_schedule_to_instance, process_instance_expiry
from service_manager.models import ServiceExpiryAction, ServiceInstance, ServiceOrder


class InstanceExpiryEngineTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='exp-eng', name='到期引擎区')
        country = Country.objects.create(region=region, name='到期引擎国')
        city = City.objects.create(country=country, name='到期引擎市')
        self.pop = PopPoint.objects.create(name='到期引擎 POP', city=city)
        self.user = User.objects.create_user(username='exp-eng-user', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='到期引擎客户',
            contact_name='联系人',
            account=self.user,
        )
        self.order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.ACTIVE,
            customer=self.customer,
            preferred_pop=self.pop,
            quoted_price=Decimal('100.00'),
        )
        self.vps = VPS.objects.create(
            name='到期引擎 VPS',
            pop_point=self.pop,
            customer=self.customer,
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
        )

    def _at(self, days_from_today, hour=12):
        day = timezone.localdate() + timedelta(days=days_from_today)
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def test_schedule_uses_expire_and_default_grace(self):
        expire = self._at(10)
        self.instance.expire_time = expire
        self.instance.grace_days = None
        changed = apply_schedule_to_instance(self.instance, force=True)
        self.assertTrue(changed)
        self.assertEqual(self.instance.suspend_at, expire)
        self.assertEqual(self.instance.recycle_at, expire + timedelta(days=1))

    def test_schedule_uses_instance_grace_days(self):
        expire = self._at(10)
        self.instance.expire_time = expire
        self.instance.grace_days = 5
        apply_schedule_to_instance(self.instance, force=True)
        self.assertEqual(self.instance.recycle_at, expire + timedelta(days=5))

    def test_backfill_expire_time_from_vps(self):
        expire = self._at(12)
        self.vps.expire_time = expire
        self.vps.save(update_fields=['expire_time'])
        self.instance.expire_time = None
        self.instance.save(update_fields=['expire_time'])

        with patch('service_manager.expiry_engine.notify_service_event'):
            stats = process_instance_expiry(
                now=self._at(0, 9),
                customer_id=self.customer.pk,
            )
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.expire_time, expire)
        self.assertEqual(self.instance.suspend_at, expire)
        self.assertEqual(self.instance.recycle_at, expire + timedelta(days=1))
        self.assertEqual(stats['expire_backfilled'], 1)

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_formal_t3_reminder_is_idempotent(self, notify):
        self.instance.expire_time = self._at(3)
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])
        now = self._at(0, 9)

        first = process_instance_expiry(now=now, customer_id=self.customer.pk)
        second = process_instance_expiry(now=now, customer_id=self.customer.pk)

        self.assertEqual(first['reminded'], 1)
        self.assertEqual(second['reminded'], 0)
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(
            ServiceExpiryAction.objects.filter(
                instance=self.instance,
                action=ServiceExpiryAction.Action.REMIND,
                lead_days=3,
            ).count(),
            1,
        )
        self.assertEqual(notify.call_args.kwargs['context']['action'], 'remind')
        self.assertEqual(notify.call_args.kwargs['context']['days_left'], 3)
        self.assertIn('续费', notify.call_args.kwargs['context']['detail'])

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_trial_reminds_one_day_before_not_three(self, notify):
        self.instance.is_trial = True
        self.instance.expire_time = self._at(3)
        self.instance.save(update_fields=['is_trial', 'expire_time', 'suspend_at', 'recycle_at'])
        early = process_instance_expiry(now=self._at(0, 9), customer_id=self.customer.pk)
        self.assertEqual(early['reminded'], 0)

        self.instance.expire_time = self._at(1)
        self.instance.suspend_at = None
        self.instance.recycle_at = None
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])
        due = process_instance_expiry(now=self._at(0, 9), customer_id=self.customer.pk)
        self.assertEqual(due['reminded'], 1)
        self.assertIn('转为正式', notify.call_args.kwargs['context']['detail'])

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_dry_run_does_not_write_or_suspend(self, notify):
        self.instance.expire_time = self._at(-1)
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])

        stats = process_instance_expiry(
            now=self._at(0, 9),
            dry_run=True,
            apply_suspend=True,
            apply_recycle=True,
            customer_id=self.customer.pk,
        )
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.status, 'active')
        self.assertEqual(ServiceExpiryAction.objects.filter(instance=self.instance).count(), 0)
        notify.assert_not_called()
        self.assertGreaterEqual(stats['suspended'], 1)
        self.assertTrue(any('到期停机' in line for line in stats['planned']))

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_default_run_notifies_expired_but_does_not_suspend(self, notify):
        self.instance.expire_time = self._at(-1)
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])

        stats = process_instance_expiry(
            now=self._at(0, 9),
            customer_id=self.customer.pk,
        )
        self.instance.refresh_from_db()
        self.assertEqual(self.instance.status, 'active')
        self.assertEqual(stats['expired'], 1)
        self.assertEqual(stats['suspended'], 0)
        self.assertTrue(
            ServiceExpiryAction.objects.filter(
                instance=self.instance,
                action=ServiceExpiryAction.Action.EXPIRED,
            ).exists(),
        )
        self.assertFalse(
            ServiceExpiryAction.objects.filter(
                instance=self.instance,
                action=ServiceExpiryAction.Action.SUSPEND,
            ).exists(),
        )

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_apply_suspend_pauses_expired_instance(self, notify):
        self.instance.expire_time = self._at(-1)
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])

        stats = process_instance_expiry(
            now=self._at(0, 9),
            apply_suspend=True,
            customer_id=self.customer.pk,
        )
        self.instance.refresh_from_db()
        self.vps.refresh_from_db()
        self.assertEqual(stats['suspended'], 1)
        self.assertEqual(self.instance.status, 'suspended')
        self.assertEqual(self.vps.status, 'suspended')

    @patch('service_manager.expiry_engine.notify_service_event')
    def test_apply_recycle_terminates_after_grace(self, notify):
        self.instance.expire_time = self._at(-4)
        self.instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at'])

        stats = process_instance_expiry(
            now=self._at(0, 9),
            apply_recycle=True,
            customer_id=self.customer.pk,
        )
        self.instance.refresh_from_db()
        self.vps.refresh_from_db()
        self.assertEqual(stats['recycled'], 1)
        self.assertEqual(self.instance.status, 'terminated')
        self.assertIsNone(self.vps.customer_id)
        self.assertEqual(self.vps.status, 'stopped')
