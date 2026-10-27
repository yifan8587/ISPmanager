"""S2：公开注册限流、门户写操作审计。"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from resource_manager.models import City, Country, PopPoint, Region, ResourceOperationLog, VPS
from service_manager.models import ServiceOperationLog


def _rest_with_rates(**overrides):
    from django.conf import settings
    framework = dict(settings.REST_FRAMEWORK)
    rates = dict(framework.get('DEFAULT_THROTTLE_RATES') or {})
    rates.update(overrides)
    framework['DEFAULT_THROTTLE_RATES'] = rates
    return framework


class PublicRegisterThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @override_settings(REST_FRAMEWORK=_rest_with_rates(public_register='1/hour'))
    def test_second_register_from_same_ip_is_throttled(self):
        first = self.client.post('/api/portal/register/', {
            'username': 's2-reg-1',
            'password': 'SafePass_2026!',
            'email': 's2-1@example.test',
            'contact_name': '限流一',
            'customer_type': 'personal',
        }, format='json')
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post('/api/portal/register/', {
            'username': 's2-reg-2',
            'password': 'SafePass_2026!',
            'email': 's2-2@example.test',
            'contact_name': '限流二',
            'customer_type': 'personal',
        }, format='json')
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.data['code'], 429)


class PortalWriteAuditTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user('s2-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='S2 门户客户',
            contact_name='审计',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        region = Region.objects.create(code='s2a', name='审计区')
        country = Country.objects.create(region=region, name='审计国')
        city = City.objects.create(country=country, name='审计市')
        self.pop = PopPoint.objects.create(name='审计 POP', city=city)
        self.vps = VPS.objects.create(
            name='审计主机',
            pop_point=self.pop,
            customer=self.customer,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            status='running',
        )
        self.client.force_authenticate(self.user)

    @patch('resource_manager.pve_ops.vm_power')
    def test_power_writes_resource_log(self, vm_power):
        vm_power.return_value = {'ok': True, 'action': 'restart'}
        response = self.client.post(
            f'/api/portal/vps/{self.vps.pk}/power/',
            {'action': 'restart'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        log = ResourceOperationLog.objects.get(vps=self.vps, description__contains='电源')
        self.assertEqual(log.operator_id, self.user.pk)
        self.assertEqual(log.action_type, ResourceOperationLog.ActionType.RESTART)
        self.assertEqual(log.new_value.get('action'), 'restart')

    def test_set_root_password_logs_without_password(self):
        response = self.client.post(
            f'/api/portal/vps/{self.vps.pk}/set_root_password/',
            {'password': 'SuperSecret_2026!'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        log = ResourceOperationLog.objects.get(vps=self.vps, description__contains='Root')
        self.assertEqual(log.action_type, ResourceOperationLog.ActionType.CONFIG_CHANGE)
        self.assertTrue(log.new_value.get('password_set'))
        blob = str(log.new_value) + log.description
        self.assertNotIn('SuperSecret_2026!', blob)
        self.assertFalse(ServiceOperationLog.objects.filter(description__contains='SuperSecret').exists())
