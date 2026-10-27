"""
节点数据同步服务。

部署完成后，从 POP 节点的 pemanager API 拉取系统现场数据，并完整映射写回
ISPmanager 的资源模型，使两侧数据保持一致（前后端数据一致性）：

  - 网络接口  → NetworkInterface（含内核 IP 地址）
  - 静态路由  → RouteTable（按内核路由表分表）+ StaticRoute（按 dev 关联出接口）
  - 策略路由  → PolicyRoute（按 ip rule，关联查找表）
  - IP 地址   → IPBlock（按真实子网分块）+ IPAddress（内核地址 + 受管 IP 合并）
  - 带宽分配  → BandwidthAllocation（VM 网卡 + 隧道/边缘接口）

一致性策略（对账清理）：
  所有由同步生成的记录都打 `is_synced=True` 标记。每次同步会：
    1. upsert（或重建）本轮在节点上仍存在的记录；
    2. 删除「带 is_synced 标记、但本轮未再出现」的陈旧记录。
  手工创建的记录（is_synced=False）永不被同步清理，避免误删人工数据。

每个区块独立 try/except，单块失败不影响其它块，最终返回统计摘要。
"""

import ipaddress
import re

from django.db import transaction
from django.utils import timezone

from .models import (
    PopPoint, NetworkInterface, RouteTable, StaticRoute, PolicyRoute,
    IPBlock, IPAddress, TunnelIPPool, VPS, BandwidthAllocation, EdgeAcceleration,
)
from .ip_scope import TUNNEL_BLOCK_TAG as _TUNNEL_BLOCK_TAG
from .pemanager_client import PemanagerClient, PemanagerError

# 节点 VM 网卡带宽分配的 interface_code 约定：vm-<节点VM主键>-net<序号>
_VM_IFCODE_RE = re.compile(r'^vm-(\d+)-net(\d+)$')


def _resolve_customer(code):
    """节点 customer_code → ISPmanager Customer（约定 code == customer_no）。"""
    code = (code or '').strip()
    if not code:
        return None
    try:
        from customer_manager.models import Customer
    except Exception:  # noqa: BLE001
        return None
    return Customer.objects.filter(customer_no=code).first()


# ═══════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════

def _first(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ''):
            return d[k]
    return default


def _to_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _is_ignorable_ip(addr):
    """判断单个 IP 是否应在同步时忽略（不写入 POP 的 IP 资源）。

    过滤：0.0.0.0 / :: (unspecified)、127.* / ::1 (loopback)、fe80* (link-local)。
    非法地址同样返回 True（忽略）。
    """
    if not addr:
        return True
    low = str(addr).strip().lower()
    if not low:
        return True
    if low.startswith('127.') or low == '::1' or low.startswith('fe80'):
        return True
    try:
        ip_obj = ipaddress.ip_address(low)
    except ValueError:
        return True
    return bool(ip_obj.is_unspecified or ip_obj.is_loopback or ip_obj.is_link_local)


def _is_ignorable_cidr(cidr):
    """判断 CIDR 段是否应在同步时忽略。

    过滤：0.0.0.0/0 / ::/0 (默认路由段)、unspecified/loopback/link-local 网络。
    非法 CIDR 同样返回 True（忽略）。
    """
    if not cidr:
        return True
    raw = str(cidr).strip().lower()
    if not raw:
        return True
    try:
        net = ipaddress.ip_network(raw, strict=False)
    except ValueError:
        # 可能是不带前缀的裸地址，回退按单 IP 判断
        return _is_ignorable_ip(raw.split('/')[0])
    if net.prefixlen == 0:
        return True  # 0.0.0.0/0 或 ::/0
    return bool(
        net.network_address.is_unspecified
        or net.is_loopback
        or net.is_link_local
    )


def _reconcile(qs, seen_ids):
    """删除带 is_synced 标记、但本轮未出现的陈旧记录，返回删除数量。"""
    stale = qs.filter(is_synced=True).exclude(id__in=list(seen_ids))
    count = stale.count()
    if count:
        stale.delete()
    return count


# ═══════════════════════════════════════════════════════════════
# § 1  网络接口
# ═══════════════════════════════════════════════════════════════

_IFACE_TYPE_MAP = {
    'ethernet': NetworkInterface.InterfaceType.PHYSICAL,
    'physical': NetworkInterface.InterfaceType.PHYSICAL,
    'veth': NetworkInterface.InterfaceType.PHYSICAL,
    'vlan': NetworkInterface.InterfaceType.VLAN,
    'bond': NetworkInterface.InterfaceType.BOND,
    'bridge': NetworkInterface.InterfaceType.BRIDGE,
    'loopback': NetworkInterface.InterfaceType.LOOPBACK,
    'tunnel': NetworkInterface.InterfaceType.TUNNEL,
    'gre': NetworkInterface.InterfaceType.TUNNEL,
    'gretap': NetworkInterface.InterfaceType.TUNNEL,
    'ip6gre': NetworkInterface.InterfaceType.TUNNEL,
    'vxlan': NetworkInterface.InterfaceType.TUNNEL,
    'geneve': NetworkInterface.InterfaceType.TUNNEL,
    'sit': NetworkInterface.InterfaceType.TUNNEL,
    'ipip': NetworkInterface.InterfaceType.TUNNEL,
    'tun': NetworkInterface.InterfaceType.TUNNEL,
    'tap': NetworkInterface.InterfaceType.TUNNEL,
    'wireguard': NetworkInterface.InterfaceType.TUNNEL,
    'dummy': NetworkInterface.InterfaceType.DUMMY,
}


def _map_iface_type(kind):
    if not kind:
        return NetworkInterface.InterfaceType.PHYSICAL
    return _IFACE_TYPE_MAP.get(str(kind).lower(), NetworkInterface.InterfaceType.PHYSICAL)


def _map_iface_status(item):
    operstate = str(_first(item, 'operstate', default='') or '').upper()
    if operstate == 'UP':
        return NetworkInterface.InterfaceStatus.UP
    if operstate in ('DOWN', 'LOWERLAYERDOWN'):
        return NetworkInterface.InterfaceStatus.DOWN
    admin_up = item.get('admin_up') if isinstance(item, dict) else None
    if admin_up is True:
        return NetworkInterface.InterfaceStatus.UP
    if admin_up is False:
        return NetworkInterface.InterfaceStatus.DOWN
    return NetworkInterface.InterfaceStatus.UNKNOWN


