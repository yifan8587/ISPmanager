"""开通失败 / 升配未执行的退款状态机（B3 骨架）。

只处理已入账且业务失败的预付账单。成功交付、库存不足等人开、续费履约
都不自动退。Stripe / PayPal 走原路退，其余标待人工退。
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from customer_manager.models import CustomerBilling, CustomerOperationLog, PaymentTransaction, record_operation_log
from customer_manager.payment_gateways import PaymentGatewayError, RefundNotSupported, refund_payment

logger = logging.getLogger(__name__)

REFUND_TERMINAL = {
    PaymentTransaction.Status.REFUNDED,
    PaymentTransaction.Status.PENDING_MANUAL,
}
REFUND_IN_FLIGHT = {
    PaymentTransaction.Status.REFUNDING,
    *REFUND_TERMINAL,
}


class RefundError(ValueError):
    pass


def is_eligible_for_auto_refund(billing: CustomerBilling) -> bool:
    return classify_auto_refund_reason(billing) is not None


def classify_auto_refund_reason(billing: CustomerBilling) -> str | None:
    """返回自动退款原因；不合格返回 None。"""
    if billing.status != CustomerBilling.BillingStatus.PAID:
        return None
    if Decimal(str(billing.paid_amount or 0)) <= 0:
        return None
    order = billing.service_order
    if order is None:
        return None
    if order.auto_provision_status != order.AutoProvisionStatus.FAILED:
        return None

    if order.order_type == order.OrderType.NEW:
        if order.instances.exists():
            return None
        return PaymentTransaction.RefundReason.PROVISION_FAILED

    if order.order_type == order.OrderType.UPGRADE:
        change = order.change_records.order_by('-id').first()
        if change is None:
            return PaymentTransaction.RefundReason.UPGRADE_UNEXECUTED
        if change.status == change.ChangeStatus.COMPLETED or change.executed_at:
            return None
        return PaymentTransaction.RefundReason.UPGRADE_UNEXECUTED
    return None


def _latest_refundable_payment(billing: CustomerBilling) -> PaymentTransaction | None:
    return billing.payment_transactions.select_related('channel').filter(
        status__in={
            PaymentTransaction.Status.SUCCEEDED,
            PaymentTransaction.Status.REFUNDING,
            PaymentTransaction.Status.REFUNDED,
            PaymentTransaction.Status.PENDING_MANUAL,
        },
    ).order_by('-id').first()


def _void_commission(billing: CustomerBilling, *, operator=None, reason: str) -> None:
    from customer_manager.models import ChannelCommission

    commission = ChannelCommission.objects.filter(billing=billing).first()
    if commission is None:
        return
    try:
        from customer_manager.channel_services import void_channel_commission
        void_channel_commission(commission, operator=operator, reason=reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning('退款后作废佣金失败 billing=%s: %s', billing.bill_no, exc)


def _append_order_message(order, text: str) -> None:
    message = (order.auto_provision_message or '').strip()
    note = text.strip()
    if not note or note in message:
        return
    order.auto_provision_message = f'{message}\n{note}'[:2000] if message else note[:2000]
    order.save(update_fields=['auto_provision_message', 'updated_at'])


def request_refund_for_billing(
    billing_id: int,
    *,
    reason: str | None = None,
    operator=None,
    force: bool = False,
) -> dict:
    """对合格已付账单发起退款。幂等。"""
    finish_args = None
    with transaction.atomic():
        billing = CustomerBilling.objects.select_for_update().select_related(
            'service_order', 'customer',
        ).get(pk=billing_id)

        if billing.status == CustomerBilling.BillingStatus.REFUNDED:
            return {'triggered': False, 'reason': 'already_refunded', 'billing_status': billing.status}

        if billing.status == CustomerBilling.BillingStatus.REFUNDING:
            payment = _latest_refundable_payment(billing)
            if payment and payment.status == PaymentTransaction.Status.REFUNDING:
                if not payment.refund_idempotency_key:
                    payment.refund_idempotency_key = f'refund:{payment.transaction_no}'
                    payment.save(update_fields=['refund_idempotency_key', 'updated_at'])
                finish_args = {
                    'payment_id': payment.pk,
                    'refund_reason': (
                        payment.refund_reason or reason or PaymentTransaction.RefundReason.MANUAL
                    ),
                    'idempotency_key': payment.refund_idempotency_key,
                    'channel': payment.channel,
                }
            else:
                return {
                    'triggered': False,
                    'reason': 'already_pending',
                    'billing_status': billing.status,
                    'payment_status': payment.status if payment else '',
                }
        else:
            auto_reason = classify_auto_refund_reason(billing)
            refund_reason = reason or auto_reason
            if force:
                if billing.status != CustomerBilling.BillingStatus.PAID:
                    raise RefundError('只有已支付账单可以强制退款')
                refund_reason = refund_reason or PaymentTransaction.RefundReason.MANUAL
            elif not auto_reason:
                raise RefundError('当前账单不符合自动退款条件（仅开通失败且无实例，或升配未执行）')

            payment = _latest_refundable_payment(billing)
            now = timezone.now()
            if payment and payment.status in REFUND_TERMINAL:
                if billing.status == CustomerBilling.BillingStatus.PAID:
                    billing.status = (
                        CustomerBilling.BillingStatus.REFUNDED
                        if payment.status == PaymentTransaction.Status.REFUNDED
                        else CustomerBilling.BillingStatus.REFUNDING
                    )
                    billing.refund_reason = payment.refund_reason or refund_reason
                    billing.save(update_fields=['status', 'refund_reason', 'updated_at'])
                return {
                    'triggered': False,
                    'reason': (
                        'already_refunded'
                        if payment.status == PaymentTransaction.Status.REFUNDED
                        else 'already_pending'
                    ),
                    'payment_status': payment.status,
                }

            billing.status = CustomerBilling.BillingStatus.REFUNDING
            billing.refund_reason = refund_reason
            billing.save(update_fields=['status', 'refund_reason', 'updated_at'])

            if payment is None:
                record_operation_log(
                    operator=operator,
                    action_type=CustomerOperationLog.ActionType.PAYMENT,
                    description=f'账单 [{billing.bill_no}] 开通/升配失败，无在线交易，待人工退款',
                    customer=billing.customer,
                    target_model=CustomerOperationLog.TargetModel.BILLING,
                    target_id=billing.pk,
                    new_value={'status': billing.status, 'refund_reason': refund_reason},
                )
                if billing.service_order_id:
                    _append_order_message(billing.service_order, '已标记待人工退款（无在线支付交易）')
                return {
                    'triggered': True,
                    'status': PaymentTransaction.Status.PENDING_MANUAL,
                    'reason': refund_reason,
                    'manual': True,
                }

            payment = PaymentTransaction.objects.select_for_update().select_related('channel').get(pk=payment.pk)
            if payment.status == PaymentTransaction.Status.SUCCEEDED:
                payment.status = PaymentTransaction.Status.REFUNDING
                payment.refund_reason = refund_reason
                payment.refund_requested_at = now
                payment.refund_idempotency_key = payment.refund_idempotency_key or f'refund:{payment.transaction_no}'
                payment.save(update_fields=[
                    'status', 'refund_reason', 'refund_requested_at',
                    'refund_idempotency_key', 'updated_at',
                ])
            finish_args = {
                'payment_id': payment.pk,
                'refund_reason': refund_reason,
                'idempotency_key': payment.refund_idempotency_key,
                'channel': payment.channel,
            }

    return _finish_gateway_refund(
        finish_args['payment_id'],
        billing_id=billing_id,
        refund_reason=finish_args['refund_reason'],
        idempotency_key=finish_args['idempotency_key'],
        channel=finish_args['channel'],
        operator=operator,
    )


def _finish_gateway_refund(
    payment_id: int,
    *,
    billing_id: int,
    refund_reason: str,
    idempotency_key: str,
    channel,
    operator=None,
) -> dict:
    payment = PaymentTransaction.objects.select_related('billing', 'channel').get(pk=payment_id)
    manual = False
    provider_refund_id = ''
    failure_reason = ''
    response = {}
    try:
        if channel is None:
            raise RefundNotSupported('交易未绑定支付渠道')
        result = refund_payment(
            payment, channel,
            amount=payment.amount,
            idempotency_key=idempotency_key,
        )
        provider_refund_id = result.get('provider_refund_id') or ''
        response = result.get('response') or {}
    except RefundNotSupported as exc:
        manual = True
        failure_reason = str(exc)
    except PaymentGatewayError as exc:
        manual = True
        failure_reason = str(exc)
        logger.warning('原路退失败，转人工 payment=%s: %s', payment.transaction_no, exc)

    now = timezone.now()
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().select_related('billing').get(pk=payment_id)
        billing = CustomerBilling.objects.select_for_update().get(pk=billing_id)
        if locked.status == PaymentTransaction.Status.REFUNDED:
            return {'triggered': False, 'reason': 'already_refunded', 'payment_status': locked.status}

        if manual:
            locked.status = PaymentTransaction.Status.PENDING_MANUAL
            locked.failure_reason = failure_reason
            locked.refund_reason = refund_reason
            locked.save(update_fields=['status', 'failure_reason', 'refund_reason', 'updated_at'])
            billing.status = CustomerBilling.BillingStatus.REFUNDING
            billing.refund_reason = refund_reason
            billing.save(update_fields=['status', 'refund_reason', 'updated_at'])
            final_status = locked.status
        else:
            locked.status = PaymentTransaction.Status.REFUNDED
            locked.refunded_amount = locked.amount
            locked.provider_refund_id = provider_refund_id
            locked.refunded_at = now
            locked.refund_reason = refund_reason
            if response:
                locked.provider_response = response
            locked.failure_reason = ''
            locked.save()
            billing.status = CustomerBilling.BillingStatus.REFUNDED
            billing.refunded_amount = billing.paid_amount
            billing.refund_reason = refund_reason
            billing.refunded_at = now
            billing.save(update_fields=[
                'status', 'refunded_amount', 'refund_reason', 'refunded_at', 'updated_at',
            ])
            _void_commission(billing, operator=operator, reason=f'自动退款：{refund_reason}')
            from customer_manager.wallet import debit_refunded_recharge

            debit_refunded_recharge(billing, operator=operator)
            final_status = locked.status

        record_operation_log(
            operator=operator,
            action_type=CustomerOperationLog.ActionType.PAYMENT,
            description=(
                f'账单 [{billing.bill_no}] 退款'
                + ('待人工处理' if manual else '已原路退回')
                + f'，交易 [{locked.transaction_no}]'
            ),
            customer=billing.customer,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=billing.pk,
            new_value={
                'status': billing.status,
                'payment_status': locked.status,
                'refund_reason': refund_reason,
                'provider_refund_id': provider_refund_id,
            },
        )
        if billing.service_order_id:
            _append_order_message(
                billing.service_order,
                f'已发起退款 {locked.transaction_no}（{locked.get_status_display()}）',
            )

    return {
        'triggered': True,
        'status': final_status,
        'reason': refund_reason,
        'manual': manual,
        'provider_refund_id': provider_refund_id,
    }


def mark_manual_refund_completed(billing_id: int, *, operator=None) -> dict:
    """把待人工退的账单标为已退款。"""
    with transaction.atomic():
        billing = CustomerBilling.objects.select_for_update().select_related('customer').get(pk=billing_id)
        if billing.status == CustomerBilling.BillingStatus.REFUNDED:
            return {'triggered': False, 'reason': 'already_refunded'}
        if billing.status not in {
            CustomerBilling.BillingStatus.REFUNDING,
            CustomerBilling.BillingStatus.PAID,
        }:
            raise RefundError('只有退款中或已支付账单可以确认人工退款')

        now = timezone.now()
        payment = _latest_refundable_payment(billing)
        if payment:
            payment = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
            payment.status = PaymentTransaction.Status.REFUNDED
            payment.refunded_amount = payment.amount
            payment.refunded_at = now
            payment.refund_reason = payment.refund_reason or PaymentTransaction.RefundReason.MANUAL
            payment.save(update_fields=[
                'status', 'refunded_amount', 'refunded_at', 'refund_reason', 'updated_at',
            ])

        billing.status = CustomerBilling.BillingStatus.REFUNDED
        billing.refunded_amount = billing.paid_amount
        billing.refunded_at = now
        billing.refund_reason = billing.refund_reason or (
            payment.refund_reason if payment else PaymentTransaction.RefundReason.MANUAL
        )
        billing.save(update_fields=[
            'status', 'refunded_amount', 'refunded_at', 'refund_reason', 'updated_at',
        ])
        _void_commission(billing, operator=operator, reason='人工确认退款')
        from customer_manager.wallet import debit_refunded_recharge

        debit_refunded_recharge(billing, operator=operator)
        record_operation_log(
            operator=operator,
            action_type=CustomerOperationLog.ActionType.PAYMENT,
            description=f'账单 [{billing.bill_no}] 已确认人工退款',
            customer=billing.customer,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=billing.pk,
            new_value={'status': billing.status},
        )
    return {'triggered': True, 'status': CustomerBilling.BillingStatus.REFUNDED}


def maybe_auto_refund(billing_id: int) -> dict | None:
    """履约结束后尝试自动退；不合格则静默返回。"""
    try:
        return request_refund_for_billing(billing_id)
    except RefundError:
        return None
    except CustomerBilling.DoesNotExist:
        return None
    except Exception:  # noqa: BLE001
        logger.exception('自动退款失败 billing=%s', billing_id)
        return None


def scan_eligible_refunds(*, dry_run: bool = False) -> dict:
    qs = CustomerBilling.objects.filter(
        status=CustomerBilling.BillingStatus.PAID,
        service_order__auto_provision_status='failed',
        service_order__order_type__in=['new', 'upgrade'],
    ).select_related('service_order')
    stats = {'scanned': 0, 'refunded': 0, 'skipped': 0, 'errors': 0, 'planned': []}
    for billing in qs.iterator():
        stats['scanned'] += 1
        reason = classify_auto_refund_reason(billing)
        if not reason:
            stats['skipped'] += 1
            continue
        stats['planned'].append(f'{billing.bill_no}: {reason}')
        if dry_run:
            continue
        try:
            result = request_refund_for_billing(billing.pk, reason=reason)
            if result.get('triggered'):
                stats['refunded'] += 1
            else:
                stats['skipped'] += 1
        except Exception:  # noqa: BLE001
            stats['errors'] += 1
            logger.exception('扫描退款失败 billing=%s', billing.pk)
    return stats
