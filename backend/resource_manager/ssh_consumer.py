"""
WebSSH 终端消费者。

浏览器通过 xterm.js 经 WebSocket 与本消费者通信，本消费者用 paramiko 打开到 POP
节点的交互式 SSH PTY，实现真正的双向终端，并支持：
  - 交互输入 / 输出
  - 终端窗口 resize
  - 把后端已上传的部署包 SFTP 推送到节点，并在终端内执行 INSTALL.sh 进行部署

消息协议（JSON 文本帧）
  客户端 → 服务端
    { "type": "open",   "host","port","username","password","pkey"? }   打开 SSH
    { "type": "input",  "data": "<keystrokes>" }                         键盘输入
    { "type": "resize", "cols": 120, "rows": 30 }                        调整窗口
    { "type": "deploy", "package":"<上传返回的文件名>", "remote_dir","args"? }  部署
  服务端 → 客户端
    { "type": "data",   "data": "<terminal output>" }
    { "type": "status", "level":"info|error|success", "msg":"..." }
    { "type": "closed" }
"""

import asyncio
import json
import os
import threading
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings


class PopSSHConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not user or not getattr(user, 'is_authenticated', False):
            await self.close(code=4401)
            return

        allowed = await self._can_open_pop_ssh(user)
        if not allowed:
            await self.close(code=4403)
            return

        self.pop_id = self.scope['url_route']['kwargs']['pop_id']
        self.pop = await self._get_pop(self.pop_id)
        if self.pop is None:
            await self.close(code=4404)
            return

        self._client = None
        self._chan = None
        self._reader = None
        self._closing = False
        self._loop = asyncio.get_running_loop()

        from resource_manager.ws_auth import chosen_ws_subprotocol
        await self.accept(subprotocol=chosen_ws_subprotocol(self.scope))
        await self._status('info', f'WebSSH 已就绪，目标节点 [{self.pop["code"]}] {self.pop["name"]}')

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
        elif mtype == 'deploy':
            await self._deploy(msg)

    # ── SSH 生命周期 ──────────────────────────────────────────
    async def _open_ssh(self, msg):
        if self._chan is not None:
            await self._status('info', '已有活动会话')
            return

        host = msg.get('host') or self.pop.get('mgmt_ip')
        port = int(msg.get('port') or self.pop.get('ssh_port') or 22)
        username = msg.get('username') or self.pop.get('mgmt_username') or 'root'
        password = msg.get('password') or self.pop.get('mgmt_password') or ''
        pkey = msg.get('pkey') or ''

        if not host:
            await self._status('error', '缺少节点地址（host / 管理 IP）')
            return

        await self._status('info', f'正在连接 {username}@{host}:{port} ...')
        try:
            await self._loop.run_in_executor(
                None, self._connect_blocking, host, port, username, password, pkey
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

    async def _deploy(self, msg):
        if self._chan is None:
            await self._status('error', '请先建立 SSH 连接再执行部署')
            return
        package = msg.get('package') or ''
        remote_dir = msg.get('remote_dir') or settings.POP_DEPLOY_REMOTE_DIR
        args = msg.get('args') or ''

        local_path = self._safe_package_path(package)
        if not local_path or not os.path.isfile(local_path):
            await self._status('error', '部署包不存在，请先上传')
            return

        fname = os.path.basename(local_path)
        remote_pkg = f'{remote_dir.rstrip("/")}/{fname}'
        await self._status('info', f'正在上传部署包 {fname} → {remote_pkg} ...')
        try:
            await self._loop.run_in_executor(
                None, self._sftp_put, local_path, remote_dir, remote_pkg
            )
        except Exception as e:  # noqa: BLE001
            await self._status('error', f'上传失败：{e}')
            return
        await self._status('success', '部署包上传完成，开始在终端执行部署')

        # 在交互终端内执行解压 + 安装，输出会经 read_loop 回传到 xterm
        extract = (
            f'mkdir -p {remote_dir} && cd {remote_dir} && '
            f'tar xzf {fname} && '
            f'cd "$(tar tzf {fname} | head -1 | cut -d/ -f1)" && '
            f'sudo bash INSTALL.sh {args}'
        )
        self._write_chan(extract + '\n')

    def _sftp_put(self, local_path, remote_dir, remote_pkg):
        sftp = self._client.open_sftp()
        try:
            # 逐级确保远端目录存在
            parts = remote_dir.strip('/').split('/')
            cur = ''
            for p in parts:
                cur += '/' + p
                try:
                    sftp.stat(cur)
                except IOError:
                    sftp.mkdir(cur)
            sftp.put(local_path, remote_pkg)
        finally:
            sftp.close()

    @staticmethod
    def _safe_package_path(package):
        if not package:
            return None
        # 仅允许文件名，禁止路径穿越
        name = os.path.basename(package)
        return os.path.join(settings.POP_DEPLOY_UPLOAD_DIR, name)

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

    # ── 发送辅助 ──────────────────────────────────────────────
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
    def _can_open_pop_ssh(user):
        from accounts.ws_access import user_can_open_pop_ssh
        return user_can_open_pop_ssh(user)

    @database_sync_to_async
    def _get_pop(self, pop_id):
        from .models import PopPoint
        try:
            p = PopPoint.objects.get(pk=pop_id)
        except PopPoint.DoesNotExist:
            return None
        return {
            'id': p.id, 'code': p.code, 'name': p.name,
            'mgmt_ip': p.mgmt_ip, 'mgmt_username': p.mgmt_username,
            'mgmt_password': p.mgmt_password, 'ssh_port': p.ssh_port,
        }
