"""在管理端服务器上按客户门户控制台的中转流程走一遍，逐步打印结果。

用法: python manage.py vnc_diagnose <vps_id>
会真实申请一次 PVE 控制台票据，不影响虚机运行。
"""
import asyncio
import ssl
import time

from django.core.management.base import BaseCommand, CommandError

from resource_manager.models import VPS
from resource_manager.pemanager_client import PemanagerClient, PemanagerError
from resource_manager.vnc_consumer import (
    close_reason,
    connect_node_websocket,
    handshake_status,
)


class Command(BaseCommand):
    help = '诊断客户门户 noVNC 控制台：取票据、连节点、等 PVE 的 RFB 握手'

    def add_arguments(self, parser):
        parser.add_argument('vps_id', type=int)
        parser.add_argument('--wait', type=float, default=10.0, help='等待 RFB 握手的秒数')

    def handle(self, *args, **opts):
        try:
            vps = VPS.objects.select_related('pop_point').get(pk=opts['vps_id'])
        except VPS.DoesNotExist as exc:
            raise CommandError('找不到这台 VPS') from exc
        pop = vps.pop_point
        self.stdout.write(f'VPS {vps.pk} {vps.name} 状态={vps.status} 节点虚机={vps.source_ref or "-"}')
        if not vps.source_ref:
            raise CommandError('VPS 没有同步到节点（source_ref 为空）')
        if pop is None:
            raise CommandError('VPS 没有所属 POP')
        self.stdout.write(
            f'POP {pop.pk} {pop.name} API={pop.api_base_url or pop.mgmt_ip} '
            f'Token={"有" if pop.mgmt_token else "无"} '
            f'账号={"有" if (pop.api_username or pop.mgmt_username) else "无"} '
            f'校验证书={"是" if getattr(pop, "api_verify_ssl", False) else "否"}'
        )

        try:
            client, _ = PemanagerClient.connect_pop(pop)
            started = time.monotonic()
            data = client.vnc_ticket(vps.source_ref) or {}
        except PemanagerError as exc:
            raise CommandError(f'取票据失败：{exc}') from exc
        ticket, port = data.get('ticket'), data.get('port')
        if not ticket or not port:
            raise CommandError(f'节点没有返回票据或端口：{sorted(data)}')
        self.stdout.write(self.style.SUCCESS(f'[1] 取票据成功 port={port}'))

        ws_client = PemanagerClient.from_pop(pop)
        ssl_ctx = None
        if ws_client.base.startswith('https://'):
            ssl_ctx = ssl.create_default_context()
            if not ws_client.verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        for mode in ws_client.websocket_auth_modes():
            label = '账号登录 JWT' if mode == 'login' else 'pem_ Token'
            try:
                ws_client.authenticate_for_websocket(mode)
            except PemanagerError as exc:
                self.stdout.write(self.style.ERROR(f'[2] {label}：节点登录失败 {exc}'))
                continue
            url = ws_client.node_vnc_ws_url(vps.source_ref, vncticket=ticket, port=port)
            self.stdout.write(f'[2] {label}：连接 {url.split("?", 1)[0]}（距取票据 {time.monotonic() - started:.1f}s）')
            ok = asyncio.run(self._probe(url, ssl_ctx, ws_client.node_ws_auth_headers(), opts['wait']))
            if ok:
                return
            if mode == 'login' and 'token' in ws_client.websocket_auth_modes():
                self.stdout.write('    换 Token 再试（同一张票据）')
        raise CommandError('节点控制台不可用，见上面的失败原因')

    async def _probe(self, url, ssl_ctx, headers, wait):
        try:
            ws = await connect_node_websocket(url, ssl_ctx, headers, None)
        except Exception as exc:  # noqa: BLE001
            status = handshake_status(exc)
            body = getattr(getattr(exc, 'response', None), 'body', b'') or b''
            hint = f' HTTP {status}' if status else ''
            self.stdout.write(self.style.ERROR(f'    握手失败{hint}：{close_reason(exc)} {body[:200]!r}'))
            return False
        self.stdout.write(self.style.SUCCESS(f'    握手成功 子协议={ws.subprotocol}'))
        try:
            first = await asyncio.wait_for(ws.recv(), wait)
        except asyncio.TimeoutError:
            self.stdout.write(self.style.ERROR(f'    {wait:.0f}s 内没有收到 PVE 的 RFB 握手，节点连 PVE 卡住'))
            return False
        except Exception as exc:  # noqa: BLE001
            received = getattr(exc, 'rcvd', None)
            if received is not None:
                self.stdout.write(self.style.ERROR(
                    f'    节点关闭连接 code={received.code} reason={received.reason}'))
            else:
                self.stdout.write(self.style.ERROR(f'    节点连接中断：{close_reason(exc)}'))
            return False
        finally:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        preview = first[:12] if isinstance(first, (bytes, bytearray)) else first[:12].encode()
        self.stdout.write(self.style.SUCCESS(f'[3] 收到 PVE 握手 {preview!r}，控制台链路正常'))
        return True
