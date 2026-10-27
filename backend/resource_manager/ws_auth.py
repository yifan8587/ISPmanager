"""
Channels WebSocket JWT 鉴权中间件。

优先从 query string `?token=` 读取（noVNC 只能走 query，因其子协议固定为 binary）。
WebSSH 可改走 `Sec-WebSocket-Protocol: access_token, <JWT>`，避免令牌进入访问日志。
校验通过后把 user 注入 scope；失败则注入 AnonymousUser，由消费者拒绝连接。
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser

WS_ACCESS_TOKEN_PROTOCOL = 'access_token'


def extract_ws_token(scope):
    """从 query 或 Sec-WebSocket-Protocol 取出 JWT。query 优先以兼容 noVNC。"""
    query = parse_qs(scope.get('query_string', b'').decode())
    token = (query.get('token') or [None])[0]
    if token:
        return token
    for name, value in scope.get('headers') or []:
        if name != b'sec-websocket-protocol':
            continue
        raw = value.decode('utf-8', errors='replace') if isinstance(value, (bytes, bytearray)) else str(value)
        parts = [part.strip() for part in raw.split(',') if part.strip()]
        for index, part in enumerate(parts):
            if part.startswith('jwt.') and len(part) > 4:
                return part[4:]
            if part == WS_ACCESS_TOKEN_PROTOCOL and index + 1 < len(parts):
                return parts[index + 1]
        break
    return None


def chosen_ws_subprotocol(scope, *, prefer=None):
    """若客户端声明了子协议，回选一个合法值（WebSSH 用 access_token，noVNC 用 binary）。"""
    requested = []
    for name, value in scope.get('headers') or []:
        if name != b'sec-websocket-protocol':
            continue
        raw = value.decode('utf-8', errors='replace') if isinstance(value, (bytes, bytearray)) else str(value)
        requested = [part.strip() for part in raw.split(',') if part.strip()]
        break
    if prefer and prefer in requested:
        return prefer
    if WS_ACCESS_TOKEN_PROTOCOL in requested:
        return WS_ACCESS_TOKEN_PROTOCOL
    return None


@database_sync_to_async
def _get_user(validated_token):
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from accounts.idle_session import login_is_idle
    auth = JWTAuthentication()
    try:
        user = auth.get_user(validated_token)
    except Exception:
        return AnonymousUser()
    if login_is_idle(user):
        return AnonymousUser()
    return user


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.exceptions import TokenError

        token = extract_ws_token(scope)

        user = AnonymousUser()
        if token:
            try:
                validated = AccessToken(token)
                user = await _get_user(validated)
            except TokenError:
                user = AnonymousUser()
            except Exception:
                user = AnonymousUser()

        scope['user'] = user
        return await super().__call__(scope, receive, send)
