"""产品 POP 与工单履约默认值：下单、支付、人工开通共用。"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import ServiceOrder

SPEC_MAP = (
    ('requested_vcpu', 'cpu_cores'),
    ('requested_ram_mb', 'ram_mb'),
    ('requested_disk_gb', 'disk_gb'),
    ('requested_bandwidth_mbps', 'bandwidth_mbps'),
    ('requested_ip_count', 'ip_count'),
)

STAFF_DELETABLE = (
    ServiceOrder.OrderStatus.DRAFT,
    ServiceOrder.OrderStatus.REJECTED,
)

SUPERUSER_BLOCKED = (
    ServiceOrder.OrderStatus.ACTIVE,
    ServiceOrder.OrderStatus.SUSPENDED,
    ServiceOrder.OrderStatus.SUSPEND_PENDING,
    ServiceOrder.OrderStatus.TERMINATE_PENDING,
)


def product_pop(product):
    return getattr(product, 'pop_point', None) if product is not None else None


def apply_product_defaults_to_validated(validated_data, *, instance=None, fill_spec=True):
    """写入阶段：有产品则补齐 preferred_pop；新建时补齐空规格。"""
    product = validated_data.get('product') or getattr(instance, 'product', None)
    pop = product_pop(product)
    if pop is None:
        return validated_data

    status = getattr(instance, 'status', None)
    locked = status in (
        ServiceOrder.OrderStatus.PROVISIONING,
        ServiceOrder.OrderStatus.ACTIVE,
        ServiceOrder.OrderStatus.SUSPENDED,
        ServiceOrder.OrderStatus.TERMINATED,
    )
    if not locked:
        incoming = validated_data.get('preferred_pop') if 'preferred_pop' in validated_data else None
        current_id = getattr(instance, 'preferred_pop_id', None) if instance else None
        if incoming is None and not current_id:
            validated_data['preferred_pop'] = pop

    if fill_spec and instance is None:
        for dest, src in SPEC_MAP:
            if validated_data.get(dest) in (None, ''):
                value = getattr(product, src, None)
                if value not in (None, ''):
                    validated_data[dest] = value
    return validated_data


def apply_product_fulfillment(order: ServiceOrder, *, save: bool = True) -> bool:
    """履约 / 人工开通前：把产品 POP 和空规格同步到工单。"""
    product = getattr(order, 'product', None)
    pop = product_pop(product)
    changed: list[str] = []
    if pop is not None and not order.preferred_pop_id:
        order.preferred_pop = pop
        changed.append('preferred_pop')
    if product is not None:
        for dest, src in SPEC_MAP:
            current = getattr(order, dest, None)
            value = getattr(product, src, None)
            if current in (None, 0, '') and value not in (None, 0, ''):
                setattr(order, dest, value)
                changed.append(dest)
    if save and changed:
        order.save(update_fields=[*changed, 'updated_at'])
    return bool(changed)


def can_quick_provision(order: ServiceOrder) -> bool:
    if order.order_type != ServiceOrder.OrderType.NEW:
        return False
    if order.business_type not in (
        ServiceOrder.BusinessType.VPS,
        ServiceOrder.BusinessType.EDGE_GRE,
        ServiceOrder.BusinessType.EDGE_WG,
        ServiceOrder.BusinessType.EDGE_SOCKS5,
    ):
        return False
    product = getattr(order, 'product', None)
    return bool(product and getattr(product, 'pop_point_id', None))


def bootstrap_order_resource(order: ServiceOrder, operator=None) -> dict:
    """按产品 POP 创建待交付的基础 VPS / 边缘记录。"""
    from resource_manager.models import EdgeAcceleration, Socks5Config, VPS

    apply_product_fulfillment(order, save=True)
    pop = order.preferred_pop
    if pop is None:
        raise ValidationError('产品未关联 POP，无法按节点快速建档')
    if not can_quick_provision(order):
        raise ValidationError('当前工单类型不支持按产品节点快速建档，请选择已有资源交付')

    name = f'{order.customer.name}-{order.order_no}'
    now = timezone.now()
    if order.business_type == ServiceOrder.BusinessType.VPS:
        vps = VPS.objects.create(
            name=name,
            pop_point=pop,
            vcpu=int(order.requested_vcpu or 1),
            ram_mb=int(order.requested_ram_mb or 1024),
            disk_gb=int(order.requested_disk_gb or 20),
            bandwidth_mbps=int(order.requested_bandwidth_mbps or 100) or 100,
            customer=order.customer,
            contract=order.contract,
            product=order.product,
            status=VPS.VPSStatus.CREATING,
            remark=f'工单 {order.order_no} 按产品节点快速建档',
            created_by=operator,
            start_time=now,
        )
        return {'vps': vps, 'edge': None}

    proto_map = {
        ServiceOrder.BusinessType.EDGE_GRE: EdgeAcceleration.Protocol.GRE,
        ServiceOrder.BusinessType.EDGE_WG: EdgeAcceleration.Protocol.WIREGUARD,
        ServiceOrder.BusinessType.EDGE_SOCKS5: EdgeAcceleration.Protocol.SOCKS5,
    }
    edge = EdgeAcceleration.objects.create(
        name=name,
        protocol=proto_map[order.business_type],
        status=EdgeAcceleration.EdgeStatus.PENDING,
        pop_point=pop,
        bandwidth_mbps=int(order.requested_bandwidth_mbps or 0) or 100,
        customer=order.customer,
        contract=order.contract,
        product=order.product,
        start_time=now,
        created_by=operator,
        remark=f'工单 {order.order_no} 按产品节点快速建档',
    )
    if edge.protocol == EdgeAcceleration.Protocol.SOCKS5:
        Socks5Config.objects.update_or_create(
            edge=edge,
            defaults={
                'listen_ip': '0.0.0.0',
                'listen_port': int(order.edge_socks5_port or 1080),
                'auth_required': bool(order.edge_socks5_user or order.edge_socks5_pass),
                'username': order.edge_socks5_user or '',
                'password': order.edge_socks5_pass or '',
                'udp_enabled': True,
            },
        )
    return {'vps': None, 'edge': edge}


def assert_order_deletable(order: ServiceOrder, user) -> None:
    """工单删除业务规则。超级管理员可删未交付工单；已开通实例仍拒绝。"""
    if order.instances.exists():
        raise ValidationError('该工单已关联服务实例，请先终止并删除实例后再删除工单')
    is_super = bool(user and getattr(user, 'is_superuser', False))
    if is_super:
        if order.status in SUPERUSER_BLOCKED:
            raise ValidationError(
                f'当前状态「{order.get_status_display()}」的工单不能删除，请先终止服务'
            )
        return
    if order.status not in STAFF_DELETABLE:
        raise ValidationError('只能删除草稿或已驳回的工单')


def detach_order_relations(order: ServiceOrder) -> None:
    """先解除库内 NO ACTION 外键，避免删工单时被账单/日志/预占拦住。"""
    from customer_manager.models import CustomerBilling

    from .models import (
        PopCapacityReservation, ServiceChangeRecord, ServiceInquiry,
        ServiceLifecycleRecord, ServiceManualDelivery, ServiceOperationLog,
    )

    CustomerBilling.objects.filter(service_order=order).update(service_order=None)
    ServiceInquiry.objects.filter(related_order=order).update(related_order=None)
    ServiceChangeRecord.objects.filter(related_order=order).update(related_order=None)
    ServiceOperationLog.objects.filter(service_order=order).update(service_order=None)
    ServiceLifecycleRecord.objects.filter(service_order=order).delete()
    ServiceManualDelivery.objects.filter(service_order=order).delete()
    PopCapacityReservation.objects.filter(service_order=order).delete()


def delete_service_order(order: ServiceOrder, *, operator=None, request=None) -> str:
    """校验后原子删除工单。通知放到事务提交后，避免先发通知再写日志。"""
    from django.db import transaction
    from django.db.models.deletion import ProtectedError

    from .capacity_reservation import release_reservation
    from .models import ServiceOperationLog, _log_service
    from .notify_helpers import notify_order_event

    assert_order_deletable(order, operator)
    order_id = order.pk
    order_no = order.order_no
    snapshot = {
        'id': order_id,
        'order_no': order_no,
        'status': order.status,
        'customer_id': order.customer_id,
        'product_id': order.product_id,
        'preferred_pop_id': order.preferred_pop_id,
    }
    with transaction.atomic():
        locked = ServiceOrder.objects.select_for_update().filter(pk=order_id).first()
        if locked is None:
            return order_no
        assert_order_deletable(locked, operator)
        release_reservation(locked, reason='删除工单，释放预占')
        detach_order_relations(locked)
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.OTHER,
            description=f'删除工单 [{order_no}]',
            service_order=None,
            old_value=snapshot,
            request=request,
        )
        notify_order_event(locked, '删除工单')
        try:
            locked.delete()
        except ProtectedError as exc:
            raise ValidationError('该工单仍被其他业务数据引用，无法删除') from exc
    return order_no
