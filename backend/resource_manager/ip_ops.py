"""
IP 地址与节点 resourcemanage 的双向同步：录入、分配、释放、边缘联动路由。
"""

import ipaddress

from django.db import transaction
from django.utils import timezone

from .ip_scope import exclude_tunnel_addresses
from .models import IPAddress, EdgeAcceleration, VPS
from .pemanager_client import PemanagerClient, PemanagerError, pop_has_api_config

_ALREADY_ALLOCATED_MARKERS = ('当前状态不允许分配', 'already allocated')

_BULK_CHUNK = 512

_ISP_TO_NODE_STATE = {
    IPAddress.IPStatus.AVAILABLE: 'available',
    IPAddress.IPStatus.RESERVED: 'reserved',
    IPAddress.IPStatus.ASSIGNED: 'allocated',
    IPAddress.IPStatus.BLOCKED: 'recycled',
}


def _customer_code(customer):
    return (getattr(customer, 'customer_no', None) or '').strip() or None


def _vps_interface_code(vps: VPS) -> str:
    if vps.source_ref:
        return f'vm-{vps.source_ref}-net0'
    return ''


def _edge_interface_code(edge: EdgeAcceleration) -> str:
    return (edge.node_ifname or '').strip()


def _node_state(ip: IPAddress) -> str:
    return _ISP_TO_NODE_STATE.get(ip.status, 'available')


def _authed_client(pop, client=None):
    """已认证的节点客户端；传入的 client 若尚未带 Authorization 则 authenticate。"""
    if client is None:
        client, _user = PemanagerClient.connect_pop(pop)
        return client
    if not client.session.headers.get('Authorization'):
        client.authenticate()
    return client


def push_ips_bulk_create(pop, addresses, state='available', subnet_label='', client=None):
    """批量录入 IP 到节点（已存在则跳过）。"""
    if not addresses:
        return {'created': 0, 'skipped': 0, 'errors': []}
    client = _authed_client(pop, client)
    created = skipped = 0
    errors = []
    addrs = [str(a) for a in addresses]
    for i in range(0, len(addrs), _BULK_CHUNK):
        chunk = addrs[i:i + _BULK_CHUNK]
        try:
            res = client.bulk_create_ips({
                'addresses': chunk,
                'state': state,
                'subnet_label': subnet_label or '',
            })
            created += len(res.get('created') or [])
            skipped += len(res.get('skipped') or [])
            errors.extend(res.get('errors') or [])
        except PemanagerError as e:
            errors.append({'error': str(e), 'addresses': chunk[:3]})
    return {'created': created, 'skipped': skipped, 'errors': errors}


def push_pop_ip_inventory_to_node(pop, client=None):
    """
    POP 同步时：将 ISPmanager 手工地址块内的 IP 录入节点（节点已有则跳过）。
    """
    client = _authed_client(pop, client)
    try:
        node_addrs = {
            str(x.get('address'))
            for x in (client.ip_addresses() or [])
            if x.get('address')
        }
    except PemanagerError:
        node_addrs = set()

    qs = (
        IPAddress.objects
        .filter(ip_block__pop_point=pop)
        .exclude(status=IPAddress.IPStatus.SYSTEM)
        .filter(ip_block__is_synced=False)
    )
    to_push = []
    labels = {}
    for ip in qs.only('address', 'subnet_label'):
        addr = str(ip.address)
        if addr in node_addrs:
            continue
        to_push.append(addr)
        if ip.subnet_label:
            labels[addr] = ip.subnet_label

    if not to_push:
        return {'pushed': 0, 'skipped': 0, 'errors': []}

    by_label = {}
    for addr in to_push:
        lbl = labels.get(addr, '')
        by_label.setdefault(lbl, []).append(addr)

    total_created = 0
    all_errors = []
    for lbl, addrs in by_label.items():
        res = push_ips_bulk_create(pop, addrs, state='available', subnet_label=lbl)
        total_created += res.get('created', 0)
        all_errors.extend(res.get('errors') or [])

    return {'pushed': total_created, 'skipped': len(to_push) - total_created, 'errors': all_errors}


