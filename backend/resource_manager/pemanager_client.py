"""
PE Manager / vpopmanager 节点 API 客户端（依据 deploy/API.md）。

部署完成后，POP 节点上运行 vpopmanager 面板，对外暴露统一 REST API。
本客户端用节点 API 基址 + 长期 Token（pem_*）或账号密码登录 JWT，然后拉取
接口 / 路由 / 策略 / IP / 带宽等系统现场数据，供 ISPmanager 同步入库。

注意（各端点的真实返回结构，参考节点后端源码）：
  - GET /api/interfacemanage/interfaces/      → { summary, count, interfaces: [...], sources }
  - GET /api/routemanage/system-routes/?table=all → { ok, table, routes: [...] }
  - GET /api/routemanage/system-rules/        → { ok, rules: [...] }
  - GET /api/resourcemanage/ip-addresses/     → [ ... ]（无分页，纯数组）
  - GET /api/resourcemanage/bandwidth-allocations/ → [ ... ]
  - GET /api/resourcemanage/customers/        → [ ... ]
  - GET /api/resourcemanage/bandwidth-pools/  → [ ... ]

因此不能简单地把响应当成列表使用，必须按 key 解包。早期实现遗漏了这点，
导致接口 / 路由 / 策略路由实际从未同步成功。

节点 apply / reconfigure 可能 HTTP 200 但未真正改本机（写入开关关闭、
warning、qos.applied=false）。调用方应使用 interpret_node_write() 区分
rest_ok / apply_ok，不要只看 HTTP 状态。
"""

import ipaddress
from urllib.parse import urlparse

import requests
import urllib3


def host_is_ip_address(base_url: str) -> bool:
    host = urlparse(base_url or '').hostname or ''
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def settings_ssl_verify_default() -> bool:
    try:
        from django.conf import settings
        return bool(getattr(settings, 'PEMANAGER_SSL_VERIFY', True))
    except Exception:  # noqa: BLE001
        return True


def resolve_pemanager_ssl_verify(base_url, *, explicit=None, pop=None) -> bool:
    """决定请求节点时是否校验 HTTPS 证书。

    优先级：调用方显式 ``verify`` > POP ``api_verify_ssl`` >
    HTTPS + IP（自签常见，默认不校验）> 全局 ``PEMANAGER_SSL_VERIFY``。
    """
    if explicit is not None:
        return bool(explicit)
    if pop is not None and hasattr(pop, 'api_verify_ssl'):
        return bool(pop.api_verify_ssl)
    if str(base_url or '').lower().startswith('https://') and host_is_ip_address(base_url):
        return False
    return settings_ssl_verify_default()


class PemanagerError(Exception):
    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload if isinstance(payload, dict) else {}


def _unique_error_texts(items):
    seen = []
    out = []
    for item in items or []:
        text = str(item or '').strip()
        if text and text not in seen:
            seen.append(text)
            out.append(text)
    return out


def _failed_step_texts(steps):
    out = []
    if not isinstance(steps, list):
        return out
    for step in steps:
        if not isinstance(step, dict) or step.get('ok') is not False:
            continue
        for key in ('stderr', 'error', 'detail', 'note'):
            val = step.get(key)
            if val:
                out.append(str(val).strip())
                break
    return out


def extract_node_error_detail(data, *, limit=800) -> str:
    """展开节点 4xx 体中的 error / failed_result / phases / steps。"""
    if not isinstance(data, dict):
        return (str(data)[:limit] if data else '')
    parts = []
    for key in ('error', 'detail'):
        val = data.get(key)
        if val not in (None, '', [], {}):
            parts.append(str(val).strip())
    failed = data.get('failed_result')
    if isinstance(failed, dict):
        if failed.get('error'):
            parts.append(str(failed['error']).strip())
        parts.extend(_failed_step_texts(failed.get('steps')))
    parts.extend(_failed_step_texts(data.get('steps')))
    for phase in data.get('phases') or []:
        if not isinstance(phase, dict):
            continue
        if phase.get('ok') is False and phase.get('error'):
            parts.append(str(phase['error']).strip())
        parts.extend(_failed_step_texts(phase.get('steps')))
        nested = phase.get('result') or phase.get('failed_result')
        if isinstance(nested, dict):
            if nested.get('error'):
                parts.append(str(nested['error']).strip())
            parts.extend(_failed_step_texts(nested.get('steps')))
    return '；'.join(_unique_error_texts(parts))[:limit]


