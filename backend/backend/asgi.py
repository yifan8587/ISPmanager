"""
ASGI config for backend project.

同时暴露 HTTP（Django）与 WebSocket（Channels）两种协议：
  - http      → 标准 Django ASGI 应用（DRF / admin / 静态）
  - websocket → resource / operation WebSSH 终端（JWT 鉴权）

生产建议用 uvicorn worker 启动以支持 WebSocket：
  gunicorn backend.asgi:application -k uvicorn.workers.UvicornWorker ...
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

# 必须在导入任何使用 ORM / settings 的模块前初始化 Django
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from backend.portal_channel import PortalChannelMiddleware  # noqa: E402
from resource_manager.ws_auth import JWTAuthMiddleware  # noqa: E402
from resource_manager.routing import websocket_urlpatterns as resource_ws  # noqa: E402
from operation_manager.routing import websocket_urlpatterns as operation_ws  # noqa: E402

application = PortalChannelMiddleware(ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': JWTAuthMiddleware(
        URLRouter(resource_ws + operation_ws)
    ),
}))
