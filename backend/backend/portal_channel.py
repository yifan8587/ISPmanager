"""门户专用通道。

管理端公网入口会清掉 X-ISP-Portal-Channel。
只有门户 API 端口上的 nginx 会把它写成 1，并带上门户服务器网关提交的密钥。
浏览器拿不到这把密钥，也不能靠自己加这个头进入门户通道。
"""
from __future__ import annotations

import hmac
from urllib.parse import urlsplit

PORTAL_HTTP_EXACT = {
    '/api/auth/token',
    '/api/auth/token/',
    '/api/auth/token/refresh',
    '/api/auth/token/refresh/',
}
PORTAL_HTTP_PREFIXES = (
    '/api/auth/me',
    '/api/portal/',
    '/api/customer/payment-transactions/',
)
PORTAL_WS_PREFIX = '/ws/resource/vnc/'


def http_path_allowed(path: str) -> bool:
    path = path or ''
    if path in PORTAL_HTTP_EXACT or path in {'/api/portal', '/api/customer/payment-transactions'}:
        return True
    return any(path.startswith(prefix) for prefix in PORTAL_HTTP_PREFIXES)


def ws_path_allowed(path: str) -> bool:
    return (path or '').startswith(PORTAL_WS_PREFIX)


def _header(scope, name: str) -> bytes:
    target = name.lower().encode('latin-1')
    for key, value in scope.get('headers') or []:
        if key.lower() == target:
            return value
    return b''


def secret_matches(given: bytes, expected: str) -> bool:
    raw = (expected or '').encode('utf-8')
    if not raw or not given or len(given) != len(raw):
        return False
    return hmac.compare_digest(given, raw)


class PortalChannelMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get('type')
        if kind not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        if _header(scope, 'x-isp-portal-channel') != b'1':
            return await self.app(scope, receive, send)

        from django.conf import settings

        path = scope.get('path') or ''
        allowed = ws_path_allowed(path) if kind == 'websocket' else http_path_allowed(path)
        ok = allowed and secret_matches(
            _header(scope, 'x-isp-portal-key'),
            str(getattr(settings, 'PORTAL_API_SECRET', '') or ''),
        )
        if ok:
            return await self.app(scope, receive, send)
        if kind == 'websocket':
            await send({'type': 'websocket.close', 'code': 4404})
            return
        await send({
            'type': 'http.response.start',
            'status': 404,
            'headers': [
                (b'content-type', b'text/plain; charset=utf-8'),
                (b'content-length', b'0'),
                (b'cache-control', b'no-store'),
            ],
        })
        await send({'type': 'http.response.body', 'body': b''})


def portal_return_origin(request) -> str:
    """门户通道上的支付回跳只用配置好的门户地址。"""
    from django.conf import settings

    if request.headers.get('X-ISP-Portal-Channel') != '1':
        return ''
    origin = str(getattr(settings, 'PORTAL_PUBLIC_ORIGIN', '') or '').strip().rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme in {'http', 'https'} and parsed.netloc:
        return origin
    return ''
