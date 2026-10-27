"""N5：节点 JWT 过期后自动 refresh，必要时重新 login。"""
from unittest.mock import patch

from django.test import SimpleTestCase

from resource_manager.pemanager_client import PemanagerClient, PemanagerError
from resource_manager.tests_node_contract import _FakeResp


class JwtRefreshTests(SimpleTestCase):
    def test_login_stores_refresh(self):
        client = PemanagerClient('http://node.example', 'admin', 'secret')

        def fake_post(url, json=None, timeout=None, verify=None):
            return _FakeResp(200, {
                'access': 'jwt-access',
                'refresh': 'jwt-refresh',
                'user': {'username': 'admin'},
            })

        with patch.object(client.session, 'post', side_effect=fake_post):
            client.login()
        self.assertEqual(client.token, 'jwt-access')
        self.assertEqual(client._refresh, 'jwt-refresh')

    def test_get_401_refreshes_and_retries(self):
        client = PemanagerClient('http://node.example', 'admin', 'secret')
        client._token = 'old'
        client._refresh = 'r1'
        client._apply_bearer('old')
        gets = []
        posts = []

        def fake_get(url, params=None, timeout=None, verify=None):
            gets.append(url)
            if len(gets) == 1:
                return _FakeResp(401, {'detail': 'token expired'})
            return _FakeResp(200, [{'id': 1}])

        def fake_post(url, json=None, timeout=None, verify=None):
            posts.append((url, json))
            return _FakeResp(200, {'access': 'new-access'})

        with patch.object(client.session, 'get', side_effect=fake_get), \
             patch.object(client.session, 'post', side_effect=fake_post):
            data = client._get_raw('/api/routemanage/desired-routes/')
        self.assertEqual(data, [{'id': 1}])
        self.assertEqual(len(gets), 2)
        self.assertTrue(posts[0][0].endswith('/api/accountmanage/auth/refresh/'))
        self.assertEqual(posts[0][1], {'refresh': 'r1'})
        self.assertEqual(client.token, 'new-access')

    def test_write_401_refresh_fails_then_relogin(self):
        client = PemanagerClient('http://node.example', 'admin', 'secret')
        client._token = 'old'
        client._refresh = 'r-bad'
        client._apply_bearer('old')
        posts = []

        def fake_post(url, json=None, timeout=None, verify=None):
            posts.append(url)
            if url.endswith('/auth/refresh/'):
                return _FakeResp(401, {'detail': 'refresh expired'})
            if url.endswith('/auth/login/'):
                return _FakeResp(200, {
                    'access': 'jwt2',
                    'refresh': 'r2',
                    'user': {'username': 'admin'},
                })
            if url.endswith('/desired-routes/'):
                if client.token == 'jwt2':
                    return _FakeResp(201, {'id': 'dr-1'})
                return _FakeResp(401, {'detail': 'token expired'})
            return _FakeResp(404, {})

        with patch.object(client.session, 'post', side_effect=fake_post):
            obj = client.create_desired_route({'dest_cidr': '10.0.0.0/8'})
        self.assertEqual(obj, {'id': 'dr-1'})
        self.assertTrue(any(u.endswith('/auth/refresh/') for u in posts))
        self.assertTrue(any(u.endswith('/auth/login/') for u in posts))
        self.assertEqual(client.token, 'jwt2')
        self.assertEqual(client._refresh, 'r2')

    def test_long_lived_token_does_not_refresh(self):
        client = PemanagerClient(
            'http://node.example', '', '', api_token='pem_abc.secret',
        )
        posts = []

        def fake_get(url, params=None, timeout=None, verify=None):
            return _FakeResp(401, {'detail': 'invalid token'})

        def fake_post(url, json=None, timeout=None, verify=None):
            posts.append(url)
            return _FakeResp(200, {'access': 'should-not'})

        with patch.object(client.session, 'get', side_effect=fake_get), \
             patch.object(client.session, 'post', side_effect=fake_post):
            with self.assertRaises(PemanagerError):
                client._get_raw('/api/routemanage/desired-routes/')
        self.assertEqual(posts, [])

    def test_refresh_failure_without_password_raises(self):
        client = PemanagerClient('http://node.example', '', '')
        client._refresh = 'r1'
        client._apply_bearer('old')

        def fake_get(url, params=None, timeout=None, verify=None):
            return _FakeResp(401, {'detail': 'expired'})

        def fake_post(url, json=None, timeout=None, verify=None):
            return _FakeResp(401, {'detail': 'bad refresh'})

        with patch.object(client.session, 'get', side_effect=fake_get), \
             patch.object(client.session, 'post', side_effect=fake_post):
            with self.assertRaises(PemanagerError):
                client._get_raw('/api/routemanage/desired-routes/')
