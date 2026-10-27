"""保持一个企业微信智能机器人的 WebSocket 长连接并投递通知队列。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from system_manager.models import (
    IMChannel,
    IMChannelType,
    IMMessageLog,
    WeComAIBotMessage,
)

logger = logging.getLogger(__name__)

WEBSOCKET_URL = 'wss://openws.work.weixin.qq.com'
REQUEST_TIMEOUT = 15
RECONNECT_DELAY = 5
MAX_ATTEMPTS = 3


def _request_id() -> str:
    return uuid.uuid4().hex


@sync_to_async
def _load_channel(channel_id: int) -> IMChannel:
    try:
        channel = IMChannel.objects.get(
            pk=channel_id, channel_type=IMChannelType.WECOM_AIBOT, enabled=True,
        )
    except IMChannel.DoesNotExist as exc:
        raise CommandError('未找到启用的企业微信智能机器人通道') from exc
    if not (channel.wecom_aibot_id and channel.wecom_aibot_secret):
        raise CommandError('该通道缺少智能机器人 BotID 或长连接 Secret')
    return channel


@sync_to_async
def _reset_inflight(channel_id: int) -> None:
    WeComAIBotMessage.objects.filter(
        channel_id=channel_id, status=WeComAIBotMessage.Status.SENDING,
    ).update(status=WeComAIBotMessage.Status.PENDING, response='连接中断，等待重新投递')


@sync_to_async
def _claim_message(channel_id: int) -> WeComAIBotMessage | None:
    with transaction.atomic():
        message = (
            WeComAIBotMessage.objects.select_for_update(skip_locked=True)
            .filter(channel_id=channel_id, status=WeComAIBotMessage.Status.PENDING)
            .order_by('created_at')
            .first()
        )
        if message is None:
            return None
        message.status = WeComAIBotMessage.Status.SENDING
        message.attempts += 1
        message.save(update_fields=['status', 'attempts', 'updated_at'])
        return message


@sync_to_async
def _record_result(message_id: int, success: bool, response: str) -> None:
    with transaction.atomic():
        message = WeComAIBotMessage.objects.select_for_update().select_related('channel').get(pk=message_id)
        if success:
            message.status = WeComAIBotMessage.Status.SENT
            message.sent_at = timezone.now()
        else:
            message.status = (
                WeComAIBotMessage.Status.FAILED
                if message.attempts >= MAX_ATTEMPTS else WeComAIBotMessage.Status.PENDING
            )
        message.response = response[:4000]
        message.save(update_fields=['status', 'sent_at', 'response', 'updated_at'])
        IMMessageLog.objects.create(
            channel=message.channel,
            channel_type=IMChannelType.WECOM_AIBOT,
            event=message.event,
            target=message.target,
            title=message.title,
            content=message.content,
            success=success,
            response=response[:4000],
        )


class Command(BaseCommand):
    help = '连接企业微信智能机器人并投递该通道的待发送通知'

    def add_arguments(self, parser):
        parser.add_argument('--channel', type=int, required=True, help='IM 通道 ID')

    def handle(self, *args, **options):
        try:
            import websockets  # noqa: F401
        except ImportError as exc:
            raise CommandError('缺少 websockets 依赖，请先安装 requirements.txt') from exc
        asyncio.run(self._run(options['channel']))

    async def _run(self, channel_id: int) -> None:
        channel = await _load_channel(channel_id)
        self.stdout.write(self.style.SUCCESS(
            f'启动企业微信智能机器人连接：{channel.name}（BotID: {channel.wecom_aibot_id}）',
        ))
        while True:
            try:
                channel = await _load_channel(channel_id)
                await _reset_inflight(channel_id)
                await self._connect_and_serve(channel)
            except CommandError as exc:
                raise exc
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception('企业微信智能机器人连接中断 channel=%s', channel_id)
                self.stderr.write(self.style.WARNING(
                    f'智能机器人连接中断，{RECONNECT_DELAY} 秒后重连',
                ))
                await asyncio.sleep(RECONNECT_DELAY)

    async def _connect_and_serve(self, channel: IMChannel) -> None:
        import websockets

        async with websockets.connect(
            WEBSOCKET_URL, ping_interval=30, ping_timeout=20, max_size=2 * 1024 * 1024,
        ) as websocket:
            subscribe_id = _request_id()
            await websocket.send(json.dumps({
                'cmd': 'aibot_subscribe',
                'headers': {'req_id': subscribe_id},
                'body': {
                    'bot_id': channel.wecom_aibot_id,
                    'secret': channel.wecom_aibot_secret,
                },
            }))
            response = await self._wait_response(websocket, subscribe_id)
            if response.get('errcode') != 0:
                raise RuntimeError(
                    f'智能机器人鉴权失败：errcode={response.get("errcode")}，'
                    f'errmsg={response.get("errmsg", "未知错误")}',
                )
            self.stdout.write(self.style.SUCCESS('企业微信智能机器人长连接已建立'))
            while True:
                message = await _claim_message(channel.pk)
                if message is None:
                    await self._receive_once(websocket, timeout=1)
                    continue
                await self._deliver(websocket, message)

    async def _deliver(self, websocket, message: WeComAIBotMessage) -> None:
        req_id = _request_id()
        payload = {
            'cmd': 'aibot_send_msg',
            'headers': {'req_id': req_id},
            'body': {
                'chatid': message.target,
                'chat_type': 1,
                'msgtype': 'markdown',
                'markdown': {'content': self._markdown(message)},
            },
        }
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
            response = await self._wait_response(websocket, req_id)
            success = response.get('errcode') == 0
            response_text = json.dumps(response, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            success = False
            response_text = str(exc)
        await _record_result(message.pk, success, response_text)

    @staticmethod
    def _markdown(message: WeComAIBotMessage) -> str:
        title = f'**{message.title}**\n' if message.title else ''
        return f'{title}{message.content}'[:4096]

    async def _wait_response(self, websocket, req_id: str) -> dict:
        deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError('等待企业微信智能机器人响应超时')
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            data = self._decode(raw)
            if data.get('headers', {}).get('req_id') == req_id:
                return data
            self._handle_callback(data)

    async def _receive_once(self, websocket, timeout: int) -> None:
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        except TimeoutError:
            return
        self._handle_callback(self._decode(raw))

    @staticmethod
    def _decode(raw) -> dict:
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError('企业微信智能机器人返回了无法解析的数据') from exc

    @staticmethod
    def _handle_callback(data: dict) -> None:
        if data.get('cmd') in ('aibot_msg_callback', 'aibot_event_callback'):
            logger.info('收到企业微信智能机器人回调 cmd=%s', data['cmd'])
