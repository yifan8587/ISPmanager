"""人工交付清单与状态回传（D1 骨架）。

Socks5 / 多实例 / 独立带宽 / 手动 POP 不走自动下发时生成结构化清单，
工单标为「已付款待人工」，避免停在「未触发」。节点或运维用令牌回传进度；
Socks5 回传可建档并完成交付。本步不做告警、限速、自动重试分类。
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from .models import (
    ServiceAllocation, ServiceInstance, ServiceManualDelivery,
    ServiceOperationLog, ServiceOrder, _log_service,
    sync_service_order_delivery_state,
)


CHECKLIST_TEMPLATES: dict[str, list[dict]] = {
    ServiceManualDelivery.Reason.SOCKS5: [
        {'key': 'create_edge', 'label': '创建 Socks5 边缘资源'},
        {'key': 'configure_auth', 'label': '配置监听端口与认证'},
        {'key': 'push_node', 'label': '节点侧下发并确认'},
        {'key': 'bind_instance', 'label': '绑定服务实例并激活'},
    ],
    ServiceManualDelivery.Reason.MULTI_INSTANCE: [
        {'key': 'pick_resources', 'label': '按数量逐项选择并核验资源'},
        {'key': 'deliver_all', 'label': '完成后台「资源交付」并激活'},
    ],
    ServiceManualDelivery.Reason.BANDWIDTH: [
        {'key': 'bind_target', 'label': '建立带宽分配并绑定 VPS/边缘'},
        {'key': 'deliver', 'label': '通过「资源交付」激活工单'},
    ],
    ServiceManualDelivery.Reason.POP_MANUAL: [
        {'key': 'prepare_node', 'label': '在手动节点上准备对应资源'},
        {'key': 'deliver', 'label': '通过「资源交付」绑定并激活'},
    ],
    ServiceManualDelivery.Reason.STOCK: [
        {'key': 'add_capacity', 'label': '在资源管理补齐 CPU、内存、磁盘、IP 或带宽'},
        {'key': 'deliver', 'label': '通过「资源交付」手动开通并交给客户'},
    ],
}

class ManualDeliveryError(ValueError):
    """回传或完成交付时的业务错误。"""


def hash_token(token: str) -> str:
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def generate_token() -> str:
    return f'mdt_{secrets.token_urlsafe(32)}'


def _blank_items(reason: str) -> list[dict]:
    rows = []
    for item in CHECKLIST_TEMPLATES.get(reason) or []:
        rows.append({
            'key': item['key'],
            'label': item['label'],
            'required': True,
            'done': False,
            'done_at': None,
            'source': '',
        })
    return rows


def _merge_items(existing: list, reason: str) -> list[dict]:
    by_key = {str(item.get('key')): dict(item) for item in existing or [] if item.get('key')}
    merged = []
    for tmpl in _blank_items(reason):
        old = by_key.get(tmpl['key'])
        merged.append(old or tmpl)
    return merged


def delivery_for(order) -> ServiceManualDelivery | None:
    try:
        return order.manual_delivery
    except ObjectDoesNotExist:
        return None


def item_progress(items) -> tuple[int, int]:
    rows = list(items or [])
    done = sum(1 for item in rows if item.get('done'))
    return done, len(rows)


def public_summary(order, *, include_items=False) -> dict | None:
    delivery = delivery_for(order)
    if delivery is None:
        return None
    done, total = item_progress(delivery.items)
    payload = {
        'exists': True,
        'id': delivery.pk,
        'reason': delivery.reason,
        'reason_label': delivery.get_reason_display(),
        'status': delivery.status,
        'status_label': delivery.get_status_display(),
        'done_count': done,
        'total_count': total,
        'open': delivery.status in (
            ServiceManualDelivery.Status.OPEN,
            ServiceManualDelivery.Status.IN_PROGRESS,
        ),
        'token_prefix': delivery.token_prefix,
        'last_callback_at': (
            delivery.last_callback_at.isoformat() if delivery.last_callback_at else None
        ),
        'last_callback_note': delivery.last_callback_note,
        'resource_edge_id': delivery.resource_edge_id,
        'completed_at': (
            delivery.completed_at.isoformat() if delivery.completed_at else None
        ),
    }
    if include_items:
        payload['items'] = list(delivery.items or [])
    return payload


def portal_summary(order) -> dict | None:
    summary = public_summary(order, include_items=True)
    if summary is None:
        return None
    summary.pop('token_prefix', None)
    summary.pop('last_callback_note', None)
    summary.pop('resource_edge_id', None)
    return summary


def order_ready_for_delivery(order: ServiceOrder) -> bool:
    try:
        billing = order.payment_billing
    except ObjectDoesNotExist:
        billing = None
    if billing is None:
        return not (order.quoted_price and order.quoted_price > 0)
    return billing.status == billing.BillingStatus.PAID


def _apply_token(delivery: ServiceManualDelivery, token: str) -> None:
    delivery.token_hash = hash_token(token)
    delivery.token_prefix = token[:16]
    delivery.token_rotated_at = timezone.now()


def rotate_token(delivery: ServiceManualDelivery) -> str:
    token = generate_token()
    _apply_token(delivery, token)
    delivery.save(update_fields=['token_hash', 'token_prefix', 'token_rotated_at', 'updated_at'])
    return token


def get_delivery_by_token(raw_token: str) -> ServiceManualDelivery | None:
    token = (raw_token or '').strip()
    if not token:
        return None
    return (
        ServiceManualDelivery.objects.select_related(
            'service_order', 'service_order__customer',
            'service_order__preferred_pop', 'service_order__product',
            'resource_edge',
        )
        .filter(token_hash=hash_token(token))
        .exclude(status=ServiceManualDelivery.Status.CANCELLED)
        .first()
    )


@transaction.atomic
def ensure_manual_delivery(order: ServiceOrder, reason: str, *, operator=None):
    """幂等创建或复用清单。已完成且已有实例时不再重开。"""
    if reason not in CHECKLIST_TEMPLATES:
        raise ManualDeliveryError(f'未知转人工原因：{reason}')
    locked = ServiceOrder.objects.select_for_update().get(pk=order.pk)
    delivery = delivery_for(locked)
    if delivery is not None:
        if (
            delivery.status == ServiceManualDelivery.Status.COMPLETED
            and locked.instances.exists()
        ):
            return delivery
        if delivery.reason != reason:
            delivery.reason = reason
            delivery.items = _merge_items(delivery.items, reason)
        if delivery.status == ServiceManualDelivery.Status.CANCELLED:
            delivery.status = ServiceManualDelivery.Status.OPEN
        delivery.save(update_fields=['reason', 'items', 'status', 'updated_at'])
        return delivery

    token = generate_token()
    delivery = ServiceManualDelivery(
        service_order=locked,
        reason=reason,
        status=ServiceManualDelivery.Status.OPEN,
        items=_blank_items(reason),
    )
    _apply_token(delivery, token)
    delivery.save()
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.OTHER,
        description=f'工单 [{locked.order_no}] 生成人工交付清单（{delivery.get_reason_display()}）',
        service_order=locked,
        new_value={'reason': reason, 'token_prefix': delivery.token_prefix},
    )
    return delivery


def park_order_for_manual_delivery(
    order: ServiceOrder,
    reason: str,
    message: str,
    *,
    operator=None,
    subject: str | None = None,
    provision_status: str | None = None,
    counted_as_auto: bool = False,
) -> dict:
    """把工单标为待人工，并确保清单存在。"""
    from .provisioning import notify_managers

    order.auto_provision_status = provision_status or ServiceOrder.AutoProvisionStatus.PAID_PENDING
    order.auto_provision_message = message
    order.save(update_fields=['auto_provision_status', 'auto_provision_message', 'updated_at'])
    ensure_manual_delivery(order, reason, operator=operator)
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.OTHER,
        description=f'工单 [{order.order_no}] 等待人工交付：{message}',
        service_order=order,
    )
    notify_managers(order, subject or '待人工交付', message)
    reason_codes = {
        ServiceManualDelivery.Reason.SOCKS5: 'socks5_manual_delivery',
        ServiceManualDelivery.Reason.MULTI_INSTANCE: 'multi_instance_manual_delivery',
        ServiceManualDelivery.Reason.BANDWIDTH: 'bandwidth_manual_delivery',
        ServiceManualDelivery.Reason.POP_MANUAL: 'pop_not_auto',
        ServiceManualDelivery.Reason.STOCK: 'stock_manual',
    }
    return {'auto': counted_as_auto, 'ok': False, 'reason': reason_codes.get(reason, reason)}


def _mark_keys(delivery: ServiceManualDelivery, keys, *, source: str) -> None:
    wanted = {str(key) for key in (keys or []) if key}
    if not wanted:
        return
    now = timezone.now().isoformat()
    items = []
    changed = False
    for item in list(delivery.items or []):
        row = dict(item)
        if row.get('key') in wanted and not row.get('done'):
            row['done'] = True
            row['done_at'] = now
            row['source'] = source
            changed = True
        items.append(row)
    if not changed:
        return
    delivery.items = items
    if delivery.status == ServiceManualDelivery.Status.OPEN:
        delivery.status = ServiceManualDelivery.Status.IN_PROGRESS
    delivery.save(update_fields=['items', 'status', 'updated_at'])


def mark_completed(delivery: ServiceManualDelivery, *, source: str, note: str = '') -> None:
    now = timezone.now()
    items = []
    for item in list(delivery.items or []):
        row = dict(item)
        if not row.get('done'):
            row['done'] = True
            row['done_at'] = now.isoformat()
            row['source'] = source
        items.append(row)
    delivery.items = items
    delivery.status = ServiceManualDelivery.Status.COMPLETED
    delivery.completed_at = now
    if note:
        delivery.last_callback_note = note[:256]
    delivery.save(update_fields=[
        'items', 'status', 'completed_at', 'last_callback_note', 'updated_at',
    ])


def mark_admin_deliver(order: ServiceOrder, *, source='admin_deliver') -> None:
    delivery = delivery_for(order)
    if delivery is None:
        return
    if delivery.status == ServiceManualDelivery.Status.COMPLETED:
        return
    mark_completed(delivery, source=source, note='后台资源交付已完成')
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
    if not order.auto_provision_message or '待人工' in order.auto_provision_message:
        order.auto_provision_message = '人工交付已完成'
    order.save(update_fields=['auto_provision_status', 'auto_provision_message', 'updated_at'])


def _assert_order_deliverable(order: ServiceOrder) -> None:
    if order.status not in (
        ServiceOrder.OrderStatus.APPROVED,
        ServiceOrder.OrderStatus.PROVISIONING,
        ServiceOrder.OrderStatus.ACTIVE,
    ):
        raise ManualDeliveryError('只有已审批或开通中的工单可以交付')
    if not order_ready_for_delivery(order):
        raise ManualDeliveryError('工单预付账单尚未付清，不能交付')


def _active_instance_count(order: ServiceOrder) -> int:
    return order.instances.exclude(
        status__in=[
            ServiceInstance.InstanceStatus.TERMINATED,
            ServiceInstance.InstanceStatus.ERROR,
            ServiceInstance.InstanceStatus.EXPIRED,
        ],
    ).count()


def upsert_socks5_edge(order: ServiceOrder, delivery: ServiceManualDelivery, payload: dict, *, source: str):
    from resource_manager.models import EdgeAcceleration, Socks5Config

    socks5 = payload.get('socks5') if isinstance(payload.get('socks5'), dict) else {}
    edge_id = payload.get('edge_id') or socks5.get('edge_id')
    edge = delivery.resource_edge
    if edge_id:
        edge = EdgeAcceleration.objects.filter(pk=int(edge_id)).first()
        if edge is None:
            raise ManualDeliveryError('指定的边缘资源不存在')
        if edge.protocol != EdgeAcceleration.Protocol.SOCKS5:
            raise ManualDeliveryError('回传资源必须是 Socks5 边缘加速')
        if edge.customer_id not in (None, order.customer_id):
            raise ManualDeliveryError('边缘资源已属于其他客户')
        if order.preferred_pop_id and edge.pop_point_id != order.preferred_pop_id:
            raise ManualDeliveryError('边缘资源所属 POP 与工单不一致')

    create = bool(socks5.get('create_edge') or (socks5 and edge is None))
    if edge is None and create:
        if order.preferred_pop_id is None:
            raise ManualDeliveryError('工单未指定期望 POP，无法创建边缘资源')
        edge = EdgeAcceleration.objects.create(
            name=f'{order.customer.name}-{order.order_no}',
            protocol=EdgeAcceleration.Protocol.SOCKS5,
            status=EdgeAcceleration.EdgeStatus.PENDING,
            pop_point=order.preferred_pop,
            bandwidth_mbps=int(order.requested_bandwidth_mbps or 0) or 100,
            customer=order.customer,
            contract=order.contract,
            product=order.product,
            start_time=timezone.now(),
            remark=f'工单 {order.order_no} 人工交付回传建档',
        )

    if edge is None:
        return None

    listen_port = socks5.get('listen_port') or order.edge_socks5_port or 1080
    username = socks5.get('username') or order.edge_socks5_user or ''
    password = socks5.get('password') or order.edge_socks5_pass or ''
    listen_ip = socks5.get('listen_ip') or '0.0.0.0'
    defaults = {
        'listen_ip': listen_ip,
        'listen_port': int(listen_port),
        'auth_required': bool(username or password),
        'username': username,
        'udp_enabled': True,
    }
    if password:
        defaults['password'] = password
    Socks5Config.objects.update_or_create(edge=edge, defaults=defaults)

    if payload.get('node_pushed') or socks5.get('node_pushed'):
        edge.node_pushed = True
        edge.is_synced = True
        edge.last_synced_at = timezone.now()
        edge.save(update_fields=['node_pushed', 'is_synced', 'last_synced_at', 'updated_at'])

    if edge.customer_id is None:
        edge.customer = order.customer
        edge.save(update_fields=['customer', 'updated_at'])

    delivery.resource_edge = edge
    delivery.save(update_fields=['resource_edge', 'updated_at'])
    marked = ['create_edge', 'configure_auth']
    if edge.node_pushed:
        marked.append('push_node')
    _mark_keys(delivery, marked, source=source)
    return edge


def complete_socks5_delivery(
    order: ServiceOrder,
    delivery: ServiceManualDelivery,
    *,
    operator=None,
    remark='',
    source='callback',
) -> ServiceInstance:
    from .provisioning import build_spec

    _assert_order_deliverable(order)
    edge = delivery.resource_edge
    if edge is None:
        raise ManualDeliveryError('尚未关联 Socks5 边缘资源')
    if not edge.node_pushed:
        raise ManualDeliveryError('节点尚未确认下发，不能激活')
    if ServiceInstance.objects.filter(edge=edge).exists():
        instance = ServiceInstance.objects.get(edge=edge)
        mark_completed(delivery, source=source, note=remark or '实例已存在，清单关闭')
        return instance
    if _active_instance_count(order) >= int(order.requested_quantity or 1):
        raise ManualDeliveryError('工单已完成全部实例交付')

    instance = ServiceInstance.objects.create(
        service_order=order,
        service_type=ServiceInstance.ServiceType.EDGE_ACCELERATION,
        status=ServiceInstance.InstanceStatus.PROVISIONING,
        edge=edge,
        spec_snapshot=build_spec(order),
        provisioned_at=timezone.now(),
        provisioned_by=operator if getattr(operator, 'is_authenticated', False) else None,
        remark=remark or 'Socks5 回传完成交付',
    )
    instance.activate(operator=operator if getattr(operator, 'is_authenticated', False) else None)
    ServiceAllocation.objects.create(
        service_instance=instance,
        action_type=ServiceAllocation.ActionType.ALLOCATE,
        description=f'人工交付回传：工单 {order.order_no} Socks5 {edge.edge_no}',
        resource_type='EdgeAcceleration',
        resource_id=edge.pk,
        resource_repr=edge.edge_no,
        operator=operator if getattr(operator, 'is_authenticated', False) else None,
        remark=remark,
    )
    sync_service_order_delivery_state(
        order, operator=operator if getattr(operator, 'is_authenticated', False) else None,
        reason=f'回传交付实例 {instance.instance_no}',
    )
    from service_manager.capacity_reservation import consume_reservation
    consume_reservation(order, spec=build_spec(order))
    mark_completed(delivery, source=source, note=remark or f'已交付 {instance.instance_no}')
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
    order.auto_provision_message = f'Socks5 {edge.edge_no} 已人工交付'
    if not order.service_start:
        order.service_start = timezone.now().date()
        order.save(update_fields=[
            'auto_provision_status', 'auto_provision_message', 'service_start', 'updated_at',
        ])
    else:
        order.save(update_fields=['auto_provision_status', 'auto_provision_message', 'updated_at'])
    _log_service(
        operator=operator if getattr(operator, 'is_authenticated', False) else None,
        action_type=ServiceOperationLog.ActionType.PROVISION,
        description=f'工单 [{order.order_no}] 回传完成 Socks5 交付 [{instance.instance_no}]',
        service_order=order,
        service_instance=instance,
    )
    return instance


def apply_callback(delivery: ServiceManualDelivery, payload: dict, *, source='callback', operator=None) -> dict:
    note = str(payload.get('note') or '')[:256]
    with transaction.atomic():
        order = ServiceOrder.objects.select_for_update().select_related(
            'customer', 'preferred_pop', 'product', 'contract',
        ).get(pk=delivery.service_order_id)
        delivery = ServiceManualDelivery.objects.select_for_update().select_related(
            'resource_edge',
        ).get(pk=delivery.pk)

        if delivery.status == ServiceManualDelivery.Status.CANCELLED:
            raise ManualDeliveryError('清单已取消')

        delivery.last_callback_at = timezone.now()
        delivery.last_callback_note = note or '状态回传'
        delivery.save(update_fields=['last_callback_at', 'last_callback_note', 'updated_at'])

        _mark_keys(delivery, payload.get('mark') or [], source=source)
        if order.business_type == ServiceOrder.BusinessType.EDGE_SOCKS5:
            upsert_socks5_edge(order, delivery, payload, source=source)

    delivery.refresh_from_db()
    order.refresh_from_db()
    instance = None
    if payload.get('complete'):
        if delivery.status == ServiceManualDelivery.Status.COMPLETED:
            return _callback_result(delivery, instance=None, already=True)
        if order.business_type == ServiceOrder.BusinessType.EDGE_SOCKS5:
            instance = complete_socks5_delivery(
                order, delivery, operator=operator, remark=note, source=source,
            )
        elif _active_instance_count(order) >= int(order.requested_quantity or 1):
            mark_completed(delivery, source=source, note=note or '实例已齐，清单关闭')
        else:
            raise ManualDeliveryError(
                '该类型请在后台「资源交付」完成绑定；回传仅更新清单进度',
            )

    return _callback_result(delivery, instance=instance, already=False)


def _callback_result(delivery: ServiceManualDelivery, *, instance, already: bool) -> dict:
    delivery.refresh_from_db()
    data = public_summary(delivery.service_order, include_items=True) or {}
    data['ok'] = True
    data['already_completed'] = already
    if instance is not None:
        data['instance_id'] = instance.pk
        data['instance_no'] = instance.instance_no
    return data


def parse_callback_payload(data: dict[str, Any] | None) -> dict:
    raw = data or {}
    mark = raw.get('mark') or []
    if isinstance(mark, str):
        mark = [mark]
    socks5 = raw.get('socks5') if isinstance(raw.get('socks5'), dict) else {}
    edge_id = raw.get('edge_id')
    return {
        'mark': [str(item) for item in mark if str(item).strip()],
        'note': str(raw.get('note') or ''),
        'edge_id': int(edge_id) if edge_id not in (None, '') else None,
        'socks5': socks5,
        'node_pushed': bool(raw.get('node_pushed')),
        'complete': bool(raw.get('complete')),
    }