def _iface_vlan_id(item):
    """从 linkinfo.info_data.id 提取 VLAN ID（若有）。"""
    info = item.get('linkinfo') if isinstance(item, dict) else None
    if isinstance(info, dict):
        data = info.get('info_data')
        if isinstance(data, dict) and data.get('id') is not None:
            return _to_int(data.get('id'), 0) or 0
    return 0


def _sync_interfaces(pop, items, now):
    """同步接口，返回 (摘要, 内核地址列表)。"""
    created = updated = 0
    seen_ids = []
    iface_addrs = []  # [{address, prefix, version, ifname}]

    for it in items or []:
        if not isinstance(it, dict):
            continue
        ifname = _first(it, 'ifname', 'name', 'dev')
        if not ifname:
            continue
        defaults = {
            'interface_type': _map_iface_type(_first(it, 'kind', 'netplan_kind', 'link_type')),
            'mac_address': _first(it, 'mac', 'mac_address', 'address') or None,
            'mtu': _to_int(_first(it, 'mtu'), 1500),
            'status': _map_iface_status(it),
            'vlan_id': _iface_vlan_id(it),
            'is_synced': True,
            'last_synced_at': now,
        }
        obj, is_new = NetworkInterface.objects.update_or_create(
            pop_point=pop, ifname=str(ifname)[:32], defaults=defaults,
        )
        created += int(is_new)
        updated += int(not is_new)
        seen_ids.append(obj.pk)

        # 收集该接口上的内核 IP 地址
        for a in (it.get('addresses') or []):
            if not isinstance(a, dict):
                continue
            local = a.get('local') or a.get('address')
            if not local:
                continue
            scope = str(a.get('scope') or '').lower()
            if scope == 'host':
                continue  # 跳过 127.0.0.1 / ::1
            # 统一过滤 0.0.0.0/:: / loopback / link-local 等无效地址
            if _is_ignorable_ip(local):
                continue
            try:
                ip_obj = ipaddress.ip_address(str(local))
            except ValueError:
                continue
            prefix = _to_int(a.get('prefixlen'), 128 if ip_obj.version == 6 else 32)
            iface_addrs.append({
                'address': str(local),
                'prefix': prefix,
                'version': 'v6' if ip_obj.version == 6 else 'v4',
                'ifname': str(ifname),
            })

    deleted = _reconcile(NetworkInterface.objects.filter(pop_point=pop), seen_ids)
    return {'created': created, 'updated': updated, 'deleted': deleted,
            'total': len(seen_ids)}, iface_addrs


# ═══════════════════════════════════════════════════════════════
# § 2  路由表 + 静态路由
# ═══════════════════════════════════════════════════════════════

_TABLE_NAME_MAP = {253: 'default', 254: 'main', 255: 'local'}
_TABLE_ALIAS = {'main': 254, 'default': 253, 'local': 255}


def _resolve_table_id(raw):
    """把路由表标识（int 或 'main'/'local'/'default'）解析为 int。"""
    if raw is None or raw == '':
        return 254
    iv = _to_int(raw)
    if iv is not None:
        return iv
    return _TABLE_ALIAS.get(str(raw).lower())


def _iface_lookup(pop):
    return {
        i.ifname: i
        for i in NetworkInterface.objects.filter(pop_point=pop)
        if i.ifname
    }


def _sync_routes(pop, items, now):
    """按内核路由表分表，同步静态路由。"""
    iface_map = _iface_lookup(pop)
    table_seen = {}            # table_id -> RouteTable
    route_seen_ids = []
    created = updated = 0
    skipped = 0

    for it in items or []:
        if not isinstance(it, dict):
            continue
        table_id = _resolve_table_id(it.get('table'))
        if table_id is None or table_id == 255:
            # 跳过 local 表（内核自动维护的本地/广播路由，无同步价值）
            skipped += 1
            continue
        rtype = str(it.get('type') or '').lower()
        if rtype in ('local', 'broadcast', 'nat', 'anycast'):
            skipped += 1
            continue

        dest = _first(it, 'dst', 'dest_cidr', 'destination', 'to')
        if not dest or str(dest).lower() == 'default':
            dest = '0.0.0.0/0'
        dest = str(dest)[:50]

        gateway = _first(it, 'gateway', 'via')
        if gateway:
            try:
                ipaddress.ip_address(str(gateway))
            except ValueError:
                gateway = None

        # 取得/创建路由表
        rt = table_seen.get(table_id)
        if rt is None:
            rt, is_new_rt = RouteTable.objects.update_or_create(
                pop_point=pop, table_id=table_id,
                defaults={
                    'owner_type': RouteTable.RouteTableOwner.POP,
                    'name': _TABLE_NAME_MAP.get(table_id, f'table-{table_id}'),
                    'description': '节点同步导入',
                    'is_synced': True,
                    'last_synced_at': now,
                },
            )
            table_seen[table_id] = rt

        dev = _first(it, 'dev', 'device', 'oif')
        iface = iface_map.get(str(dev)) if dev else None

        defaults = {
            'gateway': gateway or None,
            'metric': _to_int(_first(it, 'metric', 'priority'), 0) or 0,
            'interface': iface,
            'is_active': True,
            'is_synced': True,
            'last_synced_at': now,
            'description': f'节点同步导入 (table {table_id}'
                           + (f', dev {dev}' if dev else '') + ')',
        }
        obj, is_new = StaticRoute.objects.update_or_create(
            route_table=rt, destination=dest, gateway=(gateway or None),
            defaults=defaults,
        )
        created += int(is_new)
        updated += int(not is_new)
        route_seen_ids.append(obj.pk)

    # 对账：删除本轮未出现的同步静态路由
    route_deleted = _reconcile(
        StaticRoute.objects.filter(route_table__pop_point=pop), route_seen_ids,
    )
    # 对账：删除空的、已同步的路由表
    table_deleted = 0
    stale_tables = (
        RouteTable.objects.filter(pop_point=pop, is_synced=True)
        .exclude(id__in=[rt.pk for rt in table_seen.values()])
    )
    for t in stale_tables:
        if not t.static_routes.exists():
            t.delete()
            table_deleted += 1

    return {
        'created': created, 'updated': updated, 'deleted': route_deleted,
        'tables': len(table_seen), 'tables_deleted': table_deleted,
        'skipped': skipped, 'total': len(route_seen_ids),
    }


