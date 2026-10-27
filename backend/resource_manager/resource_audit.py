"""资源分配 / 回收 / 释放的结构化操作日志。"""
from __future__ import annotations

from .models import ResourceOperationLog, record_resource_log


def snapshot_ip(ip) -> dict:
    return {
        'id': ip.id,
        'address': str(ip.address),
        'status': ip.status,
        'assigned_vps_id': ip.assigned_vps_id,
        'assigned_edge_id': ip.assigned_edge_id,
        'is_primary': bool(ip.is_primary),
        'interface_code': ip.interface_code or '',
        'node_route_ref': ip.node_route_ref or '',
        'assigned_at': ip.assigned_at.isoformat() if ip.assigned_at else None,
    }


def snapshot_bandwidth(alloc) -> dict:
    return {
        'id': alloc.id,
        'allocated_mbps': alloc.allocated_mbps,
        'target_type': alloc.target_type,
        'vps_id': alloc.vps_id,
        'edge_id': alloc.edge_id,
        'is_active': bool(alloc.is_active),
        'is_synced': bool(getattr(alloc, 'is_synced', False)),
        'pop_point_id': alloc.pop_point_id,
    }


def _target_label(*, vps=None, edge=None) -> str:
    if vps is not None:
        return f'VPS [{getattr(vps, "vps_no", "") or vps.pk}] {getattr(vps, "name", "")}'.strip()
    if edge is not None:
        return f'边缘 [{getattr(edge, "edge_no", "") or edge.pk}] {getattr(edge, "name", "")}'.strip()
    return '未指定目标'


def log_ip_assign(
    ip,
    *,
    old_value,
    operator=None,
    request=None,
    source='',
    node_payload=None,
    extra=None,
):
    ip.refresh_from_db()
    target = _target_label(vps=ip.assigned_vps, edge=ip.assigned_edge)
    route = ''
    if isinstance(node_payload, dict) and node_payload.get('route_id'):
        route = f'，节点路由 {node_payload.get("route_id")}'
    elif ip.node_route_ref:
        route = f'，节点路由 {ip.node_route_ref}'
    src = f'（{source}）' if source else ''
    new_value = snapshot_ip(ip)
    if extra:
        new_value = {**new_value, **extra}
    record_resource_log(
        operator=operator,
        action_type=ResourceOperationLog.ActionType.IP_ASSIGN,
        description=f'分配 IP {ip.address} 给 {target}{route}{src}'[:512],
        target_model=ResourceOperationLog.TargetModel.IP_ADDRESS,
        target_id=ip.pk,
        target_repr=str(ip.address),
        pop_point=ip.ip_block.pop_point if ip.ip_block_id else None,
        vps=ip.assigned_vps,
        edge=ip.assigned_edge,
        old_value=old_value,
        new_value=new_value,
        request=request,
    )


def log_ip_release(
    ip,
    *,
    old_value,
    operator=None,
    request=None,
    source='',
    node_payload=None,
    vps=None,
    edge=None,
):
    ip.refresh_from_db()
    prev_vps = vps or None
    prev_edge = edge or None
    if isinstance(old_value, dict):
        if prev_vps is None and old_value.get('assigned_vps_id'):
            prev_vps = getattr(ip, 'assigned_vps', None)
        if prev_edge is None and old_value.get('assigned_edge_id'):
            prev_edge = getattr(ip, 'assigned_edge', None)
    target = _target_label(vps=prev_vps, edge=prev_edge) if (prev_vps or prev_edge) else '资源池'
    removed = ''
    if isinstance(node_payload, dict) and node_payload.get('removed_route_ref'):
        removed = f'，已删除节点路由 {node_payload.get("removed_route_ref")}'
    src = f'（{source}）' if source else ''
    record_resource_log(
        operator=operator,
        action_type=ResourceOperationLog.ActionType.IP_RELEASE,
        description=f'释放 IP {ip.address}（原分配 {target}）{removed}{src}'[:512],
        target_model=ResourceOperationLog.TargetModel.IP_ADDRESS,
        target_id=ip.pk,
        target_repr=str(ip.address),
        pop_point=ip.ip_block.pop_point if ip.ip_block_id else None,
        vps=prev_vps,
        edge=prev_edge,
        old_value=old_value,
        new_value=snapshot_ip(ip),
        request=request,
    )


def log_bandwidth_change(
    alloc,
    *,
    action='alloc',
    old_value=None,
    operator=None,
    request=None,
    source='',
):
    target = _target_label(vps=alloc.vps, edge=alloc.edge)
    src = f'（{source}）' if source else ''
    if action == 'release':
        action_type = ResourceOperationLog.ActionType.DELETE
        desc = f'回收带宽 {alloc.allocated_mbps}Mbps（{target}）{src}'
    elif old_value and old_value.get('allocated_mbps') != alloc.allocated_mbps:
        action_type = ResourceOperationLog.ActionType.BW_ALLOC
        desc = (
            f'调整带宽 {old_value.get("allocated_mbps")}→{alloc.allocated_mbps}Mbps'
            f'（{target}）{src}'
        )
    else:
        action_type = ResourceOperationLog.ActionType.BW_ALLOC
        desc = f'分配带宽 {alloc.allocated_mbps}Mbps 给 {target}{src}'
    record_resource_log(
        operator=operator,
        action_type=action_type,
        description=desc[:512],
        target_model=ResourceOperationLog.TargetModel.BW_ALLOC,
        target_id=alloc.pk,
        target_repr=str(alloc),
        pop_point=alloc.pop_point,
        vps=alloc.vps,
        edge=alloc.edge,
        old_value=old_value,
        new_value=None if action == 'release' else snapshot_bandwidth(alloc),
        request=request,
    )
