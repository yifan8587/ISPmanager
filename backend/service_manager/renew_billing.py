"""门户一键续费：按当前规格 × 账期出账，支付后延长到期日。"""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from customer_manager.channel_services import apply_billing_promotion
from customer_manager.models import BillingItem, CustomerBilling, PaymentTransaction
from service_manager.expiry_engine import apply_schedule_to_instance
from service_manager.models import (
    ServiceAllocation,
    ServiceInstance,
    ServiceOperationLog,
    ServiceOrder,
    _log_service,
    service_snapshot,
)
from service_manager.notify_helpers import notify_order_event, notify_service_event
from service_manager.fx_snapshot import build_fx_fields, lock_order_fx
from service_manager.payment_billing import OrderBillingError

MONEY = Decimal('0.01')

CYCLE_MONTHS = {
    ServiceOrder.BillingCycle.MONTHLY: 1,
    ServiceOrder.BillingCycle.QUARTERLY: 3,
    ServiceOrder.BillingCycle.SEMI_ANNUAL: 6,
    ServiceOrder.BillingCycle.ANNUAL: 12,
}

CYCLE_ALIASES = {
    'month': ServiceOrder.BillingCycle.MONTHLY,
    'quarter': ServiceOrder.BillingCycle.QUARTERLY,
    'year': ServiceOrder.BillingCycle.ANNUAL,
    'yearly': ServiceOrder.BillingCycle.ANNUAL,
}

RENEWABLE_STATUSES = {
    ServiceInstance.InstanceStatus.ACTIVE,
    ServiceInstance.InstanceStatus.SUSPENDED,
    ServiceInstance.InstanceStatus.EXPIRED,
}


class RenewBillingError(ValueError):
    pass


