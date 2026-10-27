"""
配置下发服务：把 ISPmanager 中维护的 POP 点配置下发到节点 vpopmanager，
实现「中心定义、节点执行」的统一管理。

当前支持下发：
  - 静态路由   StaticRoute     → 节点 desired-routes + apply-system（ip route replace 即时生效）
  - 策略路由   PolicyRoute     → 节点 policy-rules + apply-system
  - 边缘隧道   EdgeAcceleration → 节点 desired-tunnels + apply-system（GRE / VXLAN / WireGuard）
  - 带宽限速   VPS 带宽         → 节点 VM 网卡 rate（allocate_bandwidth）

幂等策略：
  下发前先拉取节点已有的 desired-* 记录，按业务键匹配；已存在的复用其 id，
  不存在的才创建，最后用 apply-system{ids} 选择性下发，避免重复推送脏数据。

dry-run（默认开启）：
  仅计算「将创建 / 将下发」的计划并返回，不实际写节点；前端二次确认后再带
  dry_run=False 真正执行。

不下发：netplan 物理接口、IP 分配、Socks5（节点无对应隧道通道）、PVE 虚机生命周期
（虚机生命周期由 pve_ops 单独处理）。
"""

import ipaddress
import re

from django.utils import timezone

from .models import (
    PopPoint, StaticRoute, PolicyRoute, EdgeAcceleration, VPS,
)
from .pemanager_client import (
    PemanagerClient, PemanagerError, interpret_node_write, pop_has_api_config,
)


# 接口类型 → netplan 设备类
_DEV_CLASS_MAP = {
    'physical': 'ethernets',
    'vlan': 'vlans',
    'bond': 'bonds',
    'bridge': 'bridges',
    'tunnel': 'tunnels',
}


def _dev_class(interface):
    if interface is None:
        return 'ethernets'
    return _DEV_CLASS_MAP.get(interface.interface_type, 'ethernets')


def _is_v6(*values):
    return any(':' in str(v) for v in values if v)


# ═══════════════════════════════════════════════════════════════
# 静态路由下发
# ═══════════════════════════════════════════════════════════════

def _push_static_routes(pop, client, dry_run=True):
    routes = list(
        StaticRoute.objects
        .filter(route_table__pop_point=pop, is_active=True)
        .select_related('route_table', 'interface')
    )
    if not routes:
        return {'pushed': 0, 'created': 0, 'applied': 0, 'skipped': 0,
                'note': '无可下发的静态路由', 'plan': []}

    # 节点已有 desired-routes 索引（记录 id 与当前 on_link，便于纠正漂移）
    existing = client.list_desired_routes()
    index = {}
    for r in existing or []:
        if not isinstance(r, dict):
            continue
        key = (
            str(r.get('interface_name') or ''),
            str(r.get('dest_cidr') or ''),
            str(r.get('gateway') or ''),
        )
        if r.get('id'):
            index[key] = {'id': r['id'], 'on_link': bool(r.get('on_link'))}

    ids = []
    plan = []
    created = patched = skipped = 0
    for rt in routes:
        if not rt.interface or not rt.interface.ifname:
            skipped += 1  # 节点 desired-route 必须指定出接口
            continue
        ifname = rt.interface.ifname
        gateway = rt.gateway or ''
        table_id = rt.route_table.table_id if rt.route_table else None
        # on_link 判定（避免内核 "Nexthop has invalid gateway"）：
        #  - 无网关：on_link=True（直连/设备路由）
        #  - 有网关且出接口是隧道（GRE/VXLAN/WireGuard 等 PtP）：网关在对端，须 on-link
        #  - 有网关且为默认路由（0.0.0.0/0、::/0、default）：按 on-link 处理（桥接/云环境常态，
        #    且接口可能尚未就绪，强制 on-link 保证路由可装入）
        #  - 其余普通接口路由：on_link=False，交由节点按直连子网/自动 onlink 判断
        is_tunnel = getattr(rt.interface, 'interface_type', None) == 'tunnel'
        is_default = str(rt.destination).strip().lower() in ('0.0.0.0/0', '::/0', 'default')
        on_link = (not rt.gateway) or (bool(rt.gateway) and (is_tunnel or is_default))

        key = (ifname, rt.destination, str(gateway))
        rec = index.get(key)
        node_id = rec['id'] if rec else None
        if rec is not None:
            # 复用：若 on_link 漂移则纠正（修复历史 on_link=False 导致的下发失败）
            action_label = '复用'
            if on_link and not rec['on_link']:
                action_label = '纠正on-link'
                if not dry_run:
                    client.patch_desired_route(node_id, {'on_link': True})
                    patched += 1
            plan.append({'dest': rt.destination, 'via': rt.gateway or 'on-link',
                         'dev': ifname, 'action': action_label})
        else:
            plan.append({'dest': rt.destination, 'via': rt.gateway or 'on-link',
                         'dev': ifname, 'action': '新建'})
            if dry_run:
                created += 1
                continue
            payload = {
                'interface_name': ifname,
                'netplan_device_class': _dev_class(rt.interface),
                'dest_cidr': rt.destination,
                'gateway': rt.gateway or None,
                'on_link': on_link,
                'metric': rt.metric or None,
                'route_table': table_id if table_id and table_id != 254 else None,
                'remark': 'ISPmanager 下发',
            }
            obj = client.create_desired_route(payload)
            node_id = obj.get('id') if isinstance(obj, dict) else None
            if node_id:
                created += 1
        if node_id:
            ids.append(node_id)

    if dry_run:
        return {'dry_run': True, 'would_push': len(plan), 'would_create': created,
                'skipped': skipped, 'plan': plan}

    applied = 0
    apply_result = {}
    if ids:
        apply_result = client.apply_desired_routes(ids=ids, phase='full')
        applied = len(apply_result.get('applied') or ids)
    interp = interpret_node_write(apply_result)

    return {
        'pushed': len(ids), 'created': created, 'patched': patched, 'applied': applied,
        'skipped': skipped, 'ok': interp['apply_ok'],
        'rest_ok': interp['rest_ok'], 'apply_ok': interp['apply_ok'],
        'apply_errors': interp['errors'], 'apply_interp': interp,
    }


# ═══════════════════════════════════════════════════════════════
# 策略路由下发
# ═══════════════════════════════════════════════════════════════

def _push_policy_rules(pop, client, dry_run=True):
    rules = list(
        PolicyRoute.objects
        .filter(pop_point=pop, is_active=True)
        .select_related('lookup_table')
    )
    if not rules:
        return {'pushed': 0, 'created': 0, 'applied': 0, 'skipped': 0,
                'note': '无可下发的策略路由', 'plan': []}

    existing = client.list_policy_rules()
    index = {}
    for r in existing or []:
        if not isinstance(r, dict):
            continue
        key = (
            r.get('priority'),
            str(r.get('family') or 'inet'),
            str(r.get('from_cidr') or ''),
            str(r.get('to_cidr') or ''),
        )
        if r.get('id'):
            index[key] = r['id']

    ids = []
    plan = []
    created = skipped = 0
    skipped_reasons = {}
    for pr in rules:
        family = 'inet6' if _is_v6(pr.src_prefix, pr.dst_prefix) else 'inet'
        from_cidr = pr.src_prefix or ''
        to_cidr = pr.dst_prefix or ''
        fwmark = (pr.fwmark or '').strip()
        key = (pr.priority, family, from_cidr, to_cidr)
        node_id = index.get(key)
        table_id = pr.lookup_table.table_id if pr.lookup_table else None
        # 节点要求每条 ip rule 至少有一个匹配条件（from/to/fwmark/iif/oif）。
        # 无匹配条件的多为内核默认规则（from all lookup local/main/default，优先级 0/32766/32767），
        # 不应、也无法下发到节点（否则节点校验失败返回 500）。
        if not (from_cidr or to_cidr or fwmark):
            skipped += 1
            skipped_reasons.setdefault('无匹配条件(内核默认规则)', 0)
            skipped_reasons['无匹配条件(内核默认规则)'] += 1
            continue
        if pr.action == 'lookup' and not table_id:
            skipped += 1  # lookup 必须有查找表
            skipped_reasons.setdefault('lookup 缺少查找表', 0)
            skipped_reasons['lookup 缺少查找表'] += 1
            continue
        plan.append({'priority': pr.priority, 'family': family,
                     'from': from_cidr or 'all', 'to': to_cidr or 'all',
                     'action': pr.action, 'table': table_id,
                     'state': '复用' if node_id else '新建'})
        if node_id is None:
            if dry_run:
                created += 1
                continue
            payload = {
                'priority': pr.priority,
                'family': family,
                'from_cidr': from_cidr,
                'to_cidr': to_cidr,
                'fwmark': fwmark,
                'action': pr.action,
                'table_id': table_id,
                'enabled': True,
                'remark': 'ISPmanager 下发',
            }
            obj = client.create_policy_rule(payload)
            node_id = obj.get('id') if isinstance(obj, dict) else None
            if node_id:
                created += 1
        if node_id:
            ids.append(node_id)

    if dry_run:
        return {'dry_run': True, 'would_push': len(plan), 'would_create': created,
                'skipped': skipped, 'skipped_reasons': skipped_reasons, 'plan': plan}

    applied = 0
    apply_result = {}
    if ids:
        apply_result = client.apply_policy_rules(ids=ids, phase='full')
        applied = int(apply_result.get('added') or 0)
    interp = interpret_node_write(apply_result)

    return {
        'pushed': len(ids), 'created': created, 'applied': applied,
        'skipped': skipped, 'skipped_reasons': skipped_reasons,
        'ok': interp['apply_ok'],
        'rest_ok': interp['rest_ok'], 'apply_ok': interp['apply_ok'],
        'apply_errors': interp['errors'], 'apply_interp': interp,
    }


