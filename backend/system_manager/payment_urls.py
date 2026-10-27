"""支付平台异步通知地址。

管理端展示、以及向支付宝 / 微信 / 银联下单时提交的 notify_url，都走这里，
避免把管理端页面来源（如 :3000）写进支付平台。
"""
from __future__ import annotations

from django.conf import settings
from django.urls import reverse


def _public_origin(host: str, port: str = '') -> str:
    host = host.strip().rstrip('/')
    if '://' in host:
        return host
    port = str(port or '').strip()
    if port in ('', '443'):
        return f'https://{host}'
    if port == '80':
        return f'http://{host}'
    scheme = str(getattr(settings, 'PAYMENT_PUBLIC_SCHEME', '') or '').strip().lower()
    if scheme not in {'http', 'https'}:
        scheme = 'https' if getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) else 'http'
    return f'{scheme}://{host}:{port}'


def public_payment_base(request=None) -> str:
    configured = str(getattr(settings, 'PAYMENT_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    if configured:
        return configured
    site = str(getattr(settings, 'SITE_DOMAIN', '') or '').strip()
    domain = site or str(getattr(settings, 'ADMIN_DOMAIN', '') or '').strip()
    if domain:
        port = '' if site else str(getattr(settings, 'ADMIN_PUBLIC_PORT', '') or '')
        return _public_origin(domain, port)
    if request is not None:
        return request.build_absolute_uri('/').rstrip('/')
    return ''


def payment_callback_url(channel_id: int, request=None) -> str:
    path = reverse('payment-webhook', kwargs={'channel_id': channel_id})
    base = public_payment_base(request)
    return f'{base}{path}' if base else path


def browser_absolute_url(request, path: str) -> str:
    """浏览器要打开的本站地址。门户 API 专用端口上的 Host 不能拿来回跳。"""
    if not path.startswith('/'):
        path = f'/{path}'
    base = public_payment_base(request)
    if base:
        return f'{base}{path}'
    return request.build_absolute_uri(path)