def _q(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def add_months(dt: datetime.datetime, months: int) -> datetime.datetime:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    month_index = dt.month - 1 + int(months)
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _format_dt(dt: datetime.datetime | None) -> str:
    dt = _aware(dt)
    if not dt:
        return ''
    return timezone.localtime(dt).strftime('%Y-%m-%d %H:%M')


def normalize_billing_cycle(raw: str | None, *, default: str | None = None) -> str:
    fallback = default or ServiceOrder.BillingCycle.MONTHLY
    value = (raw or fallback or ServiceOrder.BillingCycle.MONTHLY).strip().lower()
    value = CYCLE_ALIASES.get(value, value)
    if value not in CYCLE_MONTHS:
        raise RenewBillingError('不支持的计费周期，请选择按月 / 按季 / 半年 / 按年')
    return value


def inherited_billing_cycle(instance: ServiceInstance) -> str:
    cycle = getattr(instance.service_order, 'billing_cycle', None)
    if cycle in CYCLE_MONTHS:
        return cycle
    return ServiceOrder.BillingCycle.MONTHLY


def _resolve_product(instance: ServiceInstance):
    from service_manager.change_billing import _resolve_product as resolve_product
    return resolve_product(instance)


def _business_type_for_instance(instance: ServiceInstance):
    from service_manager.change_billing import _business_type_for_instance
    return _business_type_for_instance(instance)


def quote_instance_renewal(instance: ServiceInstance, billing_cycle: str | None = None) -> dict:
    if instance.status == ServiceInstance.InstanceStatus.TERMINATED:
        raise RenewBillingError('已终止的服务不能续费')
    if instance.status not in RENEWABLE_STATUSES:
        raise RenewBillingError('当前状态不可续费，请待服务开通完成后再试')

    cycle = normalize_billing_cycle(
        billing_cycle,
        default=inherited_billing_cycle(instance),
    )
    months = CYCLE_MONTHS[cycle]
    monthly = _q(instance.effective_monthly_fee())
    currency = instance.effective_fee_currency()
    if monthly <= 0:
        raise RenewBillingError('实例月费为 0，无法续费出账，请先在后台设置月费或关联产品')

    due_now = _q(monthly * months)
    now = timezone.now()
    current_expire = _aware(instance.expire_time)
    anchor = current_expire if current_expire and current_expire > now else now
    new_expire = add_months(anchor, months)
    cycle_label = dict(ServiceOrder.BillingCycle.choices).get(cycle, cycle)

    return {
        'instance_no': instance.instance_no,
        'service_type': instance.service_type,
        'currency': currency,
        'billing_cycle': cycle,
        'billing_cycle_label': cycle_label,
        'months': months,
        'monthly_fee': str(monthly),
        'due_now': str(due_now),
        'current_expire': _format_dt(current_expire),
        'period_start': _format_dt(anchor),
        'new_expire': _format_dt(new_expire),
        'new_expire_iso': new_expire.isoformat(),
        'period_start_iso': anchor.isoformat(),
        'available_cycles': [
            {
                'value': key,
                'label': label,
                'months': CYCLE_MONTHS[key],
            }
            for key, label in ServiceOrder.BillingCycle.choices
            if key in CYCLE_MONTHS
        ],
        'policy_text': (
            f'按当前规格续费 {cycle_label}（{months} 个月），'
            f'应付 {currency}{due_now}。支付成功后到期日延长至 '
            f'{_format_dt(new_expire)}，无需再提交续期工单。'
        ),
        'effective_policy': 'pay_then_extend',
    }


def _unpaid_renew_billing(instance: ServiceInstance, source: str = 'portal_renew') -> CustomerBilling | None:
    billings = CustomerBilling.objects.select_for_update().filter(
        customer_id=instance.service_order.customer_id,
        status=CustomerBilling.BillingStatus.UNPAID,
        service_order__order_type=ServiceOrder.OrderType.RENEW,
        service_order__related_instance=instance,
        service_order__status=ServiceOrder.OrderStatus.APPROVED,
    ).select_related('service_order').order_by('-id')
    for billing in billings:
        snap = billing.service_order.request_snapshot or {}
        if snap.get('source') == source:
            return billing
    return None


def _apply_quote_to_order(order: ServiceOrder, instance: ServiceInstance, quote: dict) -> None:
    period_start = datetime.datetime.fromisoformat(quote['period_start_iso'])
    new_expire = datetime.datetime.fromisoformat(quote['new_expire_iso'])
    order.billing_cycle = quote['billing_cycle']
    order.quoted_price = _q(quote['due_now'])
    order.service_start = _aware(period_start).date()
    order.service_end = _aware(new_expire).date()
    order.product = _resolve_product(instance)
    order.request_snapshot = {
        'source': 'portal_renew',
        'renew_quote': quote,
        'quote': {
            'monthly_total': quote['due_now'],
            'setup_total': '0.00',
            'first_payment_total': quote['due_now'],
            'currency': quote['currency'],
            'billing_kind': 'instance_renew',
        },
    }
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING
    order.auto_provision_message = '等待支付续费账单'
    order.remark = f'门户一键续费 · 实例[{instance.instance_no}] · {quote["billing_cycle_label"]}'
    lock_order_fx(order, quote_currency=quote.get('currency'), force=True)


def _ensure_renew_billing(
    order: ServiceOrder,
    instance: ServiceInstance,
    quote: dict,
) -> CustomerBilling:
    amount = _q(quote['due_now'])
    currency = quote['currency']
    due_date = timezone.localdate() + datetime.timedelta(days=30)
    period_start = _aware(datetime.datetime.fromisoformat(quote['period_start_iso']))
    period_end = _aware(datetime.datetime.fromisoformat(quote['new_expire_iso']))
    days_used = max((period_end.date() - period_start.date()).days, 1)

    billing = CustomerBilling.objects.select_for_update().filter(service_order=order).first()
    if billing is None:
        billing = CustomerBilling(
            customer=order.customer,
            service_order=order,
            bill_month=timezone.localdate().strftime('%Y-%m'),
            amount=amount,
            currency=currency,
            auto_generated=False,
            due_date=due_date,
            remark=f'实例 [{instance.instance_no}] {quote.get("action_label") or "续费"}预付账单',
        )
        apply_billing_promotion(billing, amount)
        billing.save()
    elif billing.status != CustomerBilling.BillingStatus.PAID:
        inflight = billing.payment_transactions.filter(
            status__in={
                PaymentTransaction.Status.CREATED,
                PaymentTransaction.Status.PENDING,
            },
        ).exists()
        if inflight and (billing.amount != amount or billing.currency != currency):
            raise RenewBillingError('该续费账单正在支付中，不能修改账期或金额')
        billing.currency = currency
        billing.due_date = due_date
        apply_billing_promotion(billing, amount)
        billing.save(update_fields=[
            'amount', 'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'channel_referral', 'currency', 'due_date', 'updated_at',
        ])

    BillingItem.objects.update_or_create(
        billing=billing,
        instance_no=order.order_no,
        defaults={
            'description': (
                f'服务实例 [{instance.instance_no}] '
                f'{quote.get("action_label") or "续费"} '
                f'{quote["billing_cycle_label"]}（{quote["months"]} 个月）'
            ),
            'service_instance': instance,
            'service_type': instance.service_type,
            'product': order.product,
            'product_name': order.product.name if order.product else '',
            'period_start': period_start.date(),
            'period_end': period_end.date(),
            'days_used': days_used,
            'days_in_month': 30,
            'is_prorated': False,
            'is_trial': bool(instance.is_trial),
            'unit_monthly_fee': _q(quote['monthly_fee']),
            'base_monthly_fee': _q(quote['monthly_fee']),
            'overage_monthly_fee': Decimal('0.00'),
            'source_currency': currency,
            'exchange_rate': Decimal('1.000000'),
            'amount_original': amount,
            'currency': currency,
            'amount': amount,
            'detail': {
                'billing_kind': quote.get('billing_kind') or 'instance_renew',
                'service_order_id': order.pk,
                'order_no': order.order_no,
                'instance_no': instance.instance_no,
                'billing_cycle': quote['billing_cycle'],
                'months': quote['months'],
                'monthly_fee': quote['monthly_fee'],
                'old_expire': quote['current_expire'],
                'new_expire': quote['new_expire'],
                'quoted_price': str(amount),
            },
        },
    )
    return billing


@transaction.atomic
def submit_instance_renewal(
    instance: ServiceInstance,
    *,
    billing_cycle: str | None = None,
    requirements: str = '',
    user=None,
) -> dict:
    quote = quote_instance_renewal(instance, billing_cycle)
    locked = ServiceInstance.objects.select_for_update().select_related(
        'service_order__customer', 'vps', 'edge', 'vps__product', 'edge__product',
    ).get(pk=instance.pk)
    quote = quote_instance_renewal(locked, billing_cycle)

    existing = _unpaid_renew_billing(locked)
    if existing:
        order = existing.service_order
        _apply_quote_to_order(order, locked, quote)
        if requirements:
            order.requirements = requirements
        order.save()
        try:
            billing = _ensure_renew_billing(order, locked, quote)
        except OrderBillingError as exc:
            raise RenewBillingError(str(exc)) from exc
        reused = True
    else:
        now = timezone.now()
        period_start = datetime.datetime.fromisoformat(quote['period_start_iso'])
        new_expire = datetime.datetime.fromisoformat(quote['new_expire_iso'])
        order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.RENEW,
            business_type=_business_type_for_instance(locked),
            status=ServiceOrder.OrderStatus.APPROVED,
            current_stage=ServiceOrder.LifecycleStage.CHANGE,
            source=ServiceOrder.OrderSource.PORTAL,
            customer=locked.service_order.customer,
            product=_resolve_product(locked),
            preferred_pop=getattr(locked.vps or locked.edge, 'pop_point', None),
            related_instance=locked,
            billing_cycle=quote['billing_cycle'],
            quoted_price=_q(quote['due_now']),
            service_start=_aware(period_start).date(),
            service_end=_aware(new_expire).date(),
            requirements=requirements or '',
            created_by=user,
            approved_at=now,
            auto_provision_status=ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING,
            auto_provision_message='等待支付续费账单',
            remark=f'门户一键续费 · 实例[{locked.instance_no}] · {quote["billing_cycle_label"]}',
            **build_fx_fields(quote_currency=quote.get('currency')),
            request_snapshot={
                'source': 'portal_renew',
                'renew_quote': quote,
                'quote': {
                    'monthly_total': quote['due_now'],
                    'setup_total': '0.00',
                    'first_payment_total': quote['due_now'],
                    'currency': quote['currency'],
                    'billing_kind': 'instance_renew',
                },
            },
        )
        billing = _ensure_renew_billing(order, locked, quote)
        reused = False
        try:
            notify_order_event(order, '门户一键续费', event='service_order')
        except Exception:  # noqa: BLE001
            pass

    return {
        'quote': quote,
        'order': order,
        'billing': billing,
        'payment_required': billing is not None,
        'reused': reused,
        'message': (
            '已有未支付续费账单，请完成支付后生效'
            if reused
            else '续费账单已创建，请支付后延长到期日'
        ),
    }


