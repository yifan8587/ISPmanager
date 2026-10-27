"""
Zabbix JSON-RPC 客户端（history.get / item.get / graph.get）。
"""

from __future__ import annotations

import uuid
from typing import Any

import requests

from .models import ZabbixServer


class ZabbixError(Exception):
    def __init__(self, message: str, *, data: Any = None):
        super().__init__(message)
        self.data = data


class ZabbixClient:
    def __init__(self, server: ZabbixServer):
        self.server = server
        self._auth = None
        self._session = requests.Session()
        self._session.verify = bool(server.verify_ssl)

    # 这些方法不需要认证，且 Zabbix 6.4+ 会拒绝携带 auth 的此类请求
    _NO_AUTH_METHODS = frozenset({'apiinfo.version', 'user.login', 'user.logout'})

    def _rpc(self, method: str, params: dict | None = None, *, authenticated: bool = True) -> Any:
        """
        发起 JSON-RPC 请求。

        认证方式（对齐 Zabbix 6.4+/7.x）：
          - 通过 HTTP 头 `Authorization: Bearer <token/sessionid>` 传递凭证；
          - 请求体中**不再**携带 `auth` 字段（否则报 unexpected parameter "auth"）；
          - apiinfo.version / user.login 等方法不带任何认证。
        """
        body = {
            'jsonrpc': '2.0',
            'method': method,
            'params': params or {},
            'id': int(str(uuid.uuid4().int)[:8]),
        }
        headers = {'Content-Type': 'application/json-rpc'}
        if authenticated and self._auth and method not in self._NO_AUTH_METHODS:
            headers['Authorization'] = f'Bearer {self._auth}'

        try:
            resp = self._session.post(
                self.server.api_url,
                json=body,
                headers=headers,
                timeout=self.server.timeout_sec or 30,
            )
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            raise ZabbixError(f'Zabbix API 请求失败: {exc}') from exc
        except ValueError as exc:
            raise ZabbixError(f'Zabbix API 响应非 JSON: {exc}') from exc

        if 'error' in payload:
            err = payload['error']
            parts = [p for p in (err.get('message'), err.get('data')) if p]
            msg = '：'.join(parts) or str(err)
            raise ZabbixError(msg, data=err)
        return payload.get('result')

    def login(self):
        if self.server.auth_type == ZabbixServer.AuthType.API_TOKEN:
            token = (self.server.api_token or '').strip()
            if not token:
                raise ZabbixError('未配置 API Token')
            self._auth = token
            return
        result = self._rpc('user.login', {
            'username': self.server.username,
            'password': self.server.password,
        }, authenticated=False)
        if not result:
            raise ZabbixError('Zabbix 登录失败：用户名或密码错误')
        self._auth = result

    def test_connection(self) -> dict:
        # 1) 版本探测（无需认证）
        version = self._rpc('apiinfo.version', authenticated=False)
        # 2) 登录 / 设置凭证
        self.login()
        # 3) 用一次需要认证的轻量调用验证凭证是否有效
        try:
            self._rpc('host.get', {'output': ['hostid'], 'limit': 1})
        except ZabbixError as exc:
            raise ZabbixError(f'认证校验失败：{exc}', data=exc.data) from exc
        return {'ok': True, 'version': version}

    def history_get(
        self,
        itemids: list[str],
        *,
        time_from: int,
        time_till: int,
        history_type: int = 3,
        sortfield: str = 'clock',
        sortorder: str = 'ASC',
    ) -> list[dict]:
        """history_type: 0 float 3 uint（SNMP 计数器常用 3）"""
        if not itemids:
            return []
        return self._rpc('history.get', {
            'output': 'extend',
            'history': history_type,
            'itemids': [str(i) for i in itemids],
            'time_from': int(time_from),
            'time_till': int(time_till),
            'sortfield': sortfield,
            'sortorder': sortorder,
        }) or []

    def trend_get(
        self,
        itemids: list[str],
        *,
        time_from: int,
        time_till: int,
    ) -> list[dict]:
        if not itemids:
            return []
        return self._rpc('trend.get', {
            'output': 'extend',
            'itemids': [str(i) for i in itemids],
            'time_from': int(time_from),
            'time_till': int(time_till),
        }) or []

    def item_get(self, hostids: list[str] | None = None, itemids: list[str] | None = None) -> list[dict]:
        params: dict[str, Any] = {
            'output': 'extend',
            'selectPreprocessing': 'extend',
        }
        if hostids:
            params['hostids'] = hostids
        if itemids:
            params['itemids'] = itemids
        return self._rpc('item.get', params) or []

    def item_count(self, hostids: list[str]) -> int:
        if not hostids:
            return 0
        result = self._rpc('item.get', {'hostids': hostids, 'countOutput': True})
        try:
            return int(result or 0)
        except (TypeError, ValueError):
            return 0

    def graph_count(self, hostids: list[str]) -> int:
        if not hostids:
            return 0
        result = self._rpc('graph.get', {'hostids': hostids, 'countOutput': True})
        try:
            return int(result or 0)
        except (TypeError, ValueError):
            return 0

    def graph_get(self, hostids: list[str] | None = None, graphids: list[str] | None = None) -> list[dict]:
        params: dict[str, Any] = {
            'output': 'extend',
            'selectItems': 'extend',
            'selectGraphItems': 'extend',
        }
        if hostids:
            params['hostids'] = hostids
        if graphids:
            params['graphids'] = graphids
        return self._rpc('graph.get', params) or []

    # ── 主机群组 / 模板 / 主机 ─────────────────────────────────
    def hostgroup_get(self, search: str | None = None) -> list[dict]:
        params: dict[str, Any] = {'output': ['groupid', 'name'], 'sortfield': 'name'}
        if search:
            params['search'] = {'name': search}
        return self._rpc('hostgroup.get', params) or []

    def template_get(self, search: str | None = None) -> list[dict]:
        params: dict[str, Any] = {'output': ['templateid', 'name'], 'sortfield': 'name'}
        if search:
            params['search'] = {'name': search}
        return self._rpc('template.get', params) or []

    def host_get(
        self,
        *,
        hostids: list[str] | None = None,
        host: str | None = None,
        select_interfaces: bool = False,
    ) -> list[dict]:
        params: dict[str, Any] = {'output': 'extend'}
        if hostids:
            params['hostids'] = hostids
        if host:
            params['filter'] = {'host': host}
        if select_interfaces:
            params['selectInterfaces'] = 'extend'
        return self._rpc('host.get', params) or []

    def host_create(self, params: dict) -> dict:
        return self._rpc('host.create', params) or {}

    def host_update(self, params: dict) -> dict:
        return self._rpc('host.update', params) or {}

    def host_delete(self, hostids: list[str]) -> dict:
        return self._rpc('host.delete', hostids) or {}


def get_default_zabbix_server() -> ZabbixServer | None:
    return (
        ZabbixServer.objects.filter(is_active=True, is_default=True).first()
        or ZabbixServer.objects.filter(is_active=True).order_by('id').first()
    )
