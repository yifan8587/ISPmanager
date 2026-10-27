"""将历史直连 VPS / 边缘加速回填为 ServiceInstance。

仅应由管理命令或运维脚本调用，不要在门户 GET 列表里写库。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction

from resource_manager.models import EdgeAcceleration, VPS
from service_manager.models import ServiceInstance, ServiceOrder

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

EDGE_PROTOCOL_TO_BUSINESS = {
    'gre': ServiceOrder.BusinessType.EDGE_GRE,
    'wireguard': ServiceOrder.BusinessType.EDGE_WG,
    'wg': ServiceOrder.BusinessType.EDGE_WG,
    'socks5': ServiceOrder.BusinessType.EDGE_SOCKS5,
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


def _as_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _instance_status_from_portal(status: str) -> str:
    valid = {c.value for c in ServiceInstance.InstanceStatus}
    return status if status in valid else ServiceInstance.InstanceStatus.ACTIVE


def ensure_service_instance_for_vps(vps: VPS) -> ServiceInstance | None:
    """历史直连 VPS 回填服务实例，供门户变更使用。"""
    if not vps or not vps.customer_id:
        return None
    existing = ServiceInstance.objects.filter(vps_id=vps.id).first()
    if existing:
        return existing

    monthly, currency = _fee_for_resource(vps, 'vps')
    portal_status = VPS_STATUS_TO_PORTAL.get(vps.status, 'active')
    with transaction.atomic():
        existing = ServiceInstance.objects.filter(vps_id=vps.id).first()
        if existing:
            return existing
        order = ServiceOrder.objects.create(
            customer_id=vps.customer_id,
            product_id=vps.product_id,
            preferred_pop_id=vps.pop_point_id,
            business_type=ServiceOrder.BusinessType.VPS,
            order_type=ServiceOrder.OrderType.NEW,
            status=ServiceOrder.OrderStatus.ACTIVE,
            current_stage=ServiceOrder.LifecycleStage.USAGE,
            source=ServiceOrder.OrderSource.SYSTEM,
            requirements=f'历史资源回填 · {vps.vps_no or vps.id}',
            requested_vcpu=vps.vcpu,
            requested_ram_mb=vps.ram_mb,
            requested_disk_gb=vps.disk_gb,
            requested_bandwidth_mbps=vps.bandwidth_mbps,
            quoted_price=_as_decimal(monthly),
            remark='系统自动：为门户变更关联历史 VPS',
        )
        return ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.VPS,
            status=_instance_status_from_portal(portal_status),
            vps=vps,
            spec_snapshot={
                'vcpu': vps.vcpu,
                'cpu_cores': vps.vcpu,
                'ram_mb': vps.ram_mb,
                'disk_gb': vps.disk_gb,
                'bandwidth_mbps': vps.bandwidth_mbps,
                'nic_count': vps.nic_count,
            },
            monthly_fee=_as_decimal(monthly),
            fee_currency=currency or 'CNY',
            activated_at=vps.start_time,
            expire_time=vps.expire_time,
            remark='历史资源回填：直连 VPS',
        )


def ensure_service_instance_for_edge(edge: EdgeAcceleration) -> ServiceInstance | None:
    """历史直连边缘加速回填服务实例，供门户变更使用。"""
    if not edge or not edge.customer_id:
        return None
    existing = ServiceInstance.objects.filter(edge_id=edge.id).first()
    if existing:
        return existing

    monthly, currency = _fee_for_resource(edge, 'edge')
    portal_status = EDGE_STATUS_TO_PORTAL.get(edge.status, 'active')
    protocol = (edge.protocol or '').lower()
    business_type = EDGE_PROTOCOL_TO_BUSINESS.get(
        protocol, ServiceOrder.BusinessType.EDGE_GRE,
    )
    with transaction.atomic():
        existing = ServiceInstance.objects.filter(edge_id=edge.id).first()
        if existing:
            return existing
        order = ServiceOrder.objects.create(
            customer_id=edge.customer_id,
            product_id=edge.product_id,
            preferred_pop_id=edge.pop_point_id,
            business_type=business_type,
            order_type=ServiceOrder.OrderType.NEW,
            status=ServiceOrder.OrderStatus.ACTIVE,
            current_stage=ServiceOrder.LifecycleStage.USAGE,
            source=ServiceOrder.OrderSource.SYSTEM,
            requirements=f'历史资源回填 · {edge.edge_no or edge.id}',
            requested_bandwidth_mbps=edge.bandwidth_mbps,
            quoted_price=_as_decimal(monthly),
            remark='系统自动：为门户变更关联历史边缘加速',
        )
        return ServiceInstance.objects.create(
            service_order=order,
            service_type=ServiceInstance.ServiceType.EDGE_ACCELERATION,
            status=_instance_status_from_portal(portal_status),
            edge=edge,
            spec_snapshot={
                'bandwidth_mbps': edge.bandwidth_mbps,
                'protocol': edge.protocol,
            },
            monthly_fee=_as_decimal(monthly),
            fee_currency=currency or 'CNY',
            activated_at=edge.start_time,
            expire_time=edge.expire_time,
            remark='历史资源回填：直连边缘加速',
        )


def iter_orphan_vps(customer_id: int | None = None):
    linked = ServiceInstance.objects.filter(vps_id__isnull=False).values_list('vps_id', flat=True)
    qs = VPS.objects.filter(customer_id__isnull=False).exclude(pk__in=linked)
    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    return qs.select_related('pop_point', 'product', 'customer')


def iter_orphan_edges(customer_id: int | None = None):
    linked = ServiceInstance.objects.filter(edge_id__isnull=False).values_list('edge_id', flat=True)
    qs = EdgeAcceleration.objects.filter(customer_id__isnull=False).exclude(pk__in=linked)
    if customer_id:
        qs = qs.filter(customer_id=customer_id)
    return qs.select_related('pop_point', 'product', 'customer')


def backfill_orphan_service_instances(
    *,
    customer_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    """回填未关联服务实例的客户 VPS / Edge。"""
    stats = {
        'vps_found': 0,
        'edge_found': 0,
        'vps_created': 0,
        'edge_created': 0,
        'errors': [],
    }
    vps_list = list(iter_orphan_vps(customer_id))
    edge_list = list(iter_orphan_edges(customer_id))
    stats['vps_found'] = len(vps_list)
    stats['edge_found'] = len(edge_list)
    if dry_run:
        return stats

    for vps in vps_list:
        try:
            if ensure_service_instance_for_vps(vps):
                stats['vps_created'] += 1
        except Exception as exc:  # noqa: BLE001
            stats['errors'].append(f'VPS {vps.pk}: {exc}')

    for edge in edge_list:
        try:
            if ensure_service_instance_for_edge(edge):
                stats['edge_created'] += 1
        except Exception as exc:  # noqa: BLE001
            stats['errors'].append(f'Edge {edge.pk}: {exc}')

    return stats
