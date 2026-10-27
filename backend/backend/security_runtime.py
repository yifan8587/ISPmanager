"""生产运行时安全校验。开发态（DEBUG 且未开 DJANGO_PRODUCTION）不拦截。"""
from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

INSECURE_DEFAULT_SECRET = (
    'django-insecure-h(9m6of&do(k19v1+82^1tg8+=$l!^p$on%kb+*ci)8r0q6l&t'
)
DEFAULT_DB_PASSWORD = 'ISPmanager2024!'


def collect_insecure_runtime_errors(
    *,
    debug: bool,
    secret_key: str,
    allowed_hosts: list[str],
    cors_allow_all: bool,
    db_password: str,
    production: bool,
) -> list[str]:
    if not production and debug:
        return []
    errors: list[str] = []
    if debug and production:
        errors.append('生产环境必须设置 DJANGO_DEBUG=false')
    if not secret_key or secret_key == INSECURE_DEFAULT_SECRET or secret_key.startswith('django-insecure-'):
        errors.append('必须通过 DJANGO_SECRET_KEY 设置非默认密钥')
    hosts = [h for h in (allowed_hosts or []) if h]
    if not hosts or hosts == ['*']:
        errors.append('必须设置 DJANGO_ALLOWED_HOSTS（禁止 *）')
    if cors_allow_all:
        errors.append('必须设置 DJANGO_CORS_ALLOWED_ORIGINS，禁止全放行')
    if production and (not db_password or db_password == DEFAULT_DB_PASSWORD):
        errors.append('生产环境必须设置非默认 DB_PASSWORD')
    if production and os.environ.get('DEPLOY_LAYOUT', 'combined').strip().lower() == 'split':
        portal_secret = os.environ.get('PORTAL_API_SECRET', '').strip()
        portal_origin = os.environ.get('PORTAL_PUBLIC_ORIGIN', '').strip()
        if len(portal_secret) < 32:
            errors.append('分机部署必须设置至少 32 位 PORTAL_API_SECRET')
        if not portal_origin.startswith('https://') and not portal_origin.startswith('http://'):
            errors.append('分机部署必须设置 PORTAL_PUBLIC_ORIGIN')
    return errors


def enforce_secure_runtime(**kwargs) -> None:
    errors = collect_insecure_runtime_errors(**kwargs)
    if errors:
        raise ImproperlyConfigured(
            '不安全的运行配置，已拒绝启动：' + '；'.join(errors)
        )
