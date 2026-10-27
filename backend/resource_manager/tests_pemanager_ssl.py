"""节点 HTTPS 校验：全局设置、按 IP 自签、POP 开关。"""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from resource_manager.pemanager_client import (
    PemanagerClient,
    host_is_ip_address,
    resolve_pemanager_ssl_verify,
)


class PemanagerSslVerifyTests(SimpleTestCase):
    @override_settings(PEMANAGER_SSL_VERIFY=True)
    def test_default_follows_settings_true(self):
        client = PemanagerClient('https://node.example', 'u', 'p')
        self.assertTrue(client.verify)

    @override_settings(PEMANAGER_SSL_VERIFY=False)
    def test_default_follows_settings_false(self):
        client = PemanagerClient('https://node.example', 'u', 'p')
        self.assertFalse(client.verify)

    @override_settings(PEMANAGER_SSL_VERIFY=True)
    def test_explicit_false_overrides_settings(self):
        client = PemanagerClient('https://node.example', 'u', 'p', verify=False)
        self.assertFalse(client.verify)

    def test_https_ip_skips_verify_even_when_settings_require_it(self):
        self.assertTrue(host_is_ip_address('https://216.106.183.3'))
        self.assertFalse(host_is_ip_address('https://pe.example.com'))
        with override_settings(PEMANAGER_SSL_VERIFY=True):
            self.assertFalse(
                resolve_pemanager_ssl_verify('https://216.106.183.3'),
            )
            client = PemanagerClient('https://216.106.183.3', 'u', 'p')
            self.assertFalse(client.verify)

    @override_settings(PEMANAGER_SSL_VERIFY=True)
    def test_pop_switch_overrides_hostname_and_ip(self):
        pop_off = SimpleNamespace(api_verify_ssl=False)
        pop_on = SimpleNamespace(api_verify_ssl=True)
        self.assertFalse(resolve_pemanager_ssl_verify(
            'https://pe.example.com', pop=pop_off,
        ))
        self.assertTrue(resolve_pemanager_ssl_verify(
            'https://216.106.183.3', pop=pop_on,
        ))

    @override_settings(PEMANAGER_SSL_VERIFY=True)
    def test_from_pop_uses_api_verify_ssl(self):
        pop = SimpleNamespace(
            name='测试 POP',
            api_base_url='https://216.106.183.3',
            mgmt_ip='216.106.183.3',
            api_username='admin',
            api_password='secret',
            mgmt_username='',
            mgmt_password='',
            mgmt_token='',
            api_verify_ssl=False,
        )
        client = PemanagerClient.from_pop(pop)
        self.assertFalse(client.verify)
        self.assertEqual(client.base, 'https://216.106.183.3')