def infer_p2p_peer_ip(address_cidr: str):
    """从 PtP 隧道 addresses（如 10.0.0.1/30）推断对端 IP，对齐 vpop DesiredTunnelConfig.peer_ip。"""
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


def edge_route_hints(edge: EdgeAcceleration) -> dict:
    """
    边缘加速 IP 分配联动路由的推荐参数（GRE/VXLAN/WG）。
    供前端展示与后端 _edge_route_spec 共用。
    """
    hints = {
        'protocol': edge.protocol,
        'ifname': _edge_interface_code(edge),
        'tunnel_cidr': '',
        'tunnel_local_ip': '',
        'peer_gateway': '',
        'is_wireguard': edge.protocol == EdgeAcceleration.Protocol.WIREGUARD,
        'netplan_device_class': 'tunnels',
    }
    if edge.protocol == EdgeAcceleration.Protocol.GRE:
        gre = getattr(edge, 'gre_config', None)
        if gre:
            hints['tunnel_cidr'] = gre.tunnel_ip or ''
            if gre.tunnel_ip:
                hints['tunnel_local_ip'] = str(
                    ipaddress.ip_interface(gre.tunnel_ip).ip
                ) if '/' in gre.tunnel_ip else gre.tunnel_ip
                hints['peer_gateway'] = (
                    infer_p2p_peer_ip(gre.tunnel_ip) or str(gre.remote_ip or '')
                )
            elif gre.remote_ip:
                hints['peer_gateway'] = str(gre.remote_ip)
    elif edge.protocol == EdgeAcceleration.Protocol.VXLAN:
        vx = getattr(edge, 'vxlan_config', None)
        if vx:
            hints['tunnel_cidr'] = vx.tunnel_ip or ''
            if vx.tunnel_ip:
                hints['tunnel_local_ip'] = str(
                    ipaddress.ip_interface(vx.tunnel_ip).ip
                ) if '/' in vx.tunnel_ip else vx.tunnel_ip
                hints['peer_gateway'] = (
                    infer_p2p_peer_ip(vx.tunnel_ip) or str(vx.remote_ip or '')
                )
            elif vx.remote_ip:
                hints['peer_gateway'] = str(vx.remote_ip)
    elif edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        wg = getattr(edge, 'wg_config', None)
        if wg and wg.interface_ip:
            hints['tunnel_cidr'] = wg.interface_ip
            hints['tunnel_local_ip'] = str(
                ipaddress.ip_interface(wg.interface_ip).ip
            ) if '/' in wg.interface_ip else wg.interface_ip
            peer = infer_p2p_peer_ip(wg.interface_ip)
            if peer:
                hints['peer_gateway'] = peer
    return hints


def _edge_route_spec(edge: EdgeAcceleration, ip: IPAddress, route_override=None):
    """边缘加速分配 IP 时的 host 路由（/prefix 或 /32）。"""
    ifname = _edge_interface_code(edge)
    if not ifname:
        raise PemanagerError(
            f'边缘加速 [{edge.edge_no}] 尚未下发到节点，无法联动路由（缺少 node_ifname）'
        )
    hints = edge_route_hints(edge)
    dest = f'{ip.address}/32'
    gateway = hints.get('peer_gateway') or None
    on_link = True
    netplan_class = hints.get('netplan_device_class') or 'tunnels'

    spec = {
        'dest_cidr': dest,
        'gateway': gateway,
        'on_link': on_link,
        'metric': None,
        'route_table': None,
        'netplan_device_class': netplan_class,
        'interface_name': ifname,
        'remark': f'ISPmanager edge {edge.edge_no} IP {ip.address}',
    }
    if route_override:
        for k, v in route_override.items():
            if v is not None and v != '':
                spec[k] = v
    # WireGuard：通常无 L3 下一跳，走 on-link + 隧道接口（对齐 RouteStatic WG 表单）
    if hints.get('is_wireguard'):
        if not route_override or route_override.get('gateway') in (None, ''):
            spec['gateway'] = None
        spec['on_link'] = bool(route_override.get('on_link') if route_override else True)
    return spec