def _static_route_pop(rt):
    table = getattr(rt, 'route_table', None)
    return getattr(table, 'pop_point', None) if table else None


def _static_route_on_link(rt):
    is_tunnel = getattr(rt.interface, 'interface_type', None) == 'tunnel'
    is_default = str(rt.destination or '').strip().lower() in (
        '0.0.0.0/0', '::/0', 'default',
    )
    return (not rt.gateway) or (bool(rt.gateway) and (is_tunnel or is_default))


def _match_desired_route(existing, rt):
    ifname = (getattr(rt.interface, 'ifname', None) or '').strip()
    key = (ifname, str(rt.destination or ''), str(rt.gateway or ''))
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        row_key = (
            str(row.get('interface_name') or ''),
            str(row.get('dest_cidr') or ''),
            str(row.get('gateway') or ''),
        )
        if row_key == key and row.get('id'):
            return row
    return None


def _match_policy_rule(existing, pr):
    family = 'inet6' if _is_v6(pr.src_prefix, pr.dst_prefix) else 'inet'
    key = (pr.priority, family, str(pr.src_prefix or ''), str(pr.dst_prefix or ''))
    for row in existing or []:
        if not isinstance(row, dict):
            continue
        row_key = (
            row.get('priority'),
            str(row.get('family') or 'inet'),
            str(row.get('from_cidr') or ''),
            str(row.get('to_cidr') or ''),
        )
        if row_key == key and row.get('id'):
            return row
    return None


def push_static_route_to_node(rt):
    """把一条中心静态路由下发到节点。无 API / 无出接口则跳过。"""
    pop = _static_route_pop(rt)
    if pop is None:
        return {'ok': True, 'skipped': True, 'detail': '路由未关联 POP 点'}
    if not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True, 'detail': 'POP 未配置节点 API'}
    if not rt.is_active:
        return remove_static_route_from_node(rt)
    if not rt.interface or not (rt.interface.ifname or '').strip():
        return {'ok': True, 'skipped': True, 'detail': '静态路由未选出接口，仅保存本地'}
    try:
        client, _user = PemanagerClient.connect_pop(pop)
        existing = client.list_desired_routes()
        rec = _match_desired_route(existing, rt)
        table_id = rt.route_table.table_id if rt.route_table else None
        payload = {
            'interface_name': rt.interface.ifname.strip(),
            'netplan_device_class': _dev_class(rt.interface),
            'dest_cidr': rt.destination,
            'gateway': rt.gateway or None,
            'on_link': _static_route_on_link(rt),
            'metric': rt.metric or None,
            'route_table': table_id if table_id and table_id != 254 else None,
            'remark': 'ISPmanager 下发',
        }
        if rec:
            result = client.patch_desired_route(rec['id'], payload)
            node_id = rec['id']
        else:
            result = client.create_desired_route(payload)
            node_id = result.get('id') if isinstance(result, dict) else None
        if not node_id:
            return {'ok': False, 'detail': '节点未返回路由 id', 'result': result}
        apply_result = client.apply_desired_routes(ids=[node_id], phase='full')
        interp = interpret_node_write(apply_result)
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'result': apply_result,
            'detail': (
                f'静态路由 {rt.destination} 已下发'
                if interp['apply_ok']
                else f'静态路由 {rt.destination} 已写入节点但未确认生效'
            ),
        }
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


def remove_static_route_from_node(rt):
    """删除节点侧匹配的 desired-route，并全量 apply 以撤内核路由。"""
    pop = _static_route_pop(rt)
    if pop is None or not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True}
    if not rt.interface or not (rt.interface.ifname or '').strip():
        return {'ok': True, 'skipped': True, 'detail': '无出接口，无法匹配节点路由'}
    try:
        client, _user = PemanagerClient.connect_pop(pop)
        rec = _match_desired_route(client.list_desired_routes(), rt)
        if rec is None:
            return {'ok': True, 'skipped': True, 'detail': '节点无对应静态路由'}
        try:
            client.delete_desired_route(rec['id'])
        except PemanagerError as exc:
            if '不存在' in str(exc):
                return {'ok': True, 'skipped': True, 'detail': str(exc)}
            return {'ok': False, 'detail': str(exc)}
        apply_result = client.apply_desired_routes(phase='full')
        interp = interpret_node_write(apply_result)
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'detail': (
                f'已删除节点静态路由 {rt.destination}'
                if interp['apply_ok']
                else f'节点静态路由已删库但未确认生效'
            ),
        }
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


def push_policy_route_to_node(pr):
    """把一条中心策略路由下发到节点。无匹配条件 / 无 API 则跳过。"""
    pop = getattr(pr, 'pop_point', None)
    if pop is None:
        return {'ok': True, 'skipped': True, 'detail': '策略路由未关联 POP 点'}
    if not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True, 'detail': 'POP 未配置节点 API'}
    if not pr.is_active:
        return remove_policy_route_from_node(pr)
    from_cidr = pr.src_prefix or ''
    to_cidr = pr.dst_prefix or ''
    fwmark = (pr.fwmark or '').strip()
    if not (from_cidr or to_cidr or fwmark):
        return {'ok': True, 'skipped': True, 'detail': '策略路由无匹配条件，仅保存本地'}
    table_id = pr.lookup_table.table_id if pr.lookup_table else None
    if pr.action == 'lookup' and not table_id:
        return {'ok': True, 'skipped': True, 'detail': 'lookup 缺少查找表，仅保存本地'}
    try:
        client, _user = PemanagerClient.connect_pop(pop)
        rec = _match_policy_rule(client.list_policy_rules(), pr)
        family = 'inet6' if _is_v6(pr.src_prefix, pr.dst_prefix) else 'inet'
        payload = {
            'priority': pr.priority,
            'family': family,
            'from_cidr': from_cidr,
            'to_cidr': to_cidr,
            'fwmark': fwmark,
            'action': pr.action,
            'table_id': table_id,
            'enabled': True,
            'remark': 'ISPmanager 下发',
        }
        if rec:
            result = client.patch_policy_rule(rec['id'], payload)
            node_id = rec['id']
        else:
            result = client.create_policy_rule(payload)
            node_id = result.get('id') if isinstance(result, dict) else None
        if not node_id:
            return {'ok': False, 'detail': '节点未返回策略路由 id', 'result': result}
        apply_result = client.apply_policy_rules(ids=[node_id], phase='full')
        interp = interpret_node_write(apply_result)
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'result': apply_result,
            'detail': (
                f'策略路由 prio {pr.priority} 已下发'
                if interp['apply_ok']
                else f'策略路由 prio {pr.priority} 已写入节点但未确认生效'
            ),
        }
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


def remove_policy_route_from_node(pr):
    """删除节点侧匹配的 policy-rule，并全量 apply 以撤 ip rule。"""
    pop = getattr(pr, 'pop_point', None)
    if pop is None or not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True}
    try:
        client, _user = PemanagerClient.connect_pop(pop)
        rec = _match_policy_rule(client.list_policy_rules(), pr)
        if rec is None:
            return {'ok': True, 'skipped': True, 'detail': '节点无对应策略路由'}
        try:
            client.delete_policy_rule(rec['id'])
        except PemanagerError as exc:
            if '不存在' in str(exc):
                return {'ok': True, 'skipped': True, 'detail': str(exc)}
            return {'ok': False, 'detail': str(exc)}
        apply_result = client.apply_policy_rules(phase='full')
        interp = interpret_node_write(apply_result)
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'detail': (
                f'已删除节点策略路由 prio {pr.priority}'
                if interp['apply_ok']
                else '节点策略路由已删库但未确认生效'
            ),
        }
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


# ═══════════════════════════════════════════════════════════════
# 边缘隧道下发（GRE / WireGuard → desired-tunnels）
# ═══════════════════════════════════════════════════════════════

_IFNAME_PREFIX = {
    EdgeAcceleration.Protocol.WIREGUARD: 'wg',
    EdgeAcceleration.Protocol.GRE: 'gre',
    EdgeAcceleration.Protocol.VXLAN: 'vxlan',
}


def _replaced_protocol_ifnames(edge):
    """切换协议后，旧协议按主键生成的接口名需要先从节点删除。"""
    current = f'{_IFNAME_PREFIX.get(edge.protocol, "tun")}{edge.pk}'[:15]
    names = []
    for proto, prefix in _IFNAME_PREFIX.items():
        if proto == edge.protocol:
            continue
        stale = f'{prefix}{edge.pk}'[:15]
        if stale != current:
            names.append(stale)
    return names