def sync_resource_expire_time(instance: ServiceInstance) -> None:
    if instance.vps_id and instance.vps:
        instance.vps.expire_time = instance.expire_time
        instance.vps.save(update_fields=['expire_time', 'updated_at'])
    if instance.edge_id and instance.edge:
        instance.edge.expire_time = instance.expire_time
        instance.edge.save(update_fields=['expire_time', 'updated_at'])


def extend_instance_expiry(instance: ServiceInstance, *, new_expire: datetime.datetime) -> datetime.datetime:
    instance.expire_time = _aware(new_expire)
    apply_schedule_to_instance(instance, force=True)
    instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at', 'updated_at'])
    sync_resource_expire_time(instance)
    return instance.expire_time


def quote_trial_conversion(instance: ServiceInstance, billing_cycle: str | None = None) -> dict:
    if not instance.is_trial:
        raise RenewBillingError('当前不是测试服务，请使用续费')
    quote = quote_instance_renewal(instance, billing_cycle)
    now = timezone.now()
    new_expire = add_months(now, quote['months'])
    quote.update({
        'billing_kind': 'trial_convert',
        'action_label': '测试转正式',
        'current_expire': _format_dt(_aware(instance.expire_time)),
        'period_start': _format_dt(now),
        'official_start': _format_dt(now),
        'official_start_iso': now.isoformat(),
        'new_expire': _format_dt(new_expire),
        'new_expire_iso': new_expire.isoformat(),
        'period_start_iso': now.isoformat(),
        'policy_text': (
            f'测试转正式，按 {quote["billing_cycle_label"]}（{quote["months"]} 个月）支付 '
            f'{quote["currency"]}{quote["due_now"]}。'
            f'支付完成后正式开通日为 {_format_dt(now)}，到期日为 {_format_dt(new_expire)}。'
        ),
        'effective_policy': 'pay_then_convert',
    })
    return quote


