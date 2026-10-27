from __future__ import annotations

import logging
import threading

from django.db import transaction

from customer_manager.models import CustomerBilling

from .models import ServiceOrder

logger = logging.getLogger(__name__)


def fulfill_paid_billing(billing_id: int, *, force: bool = False, operator=None) -> dict:
    """幂等地把已付工单预付账单交给现有自动开通编排。"""
    with transaction.atomic():
        billing = CustomerBilling.objects.select_for_update().select_related(
            'service_order',
        ).get(pk=billing_id)
        if (
            billing.status != CustomerBilling.BillingStatus.PAID
            or not billing.service_order_id
        ):
            return {'triggered': False, 'reason': 'billing_not_eligible'}

        order = ServiceOrder.objects.select_for_update().get(
            pk=billing.service_order_id,
        )
        from .order_pop import apply_product_fulfillment
        apply_product_fulfillment(order, save=True)
        from .capacity_reservation import pin_reservation
        pin_reservation(order)

        # 服务变更工单：支付成功后执行升配，不走新开自动开通。
        if order.order_type in (
            ServiceOrder.OrderType.UPGRADE,
            ServiceOrder.OrderType.DOWNGRADE,
            ServiceOrder.OrderType.CHANGE,
        ) and order.related_instance_id:
            from .change_billing import fulfill_paid_change_order

            return fulfill_paid_change_order(order, operator=operator)

        if order.order_type == ServiceOrder.OrderType.RENEW and order.related_instance_id:
            from .renew_billing import fulfill_paid_renewal_order

            return fulfill_paid_renewal_order(order, operator=operator)

        if order.status != ServiceOrder.OrderStatus.APPROVED:
            return {'triggered': False, 'reason': 'order_not_approved'}
        if order.instances.exists():
            return {'triggered': False, 'reason': 'instance_exists'}
        if order.auto_provision_status == ServiceOrder.AutoProvisionStatus.SUCCESS:
            return {'triggered': False, 'reason': 'already_triggered'}
        if (
            not force
            and order.auto_provision_status == ServiceOrder.AutoProvisionStatus.PROVISIONING
        ):
            return {'triggered': False, 'reason': 'already_triggered'}

        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PROVISIONING
        order.auto_provision_message = f'账单 {billing.bill_no} 已支付，正在自动开通'
        order.save(update_fields=[
            'auto_provision_status',
            'auto_provision_message',
            'updated_at',
        ])

    from .provisioning import auto_provision

    order.refresh_from_db()
    result = auto_provision(order, operator=operator)
    if not result.get('auto'):
        ServiceOrder.objects.filter(
            pk=order.pk,
            auto_provision_status=ServiceOrder.AutoProvisionStatus.PROVISIONING,
        ).update(
            auto_provision_status=ServiceOrder.AutoProvisionStatus.PAID_PENDING,
            auto_provision_message='支付已确认；当前节点需管理员手动开通',
        )
    return {'triggered': True, 'result': result}


def schedule_fulfill_paid_billing(billing_id: int, *, force: bool = False, operator=None) -> None:
    """支付确认后后台开通，避免门户请求卡住等待虚机创建。"""
    def _run():
        fulfill_paid_billing_safely(billing_id, force=force, operator=operator)

    def _start():
        threading.Thread(target=_run, name=f'fulfill-billing-{billing_id}', daemon=True).start()

    transaction.on_commit(_start)


def schedule_auto_provision(order_id: int, *, operator=None) -> None:
    """零金额等无需账单的工单：提交后后台开通。"""
    def _run():
        try:
            from .models import ServiceOrder
            from .provisioning import auto_provision

            order = ServiceOrder.objects.filter(pk=order_id).first()
            if order is None:
                return
            auto_provision(order, operator=operator)
        except Exception:  # noqa: BLE001
            logger.exception('工单 %s 后台自动开通异常', order_id)

    def _start():
        threading.Thread(target=_run, name=f'auto-provision-{order_id}', daemon=True).start()

    transaction.on_commit(_start)


def fulfill_paid_billing_safely(billing_id: int, *, force: bool = False, operator=None) -> None:
    """on_commit 入口；履约异常不能影响已经确认的资金入账。"""
    try:
        fulfill_paid_billing(billing_id, force=force, operator=operator)
    except Exception as exc:  # noqa: BLE001
        logger.exception('账单 %s 支付后自动开通异常', billing_id)
        billing = CustomerBilling.objects.filter(pk=billing_id).first()
        if billing and billing.service_order_id:
            ServiceOrder.objects.filter(pk=billing.service_order_id).update(
                auto_provision_status=ServiceOrder.AutoProvisionStatus.FAILED,
                auto_provision_message=str(exc)[:2000],
            )
            from .capacity_reservation import release_reservation
            failed_order = ServiceOrder.objects.filter(pk=billing.service_order_id).first()
            if failed_order is not None:
                release_reservation(failed_order, reason='履约异常，回滚预占')
    try:
        from customer_manager.refund_engine import maybe_auto_refund

        maybe_auto_refund(billing_id)
    except Exception:  # noqa: BLE001
        logger.exception('账单 %s 履约后自动退款异常', billing_id)


def maybe_fulfill_succeeded_payment(payment) -> None:
    """支付已成功时再触发一次幂等履约，避免只等 webhook。"""
    from customer_manager.models import CustomerBilling, PaymentTransaction

    if getattr(payment, 'status', None) != PaymentTransaction.Status.SUCCEEDED:
        return
    billing = getattr(payment, 'billing', None)
    if billing is None:
        return
    if billing.status != CustomerBilling.BillingStatus.PAID or not billing.service_order_id:
        return
    fulfill_paid_billing_safely(billing.pk)