_WRITE_DISABLED_MARKERS = (
    'WRITE_ENABLED',
    'APPLY_ENABLED',
    '写入已关闭',
    '写入 netplan 已关闭',
    '路由 netplan 写入已关闭',
    '策略路由下发已关闭',
    'WG 路由下发已关闭',
    '系统写入未启用',
    '系统写入开关',
)


def interpret_node_write(data):
    """解析节点写操作 JSON，区分 REST 已收到与系统是否真正 apply。

    调用方只应传入 HTTP 2xx 的响应体。HTTP 4xx/5xx 由客户端抛 PemanagerError。

    返回 {rest_ok, apply_ok, errors, steps}：
      - rest_ok：本次传入的是可解析的成功响应体（始终 True，除非 data 无法识别）
      - apply_ok：节点确认生效（ok 未否定、无写入开关关闭、steps/QoS 无失败）
    """
    if data is None:
        return {'rest_ok': True, 'apply_ok': True, 'errors': [], 'steps': []}
    if not isinstance(data, dict):
        return {'rest_ok': True, 'apply_ok': True, 'errors': [], 'steps': []}

    errors = []
    steps = data.get('steps') if isinstance(data.get('steps'), list) else []
    for step in steps:
        if isinstance(step, dict) and step.get('ok') is False:
            err = step.get('error') or step.get('stderr') or step.get('reason') or '步骤失败'
            errors.append(str(err).strip())

    if data.get('ok') is False:
        detail = data.get('error') or data.get('detail') or data.get('warning') or '节点未确认生效'
        if isinstance(detail, (list, dict)):
            detail = str(detail)
        detail = str(detail).strip()
        if detail and detail not in errors:
            errors.append(detail)

    warning = str(data.get('warning') or '')
    blob = ' '.join([
        warning,
        str(data.get('error') or ''),
        str(data.get('detail') or ''),
    ])
    disabled = any(marker in blob for marker in _WRITE_DISABLED_MARKERS)
    if disabled:
        hint = warning or data.get('error') or '节点写入开关已关闭，系统未变更'
        hint = str(hint).strip()
        if hint not in errors:
            errors.append(hint)

    qos = data.get('qos')
    if isinstance(qos, dict):
        if qos.get('applied') is False or qos.get('ok') is False or qos.get('error'):
            qerr = qos.get('error') or 'QoS 未 applied'
            qerr = str(qerr).strip()
            if qerr not in errors:
                errors.append(qerr)

    apply_ok = (
        data.get('ok') is not False
        and not disabled
        and not any(isinstance(s, dict) and s.get('ok') is False for s in steps)
        and not (
            isinstance(qos, dict)
            and (qos.get('applied') is False or qos.get('ok') is False or qos.get('error'))
        )
    )
    return {
        'rest_ok': True,
        'apply_ok': bool(apply_ok),
        'errors': errors,
        'steps': steps,
    }


def pop_has_api_config(pop) -> bool:
    """POP 是否具备节点 API 基址，以及 Token 或账号密码之一。"""
    try:
        PemanagerClient.from_pop(pop)
        return True
    except PemanagerError:
        return False


