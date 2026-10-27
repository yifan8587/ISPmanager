"""客户账户余额：充值入账、账单抵扣、人工调账。"""
from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db import transaction as db_transaction
from django.utils import timezone

from system_manager.currency_services import default_currency

from .models import (
    Customer,
    CustomerBalanceLedger,
    CustomerBilling,
    CustomerOperationLog,
    record_operation_log,
)

_ZERO = Decimal('0.00')
_CENT = Decimal('0.01')
MIN_RECHARGE = Decimal('1.00')
MAX_RECHARGE = Decimal('100000.00')


class WalletError(ValueError):
    """余额规则校验失败。"""


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def wallet_currency() -> str:
    return (default_currency() or 'CNY').upper()


def _entry_label(entry_type: str) -> str:
    mapping = dict(CustomerBalanceLedger.EntryType.choices)
    return mapping.get(entry_type, entry_type)


@transaction.atomic
def adjust_customer_balance(
    customer: Customer,
    *,
    delta,
    entry_type: str,
    remark: str = '',
    operator=None,
    related_billing: CustomerBilling | None = None,
    currency: str | None = None,
) -> CustomerBalanceLedger:
    """变更客户余额并写流水。允许调成负数（退回已花费的充值）。"""
    delta = q2(delta)
    if delta == 0:
        raise WalletError('变动金额不能为零')
    if entry_type not in CustomerBalanceLedger.EntryType.values:
        raise WalletError('无效的流水类型')

    locked = Customer.objects.select_for_update().get(pk=customer.pk)
    currency = (currency or wallet_currency()).upper()
    new_balance = q2(locked.balance + delta)
    locked.balance = new_balance
    locked.save(update_fields=['balance', 'updated_at'])

    ledger = CustomerBalanceLedger.objects.create(
        customer=locked,
        entry_type=entry_type,
        delta=delta,
        balance_after=new_balance,
        currency=currency,
        related_billing=related_billing,
        remark=(remark or '')[:256],
        operator=operator,
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=(
            f'账户余额{_entry_label(entry_type)} {currency}{delta:+,.2f}，'
            f'余额 {currency}{new_balance:,.2f}'
            + (f'，{remark}' if remark else '')
        ),
        customer=locked,
        target_model=CustomerOperationLog.TargetModel.CUSTOMER,
        target_id=locked.pk,
        new_value={
            'entry_type': entry_type,
            'delta': str(delta),
            'balance_after': str(new_balance),
            'currency': currency,
            'related_billing_id': related_billing.pk if related_billing else None,
        },
    )
    return ledger


@transaction.atomic
def create_recharge_billing(customer: Customer, *, amount, operator=None) -> CustomerBilling:
    amount = q2(amount)
    if amount < MIN_RECHARGE:
        raise WalletError(f'充值金额不能低于 {MIN_RECHARGE}')
    if amount > MAX_RECHARGE:
        raise WalletError(f'充值金额不能超过 {MAX_RECHARGE}')

    today = timezone.localdate()
    billing = CustomerBilling.objects.create(
        customer=customer,
        bill_month=today.strftime('%Y-%m'),
        amount=amount,
        gross_amount=amount,
        currency=wallet_currency(),
        auto_generated=False,
        billing_kind=CustomerBilling.BillingKind.WALLET_RECHARGE,
        due_date=today + datetime.timedelta(days=7),
        remark='账户余额充值',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=f'创建余额充值账单 [{billing.bill_no}] {billing.currency}{amount:,.2f}',
        customer=customer,
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=billing.pk,
        new_value={'bill_no': billing.bill_no, 'amount': str(amount)},
    )
    return billing


@transaction.atomic
def credit_paid_recharge(billing: CustomerBilling, *, operator=None) -> CustomerBalanceLedger | None:
    """充值单变为已支付后入账。按账单 + recharge 幂等。"""
    locked = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer')
        .get(pk=billing.pk)
    )
    if locked.billing_kind != CustomerBilling.BillingKind.WALLET_RECHARGE:
        return None
    if locked.status != CustomerBilling.BillingStatus.PAID:
        return None

    existing = (
        CustomerBalanceLedger.objects.select_for_update()
        .filter(
            related_billing=locked,
            entry_type=CustomerBalanceLedger.EntryType.RECHARGE,
        )
        .first()
    )
    if existing:
        return existing

    return adjust_customer_balance(
        locked.customer,
        delta=q2(locked.amount),
        entry_type=CustomerBalanceLedger.EntryType.RECHARGE,
        remark=f'充值账单 {locked.bill_no} 入账',
        operator=operator,
        related_billing=locked,
        currency=locked.currency,
    )