# ═══════════════════════════════════════════════════════════════
# § 3  策略路由（ip rule）
# ═══════════════════════════════════════════════════════════════

_RULE_ACTION_MAP = {
    'lookup': 'lookup',
    'blackhole': 'blackhole',
    'unreachable': 'unreachable',
    'prohibit': 'unreachable',
}


def _sync_policy_rules(pop, items, now):
    """
    策略路由为内核 ip rule 的纯镜像：先删除本 POP 的同步策略，再整体重建，
    避免 (pop, priority) 在 IPv4/IPv6 同优先级时的 upsert 冲突。
    """
    # 路由表查找（用于关联 lookup_table）
    table_map = {
        t.table_id: t for t in RouteTable.objects.filter(pop_point=pop)
    }

    # 清理旧的同步策略
    PolicyRoute.objects.filter(pop_point=pop, is_synced=True).delete()

    to_create = []
    skipped = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        priority = _to_int(it.get('priority'))
        if priority is None:
            skipped += 1
            continue

        raw_table = it.get('table')
        table_id = _resolve_table_id(raw_table) if raw_table not in (None, '') else None
        lookup_table = table_map.get(table_id) if table_id is not None else None

        action_raw = str(it.get('action') or ('lookup' if raw_table else 'lookup')).lower()
        action = _RULE_ACTION_MAP.get(action_raw, 'lookup')

        src = _first(it, 'from', 'src', 'src_prefix', default='') or ''
        if str(src).lower() == 'all':
            src = ''
        dst = _first(it, 'to', 'dst', 'dst_prefix', default='') or ''
        if str(dst).lower() == 'all':
            dst = ''

        family = it.get('family') or 'inet'
        table_label = raw_table if raw_table not in (None, '') else '-'
        desc = f'节点同步导入 ({family}, table {table_label})'

        to_create.append(PolicyRoute(
            pop_point=pop,
            priority=priority,
            src_prefix=str(src)[:50],
            dst_prefix=(str(dst)[:50] or None),
            fwmark=(str(it.get('fwmark'))[:32] if it.get('fwmark') not in (None, '') else None),
            lookup_table=lookup_table,
            action=action,
            is_active=True,
            is_synced=True,
            last_synced_at=now,
            description=desc[:256],
        ))

    if to_create:
        PolicyRoute.objects.bulk_create(to_create, batch_size=500)

    return {'created': len(to_create), 'skipped': skipped, 'total': len(to_create)}


# ═══════════════════════════════════════════════════════════════
# § 4  IP 地址（IPBlock 按真实子网分块 + IPAddress 合并）
# ═══════════════════════════════════════════════════════════════

_IP_STATUS_MAP = {
    'available': IPAddress.IPStatus.AVAILABLE,
    'free': IPAddress.IPStatus.AVAILABLE,
    'assigned': IPAddress.IPStatus.ASSIGNED,
    'allocated': IPAddress.IPStatus.ASSIGNED,
    'reserved': IPAddress.IPStatus.RESERVED,
    'recycled': IPAddress.IPStatus.BLOCKED,
    'blocked': IPAddress.IPStatus.BLOCKED,
}

_DEFAULT_PREFIX = {'v4': 24, 'v6': 64}


def _network_cidr(addr, prefix):
    return str(ipaddress.ip_network(f'{addr}/{prefix}', strict=False))


def _build_ip_plan(iface_addrs, managed_ips):
    """
    汇总内核接口地址 + 受管 IP，生成按子网分块的写入计划：
      { cidr: { 'version': 'v4'|'v6', 'addresses': { addr: {prefix,status,description} } } }
    受管 IP（带 customer/state）优先级高于内核地址。
    """
    candidates = []  # [(ip_network, prefixlen)]
    for a in iface_addrs:
        try:
            net = ipaddress.ip_network(f"{a['address']}/{a['prefix']}", strict=False)
            candidates.append(net)
        except ValueError:
            continue

    plan = {}
    # 地址→所属 CIDR 的全局映射：保证同一 address 在 POP 内只归属一个块，
    # 避免同地址（如接口 /24 与受管 /32）落入不同块导致 POP 级 IP 统计重复计数。
    addr_home = {}

    def _put(addr, prefix, version, status, description, managed=False, vm_ref='',
             interface_code='', subnet_label=''):
        # 统一过滤 0.0.0.0/:: / loopback / link-local 等无效地址
        if _is_ignorable_ip(addr):
            return
        try:
            cidr = _network_cidr(addr, prefix)
        except ValueError:
            return
        # 过滤 0.0.0.0/0 / ::/0 之类的无效段
        if _is_ignorable_cidr(cidr):
            return
        # 若该地址已归属某个块，沿用既有块（不再新建第二个块）
        home_cidr = addr_home.get(addr)
        if home_cidr is not None and home_cidr != cidr:
            cidr = home_cidr
        else:
            addr_home[addr] = cidr
        block = plan.setdefault(cidr, {'version': version, 'addresses': {}})
        cur = block['addresses'].get(addr)
        meta = {
            'prefix': prefix, 'status': status,
            'description': description, 'managed': managed,
            'vm_ref': vm_ref or '',
            'interface_code': interface_code or '',
            'subnet_label': subnet_label or '',
        }
        if cur is None:
            block['addresses'][addr] = meta
        else:
            if managed and not cur.get('managed'):
                cur.update(meta)
            else:
                for k in ('interface_code', 'subnet_label', 'vm_ref'):
                    if meta.get(k) and not cur.get(k):
                        cur[k] = meta[k]

    # 内核接口地址（系统在用）
    for a in iface_addrs:
        _put(a['address'], a['prefix'], a['version'],
             IPAddress.IPStatus.SYSTEM,
             f"{a['ifname']} 接口地址", managed=False)

    # 受管 IP
    for m in managed_ips or []:
        if not isinstance(m, dict):
            continue
        addr = _first(m, 'address', 'ip')
        if not addr:
            continue
        try:
            ip_obj = ipaddress.ip_address(str(addr))
        except ValueError:
            continue
        version = 'v6' if ip_obj.version == 6 else 'v4'
        # 找包含它的接口子网，否则用默认前缀
        prefix = None
        for net in candidates:
            if net.version == ip_obj.version and ip_obj in net:
                prefix = net.prefixlen
                break
        if prefix is None:
            prefix = _DEFAULT_PREFIX[version]

        status = _IP_STATUS_MAP.get(
            str(_first(m, 'state', 'status', default='') or '').lower(),
            IPAddress.IPStatus.AVAILABLE,
        )
        label = _first(m, 'subnet_label', 'description', default='') or ''
        cust = _first(m, 'customer_name', 'customer_code', default='')
        interface_code = str(_first(m, 'interface_code', default='') or '')[:128]
        subnet_label = str(label)[:128]
        desc_parts = [p for p in (subnet_label, (f'客户:{cust}' if cust else '')) if p]
        desc = ' / '.join(desc_parts) or '节点受管 IP'
        # 解析该受管 IP 绑定的节点 VM：优先 extra.pve_vm_pk，否则解析 interface_code=vm-<pk>-net<n>
        vm_ref = ''
        extra = m.get('extra') if isinstance(m.get('extra'), dict) else {}
        if extra.get('pve_vm_pk') not in (None, ''):
            vm_ref = str(extra.get('pve_vm_pk'))
        else:
            mm = _VM_IFCODE_RE.match(str(_first(m, 'interface_code', default='') or ''))
            if mm:
                vm_ref = mm.group(1)
        _put(str(addr), prefix, version, status, desc[:256], managed=True, vm_ref=vm_ref,
             interface_code=interface_code, subnet_label=subnet_label)

    return plan