def _plain_ip(value) -> str:
    return str(value or '').split('/')[0].strip()


def _is_already_allocated_error(exc) -> bool:
    text = str(exc or '')
    return any(marker in text for marker in _ALREADY_ALLOCATED_MARKERS)


def _find_node_ip_entry(client, address: str):
    want = _plain_ip(address)
    for item in client.ip_addresses() or []:
        if isinstance(item, dict) and _plain_ip(item.get('address')) == want:
            return item
    return None


def _find_node_host_route(client, *, dest_cidr='', ifname='', address=''):
    dest = (dest_cidr or '').strip()
    addr = _plain_ip(address)
    ifname = (ifname or '').strip()
    for route in client.list_desired_routes() or []:
        if not isinstance(route, dict):
            continue
        route_dest = str(route.get('dest_cidr') or '').strip()
        route_addr = _plain_ip(route.get('ip_address') or '')
        if dest and route_dest == dest:
            matched = True
        elif addr and route_addr == addr:
            matched = True
        else:
            matched = False
        if not matched:
            continue
        route_if = (route.get('interface_name') or '').strip()
        if ifname and route_if and route_if != ifname:
            continue
        return route
    return None


def _remember_route_ref(ip, route_id):
    if not ip or not route_id:
        return
    ref = str(route_id)[:64]
    if getattr(ip, 'node_route_ref', None) == ref:
        return
    ip.node_route_ref = ref
    ip.save(update_fields=['node_route_ref'])


def reconcile_edge_ip_route_refs(edge, ips=None, client=None) -> int:
    """把节点已存在的业务 /32 路由 id 回写到本地 node_route_ref。"""
    pop = getattr(edge, 'pop_point', None)
    if not pop or not pop_has_api_config(pop) or not getattr(edge, 'node_ifname', None):
        return 0
    qs = list(ips) if ips is not None else list(
        exclude_tunnel_addresses(
            IPAddress.objects.filter(assigned_edge=edge, status=IPAddress.IPStatus.ASSIGNED)
        )
    )
    pending = [ip for ip in qs if not getattr(ip, 'node_route_ref', '')]
    if not pending:
        return 0
    client = _authed_client(pop, client)
    ifname = _edge_interface_code(edge)
    synced = 0
    for ip in pending:
        route = _find_node_host_route(
            client,
            dest_cidr=f'{ip.address}/32',
            ifname=ifname,
            address=str(ip.address),
        )
        if route and route.get('id'):
            _remember_route_ref(ip, route['id'])
            synced += 1
    return synced


def _previous_protocol_ifnames(edge, current_ifname='') -> set[str]:
    """切换协议后，旧接口名（如 gre12）以及 IP 上仍记录的旧出接口。"""
    current = (current_ifname or '').strip()
    prefixes = {
        EdgeAcceleration.Protocol.WIREGUARD: 'wg',
        EdgeAcceleration.Protocol.GRE: 'gre',
        EdgeAcceleration.Protocol.VXLAN: 'vxlan',
    }
    names = set()
    for proto, prefix in prefixes.items():
        if proto == edge.protocol:
            continue
        stale = f'{prefix}{edge.pk}'[:15]
        if stale and stale != current:
            names.add(stale)
    codes = set(
        IPAddress.objects.filter(assigned_edge=edge)
        .exclude(interface_code='')
        .values_list('interface_code', flat=True)
    )
    try:
        instance = edge.service_instance
    except Exception:
        instance = None
    if instance is not None:
        codes.update(
            instance.ip_addresses.exclude(interface_code='').values_list('interface_code', flat=True)
        )
    for code in codes:
        text = str(code or '').strip()
        if text and text != current:
            names.add(text)
    return names


def _edge_business_dests(edge) -> set[str]:
    dests = set()
    addresses = set(
        str(addr) for addr in
        IPAddress.objects.filter(assigned_edge=edge).values_list('address', flat=True)
        if addr
    )
    try:
        instance = edge.service_instance
    except Exception:
        instance = None
    if instance is not None:
        addresses.update(
            str(addr) for addr in instance.ip_addresses.values_list('address', flat=True) if addr
        )
    for addr in addresses:
        dests.add(f'{addr}/32')
    return dests


