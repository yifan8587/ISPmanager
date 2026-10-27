from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from customer_manager.models import BillingItem, CustomerBilling, PaymentTransaction
from customer_manager.channel_services import apply_billing_promotion
from service_manager.models import ServiceOrder


class OrderBillingError(ValueError):
    pass


def order_quote(order: ServiceOrder) -> tuple[Decimal, str]:
    if order.quoted_price is None:
        raise OrderBillingError('工单缺少报价，不能审批')
    amount = Decimal(str(order.quoted_price)).quantize(Decimal('0.01'))
    if amount < 0:
        raise OrderBillingError('工单报价不能为负数')
    if not order.product_id:
        raise OrderBillingError('工单未关联产品，无法确定支付币种')
    currency = str(order.product.currency or 'USD').upper()
    return amount, currency


def order_quote_components(order: ServiceOrder, total_amount: Decimal | None = None) -> tuple[Decimal, Decimal]:
    """返回首期报价中的月费与一次性安装费，兼容历史工单快照。"""
    total_amount = total_amount if total_amount is not None else order_quote(order)[0]
    quote = (order.request_snapshot or {}).get('quote') or {}
    try:
        monthly_total = Decimal(str(quote.get('monthly_total'))).quantize(Decimal('0.01'))
        setup_total = Decimal(str(quote.get('setup_total') or 0)).quantize(Decimal('0.01'))
    except (ArithmeticError, TypeError, ValueError):
        return total_amount, Decimal('0.00')

    # 历史工单或人工改价后，快照明细不再可信时仍以订单报价为准，避免账单金额不一致。
    if monthly_total < 0 or setup_total < 0 or monthly_total + setup_total != total_amount:
        return total_amount, Decimal('0.00')
    return monthly_total, setup_total


@transaction.atomic
def ensure_order_payment_billing(order: ServiceOrder) -> CustomerBilling | None:
    """为已审批工单创建唯一预付账单；零金额无需账单。"""
    locked = ServiceOrder.objects.select_for_update().select_related(
        'customer',
        'product',
    ).get(pk=order.pk)
    from service_manager.fx_snapshot import lock_order_fx
    lock_order_fx(locked)
    amount, currency = order_quote(locked)
    monthly_total, setup_total = order_quote_components(locked, amount)
    if amount == 0:
        return None

    billing = CustomerBilling.objects.select_for_update().filter(
        service_order=locked,
    ).first()
    due_date = timezone.localdate() + datetime.timedelta(days=30)
    if billing is None:
        billing = CustomerBilling(
            customer=locked.customer,
            service_order=locked,
            bill_month=timezone.localdate().strftime('%Y-%m'),
            amount=amount,
            currency=currency,
            auto_generated=False,
            due_date=due_date,
            remark=f'工单 [{locked.order_no}] 审批后预付账单',
        )
        apply_billing_promotion(billing, amount)
        billing.save()
    elif billing.status != CustomerBilling.BillingStatus.PAID:
        if billing.payment_transactions.filter(
            status__in={
                PaymentTransaction.Status.CREATED,
                PaymentTransaction.Status.PENDING,
            },
        ).exists() and (billing.amount != amount or billing.currency != currency):
            raise OrderBillingError('工单账单存在支付中的交易，不能修改报价或币种')
        billing.currency = currency
        billing.due_date = due_date
        apply_billing_promotion(billing, amount)
        billing.save(update_fields=[
            'amount', 'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'channel_referral', 'currency', 'due_date', 'updated_at',
        ])

    BillingItem.objects.update_or_create(
        billing=billing,
        instance_no=locked.order_no,
        defaults={
            'description': (
                f'工单 [{locked.order_no}] {locked.get_business_type_display()} 首期预付'
                + (f'（含安装费 {currency}{setup_total:.2f}）' if setup_total else '')
            ),
            'service_type': locked.business_type,
            'product': locked.product,
            'product_name': locked.product.name if locked.product else '',
            'period_start': locked.service_start,
            'period_end': locked.service_end,
            'days_used': 0,
            'days_in_month': 30,
            'is_prorated': False,
            'is_trial': False,
            'unit_monthly_fee': monthly_total,
            'base_monthly_fee': monthly_total,
            'overage_monthly_fee': Decimal('0.00'),
            'source_currency': currency,
            'exchange_rate': Decimal('1.000000'),
            'amount_original': amount,
            'currency': currency,
            'amount': amount,
            'detail': {
                'billing_kind': 'service_order_prepayment',
                'service_order_id': locked.pk,
                'order_no': locked.order_no,
                'business_type': locked.business_type,
                'billing_cycle': locked.billing_cycle,
                'quoted_price': str(amount),
                'monthly_total': str(monthly_total),
                'setup_total': str(setup_total),
                'fx_quote_currency': locked.fx_quote_currency,
                'fx_locked_at': locked.fx_locked_at.isoformat() if locked.fx_locked_at else '',
            },
        },
    )
    return billing