def _sync_ip_addresses(pop, iface_addrs, managed_ips, now):
    plan = _build_ip_plan(iface_addrs, managed_ips)

    # 节点 VM 主键 → 本地同步 VPS（用于把受管 IP 绑定到 assigned_vps）
    vps_by_ref = {
        str(v.source_ref): v
        for v in VPS.objects.filter(pop_point=pop, is_synced=True)
        if v.source_ref
    }
    edge_by_ifname = {
        e.node_ifname: e
        for e in EdgeAcceleration.objects.filter(pop_point=pop).exclude(node_ifname='')
    }

    block_seen_ids = []
    addr_seen_ids = []
    blocks_created = blocks_updated = 0
    addr_created = addr_updated = 0

    for cidr, info in plan.items():
        version = (IPBlock.IPVersion.V6 if info['version'] == 'v6'
                   else IPBlock.IPVersion.V4)
        block, is_new_block = IPBlock.objects.update_or_create(
            pop_point=pop, cidr=cidr,
            defaults={
                'ip_version': version,
                'is_active': True,
                'is_synced': True,
                'last_synced_at': now,
                'description': '节点同步导入',
            },
        )
        blocks_created += int(is_new_block)
        blocks_updated += int(not is_new_block)
        block_seen_ids.append(block.pk)

        for addr, meta in info['addresses'].items():
            assigned_vps = vps_by_ref.get(str(meta.get('vm_ref') or '')) if meta.get('vm_ref') else None
            iface_code = meta.get('interface_code') or ''
            assigned_edge = None
            if not assigned_vps and iface_code:
                assigned_edge = edge_by_ifname.get(iface_code)
            obj, is_new = IPAddress.objects.update_or_create(
                ip_block=block, address=addr,
                defaults={
                    'prefix_len': meta['prefix'],
                    'status': meta['status'],
                    'description': meta['description'],
                    'subnet_label': meta.get('subnet_label') or '',
                    'interface_code': iface_code,
                    'assigned_vps': assigned_vps,
                    'assigned_edge': assigned_edge if not assigned_vps else None,
                    'is_synced': True,
                    'last_synced_at': now,
                },
            )
            addr_created += int(is_new)
            addr_updated += int(not is_new)
            addr_seen_ids.append(obj.pk)

    # 对账：删除本轮未出现的同步 IP（排除隧道段块，那些由 _sync_tunnel_ip_segments 单独对账）
    addr_deleted = _reconcile(
        IPAddress.objects.filter(ip_block__pop_point=pop)
        .exclude(ip_block__description__startswith=_TUNNEL_BLOCK_TAG),
        addr_seen_ids,
    )
    # 对账：删除空的、已同步的地址块（排除隧道段块）
    blocks_deleted = 0
    stale_blocks = (
        IPBlock.objects.filter(pop_point=pop, is_synced=True)
        .exclude(id__in=block_seen_ids)
        .exclude(description__startswith=_TUNNEL_BLOCK_TAG)
    )
    for b in stale_blocks:
        if not b.ip_addresses.exists():
            b.delete()
            blocks_deleted += 1

    return {
        'blocks_created': blocks_created, 'blocks_updated': blocks_updated,
        'blocks_deleted': blocks_deleted,
        'created': addr_created, 'updated': addr_updated, 'deleted': addr_deleted,
        'total': len(addr_seen_ids),
    }


# ═══════════════════════════════════════════════════════════════
# § 4b  隧道 / 边缘 IP 地址段（TunnelIPPool + desired-tunnels）
# ═══════════════════════════════════════════════════════════════

_POOL_STATE_MAP = {
    'available': IPAddress.IPStatus.AVAILABLE,
    'free': IPAddress.IPStatus.AVAILABLE,
    'reserved': IPAddress.IPStatus.RESERVED,
    'allocated': IPAddress.IPStatus.ASSIGNED,
    'assigned': IPAddress.IPStatus.ASSIGNED,
    'recycled': IPAddress.IPStatus.BLOCKED,
    'blocked': IPAddress.IPStatus.BLOCKED,
}


