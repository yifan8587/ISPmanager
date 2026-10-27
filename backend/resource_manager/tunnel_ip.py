"""
隧道点对点（p2p）IP 地址段用量计算（ISPmanager 中央侧）。

对齐 vpopmanager.resourcemanage.services.tunnel_ip 的切块规则：
  - 在父网段（如 10.21.0.0/24）内按 point_prefixlen（默认 /30）依次切块；
  - 每块取两个可用地址：本端(local)=块内首个、对端(peer)=块内第二个；
  - 下一块自动 +4（/30），即 .1/.2 → .5/.6 → .9/.10 ...；

与节点不同：ISPmanager 侧的「占用」由本 POP 下 IPAddress 的状态推断——
凡地址处于 已分配 / 预留 / 封禁 / 系统保留 之一即视为该 p2p 块已占用。
"""
from __future__ import annotations

import ipaddress
from typing import Any, Iterator

from .models import EdgeAcceleration, IPAddress, TunnelIPPool


def _customer_payload(customer):
    if not customer:
        return {'customer_id': None, 'customer_no': '', 'customer_name': ''}
    return {
        'customer_id': customer.pk,
        'customer_no': getattr(customer, 'customer_no', '') or '',
        'customer_name': getattr(customer, 'name', '') or '',
    }


def _usage_entry(ip, edge_by_ifname=None, fallback_customer=None) -> dict[str, Any]:
    edge = ip.assigned_edge
    if edge is None and edge_by_ifname and ip.interface_code:
        edge = edge_by_ifname.get(ip.interface_code)
    vps = ip.assigned_vps
    customer = None
    if edge is not None:
        customer = getattr(edge, 'customer', None)
    elif vps is not None:
        customer = getattr(vps, 'customer', None)
    payload = _customer_payload(customer or fallback_customer)
    return {
        'address': str(ip.address),
        'status': ip.status,
        'interface_code': ip.interface_code or getattr(edge, 'node_ifname', '') or '',
        'edge': edge.edge_no if edge is not None else '',
        'edge_name': edge.name if edge is not None else '',
        'vps': vps.name if vps is not None else '',
        'description': ip.description or '',
        **payload,
    }

# 视为「已占用」的 IP 状态（可用之外的都算占用）
_TAKEN_STATES = (
    IPAddress.IPStatus.ASSIGNED,
    IPAddress.IPStatus.RESERVED,
    IPAddress.IPStatus.BLOCKED,
    IPAddress.IPStatus.SYSTEM,
)


def _prefetched_taken_ips(pool: TunnelIPPool):
    """取得列表查询预取的已占用 IP；未预取时返回 ``None``。"""
    pop = getattr(pool, 'pop_point', None)
    blocks = getattr(pop, '_prefetched_objects_cache', {}).get('ip_blocks')
    if blocks is None:
        return None

    result = []
    for block in blocks:
        ips = getattr(block, '_prefetched_objects_cache', {}).get('ip_addresses')
        if ips is None:
            return None
        result.extend(ips)
    return result


def _prefetched_edges(pool: TunnelIPPool):
    pop = getattr(pool, 'pop_point', None)
    edges = getattr(pop, '_prefetched_objects_cache', {}).get('edge_accelerations')
    if edges is None:
        return None
    return {e.node_ifname: e for e in edges if e.node_ifname}


def _edge_index(pool: TunnelIPPool) -> dict[str, Any]:
    cached = _prefetched_edges(pool)
    if cached is not None:
        return cached
    return {
        e.node_ifname: e
        for e in (
            EdgeAcceleration.objects
            .filter(pop_point=pool.pop_point)
            .exclude(node_ifname='')
            .select_related('customer')
        )
        if e.node_ifname
    }


def iter_p2p_blocks(pool: TunnelIPPool) -> Iterator[dict[str, Any]]:
    """枚举池内所有 p2p 块（不查库，仅按地址计算）。"""
    try:
        net = pool.network()
    except ValueError:
        return
    plen = int(pool.point_prefixlen)
    if plen <= net.prefixlen:
        return
    for sub in net.subnets(new_prefix=plen):
        if plen in (31, 127):
            local, peer = sub[0], sub[1]
        else:
            hosts = list(sub.hosts())
            if len(hosts) < 2:
                continue
            local, peer = hosts[0], hosts[1]
        yield {
            'subnet': str(sub),
            'network': str(sub.network_address),
            'local': str(local),
            'peer': str(peer),
            'local_cidr': f'{local}/{plen}',
            'peer_cidr': f'{peer}/{plen}',
            'prefixlen': plen,
        }


def _taken_map(
    pool: TunnelIPPool,
    candidates: list[str] | None = None,
) -> dict[str, dict]:
    """返回地址段内已占用地址的映射（单次查询，限定在所属 POP）。"""
    ips = _prefetched_taken_ips(pool)
    if ips is None:
        qs = (
            IPAddress.objects
            .filter(
                ip_block__pop_point=pool.pop_point,
                status__in=_TAKEN_STATES,
            )
            .select_related(
                'assigned_edge__customer',
                'assigned_vps__customer',
            )
        )
        if candidates is not None:
            qs = qs.filter(address__in=candidates)
        ips = qs
    else:
        ips = [ip for ip in ips if ip.status in _TAKEN_STATES]
        if candidates is not None:
            candidates = set(candidates)
            ips = [ip for ip in ips if str(ip.address) in candidates]

    try:
        network = pool.network()
    except ValueError:
        return {}

    edge_by_ifname = _edge_index(pool)
    fallback_customer = getattr(pool, 'customer', None)
    out: dict[str, dict] = {}
    for ip in ips:
        try:
            if ipaddress.ip_address(str(ip.address)) not in network:
                continue
        except ValueError:
            continue
        out[str(ip.address)] = _usage_entry(
            ip, edge_by_ifname=edge_by_ifname, fallback_customer=fallback_customer,
        )
    return out


def find_next_free_block(pool: TunnelIPPool) -> dict[str, Any] | None:
    """在池内按顺序找到下一个「本端与对端都空闲」的 p2p 块。"""
    taken = _taken_map(pool)
    for block in iter_p2p_blocks(pool):
        if block['local'] not in taken and block['peer'] not in taken:
            return block
    return None


def usage(pool: TunnelIPPool) -> dict[str, Any]:
    """池使用情况：总块数、已用块、下一个可用块、已占用明细。"""
    total = pool.total_blocks()
    used_blocks: list[dict[str, Any]] = []
    next_free: dict[str, Any] | None = None
    taken = _taken_map(pool)
    for block in iter_p2p_blocks(pool):
        entries = [taken[a] for a in (block['local'], block['peer']) if a in taken]
        if entries:
            used_blocks.append({
                **block,
                'entries': entries,
                'interface_code': next((e.get('interface_code') for e in entries if e.get('interface_code')), ''),
                'customer_name': next((e.get('customer_name') for e in entries if e.get('customer_name')), ''),
                'customer_no': next((e.get('customer_no') for e in entries if e.get('customer_no')), ''),
            })
        elif next_free is None:
            next_free = block
    return {
        'pool': pool.name,
        'cidr': pool.cidr,
        'prefixlen': int(pool.point_prefixlen),
        'total_blocks': total,
        'used_blocks': len(used_blocks),
        'free_blocks': max(0, total - len(used_blocks)),
        'next_free': next_free,
        'used': used_blocks,
    }