class PemanagerClient:
    def __init__(self, base_url, username, password, timeout=20, verify=None, api_token=None):
        if not base_url:
            raise PemanagerError('节点 API 基址为空')
        self.base = base_url.rstrip('/')
        self.username = username or ''
        self.password = password or ''
        self.api_token = (api_token or '').strip() or None
        self.timeout = timeout
        self.verify = resolve_pemanager_ssl_verify(self.base, explicit=verify)
        if self.verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self._token = None
        self._refresh = None
        if self.api_token:
            self._apply_bearer(self.api_token)

    def _apply_bearer(self, token):
        self._token = token
        self.session.headers['Authorization'] = f'Bearer {token}'

    @classmethod
    def from_pop(cls, pop, timeout=20, verify=None):
        """从 PopPoint 构造客户端：Token 优先，账号密码回退 api_* / mgmt_*。"""
        name = getattr(pop, 'name', '?')
        base = (getattr(pop, 'api_base_url', None) or '').strip()
        if not base:
            mgmt_ip = getattr(pop, 'mgmt_ip', None)
            if mgmt_ip:
                base = f'http://{mgmt_ip}'
        if not base:
            raise PemanagerError(f'POP「{name}」未配置 API 基址（api_base_url / mgmt_ip）')
        username = getattr(pop, 'api_username', None) or getattr(pop, 'mgmt_username', None) or ''
        password = getattr(pop, 'api_password', None) or getattr(pop, 'mgmt_password', None) or ''
        token = (getattr(pop, 'mgmt_token', None) or '').strip()
        if not token and (not username or not password):
            raise PemanagerError(f'POP「{name}」未配置 API Token 或账号密码')
        resolved = resolve_pemanager_ssl_verify(base, explicit=verify, pop=pop)
        return cls(base, username, password, timeout=timeout, verify=resolved, api_token=token or None)

    @classmethod
    def connect_pop(cls, pop, timeout=20, verify=None):
        """from_pop + authenticate，返回 (client, user_or_auth_hint)。"""
        client = cls.from_pop(pop, timeout=timeout, verify=verify)
        user = client.authenticate()
        return client, user

    # ── 鉴权 ──────────────────────────────────────────────────
    def authenticate(self):
        """有长期 Token（pem_* 等）则直接带 Bearer；否则走 JWT login。"""
        if self.api_token:
            self._apply_bearer(self.api_token)
            return {'auth': 'token'}
        return self.login()

    def websocket_auth_modes(self):
        """控制台 WebSocket 可用的节点身份，顺序与管理端控制台一致。

        已部署的节点控制台 WebSocket 只认登录 JWT，不认 pem_ 长期 Token，
        所以有账号密码时先登录，Token 只作为后备。
        """
        modes = []
        if self.username and self.password:
            modes.append('login')
        if self.api_token:
            modes.append('token')
        return modes

    def authenticate_for_websocket(self, mode=None):
        mode = mode or (self.websocket_auth_modes() or [None])[0]
        if mode == 'login':
            return self.login()
        if mode == 'token':
            self._apply_bearer(self.api_token)
            return {'auth': 'token'}
        raise PemanagerError('节点未配置控制台账号或 Token')

    def login(self):
        if not self.username or not self.password:
            raise PemanagerError('节点登录缺少用户名或密码')
        url = f'{self.base}/api/accountmanage/auth/login/'
        try:
            r = self.session.post(
                url, json={'username': self.username, 'password': self.password},
                timeout=self.timeout, verify=self.verify,
            )
        except requests.RequestException as e:
            detail = str(e)
            hint = ''
            if 'CERTIFICATE_VERIFY_FAILED' in detail or 'SSLCertVerificationError' in type(e).__name__:
                hint = (
                    '。节点面板多为自签证书，请在 POP 编辑中关闭「校验节点 HTTPS 证书」后重试'
                )
            raise PemanagerError(f'连接节点失败（{self.base}）：{e}{hint}')
        if r.status_code != 200:
            raise PemanagerError(f'节点登录失败（HTTP {r.status_code}）：{r.text[:200]}')
        data = r.json()
        self._token = data.get('access')
        if not self._token:
            raise PemanagerError('节点登录未返回 access token')
        self._refresh = data.get('refresh') or None
        self._apply_bearer(self._token)
        return data.get('user') or {}

    def refresh_access(self):
        """用 refresh 换新的 access。长期 Token 或没有 refresh 时返回 False。"""
        if self.api_token or not self._refresh:
            return False
        url = f'{self.base}/api/accountmanage/auth/refresh/'
        try:
            r = self.session.post(
                url, json={'refresh': self._refresh},
                timeout=self.timeout, verify=self.verify,
            )
        except requests.RequestException:
            return False
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except ValueError:
            return False
        access = data.get('access')
        if not access:
            return False
        if data.get('refresh'):
            self._refresh = data['refresh']
        self._apply_bearer(access)
        return True

    def _recover_auth(self):
        """access 过期后先 refresh；失败且有账号密码则重新 login。"""
        if self.api_token:
            return False
        if self.refresh_access():
            return True
        if self.username and self.password:
            try:
                self.login()
                return True
            except PemanagerError:
                return False
        return False

    def _should_recover_auth(self, path):
        if self.api_token:
            return False
        path = path or ''
        if path.endswith('/auth/login/') or path.endswith('/auth/refresh/'):
            return False
        return True

    # ── 通用 GET ──────────────────────────────────────────────
    def _get_raw(self, path, params=None, _retried=False):
        """返回原始 JSON（dict 或 list），404 → None。"""
        url = f'{self.base}{path}'
        try:
            r = self.session.get(url, params=params, timeout=self.timeout, verify=self.verify)
        except requests.RequestException as e:
            raise PemanagerError(f'请求 {path} 失败：{e}')
        if r.status_code == 401 and not _retried and self._should_recover_auth(path):
            if self._recover_auth():
                return self._get_raw(path, params=params, _retried=True)
        if r.status_code == 404:
            return None
        if r.status_code == 403:
            raise PemanagerError(f'请求 {path} 无权限（HTTP 403），请使用管理员账号同步')
        if r.status_code != 200:
            raise PemanagerError(f'请求 {path} 失败（HTTP {r.status_code}）')
        try:
            return r.json()
        except ValueError:
            return None

    def _get_list(self, path, params=None, key=None):
        """
        统一把响应解包成 list：
          - 若响应是 list，直接返回
          - 若是分页 dict（含 results），返回 results
          - 若指定 key 且响应是 dict，返回 data[key]
        """
        data = self._get_raw(path, params=params)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if key and isinstance(data.get(key), list):
                return data[key]
            if isinstance(data.get('results'), list):
                return data['results']
        return []

    # ── 健康检查 ──────────────────────────────────────────────
    def health(self):
        return self._get_raw('/api/accountmanage/health/')

    # ── 接口管理 ──────────────────────────────────────────────
    def interfaces(self):
        """系统现场接口（kernel + netplan + wg 合并），返回接口数组。"""
        return self._get_list('/api/interfacemanage/interfaces/', key='interfaces')

    # ── 路由管理 ──────────────────────────────────────────────
    def system_routes(self, table='all'):
        """
        内核路由表。默认 table=all 抓取所有路由表（含 main/local/自定义表），
        每条 route 带有 `table` 字段，便于按表归类。
        """
        params = {'table': table} if table else None
        return self._get_list('/api/routemanage/system-routes/', params=params, key='routes')

    def system_rules(self):
        """内核 ip rule 策略路由，返回 rules 数组。"""
        return self._get_list('/api/routemanage/system-rules/', key='rules')

    # ── 资源管理 ──────────────────────────────────────────────
    def ip_addresses(self):
        """受管 IP 资源（含 state/customer/subnet_label）。"""
        return self._get_list('/api/resourcemanage/ip-addresses/')

    def bulk_create_ips(self, payload):
        return self._post('/api/resourcemanage/ip-addresses/actions/bulk-create/', payload)

    def allocate_ip(self, payload):
        return self._post('/api/resourcemanage/ip-addresses/actions/allocate/', payload)

    def allocate_ip_with_route(self, payload):
        return self._post('/api/resourcemanage/ip-addresses/actions/allocate-with-route/', payload)

    def release_ip(self, payload):
        return self._post('/api/resourcemanage/ip-addresses/actions/release/', payload)

    def reserve_ip(self, payload):
        return self._post('/api/resourcemanage/ip-addresses/actions/reserve/', payload)

    def delete_desired_route(self, route_id):
        return self._delete(f'/api/routemanage/desired-routes/{route_id}/')

    def bandwidth_allocations(self):
        return self._get_list('/api/resourcemanage/bandwidth-allocations/')

    def bandwidth_pools(self):
        return self._get_list('/api/resourcemanage/bandwidth-pools/')

    def tunnel_ip_pools(self):
        """隧道 p2p 地址段池（TunnelIPPool，含 cidr / 统计字段）。"""
        return self._get_list('/api/resourcemanage/tunnel-ip-pools/')

    def tunnel_ip_pool_usage(self, pool_id):
        """单个隧道地址段池的使用详情（含 used p2p 子网/条目）。"""
        return self._get_raw(f'/api/resourcemanage/tunnel-ip-pools/{pool_id}/usage/')

    def create_tunnel_ip_pool(self, payload):
        """在节点侧创建隧道 p2p 地址段池。"""
        return self._post('/api/resourcemanage/tunnel-ip-pools/', payload)

    def patch_tunnel_ip_pool(self, pool_id, payload):
        return self._patch(f'/api/resourcemanage/tunnel-ip-pools/{pool_id}/', payload)

    def delete_tunnel_ip_pool(self, pool_id):
        return self._delete(f'/api/resourcemanage/tunnel-ip-pools/{pool_id}/')

    def customers(self):
        return self._get_list('/api/resourcemanage/customers/')

    def create_customer(self, payload):
        return self._post('/api/resourcemanage/customers/', payload)

    def update_customer(self, customer_id, payload):
        return self._patch(f'/api/resourcemanage/customers/{customer_id}/', payload)

    # ── 节点监控对象（边缘链路遥测） ───────────────────────────
    def edge_quality_series(self, edge_id, hours=24, interface_name=''):
        """读取节点品质监控里这条边缘加速的采样，以及对应监控图表和接口流量。

        旧节点没有该接口时返回 None。
        """
        params = {'central_edge_id': edge_id, 'hours': hours}
        interface_name = (interface_name or '').strip()
        if interface_name:
            params['interface'] = interface_name
        return self._get_raw(
            '/api/operationmanage/edge-quality/',
            params=params,
        )

    def monitor_target_series(self, target_id, bucket='hour', since=None, until=None):
        """运维监控图表：某个监控目标的延迟 / 丢包曲线。"""
        params = {'bucket': bucket}
        if since:
            params['since'] = since
        if until:
            params['until'] = until
        return self._get_raw(
            f'/api/operationmanage/monitor-targets/{target_id}/series/',
            params=params,
        )

    def interface_traffic_series(self, interface_name, bucket='hour', since=None, until=None):
        """运维监控图表：某个接口的流量曲线。"""
        params = {'interface': interface_name, 'bucket': bucket}
        if since:
            params['since'] = since
        if until:
            params['until'] = until
        return self._get_raw('/api/operationmanage/traffic-series/', params=params)

    def monitor_targets(self):
        """节点侧 ping/mtr 监控目标列表。"""
        return self._get_list('/api/operationmanage/monitor-targets/')

    def create_monitor_target(self, payload):
        return self._post('/api/operationmanage/monitor-targets/', payload)

    def patch_monitor_target(self, target_id, payload):
        return self._patch(f'/api/operationmanage/monitor-targets/{target_id}/', payload)

    def monitor_interfaces(self):
        """节点侧接口监控对象列表（独立于 MonitorTarget）。"""
        return self._get_list('/api/operationmanage/monitor-interfaces/')

    def create_monitor_interface(self, payload):
        return self._post('/api/operationmanage/monitor-interfaces/', payload)

    def patch_monitor_interface(self, interface_id, payload):
        return self._patch(
            f'/api/operationmanage/monitor-interfaces/{interface_id}/',
            payload,
        )

    # ── PVE 虚机（vpopmanager 专有，pemanager 无此模块则 404 → []） ──
    def vms(self):
        """PVE 虚拟机列表（pvemanager）。节点无该模块时返回空列表。"""
        return self._get_list('/api/pvemanager/vms/')

    # ── 通用 POST（下发 / 写操作） ────────────────────────────
    def _post(self, path, payload=None, timeout=None):
        return self._write('post', path, payload, timeout=timeout)

    def _put(self, path, payload=None, timeout=None):
        return self._write('put', path, payload, timeout=timeout)

    def _patch(self, path, payload=None, timeout=None):
        return self._write('patch', path, payload, timeout=timeout)

    def _delete(self, path, timeout=None):
        return self._write('delete', path, None, timeout=timeout)

    def _write(self, method, path, payload, _retried=False, timeout=None):
        url = f'{self.base}{path}'
        fn = getattr(self.session, method)
        req_timeout = self.timeout if timeout is None else timeout
        try:
            if method == 'delete':
                r = fn(url, timeout=req_timeout, verify=self.verify)
            else:
                r = fn(url, json=payload or {}, timeout=req_timeout, verify=self.verify)
        except requests.RequestException as e:
            raise PemanagerError(f'请求 {path} 失败：{e}')
        if r.status_code == 401 and not _retried and self._should_recover_auth(path):
            if self._recover_auth():
                return self._write(method, path, payload, _retried=True, timeout=timeout)
        if r.status_code == 403:
            raise PemanagerError(f'操作 {path} 无权限（HTTP 403），请使用管理员账号')
        if r.status_code == 204:
            return {}
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code not in (200, 201, 202):
            detail = extract_node_error_detail(data) if isinstance(data, dict) else ''
            if not detail:
                detail = (getattr(r, 'text', '') or str(data)[:200])[:200]
            raise PemanagerError(
                f'操作 {path} 失败（HTTP {r.status_code}）：{detail}',
                payload=data if isinstance(data, dict) else {},
            )
        return data

    # ── 静态路由意图（下发） ──────────────────────────────────
    def list_desired_routes(self):
        return self._get_list('/api/routemanage/desired-routes/')

    def create_desired_route(self, payload):
        return self._post('/api/routemanage/desired-routes/', payload)

    def patch_desired_route(self, route_id, payload):
        return self._patch(f'/api/routemanage/desired-routes/{route_id}/', payload)

    def apply_desired_routes(self, ids=None, phase='full', persist_to_netplan=False):
        body = {'phase': phase}
        if ids is not None:
            body['ids'] = ids
        if persist_to_netplan:
            body['persist_to_netplan'] = True
        return self._post('/api/routemanage/desired-routes/apply-system/', body)

    def apply_wireguard_routes(self, ids=None):
        """WireGuard 路由：写入 wg conf PostUp/PostDown 并 wg-quick down/up。"""
        body = {}
        if ids is not None:
            body['ids'] = ids
        return self._post('/api/routemanage/desired-routes/apply-wireguard/', body)

    # ── 策略路由意图（下发） ──────────────────────────────────
    def list_policy_rules(self):
        return self._get_list('/api/routemanage/policy-rules/')

    def create_policy_rule(self, payload):
        return self._post('/api/routemanage/policy-rules/', payload)

    def patch_policy_rule(self, rule_id, payload):
        return self._patch(f'/api/routemanage/policy-rules/{rule_id}/', payload)

    def delete_policy_rule(self, rule_id):
        return self._delete(f'/api/routemanage/policy-rules/{rule_id}/')

    def apply_policy_rules(self, ids=None, phase='full'):
        body = {'phase': phase}
        if ids is not None:
            body['ids'] = ids
        return self._post('/api/routemanage/policy-rules/apply-system/', body)

    # ── 隧道意图（边缘加速下发 → desired-tunnels） ─────────────
    def list_desired_tunnels(self):
        return self._get_list('/api/interfacemanage/db/desired-tunnels/')

    def create_desired_tunnel(self, payload):
        return self._post('/api/interfacemanage/db/desired-tunnels/', payload)

    def patch_desired_tunnel(self, tunnel_id, payload):
        return self._patch(f'/api/interfacemanage/db/desired-tunnels/{tunnel_id}/', payload)

    def reconfigure_desired_tunnel(self, tunnel_id, payload):
        """隧道接口重配置（修改已存在接口专用）：
        节点先拆除旧接口 + 其关联路由 → 更新意图 → IP 变化时同步路由下一跳到新对端 →
        仅对该接口重建生效（GRE/VXLAN 走 netplan try 差量、WireGuard 走 wg-quick），
        不影响系统其他接口。body 同 desired-tunnel（kind/ifname/spec/customer/remark），
        可选 apply_system（默认 true）。"""
        return self._post(
            f'/api/interfacemanage/db/desired-tunnels/{tunnel_id}/reconfigure/', payload)

    def delete_desired_tunnel(self, tunnel_id):
        return self._delete(f'/api/interfacemanage/db/desired-tunnels/{tunnel_id}/')

    def remove_interface_config(self, ifname):
        """删除节点为该接口托管的 netplan 片段（apply-config 创建的普通接口）。"""
        return self._post(f'/api/interfacemanage/interfaces/{ifname}/remove-config/', {})

    def apply_interface_config(self, ifname, kind, spec):
        """写入单接口 netplan 片段并 netplan generate + try（对齐 vpop apply-config）。"""
        return self._post(
            f'/api/interfacemanage/interfaces/{ifname}/apply-config/',
            {'kind': kind, 'spec': spec or {}},
        )

    def apply_desired_tunnels(self, ids=None):
        body = {}
        if ids is not None:
            body['ids'] = ids
        return self._post('/api/interfacemanage/db/desired-tunnels/apply-system/', body)

    def wg_genkey(self):
        return self._post('/api/interfacemanage/tools/wg/genkey/', {})

    def wg_pubkey(self, private_key):
        return self._post('/api/interfacemanage/tools/wg/pubkey/', {'private': private_key})

    # ── 带宽（池 + 分配） ─────────────────────────────────────
    def list_bandwidth_pools(self):
        return self._get_list('/api/resourcemanage/bandwidth-pools/')

    def create_bandwidth_pool(self, payload):
        return self._post('/api/resourcemanage/bandwidth-pools/', payload)

    def upsert_bandwidth_allocation(self, payload):
        return self._post('/api/resourcemanage/bandwidth-allocations/actions/upsert/', payload)

    def delete_bandwidth_allocation(self, payload):
        return self._post('/api/resourcemanage/bandwidth-allocations/actions/delete-by-key/', payload)

    def allocate_vm_bandwidth(self, payload):
        return self._post('/api/pvemanager/vms/allocate_bandwidth/', payload)

    # ── PVE 虚机生命周期（vpopmanager pvemanager） ────────────
    def list_pve_hosts(self):
        return self._get_list('/api/pvemanager/hosts/')

    def list_nodes(self, host_id):
        # 节点 list_nodes 端点返回 {success, nodes:[...], default}，需按 nodes 解包
        return self._get_list('/api/pvemanager/vms/list_nodes/',
                              params={'host_id': host_id}, key='nodes')

    def list_templates(self):
        return self._get_list('/api/pvemanager/templates/')

    def download_template(self, payload, timeout=2100):
        data = dict(payload or {})
        data.setdefault('wait', True)
        data.setdefault('register_template', True)
        return self._post('/api/pvemanager/templates/download/', data, timeout=timeout)

    def host_storages(self, host_id, node=None):
        params = {'node': node} if node else None
        return self._get_list(f'/api/pvemanager/hosts/{host_id}/storages/', params=params)

    def host_storage_content(self, host_id, node, storage, content=None):
        params = {'node': node, 'storage': storage}
        if content:
            params['content'] = content
        return self._get_list(
            f'/api/pvemanager/hosts/{host_id}/storage_content/',
            params=params,
        )

    def host_backups(self, host_id, node, storage):
        return self._get_list(
            f'/api/pvemanager/hosts/{host_id}/backups/',
            params={'node': node, 'storage': storage},
        )

    def vm_detail(self, vm_pk):
        return self._get_raw(f'/api/pvemanager/vms/{vm_pk}/detail/')

    def sync_vms(self):
        return self._post('/api/pvemanager/vms/sync/', {})

    def vm_action(self, vm_pk, action, payload=None):
        """action ∈ start/stop/restart/shutdown"""
        return self._post(f'/api/pvemanager/vms/{vm_pk}/{action}/', payload or {})

    def create_vm(self, payload):
        return self._post('/api/pvemanager/vms/create_vm/', payload)

    def create_vm_from_template(self, payload):
        return self._post('/api/pvemanager/vms/create_from_template/', payload)

    def create_vm_from_backup(self, payload):
        return self._post('/api/pvemanager/vms/create_from_backup/', payload)

    def get_pve_job(self, job_id):
        data = self._get_raw(f'/api/pvemanager/vms/jobs/{job_id}/')
        if not isinstance(data, dict):
            raise PemanagerError(f'节点任务 {job_id} 不存在或响应无效')
        return data

    def wait_for_pve_job(self, job_id, timeout=2100, interval=2):
        """轮询节点异步 PVE 任务（备份恢复可能数分钟）。"""
        import time
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.get_pve_job(job_id)
            status = last.get('status')
            if status == 'succeeded':
                return last
            if status == 'failed':
                raise PemanagerError(last.get('error') or '节点创建虚机任务失败')
            time.sleep(interval)
        progress = last.get('progress') or ''
        raise PemanagerError(f'等待节点创建任务超时{("：" + progress) if progress else ""}')

    def clone_vm(self, vm_pk, payload):
        return self._post(f'/api/pvemanager/vms/{vm_pk}/clone/', payload)

    def delete_vm(self, vm_pk):
        return self._delete(f'/api/pvemanager/vms/{vm_pk}/')

    def vnc_ticket(self, vm_pk):
        """获取节点 PVE 虚机的 VNC 票据（触发节点上的 vncproxy，30 秒内须建立 WS）。"""
        return self._get_raw(f'/api/pvemanager/vms/{vm_pk}/vnc_ticket/')

    def update_vm_hardware(self, vm_pk, payload):
        return self._put(f'/api/pvemanager/vms/{vm_pk}/update_hardware/', payload)

    def update_vm_cloud_init(self, vm_pk, payload):
        return self._put(f'/api/pvemanager/vms/{vm_pk}/cloud_init/', payload)

    # ── VPS 编排（pvemanager /vps/，中台开通与变更首选） ──
    def vps_os_catalog(self, host_id=None):
        params = {'host_id': host_id} if host_id else None
        data = self._get_raw('/api/pvemanager/vps/os-catalog/', params=params)
        if isinstance(data, dict) and isinstance(data.get('items'), list):
            return data['items']
        if isinstance(data, list):
            return data
        return []

    def vps_next_vmid(self, host_id):
        data = self._get_raw('/api/pvemanager/vps/next-vmid/', params={'host_id': host_id})
        return data if isinstance(data, dict) else {}

    def vps_provision(self, payload, timeout=2100):
        return self._post('/api/pvemanager/vps/provision/', payload, timeout=timeout)

    def vps_resize(self, vm_pk, payload):
        return self._put(f'/api/pvemanager/vps/{vm_pk}/resize/', payload)

    def vps_credentials(self, vm_pk, payload):
        return self._put(f'/api/pvemanager/vps/{vm_pk}/credentials/', payload)

    def vps_network(self, vm_pk, payload):
        return self._put(f'/api/pvemanager/vps/{vm_pk}/network/', payload)

    def vps_reinstall(self, vm_pk, payload, timeout=2100):
        return self._post(f'/api/pvemanager/vps/{vm_pk}/reinstall/', payload, timeout=timeout)

    def vps_allocate_ip(self, vm_pk, payload):
        return self._post(f'/api/pvemanager/vps/{vm_pk}/allocate-ip/', payload)

    def vps_release_ip(self, vm_pk, payload):
        return self._post(f'/api/pvemanager/vps/{vm_pk}/release-ip/', payload)

    # ── WebSocket 中继辅助 ────────────────────────────────────
    @property
    def token(self):
        """节点登录后获得的 JWT access token（用于节点 WebSocket 鉴权）。"""
        return self._token

    def node_vnc_ws_url(self, vm_pk, vncticket=None, port=None):
        """
        构造节点 noVNC WebSocket 地址：
          wss://<节点host>/ws/pvemanager/vnc/<vm_pk>/?token=<节点JWT>&vncticket=...&port=...
        节点已把 /ws/pvemanager/vnc/ 暴露在与 API 同一 host 上。
        """
        from urllib.parse import urlsplit, urlencode
        parts = urlsplit(self.base)
        scheme = 'wss' if parts.scheme == 'https' else 'ws'
        netloc = parts.netloc or parts.path  # base 形如 http://host:port
        qs = {}
        token = self._token or ''
        if token:
            qs['token'] = token
        if vncticket:
            qs['vncticket'] = vncticket
        if port is not None:
            qs['port'] = str(port)
        return f'{scheme}://{netloc}/ws/pvemanager/vnc/{vm_pk}/?{urlencode(qs)}'

    def console_websocket_url(self, vm_pk, vncticket, port):
        """与 vpopmanager 控制台页面相同的地址。浏览器 noVNC 直接连接，不再经管理端转发。"""
        if self.username and self.password:
            self.login()
        elif not (self._token or self.api_token):
            raise PemanagerError('节点未配置控制台账号或 Token')
        else:
            self._token = self._token or self.api_token
        if not self._token:
            raise PemanagerError('节点未返回控制台登录令牌')
        return self.node_vnc_ws_url(vm_pk, vncticket=vncticket, port=port)

    def node_ws_auth_headers(self):
        """节点同时认查询参数和 Authorization。头里再带一次，避免票据把 URL 撑满后令牌被截断。"""
        token = self._token or self.api_token or ''
        if not token:
            return {}
        return {'Authorization': f'Bearer {token}'}
