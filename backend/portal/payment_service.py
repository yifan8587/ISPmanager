"""portal/payment_service.py — 客户门户线下收款兼容工具。

真实线上支付已统一迁移至 ``customer_manager.payment_service``，由持久化支付
交易、服务商验签回调和原子入账保证安全；本模块保留线下收款兼容逻辑。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from customer_manager.models import CustomerBilling, CustomerOperationLog, record_operation_log

ONLINE_METHODS = frozenset({'wechat', 'alipay', 'paypal', 'stripe', 'airwallex', 'unionpay'})
OFFLINE_METHODS = frozenset({'cash', 'bank_transfer'})
ALLOWED_METHODS = ONLINE_METHODS | OFFLINE_METHODS

METHOD_LABELS = {
    'cash': '现金',
    'bank_transfer': '银行转账',
    'wechat': '微信支付',
    'alipay': '支付宝',
    'paypal': 'PayPal',
    'stripe': 'Stripe',
    'airwallex': 'Airwallex',
    'unionpay': '银联在线支付',
}

def method_label(method: str) -> str:
    return METHOD_LABELS.get(method, method)


def outstanding_amount(billing: CustomerBilling) -> Decimal:
    return billing.amount - billing.paid_amount


def validate_bill_payable(billing: CustomerBilling) -> Decimal:
    if billing.status == CustomerBilling.BillingStatus.CANCELLED:
        raise ValueError('账单已取消，无法支付')
    if billing.status in {
        CustomerBilling.BillingStatus.REFUNDING,
        CustomerBilling.BillingStatus.REFUNDED,
    }:
        raise ValueError('账单已退款或正在退款，无法支付')
    if billing.status == CustomerBilling.BillingStatus.PAID:
        raise ValueError('账单已支付')
    owe = outstanding_amount(billing)
    if owe <= 0:
        raise ValueError('暂无待付金额')
    return owe


@transaction.atomic
def apply_bill_payment(
    billing: CustomerBilling,
    pay_amount: Decimal,
    payment_method: str,
    operator,
    *,
    request=None,
    remark: str = '',
) -> CustomerBilling:
    billing = CustomerBilling.objects.select_for_update().select_related(
        'customer',
    ).get(pk=billing.pk)
    if billing.status in {
        CustomerBilling.BillingStatus.CANCELLED,
        CustomerBilling.BillingStatus.REFUNDING,
        CustomerBilling.BillingStatus.REFUNDED,
    }:
        raise ValueError('账单已取消或已退款，无法支付')
    owe = outstanding_amount(billing)
    if pay_amount <= 0:
        raise ValueError('请输入有效的支付金额')
    if pay_amount > owe:
        raise ValueError(f'支付金额不能超过待付金额 {billing.currency}{owe:.2f}')

    old_paid = billing.paid_amount
    old_status = billing.status

    billing.paid_amount = billing.paid_amount + pay_amount
    billing.payment_method = payment_method
    billing.paid_at = timezone.now()
    if billing.paid_amount >= billing.amount:
        billing.status = CustomerBilling.BillingStatus.PAID
    if remark:
        billing.remark = remark
    billing.save(update_fields=[
        'paid_amount', 'payment_method', 'paid_at', 'status', 'remark', 'updated_at',
    ])

    commission = None
    if (
        old_status != CustomerBilling.BillingStatus.PAID
        and billing.status == CustomerBilling.BillingStatus.PAID
    ):
        from customer_manager.channel_services import ensure_paid_billing_commission

        from customer_manager.wallet import credit_paid_recharge

        credit_paid_recharge(billing, operator=operator)
        commission = ensure_paid_billing_commission(
            billing,
            operator=operator,
            request=request,
        )

    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=(
            f'客户门户支付 {billing.currency}{pay_amount:,.2f}（{method_label(payment_method)}），'
            f'账单 [{billing.bill_no}]'
        ),
        customer=billing.customer,
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=billing.pk,
        old_value={'paid_amount': str(old_paid), 'status': old_status},
        new_value={
            'paid_amount': str(billing.paid_amount),
            'status': billing.status,
            'channel_commission_id': commission.pk if commission else None,
        },
        request=request,
    )
    if (
        old_status != CustomerBilling.BillingStatus.PAID
        and billing.status == CustomerBilling.BillingStatus.PAID
        and billing.service_order_id
    ):
        from service_manager.payment_fulfillment import fulfill_paid_billing_safely

        transaction.on_commit(
            lambda billing_id=billing.pk: fulfill_paid_billing_safely(billing_id),
        )
    return billing


def create_online_payment_session(billing: CustomerBilling, payment_method: str) -> dict:
    """阻止旧模拟支付调用，避免任何代码通过缓存会话伪造线上收款。"""
    raise ValueError(
        '旧在线支付模拟会话已移除，请使用 customer_manager.payment_service 创建真实支付交易',
    )


def confirm_online_payment_session(
    session_id: str,
    billing: CustomerBilling,
    operator,
    *,
    request=None,
) -> CustomerBilling:
    """阻止浏览器确认模拟支付，线上入账仅接受服务商验签回调。"""
    raise ValueError(
        '线上支付不可由客户端确认，请等待服务商验签回调或查询真实支付交易状态',
    )


def parse_pay_amount(raw, billing: CustomerBilling) -> Decimal:
    if raw in (None, ''):
        return outstanding_amount(billing)
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('请输入有效的支付金额') from None
    return amount
