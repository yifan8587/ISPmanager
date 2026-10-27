"""资源补充后提醒管理员手动交付。

库存不足成交的已付款工单不会自动开通。容量补齐后只发一次告警，提示到工单里
手动交付；容量仍不足时不重复告警。
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q

from customer_manager.models import CustomerBilling

from .models import ServiceOrder
from .provisioning import _check_resources, build_spec, capacity_allows, notify_managers

READY_NOTE = '资源已补齐，请在服务工单中手动交付给客户。系统不会自动开通。'

logger = logging.getLogger(__name__)


def retry_insufficient_paid_orders_for_pop(pop, *, operator=None, reason: str = '') -> dict:
    """容量变化后检查待人工交付的已付款工单。

    资源已经够用时通知管理员手动交付，不调用自动开通。仍不足的工单保持原状态，
    也不重复发送告警。
    """
    if pop is None or not getattr(pop, 'pk', None):
        return {
            'pop_id': None, 'checked': 0, 'retried': 0,
            'ready_for_manual': 0, 'still_insufficient': 0,
        }

    billings = (
        CustomerBilling.objects.filter(
            status=CustomerBilling.BillingStatus.PAID,
            service_order__preferred_pop_id=pop.pk,
            service_order__status__in=(
                ServiceOrder.OrderStatus.APPROVED,
                ServiceOrder.OrderStatus.PROVISIONING,
            ),
            service_order__auto_provision_status=ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
        )
        .filter(
            Q(
                service_order__order_type=ServiceOrder.OrderType.NEW,
                service_order__instances__isnull=True,
            )
            | Q(service_order__order_type__in=(
                ServiceOrder.OrderType.UPGRADE,
                ServiceOrder.OrderType.DOWNGRADE,
                ServiceOrder.OrderType.CHANGE,
            ))
        )
        .select_related('service_order', 'service_order__preferred_pop')
        .distinct()
    )

    checked = ready_for_manual = still_insufficient = 0
    results = []
    for billing in billings.iterator():
        order = billing.service_order
        checked += 1
        try:
            if order.order_type in (
                ServiceOrder.OrderType.UPGRADE,
                ServiceOrder.OrderType.DOWNGRADE,
                ServiceOrder.OrderType.CHANGE,
            ):
                from .change_provisioning import change_capacity_spec
                spec = change_capacity_spec(order)
            else:
                spec = build_spec(order)
            capacity = _check_resources(order.preferred_pop, spec, exclude_order=order)
            if not capacity_allows(order.preferred_pop, capacity):
                still_insufficient += 1
                results.append({
                    'order_no': order.order_no,
                    'result': 'still_insufficient',
                    'shortages': capacity.get('shortages') or [],
                })
                continue

            if (order.auto_provision_message or '') == READY_NOTE:
                results.append({'order_no': order.order_no, 'result': 'already_notified'})
                continue

            order.auto_provision_message = READY_NOTE
            order.save(update_fields=['auto_provision_message', 'updated_at'])
            notify_managers(
                order,
                '资源已补齐待手动交付',
                f'{READY_NOTE}' + (f'（{reason}）' if reason else ''),
            )
            ready_for_manual += 1
            results.append({'order_no': order.order_no, 'result': 'ready_for_manual'})
        except Exception:  # noqa: BLE001
            logger.exception(
                'POP %s 资源补齐后通知工单 %s 失败（%s）',
                pop.pk,
                order.order_no,
                reason or '未提供原因',
            )
            results.append({'order_no': order.order_no, 'result': 'error'})

    return {
        'pop_id': pop.pk,
        'checked': checked,
        'retried': 0,
        'ready_for_manual': ready_for_manual,
        'still_insufficient': still_insufficient,
        'results': results,
    }


def schedule_capacity_recovery(pop, *, operator=None, reason: str = '') -> None:
    """在当前数据库提交成功后，提醒管理员手动交付已补齐资源的工单。"""
    if pop is None or not getattr(pop, 'pk', None):
        return

    def _retry():
        retry_insufficient_paid_orders_for_pop(pop, operator=operator, reason=reason)

    transaction.on_commit(_retry)