def _sync_tunnel_ip_segments(pop, client, now):
    """同步节点隧道 / 边缘 IP 地址段到 IPBlock/IPAddress。

    数据来源：
      1. tunnel-ip-pools：每个池的 `cidr` → IPBlock（隧道段，带 _TUNNEL_BLOCK_TAG 标记）；
         池内已分配 p2p 条目（local/peer）→ IPAddress（interface_code=隧道 ifname，状态按 state）。
      2. desired-tunnels：`spec.netplan_tunnel.addresses` 的本端地址 → 若其所在网络在本 POP
         尚无 IPBlock，则新建隧道段块 + IPAddress；已存在则交由普通 IP 同步处理，避免重复块。
    通过 ifname == EdgeAcceleration.node_ifname 回填 IPAddress.assigned_edge。
    统一过滤 0.0.0.0/0、::/0 等无效段（需求3）。
    """
    edge_by_ifname = {
        e.node_ifname: e
        for e in EdgeAcceleration.objects.filter(pop_point=pop).exclude(node_ifname='')
    }

    # plan[cidr] = {'version': 'v4'|'v6', 'addresses': {addr: {...}}}
    plan = {}

    def _seg(cidr, version):
        return plan.setdefault(cidr, {'version': version, 'addresses': {}})

    def _add(cidr, version, addr, prefix, status, ifname='', desc=''):
        if _is_ignorable_ip(addr) or _is_ignorable_cidr(cidr):
            return
        seg = _seg(cidr, version)
        cur = seg['addresses'].get(addr)
        meta = {
            'prefix': prefix, 'status': status,
            'interface_code': ifname or '', 'description': desc or '',
        }
        # allocated/assigned 优先，避免被 available 覆盖
        if cur is None or (status == IPAddress.IPStatus.ASSIGNED):
            seg['addresses'][addr] = meta

    # ── 1) 隧道地址段池 ──────────────────────────────────────
    try:
        pools = client.tunnel_ip_pools() or []
    except PemanagerError:
        pools = []
    pool_seen = []  # 本轮见到的节点池 → 回写 TunnelIPPool
    for p in pools:
        if not isinstance(p, dict):
            continue
        cidr = p.get('cidr')
        if not cidr or _is_ignorable_cidr(cidr):
            continue
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except ValueError:
            continue
        norm = str(net)
        version = 'v6' if net.version == 6 else 'v4'
        _seg(norm, version)  # 确保段存在（即使暂无成员 IP）
        point_prefix = _to_int(p.get('point_prefixlen'), net.prefixlen)
        pool_name = p.get('name') or ''
        pool_id = p.get('id')
        if pool_id is None:
            continue

        # 回填/更新中央侧 TunnelIPPool 登记（is_synced，source_ref=节点池 id）
        if pool_name:
            pool_obj, _ = TunnelIPPool.objects.update_or_create(
                pop_point=pop, name=pool_name,
                defaults={
                    'cidr': norm,
                    'point_prefixlen': point_prefix or net.prefixlen,
                    'customer': _resolve_customer(p.get('customer')),
                    'is_active': bool(p.get('is_active', True)),
                    'remark': (p.get('remark') or '')[:512],
                    'is_synced': True,
                    'source_ref': str(pool_id),
                    'last_synced_at': now,
                },
            )
            pool_seen.append(pool_obj.pk)
        try:
            usage = client.tunnel_ip_pool_usage(pool_id) or {}
        except PemanagerError:
            usage = {}
        for blk in (usage.get('used') or []):
            if not isinstance(blk, dict):
                continue
            for ent in (blk.get('entries') or []):
                if not isinstance(ent, dict):
                    continue
                a = ent.get('address')
                if not a:
                    continue
                st = _POOL_STATE_MAP.get(
                    str(ent.get('state') or '').lower(), IPAddress.IPStatus.RESERVED)
                ic = str(ent.get('interface_code') or '')[:128]
                _add(norm, version, str(a), point_prefix, st, ifname=ic,
                     desc=f'{_TUNNEL_BLOCK_TAG} 池:{pool_name}')

    # ── 2) desired-tunnels 本端地址段 ────────────────────────
    try:
        tunnels = client.list_desired_tunnels() or []
    except PemanagerError:
        tunnels = []
    existing_cidrs = set(
        IPBlock.objects.filter(pop_point=pop).values_list('cidr', flat=True)
    )
    for t in tunnels:
        if not isinstance(t, dict):
            continue
        ifname = t.get('ifname') or ''
        spec = t.get('spec') if isinstance(t.get('spec'), dict) else {}
        nt = spec.get('netplan_tunnel') if isinstance(spec.get('netplan_tunnel'), dict) else {}
        addrs = nt.get('addresses') or t.get('local_addresses') or []
        for a in addrs:
            if not a:
                continue
            raw = str(a)
            try:
                iface = ipaddress.ip_interface(raw if '/' in raw else f'{raw}/32')
            except ValueError:
                continue
            cidr = str(iface.network)
            if _is_ignorable_ip(str(iface.ip)) or _is_ignorable_cidr(cidr):
                continue
            # 该网络已在本 POP 存在块（含普通同步块）：不重复建块，避免跨步骤争抢
            if cidr in existing_cidrs and cidr not in plan:
                continue
            version = 'v6' if iface.version == 6 else 'v4'
            _add(cidr, version, str(iface.ip), iface.network.prefixlen,
                 IPAddress.IPStatus.ASSIGNED, ifname=ifname,
                 desc=f'{_TUNNEL_BLOCK_TAG} 隧道:{ifname}')

    # ── 写入 ────────────────────────────────────────────────
    block_seen = []
    addr_seen = []
    blocks_created = addr_created = 0
    for cidr, info in plan.items():
        version = (IPBlock.IPVersion.V6 if info['version'] == 'v6'
                   else IPBlock.IPVersion.V4)
        block, is_new = IPBlock.objects.update_or_create(
            pop_point=pop, cidr=cidr,
            defaults={
                'ip_version': version,
                'is_active': True,
                'is_synced': True,
                'last_synced_at': now,
                'description': f'{_TUNNEL_BLOCK_TAG} 隧道/边缘 IP 段',
            },
        )
        blocks_created += int(is_new)
        block_seen.append(block.pk)
        for addr, meta in info['addresses'].items():
            edge = edge_by_ifname.get(meta.get('interface_code') or '')
            obj, new = IPAddress.objects.update_or_create(
                ip_block=block, address=addr,
                defaults={
                    'prefix_len': meta['prefix'],
                    'status': meta['status'],
                    'description': (meta['description'] or '')[:256],
                    'interface_code': meta.get('interface_code') or '',
                    'assigned_edge': edge,
                    'is_synced': True,
                    'last_synced_at': now,
                },
            )
            addr_created += int(new)
            addr_seen.append(obj.pk)

    # ── 对账：仅清理隧道段标记的陈旧块/地址 ─────────────────
    tunnel_blocks_qs = IPBlock.objects.filter(
        pop_point=pop, is_synced=True,
        description__startswith=_TUNNEL_BLOCK_TAG,
    )
    IPAddress.objects.filter(
        ip_block__in=tunnel_blocks_qs, is_synced=True,
    ).exclude(id__in=addr_seen).delete()
    for b in tunnel_blocks_qs.exclude(id__in=block_seen):
        if not b.ip_addresses.exists():
            b.delete()

    # ── 对账：清理本 POP 下已不存在于节点的同步隧道地址段登记 ──
    TunnelIPPool.objects.filter(
        pop_point=pop, is_synced=True,
    ).exclude(id__in=pool_seen).delete()

    return {
        'segments': len(block_seen),
        'blocks_created': blocks_created,
        'addresses': len(addr_seen),
        'addr_created': addr_created,
        'pools': len(pool_seen),
    }


