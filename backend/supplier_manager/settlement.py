"""
供应商账单入账与结算。
客户账单支付完成后按产品所属供应商拆分，并按结算比例生成待结账单。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Supplier, SupplierBill, SupplierSettlement

_HUNDRED = Decimal('100')
_ZERO = Decimal('0.00')


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class _Share:
    supplier: Supplier
    product: object | None
    amount: Decimal
    item: object | None
    order: object | None


def _iter_supplier_shares(billing) -> list[_Share]:
    shares: list[_Share] = []
    items = list(billing.items.select_related('product__supplier').all())
    if items:
        for item in items:
            product = getattr(item, 'product', None)
            supplier = getattr(product, 'supplier', None) if product else None
            amount = q2(getattr(item, 'amount', 0))
            if supplier is None or amount <= 0:
                continue
            shares.append(_Share(
                supplier=supplier, product=product, amount=amount,
                item=item, order=None,
            ))
        return shares

    order = getattr(billing, 'service_order', None)
    product = getattr(order, 'product', None) if order else None
    supplier = getattr(product, 'supplier', None) if product else None
    amount = q2(getattr(billing, 'amount', 0))
    if supplier is None or amount <= 0:
        return []
    shares.append(_Share(
        supplier=supplier, product=product, amount=amount,
        item=None, order=order,
    ))
    return shares


def _create_bill(billing, share: _Share) -> tuple[SupplierBill, bool]:
    supplier = share.supplier
    rate = q2(supplier.settlement_rate or 90)
    if rate < 0:
        rate = _ZERO
    if rate > _HUNDRED:
        rate = _HUNDRED
    settle_amount = q2(share.amount * rate / _HUNDRED)
    product = share.product
    customer = getattr(billing, 'customer', None)
    defaults = {
        'supplier': supplier,
        'customer_billing': billing,
        'service_order': share.order or getattr(billing, 'service_order', None),
        'product': product,
        'customer_bill_no': getattr(billing, 'bill_no', '') or '',
        'customer_name': getattr(customer, 'name', '') or '',
        'product_name': (
            getattr(share.item, 'product_name', '')
            or getattr(product, 'name', '')
            or ''
        ),
        'bill_month': getattr(billing, 'bill_month', '') or '',
        'source_amount': share.amount,
        'settlement_rate': rate,
        'settle_amount': settle_amount,
        'currency': getattr(billing, 'currency', None) or supplier.settlement_currency or 'CNY',
        'status': SupplierBill.Status.PENDING,
        'paid_at': getattr(billing, 'paid_at', None),
    }
    if share.item is not None:
        obj, created = SupplierBill.objects.get_or_create(
            billing_item=share.item,
            defaults=defaults,
        )
        return obj, created
    obj, created = SupplierBill.objects.get_or_create(
        customer_billing=billing,
        product=product,
        billing_item=None,
        defaults=defaults,
    )
    return obj, created


@transaction.atomic
def ensure_paid_supplier_bills(billing, *, operator=None) -> list[SupplierBill]:
    """客户账单已支付后生成供应商账单。幂等。"""
    from customer_manager.models import CustomerBilling

    if getattr(billing, 'status', None) != CustomerBilling.BillingStatus.PAID:
        return []
    if getattr(billing, 'billing_kind', None) == CustomerBilling.BillingKind.WALLET_RECHARGE:
        return []

    locked = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer', 'service_order__product__supplier')
        .prefetch_related('items__product__supplier')
        .get(pk=billing.pk)
    )
    created_bills: list[SupplierBill] = []
    touched_suppliers: dict[int, Supplier] = {}
    for share in _iter_supplier_shares(locked):
        bill, created = _create_bill(locked, share)
        touched_suppliers[share.supplier.pk] = share.supplier
        if created:
            created_bills.append(bill)

    for supplier in touched_suppliers.values():
        maybe_auto_settle(supplier, operator=operator)
    return created_bills


def pending_bills_qs(supplier: Supplier, currency: str | None = None):
    qs = SupplierBill.objects.filter(
        supplier=supplier,
        status=SupplierBill.Status.PENDING,
        settlement__isnull=True,
    )
    if currency:
        qs = qs.filter(currency=currency)
    return qs


def finance_summary(supplier: Supplier) -> dict:
    pending = pending_bills_qs(supplier)
    pending_source = q2(pending.aggregate(t=Sum('source_amount'))['t'] or 0)
    pending_settle = q2(pending.aggregate(t=Sum('settle_amount'))['t'] or 0)
    in_settlement = SupplierBill.objects.filter(
        supplier=supplier, status=SupplierBill.Status.IN_SETTLEMENT,
    )
    in_amount = q2(in_settlement.aggregate(t=Sum('settle_amount'))['t'] or 0)
    settled = SupplierBill.objects.filter(
        supplier=supplier, status=SupplierBill.Status.SETTLED,
    )
    settled_amount = q2(settled.aggregate(t=Sum('settle_amount'))['t'] or 0)
    min_amount = q2(supplier.settlement_min_amount or 0)
    return {
        'supplier_id': supplier.pk,
        'supplier_name': supplier.name,
        'settlement_rate': q2(supplier.settlement_rate or 90),
        'settlement_min_amount': min_amount,
        'settlement_currency': supplier.settlement_currency or 'CNY',
        'pending_source_amount': pending_source,
        'pending_settle_amount': pending_settle,
        'in_settlement_amount': in_amount,
        'settled_amount': settled_amount,
        'pending_bill_count': pending.count(),
        'can_settle': pending_settle > 0 and (min_amount <= 0 or pending_settle >= min_amount),
        'threshold_gap': q2(max(min_amount - pending_settle, _ZERO)) if min_amount > 0 else _ZERO,
    }


def _assert_can_settle(supplier: Supplier, bills: list[SupplierBill], *, enforce_threshold: bool):
    if not bills:
        raise ValidationError('暂无待结算账单')
    currencies = {b.currency for b in bills}
    if len(currencies) > 1:
        raise ValidationError('一次结算只能包含同一币种的账单')
    total = q2(sum((b.settle_amount for b in bills), _ZERO))
    min_amount = q2(supplier.settlement_min_amount or 0)
    if enforce_threshold and min_amount > 0 and total < min_amount:
        raise ValidationError(
            f'待结算金额 {total} 未达到满额结算阈值 {min_amount}'
        )
    return total, next(iter(currencies))


@transaction.atomic
def create_settlement(
    supplier: Supplier,
    bills: list[SupplierBill] | None = None,
    *,
    operator=None,
    auto: bool = False,
    enforce_threshold: bool = False,
) -> SupplierSettlement:
    if bills is None:
        bills = list(pending_bills_qs(supplier).select_related('supplier'))
    else:
        bills = [
            b for b in bills
            if b.supplier_id == supplier.pk
            and b.status == SupplierBill.Status.PENDING
            and b.settlement_id is None
        ]
    total, currency = _assert_can_settle(
        supplier, bills, enforce_threshold=enforce_threshold,
    )
    settlement = SupplierSettlement.objects.create(
        supplier=supplier,
        amount=total,
        currency=currency,
        bill_count=len(bills),
        status=SupplierSettlement.Status.PENDING,
        auto_created=auto,
        remark='系统满额自动结算' if auto else '',
        created_by=operator,
    )
    SupplierBill.objects.filter(pk__in=[b.pk for b in bills]).update(
        settlement=settlement,
        status=SupplierBill.Status.IN_SETTLEMENT,
        updated_at=timezone.now(),
    )
    return settlement


def maybe_auto_settle(supplier: Supplier, *, operator=None) -> list[SupplierSettlement]:
    """待结金额达到阈值且该币种没有进行中的结算单时，自动生成待打款结算单。"""
    min_amount = q2(supplier.settlement_min_amount or 0)
    if min_amount <= 0:
        return []
    created: list[SupplierSettlement] = []
    grouped = (
        pending_bills_qs(supplier)
        .values('currency')
        .annotate(total=Sum('settle_amount'))
    )
    for row in grouped:
        currency = row['currency'] or supplier.settlement_currency or 'CNY'
        total = q2(row['total'] or 0)
        if total < min_amount:
            continue
        if SupplierSettlement.objects.filter(
            supplier=supplier,
            currency=currency,
            status=SupplierSettlement.Status.PENDING,
        ).exists():
            continue
        bills = list(pending_bills_qs(supplier, currency=currency))
        created.append(create_settlement(
            supplier, bills, operator=operator, auto=True, enforce_threshold=True,
        ))
    return created


@transaction.atomic
def request_settlement(supplier: Supplier, *, operator=None, enforce_threshold=True) -> SupplierSettlement:
    if SupplierSettlement.objects.filter(
        supplier=supplier, status=SupplierSettlement.Status.PENDING,
    ).exists():
        raise ValidationError('已有待打款结算单，请等待确认后再申请')
    return create_settlement(
        supplier, operator=operator, enforce_threshold=enforce_threshold,
    )


@transaction.atomic
def mark_settlement_paid(settlement: SupplierSettlement, *, operator=None, remark='') -> SupplierSettlement:
    locked = SupplierSettlement.objects.select_for_update().get(pk=settlement.pk)
    if locked.status == SupplierSettlement.Status.PAID:
        return locked
    if locked.status == SupplierSettlement.Status.CANCELLED:
        raise ValidationError('已取消的结算单不能确认打款')
    locked.status = SupplierSettlement.Status.PAID
    locked.settled_at = timezone.now()
    locked.settled_by = operator
    if remark:
        locked.remark = remark
    locked.save(update_fields=['status', 'settled_at', 'settled_by', 'remark', 'updated_at'])
    SupplierBill.objects.filter(settlement=locked).update(
        status=SupplierBill.Status.SETTLED,
        updated_at=timezone.now(),
    )
    from django.db.models import F
    Supplier.objects.filter(pk=locked.supplier_id).update(
        balance=F('balance') + q2(locked.amount),
    )
    return locked


@transaction.atomic
def cancel_settlement(settlement: SupplierSettlement, *, operator=None) -> SupplierSettlement:
    locked = SupplierSettlement.objects.select_for_update().get(pk=settlement.pk)
    if locked.status == SupplierSettlement.Status.PAID:
        raise ValidationError('已结算单据不能取消')
    if locked.status == SupplierSettlement.Status.CANCELLED:
        return locked
    locked.status = SupplierSettlement.Status.CANCELLED
    locked.save(update_fields=['status', 'updated_at'])
    SupplierBill.objects.filter(settlement=locked).update(
        settlement=None,
        status=SupplierBill.Status.PENDING,
        updated_at=timezone.now(),
    )
    return locked
