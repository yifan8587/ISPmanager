"""IP 网段工具：掩码、可用主机、CIDR 包含关系。"""
from __future__ import annotations

import ipaddress


def netmask_from_prefix(prefix_len: int | None, ip_version: str = 'v4') -> str:
    if prefix_len is None:
        return ''
    try:
        plen = int(prefix_len)
    except (TypeError, ValueError):
        return ''
    version = (ip_version or 'v4').lower()
    if version in ('v6', '6', 'ipv6'):
        if not 0 <= plen <= 128:
            return ''
        return str(ipaddress.IPv6Network(f'::/{plen}').netmask)
    if not 0 <= plen <= 32:
        return ''
    return str(ipaddress.IPv4Network(f'0.0.0.0/{plen}').netmask)


def parse_network(cidr: str):
    return ipaddress.ip_network(str(cidr).strip(), strict=False)


def cidr_contains(parent: str, child: str) -> bool:
    try:
        return parse_network(child).subnet_of(parse_network(parent))
    except (ValueError, TypeError):
        return False


def gateway_in_cidr(gateway: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(str(gateway)) in parse_network(cidr)
    except (ValueError, TypeError):
        return False


def expand_cidr_hosts(
    cidr: str,
    *,
    include_network: bool = False,
    include_broadcast: bool = False,
    limit: int = 1024,
) -> tuple[list[str], int]:
    """展开 CIDR 内可用主机。返回 (地址列表, 未截断前的可用数)。超限只返回前 limit 条。"""
    network = parse_network(cidr)
    is_v4 = isinstance(network, ipaddress.IPv4Network)
    prefix = network.prefixlen
    if is_v4 and prefix >= 31:
        hosts = [str(ip) for ip in network]
        return hosts[:limit], len(hosts)
    if is_v4:
        total = int(network.num_addresses)
        start = 0 if include_network else 1
        end_excl = total if include_broadcast else total - 1
        usable = max(0, end_excl - start)
        count = min(usable, limit)
        return [str(network[start + i]) for i in range(count)], usable
    total = int(network.num_addresses)
    start = 0 if include_network else 1
    usable = max(0, total - start)
    iterator = iter(network) if include_network else network.hosts()
    hosts = []
    for i, ip in enumerate(iterator):
        if i >= limit:
            break
        hosts.append(str(ip))
    return hosts, usable
