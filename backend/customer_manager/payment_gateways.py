"""在线支付服务商适配器。

所有金额、账单归属与最终入账均在 ``payment_service`` 中处理；本模块只负责：

* 向服务商创建支付单并生成跳转/表单/二维码/SKD 参数；
* 对同步回跳与异步通知进行验签；
* 从经过验签的报文提取服务商订单号、支付流水和状态；
* Stripe / PayPal 原路退款（其余渠道由业务层标为待人工退）。

这样可避免把任何来自浏览器或回调的“支付成功”字段直接写入账单。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from urllib.parse import urlencode

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.utils import timezone


REQUEST_TIMEOUT = 25
_PAYPAL_TOKEN_CACHE: dict[tuple[int, bool], tuple[str, float]] = {}
_ZERO_DECIMAL_CURRENCIES = frozenset({
    'BIF', 'CLP', 'DJF', 'GNF', 'JPY', 'KMF', 'KRW', 'MGA', 'PYG',
    'RWF', 'UGX', 'VND', 'VUV', 'XAF', 'XOF', 'XPF',
})


class PaymentGatewayError(ValueError):
    """可安全展示给发起支付用户的网关错误。"""


@dataclass
class GatewayResult:
    """已验签的服务商状态。"""

    transaction_no: str = ''
    provider_order_id: str = ''
    provider_payment_id: str = ''
    paid_amount: Decimal | None = None
    paid_currency: str = ''
    succeeded: bool = False
    failed: bool = False
    cancelled: bool = False
    processing: bool = False
    failure_reason: str = ''
    response: dict[str, Any] = field(default_factory=dict)
    webhook_event_id: str = ''
    webhook_event_type: str = ''
    webhook_transmission_id: str = ''
    webhook_payload: dict[str, Any] = field(default_factory=dict)


def _header(headers: Any, name: str) -> str:
    """兼容 Django HttpRequest headers 与普通 dict。"""
    try:
        return str(headers.get(name, headers.get(name.lower(), '')) or '')
    except AttributeError:
        return ''


def _config(channel) -> dict[str, Any]:
    try:
        return channel.get_config()
    except Exception as exc:  # noqa: BLE001
        raise PaymentGatewayError('支付渠道凭据无法读取，请联系管理员') from exc


def _response_json(response: requests.Response, context: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400:
        detail = (
            payload.get('message')
            or payload.get('error_description')
            or payload.get('error')
            or payload.get('code')
            or response.text[:200]
            or f'HTTP {response.status_code}'
        )
        issues = []
        for item in payload.get('details') or []:
            if not isinstance(item, dict):
                continue
            issue = str(item.get('issue') or '').strip()
            description = str(item.get('description') or '').strip()
            if issue or description:
                issues.append(f'{issue}: {description}'.strip(': '))
        if issues:
            detail = f'{detail}；{"；".join(issues)}'
        debug_id = str(payload.get('debug_id') or '').strip()
        if debug_id:
            detail = f'{detail}（PayPal debug_id: {debug_id}）'
        raise PaymentGatewayError(f'{context}失败：{detail}')
    if not isinstance(payload, dict):
        raise PaymentGatewayError(f'{context}失败：服务商返回格式无效')
    return payload


def _minor_amount(amount: Decimal, currency: str) -> int:
    currency = str(currency).upper()
    if currency in _ZERO_DECIMAL_CURRENCIES:
        return int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _format_amount(amount: Decimal, currency: str = '') -> str:
    precision = Decimal('1') if str(currency).upper() in _ZERO_DECIMAL_CURRENCIES else Decimal('0.01')
    return str(amount.quantize(precision, rounding=ROUND_HALF_UP))


def _decimal_from_minor(value: Any, currency: str) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    if str(currency).upper() in _ZERO_DECIMAL_CURRENCIES:
        return amount
    return (amount / Decimal('100')).quantize(Decimal('0.01'))


def _normalize_pem(value: str, kind: str = 'PUBLIC KEY') -> bytes:
    value = str(value or '').strip().replace('\\n', '\n')
    if not value:
        raise PaymentGatewayError('支付渠道缺少密钥配置')
    if '-----BEGIN' not in value:
        compact = ''.join(value.split())
        value = f'-----BEGIN {kind}-----\n{compact}\n-----END {kind}-----'
    return value.encode('utf-8')


def _load_private_key(value: str):
    try:
        return serialization.load_pem_private_key(_normalize_pem(value, 'PRIVATE KEY'), password=None)
    except (TypeError, ValueError) as exc:
        raise PaymentGatewayError('支付渠道私钥格式无效') from exc


def _load_public_key(value: str):
    pem = _normalize_pem(value, 'PUBLIC KEY')
    try:
        return serialization.load_pem_public_key(pem)
    except ValueError:
        try:
            return x509.load_pem_x509_certificate(pem).public_key()
        except ValueError as exc:
            raise PaymentGatewayError('支付渠道公钥或证书格式无效') from exc


def _rsa_sign(value: str, private_key: str) -> str:
    key = _load_private_key(private_key)
    signature = key.sign(
        value.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode('ascii')


def _rsa_verify(value: str, signature: str, public_key: str) -> bool:
    try:
        _load_public_key(public_key).verify(
            base64.b64decode(str(signature or '')),
            value.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (InvalidSignature, ValueError, TypeError, PaymentGatewayError):
        return False
    return True


def _callback_data(data: Any) -> dict[str, str]:
    """QueryDict/普通 dict 统一成扁平字符串字典，验签前不改动值。"""
    if hasattr(data, 'items'):
        return {str(key): str(value) for key, value in data.items()}
    return {}


def _safe_response(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    """只记录对排错/审计有用的非敏感服务商字段。"""
    return {key: payload.get(key) for key in keys if payload.get(key) is not None}


# ── Stripe ─────────────────────────────────────────────────────

def _stripe_base_url(_sandbox: bool) -> str:
    return 'https://api.stripe.com'


def _stripe_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    config = _config(channel)
    secret_key = str(config.get('secret_key') or '')
    if not secret_key:
        raise PaymentGatewayError('Stripe 缺少 Secret key')
    success_url = f'{return_url}?session_id={{CHECKOUT_SESSION_ID}}'
    cancel_url = f'{return_url}?cancelled=1'
    data = {
        'mode': 'payment',
        'success_url': success_url,
        'cancel_url': cancel_url,
        'client_reference_id': transaction.transaction_no,
        'metadata[transaction_no]': transaction.transaction_no,
        'payment_intent_data[metadata][transaction_no]': transaction.transaction_no,
        'line_items[0][price_data][currency]': transaction.currency.lower(),
        'line_items[0][price_data][unit_amount]': str(_minor_amount(transaction.amount, transaction.currency)),
        'line_items[0][price_data][product_data][name]': f'账单 {transaction.billing.bill_no}',
        'line_items[0][quantity]': '1',
    }
    response = requests.post(
        f'{_stripe_base_url(channel.sandbox)}/v1/checkout/sessions',
        auth=(secret_key, ''),
        data=data,
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '创建 Stripe Checkout 会话')
    url = payload.get('url')
    session_id = payload.get('id')
    if not url or not session_id:
        raise PaymentGatewayError('创建 Stripe Checkout 会话失败：缺少跳转地址')
    return {
        'mode': 'redirect',
        'url': url,
        'provider_order_id': str(session_id),
        'response': _safe_response(payload, 'id', 'status', 'payment_status', 'expires_at'),
    }


def _stripe_result_from_session(channel, payload: dict[str, Any]) -> GatewayResult:
    metadata = payload.get('metadata') or {}
    payment_status = str(payload.get('payment_status') or '').lower()
    status = str(payload.get('status') or '').lower()
    return GatewayResult(
        transaction_no=str(metadata.get('transaction_no') or payload.get('client_reference_id') or ''),
        provider_order_id=str(payload.get('id') or ''),
        provider_payment_id=str(payload.get('payment_intent') or ''),
        paid_amount=_decimal_from_minor(payload.get('amount_total'), str(payload.get('currency') or '')),
        paid_currency=str(payload.get('currency') or '').upper(),
        succeeded=payment_status == 'paid',
        failed=status == 'expired',
        processing=payment_status in ('unpaid', 'no_payment_required') and status == 'open',
        response=_safe_response(payload, 'id', 'status', 'payment_status', 'payment_intent', 'client_reference_id'),
    )


def _stripe_verify_webhook(channel, raw_body: bytes, headers: Any) -> dict[str, Any]:
    config = _config(channel)
    secret = str(config.get('webhook_secret') or '')
    signature_header = _header(headers, 'Stripe-Signature')
    if not secret or not signature_header:
        raise PaymentGatewayError('Stripe webhook 缺少验签配置或签名')
    parts: dict[str, list[str]] = {}
    for part in signature_header.split(','):
        key, sep, value = part.partition('=')
        if sep:
            parts.setdefault(key.strip(), []).append(value.strip())
    timestamp = (parts.get('t') or [''])[0]
    if not timestamp:
        raise PaymentGatewayError('Stripe webhook 签名格式无效')
    try:
        if abs(int(time.time()) - int(timestamp)) > 300:
            raise PaymentGatewayError('Stripe webhook 时间戳已过期')
    except ValueError as exc:
        raise PaymentGatewayError('Stripe webhook 时间戳无效') from exc
    expected = hmac.new(
        secret.encode('utf-8'),
        f'{timestamp}.'.encode('utf-8') + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in parts.get('v1', [])):
        raise PaymentGatewayError('Stripe webhook 验签失败')
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentGatewayError('Stripe webhook 内容无效') from exc
    if not isinstance(payload, dict):
        raise PaymentGatewayError('Stripe webhook 内容无效')
    return payload


def _stripe_webhook_result(channel, raw_body: bytes, headers: Any) -> GatewayResult:
    event = _stripe_verify_webhook(channel, raw_body, headers)
    event_type = str(event.get('type') or '')
    session = ((event.get('data') or {}).get('object') or {})
    if not isinstance(session, dict):
        raise PaymentGatewayError('Stripe webhook 缺少对象数据')
    result = _stripe_result_from_session(channel, session)
    if event_type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
        result.succeeded = str(session.get('payment_status') or '').lower() == 'paid'
    elif event_type in ('checkout.session.async_payment_failed', 'checkout.session.expired'):
        result.failed = True
        result.failure_reason = 'Stripe 支付未完成或已过期'
    else:
        result.processing = True
    return result


def _stripe_return_result(transaction, channel, data: dict[str, str]) -> GatewayResult:
    if data.get('cancelled') == '1':
        return GatewayResult(transaction_no=transaction.transaction_no, cancelled=True)
    session_id = data.get('session_id', '')
    if not session_id:
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    config = _config(channel)
    response = requests.get(
        f'{_stripe_base_url(channel.sandbox)}/v1/checkout/sessions/{session_id}',
        auth=(str(config.get('secret_key') or ''), ''),
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '查询 Stripe 支付状态')
    result = _stripe_result_from_session(channel, payload)
    if result.transaction_no and result.transaction_no != transaction.transaction_no:
        raise PaymentGatewayError('Stripe 支付会话与本地交易不匹配')
    result.transaction_no = transaction.transaction_no
    return result


# ── PayPal ─────────────────────────────────────────────────────

def _paypal_base_url(sandbox: bool) -> str:
    return 'https://api-m.sandbox.paypal.com' if sandbox else 'https://api-m.paypal.com'


def _paypal_token(channel, config: dict[str, Any]) -> str:
    cache_key = (int(getattr(channel, 'id', 0) or 0), bool(channel.sandbox))
    cached = _PAYPAL_TOKEN_CACHE.get(cache_key)
    if cached and cached[1] > time.time():
        return cached[0]
    response = requests.post(
        f'{_paypal_base_url(channel.sandbox)}/v1/oauth2/token',
        auth=(str(config.get('client_id') or ''), str(config.get('client_secret') or '')),
        data={'grant_type': 'client_credentials'},
        headers={'Accept': 'application/json'},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '获取 PayPal access token')
    token = payload.get('access_token')
    if not token:
        raise PaymentGatewayError('获取 PayPal access token 失败')
    expires_in = int(payload.get('expires_in') or 300)
    _PAYPAL_TOKEN_CACHE[cache_key] = (str(token), time.time() + max(30, expires_in - 60))
    return str(token)


def _paypal_request_id(transaction_no: str, operation: str) -> str:
    """为同一资金操作生成稳定且不超过 PayPal 38 字节限制的幂等键。"""
    digest = hashlib.sha256(f'{transaction_no}:{operation}'.encode('utf-8')).hexdigest()
    return f'PP{operation[:2].upper()}{digest[:32]}'


def _paypal_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    config = _config(channel)
    if not config.get('client_id') or not config.get('client_secret'):
        raise PaymentGatewayError('PayPal 缺少 Client ID 或 Client Secret')
    token = _paypal_token(channel, config)
    payload = {
        'intent': 'CAPTURE',
        'purchase_units': [{
            'reference_id': transaction.transaction_no,
            'custom_id': transaction.transaction_no,
            'invoice_id': transaction.billing.bill_no,
            'description': f'账单 {transaction.billing.bill_no}',
            'amount': {
                'currency_code': transaction.currency.upper(),
                'value': _format_amount(transaction.amount, transaction.currency),
            },
        }],
        'application_context': {
            'return_url': return_url,
            'cancel_url': f'{return_url}?cancelled=1',
            'user_action': 'PAY_NOW',
        },
    }
    response = requests.post(
        f'{_paypal_base_url(channel.sandbox)}/v2/checkout/orders',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'PayPal-Request-Id': _paypal_request_id(
                transaction.transaction_no,
                'create',
            ),
            'Prefer': 'return=representation',
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    created = _response_json(response, '创建 PayPal 订单')
    approve_url = next(
        (link.get('href') for link in (created.get('links') or []) if link.get('rel') == 'approve'),
        None,
    )
    order_id = created.get('id')
    if not approve_url or not order_id:
        raise PaymentGatewayError('创建 PayPal 订单失败：缺少跳转地址')
    return {
        'mode': 'redirect',
        'url': approve_url,
        'provider_order_id': str(order_id),
        'response': _safe_response(created, 'id', 'status', 'create_time'),
    }


def _paypal_capture(transaction, channel, order_id: str) -> GatewayResult:
    if transaction.provider_order_id and order_id != transaction.provider_order_id:
        raise PaymentGatewayError('PayPal 订单与本地交易不匹配')
    config = _config(channel)
    token = _paypal_token(channel, config)
    response = requests.post(
        f'{_paypal_base_url(channel.sandbox)}/v2/checkout/orders/{order_id}/capture',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'PayPal-Request-Id': _paypal_request_id(
                transaction.transaction_no,
                'capture',
            ),
            'Prefer': 'return=representation',
        },
        json={},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '确认 PayPal 支付')
    capture = (
        (((payload.get('purchase_units') or [{}])[0].get('payments') or {})
         .get('captures') or [{}])[0]
    )
    ref = str(((payload.get('purchase_units') or [{}])[0].get('reference_id') or ''))
    order_status = str(payload.get('status') or '').upper()
    capture_status = str(capture.get('status') or '').upper()
    result = GatewayResult(
        transaction_no=ref or transaction.transaction_no,
        provider_order_id=str(payload.get('id') or order_id),
        provider_payment_id=str(capture.get('id') or ''),
        paid_amount=Decimal(str((capture.get('amount') or {}).get('value')))
        if (capture.get('amount') or {}).get('value') is not None else None,
        paid_currency=str((capture.get('amount') or {}).get('currency_code') or '').upper(),
        succeeded=capture_status == 'COMPLETED',
        processing=(
            capture_status == 'PENDING'
            or order_status in {'CREATED', 'APPROVED', 'PAYER_ACTION_REQUIRED'}
        ),
        failed=(
            capture_status in {'DECLINED', 'FAILED'}
            or order_status in {'VOIDED', 'FAILED'}
        ),
        failure_reason=(
            f'PayPal capture 状态为 {capture_status or order_status}'
            if capture_status in {'DECLINED', 'FAILED'} or order_status in {'VOIDED', 'FAILED'}
            else ''
        ),
        response={
            **_safe_response(payload, 'id', 'status', 'update_time'),
            'capture_id': str(capture.get('id') or ''),
            'capture_status': capture_status,
        },
    )
    return result


def _paypal_verify_webhook(channel, raw_body: bytes, headers: Any) -> dict[str, Any]:
    config = _config(channel)
    webhook_id = str(config.get('webhook_id') or '')
    if not webhook_id:
        raise PaymentGatewayError('PayPal webhook 未配置 Webhook ID')
    try:
        event = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentGatewayError('PayPal webhook 内容无效') from exc
    token = _paypal_token(channel, config)
    verification = {
        'auth_algo': _header(headers, 'PayPal-Auth-Algo'),
        'cert_url': _header(headers, 'PayPal-Cert-Url'),
        'transmission_id': _header(headers, 'PayPal-Transmission-Id'),
        'transmission_sig': _header(headers, 'PayPal-Transmission-Sig'),
        'transmission_time': _header(headers, 'PayPal-Transmission-Time'),
        'webhook_id': webhook_id,
        'webhook_event': event,
    }
    response = requests.post(
        f'{_paypal_base_url(channel.sandbox)}/v1/notifications/verify-webhook-signature',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json=verification,
        timeout=REQUEST_TIMEOUT,
    )
    verdict = _response_json(response, '验证 PayPal webhook')
    if str(verdict.get('verification_status') or '').upper() != 'SUCCESS':
        raise PaymentGatewayError('PayPal webhook 验签失败')
    return event


def _paypal_webhook_result(channel, raw_body: bytes, headers: Any) -> GatewayResult:
    event = _paypal_verify_webhook(channel, raw_body, headers)
    event_type = str(event.get('event_type') or '')
    resource = event.get('resource') or {}
    related = resource.get('supplementary_data', {}).get('related_ids', {}) if isinstance(resource, dict) else {}
    if event_type.startswith('CHECKOUT.ORDER'):
        order_id = str(resource.get('id') or related.get('order_id') or '')
    else:
        order_id = str(related.get('order_id') or '')
    custom_id = str(resource.get('custom_id') or '')
    if not custom_id and isinstance(resource.get('purchase_units'), list) and resource['purchase_units']:
        custom_id = str(resource['purchase_units'][0].get('reference_id') or '')
    success = event_type == 'PAYMENT.CAPTURE.COMPLETED'
    failed = event_type in {
        'PAYMENT.CAPTURE.DENIED',
        'PAYMENT.CAPTURE.DECLINED',
        'PAYMENT.CAPTURE.REVERSED',
        'CHECKOUT.PAYMENT-APPROVAL.REVERSED',
    }
    return GatewayResult(
        transaction_no=custom_id,
        provider_order_id=order_id,
        provider_payment_id=str(resource.get('id') or '') if event_type.startswith('PAYMENT.CAPTURE') else '',
        paid_amount=Decimal(str((resource.get('amount') or {}).get('value')))
        if (resource.get('amount') or {}).get('value') is not None else None,
        paid_currency=str((resource.get('amount') or {}).get('currency_code') or '').upper(),
        succeeded=success,
        failed=failed,
        processing=not success and not failed,
        failure_reason=f'PayPal 事件 {event_type}' if failed else '',
        response={
            **_safe_response(resource, 'id', 'status', 'update_time'),
            'event_id': str(event.get('id') or ''),
            'event_type': event_type,
            'transmission_id': _header(headers, 'PayPal-Transmission-Id'),
        },
        webhook_event_id=str(event.get('id') or ''),
        webhook_event_type=event_type,
        webhook_transmission_id=_header(headers, 'PayPal-Transmission-Id'),
        webhook_payload=event,
    )


def _paypal_return_result(transaction, channel, data: dict[str, str]) -> GatewayResult:
    if data.get('cancelled') == '1':
        return GatewayResult(transaction_no=transaction.transaction_no, cancelled=True)
    order_id = data.get('token') or data.get('order_id') or ''
    if not order_id:
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    return _paypal_capture(transaction, channel, order_id)


def capture_paypal_order(transaction, channel, order_id: str) -> GatewayResult:
    """供已验签 CHECKOUT.ORDER.APPROVED webhook 触发服务端 capture。"""
    return _paypal_capture(transaction, channel, order_id)


# ── 支付宝 ─────────────────────────────────────────────────────

def _alipay_base_url(sandbox: bool) -> str:
    return (
        'https://openapi-sandbox.dl.alipaydev.com/gateway.do'
        if sandbox else 'https://openapi.alipay.com/gateway.do'
    )


def _alipay_signing_text(data: dict[str, str]) -> str:
    return '&'.join(
        f'{key}={data[key]}'
        for key in sorted(data)
        if key not in {'sign', 'sign_type'} and data[key] not in (None, '')
    )


def _alipay_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    if transaction.currency.upper() != 'CNY':
        raise PaymentGatewayError('支付宝仅支持 CNY 账单')
    config = _config(channel)
    now = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
    params = {
        'app_id': str(config.get('app_id') or ''),
        'method': 'alipay.trade.page.pay',
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': now,
        'version': '1.0',
        'notify_url': callback_url,
        'return_url': return_url,
        'biz_content': json.dumps({
            'out_trade_no': transaction.transaction_no,
            'product_code': 'FAST_INSTANT_TRADE_PAY',
            'total_amount': _format_amount(transaction.amount, transaction.currency),
            'subject': f'账单 {transaction.billing.bill_no}',
            'timeout_express': '30m',
        }, ensure_ascii=False, separators=(',', ':')),
    }
    if not params['app_id']:
        raise PaymentGatewayError('支付宝缺少 App ID')
    params['sign'] = _rsa_sign(_alipay_signing_text(params), str(config.get('app_private_key') or ''))
    return {
        'mode': 'form',
        'form_action': _alipay_base_url(channel.sandbox),
        'form_method': 'GET',
        'form_fields': params,
        'provider_order_id': transaction.transaction_no,
        'response': {'out_trade_no': transaction.transaction_no},
    }


def _alipay_result(channel, data: dict[str, str]) -> GatewayResult:
    config = _config(channel)
    if not _rsa_verify(
        _alipay_signing_text(data),
        data.get('sign', ''),
        str(config.get('alipay_public_key') or ''),
    ):
        raise PaymentGatewayError('支付宝通知验签失败')
    trade_status = str(data.get('trade_status') or '').upper()
    return GatewayResult(
        transaction_no=str(data.get('out_trade_no') or ''),
        provider_order_id=str(data.get('out_trade_no') or ''),
        provider_payment_id=str(data.get('trade_no') or ''),
        paid_amount=Decimal(str(data.get('total_amount'))) if data.get('total_amount') else None,
        paid_currency='CNY',
        succeeded=trade_status in {'TRADE_SUCCESS', 'TRADE_FINISHED'},
        failed=trade_status in {'TRADE_CLOSED'},
        processing=trade_status in {'WAIT_BUYER_PAY', ''},
        failure_reason='支付宝交易已关闭' if trade_status == 'TRADE_CLOSED' else '',
        response={
            'trade_status': trade_status,
            'out_trade_no': data.get('out_trade_no', ''),
            'trade_no': data.get('trade_no', ''),
            'total_amount': data.get('total_amount', ''),
        },
    )


def _alipay_return_result(transaction, channel, data: dict[str, str]) -> GatewayResult:
    result = _alipay_result(channel, data)
    if result.transaction_no != transaction.transaction_no:
        raise PaymentGatewayError('支付宝回跳交易与本地交易不匹配')
    return result


# ── 微信支付 API v3 ───────────────────────────────────────────

def _wechat_authorization(config: dict[str, Any], method: str, path: str, body: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    message = f'{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n'
    signature = _rsa_sign(message, str(config.get('merchant_private_key') or ''))
    return (
        'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{config.get("mch_id", "")}",'
        f'nonce_str="{nonce}",timestamp="{timestamp}",'
        f'serial_no="{config.get("merchant_serial_no", "")}",signature="{signature}"'
    )


def _wechat_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    if transaction.currency.upper() != 'CNY':
        raise PaymentGatewayError('微信支付仅支持 CNY 账单')
    config = _config(channel)
    required = ('app_id', 'mch_id', 'merchant_serial_no', 'merchant_private_key', 'api_v3_key')
    if any(not str(config.get(key) or '').strip() for key in required):
        raise PaymentGatewayError('微信支付渠道配置不完整')
    path = '/v3/pay/transactions/native'
    payload = {
        'appid': str(config['app_id']),
        'mchid': str(config['mch_id']),
        'description': f'账单 {transaction.billing.bill_no}',
        'out_trade_no': transaction.transaction_no,
        'notify_url': callback_url,
        'amount': {'total': _minor_amount(transaction.amount, transaction.currency), 'currency': 'CNY'},
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    response = requests.post(
        f'https://api.mch.weixin.qq.com{path}',
        headers={
            'Authorization': _wechat_authorization(config, 'POST', path, body),
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        },
        data=body.encode('utf-8'),
        timeout=REQUEST_TIMEOUT,
    )
    created = _response_json(response, '创建微信支付订单')
    code_url = created.get('code_url')
    if not code_url:
        raise PaymentGatewayError('创建微信支付订单失败：缺少二维码链接')
    return {
        'mode': 'qr',
        'qr_code_text': str(code_url),
        'provider_order_id': transaction.transaction_no,
        'response': {'code_url_created': True, 'out_trade_no': transaction.transaction_no},
    }


def _wechat_verify_and_decrypt(channel, raw_body: bytes, headers: Any) -> dict[str, Any]:
    config = _config(channel)
    timestamp = _header(headers, 'Wechatpay-Timestamp')
    nonce = _header(headers, 'Wechatpay-Nonce')
    signature = _header(headers, 'Wechatpay-Signature')
    if not timestamp or not nonce or not signature:
        raise PaymentGatewayError('微信支付通知缺少验签头')
    try:
        if abs(int(time.time()) - int(timestamp)) > 300:
            raise PaymentGatewayError('微信支付通知时间戳已过期')
    except ValueError as exc:
        raise PaymentGatewayError('微信支付通知时间戳无效') from exc
    message = f'{timestamp}\n{nonce}\n{raw_body.decode("utf-8")}\n'
    if not _rsa_verify(message, signature, str(config.get('platform_public_key') or '')):
        raise PaymentGatewayError('微信支付通知验签失败')
    try:
        envelope = json.loads(raw_body.decode('utf-8'))
        resource = envelope['resource']
        ciphertext = base64.b64decode(resource['ciphertext'])
        associated = str(resource.get('associated_data') or '').encode('utf-8')
        nonce_bytes = str(resource['nonce']).encode('utf-8')
        key = str(config.get('api_v3_key') or '').encode('utf-8')
        if len(key) != 32:
            raise PaymentGatewayError('微信支付 API v3 密钥长度必须为 32 字节')
        plaintext = AESGCM(key).decrypt(nonce_bytes, ciphertext, associated)
        result = json.loads(plaintext.decode('utf-8'))
    except PaymentGatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PaymentGatewayError('微信支付通知解密失败') from exc
    if not isinstance(result, dict):
        raise PaymentGatewayError('微信支付通知内容无效')
    return result


def _wechat_webhook_result(channel, raw_body: bytes, headers: Any) -> GatewayResult:
    data = _wechat_verify_and_decrypt(channel, raw_body, headers)
    state = str(data.get('trade_state') or '').upper()
    return GatewayResult(
        transaction_no=str(data.get('out_trade_no') or ''),
        provider_order_id=str(data.get('out_trade_no') or ''),
        provider_payment_id=str(data.get('transaction_id') or ''),
        paid_amount=_decimal_from_minor((data.get('amount') or {}).get('total'), str((data.get('amount') or {}).get('currency') or '')),
        paid_currency=str((data.get('amount') or {}).get('currency') or '').upper(),
        succeeded=state == 'SUCCESS',
        failed=state in {'CLOSED', 'PAYERROR', 'REFUND'},
        processing=state in {'NOTPAY', 'USERPAYING', ''},
        failure_reason=str(data.get('trade_state_desc') or '') if state not in {'SUCCESS', 'NOTPAY', 'USERPAYING', ''} else '',
        response=_safe_response(data, 'out_trade_no', 'transaction_id', 'trade_state', 'trade_state_desc'),
    )


# ── Airwallex ──────────────────────────────────────────────────

def _airwallex_base_url(sandbox: bool) -> str:
    return 'https://api-demo.airwallex.com' if sandbox else 'https://api.airwallex.com'


def _airwallex_token(channel, config: dict[str, Any]) -> str:
    response = requests.post(
        f'{_airwallex_base_url(channel.sandbox)}/api/v1/authentication/login',
        headers={
            'x-client-id': str(config.get('client_id') or ''),
            'x-api-key': str(config.get('api_key') or ''),
            'Content-Type': 'application/json',
        },
        json={},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '获取 Airwallex access token')
    token = payload.get('token')
    if not token:
        raise PaymentGatewayError('获取 Airwallex access token 失败')
    return str(token)


def _airwallex_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    config = _config(channel)
    if not config.get('client_id') or not config.get('api_key'):
        raise PaymentGatewayError('Airwallex 缺少 Client ID 或 API Key')
    token = _airwallex_token(channel, config)
    path = '/api/v1/pa/payment_intents/create'
    payload = {
        'request_id': transaction.transaction_no,
        'amount': float(transaction.amount),
        'currency': transaction.currency.upper(),
        'merchant_order_id': transaction.transaction_no,
        'descriptor': f'账单 {transaction.billing.bill_no}',
        'return_url': return_url,
    }
    response = requests.post(
        f'{_airwallex_base_url(channel.sandbox)}{path}',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    created = _response_json(response, '创建 Airwallex PaymentIntent')
    intent_id = created.get('id')
    client_secret = created.get('client_secret')
    if not intent_id or not client_secret:
        raise PaymentGatewayError('创建 Airwallex PaymentIntent 失败')
    return {
        'mode': 'sdk',
        'sdk': 'airwallex',
        'provider_order_id': str(intent_id),
        'sdk_payload': {
            'env': 'demo' if channel.sandbox else 'prod',
            'client_id': str(config.get('client_id') or ''),
            'intent_id': str(intent_id),
            'client_secret': str(client_secret),
            'currency': transaction.currency.upper(),
            'country_code': str(config.get('country_code') or '').upper(),
            'success_url': return_url,
        },
        'response': _safe_response(created, 'id', 'status', 'merchant_order_id'),
    }


def _airwallex_verify_webhook(channel, raw_body: bytes, headers: Any) -> dict[str, Any]:
    config = _config(channel)
    secret = str(config.get('webhook_secret') or '')
    timestamp = _header(headers, 'x-timestamp')
    signature = _header(headers, 'x-signature')
    if not secret or not timestamp or not signature:
        raise PaymentGatewayError('Airwallex webhook 缺少验签配置或签名')
    try:
        if abs(int(time.time() * 1000) - int(timestamp)) > 300_000:
            raise PaymentGatewayError('Airwallex webhook 时间戳已过期')
    except ValueError as exc:
        raise PaymentGatewayError('Airwallex webhook 时间戳无效') from exc
    expected = hmac.new(
        secret.encode('utf-8'),
        timestamp.encode('utf-8') + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise PaymentGatewayError('Airwallex webhook 验签失败')
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaymentGatewayError('Airwallex webhook 内容无效') from exc
    if not isinstance(payload, dict):
        raise PaymentGatewayError('Airwallex webhook 内容无效')
    return payload


def _airwallex_result(payload: dict[str, Any]) -> GatewayResult:
    event = str(payload.get('name') or payload.get('type') or '')
    obj = ((payload.get('data') or {}).get('object') or payload.get('data') or {})
    if not isinstance(obj, dict):
        obj = {}
    state = str(obj.get('status') or '').upper()
    return GatewayResult(
        transaction_no=str(obj.get('merchant_order_id') or ''),
        provider_order_id=str(obj.get('id') or obj.get('payment_intent_id') or ''),
        provider_payment_id=str(obj.get('latest_payment_attempt') or ''),
        paid_amount=Decimal(str(obj.get('amount'))) if obj.get('amount') is not None else None,
        paid_currency=str(obj.get('currency') or '').upper(),
        succeeded=event == 'payment_intent.succeeded' or state == 'SUCCEEDED',
        failed=event in {'payment_intent.failed', 'payment_intent.cancelled'} or state in {'FAILED', 'CANCELLED'},
        processing=state in {'PENDING', 'PENDING_REVIEW', 'REQUIRES_CUSTOMER_ACTION', 'REQUIRES_PAYMENT_METHOD', ''},
        failure_reason=str(obj.get('failure_reason') or ''),
        response=_safe_response(obj, 'id', 'status', 'merchant_order_id', 'failure_reason'),
    )


def _airwallex_webhook_result(channel, raw_body: bytes, headers: Any) -> GatewayResult:
    return _airwallex_result(_airwallex_verify_webhook(channel, raw_body, headers))


def _airwallex_return_result(transaction, channel, data: dict[str, str]) -> GatewayResult:
    intent_id = data.get('intent_id') or transaction.provider_order_id or ''
    if not intent_id:
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    config = _config(channel)
    token = _airwallex_token(channel, config)
    response = requests.get(
        f'{_airwallex_base_url(channel.sandbox)}/api/v1/pa/payment_intents/{intent_id}',
        headers={'Authorization': f'Bearer {token}'},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '查询 Airwallex 支付状态')
    result = _airwallex_result({'data': {'object': payload}})
    if result.transaction_no and result.transaction_no != transaction.transaction_no:
        raise PaymentGatewayError('Airwallex 支付交易与本地交易不匹配')
    result.transaction_no = transaction.transaction_no
    return result


# ── 银联在线支付 ───────────────────────────────────────────────

def _unionpay_base_url(sandbox: bool) -> str:
    return (
        'https://gateway.test.95516.com/gateway/api/frontTransReq.do'
        if sandbox else 'https://gateway.95516.com/gateway/api/frontTransReq.do'
    )


def _unionpay_signing_text(data: dict[str, str]) -> str:
    return '&'.join(
        f'{key}={data[key]}'
        for key in sorted(data)
        if key != 'signature' and data[key] not in (None, '')
    )


def _unionpay_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    if transaction.currency.upper() != 'CNY':
        raise PaymentGatewayError('银联在线支付仅支持 CNY 账单')
    config = _config(channel)
    required = ('mer_id', 'cert_id', 'merchant_private_key')
    if any(not str(config.get(key) or '').strip() for key in required):
        raise PaymentGatewayError('银联在线支付渠道配置不完整')
    fields = {
        'version': '5.1.0',
        'encoding': 'utf-8',
        'signMethod': '01',
        'txnType': '01',
        'txnSubType': '01',
        'bizType': '000201',
        'channelType': '07',
        'accessType': '0',
        'merId': str(config['mer_id']),
        'orderId': transaction.transaction_no,
        'txnTime': timezone.localtime().strftime('%Y%m%d%H%M%S'),
        'txnAmt': str(_minor_amount(transaction.amount, transaction.currency)),
        'currencyCode': '156',
        'frontUrl': return_url,
        'backUrl': callback_url,
        'certId': str(config['cert_id']),
    }
    fields['signature'] = _rsa_sign(
        _unionpay_signing_text(fields),
        str(config['merchant_private_key']),
    )
    return {
        'mode': 'form',
        'form_action': _unionpay_base_url(channel.sandbox),
        'form_method': 'POST',
        'form_fields': fields,
        'provider_order_id': transaction.transaction_no,
        'response': {'orderId': transaction.transaction_no},
    }


def _unionpay_result(channel, data: dict[str, str]) -> GatewayResult:
    config = _config(channel)
    if not _rsa_verify(
        _unionpay_signing_text(data),
        data.get('signature', ''),
        str(config.get('unionpay_public_key') or ''),
    ):
        raise PaymentGatewayError('银联通知验签失败')
    resp_code = str(data.get('respCode') or '')
    return GatewayResult(
        transaction_no=str(data.get('orderId') or ''),
        provider_order_id=str(data.get('orderId') or ''),
        provider_payment_id=str(data.get('queryId') or ''),
        paid_amount=_decimal_from_minor(data.get('txnAmt'), 'CNY'),
        paid_currency='CNY',
        succeeded=resp_code == '00',
        failed=resp_code in {'03', '04', '05', '34'},
        processing=resp_code in {'', '01', '02'},
        failure_reason=str(data.get('respMsg') or '') if resp_code != '00' else '',
        response={
            key: data.get(key, '')
            for key in ('orderId', 'queryId', 'respCode', 'respMsg', 'txnAmt', 'txnTime')
        },
    )


def _unionpay_return_result(transaction, channel, data: dict[str, str]) -> GatewayResult:
    result = _unionpay_result(channel, data)
    if result.transaction_no != transaction.transaction_no:
        raise PaymentGatewayError('银联回跳交易与本地交易不匹配')
    return result


def _paypal_query(transaction, channel) -> GatewayResult:
    order_id = str(transaction.provider_order_id or '')
    if not order_id:
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    config = _config(channel)
    token = _paypal_token(channel, config)
    response = requests.get(
        f'{_paypal_base_url(channel.sandbox)}/v2/checkout/orders/{order_id}',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '查询 PayPal 订单')
    order_status = str(payload.get('status') or '').upper()
    ref = str(((payload.get('purchase_units') or [{}])[0].get('reference_id') or ''))
    if ref and ref != transaction.transaction_no:
        raise PaymentGatewayError('PayPal 订单与本地交易不匹配')
    if order_status == 'APPROVED':
        return _paypal_capture(transaction, channel, order_id)
    capture = (
        (((payload.get('purchase_units') or [{}])[0].get('payments') or {})
         .get('captures') or [{}])[0]
    )
    capture_status = str(capture.get('status') or '').upper()
    return GatewayResult(
        transaction_no=transaction.transaction_no,
        provider_order_id=str(payload.get('id') or order_id),
        provider_payment_id=str(capture.get('id') or ''),
        paid_amount=Decimal(str((capture.get('amount') or {}).get('value')))
        if (capture.get('amount') or {}).get('value') is not None else None,
        paid_currency=str((capture.get('amount') or {}).get('currency_code') or '').upper(),
        succeeded=order_status == 'COMPLETED' or capture_status == 'COMPLETED',
        failed=order_status in {'VOIDED', 'FAILED'} or capture_status in {'DECLINED', 'FAILED'},
        processing=order_status in {'CREATED', 'SAVED', 'PAYER_ACTION_REQUIRED', ''},
        failure_reason=(
            f'PayPal 订单状态为 {order_status or capture_status}'
            if order_status in {'VOIDED', 'FAILED'} or capture_status in {'DECLINED', 'FAILED'}
            else ''
        ),
        response=_safe_response(payload, 'id', 'status'),
    )


def _alipay_query(transaction, channel) -> GatewayResult:
    config = _config(channel)
    now = timezone.localtime().strftime('%Y-%m-%d %H:%M:%S')
    params = {
        'app_id': str(config.get('app_id') or ''),
        'method': 'alipay.trade.query',
        'format': 'JSON',
        'charset': 'utf-8',
        'sign_type': 'RSA2',
        'timestamp': now,
        'version': '1.0',
        'biz_content': json.dumps(
            {'out_trade_no': transaction.transaction_no},
            ensure_ascii=False, separators=(',', ':'),
        ),
    }
    if not params['app_id']:
        raise PaymentGatewayError('支付宝缺少 App ID')
    params['sign'] = _rsa_sign(_alipay_signing_text(params), str(config.get('app_private_key') or ''))
    response = requests.get(
        _alipay_base_url(channel.sandbox),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '查询支付宝交易')
    data = payload.get('alipay_trade_query_response')
    if not isinstance(data, dict):
        raise PaymentGatewayError('支付宝查单返回格式无效')
    code = str(data.get('code') or '')
    if code == '40004':
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    if code != '10000':
        raise PaymentGatewayError(f'支付宝查单失败：{data.get("sub_msg") or data.get("msg") or code}')
    trade_status = str(data.get('trade_status') or '').upper()
    out_trade_no = str(data.get('out_trade_no') or '')
    if out_trade_no and out_trade_no != transaction.transaction_no:
        raise PaymentGatewayError('支付宝查单交易与本地交易不匹配')
    return GatewayResult(
        transaction_no=transaction.transaction_no,
        provider_order_id=out_trade_no or transaction.transaction_no,
        provider_payment_id=str(data.get('trade_no') or ''),
        paid_amount=Decimal(str(data.get('total_amount'))) if data.get('total_amount') else None,
        paid_currency='CNY',
        succeeded=trade_status in {'TRADE_SUCCESS', 'TRADE_FINISHED'},
        failed=trade_status in {'TRADE_CLOSED'},
        processing=trade_status in {'WAIT_BUYER_PAY', ''},
        failure_reason='支付宝交易已关闭' if trade_status == 'TRADE_CLOSED' else '',
        response=_safe_response(data, 'trade_status', 'out_trade_no', 'trade_no', 'total_amount'),
    )


def _wechat_query(transaction, channel) -> GatewayResult:
    config = _config(channel)
    mch_id = str(config.get('mch_id') or '')
    if not mch_id:
        raise PaymentGatewayError('微信支付缺少商户号')
    out_trade_no = transaction.transaction_no
    path = f'/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={mch_id}'
    response = requests.get(
        f'https://api.mch.weixin.qq.com{path}',
        headers={
            'Authorization': _wechat_authorization(config, 'GET', path, ''),
            'Accept': 'application/json',
        },
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 404:
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    data = _response_json(response, '查询微信支付订单')
    state = str(data.get('trade_state') or '').upper()
    out_no = str(data.get('out_trade_no') or '')
    if out_no and out_no != transaction.transaction_no:
        raise PaymentGatewayError('微信支付查单交易与本地交易不匹配')
    return GatewayResult(
        transaction_no=transaction.transaction_no,
        provider_order_id=out_no or transaction.transaction_no,
        provider_payment_id=str(data.get('transaction_id') or ''),
        paid_amount=_decimal_from_minor(
            (data.get('amount') or {}).get('total'),
            str((data.get('amount') or {}).get('currency') or ''),
        ),
        paid_currency=str((data.get('amount') or {}).get('currency') or '').upper(),
        succeeded=state == 'SUCCESS',
        failed=state in {'CLOSED', 'PAYERROR', 'REVOKED'},
        processing=state in {'NOTPAY', 'USERPAYING', ''},
        failure_reason=str(data.get('trade_state_desc') or '') if state not in {'SUCCESS', 'NOTPAY', 'USERPAYING', ''} else '',
        response=_safe_response(data, 'out_trade_no', 'transaction_id', 'trade_state', 'trade_state_desc'),
    )


def _unionpay_query_url(sandbox: bool) -> str:
    return (
        'https://gateway.test.95516.com/gateway/api/queryTrans.do'
        if sandbox else 'https://gateway.95516.com/gateway/api/queryTrans.do'
    )


def _unionpay_query(transaction, channel) -> GatewayResult:
    config = _config(channel)
    payload = transaction.checkout_payload if isinstance(transaction.checkout_payload, dict) else {}
    fields = payload.get('form_fields') if isinstance(payload.get('form_fields'), dict) else {}
    txn_time = str(fields.get('txnTime') or payload.get('txnTime') or '')
    if not txn_time:
        return GatewayResult(
            transaction_no=transaction.transaction_no,
            processing=True,
            failure_reason='缺少银联原交易时间，无法查单',
        )
    query = {
        'version': '5.1.0',
        'encoding': 'utf-8',
        'signMethod': '01',
        'txnType': '00',
        'txnSubType': '00',
        'bizType': '000000',
        'accessType': '0',
        'merId': str(config.get('mer_id') or ''),
        'orderId': transaction.transaction_no,
        'txnTime': txn_time,
        'certId': str(config.get('cert_id') or ''),
    }
    query['signature'] = _rsa_sign(
        _unionpay_signing_text(query),
        str(config.get('merchant_private_key') or ''),
    )
    response = requests.post(
        _unionpay_query_url(channel.sandbox),
        data=query,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code >= 400:
        raise PaymentGatewayError(f'查询银联交易失败：HTTP {response.status_code}')
    from urllib.parse import parse_qsl
    data = {key: value for key, value in parse_qsl(response.text, keep_blank_values=True)}
    if not data:
        raise PaymentGatewayError('银联查单返回格式无效')
    result = _unionpay_result(channel, data)
    if result.transaction_no and result.transaction_no != transaction.transaction_no:
        raise PaymentGatewayError('银联查单交易与本地交易不匹配')
    result.transaction_no = transaction.transaction_no
    return result


# ── 原路退款 ───────────────────────────────────────────────────

class RefundNotSupported(PaymentGatewayError):
    """当前渠道未实现自动退款，应转人工处理。"""


def _stripe_refund(transaction, channel, *, amount: Decimal, idempotency_key: str) -> dict[str, Any]:
    config = _config(channel)
    secret_key = str(config.get('secret_key') or '')
    payment_intent = str(transaction.provider_payment_id or '').strip()
    if not secret_key:
        raise PaymentGatewayError('Stripe 缺少 Secret key')
    if not payment_intent:
        raise PaymentGatewayError('Stripe 交易缺少 payment_intent，无法原路退')
    data = {
        'payment_intent': payment_intent,
        'amount': str(_minor_amount(amount, transaction.currency)),
        'metadata[transaction_no]': transaction.transaction_no,
    }
    response = requests.post(
        f'{_stripe_base_url(channel.sandbox)}/v1/refunds',
        auth=(secret_key, ''),
        data=data,
        headers={'Idempotency-Key': idempotency_key[:255]},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _response_json(response, '创建 Stripe 退款')
    refund_id = payload.get('id')
    status = str(payload.get('status') or '').lower()
    if not refund_id or status not in {'succeeded', 'pending', 'requires_action'}:
        raise PaymentGatewayError(f'创建 Stripe 退款失败：{payload.get("message") or status or "未知错误"}')
    return {
        'ok': True,
        'provider_refund_id': str(refund_id),
        'response': _safe_response(payload, 'id', 'status', 'amount', 'currency', 'payment_intent'),
    }


def _paypal_refund(transaction, channel, *, amount: Decimal, idempotency_key: str) -> dict[str, Any]:
    capture_id = str(transaction.provider_payment_id or '').strip()
    if not capture_id:
        raise PaymentGatewayError('PayPal 交易缺少 capture id，无法原路退')
    config = _config(channel)
    token = _paypal_token(channel, config)
    payload = {
        'amount': {
            'currency_code': transaction.currency.upper(),
            'value': _format_amount(amount, transaction.currency),
        },
    }
    response = requests.post(
        f'{_paypal_base_url(channel.sandbox)}/v2/payments/captures/{capture_id}/refund',
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'PayPal-Request-Id': _paypal_request_id(transaction.transaction_no, 'refund'),
            'Prefer': 'return=representation',
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    created = _response_json(response, '创建 PayPal 退款')
    refund_id = created.get('id')
    status = str(created.get('status') or '').upper()
    if not refund_id or status not in {'COMPLETED', 'PENDING'}:
        raise PaymentGatewayError(f'创建 PayPal 退款失败：{status or "未知错误"}')
    return {
        'ok': True,
        'provider_refund_id': str(refund_id),
        'response': _safe_response(created, 'id', 'status', 'create_time'),
    }


def refund_payment(transaction, channel, *, amount=None, idempotency_key: str) -> dict[str, Any]:
    """原路退回一笔已成功的在线支付。未实现的渠道抛 ``RefundNotSupported``。"""
    refund_amount = Decimal(str(amount if amount is not None else transaction.amount))
    provider = (channel.provider if channel else transaction.provider or '').lower()
    if provider == 'stripe':
        return _stripe_refund(transaction, channel, amount=refund_amount, idempotency_key=idempotency_key)
    if provider == 'paypal':
        return _paypal_refund(transaction, channel, amount=refund_amount, idempotency_key=idempotency_key)
    raise RefundNotSupported(f'渠道 {provider or "未知"} 暂不支持自动原路退，请人工退款')


# ── 对外统一入口 ───────────────────────────────────────────────

def create_checkout(transaction, channel, callback_url: str, return_url: str) -> dict[str, Any]:
    """创建服务商订单，返回不包含持久化凭据的前端支付指令。"""
    provider = channel.provider
    if provider == 'stripe':
        return _stripe_checkout(transaction, channel, callback_url, return_url)
    if provider == 'paypal':
        return _paypal_checkout(transaction, channel, callback_url, return_url)
    if provider == 'alipay':
        return _alipay_checkout(transaction, channel, callback_url, return_url)
    if provider == 'wechat':
        return _wechat_checkout(transaction, channel, callback_url, return_url)
    if provider == 'airwallex':
        return _airwallex_checkout(transaction, channel, callback_url, return_url)
    if provider == 'unionpay':
        return _unionpay_checkout(transaction, channel, callback_url, return_url)
    raise PaymentGatewayError(f'尚未实现支付渠道：{provider}')


def process_webhook(channel, raw_body: bytes, headers: Any) -> GatewayResult:
    """验签并解析异步回调。任何验签失败均抛错，调用方不得修改账单。"""
    provider = channel.provider
    if provider == 'stripe':
        return _stripe_webhook_result(channel, raw_body, headers)
    if provider == 'paypal':
        return _paypal_webhook_result(channel, raw_body, headers)
    if provider == 'alipay':
        return _alipay_result(channel, _callback_data(json.loads(raw_body.decode('utf-8'))))
    if provider == 'wechat':
        return _wechat_webhook_result(channel, raw_body, headers)
    if provider == 'airwallex':
        return _airwallex_webhook_result(channel, raw_body, headers)
    if provider == 'unionpay':
        return _unionpay_result(channel, _callback_data(json.loads(raw_body.decode('utf-8'))))
    raise PaymentGatewayError(f'尚未实现支付渠道：{provider}')


def process_form_webhook(channel, data: Any) -> GatewayResult:
    """处理 form-urlencoded 服务商通知（支付宝、银联）。"""
    payload = _callback_data(data)
    if channel.provider == 'alipay':
        return _alipay_result(channel, payload)
    if channel.provider == 'unionpay':
        return _unionpay_result(channel, payload)
    raise PaymentGatewayError('当前支付渠道不支持表单回调')


def query_payment(transaction, channel) -> GatewayResult:
    """主动向服务商查单。用于掉单对账，不信任浏览器回传。"""
    provider = (channel.provider if channel else transaction.provider or '').lower()
    if provider == 'stripe':
        session_id = str(transaction.provider_order_id or '')
        if not session_id:
            return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
        return _stripe_return_result(transaction, channel, {'session_id': session_id})
    if provider == 'paypal':
        return _paypal_query(transaction, channel)
    if provider == 'alipay':
        return _alipay_query(transaction, channel)
    if provider == 'wechat':
        return _wechat_query(transaction, channel)
    if provider == 'airwallex':
        return _airwallex_return_result(transaction, channel, {})
    if provider == 'unionpay':
        return _unionpay_query(transaction, channel)
    raise PaymentGatewayError(f'尚未实现支付渠道：{provider}')


def process_return(transaction, channel, data: Any) -> GatewayResult:
    """处理服务商同步回跳；最终状态仍以经过验证的服务商查询/回调为准。"""
    payload = _callback_data(data)
    provider = channel.provider
    if provider == 'stripe':
        return _stripe_return_result(transaction, channel, payload)
    if provider == 'paypal':
        return _paypal_return_result(transaction, channel, payload)
    if provider == 'alipay':
        return _alipay_return_result(transaction, channel, payload)
    if provider == 'wechat':
        return GatewayResult(transaction_no=transaction.transaction_no, processing=True)
    if provider == 'airwallex':
        return _airwallex_return_result(transaction, channel, payload)
    if provider == 'unionpay':
        return _unionpay_return_result(transaction, channel, payload)
    raise PaymentGatewayError(f'尚未实现支付渠道：{provider}')