# ═══════════════════════════════════════════════════════════════
# § 5  PVE 虚机 → VPS（vpopmanager 专有）
# ═══════════════════════════════════════════════════════════════

_VM_STATUS_MAP = {
    'running': VPS.VPSStatus.RUNNING,
    'stopped': VPS.VPSStatus.STOPPED,
    'paused': VPS.VPSStatus.SUSPENDED,
    'suspended': VPS.VPSStatus.SUSPENDED,
}


def _vm_mgmt_ip(config):
    """从 cloud-init ipconfig0（ip=10.0.0.20/24,gw=...）解析管理 IP。"""
    if not isinstance(config, dict):
        return None
    for key in ('ipconfig0', 'ipconfig1'):
        raw = config.get(key)
        if not raw:
            continue
        for part in str(raw).split(','):
            part = part.strip()
            if part.lower().startswith('ip='):
                val = part[3:].split('/')[0].strip()
                if val and val.lower() != 'dhcp':
                    try:
                        ipaddress.ip_address(val)
                        return val
                    except ValueError:
                        pass
    return None


def _vm_bandwidth_mbps(config):
    """从 PVE config 的 netN（rate=<MB/s>）累加估算带宽（Mbps = MB/s * 8）。"""
    if not isinstance(config, dict):
        return 0
    total = 0
    for key, raw in config.items():
        if not (isinstance(key, str) and key.startswith('net') and key[3:].isdigit()):
            continue
        for part in str(raw).split(','):
            part = part.strip()
            if part.lower().startswith('rate='):
                try:
                    total += int(round(float(part[5:]) * 8))
                except (ValueError, TypeError):
                    pass
    return total


def _pop_client(pop: PopPoint) -> PemanagerClient:
    """构建 POP 节点 API 客户端；配置缺失时抛出可读错误。有 Token 时允许无密码。"""
    return PemanagerClient.from_pop(pop)


def _find_vm_item(items, vps: VPS):
    """在节点 VM 列表中按 source_ref / source_vmid 匹配。"""
    ref = str(vps.source_ref or '').strip()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        item_ref = str(_first(it, 'id', 'pk', default='') or '')
        if ref and item_ref == ref:
            return it
    if vps.source_vmid is not None:
        for it in items or []:
            if not isinstance(it, dict):
                continue
            if _to_int(it.get('vmid')) == vps.source_vmid:
                return it
    return None


