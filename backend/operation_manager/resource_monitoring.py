"""资源监控上下文：已关联绑定、流量截取参数解析等。"""

from __future__ import annotations

from django.db.models import Q

from resource_manager.models import EdgeAcceleration, VPS

from .models import MonitoringGraph, MonitoringGraphBinding, MonitoringHost, infer_interface_name
from .traffic_analytics import infer_interface_from_graph, resolve_traffic_items, resolve_traffic_items_from_graph


def monitoring_hosts_for_pop(pop_point_id: int):
    """返回归属指定 POP 的监控主机（POP 主机 + 已绑定 service_pop 的通用主机）。"""
    if not pop_point_id:
        return MonitoringHost.objects.none()
    return MonitoringHost.objects.filter(
        Q(pop_point_id=pop_point_id) | Q(service_pop_point_id=pop_point_id),
        is_enabled=True,
    ).select_related('pop_point', 'service_pop_point', 'zabbix_server').order_by('-updated_at')


def build_host_traffic_graph_options(monitoring_host: MonitoringHost) -> list[dict]:
    """返回监控主机下可用于流量截取/95 计费的图形列表（含解析后的 item）。"""
    traffic_kinds = (
        MonitoringGraph.GraphKind.TRAFFIC,
        MonitoringGraph.GraphKind.BILLING,
    )
    graphs = MonitoringGraph.objects.filter(
        monitoring_host=monitoring_host,
        is_enabled=True,
    ).filter(
        Q(graph_kind__in=traffic_kinds) | Q(billing_enabled=True),
    ).order_by('graph_kind', 'name')

    options: list[dict] = []
    for g in graphs:
        iface = infer_interface_from_graph(g)
        in_item, out_item = resolve_traffic_items_from_graph(g, interface_name=iface)
        options.append({
            'graph_id': g.pk,
            'label': g.effective_title or g.name,
            'interface_name': iface or (in_item.interface_name if in_item else ''),
            'in_item_id': in_item.pk if in_item else None,
            'out_item_id': out_item.pk if out_item else None,
            'graph_kind': g.graph_kind,
            'ready': bool(in_item or out_item),
        })
    return options


def resolve_traffic_from_graph(graph) -> dict:
    """将监控图形解析为 query_traffic 参数。"""
    host = graph.monitoring_host
    iface = infer_interface_from_graph(graph)
    in_item, out_item = resolve_traffic_items_from_graph(graph, interface_name=iface)
    if not in_item and not out_item:
        raise ValueError('图形未关联可计费的入/出站监控项，请先同步监控主机')
    return {
        'monitoring_host': host,
        'graph': graph,
        'interface_name': iface or (in_item.interface_name if in_item else ''),
        'in_item': in_item,
        'out_item': out_item,
        'title': graph.effective_title or graph.name,
    }


def build_traffic_binding_options(target_type: str, resource_id: int) -> list[dict]:
    """返回资源已关联、可用于流量截取的监控图形/监控项列表。"""
    fk = {'pop': 'pop_point', 'vps': 'vps', 'edge': 'edge'}[target_type]
    qs = MonitoringGraphBinding.objects.filter(
        target_type=target_type, **{fk: resource_id}, is_visible=True,
    ).select_related(
        'graph', 'graph__monitoring_host',
        'monitoring_item', 'monitoring_item__monitoring_host',
        'monitoring_host',
    ).prefetch_related('graph__items').order_by('panel_slot', 'display_order', 'id')

    slot_labels = dict(MonitoringGraphBinding.PanelSlot.choices)
    options: list[dict] = []
    for b in qs:
        host = b.effective_host
        if not host:
            continue
        host_name = host.visible_name or host.hostname
        slot_label = slot_labels.get(b.panel_slot, b.panel_slot)
        if b.graph_id:
            g = b.graph
            iface = infer_interface_from_graph(g)
            title = b.panel_title or g.effective_title or g.name
            in_item, out_item = resolve_traffic_items_from_graph(g, interface_name=iface)
            options.append({
                'binding_id': b.pk,
                'bind_kind': 'graph',
                'label': title,
                'detail': f'{host_name}' + (f' · {iface}' if iface else ''),
                'monitoring_host_id': host.pk,
                'monitoring_host_name': host_name,
                'graph_id': g.pk,
                'interface_name': iface or (in_item.interface_name if in_item else ''),
                'in_item_id': in_item.pk if in_item else None,
                'out_item_id': out_item.pk if out_item else None,
                'panel_slot': b.panel_slot,
                'panel_slot_label': slot_label,
            })
        else:
            it = b.monitoring_item
            iface = (it.interface_name or '').strip() or infer_interface_name(it.item_key, it.name)
            title = b.panel_title or it.name
            dir_label = {'in': '入站', 'out': '出站'}.get(it.traffic_direction, '')
            in_id = it.pk if it.traffic_direction == 'in' else None
            out_id = it.pk if it.traffic_direction == 'out' else None
            if not in_id and not out_id:
                ri, ro = resolve_traffic_items(host, interface_name=iface, in_item_id=in_id, out_item_id=out_id)
                in_id = ri.pk if ri else None
                out_id = ro.pk if ro else None
            options.append({
                'binding_id': b.pk,
                'bind_kind': 'item',
                'label': title,
                'detail': f'{host_name}' + (f' · {iface}' if iface else '') + (f' · {dir_label}' if dir_label else ''),
                'monitoring_host_id': host.pk,
                'monitoring_host_name': host_name,
                'monitoring_item_id': it.pk,
                'interface_name': iface,
                'in_item_id': in_id,
                'out_item_id': out_id,
                'traffic_direction': it.traffic_direction,
                'panel_slot': b.panel_slot,
                'panel_slot_label': slot_label,
            })
    return options


