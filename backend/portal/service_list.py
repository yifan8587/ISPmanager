"""portal/service_list.py — 客户门户「我的服务」列表聚合

除 ServiceInstance 外，同步纳入直接归属客户、尚未回填实例的 VPS / 边缘加速
（历史上可能未创建服务实例记录）。本模块只读，不写库。

历史资源批量回填请使用：
  python manage.py backfill_service_instances
"""
from __future__ import annotations

from django.db.models import Q

from resource_manager.models import EdgeAcceleration, VPS
from service_manager.instance_backfill import (
    ensure_service_instance_for_edge,
    ensure_service_instance_for_vps,
)
from service_manager.models import ServiceInstance

INSTANCE_STATUS_LABELS = {
    'provisioning': '开通中',
    'active': '运行中',
    'suspended': '已暂停',
    'terminated': '已终止',
    'expired': '已过期',
    'error': '故障',
}

VPS_STATUS_TO_PORTAL = {
    'running': 'active',
    'stopped': 'suspended',
    'suspended': 'suspended',
    'creating': 'provisioning',
    'deleting': 'terminated',
    'error': 'error',
}

EDGE_STATUS_TO_PORTAL = {
    'active': 'active',
    'stopped': 'suspended',
    'pending': 'provisioning',
    'error': 'error',
}

# 兼容旧导入：回填实现已迁至 service_manager.instance_backfill
__all__ = [
    'build_customer_service_items',
    'ensure_service_instance_for_edge',
    'ensure_service_instance_for_vps',
]


def _portal_status_label(status: str) -> str:
    return INSTANCE_STATUS_LABELS.get(status, status)


def _ip_delivery_fields(pop) -> dict:
    if not pop:
        return {'ip_delivery_mode': '', 'ip_delivery_mode_label': ''}
    return {
        'ip_delivery_mode': getattr(pop, 'ip_delivery_mode', '') or '',
        'ip_delivery_mode_label': pop.get_ip_delivery_mode_display() if hasattr(pop, 'get_ip_delivery_mode_display') else '',
    }


def _fee_for_resource(resource, kind: str) -> tuple[str, str]:
    try:
        from product_manager import services as product_services
        billing = product_services.compute_resource_billing(resource, kind)
        if billing:
            return str(billing.get('monthly_price', 0)), billing.get('currency', 'CNY')
    except Exception:
        pass
    product = getattr(resource, 'product', None)
    if product:
        return str(product.monthly_price), product.currency
    return '0', 'CNY'


def _item_from_instance(ins: ServiceInstance) -> dict:
    resource = ins.vps or ins.edge or ins.bandwidth_alloc
    pop_name = ''
    product_name = ''
    if resource:
        pop_name = getattr(getattr(resource, 'pop_point', None), 'name', '') or ''
        product_name = getattr(getattr(resource, 'product', None), 'name', '') or ''

    monthly = str(ins.monthly_fee)
    currency = ins.fee_currency
    try:
        monthly = str(ins.effective_monthly_fee())
        currency = ins.effective_fee_currency()
    except Exception:
        pass

    resource_payload = None
    if ins.vps_id and ins.vps:
        resource_payload = {
            'kind': 'vps', 'id': ins.vps.id,
            'no': ins.vps.vps_no, 'name': ins.vps.name,
        }
    elif ins.edge_id and ins.edge:
        resource_payload = {
            'kind': 'edge', 'id': ins.edge.id,
            'no': ins.edge.edge_no, 'name': ins.edge.name,
        }

    status = ins.status
    can_change = status != ServiceInstance.InstanceStatus.TERMINATED
    needs_config = False
    spec_snapshot = ins.spec_snapshot or {}
    if ins.edge_id and ins.edge:
        from resource_manager.edge_provision import needs_protocol_config
        from product_manager.services import spec_from_edge
        needs_config = needs_protocol_config(ins.edge)
        if needs_config:
            status_label = '待配置'
        else:
            status_label = ins.get_status_display()
        live = spec_from_edge(ins.edge)
        spec_snapshot = {
            'bandwidth_mbps': live.get('bandwidth_mbps') or spec_snapshot.get('bandwidth_mbps'),
            'ip_count': live.get('ip_count') or spec_snapshot.get('ip_count'),
            'protocol': ins.edge.protocol,
        }
    else:
        status_label = ins.get_status_display()
    return {
        'id': f'ins:{ins.id}',
        'entry_type': 'instance',
        'instance_id': ins.id,
        'instance_no': ins.instance_no,
        'service_type': ins.service_type,
        'service_type_label': ins.get_service_type_display(),
        'status': status,
        'status_label': status_label,
        'pop_name': pop_name,
        **_ip_delivery_fields(getattr(resource, 'pop_point', None) if resource else None),
        'product_name': product_name,
        'spec_snapshot': spec_snapshot,
        'monthly_fee_effective': monthly,
        'fee_currency': currency,
        'is_trial': ins.is_trial,
        'activated_at': ins.activated_at,
        'expire_time': ins.expire_time,
        'created_at': ins.created_at,
        'resource': resource_payload,
        'can_change_request': can_change,
        'needs_protocol_config': needs_config,
    }


