"""S-H1/H2/H3：WebSocket 访问判定。"""
from django.contrib.auth.models import User
from django.test import TestCase

from accounts.models import UserProfile
from accounts.ws_access import (
    authorize_vps_console,
    user_can_open_operation_ssh,
    user_can_open_pop_ssh,
    user_can_open_vps_console,
)
from customer_manager.models import Customer
from resource_manager.models import City, Country, PopPoint, Region, VPS


class WsAccessTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='ws', name='鉴权区')
        country = Country.objects.create(region=region, name='鉴权国')
        city = City.objects.create(country=country, name='鉴权市')
        self.pop = PopPoint.objects.create(name='鉴权 POP', city=city)

        self.owner = User.objects.create_user('ws-owner', password='SafePass_2026!')
        self.owner_customer = Customer.objects.create(
            name='属主客户', contact_name='甲', account=self.owner, status='active',
        )
        owner_profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        owner_profile.account_type = UserProfile.AccountType.CUSTOMER
        owner_profile.role = UserProfile.Role.CUSTOMER_USER
        owner_profile.save(update_fields=['account_type', 'role'])

        self.other = User.objects.create_user('ws-other', password='SafePass_2026!')
        self.other_customer = Customer.objects.create(
            name='其他客户', contact_name='乙', account=self.other, status='active',
        )
        other_profile, _ = UserProfile.objects.get_or_create(user=self.other)
        other_profile.account_type = UserProfile.AccountType.CUSTOMER
        other_profile.role = UserProfile.Role.CUSTOMER_USER
        other_profile.save(update_fields=['account_type', 'role'])

        self.admin = User.objects.create_superuser('ws-admin', 'ws@example.test', 'password')
        self.vps = VPS.objects.create(
            name='属主主机',
            pop_point=self.pop,
            customer=self.owner_customer,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            status='running',
            source_ref='100',
        )

    def test_customer_can_open_own_vnc_not_others(self):
        self.assertTrue(user_can_open_vps_console(self.owner, self.vps))
        self.assertFalse(user_can_open_vps_console(self.other, self.vps))
        status, payload = authorize_vps_console(self.other, self.vps.pk)
        self.assertEqual(status, 'forbidden')
        self.assertIsNone(payload)
        status, payload = authorize_vps_console(self.owner, self.vps.pk)
        self.assertEqual(status, 'ok')
        self.assertEqual(payload['source_ref'], '100')

    def test_customer_cannot_open_pop_or_operation_ssh(self):
        self.assertFalse(user_can_open_pop_ssh(self.owner))
        self.assertFalse(user_can_open_operation_ssh(self.owner))

    def test_superuser_can_open_all(self):
        self.assertTrue(user_can_open_vps_console(self.admin, self.vps))
        self.assertTrue(user_can_open_pop_ssh(self.admin))
        self.assertTrue(user_can_open_operation_ssh(self.admin))
