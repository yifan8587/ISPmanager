"""Channels 通道层：开发默认内存，生产可通过 Redis 跨 gunicorn worker。

未设置 Redis 时保持 InMemory，本地零配置。
设置 ``CHANNEL_REDIS_URL`` / ``REDIS_URL``，或 ``CHANNEL_LAYER_BACKEND=redis`` 后
使用 ``channels_redis``，以便 WebSSH / VNC 在多进程下共享通道。
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import quote

INMEMORY_BACKEND = 'channels.layers.InMemoryChannelLayer'
REDIS_BACKEND = 'channels_redis.core.RedisChannelLayer'
DEFAULT_PREFIX = 'ispmanager'


def _env(env: Mapping[str, str], *keys: str, default: str = '') -> str:
    for key in keys:
        value = env.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _normalize_backend(value: str) -> str:
    return value.lower().replace('-', '').replace('_', '')


def _want_redis(env: Mapping[str, str]) -> bool:
    backend = _normalize_backend(_env(env, 'CHANNEL_LAYER_BACKEND'))
    if backend in {'memory', 'inmemory'}:
        return False
    if backend in {'redis', 'channelsredis'}:
        return True
    return bool(_env(env, 'CHANNEL_REDIS_URL', 'REDIS_URL'))


def redis_connection_url(env: Mapping[str, str] | None = None) -> str:
    environ = os.environ if env is None else env
    url = _env(environ, 'CHANNEL_REDIS_URL', 'REDIS_URL')
    if url:
        return url
    host = _env(environ, 'REDIS_HOST', default='127.0.0.1')
    port = _env(environ, 'REDIS_PORT', default='6379')
    db = _env(environ, 'CHANNEL_REDIS_DB', 'REDIS_DB', default='0')
    password = _env(environ, 'CHANNEL_REDIS_PASSWORD', 'REDIS_PASSWORD')
    if password:
        return f'redis://:{quote(password, safe="")}@{host}:{port}/{db}'
    return f'redis://{host}:{port}/{db}'


def build_channel_layers(
    env: Mapping[str, str] | None = None,
    *,
    secret_key: str = '',
) -> dict:
    environ = os.environ if env is None else env
    if not _want_redis(environ):
        return {
            'default': {
                'BACKEND': INMEMORY_BACKEND,
            },
        }

    config: dict = {
        'hosts': [redis_connection_url(environ)],
        'prefix': _env(environ, 'CHANNEL_REDIS_PREFIX', default=DEFAULT_PREFIX),
    }
    key = secret_key or _env(environ, 'DJANGO_SECRET_KEY')
    if key:
        config['symmetric_encryption_keys'] = [key]
    return {
        'default': {
            'BACKEND': REDIS_BACKEND,
            'CONFIG': config,
        },
    }