@transaction.atomic
def submit_trial_conversion(
    instance: ServiceInstance,
    *,
    billing_cycle: str | None = None,
    requirements: str = '',
    user=None,
) -> dict:
    quote = quote_trial_conversion(instance, billing_cycle)
    locked = ServiceInstance.objects.select_for_update().select_related(
        'service_order__customer', 'vps', 'edge', 'vps__product', 'edge__product',
    ).get(pk=instance.pk)
    quote = quote_trial_conversion(locked, billing_cycle)

    existing = _unpaid_renew_billing(locked, 'portal_trial_convert')
    if existing:
        order = existing.service_order
        _apply_quote_to_order(order, locked, quote)
        order.remark = f'门户测试转正式 · 实例[{locked.instance_no}] · {quote["billing_cycle_label"]}'
        order.request_snapshot = {
            **(order.request_snapshot or {}),
            'source': 'portal_trial_convert',
            'renew_quote': quote,
        }
        if requirements:
            order.requirements = requirements
        order.save()
        try:
            billing = _ensure_renew_billing(order, locked, quote)
        except OrderBillingError as exc:
            raise RenewBillingError(str(exc)) from exc
        reused = True
    else:
        now = timezone.now()
        period_start = datetime.datetime.fromisoformat(quote['period_start_iso'])
        new_expire = datetime.datetime.fromisoformat(quote['new_expire_iso'])
        order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.RENEW,
            business_type=_business_type_for_instance(locked),
            status=ServiceOrder.OrderStatus.APPROVED,
            current_stage=ServiceOrder.LifecycleStage.USAGE,
            source=ServiceOrder.OrderSource.PORTAL,
            customer=locked.service_order.customer,
            product=_resolve_product(locked),
            preferred_pop=getattr(locked.vps or locked.edge, 'pop_point', None),
            related_instance=locked,
            billing_cycle=quote['billing_cycle'],
            quoted_price=_q(quote['due_now']),
            service_start=_aware(period_start).date(),
            service_end=_aware(new_expire).date(),
            requirements=requirements or '',
            created_by=user,
            approved_at=now,
            auto_provision_status=ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING,
            auto_provision_message='等待支付测试转正式账单',
            remark=f'门户测试转正式 · 实例[{locked.instance_no}] · {quote["billing_cycle_label"]}',
            **build_fx_fields(quote_currency=quote.get('currency')),
            request_snapshot={
                'source': 'portal_trial_convert',
                'renew_quote': quote,
                'quote': {
                    'monthly_total': quote['due_now'],
                    'setup_total': '0.00',
                    'first_payment_total': quote['due_now'],
                    'currency': quote['currency'],
                    'billing_kind': 'trial_convert',
                },
            },
        )
        billing = _ensure_renew_billing(order, locked, quote)
        reused = False
        try:
            notify_order_event(order, '门户测试转正式', event='service_order')
        except Exception:  # noqa: BLE001
            pass

    return {
        'quote': quote,
        'order': order,
        'billing': billing,
        'payment_required': billing is not None,
        'reused': reused,
        'message': (
            '已有未支付的转正式账单，请完成支付后生效'
            if reused
            else '转正式账单已创建，请支付后写入正式开通日和到期日'
        ),
    }


