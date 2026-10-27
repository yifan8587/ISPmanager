"""服务工单与变更单的状态、支付守卫。"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from customer_manager.models import CustomerBilling
from service_manager.models import ServiceChangeRecord, ServiceOrder


def order_due_amount(order: ServiceOrder | None) -> Decimal:
    if order is None or order.quoted_price is None:
        return Decimal('0.00')
    return Decimal(str(order.quoted_price)).quantize(Decimal('0.01'))


def related_order_billing(order: ServiceOrder | None):
    if order is None:
        return None
    return CustomerBilling.objects.filter(service_order_id=order.pk).first()


def change_unpaid_reason(record: ServiceChangeRecord) -> str | None:
    """有未付预付账单时返回原因，否则返回 None。"""
    order = record.related_order
    if order is None:
        return None
    billing = related_order_billing(order)
    if billing is not None:
        if billing.status != CustomerBilling.BillingStatus.PAID:
            return f'关联工单专属账单 {billing.bill_no} 尚未付清，不能执行变更'
        return None
    if order_due_amount(order) > 0:
        return '关联工单尚未生成或支付预付账单，不能执行变更'
    return None


def change_requires_payment(record: ServiceChangeRecord) -> bool:
    return change_unpaid_reason(record) is not None


def change_payment_snapshot(record: ServiceChangeRecord) -> dict | None:
    billing = related_order_billing(record.related_order)
    if billing is None:
        return None
    return {
        'id': billing.pk,
        'bill_no': billing.bill_no,
        'amount': str(billing.amount),
        'paid_amount': str(billing.paid_amount),
        'currency': billing.currency,
        'status': billing.status,
        'status_label': billing.get_status_display(),
        'due_date': billing.due_date,
        'paid_at': billing.paid_at,
    }


def ensure_change_related_order_approved(record: ServiceChangeRecord, *, operator=None):
    """审批变更时同步关联工单，有报价则生成预付账单。"""
    order = record.related_order
    if order is None:
        return None

    update_fields = set()
    if order.status == ServiceOrder.OrderStatus.PENDING:
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.approved_by = operator
        order.approved_at = timezone.now()
        update_fields.update({'status', 'approved_by', 'approved_at'})

    due = order_due_amount(order)
    billing = None
    if due > 0:
        from .payment_billing import OrderBillingError, ensure_order_payment_billing

        try:
            billing = ensure_order_payment_billing(order)
        except OrderBillingError as exc:
            raise ValidationError({'quoted_price': str(exc)}) from exc
        billing = related_order_billing(order) if billing is None else billing
        if billing is not None and billing.status != CustomerBilling.BillingStatus.PAID:
            order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING
            order.auto_provision_message = (
                f'等待支付账单 {billing.bill_no}'
                f'（{billing.currency}{billing.amount:.2f}）'
            )
            update_fields.update({'auto_provision_status', 'auto_provision_message'})
    if update_fields:
        update_fields.add('updated_at')
        order.save(update_fields=sorted(update_fields))
    return billing


def close_change_related_order(record: ServiceChangeRecord, *, reason: str, allow_paid: bool = False):
    """取消/驳回变更时同步未交付的关联工单。"""
    order = record.related_order
    if order is None:
        return
    closable = {
        ServiceOrder.OrderStatus.DRAFT,
        ServiceOrder.OrderStatus.PENDING,
        ServiceOrder.OrderStatus.APPROVED,
    }
    if order.status not in closable:
        return

    billing = related_order_billing(order)
    if (
        billing is not None
        and billing.status == CustomerBilling.BillingStatus.PAID
        and not allow_paid
    ):
        raise ValidationError(
            '关联工单账单已支付，不能直接取消/驳回；请先处理退款或继续执行变更'
        )

    order.status = ServiceOrder.OrderStatus.REJECTED
    order.reject_reason = (reason or '关联变更已关闭')[:500]
    order.auto_provision_message = order.reject_reason
    order.save(update_fields=[
        'status', 'reject_reason', 'auto_provision_message', 'updated_at',
    ])
    from .capacity_reservation import release_reservation
    release_reservation(order, reason=order.reject_reason)
