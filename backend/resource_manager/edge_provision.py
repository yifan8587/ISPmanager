"""
边缘加速自动开通：预分配资源，待客户补全协议后再下发。

购买支付后只做本地建档与资源预占，不把未完成的隧道推到 POP：
  - 带宽按采购规格写入 BandwidthAllocation（无 node_ifname 时不下节点）
  - 业务 IP 从该 POP 未分配地址中领取（隧道段除外）
  - 隧道 p2p 块从 TunnelIPPool 领取并占用
  - 我司对接地址固定为 POP.mgmt_ip
客户在门户补全远端 / 对端密钥后「保存并下发」，节点生效后再激活实例，
并按 /32 主机路由把已分配业务 IP 下到隧道接口。
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from .ip_scope import TUNNEL_BLOCK_TAG, exclude_tunnel_addresses
from .models import (
    BandwidthAllocation,
    EdgeAcceleration,
    GREConfig,
    IPAddress,
    IPBlock,
    TunnelIPPool,
    WireguardConfig,
)
from .pemanager_client import pop_has_api_config
from .tunnel_ip import find_next_free_block

logger = logging.getLogger(__name__)

WG_LISTEN_PORT_START = 51820

_LOCKED_FIELDS = {
    EdgeAcceleration.Protocol.GRE: ('local_ip', 'tunnel_ip'),
    EdgeAcceleration.Protocol.WIREGUARD: ('interface_ip', 'listen_port'),
}


def pop_peer_ip(pop, *, required: bool = True) -> str:
    """隧道对接我司地址：以 POP 管理 IP 为准。"""
    ip = (getattr(pop, 'mgmt_ip', None) or '').strip()
    if not ip and required:
        raise ValueError('POP 点未配置管理 IP，无法作为隧道对接地址')
    return ip


def pick_tunnel_pool(pop, customer=None) -> TunnelIPPool:
    qs = TunnelIPPool.objects.select_for_update().filter(pop_point=pop, is_active=True)
    if customer is not None:
        pool = qs.filter(customer=customer).first()
        if pool is not None:
            return pool
    pool = qs.filter(customer__isnull=True).first() or qs.first()
    if pool is None:
        raise ValueError('该 POP 未配置可用的隧道 IP 地址段，无法自动分配隧道地址')
    return pool


def _tunnel_ip_block(pop, pool: TunnelIPPool) -> IPBlock:
    tagged = IPBlock.objects.filter(
        pop_point=pop,
        cidr=pool.cidr,
        description__startswith=TUNNEL_BLOCK_TAG,
    ).first()
    if tagged is not None:
        return tagged
    return IPBlock.objects.create(
        pop_point=pop,
        cidr=pool.cidr,
        description=f'{TUNNEL_BLOCK_TAG} {pool.name}',
        is_active=True,
    )


def reserve_tunnel_block(pop, edge: EdgeAcceleration, customer=None) -> dict[str, Any]:
    """从 POP 隧道池领取下一空闲 p2p 块，并占用本端/对端地址。"""
    pool = pick_tunnel_pool(pop, customer=customer or edge.customer)
    block = find_next_free_block(pool)
    if block is None:
        raise ValueError(f'隧道 IP 地址段 {pool.name}（{pool.cidr}）已无可用 p2p 块')

    ip_block = _tunnel_ip_block(pop, pool)
    now = timezone.now()
    plen = int(block['prefixlen'])
    for addr, role in ((block['local'], '本端'), (block['peer'], '对端')):
        ip = (
            IPAddress.objects.select_for_update()
            .filter(ip_block=ip_block, address=addr)
            .first()
        )
        if ip is None:
            IPAddress.objects.create(
                ip_block=ip_block,
                address=addr,
                prefix_len=plen,
                status=IPAddress.IPStatus.ASSIGNED,
                assigned_edge=edge,
                assigned_at=now,
                description=f'隧道{role} {edge.edge_no}',
            )
            continue
        if (
            ip.status not in (IPAddress.IPStatus.AVAILABLE, IPAddress.IPStatus.RESERVED)
            and ip.assigned_edge_id != edge.id
        ):
            raise ValueError(f'隧道地址 {addr} 已被占用')
        ip.status = IPAddress.IPStatus.ASSIGNED
        ip.assigned_edge = edge
        ip.assigned_at = now
        ip.prefix_len = plen
        ip.description = (ip.description or '')[:180] + f' | 隧道{role} {edge.edge_no}'
        ip.save(update_fields=[
            'status', 'assigned_edge', 'assigned_at', 'prefix_len', 'description',
        ])
    return {**block, 'pool_id': pool.pk, 'pool_name': pool.name}


def generate_wg_keypair() -> tuple[str, str]:
    """本地生成 WireGuard 密钥对（标准 base64 raw X25519）。"""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    private = X25519PrivateKey.generate()
    priv_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_raw).decode('ascii'),
        base64.b64encode(pub_raw).decode('ascii'),
    )


def is_wireguard_key(value) -> bool:
    """WireGuard 密钥是 32 字节的标准 Base64。"""
    text = str(value or '').strip()
    if not text:
        return False
    try:
        raw = base64.b64decode(text, validate=True)
    except Exception:
        return False
    return len(raw) == 32


def wireguard_key_errors(public_key='', private_key='', peer_public_key='', *, require_peer=False) -> list[str]:
    """对端公钥必须是客户自己的公钥，不能填成本端公钥。"""
    public_key = str(public_key or '').strip()
    private_key = str(private_key or '').strip()
    peer_public_key = str(peer_public_key or '').strip()
    errors = []
    if private_key and not is_wireguard_key(private_key):
        errors.append('本端私钥不是有效的 WireGuard 密钥')
    if public_key and not is_wireguard_key(public_key):
        errors.append('本端公钥不是有效的 WireGuard 密钥')
    if require_peer and not peer_public_key:
        errors.append('请填写对端公钥')
    elif peer_public_key and not is_wireguard_key(peer_public_key):
        errors.append('对端公钥不是有效的 WireGuard 密钥，请填写您侧 WireGuard 的公钥')
    if peer_public_key and public_key and peer_public_key == public_key:
        errors.append('对端公钥不能与本端公钥相同，请填写您侧 WireGuard 的公钥，不要填写本端公钥')
    if peer_public_key and private_key and peer_public_key == private_key:
        errors.append('对端公钥不能填写本端私钥')
    return errors


def next_wg_listen_port(pop, start: int = WG_LISTEN_PORT_START, exclude_edge_id=None) -> int:
    """同一 POP 上从 51820 起分配下一个未被占用的监听端口，保证各客户端口不同。"""
    qs = (
        WireguardConfig.objects.filter(edge__pop_point=pop)
        .exclude(listen_port__isnull=True)
    )
    if exclude_edge_id:
        qs = qs.exclude(edge_id=exclude_edge_id)
    used = {int(port) for port in qs.values_list('listen_port', flat=True) if port}
    port = max(int(start), WG_LISTEN_PORT_START)
    while port in used:
        port += 1
        if port > 65535:
            raise ValueError('无可用 WireGuard 监听端口')
    return port


def assign_wg_listen_port(pop, edge, current=None) -> int:
    """已有且不冲突的端口（≥51820）保持不变，否则取该 POP 的下一个端口。"""
    port = int(current or 0)
    if port >= WG_LISTEN_PORT_START:
        clash = (
            WireguardConfig.objects.filter(edge__pop_point=pop, listen_port=port)
            .exclude(edge_id=edge.pk)
            .exists()
        )
        if not clash:
            return port
    return next_wg_listen_port(pop, exclude_edge_id=edge.pk)


def discard_other_protocol_configs(edge: EdgeAcceleration, keep: str) -> None:
    """公网对接只保留一种协议。切换前先删除另一种隧道配置。"""
    from .models import Socks5Config, VXLANConfig

    if keep != EdgeAcceleration.Protocol.GRE:
        GREConfig.objects.filter(edge_id=edge.pk).delete()
    if keep != EdgeAcceleration.Protocol.WIREGUARD:
        WireguardConfig.objects.filter(edge_id=edge.pk).delete()
    if keep in (EdgeAcceleration.Protocol.GRE, EdgeAcceleration.Protocol.WIREGUARD):
        VXLANConfig.objects.filter(edge_id=edge.pk).delete()
        Socks5Config.objects.filter(edge_id=edge.pk).delete()
    for rel in ('gre_config', 'wg_config', 'vxlan_config', 'socks5_config'):
        edge.__dict__.pop(rel, None)


def _existing_tunnel_cidr(edge: EdgeAcceleration) -> str:
    gre = getattr(edge, 'gre_config', None)
    if gre and gre.tunnel_ip:
        return gre.tunnel_ip
    wg = getattr(edge, 'wg_config', None)
    if wg and wg.interface_ip:
        return str(wg.interface_ip).split(',')[0].strip()
    return ''


def ensure_pending_protocol_config(
    edge: EdgeAcceleration,
    *,
    reserve_tunnel: bool = True,
    tunnel_cidr: str = '',
):
    """保证当前协议有一份待补全骨架（本端/隧道地址已分配）。"""
    with transaction.atomic():
        edge = (
            EdgeAcceleration.objects.select_for_update()
            .select_related('pop_point', 'customer', 'gre_config', 'wg_config')
            .get(pk=edge.pk)
        )
        pop = edge.pop_point
        local_ip = pop_peer_ip(pop, required=False)
        tunnel_cidr = (tunnel_cidr or '').strip() or _existing_tunnel_cidr(edge)
        if edge.protocol in (
            EdgeAcceleration.Protocol.GRE,
            EdgeAcceleration.Protocol.WIREGUARD,
        ):
            discard_other_protocol_configs(edge, edge.protocol)
        if reserve_tunnel and not tunnel_cidr:
            try:
                block = reserve_tunnel_block(pop, edge, customer=edge.customer)
                tunnel_cidr = block['local_cidr']
            except ValueError:
                tunnel_cidr = ''

        if edge.protocol == EdgeAcceleration.Protocol.GRE:
            cfg = getattr(edge, 'gre_config', None)
            if cfg is None:
                if not local_ip:
                    return None
                cfg = GREConfig.objects.create(
                    edge=edge,
                    local_ip=local_ip,
                    remote_ip=None,
                    tunnel_ip=tunnel_cidr,
                    mtu=1400,
                    pmtud=True,
                    checksum=False,
                )
            else:
                fields = []
                if local_ip and not cfg.local_ip:
                    cfg.local_ip = local_ip
                    fields.append('local_ip')
                if tunnel_cidr and not cfg.tunnel_ip:
                    cfg.tunnel_ip = tunnel_cidr
                    fields.append('tunnel_ip')
                if fields:
                    cfg.save(update_fields=fields)
            return cfg

        if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
            cfg = getattr(edge, 'wg_config', None)
            if cfg is None:
                private, public = generate_wg_keypair()
                cfg = WireguardConfig.objects.create(
                    edge=edge,
                    listen_port=assign_wg_listen_port(pop, edge),
                    interface_ip=tunnel_cidr,
                    private_key=private,
                    public_key=public,
                    persistent_keepalive=25,
                    mtu=1400,
                )
            else:
                fields = []
                if tunnel_cidr and not cfg.interface_ip:
                    cfg.interface_ip = tunnel_cidr
                    fields.append('interface_ip')
                if not cfg.private_key or not cfg.public_key:
                    private, public = generate_wg_keypair()
                    cfg.private_key = cfg.private_key or private
                    cfg.public_key = cfg.public_key or public
                    fields.extend(['private_key', 'public_key'])
                assigned_port = assign_wg_listen_port(pop, edge, cfg.listen_port)
                if cfg.listen_port != assigned_port:
                    cfg.listen_port = assigned_port
                    fields.append('listen_port')
                if fields:
                    cfg.save(update_fields=list(dict.fromkeys(fields)))
            return cfg
    return None


def apply_locked_protocol_fields(edge, proto, data, config) -> dict:
    """客户不可改写系统预分配的本端 / 隧道地址。"""
    payload = dict(data or {})
    locked = _LOCKED_FIELDS.get(proto, ())
    if config is None:
        return payload
    for field in locked:
        current = getattr(config, field, None)
        if current not in (None, ''):
            payload[field] = current
    return payload


def _tunnel_peer_cidr(local_cidr: str) -> str:
    from .ip_ops import infer_p2p_peer_ip

    if not local_cidr:
        return ''
    peer = infer_p2p_peer_ip(local_cidr)
    if not peer:
        return ''
    prefix = ''
    if '/' in local_cidr:
        prefix = local_cidr.split('/', 1)[1]
    return f'{peer}/{prefix}' if prefix else peer


def edge_config_checklist(edge: EdgeAcceleration) -> list[dict[str, Any]]:
    """门户协议配置待办：系统已分配 vs 客户待补全。"""
    items: list[dict[str, Any]] = []
    proto = edge.protocol
    if proto == EdgeAcceleration.Protocol.GRE:
        cfg = getattr(edge, 'gre_config', None)
        local_ip = getattr(cfg, 'local_ip', None) or pop_peer_ip(edge.pop_point, required=False)
        tunnel_ip = getattr(cfg, 'tunnel_ip', '') if cfg else ''
        remote_ip = getattr(cfg, 'remote_ip', None) if cfg else None
        items.extend([
            {
                'key': 'local_ip',
                'label': '本端 IP（我司对接地址）',
                'value': local_ip or '',
                'source': 'system',
                'required': True,
                'complete': bool(local_ip),
                'editable': False,
                'hint': '以 POP 管理 IP 为准，系统自动分配',
            },
            {
                'key': 'remote_ip',
                'label': '远端 IP（您的公网 IP）',
                'value': remote_ip or '',
                'source': 'customer',
                'required': True,
                'complete': bool(remote_ip),
                'editable': True,
                'hint': '用于建立 GRE 隧道对端',
            },
            {
                'key': 'tunnel_ip',
                'label': '隧道 IP/CIDR（我司侧）',
                'value': tunnel_ip or '',
                'source': 'system',
                'required': True,
                'complete': bool(tunnel_ip),
                'editable': False,
                'hint': '从 POP 隧道地址段自动分配',
            },
            {
                'key': 'tunnel_peer',
                'label': '对端隧道 IP（请在您侧配置）',
                'value': _tunnel_peer_cidr(tunnel_ip),
                'source': 'system',
                'required': False,
                'complete': bool(tunnel_ip),
                'editable': False,
                'hint': '您侧隧道接口请使用该地址',
            },
        ])
    elif proto == EdgeAcceleration.Protocol.WIREGUARD:
        cfg = getattr(edge, 'wg_config', None)
        interface_ip = getattr(cfg, 'interface_ip', '') if cfg else ''
        public_key = getattr(cfg, 'public_key', '') if cfg else ''
        peer_key = getattr(cfg, 'peer_public_key', '') if cfg else ''
        listen_port = getattr(cfg, 'listen_port', None) if cfg else None
        items.extend([
            {
                'key': 'interface_ip',
                'label': '接口 IP/CIDR（我司侧）',
                'value': interface_ip or '',
                'source': 'system',
                'required': True,
                'complete': bool(interface_ip),
                'editable': False,
                'hint': '读取已分配的隧道 IP，不可修改',
            },
            {
                'key': 'public_key',
                'label': '本端公钥（我司）',
                'value': public_key or '',
                'source': 'system',
                'required': True,
                'complete': bool(public_key),
                'editable': False,
                'hint': '请填写到您侧 Peer PublicKey',
            },
            {
                'key': 'listen_port',
                'label': '监听端口',
                'value': listen_port or '',
                'source': 'system',
                'required': True,
                'complete': bool(listen_port),
                'editable': False,
                'hint': '从 51820 起按客户依次分配，不可修改',
            },
            {
                'key': 'peer_public_key',
                'label': '对端公钥（您的公钥）',
                'value': peer_key or '',
                'source': 'customer',
                'required': True,
                'complete': bool(peer_key),
                'editable': True,
                'hint': '您侧 WireGuard 公钥',
            },
            {
                'key': 'tunnel_peer',
                'label': '对端隧道 IP（请在您侧配置）',
                'value': _tunnel_peer_cidr(interface_ip),
                'source': 'system',
                'required': False,
                'complete': bool(interface_ip),
                'editable': False,
                'hint': '您侧 Address 请使用该地址',
            },
        ])
    return items


def edge_config_ready(edge: EdgeAcceleration) -> tuple[bool, list[str]]:
    missing = [
        item['label']
        for item in edge_config_checklist(edge)
        if item.get('required') and not item.get('complete')
    ]
    return not missing, missing


def client_tunnel_guide(edge: EdgeAcceleration) -> dict[str, Any]:
    """客户侧需要配置的隧道参数（只读展示）。"""
    pop = edge.pop_point
    our_ip = pop_peer_ip(pop, required=False)
    if edge.protocol == EdgeAcceleration.Protocol.GRE:
        cfg = getattr(edge, 'gre_config', None)
        tunnel_ip = getattr(cfg, 'tunnel_ip', '') if cfg else ''
        return {
            'protocol': 'gre',
            'our_public_ip': our_ip,
            'our_tunnel_ip': tunnel_ip,
            'your_tunnel_ip': _tunnel_peer_cidr(tunnel_ip),
            'notes': [
                f'GRE 对端（我司）公网 IP：{our_ip or "（POP 未配置管理 IP）"}',
                f'我司隧道 IP：{tunnel_ip or "（待分配）"}',
                f'您侧隧道 IP：{_tunnel_peer_cidr(tunnel_ip) or "（待分配）"}',
                '分配的业务 IP 将在隧道生效后以 /32 主机路由经隧道下发',
            ],
        }
    if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        cfg = getattr(edge, 'wg_config', None)
        interface_ip = getattr(cfg, 'interface_ip', '') if cfg else ''
        port = getattr(cfg, 'listen_port', None) if cfg else 51820
        public_key = getattr(cfg, 'public_key', '') if cfg else ''
        endpoint = f'{our_ip}:{port}' if our_ip and port else our_ip
        return {
            'protocol': 'wireguard',
            'our_public_ip': our_ip,
            'our_endpoint': endpoint,
            'our_public_key': public_key,
            'our_tunnel_ip': interface_ip,
            'your_tunnel_ip': _tunnel_peer_cidr(interface_ip),
            'notes': [
                f'对端 Endpoint：{endpoint or "（待分配）"}',
                f'对端公钥：{public_key or "（待生成）"}',
                f'我司接口 IP：{interface_ip or "（待分配）"}',
                f'您侧 Address：{_tunnel_peer_cidr(interface_ip) or "（待分配）"}',
                '分配的业务 IP 将在隧道生效后以 /32 主机路由经隧道下发',
            ],
        }
    return {'protocol': edge.protocol, 'notes': []}


def edge_business_ip_rows(edge: EdgeAcceleration) -> list[dict[str, Any]]:
    rows = []
    qs = list(exclude_tunnel_addresses(edge.ip_addresses.all()))
    if edge.node_ifname and any(not ip.node_route_ref for ip in qs):
        try:
            from .ip_ops import reconcile_edge_ip_route_refs
            if reconcile_edge_ip_route_refs(edge, ips=qs):
                for ip in qs:
                    ip.refresh_from_db(fields=['node_route_ref'])
        except Exception:  # noqa: BLE001
            logger.debug('边缘 %s 回写业务路由引用失败', getattr(edge, 'edge_no', edge.pk), exc_info=True)
    for ip in qs:
        addr = str(ip.address)
        pushed = bool(ip.node_route_ref)
        rows.append({
            'id': ip.id,
            'address': addr,
            'prefix_len': ip.effective_prefix_len,
            'gateway': ip.effective_gateway,
            'netmask': ip.netmask,
            'route_cidr': f'{addr}/32',
            'route_status': 'pushed' if pushed else 'pending',
            'route_status_label': '已下发' if pushed else '待下发',
            'node_route_ref': ip.node_route_ref or '',
            'is_primary': bool(ip.is_primary),
            'status': ip.status,
        })
    return rows


def needs_protocol_config(edge: EdgeAcceleration | None) -> bool:
    if edge is None:
        return False
    if edge.node_pushed:
        return False
    if edge.status != EdgeAcceleration.EdgeStatus.PENDING:
        return False
    return True


def allocate_edge_bandwidth(pop, edge: EdgeAcceleration, mbps: int, *, product=None, operator=None):
    """按采购规格写本地带宽分配；无隧道接口时不下节点。"""
    from .resource_audit import log_bandwidth_change, snapshot_bandwidth

    mbps = int(mbps or 0) or 100
    existing = BandwidthAllocation.objects.filter(is_synced=False, edge=edge).first()
    if existing is not None:
        if existing.allocated_mbps != mbps:
            old_bw = snapshot_bandwidth(existing)
            existing.allocated_mbps = mbps
            existing.save(update_fields=['allocated_mbps', 'updated_at'])
            log_bandwidth_change(
                existing,
                old_value=old_bw,
                operator=operator,
                source='边缘开通-调整带宽',
            )
        return existing
    alloc = BandwidthAllocation.objects.create(
        pop_point=pop,
        target_type=BandwidthAllocation.AllocTarget.EDGE,
        edge=edge,
        allocated_mbps=mbps,
        is_active=True,
        product=product,
        created_by=operator,
    )
    if edge.bandwidth_mbps != mbps:
        edge.bandwidth_mbps = mbps
        edge.save(update_fields=['bandwidth_mbps', 'updated_at'])
    log_bandwidth_change(alloc, operator=operator, source='边缘开通-分配带宽')
    return alloc


def finalize_edge_after_push(edge: EdgeAcceleration, operator=None) -> dict[str, Any]:
    """隧道在节点生效后：改状态、激活实例、下发业务 IP 的 /32 主机路由。"""
    edge.refresh_from_db()
    if not edge.node_pushed:
        return {'ok': False, 'skipped': True, 'detail': '隧道尚未下发成功'}

    if edge.status != EdgeAcceleration.EdgeStatus.ACTIVE:
        edge.status = EdgeAcceleration.EdgeStatus.ACTIVE
        edge.save(update_fields=['status', 'updated_at'])

    instance = getattr(edge, 'service_instance', None)
    if instance is not None and edge.node_ifname:
        for ip in instance.ip_addresses.all():
            if ip.interface_code != edge.node_ifname:
                ip.interface_code = edge.node_ifname
                ip.save(update_fields=['interface_code'])

    route_errors: list[str] = []
    route_pushed = 0
    business_ips = []
    if instance is not None:
        business_ips = list(instance.ip_addresses.all())
    else:
        business_ips = list(exclude_tunnel_addresses(
            IPAddress.objects.filter(assigned_edge=edge, status=IPAddress.IPStatus.ASSIGNED)
        ))

    pop = edge.pop_point
    if edge.protocol == EdgeAcceleration.Protocol.WIREGUARD:
        from .deploy_service import wireguard_allowed_ip_list
        allowed = wireguard_allowed_ip_list(edge)
        activated = False
        if instance is not None and instance.status != instance.InstanceStatus.ACTIVE:
            try:
                instance.activate(operator=operator)
                activated = True
            except Exception as exc:  # noqa: BLE001
                logger.warning('边缘 %s 实例激活失败：%s', edge.edge_no, exc)
                route_errors.append(f'实例激活失败：{exc}')
        if instance is not None:
            try:
                from service_manager.change_provisioning import refresh_instance_spec_snapshot
                refresh_instance_spec_snapshot(instance)
            except Exception as exc:  # noqa: BLE001
                logger.warning('边缘 %s 刷新规格快照失败：%s', edge.edge_no, exc)
        detail = '业务路由已写入 WireGuard AllowedIPs'
        if allowed:
            detail += '：' + '、'.join(allowed)
        return {
            'ok': True,
            'activated': activated,
            'route_pushed': 0,
            'route_mode': 'wireguard_allowed_ips',
            'allowed_ips': allowed,
            'route_detail': detail,
            'route_errors': route_errors,
            'node_ifname': edge.node_ifname,
            'status': edge.status,
        }

    if business_ips and pop_has_api_config(pop) and edge.node_ifname:
        from .ip_ops import PemanagerError, push_order_ips_to_node
        try:
            result = push_order_ips_to_node(
                pop, business_ips, customer=edge.customer, edge=edge,
            )
            route_pushed = int((result or {}).get('pushed') or 0)
        except PemanagerError as exc:
            route_errors.append(str(exc))
            logger.warning('边缘 %s 业务 IP /32 路由下发失败：%s', edge.edge_no, exc)
            try:
                from .ip_ops import reconcile_edge_ip_route_refs
                synced = reconcile_edge_ip_route_refs(edge, ips=business_ips)
                if synced and not route_pushed:
                    route_pushed = synced
                    route_errors.clear()
            except Exception:  # noqa: BLE001
                logger.debug('边缘 %s 回写已存在业务路由失败', edge.edge_no, exc_info=True)

    activated = False
    if instance is not None and instance.status != instance.InstanceStatus.ACTIVE:
        try:
            instance.activate(operator=operator)
            activated = True
        except Exception as exc:  # noqa: BLE001
            logger.warning('边缘 %s 实例激活失败：%s', edge.edge_no, exc)
            route_errors.append(f'实例激活失败：{exc}')
    if instance is not None:
        try:
            from service_manager.change_provisioning import refresh_instance_spec_snapshot
            refresh_instance_spec_snapshot(instance)
        except Exception as exc:  # noqa: BLE001
            logger.warning('边缘 %s 刷新规格快照失败：%s', edge.edge_no, exc)

    return {
        'ok': not route_errors or activated or edge.node_pushed,
        'activated': activated,
        'route_pushed': route_pushed,
        'route_errors': route_errors,
        'node_ifname': edge.node_ifname,
        'status': edge.status,
    }
