"""邮件通知：催缴 / 逾期 / 开通成功 / 工单回复给客户，运维抄送可选。

投递失败不影响主流程。总开关为 ``notify_email_enabled``（默认关）。
SMTP 优先读取系统参数（邮件服务器地址/端口/账号），未填写时回退环境变量。
"""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.core.mail import get_connection, send_mail

from .notification_templates import render_rule_message
from .services import get_setting

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _as_int(value, default: int) -> int:
    try:
        if value is None or value == '':
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def uses_ui_smtp() -> bool:
    return bool((get_setting('smtp_host', '') or '').strip())


def smtp_config() -> dict:
    host = (get_setting('smtp_host', '') or '').strip()
    env_port = getattr(settings, 'EMAIL_PORT', 25)
    port = _as_int(get_setting('smtp_port', None), 0)
    if host and port <= 0:
        port = 465
    elif port <= 0:
        port = _as_int(env_port, 25)
    user = (get_setting('smtp_user', '') or '').strip() or getattr(settings, 'EMAIL_HOST_USER', '')
    password = (get_setting('smtp_password', '') or '').strip() or getattr(settings, 'EMAIL_HOST_PASSWORD', '')
    if host:
        use_ssl = _as_bool(get_setting('smtp_use_ssl', True), True)
        use_tls = _as_bool(get_setting('smtp_use_tls', False), False)
    else:
        use_ssl = bool(getattr(settings, 'EMAIL_USE_SSL', False))
        use_tls = bool(getattr(settings, 'EMAIL_USE_TLS', False))
    if use_ssl and use_tls:
        use_tls = False
    return {
        'host': host or getattr(settings, 'EMAIL_HOST', 'localhost'),
        'port': port,
        'username': user,
        'password': password,
        'use_tls': use_tls,
        'use_ssl': use_ssl,
        'timeout': getattr(settings, 'EMAIL_TIMEOUT', 15),
        'from_ui': bool(host),
    }


def mail_connection():
    """参数页填写了邮件服务器时走 SMTP；否则使用 Django EMAIL_BACKEND（测试 locmem / 开发 console）。"""
    if uses_ui_smtp():
        cfg = smtp_config()
        return get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=cfg['host'],
            port=cfg['port'],
            username=cfg['username'],
            password=cfg['password'],
            use_tls=cfg['use_tls'],
            use_ssl=cfg['use_ssl'],
            timeout=cfg['timeout'],
        )
    return get_connection()

# 发给客户的事件；值为 None 表示该事件全部动作，否则仅列出的 action
CUSTOMER_EMAIL_ACTIONS = {
    'service_expiry': None,
    'billing_overdue': None,
    'service_provision': {'success'},
    'service_ticket': {'create', 'reply', 'resolve', 'close'},
}


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def email_enabled() -> bool:
    return _as_bool(get_setting('notify_email_enabled', False), False)


def email_from() -> str:
    configured = (get_setting('notify_email_from', '') or '').strip()
    if configured:
        return configured
    support = (get_setting('support_email', '') or '').strip()
    if support and _EMAIL_RE.match(support):
        return support
    return getattr(settings, 'DEFAULT_FROM_EMAIL', 'webmaster@localhost')


def parse_emails(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r'[,;\s]+', str(raw))
    seen = set()
    result = []
    for part in parts:
        addr = part.strip()
        if not addr or not _EMAIL_RE.match(addr) or addr in seen:
            continue
        seen.add(addr)
        result.append(addr)
    return result


def ops_recipients() -> list[str]:
    emails = parse_emails(get_setting('notify_email_recipients', ''))
    if emails:
        return emails
    return parse_emails(get_setting('support_email', ''))


def customer_recipients(context: dict | None) -> list[str]:
    context = context or {}
    emails = parse_emails(context.get('customer_email'))
    if emails:
        return emails
    customer_no = (context.get('customer_no') or '').strip()
    if not customer_no:
        return []
    try:
        from customer_manager.models import Customer
        customer = (
            Customer.objects.filter(customer_no=customer_no)
            .select_related('account')
            .first()
        )
    except Exception:  # noqa: BLE001
        return []
    if customer is None:
        return []
    found = parse_emails(getattr(customer, 'contact_email', ''))
    account = getattr(customer, 'account', None)
    found.extend(parse_emails(getattr(account, 'email', '') if account else ''))
    return parse_emails(found)


def should_email_customer(event: str, context: dict | None) -> bool:
    allowed = CUSTOMER_EMAIL_ACTIONS.get(event)
    if event not in CUSTOMER_EMAIL_ACTIONS:
        return False
    if allowed is None:
        return True
    action = str((context or {}).get('action') or '')
    return action in allowed


def send_notification_email(*, subject: str, body: str, recipients, fail_silently: bool = True) -> dict:
    to = parse_emails(recipients)
    if not to:
        return {'sent': False, 'recipients': [], 'reason': 'no_recipients'}
    try:
        send_mail(
            subject=subject[:256] or 'ISP Manager 通知',
            message=body or subject,
            from_email=email_from(),
            recipient_list=to,
            fail_silently=fail_silently,
            connection=mail_connection(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('邮件发送失败 recipients=%s: %s', to, exc, exc_info=True)
        return {'sent': False, 'recipients': to, 'reason': str(exc)}
    return {'sent': True, 'recipients': to, 'reason': ''}


def dispatch_email(
    event: str,
    title: str = '',
    body: str = '',
    *,
    context: dict | None = None,
) -> dict:
    """按开关把同一条业务通知发邮件：运维抄送 + 客户面向事件。"""
    if not email_enabled():
        return {'event': event, 'disabled': True, 'ops': None, 'customer': None}

    rendered_title, text, normalized = render_rule_message(
        event, title=title, content=body, context=context,
    )
    ops = send_notification_email(
        subject=rendered_title,
        body=text,
        recipients=ops_recipients(),
    ) if ops_recipients() else {'sent': False, 'recipients': [], 'reason': 'no_ops_recipients'}

    customer = None
    if should_email_customer(event, normalized):
        customer = send_notification_email(
            subject=rendered_title,
            body=text,
            recipients=customer_recipients(normalized),
        )
    return {
        'event': event,
        'disabled': False,
        'ops': ops,
        'customer': customer,
    }


def send_test_email(to: str = '') -> dict:
    """管理端探测 SMTP；不要求先打开 notify_email_enabled。"""
    recipients = parse_emails(to) or ops_recipients()
    if not recipients:
        return {
            'ok': False,
            'detail': '请填写收件人或配置 notify_email_recipients / support_email',
            'recipients': [],
        }
    result = send_notification_email(
        subject='【ISP Manager】邮件通道测试',
        body=(
            '这是一封测试邮件。若你能看到本内容，说明 SMTP 已接通。\n'
            '客户催缴 / 逾期 / 开通成功 / 工单回复会在「启用邮件通知」打开后自动发送。'
        ),
        recipients=recipients,
        fail_silently=False,
    )
    cfg = smtp_config()
    backend = (
        'django.core.mail.backends.smtp.EmailBackend'
        if uses_ui_smtp()
        else getattr(settings, 'EMAIL_BACKEND', '')
    )
    return {
        'ok': bool(result.get('sent')),
        'detail': result.get('reason') or '已投递',
        'recipients': result.get('recipients') or [],
        'from_email': email_from(),
        'backend': backend,
        'smtp_host': cfg['host'] if uses_ui_smtp() else '',
        'smtp_port': cfg['port'] if uses_ui_smtp() else None,
    }
