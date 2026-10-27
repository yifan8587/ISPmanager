"""客户信誉值：一次性额度或按月刷新，用于产品信誉支付。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import Customer, CustomerBilling, CustomerOperationLog, record_operation_log

_ZERO = Decimal('0.00')
_CENT = Decimal('0.01')


class ReputationError(ValueError):
    """信誉值规则校验失败。"""


def q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def current_period() -> str:
    return timezone.now().strftime('%Y-%m')


def refresh_reputation(customer: Customer) -> Customer:
    """按月模式在新账期把余额重置为额度。"""
    if customer.reputation_mode != Customer.ReputationMode.MONTHLY:
        return customer
    period = current_period()
    if customer.reputation_period == period:
        return customer
    customer.reputation_period = period
    customer.reputation_balance = q2(customer.reputation_quota)
    customer.save(update_fields=['reputation_period', 'reputation_balance', 'updated_at'])
    return customer


def reputation_enabled(customer: Customer | None) -> bool:
    if customer is None:
        return False
    refresh_reputation(customer)
    return (
        customer.reputation_mode != Customer.ReputationMode.NONE
        and (q2(customer.reputation_quota) > _ZERO or q2(customer.reputation_balance) > _ZERO)
    )


def apply_reputation_settings(
    customer: Customer,
    *,
    mode: str | None = None,
    quota=None,
    balance=None,
) -> Customer:
    """管理端写入信誉值模式与额度。"""
    prev_mode = customer.reputation_mode
    if mode is not None:
        if mode not in Customer.ReputationMode.values:
            raise ReputationError('无效的信誉值模式')
        customer.reputation_mode = mode
    if quota is not None:
        customer.reputation_quota = q2(quota)
    if customer.reputation_mode == Customer.ReputationMode.NONE:
        customer.reputation_quota = _ZERO
        customer.reputation_balance = _ZERO
        customer.reputation_period = ''
    else:
        if q2(customer.reputation_quota) < _ZERO:
            raise ReputationError('信誉值额度不能为负')
        enabling = prev_mode == Customer.ReputationMode.NONE
        if balance is not None:
            customer.reputation_balance = q2(balance)
        elif customer.reputation_mode == Customer.ReputationMode.MONTHLY:
            customer.reputation_period = current_period()
            customer.reputation_balance = q2(customer.reputation_quota)
        elif q2(customer.reputation_balance) == _ZERO and q2(customer.reputation_quota) > _ZERO:
            customer.reputation_balance = q2(customer.reputation_quota)
        # 后台表单开通时余额常默认 0；从关闭切到开通则按额度入账，客户才能实际支付。
        if enabling and q2(customer.reputation_balance) == _ZERO and q2(customer.reputation_quota) > _ZERO:
            customer.reputation_balance = q2(customer.reputation_quota)
        if q2(customer.reputation_balance) < _ZERO:
            raise ReputationError('信誉值余额不能为负')
        if customer.reputation_mode == Customer.ReputationMode.MONTHLY and not customer.reputation_period:
            customer.reputation_period = current_period()
    customer.save(update_fields=[
        'reputation_mode', 'reputation_quota', 'reputation_balance',
        'reputation_period', 'updated_at',
    ])
    return customer


def grant_from_channel(customer: Customer, channel_profile) -> Customer:
    """注册绑定渠道优惠码时，按渠道规则授予信誉值。"""
    mode = getattr(channel_profile, 'reputation_grant_mode', Customer.ReputationMode.NONE) or Customer.ReputationMode.NONE
    amount = q2(getattr(channel_profile, 'reputation_grant_amount', 0))
    if mode == Customer.ReputationMode.NONE or amount <= _ZERO:
        return customer
    return apply_reputation_settings(customer, mode=mode, quota=amount, balance=amount)


def product_allows_reputation(product) -> bool:
    if product is None:
        return False
    return bool(getattr(product, 'allow_reputation_pay', False)) and q2(getattr(product, 'reputation_price', 0)) > _ZERO


def resolve_billing_product(billing):
    """从账单明细解析可信誉支付的关联产品。"""
    if billing is None:
        return None
    items = getattr(billing, 'items', None)
    if items is None:
        return None
    queryset = items.all()
    if hasattr(queryset, 'select_related'):
        item = queryset.select_related('product').filter(product_id__isnull=False).first()
    else:
        item = next((row for row in queryset if getattr(row, 'product_id', None)), None)
    return getattr(item, 'product', None) if item is not None else None


def portal_can_see_reputation(customer, product=None) -> bool:
    if not reputation_enabled(customer):
        return False
    if product is None:
        return True
    return bool(getattr(product, 'allow_reputation_pay', False)) and q2(getattr(product, 'reputation_price', 0)) > _ZERO


def required_reputation(product, quantity=1, custom_spec=None) -> Decimal:
    from product_manager.services import compute_price

    price = compute_price(product, custom_spec)
    unit = q2(price.get('reputation_price'))
    qty = max(1, int(quantity or 1))
    return q2(unit * qty)


def can_pay_with_reputation(customer, product, *, quantity=1, custom_spec=None) -> bool:
    if not portal_can_see_reputation(customer, product):
        return False
    needed = required_reputation(product, quantity, custom_spec)
    return needed > _ZERO and q2(customer.reputation_balance) >= needed


def build_reputation_payment_method(customer, product, *, quantity=1, custom_spec=None, currency='USD') -> dict | None:
    """产品/账单可选的信誉值支付方式；余额不足时仍返回，供前端展示原因。"""
    if not portal_can_see_reputation(customer, product):
        return None
    refresh_reputation(customer)
    needed = required_reputation(product, quantity, custom_spec)
    balance = q2(customer.reputation_balance)
    available = needed > _ZERO and balance >= needed
    if needed <= _ZERO:
        reason = '该产品未配置信誉值价格'
    elif balance <= _ZERO:
        reason = '信誉值余额为 0，请联系管理员充值'
    elif not available:
        reason = f'余额不足，当前 {balance}，需要 {needed}'
    else:
        reason = ''
    return {
        'value': 'reputation',
        'channel_id': 'reputation',
        'label': '信誉值支付',
        'provider_label': '信誉值',
        'online': True,
        'reputation': True,
        'available': available,
        'needed': str(needed),
        'balance': str(balance),
        'disabled_reason': reason,
        'sandbox': False,
        'allowed_currencies': [currency],
    }


def serialize_reputation(customer: Customer | None) -> dict:
    if customer is None:
        return {
            'reputation_visible': False,
            'reputation_mode': Customer.ReputationMode.NONE,
            'reputation_quota': '0.00',
            'reputation_balance': '0.00',
        }
    refresh_reputation(customer)
    visible = reputation_enabled(customer)
    return {
        'reputation_visible': visible,
        'reputation_mode': customer.reputation_mode,
        'reputation_mode_label': customer.get_reputation_mode_display(),
        'reputation_quota': str(q2(customer.reputation_quota)),
        'reputation_balance': str(q2(customer.reputation_balance)),
    }


@transaction.atomic
def consume_reputation(
    customer: Customer,
    amount,
    *,
    remark: str = '',
    operator=None,
    related_billing: CustomerBilling | None = None,
    request=None,
) -> Customer:
    amount = q2(amount)
    if amount <= _ZERO:
        raise ReputationError('扣减信誉值必须大于 0')
    locked = Customer.objects.select_for_update().get(pk=customer.pk)
    refresh_reputation(locked)
    locked = Customer.objects.select_for_update().get(pk=locked.pk)
    if locked.reputation_mode == Customer.ReputationMode.NONE:
        raise ReputationError('当前账号未开通信誉值支付')
    available = q2(locked.reputation_balance)
    if available < amount:
        raise ReputationError(f'信誉值不足，当前余额 {available}，需要 {amount}')
    locked.reputation_balance = q2(available - amount)
    locked.save(update_fields=['reputation_balance', 'updated_at'])
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=remark or f'扣减信誉值 {amount}',
        customer=locked,
        target_model=CustomerOperationLog.TargetModel.BILLING if related_billing else CustomerOperationLog.TargetModel.CUSTOMER,
        target_id=related_billing.pk if related_billing else locked.pk,
        old_value={'reputation_balance': str(available)},
        new_value={'reputation_balance': str(locked.reputation_balance)},
        request=request,
    )
    return locked


@transaction.atomic
def pay_billing_with_reputation(
    billing: CustomerBilling,
    *,
    needed,
    operator=None,
    request=None,
) -> CustomerBilling:
    """用信誉值结清账单并进入既有履约。"""
    locked = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer')
        .get(pk=billing.pk)
    )
    if locked.status == CustomerBilling.BillingStatus.PAID:
        return locked
    if locked.status in {
        CustomerBilling.BillingStatus.CANCELLED,
        CustomerBilling.BillingStatus.REFUNDING,
        CustomerBilling.BillingStatus.REFUNDED,
    }:
        raise ReputationError('账单已取消或已退款，无法支付')

    consume_reputation(
        locked.customer,
        needed,
        remark=f'信誉值支付账单 {locked.bill_no}（{q2(needed)}）',
        operator=operator,
        related_billing=locked,
        request=request,
    )
    locked.paid_amount = q2(locked.amount)
    locked.payment_method = CustomerBilling.PaymentMethod.REPUTATION
    locked.paid_at = timezone.now()
    locked.status = CustomerBilling.BillingStatus.PAID
    locked.save(update_fields=[
        'paid_amount', 'payment_method', 'paid_at', 'status', 'updated_at',
    ])
    return locked
