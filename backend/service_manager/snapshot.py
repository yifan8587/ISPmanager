"""服务单资源快照：聚合服务、实例、资源池与计费上下文。"""
from __future__ import annotations

from .lifecycle import fee_snapshot_for
from .models import service_snapshot


def _pop_data(pop):
    if pop is None:
        return None
    return {
        'id': pop.id,
        'code': pop.code,
        'name': pop.name,
        'network_type': pop.network_type,
        'config_mode': pop.config_mode,
        'available_bandwidth_mbps': getattr(pop, 'available_bandwidth_mbps', None),
    }


def build_order_snapshot(order) -> dict:
    """返回可直接用于服务详情 Dashboard 的当前资源快照。"""
    instances = []
    for instance in order.instances.select_related(
        'vps__pop_point', 'edge__pop_point', 'bandwidth_alloc__pop_point',
    ).prefetch_related('ip_addresses'):
        data = service_snapshot(instance)
        resource = instance.vps or instance.edge or instance.bandwidth_alloc
        pop = getattr(resource, 'pop_point', None) if resource else order.preferred_pop
        data.update({
            'instance_id': instance.id,
            'instance_no': instance.instance_no,
            'is_trial': instance.is_trial,
            'expire_time': instance.expire_time.isoformat() if instance.expire_time else None,
            'product': {
                'id': order.product_id,
                'name': order.product.name if order.product_id else None,
            },
            'pop': _pop_data(pop),
            'fee': fee_snapshot_for(instance=instance, order=order),
        })
        if instance.vps_id:
            data['vps'].update({
                'mgmt_ip': instance.vps.mgmt_ip,
                'source_ref': instance.vps.source_ref,
            })
        instances.append(data)

    return {
        'order': {
            'id': order.id,
            'order_no': order.order_no,
            'status': order.status,
            'stage': order.current_stage,
            'business_type': order.business_type,
            'customer': {'id': order.customer_id, 'name': order.customer.name},
            'product': {
                'id': order.product_id,
                'name': order.product.name if order.product_id else None,
            },
            'requested_spec': {
                'vcpu': order.requested_vcpu,
                'ram_mb': order.requested_ram_mb,
                'disk_gb': order.requested_disk_gb,
                'bandwidth_mbps': order.requested_bandwidth_mbps,
                'ip_count': order.requested_ip_count,
                'os': order.requested_os,
            },
            'preferred_pop': _pop_data(order.preferred_pop),
        },
        'instances': instances,
        'captured_at': __import__('django.utils.timezone', fromlist=['now']).now().isoformat(),
    }