def _fulfill_trial_conversion(order: ServiceOrder, *, operator=None) -> dict:
    instance = ServiceInstance.objects.select_for_update().select_related(
        'service_order', 'vps', 'edge',
    ).get(pk=order.related_instance_id)
    snapshot = (order.request_snapshot or {}).get('renew_quote') or {}
    raw_start = snapshot.get('official_start_iso') or snapshot.get('period_start_iso')
    raw_expire = snapshot.get('new_expire_iso')
    official_start = datetime.datetime.fromisoformat(raw_start) if raw_start else timezone.now()
    if raw_expire:
        new_expire = datetime.datetime.fromisoformat(raw_expire)
    else:
        quote = quote_trial_conversion(instance, order.billing_cycle)
        official_start = datetime.datetime.fromisoformat(quote['official_start_iso'])
        new_expire = datetime.datetime.fromisoformat(quote['new_expire_iso'])

    before = service_snapshot(instance)
    instance.is_trial = False
    instance.trial_end = timezone.localdate()
    instance.billing_start_date = timezone.localtime(_aware(official_start)).date()
    instance.activated_at = _aware(official_start)
    instance.save(update_fields=[
        'is_trial', 'trial_end', 'billing_start_date', 'activated_at', 'updated_at',
    ])
    extend_instance_expiry(instance, new_expire=new_expire)
    instance.refresh_from_db()
    if instance.status in {
        ServiceInstance.InstanceStatus.SUSPENDED,
        ServiceInstance.InstanceStatus.EXPIRED,
    }:
        instance.resume(operator=operator)

    after = service_snapshot(instance)
    ServiceAllocation.objects.create(
        service_instance=instance,
        action_type=ServiceAllocation.ActionType.MODIFY,
        description=(
            f'测试转正式，开通 {_format_dt(instance.activated_at)}，'
            f'到期 {_format_dt(instance.expire_time)}'
        ),
        before_state=before,
        after_state=after,
        operator=operator,
    )
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.CONVERT_BILLING,
        description=(
            f'服务实例 [{instance.instance_no}] 测试转正式，'
            f'到期 {_format_dt(instance.expire_time)}'
        ),
        service_instance=instance,
        service_order=order,
        old_value={'is_trial': True},
        new_value={
            'is_trial': False,
            'billing_start_date': str(instance.billing_start_date),
            'expire_time': _format_dt(instance.expire_time),
        },
    )
    order.status = ServiceOrder.OrderStatus.ACTIVE
    order.current_stage = ServiceOrder.LifecycleStage.USAGE
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
    order.auto_provision_message = (
        f'已转为正式使用，开通日 {instance.billing_start_date}，'
        f'到期 {_format_dt(instance.expire_time)}'
    )
    order.save(update_fields=[
        'status', 'current_stage', 'auto_provision_status', 'auto_provision_message', 'updated_at',
    ])
    try:
        notify_service_event(
            'service_expiry',
            context={
                'module': '服务履约',
                'action': 'convert',
                'action_label': '测试转正式生效',
                'customer_name': instance.service_order.customer.name,
                'instance_no': instance.instance_no,
                'order_no': order.order_no,
                'expire_time': _format_dt(instance.expire_time),
                'is_trial': False,
                'operator_name': '到期引擎' if operator is None else (
                    operator.get_full_name() or operator.username
                ),
                'detail': order.auto_provision_message,
                'target_type': '服务实例',
                'target_name': instance.instance_no,
            },
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        'triggered': True,
        'reason': 'converted',
        'instance_id': instance.pk,
        'expire_time': instance.expire_time.isoformat() if instance.expire_time else '',
        'billing_start_date': str(instance.billing_start_date or ''),
    }


