"""
运维 WebSSH 终端消费者。

支持目标类型：pop / vps / monitoring_host
消息协议与 resource_manager.ssh_consumer.PopSSHConsumer 一致（不含 deploy）。
"""

import asyncio
import json
import threading
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .ssh_resolver import resolve_ssh_target


class OperationSSHConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not user or not getattr(user, 'is_authenticated', False):
            await self.close(code=4401)
            return

        allowed = await self._can_open_operation_ssh(user)
        if not allowed:
            await self.close(code=4403)
            return

        self.target_type = self.scope['url_route']['kwargs']['target_type']
        self.target_id = self.scope['url_route']['kwargs']['target_id']

        if self.target_type == 'custom':
            self.context = {
                'target_type': 'custom',
                'label': '手动连接',
                'host': '',
                'port': 22,
                'username': 'root',
                'password': '',
            }
        else:
            self.context = await self._resolve_context()
            if self.context is None:
                await self.close(code=4404)
                return

        self._client = None
        self._chan = None
        self._reader = None
        self._closing = False
        self._loop = asyncio.get_running_loop()

        from resource_manager.ws_auth import chosen_ws_subprotocol
        await self.accept(subprotocol=chosen_ws_subprotocol(self.scope))
        await self._status(
            'info',
            f'WebSSH 已就绪，目标 [{self.context.get("label") or self.target_type}]',
        )

    async def disconnect(self, code):
        self._closing = True
        await self._teardown_ssh()

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data:
            return
        try:
            msg = json.loads(text_data)
        except (ValueError, TypeError):
            return
        mtype = msg.get('type')

        if mtype == 'open':
            await self._open_ssh(msg)
        elif mtype == 'input':
            self._write_chan(msg.get('data', ''))
        elif mtype == 'resize':
            self._resize(msg.get('cols', 80), msg.get('rows', 24))
        elif mtype == 'exec':
            await self._exec_command(msg)

    async def _exec_command(self, msg):
        if self._chan is None:
            await self._status('error', '请先建立 SSH 连接再执行命令')
            return
        command = (msg.get('command') or '').strip()
        if not command:
            await self._status('error', '命令不能为空')
            return
        self._write_chan(command + '\n')
        await self._status('info', f'已发送：{command[:120]}')

    async def _open_ssh(self, msg):
        if self._chan is not None:
            await self._status('info', '已有活动会话')
            return

        ctx = self.context or {}
        host = msg.get('host') or ctx.get('host')
        port = int(msg.get('port') or ctx.get('port') or 22)
        username = msg.get('username') or ctx.get('username') or 'root'
        password = msg.get('password')
        if password is None:
            password = ctx.get('password') or ''
        pkey = msg.get('pkey') or ''

        if not host:
            await self._status('error', '缺少主机地址（host / 管理 IP）')
            return

        await self._status('info', f'正在连接 {username}@{host}:{port} ...')
        try:
            await self._loop.run_in_executor(
                None, self._connect_blocking, host, port, username, password, pkey,
            )
        except Exception as e:  # noqa: BLE001
            await self._status('error', f'SSH 连接失败：{e}')
            await self._teardown_ssh()
            return

        await self._status('success', 'SSH 连接成功')
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _connect_blocking(self, host, port, username, password, pkey):
        import io
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs = dict(
            hostname=host, port=port, username=username,
            timeout=15, banner_timeout=15, auth_timeout=15,
            look_for_keys=False, allow_agent=False,
        )
        if pkey:
            key = None
            for kcls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
                try:
                    key = kcls.from_private_key(io.StringIO(pkey))
                    break
                except Exception:  # noqa: BLE001
                    continue
            if key is None:
                raise ValueError('无法解析私钥')
            connect_kwargs['pkey'] = key
        else:
            connect_kwargs['password'] = password

        client.connect(**connect_kwargs)
        chan = client.invoke_shell(term='xterm-256color', width=120, height=32)
        chan.settimeout(0.0)
        self._client = client
        self._chan = chan

    def _read_loop(self):
        chan = self._chan
        while not self._closing and chan is not None and not chan.closed:
            try:
                if chan.recv_ready():
                    data = chan.recv(4096)
                    if not data:
                        break
                    text = data.decode('utf-8', errors='replace')
                    asyncio.run_coroutine_threadsafe(self._send_data(text), self._loop)
                else:
                    time.sleep(0.02)
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
            if chan is not None and chan.exit_status_ready() and not chan.recv_ready():
                break
        asyncio.run_coroutine_threadsafe(self._on_remote_closed(), self._loop)

    def _write_chan(self, data):
        if self._chan is not None and not self._chan.closed:
            try:
                self._chan.send(data)
            except Exception:  # noqa: BLE001
                pass

    def _resize(self, cols, rows):
        if self._chan is not None and not self._chan.closed:
            try:
                self._chan.resize_pty(width=int(cols), height=int(rows))
            except Exception:  # noqa: BLE001
                pass

    async def _teardown_ssh(self):
        try:
            if self._chan is not None:
                self._chan.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._client is not None:
                self._client.close()
        except Exception:  # noqa: BLE001
            pass
        self._chan = None
        self._client = None

    async def _on_remote_closed(self):
        if self._closing:
            return
        await self._status('info', '远端会话已关闭')
        await self._teardown_ssh()
        try:
            await self.send(text_data=json.dumps({'type': 'closed'}))
        except Exception:  # noqa: BLE001
            pass

    async def _send_data(self, text):
        try:
            await self.send(text_data=json.dumps({'type': 'data', 'data': text}))
        except Exception:  # noqa: BLE001
            pass

    async def _status(self, level, msg):
        try:
            await self.send(text_data=json.dumps({'type': 'status', 'level': level, 'msg': msg}))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    @database_sync_to_async
    def _can_open_operation_ssh(user):
        from accounts.ws_access import user_can_open_operation_ssh
        return user_can_open_operation_ssh(user)

    @database_sync_to_async
    def _resolve_context(self):
        try:
            target_id = int(self.target_id)
        except (TypeError, ValueError):
            return None
        return resolve_ssh_target(self.target_type, target_id)