def resolve_traffic_from_binding(binding: MonitoringGraphBinding) -> dict:
    """将图形/监控项绑定解析为 query_traffic 所需参数（含 Zabbix item）。"""
    host = binding.effective_host
    if not host:
        raise ValueError('该关联缺少监控主机')

    if binding.graph_id:
        g = binding.graph
        iface = infer_interface_from_graph(g)
        in_item, out_item = resolve_traffic_items_from_graph(g, interface_name=iface)
        if not in_item and not out_item:
            raise ValueError('图形未关联可计费的入/出站监控项，请先在监控主机上执行同步')
        resolved_iface = iface or infer_interface_name(
            in_item.item_key if in_item else '',
            in_item.name if in_item else '',
        )
        return {
            'monitoring_host': host,
            'graph': g,
            'interface_name': resolved_iface,
            'in_item': in_item,
            'out_item': out_item,
            'title': binding.panel_title or g.effective_title or g.name,
        }

    it = binding.monitoring_item
    iface = (it.interface_name or '').strip() or infer_interface_name(it.item_key, it.name)
    in_item = out_item = None
    if it.traffic_direction == 'in':
        in_item = it
    elif it.traffic_direction == 'out':
        out_item = it
    in_item, out_item = resolve_traffic_items(
        host,
        interface_name=iface,
        in_item_id=in_item.pk if in_item else None,
        out_item_id=out_item.pk if out_item else None,
    )
    if not in_item and not out_item:
        raise ValueError('未找到匹配的入/出站监控项，请确认监控项已同步')
    return {
        'monitoring_host': host,
        'graph': None,
        'interface_name': iface or (in_item.interface_name if in_item else ''),
        'in_item': in_item,
        'out_item': out_item,
        'title': binding.panel_title or it.name,
    }


def validate_binding_for_resource(binding: MonitoringGraphBinding, *, target_type: str, resource_id: int) -> None:
    fk = {'pop': 'pop_point_id', 'vps': 'vps_id', 'edge': 'edge_id'}[target_type]
    if getattr(binding, fk) != int(resource_id):
        raise ValueError('所选监控关联不属于当前资源')
    if binding.target_type != target_type:
        raise ValueError('所选监控关联与资源类型不匹配')


def get_traffic_resource_context(*, target_type: str, resource_id: int) -> dict:
    pop_point = None
    resource_name = ''

    if target_type == 'vps':
        vps = VPS.objects.select_related('pop_point').get(pk=resource_id)
        pop_point = vps.pop_point
        resource_name = f'[{vps.vps_no}] {vps.name}'
    elif target_type == 'edge':
        edge = EdgeAcceleration.objects.select_related('pop_point').get(pk=resource_id)
        pop_point = edge.pop_point
        resource_name = f'[{edge.edge_no}] {edge.name}'
    else:
        raise ValueError('target_type 须为 vps 或 edge')

    bindings = build_traffic_binding_options(target_type, resource_id)
    default_binding_id = bindings[0]['binding_id'] if bindings else None

    return {
        'target_type': target_type,
        'resource_id': int(resource_id),
        'resource_name': resource_name,
        'pop_point_id': pop_point.pk if pop_point else None,
        'pop_point_label': f'[{pop_point.code}] {pop_point.name}' if pop_point else '',
        'bindings': bindings,
        'default_binding_id': default_binding_id,
    }


def validate_monitoring_host_for_resource(*, monitoring_host: MonitoringHost, pop_point_id: int | None) -> None:
    """VPS/Edge 绑定或流量查询时校验监控主机归属 POP。"""
    if not pop_point_id:
        return
    if monitoring_host.host_kind == MonitoringHost.HostKind.POP:
        if monitoring_host.pop_point_id != pop_point_id:
            raise ValueError('所选监控主机不属于该 POP 点，请重新选择')
        return
    scoped = monitoring_host.service_pop_point_id
    if not scoped:
        raise ValueError('该通用监控主机未设置「服务 POP」，请先在监控主机中配置')
    if scoped != pop_point_id:
        raise ValueError('所选监控主机不属于该 POP 点，请重新选择')
