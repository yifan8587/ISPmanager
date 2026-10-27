"""支付渠道凭据的加密存储辅助函数。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class PaymentCredentialError(ValueError):
    """支付凭据无法解密或格式错误。"""


def _fernet_key() -> bytes:
    """优先使用独立环境变量；未配置时由 Django SECRET_KEY 推导开发默认密钥。"""
    material = os.environ.get('PAYMENT_CONFIG_ENCRYPTION_KEY', '').strip()
    if not material:
        material = settings.SECRET_KEY
    # 接受标准 Fernet key，其他值（如环境变量口令）通过 SHA-256 规整。
    try:
        decoded = base64.urlsafe_b64decode(material.encode('ascii'))
        if len(decoded) == 32:
            return material.encode('ascii')
    except (ValueError, UnicodeEncodeError):
        pass
    digest = hashlib.sha256(material.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_payment_config(config: dict[str, Any] | None) -> str:
    payload = json.dumps(config or {}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return Fernet(_fernet_key()).encrypt(payload).decode('ascii')


def decrypt_payment_config(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        raw = Fernet(_fernet_key()).decrypt(token.encode('ascii'))
        value = json.loads(raw.decode('utf-8'))
    except (InvalidToken, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentCredentialError('支付渠道凭据无法解密，请检查 PAYMENT_CONFIG_ENCRYPTION_KEY') from exc
    if not isinstance(value, dict):
        raise PaymentCredentialError('支付渠道凭据格式无效')
    return value