def fulfill_paid_renewal_order(order: ServiceOrder, *, operator=None) -> dict:
    """支付成功后延长实例到期，不开通新资源。"""
    if (order.request_snapshot or {}).get('source') == 'portal_trial_convert':
        if not order.related_instance_id:
            return {'triggered': False, 'reason': 'no_related_instance'}
        if order.auto_provision_status == ServiceOrder.AutoProvisionStatus.SUCCESS:
            return {'triggered': False, 'reason': 'already_fulfilled'}
        return _fulfill_trial_conversion(order, operator=operator)
    if not order.related_instance_id:
        return {'triggered': False, 'reason': 'no_related_instance'}
    if order.order_type != ServiceOrder.OrderType.RENEW:
        return {'triggered': False, 'reason': 'not_renew_order'}
    if order.auto_provision_status == ServiceOrder.AutoProvisionStatus.SUCCESS:
        return {'triggered': False, 'reason': 'already_fulfilled'}

    instance = ServiceInstance.objects.select_related(
        'service_order', 'vps', 'edge',
    ).get(pk=order.related_instance_id)
    snapshot = (order.request_snapshot or {}).get('renew_quote') or {}
    raw_expire = snapshot.get('new_expire_iso')
    if raw_expire:
        new_expire = datetime.datetime.fromisoformat(raw_expire)
    else:
        quote = quote_instance_renewal(instance, order.billing_cycle)
        new_expire = datetime.datetime.fromisoformat(quote['new_expire_iso'])

    before = service_snapshot(instance)
    old_expire = instance.expire_time
    extend_instance_expiry(instance, new_expire=new_expire)
    instance.refresh_from_db()

    if instance.status in {
        ServiceInstance.InstanceStatus.SUSPENDED,
        ServiceInstance.InstanceStatus.EXPIRED,
    }:
        instance.resume(operator=operator)

    after = service_snapshot(instance)
    ServiceAllocation.objects.create(
        service_instance=instance,
        action_type=ServiceAllocation.ActionType.RENEW,
        description=(
            f'续费生效，到期 '
            f'{_format_dt(old_expire) or "未设置"} → {_format_dt(instance.expire_time)}'
        ),
        before_state=before,
        after_state=after,
        operator=operator,
    )
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.RENEW,
        description=f'服务实例 [{instance.instance_no}] 续费至 {_format_dt(instance.expire_time)}',
        service_instance=instance,
        service_order=order,
        old_value={'expire_time': _format_dt(old_expire)},
        new_value={'expire_time': _format_dt(instance.expire_time)},
    )

    order.status = ServiceOrder.OrderStatus.ACTIVE
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
    order.auto_provision_message = f'续费已生效，到期日至 {_format_dt(instance.expire_time)}'
    order.save(update_fields=[
        'status', 'auto_provision_status', 'auto_provision_message', 'updated_at',
    ])

    try:
        notify_service_event(
            'service_expiry',
            context={
                'module': '服务履约',
                'action': 'renew',
                'action_label': '一键续费生效',
                'customer_name': instance.service_order.customer.name,
                'instance_no': instance.instance_no,
                'order_no': order.order_no,
                'expire_time': _format_dt(instance.expire_time),
                'operator_name': '到期引擎' if operator is None else (
                    operator.get_full_name() or operator.username
                ),
                'detail': order.auto_provision_message,
                'target_type': '服务实例',
                'target_name': instance.instance_no,
            },
        )
        notify_order_event(order, '续费已生效', event='service_order')
    except Exception:  # noqa: BLE001
        pass

    return {
        'triggered': True,
        'reason': 'renewed',
        'instance_id': instance.pk,
        'expire_time': instance.expire_time.isoformat() if instance.expire_time else '',
        'months': snapshot.get('months') or CYCLE_MONTHS.get(order.billing_cycle, 1),
    }
