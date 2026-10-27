"""对外客服配置：仅供门户展示，与 IM 对内告警通道隔离。"""
from __future__ import annotations

from django.core.cache import cache

from .models import SystemSetting

CACHE_KEY = 'portal:public_support_config'
CACHE_TTL = 60

PUBLIC_SUPPORT_KEYS = (
    'support_qq',
    'support_wechat',
    'support_wechat_qr',
    'support_telegram',
    'support_wecom_kf_url',
    'support_email',
    'support_phone',
)


def invalidate_public_support_cache() -> None:
    cache.delete(CACHE_KEY)


def _load_public_support_config() -> dict:
    settings_map = {
        s.key: (s.value or '').strip()
        for s in SystemSetting.objects.filter(key__in=PUBLIC_SUPPORT_KEYS, is_public=True)
    }
    return {
        'qq': settings_map.get('support_qq', ''),
        'wechat': settings_map.get('support_wechat', ''),
        'wechat_qr': settings_map.get('support_wechat_qr', ''),
        'telegram': settings_map.get('support_telegram', ''),
        'wecom_kf_url': settings_map.get('support_wecom_kf_url', ''),
        'email': settings_map.get('support_email', ''),
        'phone': settings_map.get('support_phone', ''),
    }


def get_public_support_config(*, use_cache: bool = True) -> dict:
    """门户客服弹窗数据。只读公开系统参数，不读取 IMChannel.default_target。"""
    if use_cache:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict):
            return cached
    payload = _load_public_support_config()
    cache.set(CACHE_KEY, payload, CACHE_TTL)
    return payload
