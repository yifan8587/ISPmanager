"""运维 WebSSH WebSocket 路由。"""

from django.urls import re_path

from .ssh_consumer import OperationSSHConsumer

websocket_urlpatterns = [
    re_path(
        r'^ws/operation/webssh/(?P<target_type>pop|vps|monitoring_host|custom)/(?P<target_id>\d+)/$',
        OperationSSHConsumer.as_asgi(),
    ),
]