def _item_from_vps(vps: VPS) -> dict:
    monthly, currency = _fee_for_resource(vps, 'vps')
    status = VPS_STATUS_TO_PORTAL.get(vps.status, 'active')
    return {
        'id': f'vps:{vps.id}',
        'entry_type': 'vps',
        'instance_id': None,
        'instance_no': vps.vps_no or f'VPS{vps.id}',
        'service_type': 'vps',
        'service_type_label': 'VPS',
        'status': status,
        'status_label': _portal_status_label(status),
        'pop_name': getattr(vps.pop_point, 'name', '') if vps.pop_point_id else '',
        **_ip_delivery_fields(vps.pop_point if vps.pop_point_id else None),
        'product_name': getattr(vps.product, 'name', '') if vps.product_id else '',
        'spec_snapshot': {
            'vcpu': vps.vcpu,
            'cpu_cores': vps.vcpu,
            'ram_mb': vps.ram_mb,
            'disk_gb': vps.disk_gb,
            'bandwidth_mbps': vps.bandwidth_mbps,
            'nic_count': vps.nic_count,
        },
        'monthly_fee_effective': monthly,
        'fee_currency': currency,
        'is_trial': False,
        'activated_at': vps.start_time,
        'expire_time': vps.expire_time,
        'created_at': vps.created_at,
        'resource': {
            'kind': 'vps', 'id': vps.id,
            'no': vps.vps_no, 'name': vps.name,
        },
        'can_change_request': False,
        'needs_protocol_config': False,
    }


def _item_from_edge(edge: EdgeAcceleration) -> dict:
    monthly, currency = _fee_for_resource(edge, 'edge')
    status = EDGE_STATUS_TO_PORTAL.get(edge.status, 'active')
    from resource_manager.edge_provision import needs_protocol_config
    from product_manager.services import spec_from_edge
    needs_config = needs_protocol_config(edge)
    status_label = '待配置' if needs_config else _portal_status_label(status)
    live = spec_from_edge(edge)
    return {
        'id': f'edge:{edge.id}',
        'entry_type': 'edge',
        'instance_id': None,
        'instance_no': edge.edge_no,
        'service_type': 'edge',
        'service_type_label': '边缘加速',
        'status': status,
        'status_label': status_label,
        'pop_name': getattr(edge.pop_point, 'name', '') if edge.pop_point_id else '',
        **_ip_delivery_fields(edge.pop_point if edge.pop_point_id else None),
        'product_name': getattr(edge.product, 'name', '') if edge.product_id else '',
        'spec_snapshot': {
            'bandwidth_mbps': live.get('bandwidth_mbps'),
            'ip_count': live.get('ip_count'),
            'protocol': edge.protocol,
        },
        'monthly_fee_effective': monthly,
        'fee_currency': currency,
        'is_trial': False,
        'activated_at': edge.start_time,
        'expire_time': edge.expire_time,
        'created_at': edge.created_at,
        'resource': {
            'kind': 'edge', 'id': edge.id,
            'no': edge.edge_no, 'name': edge.name,
        },
        'can_change_request': False,
        'needs_protocol_config': needs_config,
    }


def build_customer_service_items(customer, status_filter: str | None = None) -> list[dict]:
    """汇总客户可见的全部服务条目（只读，不创建工单或实例）。"""
    items: list[dict] = []

    inst_qs = ServiceInstance.objects.filter(
        Q(service_order__customer=customer)
        | Q(vps__customer=customer)
        | Q(edge__customer=customer)
    ).select_related(
        'service_order', 'vps__pop_point', 'vps__product',
        'edge__pop_point', 'edge__product',
    ).distinct()

    linked_vps_ids: set[int] = set()
    linked_edge_ids: set[int] = set()

    for ins in inst_qs:
        items.append(_item_from_instance(ins))
        if ins.vps_id:
            linked_vps_ids.add(ins.vps_id)
        if ins.edge_id:
            linked_edge_ids.add(ins.edge_id)

    for vps in VPS.objects.filter(customer=customer).exclude(
        pk__in=linked_vps_ids,
    ).select_related('pop_point', 'product'):
        items.append(_item_from_vps(vps))

    for edge in EdgeAcceleration.objects.filter(customer=customer).exclude(
        pk__in=linked_edge_ids,
    ).select_related('pop_point', 'product'):
        items.append(_item_from_edge(edge))

    if status_filter:
        items = [it for it in items if it['status'] == status_filter]

    items.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    return items
