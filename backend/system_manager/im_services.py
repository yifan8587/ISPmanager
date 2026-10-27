"""
system_manager/im_services.py
IM 通道发送实现与事件分发：Telegram / 企业微信 / 钉钉 / 自定义 Webhook。

对外主要接口：
  - test_channel(channel)                  发送测试消息并回写测试状态
  - send_to_channel(channel, title, body)  向单个通道（其全部接口人）发送
  - dispatch_event(event, title, body)     按通知规则把变更/异动推送到对应通道
  - notify(event, title, body)             dispatch_event 的安全包装（吞掉异常）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

import requests
from django.core.cache import cache
from django.utils import timezone

from .models import (
    IMChannel, IMChannelType, IMMessageLog, IMTestStatus, WeComAIBotMessage,
    NotificationRule,
)
from .notification_templates import render_rule_message

logger = logging.getLogger(__name__)

_TIMEOUT = 10


class IMSendError(Exception):
    pass


# ─────────────────────────────────────────────────────────────
# 各平台发送实现：返回 (success: bool, response_text: str)
# ─────────────────────────────────────────────────────────────

def _send_telegram(channel: IMChannel, text: str, target: str) -> tuple[bool, str]:
    if not channel.bot_token:
        return False, '未配置 Bot Token'
    url = f'https://api.telegram.org/bot{channel.bot_token}/sendMessage'
    try:
        resp = requests.post(url, json={'chat_id': target, 'text': text}, timeout=_TIMEOUT)
        ok = resp.status_code == 200 and resp.json().get('ok', False)
        return ok, resp.text[:1000]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _wecom_credentials(channel: IMChannel) -> tuple[str, str]:
    """返回 CorpID 与 Secret；普通自建应用优先使用通道保存的 Secret。"""
    if channel.channel_type == IMChannelType.WECOM:
        secret = channel.corp_secret
    else:
        secret = (
            os.environ.get(channel.wecom_secret_env, '').strip()
            if channel.wecom_secret_env else ''
        ) or channel.corp_secret
    return (channel.corp_id or '').strip(), (secret or '').strip()


def _wecom_access_token(channel: IMChannel, *, force_refresh: bool = False) -> tuple[str | None, str]:
    """获取并缓存企业微信应用 access_token，避免触发官方频率限制。"""
    corp_id, corp_secret = _wecom_credentials(channel)
    if not (corp_id and corp_secret):
        return None, '未配置 CorpID 或应用 Secret'
    cache_key = f'im:wecom:access-token:{channel.pk}'
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached:
            return cached, ''
    url = ('https://qyapi.weixin.qq.com/cgi-bin/gettoken'
           f'?corpid={corp_id}&corpsecret={corp_secret}')
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        data = resp.json()
    except Exception:  # noqa: BLE001
        logger.warning('企业微信 access_token 获取失败', exc_info=True)
        return None, '请求企业微信 access_token 接口失败'
    token = data.get('access_token') if data.get('errcode', 0) == 0 else None
    if not token:
        logger.warning(
            '企业微信 access_token 获取失败 channel=%s errcode=%s errmsg=%s',
            channel.pk, data.get('errcode'), data.get('errmsg'),
        )
        return None, (
            f'获取企业微信 access_token 失败：errcode={data.get("errcode", "unknown")}，'
            f'errmsg={data.get("errmsg", "未知错误")}'
        )
    if token:
        # 官方 token 通常有效两小时，提前五分钟刷新。
        cache.set(cache_key, token, timeout=max(int(data.get('expires_in', 7200)) - 300, 60))
    return token, ''


def _send_wecom(channel: IMChannel, text: str, target: str) -> tuple[bool, str]:
    """按企业微信自建应用 API 发送文本消息。"""
    corp_id, corp_secret = _wecom_credentials(channel)
    if not (corp_id and corp_secret and channel.agent_id):
        return False, '企业微信自建应用需完整配置 CorpID、AgentID 和可用的 Secret 环境变量'
    if not str(channel.agent_id).isdigit():
        return False, '企业微信 AgentID 必须为数字'
    if not target:
        return False, '企业微信自建应用需配置接收员工 UserID；如确需全员发送请显式填写 @all'

    def send(token):
        url = f'https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}'
        payload = {
            'touser': target,
            'toparty': '',
            'totag': '',
            'msgtype': 'text',
            'agentid': int(channel.agent_id),
            'text': {'content': text},
            'safe': 0,
        }
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data, resp.text[:1000]

    try:
        token, token_error = _wecom_access_token(channel)
        if not token:
            return False, token_error
        data, response_text = send(token)
        # 企业微信常见 token 失效码：40014 / 42001，仅刷新重试一次。
        if data.get('errcode') in (40014, 42001):
            token, token_error = _wecom_access_token(channel, force_refresh=True)
            if not token:
                return False, token_error or response_text
            data, response_text = send(token)
        return data.get('errcode') == 0, response_text
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _is_wecom_group_webhook(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme == 'https'
        and parsed.hostname == 'qyapi.weixin.qq.com'
        and parsed.path.rstrip('/') == '/cgi-bin/webhook/send'
        and bool(urllib.parse.parse_qs(parsed.query).get('key'))
    )


def _send_wecom_webhook(channel: IMChannel, text: str) -> tuple[bool, str]:
    """按企业微信群机器人 Webhook 协议发送文本消息。"""
    if not channel.webhook_url:
        return False, '未配置企业微信群机器人 Webhook 地址'
    if not _is_wecom_group_webhook(channel.webhook_url):
        return False, (
            '企业微信群机器人地址格式无效，应为 '
            'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...'
        )
    payload = {
        'msgtype': 'text',
        'text': {'content': text},
    }
    try:
        resp = requests.post(channel.webhook_url, json=payload, timeout=_TIMEOUT)
        try:
            data = resp.json()
        except ValueError:
            return False, f'企业微信返回非 JSON 响应：{resp.text[:800]}'
        ok = 200 <= resp.status_code < 300 and data.get('errcode') == 0
        return ok, resp.text[:1000]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _send_wecom_kf(channel: IMChannel, text: str, target: str) -> tuple[bool, str]:
    """
    向企业微信客服会话发送消息。

    target 必须是客服回调事件提供的 external_userid；客服链接中的 kfid 仅用于
    open_kfid，不能替代客户身份。企业微信还会限制客户主动发言后的可回复窗口。
    """
    corp_id, corp_secret = _wecom_credentials(channel)
    if not (corp_id and corp_secret and channel.wecom_kf_id):
        return False, '未完整配置 CorpID、应用 Secret 或客服账号 OpenKfId'
    if not target:
        return False, '企业微信客服发送需要客户 external_userid 作为接收目标'

    def send(token):
        url = f'https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}'
        payload = {
            'touser': target,
            'open_kfid': channel.wecom_kf_id,
            'msgtype': 'text',
            'text': {'content': text},
        }
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data, resp.text[:1000]

    try:
        token, token_error = _wecom_access_token(channel)
        if not token:
            return False, token_error
        data, response_text = send(token)
        # token 失效时只刷新并重试一次。
        if data.get('errcode') in (40014, 42001):
            token, token_error = _wecom_access_token(channel, force_refresh=True)
            if not token:
                return False, token_error or response_text
            data, response_text = send(token)
        return data.get('errcode') == 0, response_text
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _queue_wecom_aibot(channel: IMChannel, title: str, body: str, target: str,
                       event: str) -> tuple[bool, str]:
    """
    将智能机器人消息写入投递队列。

    企业微信智能机器人只能经已建立的 WebSocket 长连接发送；HTTP 请求中不能临时
    建连。实际投递、重试和发送日志由 run_wecom_aibot 命令处理。
    """
    if not (channel.wecom_aibot_id and channel.wecom_aibot_secret):
        return False, '未配置智能机器人 BotID 或长连接 Secret'
    if not target:
        return False, '企业微信智能机器人需配置已与机器人会话过的接收成员 UserID'
    queued = WeComAIBotMessage.objects.create(
        channel=channel, target=target, title=title, content=body, event=event,
    )
    return True, f'已加入智能机器人发送队列（消息 #{queued.pk}），等待长连接服务投递'


def _send_dingtalk(channel: IMChannel, text: str, target: str) -> tuple[bool, str]:
    if not channel.webhook_url:
        return False, '未配置 Webhook 地址'
    url = channel.webhook_url
    # 加签
    if channel.secret:
        ts = str(round(time.time() * 1000))
        string_to_sign = f'{ts}\n{channel.secret}'
        hmac_code = hmac.new(channel.secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        sep = '&' if '?' in url else '?'
        url = f'{url}{sep}timestamp={ts}&sign={sign}'
    at_mobiles = [target] if target else []
    payload = {
        'msgtype': 'text',
        'text': {'content': text},
        'at': {'atMobiles': at_mobiles, 'isAtAll': False},
    }
    try:
        resp = requests.post(url, json=payload, timeout=_TIMEOUT)
        ok = resp.json().get('errcode') == 0
        return ok, resp.text[:1000]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _send_webhook(channel: IMChannel, title: str, text: str, target: str) -> tuple[bool, str]:
    if not channel.webhook_url:
        return False, '未配置 Webhook 地址'
    # 兼容修复前误配置为“自定义 Webhook”的企业微信群机器人。
    if _is_wecom_group_webhook(channel.webhook_url):
        return _send_wecom_webhook(channel, _format_text(title, text))
    headers = {}
    if channel.secret:
        headers['Authorization'] = channel.secret
    payload = {'title': title, 'content': text, 'target': target}
    try:
        resp = requests.post(channel.webhook_url, json=payload, headers=headers, timeout=_TIMEOUT)
        return 200 <= resp.status_code < 300, resp.text[:1000]
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _format_text(title: str, body: str) -> str:
    return f'【{title}】\n{body}' if title else body


def _send_one(channel: IMChannel, title: str, body: str, target: str,
              event: str = '') -> tuple[bool, str]:
    text = _format_text(title, body)
    ct = channel.channel_type
    if ct == IMChannelType.TELEGRAM:
        return _send_telegram(channel, text, target)
    if ct == IMChannelType.WECOM:
        return _send_wecom(channel, text, target)
    if ct == IMChannelType.WECOM_WEBHOOK:
        return _send_wecom_webhook(channel, text)
    if ct == IMChannelType.WECOM_KF:
        return _send_wecom_kf(channel, text, target)
    if ct == IMChannelType.WECOM_AIBOT:
        return _queue_wecom_aibot(channel, title, body, target, event)
    if ct == IMChannelType.DINGTALK:
        return _send_dingtalk(channel, text, target)
    if ct == IMChannelType.WEBHOOK:
        return _send_webhook(channel, title, body, target)
    return False, f'未知通道类型 {ct}'


# ─────────────────────────────────────────────────────────────
# 对外接口
# ─────────────────────────────────────────────────────────────

def send_to_channel(channel: IMChannel, title: str, body: str,
                    event: str = '', targets: list[str] | None = None) -> dict:
    """向单个通道（其全部接口人/默认目标）发送消息，记录日志。"""
    target_list = targets if targets is not None else channel.target_list()
    if channel.channel_type == IMChannelType.WECOM_WEBHOOK:
        target_list = ['']
    if not target_list:
        target_list = ['']  # 群机器人/webhook 无需指定接收人
    sent, queued, failed = 0, 0, 0
    last_resp = ''
    for target in target_list:
        ok, resp = _send_one(channel, title, body, target, event)
        last_resp = resp
        # 智能机器人由常驻 WebSocket 进程在企业微信确认投递后写入发送日志。
        if channel.channel_type != IMChannelType.WECOM_AIBOT:
            IMMessageLog.objects.create(
                channel=channel, channel_type=channel.channel_type, event=event,
                target=target, title=title, content=body, success=ok, response=resp,
            )
        if ok and channel.channel_type == IMChannelType.WECOM_AIBOT:
            queued += 1
        elif ok:
            sent += 1
        else:
            failed += 1
    return {
        'channel': channel.name,
        'sent': sent,
        'queued': queued,
        'failed': failed,
        'last_response': last_resp,
    }


def test_channel(channel: IMChannel) -> dict:
    """测试通道并回写结果；企业微信客服只校验鉴权，避免误发给客户。"""
    if channel.channel_type == IMChannelType.WECOM_KF:
        token, token_error = _wecom_access_token(channel, force_refresh=True)
        success = bool(token)
        result = {
            'channel': channel.name,
            'sent': 0,
            'failed': 0 if success else 1,
            'last_response': (
                '企业微信客服凭证验证成功；发送消息时需提供客户 external_userid，'
                '且须满足企业微信客服会话的可回复窗口。'
                if success else token_error
            ),
        }
    else:
        result = send_to_channel(
            channel, 'ISP Manager 测试消息',
            '这是一条来自 ISP Manager 的 IM 对接测试消息，如收到说明配置成功。',
            event='custom',
        )
        success = (result['sent'] > 0 or result.get('queued', 0) > 0) and result['failed'] == 0
    channel.last_test_at = timezone.now()
    channel.last_test_status = IMTestStatus.SUCCESS if success else IMTestStatus.FAILED
    channel.last_test_message = (result.get('last_response') or '')[:512]
    channel.save(update_fields=['last_test_at', 'last_test_status', 'last_test_message', 'updated_at'])
    return {'success': success, **result}


def _is_duplicate_event(
    rule: NotificationRule,
    event: str,
    context: dict[str, str],
    dedupe_key: str,
) -> bool:
    """按规则的可选窗口抑制同一对象的重复通知。"""
    if not rule.dedupe_seconds:
        return False
    fingerprint = dedupe_key or '|'.join([
        event,
        context.get('action', ''),
        context.get('target_id', '') or context.get('target_name', ''),
        context.get('customer_no', '') or context.get('customer_name', ''),
        context.get('order_no', ''),
        context.get('instance_no', ''),
        context.get('vps_no', ''),
        context.get('edge_no', ''),
    ])
    digest = hashlib.sha256(f'{rule.pk}|{fingerprint}'.encode()).hexdigest()
    return not cache.add(
        f'im:notification-dedupe:{digest}',
        True,
        timeout=rule.dedupe_seconds,
    )


# 业务动作代码与规则筛选值不完全一致时的等价映射。
_ACTION_EQUIVALENTS = {
    'billing_payment': {
        'succeeded': ('payment',),
        'payment': ('succeeded',),
    },
}


def _action_matches(event: str, action: str, filters: list[str]) -> bool:
    """动作筛选：未配置、手动测试或未带动作时放行，避免误杀整条规则。"""
    if not filters:
        return True
    action = (action or '').strip()
    if not action or action == 'custom':
        return True
    if action in filters:
        return True
    equivalents = _ACTION_EQUIVALENTS.get(event, {})
    if any(alias in filters for alias in equivalents.get(action, ())):
        return True
    return any(action in equivalents.get(item, ()) for item in filters)


def dispatch_event(
    event: str,
    title: str = '',
    body: str = '',
    *,
    context: dict | None = None,
    dedupe_key: str = '',
    skip_action_filters: bool = False,
    rule_id: int | None = None,
) -> dict:
    """
    按通知规则把事件推送到 IM 通道。

    ``title`` / ``body`` 保留给旧调用方；新业务代码应传入 ``context``，由规则的
    标题、正文模板及格式模式渲染完整信息。
    """
    # 全局 IM 开关
    from .services import get_setting
    if not get_setting('notify_im_enabled', True):
        return {
            'event': event,
            'dispatched': 0,
            'disabled': True,
            'matched_rules': 0,
            'skipped': 0,
            'results': [],
            'skip_reasons': [{'reason': 'im_disabled', 'detail': 'IM 通知总开关已关闭'}],
        }
    # 多事件规则的 events 保存在 JSON 中；为兼容 MySQL/SQLite 部署差异，先取
    # 已启用规则再用模型方法判断命中，避免依赖 JSON contains 查询语法。
    rules = (
        NotificationRule.objects
        .filter(enabled=True)
        .prefetch_related('channels')
    )
    if rule_id:
        rules = rules.filter(pk=rule_id)
    results = []
    skipped = 0
    matched_rules = 0
    skip_reasons = []
    for rule in rules:
        if not rule.matches_event(event):
            skip_reasons.append({
                'rule_id': rule.pk,
                'name': rule.name,
                'reason': 'event_mismatch',
                'detail': f'规则未订阅事件 {event}',
            })
            continue
        rendered_title, text, normalized_context = render_rule_message(
            event,
            title=title,
            content=body,
            context=context,
            title_template=rule.title_template,
            content_template=rule.template,
            format_mode=rule.format_mode,
        )
        action_filters = rule.action_filters_for_event(event)
        if (
            not skip_action_filters
            and not _action_matches(event, normalized_context.get('action', ''), action_filters)
        ):
            skip_reasons.append({
                'rule_id': rule.pk,
                'name': rule.name,
                'reason': 'action_filter',
                'detail': (
                    f'动作 {normalized_context.get("action") or "（空）"} '
                    f'未命中筛选 {action_filters}'
                ),
            })
            continue
        matched_rules += 1
        if _is_duplicate_event(rule, event, normalized_context, dedupe_key):
            skipped += 1
            skip_reasons.append({
                'rule_id': rule.pk,
                'name': rule.name,
                'reason': 'dedupe',
                'detail': f'重复抑制窗口 {rule.dedupe_seconds} 秒内已发送',
            })
            continue
        channels = list(rule.channels.filter(enabled=True))
        if not channels:
            skip_reasons.append({
                'rule_id': rule.pk,
                'name': rule.name,
                'reason': 'no_channel',
                'detail': '规则未绑定已启用的 IM 通道',
            })
            continue
        for channel in channels:
            results.append(send_to_channel(channel, rendered_title, text, event=event))
    if not results and not skip_reasons:
        skip_reasons.append({
            'reason': 'no_enabled_rule',
            'detail': '没有已启用的通知规则',
        })
    if not results:
        logger.info(
            'IM 通知未投递 event=%s matched=%s skipped=%s reasons=%s',
            event, matched_rules, skipped,
            [item.get('detail') or item.get('reason') for item in skip_reasons][:8],
        )
    return {
        'event': event,
        'dispatched': len(results),
        'matched_rules': matched_rules,
        'skipped': skipped,
        'results': results,
        'skip_reasons': skip_reasons,
    }


def notify(
    event: str,
    title: str = '',
    body: str = '',
    *,
    context: dict | None = None,
    dedupe_key: str = '',
) -> None:
    """安全包装：用于业务侧/信号触发，绝不因通知失败而影响主流程。"""
    try:
        dispatch_event(
            event,
            title,
            body,
            context=context,
            dedupe_key=dedupe_key,
        )
    except Exception:  # noqa: BLE001
        logger.warning('IM 通知发送失败 event=%s', event, exc_info=True)
    try:
        from .email_services import dispatch_email
        dispatch_email(event, title, body, context=context)
    except Exception:  # noqa: BLE001
        logger.warning('邮件通知发送失败 event=%s', event, exc_info=True)