def _route_matches_address(route, *, dest_cidr='', address='') -> bool:
    route_dest = str(route.get('dest_cidr') or '').strip()
    route_addr = _plain_ip(route.get('ip_address') or '')
    dest = (dest_cidr or '').strip()
    addr = _plain_ip(address)
    if dest and route_dest == dest:
        return True
    return bool(addr and route_addr == addr)


def retire_previous_edge_interface(client, edge, current_ifname='', *, dry_run=False) -> dict:
    """删除上一协议的隧道和挂在上面的业务 IP 路由，避免新接口写入相同目标时冲突。"""
    current = (current_ifname or _edge_interface_code(edge) or '').strip()
    stale_ifnames = _previous_protocol_ifnames(edge, current)
    business_dests = _edge_business_dests(edge)
    removed_routes = []
    for route in client.list_desired_routes() or []:
        if not isinstance(route, dict) or not route.get('id'):
            continue
        rif = str(route.get('interface_name') or '').strip()
        if current and rif == current:
            continue
        dest = str(route.get('dest_cidr') or '').strip()
        on_stale = rif in stale_ifnames
        moved_business = dest in business_dests and rif and rif != current
        if not on_stale and not moved_business:
            continue
        removed_routes.append({
            'id': route['id'],
            'interface_name': rif,
            'dest_cidr': dest,
        })
        if dry_run:
            continue
        client.delete_desired_route(route['id'])

    removed_tunnels = []
    if stale_ifnames:
        for tunnel in client.list_desired_tunnels() or []:
            if not isinstance(tunnel, dict) or not tunnel.get('id'):
                continue
            ifname = str(tunnel.get('ifname') or '').strip()
            if ifname not in stale_ifnames:
                continue
            kind = str(tunnel.get('kind') or '').strip().lower()
            # 删除 WireGuard 意图不会执行 wg-quick down，接口和 conf 会留下。
            # 保留这条记录，交给隧道下发按同一 id 重配置（节点会先拆旧接口再拉起新协议）。
            if kind == 'wireguard' or ifname.startswith('wg'):
                continue
            removed_tunnels.append(ifname)
            if dry_run:
                continue
            client.delete_desired_tunnel(tunnel['id'])

    if not dry_run and (removed_routes or removed_tunnels):
        if removed_routes:
            try:
                client.apply_desired_routes(phase='full', persist_to_netplan=True)
            except PemanagerError:
                pass
        if removed_tunnels:
            try:
                client.apply_desired_tunnels()
            except PemanagerError:
                pass
        IPAddress.objects.filter(assigned_edge=edge, interface_code__in=list(stale_ifnames)).update(
            interface_code=current,
            node_route_ref='',
        )
    return {
        'routes': removed_routes,
        'tunnels': removed_tunnels,
        'stale_ifnames': sorted(stale_ifnames),
    }


def _reuse_or_create_allocated_route(client, *, ip, edge, route, if_code):
    """节点 IP 已分配时：复用当前接口上的 /32；若还挂在旧协议接口上，先删除再重建。"""
    node_ip = _find_node_ip_entry(client, ip.address)
    state = str((node_ip or {}).get('state') or '')
    if state not in {'allocated', 'reserved'}:
        return None
    existing = _find_node_host_route(
        client,
        dest_cidr=route.get('dest_cidr') or f'{ip.address}/32',
        ifname=if_code,
        address=str(ip.address),
    )
    if existing and existing.get('id'):
        return {
            'address': str(ip.address),
            'state': state,
            'route_id': existing['id'],
            'reused': True,
        }
    retire_previous_edge_interface(client, edge, if_code)
    payload = {
        'interface_name': route.get('interface_name') or if_code,
        'netplan_device_class': route.get('netplan_device_class') or 'tunnels',
        'dest_cidr': route.get('dest_cidr') or f'{ip.address}/32',
        'on_link': bool(route.get('on_link')),
        'remark': route.get('remark') or f'ISPmanager edge {edge.edge_no} IP {ip.address}',
    }
    if route.get('gateway'):
        payload['gateway'] = route['gateway']
    if node_ip and node_ip.get('id'):
        payload['ip_allocation'] = node_ip['id']
    created = client.create_desired_route(payload)
    return {
        'address': str(ip.address),
        'state': state,
        'route_id': (created or {}).get('id'),
        'created_route': True,
    }


