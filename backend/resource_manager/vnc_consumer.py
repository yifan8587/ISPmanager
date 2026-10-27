"""
noVNC 远程控制台中继消费者。

ISPmanager 不直接连接 PVE，而是经由 POP/PE 节点的 vpopmanager 暴露的 noVNC
WebSocket（节点再代理到 Proxmox vncwebsocket）。本消费者把浏览器的 noVNC 流量
中继到节点 WebSocket，实现「浏览器 ↔ ISPmanager ↔ 节点 ↔ PVE」的链路：

  浏览器 RFB ──ws/resource/vnc/<vps_id>/?token=<ISP JWT>&vncticket=&port=──▶ 本消费者
  本消费者 ──wss://<节点host>/ws/pvemanager/vnc/<source_ref>/?token=<节点 JWT>&...──▶ 节点
  节点 ──wss://<pve>/api2/json/.../vncwebsocket──▶ Proxmox

票据流程（与 vpopmanager 一致，避免重复调用 vncproxy 使票据失效）：
  前端先调 REST /vps/{id}/vnc_ticket/ 取得 ticket/port/password（仅调用一次 vncproxy），
  随后立即用 ticket/port 建立本 WebSocket；本消费者把 ticket/port 原样透传给节点，
  节点不会再次调用 vncproxy。
"""

import asyncio
import logging
import ssl
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None


def node_ws_connect_kwargs(ssl_context):
    """直连节点。websockets 15 默认会走环境变量里的 HTTP 代理并开启压缩，节点侧接不住。"""
    kwargs = {
        'subprotocols': ['binary'],
        'max_size': None,
        'compression': None,
        'proxy': None,
        'open_timeout': 20,
        'ping_interval': None,
    }
    if ssl_context is not None:
        try:
            ssl_context.set_alpn_protocols(['http/1.1'])
        except Exception:  # noqa: BLE001
            pass
        kwargs['ssl'] = ssl_context
    return kwargs


async def connect_node_websocket(url, ssl_context, headers, origin):
    """连节点控制台。新库用 additional_headers，旧库用 extra_headers。"""
    if websockets is None:
        raise RuntimeError('服务端缺少 websockets')
    base = node_ws_connect_kwargs(ssl_context)
    if origin:
        base['origin'] = origin
    header_items = list((headers or {}).items())
    variants = []
    for header_key in ('additional_headers', 'extra_headers'):
        variants.append({header_key: header_items, **base})
        if 'proxy' in base:
            slim = dict(base)
            slim.pop('proxy', None)
            variants.append({header_key: header_items, **slim})
    last_error = None
    for variant in variants:
        try:
            return await websockets.connect(url, **variant)
        except TypeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError('无法连接节点控制台')


def handshake_status(exc):
    response = getattr(exc, 'response', None)
    return getattr(response, 'status_code', None) or getattr(exc, 'status_code', None)


def handshake_rejected(exc) -> bool:
    """节点在握手阶段按身份拒绝（HTTP 401/403）。"""
    return handshake_status(exc) in (401, 403)


def fit_close_reason(text) -> str:
    """WebSocket 关闭原因最多 123 字节，超了整个关闭帧会被浏览器当成协议错误。"""
    raw = ' '.join(str(text or '').split()).encode('utf-8')[:123]
    return raw.decode('utf-8', 'ignore')


def close_reason(exc) -> str:
    return fit_close_reason(f'{type(exc).__name__}: {exc}')


def node_connect_failure(exc) -> str:
    status = handshake_status(exc)
    if status in (401, 403):
        # 旧版节点（1.1.2 之前）把身份、权限和 PVE 失败都报成握手 403。
        return fit_close_reason(f'节点拒绝控制台连接 HTTP {status}，请查看节点日志或升级节点')
    if status:
        return fit_close_reason(f'节点控制台握手失败 HTTP {status}')
    return close_reason(exc)


