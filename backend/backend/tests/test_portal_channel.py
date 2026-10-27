from django.test import SimpleTestCase

from backend.portal_channel import http_path_allowed, secret_matches, ws_path_allowed


class PortalChannelPathTests(SimpleTestCase):
    def test_portal_reads_are_allowed(self):
        self.assertTrue(http_path_allowed('/api/portal/products/'))
        self.assertTrue(http_path_allowed('/api/auth/token/'))
        self.assertTrue(http_path_allowed('/api/auth/me/change_password/'))
        self.assertTrue(http_path_allowed('/api/customer/payment-transactions/NO1/'))
        self.assertTrue(ws_path_allowed('/ws/resource/vnc/9/'))

    def test_admin_surface_is_rejected(self):
        self.assertFalse(http_path_allowed('/api/system/settings/'))
        self.assertFalse(http_path_allowed('/api/auth/users/'))
        self.assertFalse(http_path_allowed('/api/accounts/roles/'))
        self.assertFalse(http_path_allowed('/api/portal-extra/'))
        self.assertFalse(ws_path_allowed('/ws/resource/pop-ssh/3/'))

    def test_secret_compare_rejects_wrong_length(self):
        self.assertFalse(secret_matches(b'short', 'unit-test-portal-api-secret-32b!!'))
        self.assertTrue(secret_matches(b'unit-test-portal-api-secret-32b!!', 'unit-test-portal-api-secret-32b!!'))