def _upsert_vm_from_item(pop, it, now):
    """单条 PVE 虚机 upsert（不做对账删除）。返回 (vps, created_bool)。"""
    vmid = _to_int(it.get('vmid'))
    if vmid is None:
        raise PemanagerError('节点 VM 数据缺少 vmid')

    config = it.get('config') if isinstance(it.get('config'), dict) else {}
    cores = _to_int(config.get('cores'), 1) or 1
    sockets = _to_int(config.get('sockets'), 1) or 1
    vcpu = max(1, cores * sockets)

    mem_max = _to_int(it.get('memory_max'), 0) or 0
    if mem_max > 0:
        ram_mb = max(1, mem_max // (1024 * 1024))
    else:
        ram_mb = _to_int(config.get('memory'), 512) or 512

    disk_max = _to_int(it.get('disk_max'), 0) or 0
    disk_gb = max(0, disk_max // (1024 * 1024 * 1024)) if disk_max else 0

    status = _VM_STATUS_MAP.get(
        str(it.get('status') or '').lower(), VPS.VPSStatus.STOPPED)
    name = (it.get('name') or f'vm-{vmid}')[:128]
    node = (it.get('node') or '')[:128]
    mgmt_ip = _vm_mgmt_ip(config)
    bw = _vm_bandwidth_mbps(config)
    source_ref = str(_first(it, 'id', 'pk', default='') or '')[:64]
    host_id = str(_first(it, 'host', 'host_id', default='') or '')[:64]
    cust_code = it.get('customer_code') or ''
    remark = f'PVE 同步：vmid={vmid} node={node}'
    if cust_code:
        remark += f' 客户={cust_code}'

    vps = VPS.objects.filter(pop_point=pop, source_vmid=vmid, is_synced=True).first()
    created = vps is None
    if vps is None:
        vps = VPS(
            pop_point=pop, source_vmid=vmid, is_synced=True,
            bandwidth_mbps=0,
        )

    vps.name = name
    vps.status = status
    vps.vcpu = vcpu
    vps.ram_mb = ram_mb
    vps.disk_gb = disk_gb
    vps.source_node = node
    if source_ref:
        vps.source_ref = source_ref
    if host_id:
        vps.source_host_id = host_id
    vps.bandwidth_mbps = bw
    vps.last_synced_at = now
    vps.remark = remark
    if mgmt_ip:
        vps.mgmt_ip = mgmt_ip
    cust = _resolve_customer(cust_code)
    if cust is not None:
        vps.customer = cust
    vps.save()
    return vps, created


def _sync_vms(pop, items, now):
    """把节点 PVE 虚机映射为 ISPmanager VPS（按 source_vmid 对齐）。"""
    created = updated = 0
    seen_vmids = []

    for it in items or []:
        if not isinstance(it, dict):
            continue
        vmid = _to_int(it.get('vmid'))
        if vmid is None:
            continue
        seen_vmids.append(vmid)
        _, is_new = _upsert_vm_from_item(pop, it, now)
        if is_new:
            created += 1
        else:
            updated += 1

    # 对账：删除本轮未出现的同步虚机（手工 VPS 不受影响）
    stale = VPS.objects.filter(pop_point=pop, is_synced=True).exclude(source_vmid__in=seen_vmids)
    deleted = stale.count()
    if deleted:
        stale.delete()

    return {'created': created, 'updated': updated, 'deleted': deleted,
            'total': len(seen_vmids)}


# ═══════════════════════════════════════════════════════════════
# § 6  带宽分配（节点 bandwidth-allocations → BandwidthAllocation）
# ═══════════════════════════════════════════════════════════════

def _sync_bandwidth(pop, items, now):
    """
    把节点的带宽分配落库到 ISPmanager BandwidthAllocation。

    - VM 网卡：interface_code 形如 vm-<节点VM主键>-net<idx> → 同步 VPS
    - 隧道/其它接口：按 EdgeAcceleration.node_ifname 对齐边缘加速；
      对不上也仍入库（target_type=edge，记下 source_interface_code），
      以便带宽管理能看到节点侧给隧道分配的带宽。
    """
    vps_by_ref = {
        str(v.source_ref): v
        for v in VPS.objects.filter(pop_point=pop, is_synced=True)
        if v.source_ref
    }
    edge_by_ifname = {
        e.node_ifname: e
        for e in EdgeAcceleration.objects.filter(pop_point=pop).exclude(node_ifname='')
        if e.node_ifname
    }
    prev_edge_ids = set(
        BandwidthAllocation.objects.filter(
            pop_point=pop, is_synced=True, edge__isnull=False,
        ).values_list('edge_id', flat=True)
    )

    seen_codes = []
    vps_bw_sum = {}
    edge_bw_sum = {}
    imported = updated = skipped = 0

    for a in items or []:
        if not isinstance(a, dict):
            continue
        code = str(_first(a, 'interface_code', default='') or '').strip()
        if not code:
            skipped += 1
            continue
        mbps = _to_int(a.get('allocated_mbps'), 0) or 0
        m = _VM_IFCODE_RE.match(code)
        if m:
            vps = vps_by_ref.get(m.group(1))
            if vps is None:
                skipped += 1
                continue
            defaults = {
                'target_type': BandwidthAllocation.AllocTarget.VPS,
                'vps': vps, 'edge': None,
                'allocated_mbps': mbps, 'is_active': True,
                'last_synced_at': now,
            }
            vps_bw_sum[vps.pk] = vps_bw_sum.get(vps.pk, 0) + mbps
        else:
            edge = edge_by_ifname.get(code)
            defaults = {
                'target_type': BandwidthAllocation.AllocTarget.EDGE,
                'vps': None, 'edge': edge,
                'allocated_mbps': mbps, 'is_active': True,
                'last_synced_at': now,
            }
            if edge is not None:
                edge_bw_sum[edge.pk] = edge_bw_sum.get(edge.pk, 0) + mbps

        _obj, is_new = BandwidthAllocation.objects.update_or_create(
            pop_point=pop, source_interface_code=code, is_synced=True,
            defaults=defaults,
        )
        imported += int(is_new)
        updated += int(not is_new)
        seen_codes.append(code)

    stale = (
        BandwidthAllocation.objects
        .filter(pop_point=pop, is_synced=True)
        .exclude(source_interface_code__in=seen_codes)
    )
    deleted = stale.count()
    if deleted:
        stale.delete()

    zeroed = 0
    for vps in vps_by_ref.values():
        total = vps_bw_sum.get(vps.pk, 0)
        if vps.bandwidth_mbps != total:
            VPS.objects.filter(pk=vps.pk).update(bandwidth_mbps=total)
            if total == 0:
                zeroed += 1

    for edge_id in prev_edge_ids | set(edge_bw_sum):
        total = edge_bw_sum.get(edge_id, 0)
        EdgeAcceleration.objects.filter(pk=edge_id).update(bandwidth_mbps=total)

    return {
        'imported': imported, 'updated': updated,
        'deleted': deleted, 'skipped': skipped, 'zeroed_vps': zeroed,
        'fetched': len(items or []),
        'edges': len(edge_bw_sum),
    }


def _sync_single_vps_bandwidth(vps: VPS, pop, items, now):
    """仅同步单台 VPS 的带宽分配（不影响同 POP 其它 VPS）。"""
    ref = str(vps.source_ref or '').strip()
    if not ref:
        return {'skipped': True, 'reason': '缺少 source_ref'}

    seen_codes = []
    total_mbps = 0
    imported = updated = 0

    for a in items or []:
        if not isinstance(a, dict):
            continue
        code = str(_first(a, 'interface_code', default='') or '')
        mbps = _to_int(a.get('allocated_mbps'), 0) or 0
        m = _VM_IFCODE_RE.match(code)
        if not m or m.group(1) != ref:
            continue

        obj, is_new = BandwidthAllocation.objects.update_or_create(
            pop_point=pop, source_interface_code=code, is_synced=True,
            defaults={
                'target_type': BandwidthAllocation.AllocTarget.VPS,
                'vps': vps, 'edge': None,
                'allocated_mbps': mbps, 'is_active': True,
                'last_synced_at': now,
            },
        )
        imported += int(is_new)
        updated += int(not is_new)
        seen_codes.append(code)
        total_mbps += mbps

    stale = (
        BandwidthAllocation.objects
        .filter(pop_point=pop, vps=vps, is_synced=True)
        .exclude(source_interface_code__in=seen_codes)
    )
    deleted = stale.count()
    if deleted:
        stale.delete()

    if vps.bandwidth_mbps != total_mbps:
        VPS.objects.filter(pk=vps.pk).update(bandwidth_mbps=total_mbps)

    return {
        'imported': imported, 'updated': updated, 'deleted': deleted,
        'bandwidth_mbps': total_mbps,
    }


def sync_single_vps_from_node(vps: VPS):
    """
    仅同步单台 VPS 的 PVE 状态与带宽（不触发 POP 全量对账，避免误删其它虚机）。
    返回 {ok, node_user, summary, errors}。
    """
    if not vps.is_synced:
        raise PemanagerError('该 VPS 为手工创建记录，不支持从节点同步')
    pop = vps.pop_point
    if not pop:
        raise PemanagerError('VPS 未关联 POP 节点')

    client = _pop_client(pop)
    user = client.authenticate()
    now = timezone.now()
    summary = {}
    errors = {}

    try:
        client.sync_vms()
    except PemanagerError as e:
        errors['sync_cache'] = str(e)

    try:
        vm_items = client.vms()
        match = _find_vm_item(vm_items, vps)
        if match is None:
            raise PemanagerError(
                f'节点 VM 列表中未找到 vmid={vps.source_vmid} / ref={vps.source_ref}；'
                '请确认 POP 节点 PVE 模块正常，或在 POP 详情执行「从节点同步」'
            )
        with transaction.atomic():
            obj, created = _upsert_vm_from_item(pop, match, now)
            summary['vps'] = {
                'id': obj.pk, 'created': int(created), 'updated': int(not created),
                'name': obj.name, 'status': obj.status,
            }
    except PemanagerError as e:
        errors['vps'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['vps'] = f'写入失败：{e}'

    if 'vps' not in errors:
        vps.refresh_from_db()
        try:
            bw_items = client.bandwidth_allocations()
            with transaction.atomic():
                summary['bandwidth'] = _sync_single_vps_bandwidth(vps, pop, bw_items, now)
        except PemanagerError as e:
            errors['bandwidth'] = str(e)
        except Exception as e:  # noqa: BLE001
            errors['bandwidth'] = f'写入失败：{e}'

    return {
        'ok': 'vps' not in errors,
        'node_user': user,
        'summary': summary,
        'errors': errors,
    }


# ═══════════════════════════════════════════════════════════════
# § 7  主入口
# ═══════════════════════════════════════════════════════════════

def sync_pop_from_node(pop: PopPoint):
    """从节点拉取并同步，返回 {ok, node_user, summary, errors}。"""
    client = _pop_client(pop)
    user = client.authenticate()

    now = timezone.now()
    summary = {}
    errors = {}

    # 1) 接口（必须先于路由/IP，路由按 dev 关联接口、IP 复用接口地址）
    iface_addrs = []
    try:
        iface_items = client.interfaces()
        with transaction.atomic():
            summary['interfaces'], iface_addrs = _sync_interfaces(pop, iface_items, now)
    except PemanagerError as e:
        errors['interfaces'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['interfaces'] = f'写入失败：{e}'

    # 2) 静态路由（按表分组）
    try:
        route_items = client.system_routes(table='all')
        with transaction.atomic():
            summary['static_routes'] = _sync_routes(pop, route_items, now)
    except PemanagerError as e:
        errors['static_routes'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['static_routes'] = f'写入失败：{e}'

    # 3) 策略路由
    try:
        rule_items = client.system_rules()
        with transaction.atomic():
            summary['policy_rules'] = _sync_policy_rules(pop, rule_items, now)
    except PemanagerError as e:
        errors['policy_rules'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['policy_rules'] = f'写入失败：{e}'

    # 4) IP 地址（内核地址 + 受管 IP）
    try:
        managed_ips = client.ip_addresses()
        with transaction.atomic():
            summary['ip_addresses'] = _sync_ip_addresses(pop, iface_addrs, managed_ips, now)
    except PemanagerError as e:
        errors['ip_addresses'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['ip_addresses'] = f'写入失败：{e}'

    # 4b) 隧道 / 边缘 IP 地址段（TunnelIPPool + desired-tunnels）
    try:
        with transaction.atomic():
            summary['tunnel_segments'] = _sync_tunnel_ip_segments(pop, client, now)
    except PemanagerError as e:
        errors['tunnel_segments'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['tunnel_segments'] = f'写入失败：{e}'

    # 5) PVE 虚机 → VPS（vpopmanager 专有；pemanager 无该模块时返回空）
    try:
        vm_items = client.vms()
        with transaction.atomic():
            summary['vms'] = _sync_vms(pop, vm_items, now)
    except PemanagerError as e:
        errors['vms'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['vms'] = f'写入失败：{e}'

    # 6) 带宽分配（节点 bandwidth-allocations → BandwidthAllocation，VM 网卡分配入库）
    try:
        bw = client.bandwidth_allocations()
        with transaction.atomic():
            summary['bandwidth'] = _sync_bandwidth(pop, bw, now)
    except PemanagerError as e:
        errors['bandwidth'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['bandwidth'] = f'写入失败：{e}'

    # 7) 手工地址块 IP → 节点 resourcemanage（Outbound 对账录入）
    try:
        from . import ip_ops
        summary['ip_outbound'] = ip_ops.push_pop_ip_inventory_to_node(pop, client=client)
    except PemanagerError as e:
        errors['ip_outbound'] = str(e)
    except Exception as e:  # noqa: BLE001
        errors['ip_outbound'] = f'写入失败：{e}'

    pop.last_sync_at = now
    if pop.deploy_status != PopPoint.DeployStatus.DEPLOYED:
        pop.deploy_status = PopPoint.DeployStatus.DEPLOYED
    pop.save(update_fields=['last_sync_at', 'deploy_status', 'updated_at'])

    return {'ok': not errors, 'node_user': user, 'summary': summary, 'errors': errors}
