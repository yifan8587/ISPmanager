"""S-H4 / S-H5：管理端统计、账单预览、开通 Hook 隔离与 URL 校验。"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from service_manager.hooks import validate_webhook_url


class ServiceAdminIsolationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('sec-admin', 'sec@example.test', 'password')
        self.user = User.objects.create_user('sec-portal', password='SafePass_2026!')
        Customer.objects.create(
            name='隔离客户', contact_name='联系人', account=self.user, status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.portal = APIClient()
        self.portal.force_authenticate(self.user)
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)

    def test_customer_cannot_read_global_stats_or_preview(self):
        stats = self.portal.get('/api/service/stats/by-customer/')
        self.assertEqual(stats.status_code, 403)
        preview = self.portal.get('/api/service/billing/preview/?customer=1')
        self.assertEqual(preview.status_code, 403)

    def test_customer_cannot_create_provision_hook(self):
        resp = self.portal.post('/api/service/hooks/', {
            'name': 'evil',
            'event': 'provision',
            'hook_type': 'webhook',
            'endpoint_url': 'https://example.com/hook',
            'is_active': True,
        }, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_read_stats(self):
        resp = self.admin_client.get('/api/service/stats/by-type/')
        self.assertEqual(resp.status_code, 200, resp.data)


class WebhookUrlValidationTests(TestCase):
    def test_blocks_loopback_and_private(self):
        for url in (
            'http://127.0.0.1/hook',
            'http://localhost/hook',
            'http://10.0.0.8/hook',
            'http://192.168.1.1/hook',
            'http://169.254.169.254/latest/meta-data',
            'file:///etc/passwd',
        ):
            with self.assertRaises(ValidationError, msg=url):
                validate_webhook_url(url)

    def test_allows_public_literal(self):
        self.assertEqual(
            validate_webhook_url('https://1.1.1.1/hook'),
            'https://1.1.1.1/hook',
        )

    @override_settings(WEBHOOK_ALLOW_PRIVATE_URLS=True)
    def test_override_allows_private(self):
        self.assertEqual(
            validate_webhook_url('http://10.0.0.8/hook'),
            'http://10.0.0.8/hook',
        )
