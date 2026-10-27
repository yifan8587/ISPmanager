"""WebSSH 目标解析：从 POP / VPS / 监控主机解析默认 SSH 连接信息。"""

from __future__ import annotations

from typing import Any


def resolve_ssh_target(target_type: str, target_id: int) -> dict[str, Any] | None:
    """返回 label、host、port、username、password（可能为空，由前端补全）。"""
    target_type = (target_type or '').strip().lower()
    try:
        tid = int(target_id)
    except (TypeError, ValueError):
        return None

    if target_type == 'pop':
        return _resolve_pop(tid)
    if target_type == 'vps':
        return _resolve_vps(tid)
    if target_type == 'monitoring_host':
        return _resolve_monitoring_host(tid)
    return None


def _resolve_pop(pop_id: int) -> dict[str, Any] | None:
    from resource_manager.models import PopPoint

    try:
        pop = PopPoint.objects.get(pk=pop_id)
    except PopPoint.DoesNotExist:
        return None
    return {
        'target_type': 'pop',
        'target_id': pop.id,
        'label': f'[{pop.code}] {pop.name}',
        'host': pop.mgmt_ip or '',
        'port': pop.ssh_port or 22,
        'username': pop.mgmt_username or 'root',
        'password': pop.mgmt_password or '',
        'has_password': bool(pop.mgmt_password),
    }


def _resolve_vps(vps_id: int) -> dict[str, Any] | None:
    from resource_manager.models import VPS

    try:
        vps = VPS.objects.select_related('pop_point').get(pk=vps_id)
    except VPS.DoesNotExist:
        return None
    label = vps.name
    if vps.vps_no:
        label = f'[{vps.vps_no}] {vps.name}'
    return {
        'target_type': 'vps',
        'target_id': vps.id,
        'label': label,
        'host': vps.mgmt_ip or '',
        'port': vps.mgmt_port or 22,
        'username': vps.mgmt_username or 'root',
        'password': vps.mgmt_password or '',
        'has_password': bool(vps.mgmt_password),
        'pop_point_id': vps.pop_point_id,
        'pop_point_label': str(vps.pop_point) if vps.pop_point_id else '',
    }


def _resolve_monitoring_host(host_id: int) -> dict[str, Any] | None:
    from .models import MonitoringHost

    try:
        mh = MonitoringHost.objects.select_related('pop_point', 'pop_point__city').get(pk=host_id)
    except MonitoringHost.DoesNotExist:
        return None

    label = mh.visible_name or mh.hostname or f'MonitoringHost#{mh.pk}'
    host = mh.monitored_ip or ''
    port = 22
    username = 'root'
    password = ''
    has_password = False
    linked_pop_id = None

    if mh.host_kind == MonitoringHost.HostKind.POP and mh.pop_point_id:
        pop = mh.pop_point
        linked_pop_id = pop.id
        host = pop.mgmt_ip or mh.monitored_ip or ''
        port = pop.ssh_port or 22
        username = pop.mgmt_username or 'root'
        password = pop.mgmt_password or ''
        has_password = bool(pop.mgmt_password)
        label = f'{label} · [{pop.code}] {pop.name}'

    return {
        'target_type': 'monitoring_host',
        'target_id': mh.id,
        'label': label,
        'host': host or '',
        'port': port,
        'username': username,
        'password': password,
        'has_password': has_password,
        'host_kind': mh.host_kind,
        'monitored_ip': mh.monitored_ip,
        'linked_pop_id': linked_pop_id,
    }