@transaction.atomic
def debit_refunded_recharge(billing: CustomerBilling, *, operator=None) -> CustomerBalanceLedger | None:
    """充值单退款完成后扣回余额。从未入账则跳过；按账单 + refund 幂等。"""
    locked = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer')
        .get(pk=billing.pk)
    )
    if locked.billing_kind != CustomerBilling.BillingKind.WALLET_RECHARGE:
        return None
    if locked.status != CustomerBilling.BillingStatus.REFUNDED:
        return None

    existing = (
        CustomerBalanceLedger.objects.select_for_update()
        .filter(
            related_billing=locked,
            entry_type=CustomerBalanceLedger.EntryType.REFUND,
        )
        .first()
    )
    if existing:
        return existing

    credited = (
        CustomerBalanceLedger.objects.filter(
            related_billing=locked,
            entry_type=CustomerBalanceLedger.EntryType.RECHARGE,
        )
        .first()
    )
    if credited is None:
        return None

    return adjust_customer_balance(
        locked.customer,
        delta=-q2(credited.delta),
        entry_type=CustomerBalanceLedger.EntryType.REFUND,
        remark=f'充值账单 {locked.bill_no} 退回',
        operator=operator,
        related_billing=locked,
        currency=locked.currency or credited.currency,
    )


@transaction.atomic
def apply_balance_to_billing(
    billing: CustomerBilling,
    *,
    operator=None,
    request=None,
) -> CustomerBilling:
    """用余额抵扣普通账单。禁止抵扣充值单；币种须与钱包一致。"""
    locked = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer')
        .get(pk=billing.pk)
    )
    if locked.billing_kind == CustomerBilling.BillingKind.WALLET_RECHARGE:
        raise WalletError('充值账单不能使用余额支付')
    if locked.status in {
        CustomerBilling.BillingStatus.CANCELLED,
        CustomerBilling.BillingStatus.REFUNDING,
        CustomerBilling.BillingStatus.REFUNDED,
    }:
        raise WalletError('账单已取消或已退款，无法支付')

    currency = wallet_currency()
    bill_currency = (locked.currency or 'CNY').upper()
    if bill_currency != currency:
        raise WalletError(f'余额仅支持 {currency} 账单，当前账单币种为 {bill_currency}')

    outstanding = q2(locked.amount - locked.paid_amount)
    if outstanding <= _ZERO or locked.status == CustomerBilling.BillingStatus.PAID:
        raise WalletError('账单已结清')

    customer = Customer.objects.select_for_update().get(pk=locked.customer_id)
    available = q2(customer.balance)
    if available <= _ZERO:
        raise WalletError('账户余额不足')

    apply_amount = min(available, outstanding)
    old_status = locked.status
    old_paid = locked.paid_amount

    adjust_customer_balance(
        customer,
        delta=-apply_amount,
        entry_type=CustomerBalanceLedger.EntryType.APPLY,
        remark=f'抵扣账单 {locked.bill_no}',
        operator=operator,
        related_billing=locked,
        currency=currency,
    )

    locked.paid_amount = q2(locked.paid_amount + apply_amount)
    locked.payment_method = CustomerBilling.PaymentMethod.BALANCE
    locked.paid_at = timezone.now()
    if locked.paid_amount >= locked.amount:
        locked.status = CustomerBilling.BillingStatus.PAID
    locked.save(update_fields=[
        'paid_amount', 'payment_method', 'paid_at', 'status', 'updated_at',
    ])

    commission = None
    if (
        old_status != CustomerBilling.BillingStatus.PAID
        and locked.status == CustomerBilling.BillingStatus.PAID
    ):
        from .channel_services import ensure_paid_billing_commission

        commission = ensure_paid_billing_commission(
            locked,
            operator=operator,
            request=request,
        )
        if locked.service_order_id:
            from service_manager.payment_fulfillment import fulfill_paid_billing_safely

            db_transaction.on_commit(
                lambda billing_id=locked.pk: fulfill_paid_billing_safely(billing_id),
            )

    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=(
            f'余额支付 {currency}{apply_amount:,.2f}，账单 [{locked.bill_no}]'
        ),
        customer=locked.customer,
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=locked.pk,
        old_value={'paid_amount': str(old_paid), 'status': old_status},
        new_value={
            'paid_amount': str(locked.paid_amount),
            'status': locked.status,
            'channel_commission_id': commission.pk if commission else None,
        },
        request=request,
    )
    return locked