def push_assign(
    pop,
    ip: IPAddress,
    *,
    target_type: str,
    vps: VPS | None = None,
    edge: EdgeAcceleration | None = None,
    customer=None,
    interface_code: str = '',
    create_route: bool = False,
    route_override=None,
    allow_from_reserved: bool = True,
    apply_route: bool = True,
):
    """分配 IP 到节点 resourcemanage；边缘可选联动 desired-route 并 apply。"""
    client = _authed_client(pop)

    cust_code = _customer_code(customer)
    if not cust_code:
        raise PemanagerError('分配 IP 到节点需要关联客户（customer_no）')

    if target_type == 'vps':
        if vps is None:
            raise PemanagerError('缺少 VPS 目标')
        if_code = interface_code or _vps_interface_code(vps)
    else:
        if edge is None:
            raise PemanagerError('缺少边缘加速目标')
        if_code = interface_code or _edge_interface_code(edge)

    base = {
        'address': str(ip.address),
        'customer_code': cust_code,
        'interface_code': if_code,
        'subnet_label': ip.subnet_label or ip.ip_block.description or ip.ip_block.cidr,
        'allow_from_reserved': allow_from_reserved,
    }

    node_result = None
    route_id = None

    if target_type == 'edge' and create_route:
        route = _edge_route_spec(edge, ip, route_override)
        try:
            node_result = client.allocate_ip_with_route({**base, 'route': route})
            route_id = node_result.get('route_id')
        except PemanagerError as exc:
            if not _is_already_allocated_error(exc):
                raise
            reused = _reuse_or_create_allocated_route(
                client, ip=ip, edge=edge, route=route, if_code=if_code,
            )
            if not reused or not reused.get('route_id'):
                raise
            node_result = reused
            route_id = reused.get('route_id')
        if route_id and apply_route:
            try:
                if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
                    client.apply_wireguard_routes(ids=[route_id])
                else:
                    client.apply_desired_routes(
                        ids=[route_id], phase='full', persist_to_netplan=True,
                    )
            except PemanagerError:
                pass
    else:
        node_result = client.allocate_ip(base)

    return {
        'node_result': node_result,
        'route_id': route_id,
        'interface_code': if_code,
    }


def push_release(pop, ip: IPAddress):
    """释放节点 IP 并删除关联路由意图。"""
    client = _authed_client(pop)
    return client.release_ip({'address': str(ip.address)})


def _ensure_node_customer(client, customer):
    """节点 allocate 需要 ResourceCustomer.code；缺失时尽力创建。"""
    cust_code = _customer_code(customer)
    if not cust_code:
        raise PemanagerError('分配 IP 到节点需要关联客户（customer_no）')
    try:
        existing = {
            str(c.get('code'))
            for c in (client.customers() or [])
            if isinstance(c, dict) and c.get('code')
        }
        if cust_code not in existing:
            client.create_customer({
                'code': cust_code,
                'name': getattr(customer, 'name', None) or cust_code,
            })
    except PemanagerError:
        # 并发或校验失败时交给后续 allocate 报错
        pass
    return cust_code