class VPSVNCConsumer(AsyncWebsocketConsumer):
    """路径: ws/resource/vnc/<vps_id>/?token=<JWT>&vncticket=<t>&port=<p>"""

    node_ws = None

    async def connect(self):
        self._upstream = asyncio.Lock()
        self._node_ready = False
        self._accepted = False
        self._fail = None
        await self._upstream.acquire()
        try:
            await self._open()
        except Exception as e:  # noqa: BLE001
            logger.exception('vnc open failed')
            if not self._fail:
                await self._reject(4502, close_reason(e))
        finally:
            self._node_ready = self.node_ws is not None
            self._upstream.release()
        if self._fail:
            if not self._accepted:
                chosen = 'binary' if 'binary' in self._client_subprotocols() else None
                await self.accept(subprotocol=chosen)
            code, reason = self._fail
            logger.warning('vnc console refused vps=%s code=%s reason=%s',
                           getattr(self, 'vps_id', '?'), code, reason)
            await self.close(code=code, reason=reason)

    async def _open(self):
        if websockets is None:
            await self._reject(4401, '服务端缺少 websockets')
            return

        user = self.scope.get('user')
        if not user or not getattr(user, 'is_authenticated', False):
            await self._reject(4401, '登录已失效')
            return

        self.vps_id = self.scope['url_route']['kwargs']['vps_id']
        decision, vps = await self._authorize_vps(user, self.vps_id)
        if decision == 'forbidden':
            await self._reject(4403, '没有控制台权限')
            return
        if vps is None:
            await self._reject(4404, '找不到这台 VPS')
            return
        if not vps['source_ref']:
            await self._reject(4400, '这台 VPS 还没有同步到节点')
            return

        qs = parse_qs(self.scope.get('query_string', b'').decode())
        grant_id = (qs.get('grant') or [None])[0]
        vncticket = (qs.get('vncticket') or [None])[0]
        port = (qs.get('port') or [None])[0]
        if grant_id:
            granted = await self._take_grant(user, self.vps_id, grant_id)
            if not granted:
                await self._reject(4408, '控制台票据已失效，请重新打开')
                return
            vncticket, port = granted

        # 先接受浏览器，关闭时才能把原因送回去。浏览器发出的 RFB 数据先堵住，
        # 等节点连上再放行，避免开头的协议字节被丢掉。
        chosen = 'binary' if 'binary' in self._client_subprotocols() else None
        await self.accept(subprotocol=chosen)
        self._accepted = True
        if not vncticket or not port:
            await self._reject(4408, '缺少控制台票据')
            return

        ssl_ctx = None
        try:
            client = self._node_client(vps)
        except Exception as e:  # noqa: BLE001
            logger.error('build node vnc client failed: %s', e)
            await self._reject(4500, close_reason(e))
            return
        modes = client.websocket_auth_modes()
        if not modes:
            await self._reject(4500, '节点未配置控制台账号或 Token')
            return

        if client.base.startswith('https://'):
            ssl_ctx = ssl.create_default_context()
            if not client.verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        # 节点在校验身份时就拒绝，还没去连 PVE，票据仍然有效，可以换身份再试。
        last_error = None
        for mode in modes:
            try:
                node_url, auth_headers, origin = await asyncio.to_thread(
                    self._build_node_ws_url, client, mode, vps['source_ref'], vncticket, port,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning('node vnc auth (%s) failed: %s', mode, e)
                last_error = e
                continue
            try:
                self.node_ws = await connect_node_websocket(node_url, ssl_ctx, auth_headers, origin)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning('connect node vnc ws (%s) failed: %s', mode, e)
                last_error = e
                if not handshake_rejected(e):
                    break
        if self.node_ws is None:
            if last_error is None:
                await self._reject(4502, '无法连接节点控制台')
            else:
                await self._reject(4502, node_connect_failure(last_error))
            return

        self._relay_task = asyncio.create_task(self._relay_from_node())

    async def _reject(self, code, reason):
        self._fail = (code, fit_close_reason(reason))

    @staticmethod
    @database_sync_to_async
    def _take_grant(user, vps_id, grant_id):
        from datetime import timedelta

        from django.core.exceptions import ValidationError
        from django.utils import timezone

        from .models import VNCConsoleGrant

        try:
            grant = VNCConsoleGrant.objects.get(pk=grant_id, vps_id=vps_id, user_id=user.pk)
        except (VNCConsoleGrant.DoesNotExist, ValueError, TypeError, ValidationError):
            return None
        if grant.created_at < timezone.now() - timedelta(seconds=45):
            grant.delete()
            return None
        payload = (grant.ticket, grant.port)
        grant.delete()
        return payload

    def _client_subprotocols(self):
        declared = self.scope.get('subprotocols')
        if declared:
            return [str(p).strip() for p in declared if str(p).strip()]
        headers = dict(self.scope.get('headers') or [])
        raw = headers.get(b'sec-websocket-protocol', b'').decode()
        return [p.strip() for p in raw.split(',') if p.strip()]

    @staticmethod
    def _node_client(vps):
        from .pemanager_client import PemanagerClient
        base = vps['api_base_url'] or (f'http://{vps["mgmt_ip"]}' if vps['mgmt_ip'] else '')
        return PemanagerClient(
            base, vps['api_username'], vps['api_password'],
            verify=vps.get('api_verify_ssl'),
            api_token=vps.get('mgmt_token') or None,
        )

    @staticmethod
    def _build_node_ws_url(client, mode, source_ref, vncticket, port):
        from urllib.parse import urlsplit
        client.authenticate_for_websocket(mode)
        url = client.node_vnc_ws_url(source_ref, vncticket=vncticket, port=port)
        parts = urlsplit(client.base)
        origin = f'{parts.scheme}://{parts.netloc}' if parts.netloc else None
        return url, client.node_ws_auth_headers(), origin

    @staticmethod
    @database_sync_to_async
    def _authorize_vps(user, vps_id):
        from accounts.ws_access import authorize_vps_console
        return authorize_vps_console(user, vps_id)

    async def _relay_from_node(self):
        close_code = None
        close_reason_text = ''
        try:
            async for message in self.node_ws:
                if isinstance(message, bytes):
                    await self.send(bytes_data=message)
                else:
                    await self.send(text_data=message)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            received = getattr(e, 'rcvd', None)
            if received is not None:
                close_code = getattr(received, 'code', None)
                close_reason_text = fit_close_reason(getattr(received, 'reason', ''))
            else:
                close_code = 4502
                close_reason_text = fit_close_reason(f'节点连接中断：{close_reason(e)}')
            logger.warning('node vnc closed vps=%s code=%s reason=%s', getattr(self, 'vps_id', '?'),
                           close_code, close_reason_text)
        if close_code and close_code not in (1004, 1005, 1006, 1015):
            await self.close(code=close_code, reason=close_reason_text or None)
        else:
            await self.close()

    async def receive(self, text_data=None, bytes_data=None):
        if not getattr(self, '_node_ready', False):
            upstream = getattr(self, '_upstream', None)
            if upstream is not None:
                await upstream.acquire()
                upstream.release()
        if not self.node_ws:
            return
        try:
            if bytes_data is not None:
                await self.node_ws.send(bytes_data)
            elif text_data is not None:
                await self.node_ws.send(text_data)
        except Exception as e:  # noqa: BLE001
            logger.warning('send to node vnc: %s', e)
            await self.close()

    async def disconnect(self, code):
        if getattr(self, '_relay_task', None):
            self._relay_task.cancel()
        if self.node_ws:
            try:
                await self.node_ws.close()
            except Exception:  # noqa: BLE001
                pass