def _edge_ifname(edge):
    """节点隧道接口名：优先用已回填的 node_ifname，否则按协议+主键生成（<=15 字符）。"""
    if edge.node_ifname:
        return edge.node_ifname[:15]
    prefix = _IFNAME_PREFIX.get(edge.protocol, 'tun')
    return f'{prefix}{edge.pk}'[:15]


def _split_csv(value):
    """把逗号/空白分隔的字符串拆成去空列表。"""
    if not value:
        return []
    return [s.strip() for s in re.split(r'[,\s]+', str(value)) if s.strip()]


def _maybe_int(value):
    """GRE key 等：数字字符串转 int，否则原样返回。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return value


def _infer_p2p_peer_ip(address_cidr: str):
    """从 PtP addresses 推断对端 IP（与 ip_ops / vpop peer_ip 一致）。"""
    import ipaddress

    if not address_cidr:
        return None
    raw = str(address_cidr).strip()
    if '/' not in raw:
        raw = f'{raw}/32'
    try:
        iface = ipaddress.ip_interface(raw)
    except ValueError:
        return None
    net = iface.network
    local = iface.ip
    try:
        if net.prefixlen in (31, 127):
            cands = [net[0], net[1]]
        elif net.prefixlen in (30, 126):
            cands = list(net.hosts())
        else:
            return None
    except ValueError:
        return None
    for c in cands:
        if c != local:
            return str(c)
    return None


def _normalize_wg_peer_endpoint(endpoint: str, listen_port: int) -> str:
    """纯 IP 时补全 :listen_port，满足节点 wg-quick 校验。"""
    ep = (endpoint or '').strip()
    if not ep:
        return ''
    if ep.startswith('[') and ']:' in ep:
        return ep
    if ep.count(':') == 1:
        _, port_part = ep.rsplit(':', 1)
        if port_part.isdigit():
            return ep
    return f'{ep}:{listen_port or 51820}'


def _wg_peer_allowed_ips(interface_ip: str, explicit: str) -> list[str]:
    """PtP 未填 allowed_ips 时默认对端 /32；避免误用 0.0.0.0/0 或 /30 与本端冲突。"""
    parts = _split_csv(explicit)
    if parts:
        return parts
    addrs = _split_csv(interface_ip)
    if addrs:
        peer = _infer_p2p_peer_ip(addrs[0])
        if peer:
            return [f'{peer}/32']
    return ['0.0.0.0/0']


def wireguard_allowed_ip_list(edge) -> list[str]:
    """WireGuard 的业务路由就是对端 AllowedIPs，不再另建静态路由。"""
    wg = getattr(edge, 'wg_config', None)
    if wg is None:
        return []
    explicit = _split_csv(getattr(wg, 'peer_allowed_ips', ''))
    if explicit:
        return explicit
    dests = []
    if getattr(edge, 'pk', None):
        from .ip_ops import _edge_business_dests
        dests = sorted(_edge_business_dests(edge))
    if dests:
        return dests
    return _wg_peer_allowed_ips(getattr(wg, 'interface_ip', ''), '')


def _tunnel_spec_unchanged(node_obj, kind, spec) -> bool:
    """节点已存的隧道意图与本次将下发的 (kind, spec) 是否完全一致。
    完全一致说明本接口无改动，可跳过 reconfigure，避免无谓的接口重建/抖动。"""
    if not isinstance(node_obj, dict):
        return False
    if str(node_obj.get('kind') or '') != str(kind or ''):
        return False
    import json
    try:
        a = json.dumps(node_obj.get('spec'), sort_keys=True, ensure_ascii=False)
        b = json.dumps(spec, sort_keys=True, ensure_ascii=False)
        return a == b
    except TypeError:
        return node_obj.get('spec') == spec


def _build_tunnel_spec(edge):
    """
    把 EdgeAcceleration 协议配置映射为节点 desired-tunnel spec，返回 (kind, spec) 或 None。
    spec 结构与 vpopmanager DesiredTunnelConfig 完全一致：以 spec.netplan_tunnel 为唯一配置源。
    """
    if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        wg = getattr(edge, 'wg_config', None)
        if wg is None:
            return None
        if not wg.interface_ip or not wg.private_key or not wg.peer_public_key:
            return None
        peer = {}
        if wg.peer_public_key:
            peer['public_key'] = wg.peer_public_key
            peer['allowed_ips'] = wireguard_allowed_ip_list(edge)
            listen_port = wg.listen_port or 51820
            if wg.peer_endpoint:
                peer['endpoint'] = _normalize_wg_peer_endpoint(wg.peer_endpoint, listen_port)
            if wg.persistent_keepalive:
                peer['persistent_keepalive'] = wg.persistent_keepalive
            if wg.peer_preshared_key:
                peer['preshared_key'] = wg.peer_preshared_key
        netplan_tunnel = {
            'mode': 'wireguard',
            'addresses': _split_csv(wg.interface_ip),
            'keys': {'private': wg.private_key or '', 'public': wg.public_key or ''},
            'listen_port': wg.listen_port or 51820,
        }
        if wg.fwmark:
            netplan_tunnel['fwmark'] = wg.fwmark
        netplan_tunnel['mtu'] = int(wg.mtu or 1400)
        if wg.dns:
            netplan_tunnel['dns'] = _split_csv(wg.dns)
        if peer:
            netplan_tunnel['peers'] = [peer]
        return 'wireguard', {'type': 'wireguard', 'netplan_tunnel': netplan_tunnel}

    if edge.protocol == EdgeAcceleration.Protocol.GRE:
        gre = getattr(edge, 'gre_config', None)
        if gre is None:
            return None
        if not gre.local_ip or not gre.remote_ip or not gre.tunnel_ip:
            return None
        netplan_tunnel = {
            'mode': 'gre',
            'local': gre.local_ip,
            'remote': gre.remote_ip,
            'addresses': [gre.tunnel_ip] if gre.tunnel_ip else [],
        }
        # ttl 为 netplan 版本相关的可选键（部分节点 netplan 不识别 'ttl'）；
        # 仅在显式配置时下发，避免无意义的默认值导致 netplan generate 失败。
        if gre.ttl:
            netplan_tunnel['ttl'] = gre.ttl
        if gre.key not in (None, ''):
            netplan_tunnel['key'] = _maybe_int(gre.key)
        netplan_tunnel['mtu'] = int(gre.mtu or 1400)
        return 'gre', {'type': 'gre', 'netplan_tunnel': netplan_tunnel}

    if edge.protocol == EdgeAcceleration.Protocol.VXLAN:
        vx = getattr(edge, 'vxlan_config', None)
        if vx is None:
            return None
        netplan_tunnel = {
            'mode': 'vxlan',
            'id': vx.vni,
            'addresses': [vx.tunnel_ip] if vx.tunnel_ip else [],
        }
        if vx.port:
            netplan_tunnel['port'] = vx.port
        if vx.ttl:
            netplan_tunnel['ttl'] = vx.ttl
        if vx.local_ip:
            netplan_tunnel['local'] = vx.local_ip
        if vx.remote_ip:
            netplan_tunnel['remote'] = vx.remote_ip
        netplan_tunnel['mtu'] = int(vx.mtu or 1400)
        return 'vxlan', {'type': 'vxlan', 'netplan_tunnel': netplan_tunnel}

    return None


def _wireguard_peer_host(peer_endpoint: str) -> str:
    """从 WireGuard endpoint 提取不带端口/方括号的探测主机。"""
    endpoint = (peer_endpoint or '').strip()
    if not endpoint:
        return ''
    if endpoint.startswith('['):
        closing = endpoint.find(']')
        if closing > 1:
            return endpoint[1:closing]
        return ''
    # hostname:port / IPv4:port；未加括号的 IPv6 不试图剥离端口，以免截断地址。
    if endpoint.count(':') == 1:
        host, port = endpoint.rsplit(':', 1)
        if host and port.isdigit():
            return host
    return endpoint


def _edge_telemetry_target_address(edge) -> str:
    """按隧道协议解析节点品质探测的对端地址。"""
    if edge.protocol == EdgeAcceleration.Protocol.GRE:
        return str(getattr(getattr(edge, 'gre_config', None), 'remote_ip', '') or '').strip()
    if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        endpoint = getattr(getattr(edge, 'wg_config', None), 'peer_endpoint', '')
        return _wireguard_peer_host(endpoint)
    if edge.protocol == EdgeAcceleration.Protocol.VXLAN:
        return str(getattr(getattr(edge, 'vxlan_config', None), 'remote_ip', '') or '').strip()
    return ''


def _sync_edge_telemetry(client, edge, ifname: str) -> dict:
    """
    将成功下发的边缘隧道映射到节点 MonitorTarget / MonitorInterface。

    监控对象同步是交付后的附加动作：异常由调用方收集，绝不影响隧道的创建、重配或
    已有接口复用。MonitorInterface 只用于节点流量监控，不是 Target source_interface
    的前置条件。
    """
    ifname = (ifname or '').strip()
    target_name = f'isp-edge-quality-{edge.pk}'
    target_address = _edge_telemetry_target_address(edge)
    customer_code = _customer_code(edge.customer)
    result = {
        'edge_id': edge.pk,
        'ifname': ifname,
        'target_address': target_address,
        'target_action': 'skipped',
        'interface_action': 'skipped',
        'errors': [],
    }
    if not ifname:
        result['errors'].append('节点接口名为空，无法同步遥测监控对象')
        return result

    target = None
    try:
        for item in client.monitor_targets() or []:
            if isinstance(item, dict) and str(item.get('name') or '') == target_name:
                target = item
                break
    except Exception as exc:  # noqa: BLE001
        result['errors'].append(f'读取 MonitorTarget 失败：{exc}')

    # MonitorInterface 与品质 Target 独立维护，缺失对端时仍可采集该隧道的流量。
    interface_payload = {
        'interface_name': ifname,
        'enabled': True,
        'interval_sec': 15,
        'customer': customer_code,
        'remark': f'ISPmanager 边缘接口 {edge.edge_no}',
    }
    try:
        monitor_interface = None
        for item in client.monitor_interfaces() or []:
            if isinstance(item, dict) and str(item.get('interface_name') or '') == ifname:
                monitor_interface = item
                break
        if monitor_interface and monitor_interface.get('id') is not None:
            client.patch_monitor_interface(monitor_interface['id'], interface_payload)
            result['interface_action'] = 'updated'
        else:
            client.create_monitor_interface(interface_payload)
            result['interface_action'] = 'created'
    except Exception as exc:  # noqa: BLE001
        result['errors'].append(f'同步 MonitorInterface 失败：{exc}')

    if not target_address:
        # 协议不支持或未设置可解析对端：只停用既有 target，避免旧地址继续上报。
        if target and target.get('id') is not None:
            try:
                client.patch_monitor_target(target['id'], {
                    'enabled': False,
                    'telemetry_enabled': False,
                    'source_interface': ifname,
                    'customer': customer_code,
                    'central_edge_id': edge.pk,
                    'remark': f'ISPmanager 边缘遥测已停用 {edge.edge_no}',
                })
                result['target_action'] = 'disabled'
            except Exception as exc:  # noqa: BLE001
                result['errors'].append(f'停用 MonitorTarget 失败：{exc}')
        else:
            result['target_action'] = 'not_created_no_peer'
        return result

    target_payload = {
        'name': target_name,
        'address': target_address,
        'kind': 'ping',
        'interval_sec': 60,
        'count': 5,
        'source_interface': ifname,
        'customer': customer_code,
        'enabled': True,
        'telemetry_enabled': True,
        'central_edge_id': edge.pk,
        'remark': f'ISPmanager 边缘质量遥测 {edge.edge_no}',
    }
    try:
        if target and target.get('id') is not None:
            client.patch_monitor_target(target['id'], target_payload)
            result['target_action'] = 'updated'
        else:
            client.create_monitor_target(target_payload)
            result['target_action'] = 'created'
    except Exception as exc:  # noqa: BLE001
        result['errors'].append(f'同步 MonitorTarget 失败：{exc}')
    return result


def _disable_orphaned_edge_telemetry_targets(client, edges) -> dict:
    """
    停用已经不对应当前 POP Edge 的节点探针。

    Edge 删除或迁移后，旧 POP 的下一次 edge_tunnels 同步也会执行本清理，避免节点继续
    探测并上传已回收接口。只处理中心命名空间内的目标，绝不影响人工创建的 MonitorTarget。
    """
    expected_names = {f'isp-edge-quality-{edge.pk}' for edge in edges}
    result = {'disabled': [], 'errors': []}
    try:
        targets = client.monitor_targets() or []
    except Exception as exc:  # noqa: BLE001 - 清理失败不可阻断隧道下发
        result['errors'].append(f'读取 MonitorTarget 失败：{exc}')
        return result

    for target in targets:
        if not isinstance(target, dict):
            continue
        name = str(target.get('name') or '')
        target_id = target.get('id')
        if (
            not name.startswith('isp-edge-quality-')
            or name in expected_names
            or target_id is None
        ):
            continue
        try:
            client.patch_monitor_target(target_id, {
                'enabled': False,
                'telemetry_enabled': False,
                'remark': 'ISPmanager：关联边缘接口已回收或迁移，遥测已停用',
            })
            result['disabled'].append(name)
        except Exception as exc:  # noqa: BLE001
            result['errors'].append(f'停用 {name} 失败：{exc}')
    return result


# 接口类型 → 节点 apply-config kind
_IFACE_KIND_MAP = {
    'physical': 'ether',
    'vlan': 'vlan',
    'bond': 'bond',
    'bridge': 'bridge',
    'loopback': 'ether',
    'tunnel': 'gre',
    'dummy': 'ether',
}


def push_interface_to_node(pop, interface):
    """
    将 NetworkInterface.netplan_config 下发到节点（apply-config）。
    interface 需有 ifname 与 netplan_config={kind, spec}。
    """
    ifname = (interface.ifname or '').strip()
    cfg = interface.netplan_config if isinstance(interface.netplan_config, dict) else {}
    kind = cfg.get('kind') or _IFACE_KIND_MAP.get(interface.interface_type, 'ether')
    spec = cfg.get('spec') if isinstance(cfg.get('spec'), dict) else {}
    if not ifname:
        return {'ok': False, 'detail': '接口名为空'}
    if not spec and interface.interface_type not in ('tunnel',):
        return {'ok': False, 'detail': '未配置 netplan spec，无法下发'}

    client, _user = PemanagerClient.connect_pop(pop)

    # 隧道类接口走 desired-tunnels（若 spec 含 netplan_tunnel）
    if kind in ('gre', 'vxlan', 'wireguard') or spec.get('netplan_tunnel'):
        tunnel_spec = spec.get('netplan_tunnel') or spec
        payload = {
            'ifname': ifname,
            'kind': kind if kind in ('gre', 'vxlan', 'wireguard') else 'gre',
            'spec': {'type': kind, 'netplan_tunnel': tunnel_spec},
            'remark': interface.description or 'ISPmanager 下发',
        }
        existing = client.list_desired_tunnels() or []
        node_id = None
        for t in existing:
            if isinstance(t, dict) and str(t.get('ifname')) == ifname:
                node_id = t.get('id')
                break
        if node_id:
            client.patch_desired_tunnel(node_id, payload)
        else:
            client.create_desired_tunnel(payload)
        apply_result = client.apply_desired_tunnels()
        interp = interpret_node_write(apply_result)
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'detail': (
                f'隧道 {ifname} 已下发' if interp['apply_ok']
                else f'隧道 {ifname} 已写入节点但未确认生效'
            ),
            'apply': apply_result,
        }

    # 补齐 MTU
    if interface.mtu and 'mtu' not in spec:
        spec = {**spec, 'mtu': interface.mtu}
    result = client.apply_interface_config(ifname, kind, spec)
    interp = interpret_node_write(result)
    return {
        'ok': interp['apply_ok'],
        'rest_ok': interp['rest_ok'],
        'apply_ok': interp['apply_ok'],
        'apply_errors': interp['errors'],
        'detail': (
            f'接口 {ifname} 配置已下发' if interp['apply_ok']
            else f'接口 {ifname} 已写入节点但未确认生效'
        ),
        'apply': result,
    }


def push_vps_bandwidth_to_node(vps):
    """将单台同步 VPS 的带宽下发到节点 VM 网卡（net0 rate）。"""
    if not vps.is_synced or not vps.source_ref:
        return {'ok': False, 'detail': '仅支持已同步且有关联节点 VM 的 VPS'}
    if not vps.bandwidth_mbps or vps.bandwidth_mbps <= 0:
        return {'ok': False, 'detail': '带宽必须大于 0'}
    pop = vps.pop_point
    if pop is None:
        return {'ok': False, 'detail': 'VPS 未关联 POP 点'}

    client, _user = PemanagerClient.connect_pop(pop)

    pool_name = _resolve_pool_name(pop, client, dry_run=False)
    payload = {
        'pool_name': pool_name,
        'vm_id': vps.source_ref,
        'net_index': 0,
        'allocated_mbps': vps.bandwidth_mbps,
        'remark': f'ISPmanager 下发 {vps.name}',
    }
    cust_code = _customer_code(vps.customer)
    if cust_code:
        payload['customer_code'] = cust_code
    result = client.allocate_vm_bandwidth(payload)
    interp = interpret_node_write(result)
    return {
        'ok': interp['apply_ok'],
        'rest_ok': interp['rest_ok'],
        'apply_ok': interp['apply_ok'],
        'apply_errors': interp['errors'],
        'detail': (
            f'VPS {vps.name} 带宽 {vps.bandwidth_mbps}Mbps 已下发'
            if interp['apply_ok']
            else f'VPS {vps.name} 带宽已写入节点但未确认生效'
        ),
        'result': result,
    }


def push_bandwidth_allocation_to_node(alloc):
    """把一条中心带宽分配下发到节点（VPS → PVE rate；边缘 → QoS）。

    未同步 VPS / 未下发边缘只跳过，不报失败，避免挡住本地建档。
    """
    pop = alloc.pop_point
    if pop is None:
        return {'ok': False, 'detail': '带宽分配未关联 POP 点'}
    if not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True, 'detail': 'POP 未配置节点 API'}

    if alloc.vps_id and alloc.vps:
        vps = alloc.vps
        if not vps.is_synced or not vps.source_ref:
            return {'ok': True, 'skipped': True, 'detail': 'VPS 未同步到节点，仅保存本地'}
        if alloc.allocated_mbps:
            if vps.bandwidth_mbps != alloc.allocated_mbps:
                vps.bandwidth_mbps = alloc.allocated_mbps
                vps.save(update_fields=['bandwidth_mbps', 'updated_at'])
        return push_vps_bandwidth_to_node(vps)

    if alloc.edge_id and alloc.edge:
        edge = alloc.edge
        ifname = (edge.node_ifname or '').strip()
        if not ifname:
            return {'ok': True, 'skipped': True, 'detail': '边缘尚未下发到节点，仅保存本地'}
        if not alloc.allocated_mbps or alloc.allocated_mbps <= 0:
            return {'ok': False, 'detail': '带宽必须大于 0'}
        client, _user = PemanagerClient.connect_pop(pop)
        pool_name = _resolve_pool_name(pop, client, dry_run=False)
        payload = {
            'pool_name': pool_name,
            'interface_code': ifname,
            'allocated_mbps': alloc.allocated_mbps,
            'remark': f'ISPmanager 带宽分配 {alloc.pk}',
            'apply_qos': True,
            'qos_direction': 'egress',
            'qos_root_kind': 'htb',
        }
        cust = _customer_code(edge.customer)
        if cust:
            payload['customer_code'] = cust
        result = client.upsert_bandwidth_allocation(payload)
        interp = interpret_node_write(result)
        qos = result.get('qos') if isinstance(result, dict) else None
        if isinstance(qos, dict):
            interp = interpret_node_write({'ok': result.get('ok', True), 'qos': qos})
        return {
            'ok': interp['apply_ok'],
            'rest_ok': interp['rest_ok'],
            'apply_ok': interp['apply_ok'],
            'apply_errors': interp['errors'],
            'result': result,
            'detail': (
                f'边缘 {ifname} 带宽 {alloc.allocated_mbps}Mbps 已下发'
                if interp['apply_ok']
                else f'边缘 {ifname} 带宽已写入节点但未确认生效'
            ),
        }

    return {'ok': False, 'detail': '带宽分配缺少 VPS 或边缘目标'}


def remove_bandwidth_allocation_from_node(alloc):
    """删除节点侧对应带宽分配（按接口编码）。无接口或无 API 则跳过。"""
    pop = alloc.pop_point
    if pop is None or not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True}
    if_code = ''
    if alloc.vps_id and alloc.vps and alloc.vps.source_ref:
        if_code = f'vm-{alloc.vps.source_ref}-net0'
    elif alloc.edge_id and alloc.edge:
        if_code = (alloc.edge.node_ifname or '').strip()
    if not if_code:
        return {'ok': True, 'skipped': True, 'detail': '无节点接口编码'}
    client, _user = PemanagerClient.connect_pop(pop)
    pool_name = _resolve_pool_name(pop, client, dry_run=True)
    try:
        client.delete_bandwidth_allocation({
            'pool_name': pool_name,
            'interface_code': if_code,
        })
    except PemanagerError as exc:
        if '不存在' in str(exc):
            return {'ok': True, 'skipped': True, 'detail': str(exc)}
        return {'ok': False, 'detail': str(exc)}
    return {'ok': True, 'detail': f'已删除节点带宽 {if_code}'}


def push_tunnel_ip_pool_to_node(pool, dry_run=True):
    """把 ISPmanager 中的隧道 IP 地址段（TunnelIPPool）下发到所属 POP 节点。

    - 节点侧按 name 幂等 upsert：已存在同名池则 PATCH，否则创建；
    - 归属客户按 customer_no → 节点 ResourceCustomer.code 传递（节点无该客户则不绑定，公共池）；
    - 成功后回写 pool.source_ref / is_synced / last_synced_at。
    dry_run=True 时仅返回将要下发的 payload，不实际写节点。
    """
    from .models import TunnelIPPool  # 避免循环导入

    pop = pool.pop_point
    if pop is None:
        return {'ok': False, 'detail': '地址段未关联 POP 点'}
    if not dry_run and not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True, 'detail': 'POP 未配置节点 API'}

    payload = {
        'name': pool.name,
        'cidr': pool.cidr,
        'point_prefixlen': int(pool.point_prefixlen or 30),
        'is_active': bool(pool.is_active),
        'remark': pool.remark or 'ISPmanager 下发',
    }
    cust_code = _customer_code(pool.customer)
    if cust_code:
        payload['customer'] = cust_code

    if dry_run:
        return {'ok': True, 'dry_run': True, 'payload': payload,
                'detail': f'将下发隧道地址段 {pool.name}（{pool.cidr}）到节点 {pop.name}'}

    try:
        client, _user = PemanagerClient.connect_pop(pop)

        # 先确保客户存在（节点无该客户时创建，避免 SlugRelatedField 400）
        if cust_code:
            try:
                existing_cust = {
                    str(c.get('code')): c
                    for c in (client.customers() or []) if isinstance(c, dict)
                }
                if cust_code not in existing_cust:
                    client.create_customer({
                        'code': cust_code,
                        'name': pool.customer.name if pool.customer else cust_code,
                    })
            except PemanagerError:
                # 客户同步失败则退回公共池，不阻断地址段下发
                payload.pop('customer', None)

        # 幂等 upsert：按 name 匹配节点已有池
        node_id = None
        for p in (client.tunnel_ip_pools() or []):
            if isinstance(p, dict) and str(p.get('name')) == pool.name:
                node_id = p.get('id')
                break
        if node_id is not None:
            result = client.patch_tunnel_ip_pool(node_id, payload)
        else:
            result = client.create_tunnel_ip_pool(payload)
            node_id = result.get('id') if isinstance(result, dict) else None

        from django.utils import timezone
        TunnelIPPool.objects.filter(pk=pool.pk).update(
            is_synced=True,
            source_ref=str(node_id or ''),
            last_synced_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return {
            'ok': True,
            'detail': f'隧道地址段 {pool.name} 已下发到节点 {pop.name}',
            'node_pool_id': node_id,
            'result': result,
        }
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


def remove_tunnel_ip_pool_from_node(pool):
    """删除节点侧同名 / source_ref 对应的隧道地址段。无 API 或找不到则跳过。"""
    pop = pool.pop_point
    if pop is None or not pop_has_api_config(pop):
        return {'ok': True, 'skipped': True}
    try:
        client, _user = PemanagerClient.connect_pop(pop)
        node_id = str(pool.source_ref or '').strip()
        if not node_id:
            for row in (client.tunnel_ip_pools() or []):
                if isinstance(row, dict) and str(row.get('name')) == pool.name:
                    node_id = str(row.get('id') or '')
                    break
        if not node_id:
            return {'ok': True, 'skipped': True, 'detail': '节点无对应隧道地址段'}
        try:
            client.delete_tunnel_ip_pool(node_id)
        except PemanagerError as exc:
            if '不存在' in str(exc):
                return {'ok': True, 'skipped': True, 'detail': str(exc)}
            return {'ok': False, 'detail': str(exc)}
        return {'ok': True, 'detail': f'已删除节点隧道地址段 {pool.name}'}
    except PemanagerError as exc:
        return {'ok': False, 'detail': str(exc)}


def delete_interface_on_node(pop, ifname, interface_type):
    """
    把 ISPmanager 中删除接口的操作传播到节点。
    - 隧道接口（gre/vxlan/wireguard/tunnel）：按 ifname 找到 desired-tunnel 并删除，
      再全量 apply-system，使节点 netplan 拆除该隧道（GRE/VXLAN 生效；WireGuard 为尽力而为）。
    - 普通接口（physical/vlan/bridge 等）：删除节点为其托管的 netplan 片段（remove-config）。
    返回 {ok, detail}；调用方负责异常处理。
    """
    client, _user = PemanagerClient.connect_pop(pop)

    ifname = (ifname or '').strip()
    if not ifname:
        return {'ok': False, 'detail': '接口名为空，无法在节点删除'}

    tunnel_kinds = {'gre', 'vxlan', 'wireguard', 'tunnel'}
    if interface_type in tunnel_kinds:
        existing = client.list_desired_tunnels() or []
        tid = None
        for t in existing:
            if isinstance(t, dict) and str(t.get('ifname')) == ifname:
                tid = t.get('id')
                break
        if tid is None:
            return {'ok': True, 'detail': f'节点无对应隧道意图 {ifname}，无需删除'}
        client.delete_desired_tunnel(tid)
        # 必须全量 apply-system（{}），GRE/VXLAN 才会从 netplan 拆除
        client.apply_desired_tunnels()
        return {'ok': True, 'detail': f'已删除节点隧道 {ifname} 并重建 netplan'}

    # 普通接口：删除 pemanager 托管的 netplan 片段
    client.remove_interface_config(ifname)
    return {'ok': True, 'detail': f'已删除节点接口配置 {ifname}'}


def _ip_family(value):
    raw = str(value or '').strip().lower()
    if not raw:
        return None
    if raw in {'default', '0.0.0.0/0', '0.0.0.0'}:
        return 4
    if raw in {'::/0', '::'}:
        return 6
    try:
        if '/' in raw:
            return ipaddress.ip_network(raw, strict=False).version
        return ipaddress.ip_address(raw).version
    except ValueError:
        return None


def route_dest_via_family_mismatch(route):
    if not isinstance(route, dict):
        return False
    dest_v = _ip_family(route.get('dest_cidr') or route.get('to'))
    via_v = _ip_family(route.get('gateway') or route.get('via'))
    return dest_v is not None and via_v is not None and dest_v != via_v


_CROSS_FAMILY_MARKERS = (
    'IP family mismatch',
    'netplan generate',
    '地址族',
)


def looks_like_cross_family_netplan(exc) -> bool:
    blob = str(exc or '')
    payload = getattr(exc, 'payload', None)
    if isinstance(payload, dict):
        blob = f'{blob} {payload}'
    return any(marker in blob for marker in _CROSS_FAMILY_MARKERS)


def repair_node_cross_family_routes(client) -> list[str]:
    """删除节点跨族路由意图，并重写 netplan 路由片段（reconfigure 的 generate 发生在 persist 之前）。"""
    repaired = []
    for route in client.list_desired_routes() or []:
        if not isinstance(route, dict) or not route.get('id'):
            continue
        if not route_dest_via_family_mismatch(route):
            continue
        try:
            client.delete_desired_route(route['id'])
            repaired.append(
                f"{route.get('interface_name')} {route.get('dest_cidr')} via {route.get('gateway')}"
            )
        except PemanagerError:
            continue
    if not repaired:
        return repaired
    remaining_ids = [
        route['id']
        for route in (client.list_desired_routes() or [])
        if isinstance(route, dict) and route.get('id')
    ]
    try:
        if remaining_ids:
            client.apply_desired_routes(
                ids=remaining_ids, phase='validate', persist_to_netplan=True,
            )
        else:
            client.apply_desired_routes(ids=None, phase='validate')
    except PemanagerError as persist_exc:
        raise PemanagerError(
            f'已删除跨族路由（{"；".join(repaired)}），但重写节点路由片段失败：{persist_exc}',
            payload=getattr(persist_exc, 'payload', None),
        ) from persist_exc
    return repaired


def reconfigure_tunnel_with_route_repair(client, node_id, payload):
    """reconfigure；若因跨族路由导致 netplan generate 失败，清理后重试一次。"""
    try:
        return client.reconfigure_desired_tunnel(node_id, payload)
    except PemanagerError as first:
        if not looks_like_cross_family_netplan(first):
            raise
        repaired = repair_node_cross_family_routes(client)
        if not repaired:
            raise
        return client.reconfigure_desired_tunnel(node_id, payload)


def _wireguard_tunnel_to_replace(edge, index_obj, current_ifname):
    """节点上仍存在的上一份 WireGuard 隧道。重配置这条记录才会 wg-quick down 并删除 conf。"""
    from .ip_ops import _previous_protocol_ifnames

    current = (current_ifname or '').strip()
    for name in _previous_protocol_ifnames(edge, current):
        obj = index_obj.get(name)
        if not isinstance(obj, dict) or not obj.get('id'):
            continue
        kind = str(obj.get('kind') or '').strip().lower()
        ifname = str(obj.get('ifname') or '').strip()
        if kind == 'wireguard' or ifname.startswith('wg'):
            return obj
    return None


def _drop_replaced_interface_extras(client, ifnames):
    """去掉旧接口上的带宽分配和流量监控，避免切到新协议后 wg 接口仍占着限速和采样。"""
    names = {str(name).strip() for name in (ifnames or []) if str(name or '').strip()}
    if not names:
        return
    monitors = client.monitor_interfaces() if hasattr(client, 'monitor_interfaces') else None
    if isinstance(monitors, list):
        for item in monitors:
            if not isinstance(item, dict):
                continue
            ifname = str(item.get('interface_name') or '').strip()
            if ifname not in names or item.get('id') is None:
                continue
            try:
                client.patch_monitor_interface(item['id'], {
                    'interface_name': ifname,
                    'enabled': False,
                    'remark': 'ISPmanager：协议已切换，旧接口监控已停用',
                })
            except PemanagerError:
                pass
    pools = client.list_bandwidth_pools() if hasattr(client, 'list_bandwidth_pools') else None
    if not isinstance(pools, list):
        return
    pool_name = ''
    for pool in pools:
        if isinstance(pool, dict) and pool.get('name'):
            pool_name = pool['name']
            break
    if not pool_name:
        return
    for ifname in names:
        try:
            client.delete_bandwidth_allocation({
                'pool_name': pool_name,
                'interface_code': ifname,
            })
        except PemanagerError:
            pass


def _push_edge_tunnels(pop, client, dry_run=True):
    edges = list(
        EdgeAcceleration.objects
        .filter(pop_point=pop)
        .select_related('gre_config', 'vxlan_config', 'wg_config', 'customer')
    )
    if not edges:
        if dry_run:
            return {
                'pushed': 0, 'created': 0, 'applied': 0, 'skipped': 0,
                'note': '无边缘加速节点', 'plan': [],
                'telemetry_cleanup': [],
            }
        cleanup = _disable_orphaned_edge_telemetry_targets(client, edges)
        return {
            'pushed': 0, 'created': 0, 'applied': 0, 'skipped': 0,
            'note': '无边缘加速节点',
            'telemetry_cleanup': cleanup['disabled'],
            'telemetry_errors': cleanup['errors'],
            'ok': True,
        }

    from .ip_ops import retire_previous_edge_interface

    reconfigure_errors = []
    retired = []
    for edge in edges:
        if edge.protocol == EdgeAcceleration.Protocol.SOCKS5:
            continue
        try:
            cleaned = retire_previous_edge_interface(
                client, edge, _edge_ifname(edge), dry_run=dry_run,
            )
        except PemanagerError as exc:
            reconfigure_errors.append(f'拆除旧协议失败：{exc}')
            continue
        retired.extend(cleaned.get('tunnels') or [])
        for route in cleaned.get('routes') or []:
            retired.append(f"{route.get('interface_name')} {route.get('dest_cidr')}")

    existing = client.list_desired_tunnels()
    index = {}
    index_obj = {}
    for t in existing or []:
        if isinstance(t, dict) and t.get('ifname') and t.get('id'):
            index[str(t['ifname'])] = t['id']
            index_obj[str(t['ifname'])] = t

    new_ids = []            # 新建隧道 → 批量 apply-system
    new_edges = []          # 新建但尚未确认 apply-system 成功的边缘
    plan = []
    applied_edges = []      # [(edge, ifname)] 下发成功、需回填状态/带宽的边缘
    created = skipped = 0
    reconfigured = unchanged = 0
    socks5 = 0
    for edge in edges:
        if edge.protocol == EdgeAcceleration.Protocol.SOCKS5:
            socks5 += 1
            skipped += 1
            continue
        built = _build_tunnel_spec(edge)
        if built is None:
            skipped += 1  # 缺少协议配置
            continue
        kind, spec = built
        ifname = _edge_ifname(edge)
        node_id = index.get(ifname)
        node_obj = index_obj.get(ifname)
        replaced_wg = _wireguard_tunnel_to_replace(edge, index_obj, ifname)
        if replaced_wg and node_id and str(node_id) == str(replaced_wg.get('id')):
            replaced_wg = None
        spec_changed = node_id is None or not _tunnel_spec_unchanged(node_obj, kind, spec)
        if replaced_wg:
            state = '重配置'
        elif node_id is None:
            state = '新建'
        elif spec_changed:
            state = '重配置'
        else:
            state = '无变化'
        plan.append({'edge_no': edge.edge_no, 'name': edge.name,
                     'protocol': edge.protocol, 'ifname': ifname, 'state': state})
        if dry_run:
            created += int(node_id is None and not replaced_wg)
            applied_edges.append((edge, ifname))
            continue

        payload = {
            'ifname': ifname,
            'kind': kind,
            'spec': spec,
            'remark': f'ISPmanager 边缘加速 {edge.edge_no}',
        }
        cust_code = _customer_code(edge.customer)
        if cust_code:
            payload['customer'] = cust_code  # 节点按 code 绑定 ResourceCustomer

        if replaced_wg:
            # 同一条意图从 WireGuard 改成当前协议。节点 reconfigure 会 wg-quick down、
            # 删除 /etc/wireguard/<旧接口>.conf，再创建新接口。
            old_ifname = str(replaced_wg.get('ifname') or '').strip()
            if node_id:
                try:
                    client.delete_desired_tunnel(node_id)
                except PemanagerError as exc:
                    reconfigure_errors.append(f'{ifname}: 删除占位隧道失败：{exc}')
                    continue
                index.pop(ifname, None)
                index_obj.pop(ifname, None)
            payload['apply_system'] = True
            try:
                rr = reconfigure_tunnel_with_route_repair(client, replaced_wg['id'], payload)
                interp_rr = interpret_node_write(rr)
                if not interp_rr['apply_ok'] or (isinstance(rr, dict) and rr.get('ok') is False):
                    detail = (
                        '；'.join(interp_rr['errors'])
                        or (rr.get('detail') or rr.get('error') or rr if isinstance(rr, dict) else rr)
                    )
                    reconfigure_errors.append(f'{ifname}: {detail}')
                else:
                    reconfigured += 1
                    applied_edges.append((edge, ifname))
                    _drop_replaced_interface_extras(client, [old_ifname])
                    index.pop(old_ifname, None)
                    index_obj.pop(old_ifname, None)
                    index[ifname] = replaced_wg['id']
                    index_obj[ifname] = {**replaced_wg, 'ifname': ifname, 'kind': kind}
            except PemanagerError as exc:
                reconfigure_errors.append(f'{ifname}: {exc}')
        elif node_id and not spec_changed:
            # 接口配置未变化：仅同步元数据（客户/备注），不触碰接口，避免无谓抖动。
            try:
                client.patch_desired_tunnel(node_id, payload)
            except PemanagerError:
                pass
            unchanged += 1
            applied_edges.append((edge, ifname))
        elif node_id:
            # 已存在接口且配置有变（前端修改后重新下发）：走 reconfigure —— 节点拆旧接口
            # + 其关联路由，更新意图后仅对该接口重建生效（GRE/VXLAN 走 netplan try 差量、
            # WireGuard 走 wg-quick，不影响系统其他接口），并在隧道 IP 变化时把引用该接口
            # 的路由下一跳/目标同步为新对端 IP。
            payload['apply_system'] = True
            try:
                rr = reconfigure_tunnel_with_route_repair(client, node_id, payload)
                interp_rr = interpret_node_write(rr)
                if not interp_rr['apply_ok'] or (isinstance(rr, dict) and rr.get('ok') is False):
                    detail = (
                        '；'.join(interp_rr['errors'])
                        or (rr.get('detail') or rr.get('error') or rr if isinstance(rr, dict) else rr)
                    )
                    reconfigure_errors.append(f'{ifname}: {detail}')
                else:
                    reconfigured += 1
                    applied_edges.append((edge, ifname))
            except PemanagerError as e:
                reconfigure_errors.append(f'{ifname}: {e}')
        else:
            obj = client.create_desired_tunnel(payload)
            node_id = obj.get('id') if isinstance(obj, dict) else None
            if node_id:
                created += 1
                new_ids.append(node_id)
                new_edges.append((edge, ifname))

    if dry_run:
        return {'dry_run': True, 'would_push': len(plan), 'would_create': created,
                'skipped': skipped, 'socks5_skipped': socks5, 'plan': plan}

    # 仅对「新建」隧道批量 apply-system；「已存在」隧道已在 reconfigure 内完成生效
    apply_result = {}
    if new_ids:
        apply_result = client.apply_desired_tunnels(ids=new_ids)
    apply_interp = interpret_node_write(apply_result)
    apply_ok = apply_interp['apply_ok']
    if apply_ok:
        # 新建意图只有在 apply-system 成功后才能视为已下发并配置遥测。
        applied_edges.extend(new_edges)

    # 回填下发状态 + 同步隧道接口带宽（interface_code = ifname，对齐 vpopmanager）
    now = timezone.now()
    bw_pool = None
    bw_pushed = 0
    qos_applied = 0
    bw_errors = []
    for edge, ifname in applied_edges:
        edge.node_pushed = True
        edge.is_synced = True  # 兼容别名
        edge.node_ifname = ifname
        edge.last_synced_at = now
        edge.save(update_fields=['node_pushed', 'is_synced', 'node_ifname',
                                 'last_synced_at', 'updated_at'])
        if edge.bandwidth_mbps and edge.bandwidth_mbps > 0:
            if bw_pool is None:
                bw_pool = _resolve_pool_name(pop, client, dry_run=False)
            try:
                bw_payload = {
                    'pool_name': bw_pool,
                    'interface_code': ifname,
                    'allocated_mbps': edge.bandwidth_mbps,
                    'remark': f'ISPmanager 边缘加速 {edge.edge_no}',
                    # 最后一步：联动下发到节点 QoS（tc 限速）到该隧道接口
                    'apply_qos': True,
                    'qos_direction': 'egress',
                    'qos_root_kind': 'htb',
                }
                _edge_cust = _customer_code(edge.customer)
                if _edge_cust:
                    bw_payload['customer_code'] = _edge_cust
                resp = client.upsert_bandwidth_allocation(bw_payload)
                bw_pushed += 1
                # 校验节点侧 QoS 是否真正 apply 成功（tc 下发）
                qos = resp.get('qos') if isinstance(resp, dict) else None
                if isinstance(qos, dict):
                    if qos.get('applied'):
                        qos_applied += 1
                    elif qos.get('ok') is False or qos.get('error'):
                        bw_errors.append(
                            f'{ifname}: QoS 下发失败 {qos.get("error") or qos}')
            except PemanagerError as e:
                # 带宽/QoS 下发失败不阻断隧道下发，但需回报
                bw_errors.append(f'{ifname}: {e}')
            except Exception as e:  # noqa: BLE001
                bw_errors.append(f'{ifname}: {e}')

    telemetry_candidates = {(edge.pk, ifname): (edge, ifname) for edge, ifname in applied_edges}
    # 协议被改为不支持、或协议配置被清空后，仍需停用曾经下发过的旧目标，避免继续
    # 对旧 peer 采样。没有 node_ifname 的资源从未形成可用 target，无需创建占位记录。
    for edge in edges:
        if not edge.node_ifname:
            continue
        if edge.protocol == EdgeAcceleration.Protocol.SOCKS5 or _build_tunnel_spec(edge) is None:
            telemetry_candidates.setdefault((edge.pk, edge.node_ifname), (edge, edge.node_ifname))

    telemetry_sync = []
    telemetry_errors = []
    for edge, ifname in telemetry_candidates.values():
        try:
            synced = _sync_edge_telemetry(client, edge, ifname)
            telemetry_sync.append({
                'edge_id': edge.pk,
                'ifname': ifname,
                'target_action': synced.get('target_action'),
                'interface_action': synced.get('interface_action'),
            })
            telemetry_errors.extend(
                f'{ifname}: {error}' for error in (synced.get('errors') or [])
            )
        except Exception as exc:  # noqa: BLE001
            telemetry_errors.append(f'{ifname}: 遥测同步异常：{exc}')
    telemetry_cleanup = _disable_orphaned_edge_telemetry_targets(client, edges)
    telemetry_errors.extend(telemetry_cleanup['errors'])

    pushed_total = (len(new_ids) if apply_ok else 0) + reconfigured
    apply_errors = list(apply_interp.get('errors') or []) + list(reconfigure_errors)
    # QoS 未 applied 计入 apply_ok；遥测失败不阻断隧道生效判断
    if bw_errors:
        apply_errors.extend(bw_errors)
    section_apply_ok = apply_ok and not reconfigure_errors and not bw_errors
    combined_interp = {
        'rest_ok': apply_interp.get('rest_ok', True),
        'apply_ok': section_apply_ok,
        'errors': apply_errors,
        'steps': apply_interp.get('steps') or [],
    }
    return {
        'pushed': pushed_total, 'created': created, 'reconfigured': reconfigured,
        'unchanged': unchanged, 'applied': pushed_total,
        'skipped': skipped, 'socks5_skipped': socks5,
        'reconfigure_errors': reconfigure_errors,
        'bandwidth_pushed': bw_pushed, 'qos_applied': qos_applied,
        'bandwidth_errors': bw_errors,
        'telemetry_sync': telemetry_sync,
        'telemetry_cleanup': telemetry_cleanup['disabled'],
        'telemetry_errors': telemetry_errors,
        'ok': section_apply_ok,
        'rest_ok': combined_interp['rest_ok'],
        'apply_ok': section_apply_ok,
        'apply_errors': apply_errors,
        'apply_interp': combined_interp,
    }


# ═══════════════════════════════════════════════════════════════
# 带宽限速下发（VPS 带宽 → 节点 VM 网卡 rate）
# ═══════════════════════════════════════════════════════════════

def _resolve_pool_name(pop, client, dry_run):
    """选择节点带宽池：复用已有的第一个；dry_run 不创建，实际执行时按需创建。"""
    pools = client.list_bandwidth_pools()
    if pools:
        for p in pools:
            if isinstance(p, dict) and p.get('name'):
                return p['name']
    if dry_run:
        return 'ispmanager'
    total = pop.total_bandwidth_mbps or 10000
    client.create_bandwidth_pool({'name': 'ispmanager', 'total_mbps': total,
                                  'remark': 'ISPmanager 自动创建'})
    return 'ispmanager'


def _push_bandwidth(pop, client, dry_run=True):
    """把带宽限速下发到节点：
      - 同步虚机 → VM 网卡 rate（allocate_bandwidth，hypervisor 侧限速）；
      - 已下发的边缘加速隧道接口 → 节点 QoS（tc 限速，apply_qos=True）。
    这样即使用户仅下发「带宽」区块，也能保证接口带宽最终落到 POP QoS 配置。
    """
    vps_list = list(
        VPS.objects
        .filter(pop_point=pop, is_synced=True)
        .exclude(source_ref='')
        .filter(bandwidth_mbps__gt=0)
        .select_related('customer')
    )
    # 已在节点建立隧道接口（node_ifname 存在）的边缘加速，带宽>0 才需 QoS
    edge_list = list(
        EdgeAcceleration.objects
        .filter(pop_point=pop, bandwidth_mbps__gt=0)
        .exclude(node_ifname='')
        .select_related('customer')
    )
    if not vps_list and not edge_list:
        return {'pushed': 0, 'skipped': 0, 'note': '无可下发带宽的虚机/边缘接口', 'plan': []}

    pool_name = _resolve_pool_name(pop, client, dry_run)
    plan = []
    pushed = failed = 0
    qos_applied = 0
    errors = []

    # ── 1) VPS：VM 网卡 rate ──────────────────────────────────
    for vps in vps_list:
        plan.append({'type': 'vps', 'vps': vps.name, 'vm_id': vps.source_ref,
                     'net_index': 0, 'mbps': vps.bandwidth_mbps})
        if dry_run:
            continue
        try:
            vm_bw_payload = {
                'pool_name': pool_name,
                'vm_id': vps.source_ref,
                'net_index': 0,
                'allocated_mbps': vps.bandwidth_mbps,
                'remark': f'ISPmanager 下发 {vps.name}',
            }
            _vps_cust = _customer_code(vps.customer)
            if _vps_cust:
                vm_bw_payload['customer_code'] = _vps_cust
            client.allocate_vm_bandwidth(vm_bw_payload)
            pushed += 1
        except PemanagerError as e:
            failed += 1
            errors.append(f'{vps.name}: {e}')

    # ── 2) 边缘加速隧道接口：节点 QoS（tc 限速）─────────────────
    for edge in edge_list:
        ifname = edge.node_ifname
        plan.append({'type': 'edge', 'edge_no': edge.edge_no, 'ifname': ifname,
                     'mbps': edge.bandwidth_mbps, 'qos': True})
        if dry_run:
            continue
        try:
            bw_payload = {
                'pool_name': pool_name,
                'interface_code': ifname,
                'allocated_mbps': edge.bandwidth_mbps,
                'remark': f'ISPmanager 边缘加速 {edge.edge_no}',
                'apply_qos': True,
                'qos_direction': 'egress',
                'qos_root_kind': 'htb',
            }
            _edge_cust = _customer_code(edge.customer)
            if _edge_cust:
                bw_payload['customer_code'] = _edge_cust
            resp = client.upsert_bandwidth_allocation(bw_payload)
            pushed += 1
            qos = resp.get('qos') if isinstance(resp, dict) else None
            if isinstance(qos, dict):
                if qos.get('applied'):
                    qos_applied += 1
                elif qos.get('ok') is False or qos.get('error'):
                    failed += 1
                    errors.append(f'{ifname}: QoS 下发失败 {qos.get("error") or qos}')
        except PemanagerError as e:
            failed += 1
            errors.append(f'{ifname}: {e}')

    if dry_run:
        return {'dry_run': True, 'would_push': len(plan),
                'pool_name': pool_name, 'plan': plan}

    interp = {
        'rest_ok': True,
        'apply_ok': not failed,
        'errors': list(errors),
        'steps': [],
    }
    return {
        'pushed': pushed, 'failed': failed, 'qos_applied': qos_applied,
        'pool_name': pool_name, 'errors': errors, 'ok': not failed,
        'rest_ok': True, 'apply_ok': not failed,
        'apply_errors': list(errors), 'apply_interp': interp,
    }


# ═══════════════════════════════════════════════════════════════
# 客户信息下发（ISPmanager Customer → 节点 ResourceCustomer，按 code 对齐）
# ═══════════════════════════════════════════════════════════════

def _customer_code(customer):
    """节点 ResourceCustomer.code 采用 ISPmanager 的 customer_no（唯一、slug 安全）。"""
    return customer.customer_no if customer else None


def _collect_pop_customers(pop):
    """汇总该 POP 下资源（VPS / 边缘加速）关联的客户，去重。"""
    seen = {}
    for vps in VPS.objects.filter(pop_point=pop).select_related('customer'):
        if vps.customer_id and vps.customer:
            seen[vps.customer_id] = vps.customer
    for edge in EdgeAcceleration.objects.filter(pop_point=pop).select_related('customer'):
        if edge.customer_id and edge.customer:
            seen[edge.customer_id] = edge.customer
    return list(seen.values())


def _push_customers(pop, client, dry_run=True):
    """把该 POP 下资源关联的客户主数据下发到节点（upsert by code=customer_no）。"""
    customers = _collect_pop_customers(pop)
    if not customers:
        return {'pushed': 0, 'created': 0, 'updated': 0, 'note': '无关联客户', 'plan': []}

    existing = client.customers() or []
    by_code = {str(c.get('code')): c for c in existing if isinstance(c, dict) and c.get('code')}

    plan = []
    created = updated = 0
    for cust in customers:
        code = _customer_code(cust)
        if not code:
            continue
        node_c = by_code.get(code)
        is_active = cust.status == 'active'
        plan.append({'code': code, 'name': cust.name,
                     'state': '更新' if node_c else '新建'})
        if dry_run:
            created += int(node_c is None)
            updated += int(node_c is not None)
            continue
        payload = {'code': code, 'name': cust.name,
                   'remark': f'ISPmanager 同步 {cust.customer_no}', 'is_active': is_active}
        try:
            if node_c:
                client.update_customer(node_c.get('id'), payload)
                updated += 1
            else:
                client.create_customer(payload)
                created += 1
        except PemanagerError:
            # 客户已存在（并发）或校验失败时忽略，不阻断后续下发
            pass

    if dry_run:
        return {'dry_run': True, 'would_create': created, 'would_update': updated, 'plan': plan}
    return {'pushed': created + updated, 'created': created, 'updated': updated, 'ok': True}


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

_SECTION_HANDLERS = {
    'customers': _push_customers,
    'static_routes': _push_static_routes,
    'policy_rules': _push_policy_rules,
    'edge_tunnels': _push_edge_tunnels,
    'bandwidth': _push_bandwidth,
}


def _section_apply_state(result):
    """从区块结果提取 apply_ok 与错误列表。dry_run / 非 dict 视为通过。"""
    if not isinstance(result, dict) or result.get('dry_run'):
        return True, []
    interp = result.get('apply_interp')
    if isinstance(interp, dict):
        return bool(interp.get('apply_ok', True)), list(interp.get('errors') or [])
    extra = []
    extra.extend(result.get('reconfigure_errors') or [])
    extra.extend(result.get('bandwidth_errors') or [])
    extra.extend(result.get('apply_errors') or [])
    raw_errors = result.get('errors')
    if isinstance(raw_errors, list):
        extra.extend(raw_errors)
    if result.get('ok') is False:
        return False, extra or [result.get('note') or '下发未生效']
    return True, extra


def push_pop_to_node(pop: PopPoint, sections=None, dry_run=True):
    """
    把 POP 点配置下发到节点。sections 为待下发区块列表，缺省下发全部。
    dry_run=True 仅返回下发计划，不实际写节点。
    返回 {ok, rest_ok, apply_ok, apply_errors, dry_run, node_user, summary, errors}。
    ok 要求 REST 与 apply 都成功；apply_ok 为假时不清 config_dirty。
    """
    client, user = PemanagerClient.connect_pop(pop)

    if not sections:
        sections = list(_SECTION_HANDLERS.keys())

    summary = {}
    errors = {}
    for sec in sections:
        handler = _SECTION_HANDLERS.get(sec)
        if handler is None:
            errors[sec] = '不支持的下发区块'
            continue
        try:
            summary[sec] = handler(pop, client, dry_run=dry_run)
        except PemanagerError as e:
            errors[sec] = str(e)
        except Exception as e:  # noqa: BLE001
            errors[sec] = f'下发异常：{e}'

    rest_ok = not errors
    apply_ok = rest_ok
    apply_errors = {}
    if not dry_run:
        for sec, result in summary.items():
            sec_apply, sec_errs = _section_apply_state(result)
            if sec_errs:
                apply_errors[sec] = sec_errs
            if not sec_apply:
                apply_ok = False

    ok = rest_ok and (dry_run or apply_ok)
    # 实际下发且 REST + apply 都成功时，才清除「待下发」标记
    if not dry_run and rest_ok and apply_ok:
        from django.utils import timezone
        PopPoint.objects.filter(pk=pop.pk).update(
            config_dirty=False, last_push_at=timezone.now())

    return {
        'ok': ok,
        'rest_ok': rest_ok,
        'apply_ok': apply_ok if not dry_run else True,
        'apply_errors': apply_errors,
        'dry_run': dry_run,
        'node_user': user,
        'summary': summary,
        'errors': errors,
    }
