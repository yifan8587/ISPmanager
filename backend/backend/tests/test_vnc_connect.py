import asyncio
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase
from django.urls import re_path

from resource_manager.pemanager_client import PemanagerClient
from resource_manager.vnc_consumer import (
    VPSVNCConsumer,
    close_reason,
    fit_close_reason,
    handshake_rejected,
    node_connect_failure,
    node_ws_connect_kwargs,
)


class VncConnectTests(SimpleTestCase):
    def test_node_socket_does_not_use_env_proxy_or_compression(self):
        kwargs = node_ws_connect_kwargs(None)
        self.assertIsNone(kwargs['proxy'])
        self.assertIsNone(kwargs['compression'])
        self.assertIsNone(kwargs['ping_interval'])
        self.assertEqual(kwargs['subprotocols'], ['binary'])
        self.assertNotIn('ssl', kwargs)

    def test_ssl_context_is_forwarded(self):
        sentinel = object()
        self.assertIs(node_ws_connect_kwargs(sentinel)['ssl'], sentinel)

    def test_close_reason_is_one_short_line(self):
        text = close_reason(RuntimeError('连不上\n节点  ' + 'x' * 200))
        self.assertNotIn('\n', text)
        self.assertLessEqual(len(text), 120)
        self.assertTrue(text.startswith('RuntimeError:'))

    def test_login_comes_before_token_like_admin_console(self):
        client = PemanagerClient('https://10.0.0.1', 'admin', 'pw', api_token='pem_a.b')
        self.assertEqual(client.websocket_auth_modes(), ['login', 'token'])
        token_only = PemanagerClient('https://10.0.0.1', '', '', api_token='pem_a.b')
        self.assertEqual(token_only.websocket_auth_modes(), ['token'])

    def test_close_reason_fits_websocket_byte_limit(self):
        text = fit_close_reason('连接 PVE 控制台失败' * 20)
        self.assertLessEqual(len(text.encode('utf-8')), 123)
        self.assertTrue(text.startswith('连接 PVE'))

    def test_old_node_403_gets_readable_reason(self):
        text = node_connect_failure(NodeRejected(403))
        self.assertIn('HTTP 403', text)
        self.assertLessEqual(len(text.encode('utf-8')), 123)

    def test_only_auth_rejection_allows_retry(self):
        rejected = SimpleNamespace(response=SimpleNamespace(status_code=403))
        self.assertTrue(handshake_rejected(rejected))
        self.assertFalse(handshake_rejected(TimeoutError('slow')))


class NodeRejected(Exception):
    def __init__(self, status):
        super().__init__(f'server rejected WebSocket connection: HTTP {status}')
        self.response = SimpleNamespace(status_code=status)


class NodeClosed(Exception):
    def __init__(self, code, reason):
        super().__init__(f'received {code} ({reason})')
        self.rcvd = SimpleNamespace(code=code, reason=reason)


class FakeNodeSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.frames:
            await asyncio.sleep(3600)
        return self.frames.pop(0)

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        pass


class VncRelayIdentityTests(SimpleTestCase):
    """线上节点的 VNC WebSocket 只认登录 JWT，拿 pem_ Token 握手会被 403。"""

    vps = {
        'source_ref': '42',
        'api_base_url': 'https://10.0.0.1',
        'mgmt_ip': '',
        'api_username': 'admin',
        'api_password': 'pw',
        'mgmt_token': 'pem_a.b',
        'api_verify_ssl': False,
    }

    def _run(self, accepted_tokens, node=None, expect_close=False):
        tried = []
        node = node or FakeNodeSocket([b'RFB 003.008\n'])

        async def fake_connect(url, ssl_context, headers, origin):
            token = parse_qs(urlsplit(url).query)['token'][0]
            tried.append(token)
            if token not in accepted_tokens:
                raise NodeRejected(403)
            return node

        def fake_login(client):
            client._apply_bearer('jwt-access')
            return {}

        async def scenario():
            app = URLRouter([re_path(r'^ws/resource/vnc/(?P<vps_id>\d+)/$', VPSVNCConsumer.as_asgi())])
            communicator = WebsocketCommunicator(
                app, '/ws/resource/vnc/7/?vncticket=PVEVNC%3Aabc&port=5900', subprotocols=['binary'],
            )
            communicator.scope['user'] = SimpleNamespace(is_authenticated=True, pk=1)
            connected, subprotocol = await communicator.connect()
            if expect_close:
                first = await communicator.receive_output(timeout=2)
            else:
                first = await communicator.receive_from(timeout=2) if connected else None
            await communicator.disconnect()
            return connected, subprotocol, first

        with mock.patch.object(VPSVNCConsumer, '_authorize_vps', new=mock.AsyncMock(return_value=('ok', self.vps))), \
                mock.patch('resource_manager.vnc_consumer.connect_node_websocket', new=fake_connect), \
                mock.patch.object(PemanagerClient, 'login', new=fake_login):
            result = asyncio.run(scenario())
        return result, tried

    def test_uses_login_jwt_first(self):
        (connected, subprotocol, first), tried = self._run({'jwt-access'})
        self.assertTrue(connected)
        self.assertEqual(subprotocol, 'binary')
        self.assertEqual(first, b'RFB 003.008\n')
        self.assertEqual(tried, ['jwt-access'])

    def test_node_close_reason_reaches_browser(self):
        class Closing(FakeNodeSocket):
            async def __anext__(self):
                raise NodeClosed(4502, '无法登录 PVE')

        (_c, _s, message), _tried = self._run({'jwt-access'}, node=Closing([]), expect_close=True)
        self.assertEqual(message['type'], 'websocket.close')
        self.assertEqual(message['code'], 4502)
        self.assertEqual(message['reason'], '无法登录 PVE')

    def test_rejected_everywhere_reports_403(self):
        (_c, _s, message), tried = self._run(set(), expect_close=True)
        self.assertEqual(tried, ['jwt-access', 'pem_a.b'])
        self.assertEqual(message['code'], 4502)
        self.assertIn('HTTP 403', message['reason'])

    def test_falls_back_to_token_when_login_rejected(self):
        (connected, _sub, first), tried = self._run({'pem_a.b'})
        self.assertTrue(connected)
        self.assertEqual(first, b'RFB 003.008\n')
        self.assertEqual(tried, ['jwt-access', 'pem_a.b'])
