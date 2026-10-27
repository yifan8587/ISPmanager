"""渠道推广、优惠、佣金和结算的统一业务逻辑。"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    ChannelCommission,
    ChannelFinanceAccount,
    ChannelFinanceLedger,
    ChannelPayoutProfile,
    ChannelProfile,
    ChannelSettlement,
    Customer,
    CustomerBilling,
    CustomerOperationLog,
    CustomerReferral,
    CustomerStatus,
    CustomerType,
    record_operation_log,
)


_PROMOTION_CODE_RE = re.compile(r'^[A-Z0-9_-]{3,32}$')
_HUNDRED = Decimal('100')
_ZERO = Decimal('0.00')


class ChannelServiceError(ValueError):
    """渠道业务规则校验失败。"""


def q2(value) -> Decimal:
    """统一的金额四舍五入规则。"""
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _commission_snapshot(billing: CustomerBilling) -> dict:
    items = list(billing.items.order_by('id').values(
        'instance_no', 'description', 'service_type', 'product_name',
        'period_start', 'period_end', 'amount', 'currency',
    ))
    for item in items:
        for key in ('period_start', 'period_end'):
            if item[key]:
                item[key] = item[key].isoformat()
        item['amount'] = str(item['amount'])
    payments = list(
        billing.payment_transactions.filter(status='succeeded')
        .order_by('paid_at', 'id')
        .values('transaction_no', 'provider', 'provider_payment_id', 'amount', 'currency', 'paid_at')
    )
    for payment in payments:
        payment['amount'] = str(payment['amount'])
        if payment['paid_at']:
            payment['paid_at'] = payment['paid_at'].isoformat()
    return {
        'service_items_snapshot': items,
        'payment_summary_snapshot': {
            'payment_method': billing.payment_method,
            'paid_amount': str(billing.paid_amount),
            'paid_at': billing.paid_at.isoformat() if billing.paid_at else None,
            'transactions': payments,
        },
    }


@transaction.atomic
def append_finance_ledger(
    *,
    channel: Customer,
    currency: str,
    entry_type: str,
    delta,
    source_type: str,
    source_id='',
    snapshot=None,
    operator=None,
    idempotency_key: str,
) -> ChannelFinanceLedger:
    """锁定账户后追加流水；相同幂等键只返回首笔结果。"""
    existing = ChannelFinanceLedger.objects.select_related('account').filter(
        idempotency_key=idempotency_key,
    ).first()
    if existing:
        return existing
    currency = str(currency or 'CNY').upper()
    account, _ = ChannelFinanceAccount.objects.get_or_create(
        channel=channel,
        currency=currency,
        defaults={'balance': _ZERO},
    )
    account = ChannelFinanceAccount.objects.select_for_update().get(pk=account.pk)
    existing = ChannelFinanceLedger.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    delta = q2(delta)
    balance_after = q2(account.balance + delta)
    try:
        with transaction.atomic():
            ledger = ChannelFinanceLedger.objects.create(
                account=account,
                entry_type=entry_type,
                delta=delta,
                balance_after=balance_after,
                source_type=source_type,
                source_id=str(source_id or ''),
                snapshot=snapshot or {},
                operator=operator,
                idempotency_key=idempotency_key,
            )
    except IntegrityError:
        return ChannelFinanceLedger.objects.get(idempotency_key=idempotency_key)
    account.balance = balance_after
    account.save(update_fields=['balance', 'updated_at'])
    return ledger


@transaction.atomic
def adjust_channel_balance(
    *,
    channel: Customer,
    currency: str,
    delta,
    reason: str,
    reference: str,
    evidence: str,
    operator=None,
    idempotency_key: str,
) -> ChannelFinanceLedger:
    amount = q2(delta)
    if amount == 0:
        raise ChannelServiceError('调整金额不能为零')
    if not str(reason or '').strip():
        raise ChannelServiceError('请填写调整原因')
    if not str(reference or '').strip() or not str(evidence or '').strip():
        raise ChannelServiceError('请填写调整依据编号和凭证说明')
    if not str(idempotency_key or '').strip():
        raise ChannelServiceError('请提供幂等键')
    return append_finance_ledger(
        channel=channel,
        currency=currency,
        entry_type=ChannelFinanceLedger.EntryType.ADJUSTMENT,
        delta=amount,
        source_type='manual_adjustment',
        snapshot={
            'reason': str(reason).strip(),
            'reference': str(reference).strip(),
            'evidence': str(evidence).strip(),
        },
        operator=operator,
        idempotency_key=f'adjustment:{idempotency_key.strip()}',
    )


def normalize_promotion_code(value: str) -> str:
    code = str(value or '').strip().upper()
    if not code:
        return ''
    if not _PROMOTION_CODE_RE.fullmatch(code):
        raise ChannelServiceError('推广码仅支持 3–32 位大写字母、数字、下划线或短横线')
    return code


def _default_promotion_code(customer: Customer) -> str:
    return f'CH{customer.pk:08d}'


def resolve_active_promotion(promotion_code: str, *, lock: bool = False) -> ChannelProfile:
    """解析当前可用于注册的渠道推广码。"""
    code = normalize_promotion_code(promotion_code)
    if not code:
        raise ChannelServiceError('请输入推广优惠码')
    queryset = ChannelProfile.objects.select_related('customer')
    if lock:
        queryset = queryset.select_for_update()
    profile = queryset.filter(promotion_code=code, is_active=True).first()
    if profile is None:
        raise ChannelServiceError('推广优惠码不存在或已停用')
    if (
        profile.customer.customer_type != CustomerType.CHANNEL
        or profile.customer.status != CustomerStatus.ACTIVE
    ):
        raise ChannelServiceError('该推广渠道当前不可用')
    return profile


@transaction.atomic
def configure_channel_profile(
    customer: Customer,
    *,
    promotion_code: str | None = None,
    promotion_discount_rate: Decimal | str | int | None = None,
    commission_rate: Decimal | str | int | None = None,
    is_active: bool | None = None,
    remark: str | None = None,
    reputation_grant_mode: str | None = None,
    reputation_grant_amount=None,
) -> ChannelProfile:
    """创建或更新渠道规则；未传入的字段保持原值。"""
    if customer.customer_type != CustomerType.CHANNEL:
        raise ChannelServiceError('只有渠道客户可以配置推广规则')

    profile = ChannelProfile.objects.select_for_update().filter(customer=customer).first()
    if profile is None:
        profile = ChannelProfile(
            customer=customer,
            promotion_code=normalize_promotion_code(promotion_code or _default_promotion_code(customer)),
        )
    elif promotion_code is not None:
        normalized_code = normalize_promotion_code(promotion_code)
        if normalized_code:
            profile.promotion_code = normalized_code

    for field, value in (
        ('promotion_discount_rate', promotion_discount_rate),
        ('commission_rate', commission_rate),
    ):
        if value is None:
            continue
        try:
            decimal_value = Decimal(str(value)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ChannelServiceError('优惠比例和提成比例必须是有效数字') from exc
        if decimal_value < 0 or decimal_value > _HUNDRED:
            raise ChannelServiceError('优惠比例和提成比例必须介于 0 与 100 之间')
        setattr(profile, field, decimal_value)

    if is_active is not None:
        profile.is_active = bool(is_active)
    if remark is not None:
        profile.remark = str(remark).strip()
    if reputation_grant_mode is not None:
        if reputation_grant_mode not in Customer.ReputationMode.values:
            raise ChannelServiceError('无效的注册授信模式')
        profile.reputation_grant_mode = reputation_grant_mode
    if reputation_grant_amount is not None:
        try:
            profile.reputation_grant_amount = Decimal(str(reputation_grant_amount)).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ChannelServiceError('注册授信额度必须是有效数字') from exc
        if profile.reputation_grant_amount < 0:
            raise ChannelServiceError('注册授信额度不能为负')

    profile.full_clean()
    profile.save()
    return profile


@transaction.atomic
def bind_customer_referral(
    customer: Customer,
    promotion_code: str,
    *,
    bound_by=None,
) -> CustomerReferral:
    """将客户一次性绑定到渠道，并固化优惠与提成比例快照。"""
    profile = resolve_active_promotion(promotion_code, lock=True)
    if customer.pk == profile.customer_id:
        raise ChannelServiceError('不能使用自己的推广码')

    existing = CustomerReferral.objects.select_for_update().filter(customer=customer).first()
    if existing:
        if existing.channel_id == profile.customer_id and existing.promotion_code == profile.promotion_code:
            return existing
        raise ChannelServiceError('该客户已绑定推广渠道，不能重复或更换归属')

    referral = CustomerReferral.objects.create(
        customer=customer,
        channel=profile.customer,
        promotion_code=profile.promotion_code,
        promotion_discount_rate=profile.promotion_discount_rate,
        commission_rate=profile.commission_rate,
        bound_by=bound_by,
    )
    from .reputation import grant_from_channel
    grant_from_channel(customer, profile)
    record_operation_log(
        operator=bound_by,
        action_type=CustomerOperationLog.ActionType.CREATE,
        description=(
            f'客户使用推广码 {profile.promotion_code} 注册，'
            f'归属渠道 [{profile.customer.customer_no}] {profile.customer.name}'
        ),
        customer=customer,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_REFERRAL,
        target_id=referral.pk,
        new_value={
            'channel_id': profile.customer_id,
            'promotion_code': profile.promotion_code,
            'promotion_discount_rate': str(profile.promotion_discount_rate),
            'commission_rate': str(profile.commission_rate),
        },
    )
    return referral


def customer_promotion_quote(customer: Customer, gross_amount) -> dict:
    """按客户已固化的推荐关系计算优惠；渠道规则变更不影响已绑定客户。"""
    gross_amount = q2(gross_amount)
    referral = getattr(customer, 'channel_referral', None)
    if referral is None and customer.pk:
        referral = CustomerReferral.objects.select_related('channel').filter(customer=customer).first()

    if referral is None or gross_amount <= 0:
        return {
            'gross_amount': gross_amount,
            'discount_amount': _ZERO,
            'amount': gross_amount,
            'promotion_code': '',
            'referral': None,
        }

    rate = Decimal(str(referral.promotion_discount_rate or 0))
    discount_amount = q2(gross_amount * rate / _HUNDRED)
    return {
        'gross_amount': gross_amount,
        'discount_amount': discount_amount,
        'amount': q2(gross_amount - discount_amount),
        'promotion_code': referral.promotion_code,
        'referral': referral,
    }


def apply_billing_promotion(billing: CustomerBilling, gross_amount=None) -> dict:
    """把当前客户的优惠快照写入账单，并将 amount 设为优惠后的应付金额。"""
    if gross_amount is None:
        gross_amount = billing.gross_amount or billing.amount
    quote = customer_promotion_quote(billing.customer, gross_amount)
    billing.gross_amount = quote['gross_amount']
    billing.promotion_discount_amount = quote['discount_amount']
    billing.promotion_code = quote['promotion_code']
    billing.channel_referral = quote['referral']
    billing.amount = quote['amount']
    return quote


@transaction.atomic
def ensure_paid_billing_commission(
    billing: CustomerBilling,
    *,
    operator=None,
    request=None,
) -> ChannelCommission | None:
    """账单全部支付后创建一次佣金明细。幂等调用安全。"""
    locked_billing = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer', 'channel_referral', 'channel_referral__channel')
        .get(pk=billing.pk)
    )
    if locked_billing.status != CustomerBilling.BillingStatus.PAID:
        return None
    if locked_billing.billing_kind == CustomerBilling.BillingKind.WALLET_RECHARGE:
        return None

    existing = ChannelCommission.objects.filter(billing=locked_billing).first()
    if existing:
        return existing

    referral = locked_billing.channel_referral
    if referral is None:
        referral = (
            CustomerReferral.objects.select_related('channel')
            .filter(customer=locked_billing.customer)
            .first()
        )
    if referral is None:
        return None

    rate = Decimal(str(referral.commission_rate or 0))
    commission_base_amount = q2(locked_billing.amount)
    commission_amount = q2(commission_base_amount * rate / _HUNDRED)
    if commission_base_amount <= 0 or commission_amount <= 0:
        return None

    snapshot = _commission_snapshot(locked_billing)
    try:
        with transaction.atomic():
            commission = ChannelCommission.objects.create(
                channel=referral.channel,
                referral=referral,
                customer=locked_billing.customer,
                billing=locked_billing,
                customer_no=locked_billing.customer.customer_no,
                customer_name=locked_billing.customer.name,
                bill_no=locked_billing.bill_no,
                gross_amount=locked_billing.gross_amount,
                promotion_discount_amount=locked_billing.promotion_discount_amount,
                billing_amount=locked_billing.amount,
                billing_period=locked_billing.bill_month,
                billing_paid_at=locked_billing.paid_at,
                service_items_snapshot=snapshot['service_items_snapshot'],
                payment_summary_snapshot=snapshot['payment_summary_snapshot'],
                promotion_code=referral.promotion_code,
                commission_base_amount=commission_base_amount,
                commission_rate=rate,
                commission_amount=commission_amount,
                currency=locked_billing.currency,
            )
    except IntegrityError:
        return ChannelCommission.objects.get(billing=locked_billing)
    append_finance_ledger(
        channel=referral.channel,
        currency=locked_billing.currency,
        entry_type=ChannelFinanceLedger.EntryType.ACCRUAL,
        delta=commission_amount,
        source_type='channel_commission',
        source_id=commission.pk,
        snapshot={
            'commission_id': commission.pk,
            'billing_id': locked_billing.pk,
            'bill_no': locked_billing.bill_no,
            'customer_id': locked_billing.customer_id,
            'amount': str(commission_amount),
        },
        operator=operator,
        idempotency_key=f'commission:{commission.pk}:accrual',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.CREATE,
        description=(
            f'推荐客户账单 [{locked_billing.bill_no}] 已支付，'
            f'产生渠道佣金 {locked_billing.currency}{commission_amount}'
        ),
        customer=referral.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_COMMISSION,
        target_id=commission.pk,
        new_value={
            'billing_id': locked_billing.pk,
            'bill_no': locked_billing.bill_no,
            'referred_customer_id': locked_billing.customer_id,
            'commission_base_amount': str(commission_base_amount),
            'commission_rate': str(rate),
            'commission_amount': str(commission_amount),
            'currency': locked_billing.currency,
        },
        request=request,
    )
    return commission


@transaction.atomic
def create_channel_settlement(
    commission_ids: Iterable[int],
    *,
    operator=None,
    remark: str = '',
) -> ChannelSettlement:
    """将同一渠道、同一币种的待结算佣金锁定到一个结算批次。"""
    ids = sorted({int(item) for item in commission_ids if item})
    if not ids:
        raise ChannelServiceError('请至少选择一笔待结算佣金')

    commissions = list(
        ChannelCommission.objects.select_for_update()
        .select_related('channel')
        .filter(id__in=ids)
        .order_by('earned_at')
    )
    if len(commissions) != len(ids):
        raise ChannelServiceError('部分佣金记录不存在')
    settlement_ids = {item.settlement_id for item in commissions}
    if (
        all(item.status == ChannelCommission.Status.SETTLING for item in commissions)
        and len(settlement_ids) == 1
        and None not in settlement_ids
    ):
        return ChannelSettlement.objects.select_for_update().get(pk=settlement_ids.pop())
    if any(item.status != ChannelCommission.Status.PENDING for item in commissions):
        raise ChannelServiceError('仅可结算“待结算”状态的佣金')

    channel_ids = {item.channel_id for item in commissions}
    currencies = {item.currency for item in commissions}
    if len(channel_ids) != 1 or len(currencies) != 1:
        raise ChannelServiceError('一次结算只能选择同一渠道、同一币种的佣金')

    payout_profile = ChannelPayoutProfile.objects.filter(
        channel_id=commissions[0].channel_id,
        is_active=True,
    ).first()
    settlement = ChannelSettlement.objects.create(
        channel=commissions[0].channel,
        channel_no=commissions[0].channel.customer_no,
        channel_name=commissions[0].channel.name,
        currency=commissions[0].currency,
        total_amount=q2(sum((item.commission_amount for item in commissions), _ZERO)),
        commission_count=len(commissions),
        period_start=min(item.earned_at.date() for item in commissions),
        period_end=max(item.earned_at.date() for item in commissions),
        payout_profile_snapshot=payout_profile.snapshot() if payout_profile else {},
        remark=str(remark or '').strip(),
        created_by=operator,
    )
    ChannelCommission.objects.filter(id__in=ids).update(
        status=ChannelCommission.Status.SETTLING,
        settlement=settlement,
        updated_at=timezone.now(),
    )
    append_finance_ledger(
        channel=settlement.channel,
        currency=settlement.currency,
        entry_type=ChannelFinanceLedger.EntryType.SETTLEMENT_CREATED,
        delta=_ZERO,
        source_type='channel_settlement',
        source_id=settlement.pk,
        snapshot={
            'settlement_no': settlement.settlement_no,
            'commission_ids': ids,
            'total_amount': str(settlement.total_amount),
            'payout_profile': settlement.payout_profile_snapshot,
        },
        operator=operator,
        idempotency_key=f'settlement:{settlement.pk}:created',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.CREATE,
        description=(
            f'创建渠道佣金结算单 [{settlement.settlement_no}]，'
            f'{settlement.currency}{settlement.total_amount}，共 {len(commissions)} 笔'
        ),
        customer=settlement.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_SETTLEMENT,
        target_id=settlement.pk,
        new_value={
            'commission_ids': ids,
            'currency': settlement.currency,
            'total_amount': str(settlement.total_amount),
        },
    )
    return settlement


@transaction.atomic
def confirm_channel_settlement(settlement: ChannelSettlement, *, operator=None) -> ChannelSettlement:
    locked = ChannelSettlement.objects.select_for_update().get(pk=settlement.pk)
    if locked.status == ChannelSettlement.Status.CONFIRMED:
        return locked
    if locked.status != ChannelSettlement.Status.DRAFT:
        raise ChannelServiceError('只有草稿结算单可以确认')
    locked.status = ChannelSettlement.Status.CONFIRMED
    locked.confirmed_by = operator
    locked.confirmed_at = timezone.now()
    locked.save(update_fields=['status', 'confirmed_by', 'confirmed_at', 'updated_at'])
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.UPDATE,
        description=f'确认渠道佣金结算单 [{locked.settlement_no}]',
        customer=locked.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_SETTLEMENT,
        target_id=locked.pk,
        new_value={'status': locked.status},
    )
    return locked


@transaction.atomic
def pay_channel_settlement(
    settlement: ChannelSettlement,
    *,
    operator=None,
    payment_method: str = '',
    payment_reference: str = '',
) -> ChannelSettlement:
    locked = ChannelSettlement.objects.select_for_update().get(pk=settlement.pk)
    payment_method = str(payment_method or '').strip()
    payment_reference = str(payment_reference or '').strip()
    if locked.status == ChannelSettlement.Status.PAID:
        if (
            locked.payment_method == payment_method
            and locked.payment_reference == payment_reference
        ):
            return locked
        raise ChannelServiceError('结算单已打款，打款信息不一致')
    if locked.status not in {ChannelSettlement.Status.DRAFT, ChannelSettlement.Status.CONFIRMED}:
        raise ChannelServiceError('当前结算单不能登记打款')
    if not payment_method or not payment_reference:
        raise ChannelServiceError('打款方式和打款流水号均为必填')
    commissions = ChannelCommission.objects.select_for_update().filter(
        settlement=locked,
        status=ChannelCommission.Status.SETTLING,
    )
    if commissions.count() != locked.commission_count:
        raise ChannelServiceError('结算单佣金状态异常，请先核对明细')

    now = timezone.now()
    locked.status = ChannelSettlement.Status.PAID
    locked.payment_method = payment_method
    locked.payment_reference = payment_reference
    locked.paid_by = operator
    locked.paid_at = now
    locked.save(update_fields=[
        'status', 'payment_method', 'payment_reference', 'paid_by', 'paid_at', 'updated_at',
    ])
    commissions.update(
        status=ChannelCommission.Status.PAID,
        settled_at=now,
        updated_at=now,
    )
    append_finance_ledger(
        channel=locked.channel,
        currency=locked.currency,
        entry_type=ChannelFinanceLedger.EntryType.PAYOUT,
        delta=-locked.total_amount,
        source_type='channel_settlement',
        source_id=locked.pk,
        snapshot={
            'settlement_no': locked.settlement_no,
            'payment_method': locked.payment_method,
            'payment_reference': locked.payment_reference,
            'total_amount': str(locked.total_amount),
        },
        operator=operator,
        idempotency_key=f'settlement:{locked.pk}:payout',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.PAYMENT,
        description=(
            f'渠道佣金结算单 [{locked.settlement_no}] 已打款，'
            f'{locked.currency}{locked.total_amount}'
        ),
        customer=locked.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_SETTLEMENT,
        target_id=locked.pk,
        new_value={
            'status': locked.status,
            'payment_method': locked.payment_method,
            'payment_reference': locked.payment_reference,
            'total_amount': str(locked.total_amount),
        },
    )
    return locked


@transaction.atomic
def cancel_channel_settlement(
    settlement: ChannelSettlement,
    *,
    operator=None,
    reason: str = '',
) -> ChannelSettlement:
    locked = ChannelSettlement.objects.select_for_update().get(pk=settlement.pk)
    if locked.status == ChannelSettlement.Status.PAID:
        raise ChannelServiceError('已打款的结算单不能取消，请通过财务调整处理')
    if locked.status == ChannelSettlement.Status.CANCELLED:
        return locked

    ChannelCommission.objects.select_for_update().filter(
        settlement=locked,
        status=ChannelCommission.Status.SETTLING,
    ).update(
        settlement=None,
        status=ChannelCommission.Status.PENDING,
        updated_at=timezone.now(),
    )
    locked.status = ChannelSettlement.Status.CANCELLED
    if reason:
        locked.remark = f'{locked.remark}\n取消原因：{reason}'.strip()
    locked.save(update_fields=['status', 'remark', 'updated_at'])
    append_finance_ledger(
        channel=locked.channel,
        currency=locked.currency,
        entry_type=ChannelFinanceLedger.EntryType.SETTLEMENT_CANCELLED,
        delta=_ZERO,
        source_type='channel_settlement',
        source_id=locked.pk,
        snapshot={
            'settlement_no': locked.settlement_no,
            'total_amount': str(locked.total_amount),
            'reason': str(reason or '').strip(),
        },
        operator=operator,
        idempotency_key=f'settlement:{locked.pk}:cancelled',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.UPDATE,
        description=f'取消渠道佣金结算单 [{locked.settlement_no}]，佣金退回待结算',
        customer=locked.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_SETTLEMENT,
        target_id=locked.pk,
        new_value={'status': locked.status, 'reason': str(reason or '').strip()},
    )
    return locked


@transaction.atomic
def void_channel_commission(
    commission: ChannelCommission,
    *,
    operator=None,
    reason: str,
) -> ChannelCommission:
    locked = ChannelCommission.objects.select_for_update().get(pk=commission.pk)
    clean_reason = str(reason or '').strip()
    if locked.status == ChannelCommission.Status.VOID:
        if locked.void_reason == clean_reason:
            return locked
        raise ChannelServiceError('佣金已作废，作废原因不一致')
    if locked.status != ChannelCommission.Status.PENDING:
        raise ChannelServiceError('只有待结算佣金可以作废')
    if not clean_reason:
        raise ChannelServiceError('请填写作废原因')
    locked.status = ChannelCommission.Status.VOID
    locked.void_reason = clean_reason
    locked.save(update_fields=['status', 'void_reason', 'updated_at'])
    append_finance_ledger(
        channel=locked.channel,
        currency=locked.currency,
        entry_type=ChannelFinanceLedger.EntryType.VOID,
        delta=-locked.commission_amount,
        source_type='channel_commission',
        source_id=locked.pk,
        snapshot={
            'commission_id': locked.pk,
            'bill_no': locked.bill_no,
            'reason': locked.void_reason,
            'amount': str(locked.commission_amount),
        },
        operator=operator,
        idempotency_key=f'commission:{locked.pk}:void',
    )
    record_operation_log(
        operator=operator,
        action_type=CustomerOperationLog.ActionType.UPDATE,
        description=f'作废渠道佣金 [{locked.bill_no}]：{locked.void_reason}',
        customer=locked.channel,
        target_model=CustomerOperationLog.TargetModel.CHANNEL_COMMISSION,
        target_id=locked.pk,
        new_value={'status': locked.status, 'void_reason': locked.void_reason},
    )
    return locked
