"""WebSocket 路由：WebSSH 终端 + VPS noVNC 远程控制台。"""

from django.urls import re_path

from .ssh_consumer import PopSSHConsumer
from .vnc_consumer import VPSVNCConsumer

websocket_urlpatterns = [
    re_path(r'^ws/resource/pop-ssh/(?P<pop_id>\d+)/$', PopSSHConsumer.as_asgi()),
    re_path(r'^ws/resource/vnc/(?P<vps_id>\d+)/$', VPSVNCConsumer.as_asgi()),
]