def ensure_available_ips_for_pop(pop, count: int):
    """保证该 POP 至少有 count 条可用业务 IP；地址块已建但未点「生成 IP」时按需补齐。"""
    from .ip_net import expand_cidr_hosts
    from .ip_scope import TUNNEL_BLOCK_TAG
    from .models import IPBlock

    count = int(count or 0)
    if count <= 0 or pop is None:
        return []

    available_qs = (
        IPAddress.objects.filter(
            ip_block__pop_point=pop,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        .exclude(ip_block__description__startswith=TUNNEL_BLOCK_TAG)
        .select_related('ip_block')
        .order_by('address')
    )
    available = list(available_qs[:count])
    if len(available) >= count:
        return available

    existing = {
        str(addr)
        for addr in IPAddress.objects.filter(ip_block__pop_point=pop).values_list('address', flat=True)
        if addr
    }
    blocks = (
        IPBlock.objects.filter(pop_point=pop)
        .exclude(description__startswith=TUNNEL_BLOCK_TAG)
        .order_by('-gateway', 'id')
    )
    need = count - len(available)
    created = []
    for block in blocks:
        if getattr(block, 'is_active', True) is False:
            continue
        hosts, _total = expand_cidr_hosts(block.cidr, limit=256)
        gateway = str(block.gateway) if block.gateway else ''
        prefix = block.prefix_len
        for addr in hosts:
            if addr in existing or (gateway and addr == gateway):
                continue
            obj = IPAddress(
                ip_block=block,
                address=addr,
                prefix_len=prefix,
                gateway=block.gateway,
                status=IPAddress.IPStatus.AVAILABLE,
                description='开通时按地址块自动生成',
            )
            obj.save()
            existing.add(addr)
            created.append(obj)
            if len(created) >= need:
                break
        if len(created) >= need:
            break
    return list(available_qs[:count])


def push_order_ips_to_node(pop, ips, *, customer, vps=None, edge=None):
    """工单已分配的 IP 同步到节点账本。

    - 先 bulk-create，保证节点有地址；
    - VPS → allocate（interface_code 可空）；
    - Edge 且已有 node_ifname → allocate-with-route；
    - 仅 IP 工单 → allocate（interface_code 可空）。
    节点失败抛 PemanagerError，由开通编排回滚本地分配并转人工。
    """
    ips = list(ips or [])
    if not ips:
        return {'pushed': 0, 'skipped': True, 'errors': []}
    if not pop_has_api_config(pop):
        return {'pushed': 0, 'skipped': True, 'errors': []}

    client = _authed_client(pop)
    _ensure_node_customer(client, customer)

    first = ips[0]
    subnet = ''
    if getattr(first, 'ip_block_id', None) and first.ip_block:
        subnet = first.ip_block.cidr or first.subnet_label or ''
    elif getattr(first, 'subnet_label', None):
        subnet = first.subnet_label
    addrs = [str(ip.address) for ip in ips]
    bulk = push_ips_bulk_create(
        pop, addrs, state='available', subnet_label=subnet, client=client,
    )
    hard_errors = []
    for item in bulk.get('errors') or []:
        if isinstance(item, dict) and item.get('error'):
            hard_errors.append(str(item['error']))
        elif isinstance(item, str):
            hard_errors.append(item)
    if hard_errors and not bulk.get('created') and not bulk.get('skipped'):
        raise PemanagerError('节点录入 IP 失败：' + '；'.join(hard_errors[:3]))

    errors = []
    pushed = 0
    reused = 0
    if edge is not None and edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        if getattr(edge, 'node_ifname', None):
            retire_previous_edge_interface(client, edge, edge.node_ifname)
        return {
            'pushed': 0,
            'skipped': True,
            'route_mode': 'wireguard_allowed_ips',
            'errors': [],
        }
    if edge is not None and getattr(edge, 'node_ifname', None):
        retire_previous_edge_interface(client, edge, edge.node_ifname)
    for ip in ips:
        try:
            payload = None
            if vps is not None:
                payload = push_assign(pop, ip, target_type='vps', vps=vps, customer=customer)
            elif edge is not None:
                payload = push_assign(
                    pop, ip,
                    target_type='edge',
                    edge=edge,
                    customer=customer,
                    create_route=bool(getattr(edge, 'node_ifname', None)),
                    apply_route=True,
                )
            else:
                client.allocate_ip({
                    'address': str(ip.address),
                    'customer_code': _customer_code(customer),
                    'interface_code': '',
                    'subnet_label': (
                        ip.subnet_label
                        or (ip.ip_block.cidr if getattr(ip, 'ip_block_id', None) else '')
                    ),
                    'allow_from_reserved': True,
                })
            if isinstance(payload, dict):
                _remember_route_ref(ip, payload.get('route_id'))
                node_result = payload.get('node_result')
                if payload.get('reused') or (
                    isinstance(node_result, dict) and node_result.get('reused')
                ):
                    reused += 1
            pushed += 1
        except PemanagerError as exc:
            errors.append(f'{ip.address}: {exc}')
    if errors:
        raise PemanagerError('工单 IP 下发节点失败：' + '；'.join(errors))
    return {'pushed': pushed, 'reused': reused, 'errors': []}


def assign_ip_local_and_node(
    ip: IPAddress,
    *,
    target_type: str,
    vps=None,
    edge=None,
    is_primary=False,
    assigned_at=None,
    push_to_node=True,
    create_route=True,
    route_override=None,
    operator=None,
    request=None,
    source='',
):
    """本地 DB 分配 + 节点下发（节点失败则回滚）。"""
    from .resource_audit import log_ip_assign, snapshot_ip

    old_value = snapshot_ip(ip)
    pop = ip.ip_block.pop_point
    if target_type == 'vps':
        if vps.pop_point_id != pop.id:
            raise ValueError('VPS 不属于该 IP 所在 POP 节点')
        customer = vps.customer
    else:
        if edge.pop_point_id != pop.id:
            raise ValueError('边缘加速不属于该 IP 所在 POP 节点')
        customer = edge.customer

    if ip.status not in (IPAddress.IPStatus.AVAILABLE, IPAddress.IPStatus.RESERVED):
        raise ValueError(f'IP 当前状态 {ip.get_status_display()} 不可分配')

    node_payload = None
    with transaction.atomic():
        if target_type == 'vps':
            ip.assigned_vps = vps
            ip.assigned_edge = None
            ip.interface_code = _vps_interface_code(vps)
        else:
            ip.assigned_edge = edge
            ip.assigned_vps = None
            ip.interface_code = _edge_interface_code(edge)

        ip.status = IPAddress.IPStatus.ASSIGNED
        ip.is_primary = is_primary
        ip.assigned_at = assigned_at or timezone.now()
        ip.save()

        if push_to_node:
            node_payload = push_assign(
                pop, ip,
                target_type=target_type,
                vps=vps,
                edge=edge,
                customer=customer,
                create_route=(target_type == 'edge' and create_route),
                route_override=route_override,
            )
            if node_payload.get('route_id'):
                ip.node_route_ref = str(node_payload['route_id'])[:64]
                ip.save(update_fields=['node_route_ref'])

    log_ip_assign(
        ip,
        old_value=old_value,
        operator=operator,
        request=request,
        source=source or ('节点同步分配' if push_to_node else '仅本地分配'),
        node_payload=node_payload,
    )
    return node_payload


def release_ip_local_and_node(ip: IPAddress, *, push_to_node=True, operator=None, request=None, source=''):
    """本地释放 + 节点 release（含删路由）。"""
    from .resource_audit import log_ip_release, snapshot_ip

    pop = ip.ip_block.pop_point
    old_value = snapshot_ip(ip)
    old_vps = ip.assigned_vps
    old_edge = ip.assigned_edge
    old_route = ip.node_route_ref
    node_result = None
    with transaction.atomic():
        ip.assigned_vps = None
        ip.assigned_edge = None
        ip.interface_code = ''
        ip.node_route_ref = ''
        ip.status = IPAddress.IPStatus.AVAILABLE
        ip.is_primary = False
        ip.assigned_at = None
        ip.save()

        if push_to_node:
            node_result = push_release(pop, ip)

    payload = {'node_result': node_result, 'removed_route_ref': old_route}
    log_ip_release(
        ip,
        old_value=old_value,
        operator=operator,
        request=request,
        source=source or ('节点同步释放' if push_to_node else '仅本地释放'),
        node_payload=payload,
        vps=old_vps,
        edge=old_edge,
    )
    return payload
