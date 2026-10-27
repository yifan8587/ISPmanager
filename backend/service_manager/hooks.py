"""
外部开通 / 对接 API 钩子调度。

本地资源可自动开通时由 provisioning.auto_provision 处理；
需要对接第三方时，通过 ServiceProvisionHook 配置 Webhook 或内部适配器。
适配器注册表 INTERNAL_ADAPTERS 供后续扩展。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Callable
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ServiceProvisionHook

logger = logging.getLogger(__name__)

# 内部适配器：adapter_key -> callable(context) -> dict
INTERNAL_ADAPTERS: dict[str, Callable[[dict], dict]] = {}


def register_adapter(key: str):
    """装饰器：注册内部对接适配器。"""
    def decorator(fn: Callable[[dict], dict]):
        INTERNAL_ADAPTERS[key] = fn
        return fn
    return decorator


def _build_context(*, event: str, order=None, instance=None, change=None, extra=None) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        'event': event,
        'timestamp': timezone.now().isoformat(),
        'order': None,
        'instance': None,
        'change': None,
    }
    if order is not None:
        ctx['order'] = {
            'id': order.id,
            'order_no': order.order_no,
            'business_type': order.business_type,
            'status': order.status,
            'customer_id': order.customer_id,
            'customer_name': order.customer.name,
            'preferred_pop_id': order.preferred_pop_id,
            'quoted_price': str(order.quoted_price) if order.quoted_price is not None else None,
            'requirements': order.requirements,
        }
    if instance is not None:
        ctx['instance'] = {
            'id': instance.id,
            'instance_no': instance.instance_no,
            'service_type': instance.service_type,
            'status': instance.status,
            'is_trial': instance.is_trial,
            'monthly_fee': str(instance.effective_monthly_fee()),
        }
    if change is not None:
        ctx['change'] = {
            'id': change.id,
            'change_no': change.change_no,
            'change_type': change.change_type,
            'status': change.status,
            'before_spec': change.before_spec,
            'after_spec': change.after_spec,
            'price_delta': str(change.price_delta) if change.price_delta is not None else None,
        }
    if extra:
        ctx['extra'] = extra
    return ctx


_BLOCKED_HOSTS = frozenset({
    'localhost', 'localhost.localdomain',
    'metadata.google.internal', 'metadata.google.com',
})


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_webhook_url(url: str) -> str:
    """拒绝非 http(s)、内网、链路本地与云元数据地址，降低 SSRF。"""
    raw = (url or '').strip()
    if not raw:
        raise ValidationError('未配置 endpoint_url')
    if getattr(settings, 'WEBHOOK_ALLOW_PRIVATE_URLS', False):
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in {'http', 'https'}:
        raise ValidationError('钩子地址仅允许 http/https')
    if parsed.username or parsed.password:
        raise ValidationError('钩子地址不能包含用户名或密码')
    host = (parsed.hostname or '').strip().lower().rstrip('.')
    if not host:
        raise ValidationError('钩子地址无效')
    if host in _BLOCKED_HOSTS or host.endswith('.localhost'):
        raise ValidationError('禁止指向本机或云元数据地址')
    candidates: list[str] = [host]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValidationError(f'无法解析钩子主机：{exc}') from exc
        candidates = [item[4][0] for item in infos if item[4]]
    for item in candidates:
        try:
            ip = ipaddress.ip_address(item)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValidationError('禁止指向内网、环回或链路本地地址')
    return raw


def _invoke_webhook(hook: ServiceProvisionHook, context: dict) -> dict:
    if not hook.endpoint_url:
        return {'ok': False, 'message': '未配置 endpoint_url'}
    try:
        validate_webhook_url(hook.endpoint_url)
    except ValidationError as exc:
        return {'ok': False, 'message': exc.messages[0] if exc.messages else str(exc)}
    headers = {'Content-Type': 'application/json'}
    if isinstance(hook.headers, dict):
        headers.update({str(k): str(v) for k, v in hook.headers.items()})
    method = (hook.http_method or 'POST').upper()
    try:
        resp = requests.request(
            method, hook.endpoint_url,
            json=context, headers=headers,
            timeout=hook.timeout_seconds or 15,
        )
        return {
            'ok': 200 <= resp.status_code < 300,
            'status_code': resp.status_code,
            'response': resp.text[:2000],
        }
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': str(exc)}


def _invoke_internal(hook: ServiceProvisionHook, context: dict) -> dict:
    adapter = INTERNAL_ADAPTERS.get(hook.adapter_key or '')
    if not adapter:
        return {
            'ok': False,
            'message': f'未注册内部适配器: {hook.adapter_key or "(空)"}，请在 hooks.INTERNAL_ADAPTERS 中注册',
            'pending_manual': True,
        }
    try:
        result = adapter(context) or {}
        if 'ok' not in result:
            result = {'ok': True, **result}
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception('内部适配器执行失败 key=%s', hook.adapter_key)
        return {'ok': False, 'message': str(exc)}


def dispatch_provision_hooks(
    event: str,
    *,
    order=None,
    instance=None,
    change=None,
    extra=None,
) -> list[dict]:
    """按事件分发所有启用的钩子；异常不抛出。"""
    business_type = ''
    if order is not None:
        business_type = order.business_type
    elif instance is not None:
        business_type = instance.service_order.business_type

    hooks = ServiceProvisionHook.objects.filter(is_active=True, event=event)
    context = _build_context(
        event=event, order=order, instance=instance, change=change, extra=extra,
    )
    results = []
    for hook in hooks:
        if business_type and not hook.matches_business_type(business_type):
            continue
        try:
            if hook.hook_type == ServiceProvisionHook.HookType.WEBHOOK:
                result = _invoke_webhook(hook, context)
            elif hook.hook_type == ServiceProvisionHook.HookType.INTERNAL:
                result = _invoke_internal(hook, context)
            else:
                result = {
                    'ok': True,
                    'pending_manual': True,
                    'message': '已标记为待人工对接（manual）',
                }
        except Exception as exc:  # noqa: BLE001
            logger.exception('钩子执行异常 hook=%s', hook.name)
            result = {'ok': False, 'message': str(exc)}
        results.append({'hook_id': hook.id, 'hook_name': hook.name, **result})
        logger.info(
            'provision hook [%s] event=%s ok=%s',
            hook.name, event, result.get('ok'),
        )
    return results
