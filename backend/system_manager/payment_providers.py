"""在线支付渠道的公共元数据与配置校验。

支付密钥不应作为 ``SystemSetting`` 的普通文本保存；本模块仅描述各渠道需要的
配置字段，实际密文存储和网关调用由相应服务处理。
"""
from __future__ import annotations

from typing import Any


PAYMENT_PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    'stripe': {
        'label': 'Stripe',
        'description': 'Stripe Checkout 托管收银台。',
        'checkout_mode': 'redirect',
        'callback_usage': '把下方回调 URL 填到 Stripe Dashboard 的 Webhook，并订阅 checkout.session.completed。支付成功后 Stripe 会通知该地址，系统验签入账并自动交付。',
        'fields': [
            {'key': 'publishable_key', 'label': 'Publishable key', 'secret': False},
            {'key': 'secret_key', 'label': 'Secret key', 'secret': True, 'required': True},
            {'key': 'webhook_secret', 'label': 'Webhook signing secret', 'secret': True, 'required': True},
        ],
    },
    'paypal': {
        'label': 'PayPal',
        'description': 'PayPal Orders v2 托管付款页。',
        'checkout_mode': 'redirect',
        'callback_usage': '把下方回调 URL 填到 PayPal 应用的 Webhook。支付成功后 PayPal 通知该地址，系统验签入账并自动交付。',
        'recommended_events': [
            'PAYMENT.CAPTURE.COMPLETED',
            'PAYMENT.CAPTURE.PENDING',
            'PAYMENT.CAPTURE.DENIED',
            'PAYMENT.CAPTURE.DECLINED',
            'PAYMENT.CAPTURE.REVERSED',
            'CHECKOUT.ORDER.APPROVED',
        ],
        'documentation_url': (
            'https://developer.paypal.com/api/rest/webhooks/'
        ),
        'fields': [
            {'key': 'client_id', 'label': 'Client ID', 'secret': False, 'required': True},
            {'key': 'client_secret', 'label': 'Client Secret', 'secret': True, 'required': True},
            {'key': 'webhook_id', 'label': 'Webhook ID', 'secret': False, 'required': True},
        ],
    },
    'alipay': {
        'label': '支付宝',
        'description': '支付宝电脑网站支付（RSA2）。',
        'checkout_mode': 'redirect',
        'callback_usage': '下单时系统会把下方回调 URL 作为 notify_url 提交给支付宝。支付成功后支付宝异步通知该地址，系统验签入账并自动交付。',
        'fields': [
            {'key': 'app_id', 'label': 'App ID', 'secret': False, 'required': True},
            {'key': 'app_private_key', 'label': '应用私钥（PEM）', 'secret': True, 'required': True, 'multiline': True},
            {'key': 'alipay_public_key', 'label': '支付宝公钥（PEM）', 'secret': False, 'required': True, 'multiline': True},
        ],
    },
    'wechat': {
        'label': '微信支付',
        'description': '微信支付 API v3 Native 扫码支付。',
        'checkout_mode': 'qr',
        'callback_usage': '下单时系统会把下方回调 URL 作为 notify_url 提交给微信支付。支付成功后微信通知该地址，系统验签入账并自动交付。',
        'fields': [
            {'key': 'app_id', 'label': 'AppID', 'secret': False, 'required': True},
            {'key': 'mch_id', 'label': '商户号（MchID）', 'secret': False, 'required': True},
            {'key': 'merchant_serial_no', 'label': '商户证书序列号', 'secret': False, 'required': True},
            {'key': 'merchant_private_key', 'label': '商户 API 私钥（PEM）', 'secret': True, 'required': True, 'multiline': True},
            {'key': 'api_v3_key', 'label': 'API v3 密钥', 'secret': True, 'required': True},
            {'key': 'platform_public_key', 'label': '微信支付平台公钥（PEM）', 'secret': False, 'required': True, 'multiline': True},
        ],
    },
    'airwallex': {
        'label': 'Airwallex',
        'description': 'Airwallex Hosted Payment Page。',
        'checkout_mode': 'sdk',
        'callback_usage': '把下方回调 URL 填到 Airwallex 的 Webhook。支付成功后 Airwallex 通知该地址，系统验签入账并自动交付。',
        'fields': [
            {'key': 'client_id', 'label': 'Client ID', 'secret': False, 'required': True},
            {'key': 'api_key', 'label': 'API Key', 'secret': True, 'required': True},
            {'key': 'webhook_secret', 'label': 'Webhook secret', 'secret': True, 'required': True},
            {'key': 'country_code', 'label': '商户国家/地区代码（ISO 3166-1）', 'secret': False, 'required': True},
        ],
    },
    'unionpay': {
        'label': '银联在线支付',
        'description': '银联全渠道前台消费交易（RSA）。',
        'checkout_mode': 'form',
        'callback_usage': '下单时系统会把下方回调 URL 作为 backUrl 提交给银联。支付成功后银联通知该地址，系统验签入账并自动交付。',
        'fields': [
            {'key': 'mer_id', 'label': '商户号（MerId）', 'secret': False, 'required': True},
            {'key': 'cert_id', 'label': '签名证书 ID（CertId）', 'secret': False, 'required': True},
            {'key': 'merchant_private_key', 'label': '商户签名私钥（PEM）', 'secret': True, 'required': True, 'multiline': True},
            {'key': 'unionpay_public_key', 'label': '银联验签公钥（PEM）', 'secret': False, 'required': True, 'multiline': True},
        ],
    },
}

PAYMENT_PROVIDER_CHOICES = tuple(
    (provider, spec['label'])
    for provider, spec in PAYMENT_PROVIDER_SPECS.items()
)


def get_provider_spec(provider: str) -> dict[str, Any]:
    try:
        return PAYMENT_PROVIDER_SPECS[provider]
    except KeyError as exc:
        raise ValueError(f'不支持的支付渠道：{provider}') from exc


def provider_fields(provider: str) -> list[dict[str, Any]]:
    return [dict(field) for field in get_provider_spec(provider)['fields']]


def secret_config_keys(provider: str) -> set[str]:
    return {field['key'] for field in provider_fields(provider) if field.get('secret')}


def public_config_view(provider: str, config: dict[str, Any]) -> dict[str, Any]:
    """构造可安全返回给管理端的配置视图，绝不回显密钥。"""
    view: dict[str, Any] = {}
    for field in provider_fields(provider):
        key = field['key']
        if field.get('secret'):
            view[f'{key}_configured'] = bool(config.get(key))
        else:
            view[key] = config.get(key, '')
    return view


def validate_provider_config(provider: str, config: dict[str, Any], *, enabled: bool) -> None:
    """启用通道时检查其必填配置；未启用的草稿可先不完整保存。"""
    get_provider_spec(provider)
    if not enabled:
        return
    missing = [
        field['label']
        for field in provider_fields(provider)
        if field.get('required') and not str(config.get(field['key']) or '').strip()
    ]
    if missing:
        raise ValueError(f'启用支付渠道前请填写：{"、".join(missing)}')
