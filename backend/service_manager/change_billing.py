"""服务变更报价与门户提交：升配补差价支付、降配下周期生效。"""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from product_manager import services as product_services
from service_manager.models import (
    ServiceChangeRecord,
    ServiceInstance,
    ServiceLifecycleRecord,
    ServiceOrder,
    service_snapshot,
)
from service_manager.lifecycle import mark_change_stage
from service_manager.notify_helpers import notify_change_event, notify_order_event
from service_manager.payment_billing import ensure_order_payment_billing

MONEY = Decimal('0.01')


class ChangeBillingError(ValueError):
    pass


def _q(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _as_int(value, default=None):
    if value is None or value == '':
        return default
    return int(value)


def current_resource_spec(instance: ServiceInstance) -> dict:
    resource, kind = instance.billed_resource()
    if resource is None:
        spec = {
            'cpu_cores': 0,
            'ram_mb': 0,
            'disk_gb': 0,
            'bandwidth_mbps': 0,
            'ip_count': 0,
            'nic_count': 0,
        }
    else:
        spec = product_services.resource_spec(resource, kind)
    product = _resolve_product(instance)
    if product is not None:
        spec = product_services.normalize_spec_for_category(product, spec)
    elif instance.edge_id:
        spec = product_services.normalize_spec_for_category(
            type('P', (), {'category': 'edge'})(), spec,
        )
    return spec


def instance_business_ip_queryset(instance: ServiceInstance):
    """实例已分配业务 IP：实例 M2M ∪ VPS/边缘归属，边缘不含隧道段。"""
    from resource_manager.ip_scope import exclude_tunnel_addresses
    from resource_manager.models import IPAddress

    ids = set(instance.ip_addresses.values_list('id', flat=True))
    if instance.edge_id and instance.edge:
        ids.update(instance.edge.ip_addresses.values_list('id', flat=True))
    if instance.vps_id and instance.vps:
        ids.update(instance.vps.ip_addresses.values_list('id', flat=True))
    qs = IPAddress.objects.filter(pk__in=ids) if ids else IPAddress.objects.none()
    if instance.edge_id:
        qs = exclude_tunnel_addresses(qs)
    return qs


def instance_business_ip_rows(instance: ServiceInstance) -> list[dict]:
    """当前实例已分配的业务 IP（边缘不含隧道段）。"""
    if instance.edge_id and instance.edge:
        from resource_manager.edge_provision import edge_business_ip_rows
        return edge_business_ip_rows(instance.edge)
    rows = []
    for ip in instance_business_ip_queryset(instance).order_by('-is_primary', 'id'):
        pushed = bool(getattr(ip, 'node_route_ref', ''))
        rows.append({
            'id': ip.id,
            'address': str(ip.address),
            'prefix_len': ip.prefix_len,
            'route_cidr': f'{ip.address}/32',
            'route_status': 'pushed' if pushed else 'pending',
            'route_status_label': '已下发' if pushed else '待下发',
            'is_primary': bool(ip.is_primary),
            'status': ip.status,
        })
    return rows


def _normalize_id_list(value) -> list[int]:
    ids = []
    for item in value or []:
        try:
            pk = int(item)
        except (TypeError, ValueError):
            continue
        if pk > 0 and pk not in ids:
            ids.append(pk)
    return ids


def merge_requested_spec(instance: ServiceInstance, requested: dict | None) -> dict:
    current = current_resource_spec(instance)
    requested = requested or {}
    merged = dict(current)
    mapping = {
        'requested_vcpu': 'cpu_cores',
        'cpu_cores': 'cpu_cores',
        'vcpu': 'cpu_cores',
        'requested_ram_mb': 'ram_mb',
        'ram_mb': 'ram_mb',
        'requested_disk_gb': 'disk_gb',
        'disk_gb': 'disk_gb',
        'requested_bandwidth_mbps': 'bandwidth_mbps',
        'bandwidth_mbps': 'bandwidth_mbps',
        'requested_ip_count': 'ip_count',
        'ip_count': 'ip_count',
        'nic_count': 'nic_count',
    }
    for src, dst in mapping.items():
        if src in requested and requested[src] is not None:
            merged[dst] = _as_int(requested[src], merged.get(dst) or 0)
    product = _resolve_product(instance)
    if product is not None:
        merged = product_services.normalize_spec_for_category(product, merged)
    elif instance.edge_id:
        merged = product_services.normalize_spec_for_category(
            type('P', (), {'category': 'edge'})(), merged,
        )
    return merged


def specs_equal(a: dict, b: dict) -> bool:
    keys = ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count', 'nic_count')
    return all(int(a.get(k) or 0) == int(b.get(k) or 0) for k in keys)


def next_billing_cycle_start(instance: ServiceInstance | None = None) -> datetime.date:
    """下个计费周期起点：优先按计费起始日周年，否则下月 1 日。"""
    today = timezone.localdate()
    anchor = None
    if instance is not None:
        anchor = instance.billing_effective_start()
    if anchor:
        day = min(anchor.day, 28)
        year, month = today.year, today.month
        candidate = datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if candidate <= today:
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            candidate = datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))
        return candidate
    if today.month == 12:
        return datetime.date(today.year + 1, 1, 1)
    return datetime.date(today.year, today.month + 1, 1)


def prorate_remainder(amount: Decimal, *, on_date: datetime.date | None = None) -> Decimal:
    """按本月剩余天数（含当天）折算金额。"""
    on_date = on_date or timezone.localdate()
    days_in_month = calendar.monthrange(on_date.year, on_date.month)[1]
    remaining = max(days_in_month - on_date.day + 1, 1)
    return (_q(amount) * Decimal(remaining) / Decimal(days_in_month)).quantize(MONEY)


def _resolve_product(instance: ServiceInstance):
    resource, _kind = instance.billed_resource()
    product = getattr(resource, 'product', None) if resource is not None else None
    if product is None:
        product = getattr(instance.service_order, 'product', None)
    return product


def compute_monthly_for_spec(instance: ServiceInstance, spec: dict) -> tuple[Decimal, str, dict | None]:
    """按目标规格估算月费。有产品则走阶梯定价；否则沿用当前有效月费。"""
    currency = instance.effective_fee_currency()
    product = _resolve_product(instance)
    if product is not None:
        price = product_services.compute_price(product, spec)
        return _q(price['monthly_price']), (price.get('currency') or product.currency or currency).upper(), price
    return _q(instance.effective_monthly_fee()), currency, None


def quote_service_change(instance: ServiceInstance, requested: dict | None = None) -> dict:
    requested = dict(requested or {})
    allocated_ips = instance_business_ip_rows(instance)
    allocated_ids = {int(row['id']) for row in allocated_ips if row.get('id')}
    release_ip_ids = _normalize_id_list(requested.get('release_ip_ids'))
    unknown = [pk for pk in release_ip_ids if pk not in allocated_ids]
    if unknown:
        raise ChangeBillingError('只能释放当前服务已分配的 IP')
    add_ip_count = max(_as_int(requested.get('add_ip_count'), 0) or 0, 0)
    if requested.get('requested_ip_count') is None and (release_ip_ids or add_ip_count):
        requested['requested_ip_count'] = max(0, len(allocated_ips) - len(release_ip_ids) + add_ip_count)

    current_spec = current_resource_spec(instance)
    current_spec = dict(current_spec)
    live_ip_count = len(allocated_ips)
    if live_ip_count or instance.edge_id:
        current_spec['ip_count'] = live_ip_count
    else:
        current_spec['ip_count'] = int(current_spec.get('ip_count') or 0)
    target_spec = merge_requested_spec(instance, requested)
    if requested.get('requested_ip_count') is None:
        target_spec = dict(target_spec)
        target_spec['ip_count'] = current_spec['ip_count']
    old_fee = _q(instance.effective_monthly_fee())
    new_fee, currency, price_detail = compute_monthly_for_spec(instance, target_spec)
    delta = _q(new_fee - old_fee)
    replace_ips = bool(requested.get('replace_ips'))

    bw_delta = int(target_spec.get('bandwidth_mbps') or 0) - int(current_spec.get('bandwidth_mbps') or 0)
    ip_delta = int(target_spec.get('ip_count') or 0) - int(current_spec.get('ip_count') or 0)

    today = timezone.localdate()
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    remaining_days = max(days_in_month - today.day + 1, 1)
    next_cycle = next_billing_cycle_start(instance)
    credit_now = Decimal('0.00')
    due_now = Decimal('0.00')

    if replace_ips:
        direction = 'ip_replace'
        effective_at = today.isoformat()
        effective_policy = 'manual_confirm'
        policy_text = '替换 IP 需人工确认：提交后由运维核对原地址与新地址，确认后再下发，不会自动释放或重分配。'
    elif specs_equal(current_spec, target_spec):
        direction = 'unchanged'
        effective_at = today.isoformat()
        effective_policy = 'immediate_no_charge'
        policy_text = '规格未变化。若仅需补全协议或接口参数，请到「管理」页按步骤配置并下发。'
    elif delta > 0:
        direction = 'upgrade'
        due_now = prorate_remainder(delta, on_date=today)
        effective_at = today.isoformat()
        effective_policy = 'pay_then_apply'
        policy_text = (
            '带宽或 IP 增加：新增月费按本月剩余天数折算，支付成功后立即下发；'
            '节点资源不足则通知管理员补齐后手动交付。'
        )
    elif delta < 0:
        direction = 'downgrade'
        credit_now = prorate_remainder(-delta, on_date=today)
        effective_at = today.isoformat()
        effective_policy = 'immediate_credit'
        policy_text = (
            '带宽或 IP 减少：减少月费按本月剩余天数折算并立即生效；'
            f'本期减少费用 {currency}{credit_now} 将记入账户余额（币种一致时）或变更单。'
        )
    else:
        direction = 'change'
        effective_at = today.isoformat()
        effective_policy = 'immediate_apply'
        policy_text = '规格有调整但月费不变，提交后立即下发到节点。'

    return {
        'instance_no': instance.instance_no,
        'service_type': instance.service_type,
        'currency': currency,
        'direction': direction,
        'current_spec': current_spec,
        'target_spec': target_spec,
        'bandwidth_delta': bw_delta,
        'ip_delta': ip_delta,
        'current_bandwidth_mbps': int(current_spec.get('bandwidth_mbps') or 0),
        'target_bandwidth_mbps': int(target_spec.get('bandwidth_mbps') or 0),
        'bandwidth_action': (
            'increase' if bw_delta > 0 else 'decrease' if bw_delta < 0 else 'keep'
        ),
        'allocated_ips': allocated_ips,
        'release_ip_ids': release_ip_ids,
        'add_ip_count': add_ip_count,
        'replace_ips': replace_ips,
        'old_monthly_fee': str(old_fee),
        'new_monthly_fee': str(new_fee),
        'monthly_delta': str(delta),
        'due_now': str(due_now),
        'credit_now': str(credit_now),
        'remaining_days': remaining_days,
        'days_in_month': days_in_month,
        'effective_at': effective_at,
        'next_cycle_at': next_cycle.isoformat(),
        'effective_policy': effective_policy,
        'policy_text': policy_text,
        'price_breakdown': (price_detail or {}).get('breakdown') or [],
        'has_product_pricing': price_detail is not None,
    }


def _business_type_for_instance(instance: ServiceInstance):
    if instance.service_type == 'vps':
        return ServiceOrder.BusinessType.VPS
    if instance.edge_id and instance.edge:
        proto = (instance.edge.protocol or '').lower()
        if 'wireguard' in proto or proto == 'wg':
            return ServiceOrder.BusinessType.EDGE_WG
        if 'socks' in proto:
            return ServiceOrder.BusinessType.EDGE_SOCKS5
        return ServiceOrder.BusinessType.EDGE_GRE
    return instance.service_order.business_type


def _build_after_spec(instance: ServiceInstance, quote: dict) -> dict:
    before = service_snapshot(instance)
    after = dict(before)
    target = quote['target_spec']
    after['cpu_cores'] = target.get('cpu_cores')
    after['vcpu'] = target.get('cpu_cores')
    after['ram_mb'] = target.get('ram_mb')
    after['disk_gb'] = target.get('disk_gb')
    after['bandwidth_mbps'] = target.get('bandwidth_mbps')
    after['ip_count'] = target.get('ip_count')
    after['nic_count'] = target.get('nic_count')
    after['monthly_fee'] = quote['new_monthly_fee']
    after['billing'] = {
        'old_monthly_fee': quote['old_monthly_fee'],
        'new_monthly_fee': quote['new_monthly_fee'],
        'monthly_delta': quote['monthly_delta'],
        'due_now': quote['due_now'],
        'direction': quote['direction'],
        'effective_policy': quote['effective_policy'],
        'effective_at': quote['effective_at'],
        'currency': quote['currency'],
    }
    if instance.vps_id:
        after['vps'] = {
            **(after.get('vps') or {}),
            'vcpu': target.get('cpu_cores'),
            'ram_mb': target.get('ram_mb'),
            'disk_gb': target.get('disk_gb'),
            'bandwidth_mbps': target.get('bandwidth_mbps'),
        }
    if instance.edge_id:
        after['edge'] = {
            **(after.get('edge') or {}),
            'bandwidth_mbps': target.get('bandwidth_mbps'),
        }
    after['release_ip_ids'] = list(quote.get('release_ip_ids') or [])
    after['add_ip_count'] = int(quote.get('add_ip_count') or 0)
    return after


def _change_type_for_direction(direction: str) -> str:
    if direction == 'upgrade':
        return ServiceChangeRecord.ChangeType.UPGRADE
    if direction == 'downgrade':
        return ServiceChangeRecord.ChangeType.DOWNGRADE
    if direction == 'ip_replace':
        return ServiceChangeRecord.ChangeType.IP_CHANGE
    return ServiceChangeRecord.ChangeType.OTHER


def _order_type_for_direction(direction: str) -> str:
    if direction == 'upgrade':
        return ServiceOrder.OrderType.UPGRADE
    if direction == 'downgrade':
        return ServiceOrder.OrderType.DOWNGRADE
    return ServiceOrder.OrderType.CHANGE


def _credit_downgrade_wallet(instance: ServiceInstance, quote: dict, change: ServiceChangeRecord, user=None):
    amount = _q(quote.get('credit_now'))
    if amount <= 0:
        return None
    customer = getattr(instance.service_order, 'customer', None)
    if customer is None:
        return None
    try:
        from customer_manager.models import CustomerBalanceLedger
        from customer_manager.wallet import WalletError, adjust_customer_balance, wallet_currency

        currency = (quote.get('currency') or instance.effective_fee_currency() or '').upper()
        if (wallet_currency() or '').upper() != currency:
            return None
        return adjust_customer_balance(
            customer,
            delta=amount,
            entry_type=CustomerBalanceLedger.EntryType.ADJUST,
            remark=f'服务变更 {change.change_no} 降配按剩余天数折算退回',
            operator=user,
            currency=currency,
        )
    except (WalletError, Exception):  # noqa: BLE001
        return None


@transaction.atomic
def submit_service_change(
    instance: ServiceInstance,
    *,
    requested: dict | None = None,
    requirements: str = '',
    user=None,
) -> dict:
    """创建变更工单与变更记录；升配补差价支付，降配立即折算并生效，换 IP 转人工。"""
    quote = quote_service_change(instance, requested)
    if quote['direction'] == 'unchanged':
        raise ChangeBillingError('规格未发生变化，无需提交变更。协议或接口参数请到管理页配置。')

    product = _resolve_product(instance)
    if quote['direction'] == 'upgrade' and not quote['has_product_pricing'] and _q(quote['due_now']) > 0:
        # 无产品定价时无法可靠计算补差价，仍允许提交为人工审批工单
        pass

    locked = ServiceInstance.objects.select_for_update().select_related(
        'service_order', 'vps', 'edge', 'vps__product', 'edge__product',
    ).get(pk=instance.pk)

    pending = locked.change_records.filter(
        status__in=[
            ServiceChangeRecord.ChangeStatus.PENDING,
            ServiceChangeRecord.ChangeStatus.APPROVED,
            ServiceChangeRecord.ChangeStatus.IN_PROGRESS,
        ],
    ).exists()
    if pending:
        raise ChangeBillingError('该服务已有进行中的变更，请完成或取消后再提交')

    before = service_snapshot(locked)
    after_spec = _build_after_spec(locked, quote)
    direction = quote['direction']
    due_now = _q(quote['due_now'])
    target = quote['target_spec']

    if direction == 'upgrade' and due_now > 0 and product is None:
        raise ChangeBillingError('当前服务未关联产品定价，无法自动计算升配补差价，请联系客服处理')

    from service_manager.fx_snapshot import build_fx_fields

    order = ServiceOrder.objects.create(
        order_type=_order_type_for_direction(direction),
        business_type=_business_type_for_instance(locked),
        status=ServiceOrder.OrderStatus.PENDING,
        current_stage=ServiceOrder.LifecycleStage.CHANGE,
        source=ServiceOrder.OrderSource.PORTAL,
        customer=locked.service_order.customer,
        product=product,
        preferred_pop=getattr(locked.vps or locked.edge, 'pop_point', None),
        related_instance=locked,
        requested_vcpu=target.get('cpu_cores'),
        requested_ram_mb=target.get('ram_mb'),
        requested_disk_gb=target.get('disk_gb'),
        requested_bandwidth_mbps=target.get('bandwidth_mbps'),
        requested_ip_count=target.get('ip_count'),
        requirements=requirements or '',
        quoted_price=due_now if due_now > 0 else Decimal('0.00'),
        remark=f'门户服务变更 · 实例[{locked.instance_no}] · {direction}',
        **build_fx_fields(quote_currency=quote.get('currency')),
        request_snapshot={
            'source': 'portal_change',
            'change_quote': quote,
            'requested_spec': target,
            'requirements': requirements or '',
            'quote': {
                'monthly_total': quote['due_now'],
                'setup_total': '0.00',
                'first_payment_total': quote['due_now'],
                'currency': quote['currency'],
                'old_monthly_fee': quote['old_monthly_fee'],
                'new_monthly_fee': quote['new_monthly_fee'],
                'billing_kind': 'service_change_proration',
            },
        },
        created_by=user,
    )

    scheduled_at = None
    change_status = ServiceChangeRecord.ChangeStatus.PENDING
    policy = quote.get('effective_policy')
    if direction == 'downgrade' and policy == 'next_cycle':
        scheduled_at = timezone.make_aware(
            datetime.datetime.combine(
                next_billing_cycle_start(locked),
                datetime.time.min,
            )
        )
        change_status = ServiceChangeRecord.ChangeStatus.APPROVED
    elif direction in ('downgrade', 'change') and policy in (
        'immediate_credit', 'immediate_apply', 'immediate_no_charge',
    ):
        change_status = ServiceChangeRecord.ChangeStatus.APPROVED

    change = ServiceChangeRecord.objects.create(
        service_instance=locked,
        related_order=order,
        change_type=_change_type_for_direction(direction),
        status=change_status,
        change_reason=requirements or f'门户发起{direction}变更',
        change_detail=(
            f'原月费 {quote["currency"]}{quote["old_monthly_fee"]} → '
            f'变更后 {quote["currency"]}{quote["new_monthly_fee"]}；'
            f'{quote["policy_text"]}'
        ),
        before_spec=before,
        after_spec=after_spec,
        price_delta=_q(quote['monthly_delta']),
        scheduled_at=scheduled_at,
        requested_by=user,
        approved_by=user if change_status == ServiceChangeRecord.ChangeStatus.APPROVED else None,
        approved_at=timezone.now() if change_status == ServiceChangeRecord.ChangeStatus.APPROVED else None,
    )

    billing = None
    payment_required = False

    if direction == 'upgrade' and due_now > 0:
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.approved_at = timezone.now()
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING
        order.auto_provision_message = '等待支付升配补差价账单'
        order.save(update_fields=[
            'status', 'approved_at', 'auto_provision_status',
            'auto_provision_message', 'updated_at',
        ])
        from service_manager.payment_billing import OrderBillingError
        try:
            billing = ensure_order_payment_billing(order)
        except OrderBillingError as exc:
            raise ChangeBillingError(str(exc)) from exc
        if billing is not None:
            item = billing.items.first()
            if item is not None:
                item.description = (
                    f'服务变更 [{change.change_no}] 升配补差价 '
                    f'（{quote["old_monthly_fee"]} → {quote["new_monthly_fee"]} /月，'
                    f'本月剩余 {quote["remaining_days"]} 天）'
                )
                item.detail = {
                    **(item.detail or {}),
                    'billing_kind': 'service_change_proration',
                    'change_no': change.change_no,
                    'change_id': change.id,
                    'old_monthly_fee': quote['old_monthly_fee'],
                    'new_monthly_fee': quote['new_monthly_fee'],
                }
                item.save(update_fields=['description', 'detail'])
        payment_required = billing is not None
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.approved_at = timezone.now()
        change.approved_by = user
        change.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])
    elif direction == 'upgrade' and due_now == 0:
        # 无补差价的升配：直接审批，等待支付履约或立即执行由调用方决定
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.approved_at = timezone.now()
        order.save(update_fields=['status', 'approved_at', 'updated_at'])
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.approved_at = timezone.now()
        change.approved_by = user
        change.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])
    if direction == 'upgrade':
        from service_manager.capacity_reservation import (
            CapacityReservationError, reserve_for_order,
        )
        try:
            reserve_for_order(order)
        except CapacityReservationError as exc:
            raise ChangeBillingError(str(exc)) from exc
    elif direction == 'ip_replace':
        order.status = ServiceOrder.OrderStatus.PENDING
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAID_PENDING
        order.auto_provision_message = '替换 IP 待管理员确认后手动交付'
        order.save(update_fields=[
            'status', 'auto_provision_status', 'auto_provision_message', 'updated_at',
        ])
        from .provisioning import notify_managers
        notify_managers(
            order,
            '门户申请替换 IP，待人工确认',
            f'实例 {locked.instance_no} 申请替换 IP，请核对原地址后手动确认并下发。',
        )
    elif direction == 'downgrade':
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.approved_at = timezone.now()
        if policy == 'next_cycle':
            order.remark = (
                f'{order.remark}\n降配已预约于 {quote["effective_at"]} 生效，本月仍按原月费计费。'
            )[:2000]
        else:
            order.remark = (
                f'{order.remark}\n降配立即生效，本期折算减少 {quote["currency"]}{quote.get("credit_now") or "0.00"}。'
            )[:2000]
        order.save(update_fields=['status', 'approved_at', 'remark', 'updated_at'])
        _credit_downgrade_wallet(locked, quote, change, user)
    elif direction == 'change' and policy in ('immediate_apply', 'immediate_no_charge'):
        order.status = ServiceOrder.OrderStatus.APPROVED
        order.approved_at = timezone.now()
        order.save(update_fields=['status', 'approved_at', 'updated_at'])

    try:
        mark_change_stage(
            order, locked, change,
            submitted_by=user,
            status=(
                ServiceLifecycleRecord.StageStatus.IN_PROGRESS
                if change.status == ServiceChangeRecord.ChangeStatus.APPROVED
                else ServiceLifecycleRecord.StageStatus.PENDING
            ),
        )
        notify_order_event(order, '门户服务变更', event='service_order')
        notify_change_event(change, '门户服务变更已提交')
    except Exception:  # noqa: BLE001
        # 通知失败不影响主流程
        pass

    applied = None
    if (
        direction in ('downgrade', 'change')
        and policy in ('immediate_credit', 'immediate_apply', 'immediate_no_charge')
        and change.status == ServiceChangeRecord.ChangeStatus.APPROVED
    ):
        from service_manager.change_provisioning import apply_paid_change
        applied = apply_paid_change(order, change, operator=user)
        order.refresh_from_db()
        change.refresh_from_db()

    if payment_required:
        message = '升配变更已创建，请支付补差价后生效'
    elif direction == 'ip_replace':
        message = '替换 IP 申请已提交，等待管理员确认后下发'
    elif direction == 'downgrade' and policy == 'next_cycle':
        message = f'降配已预约，将于 {quote["effective_at"]} 生效；本月费用保持不变'
    elif direction == 'downgrade':
        message = (
            f'变更成功。降配已立即生效，本期按剩余天数折算减少 {quote["currency"]}'
            f'{quote.get("credit_now") or "0.00"}'
        )
        if applied and not applied.get('triggered'):
            message += f'；自动下发未完成：{applied.get("reason") or applied.get("error") or "待运维处理"}'
        elif applied and any(s.get('status') == 'error' for s in (applied.get('progress_steps') or [])):
            message = '变更已生效，但部分 IP/路由步骤未完成，请查看详情'
    else:
        if applied and any(s.get('status') == 'error' for s in (applied.get('progress_steps') or [])):
            message = '变更已生效，但部分 IP/路由步骤未完成，请查看详情'
        else:
            message = '变更成功' if applied and applied.get('triggered') else '变更申请已提交'

    return {
        'quote': quote,
        'order': order,
        'change': change,
        'billing': billing,
        'payment_required': payment_required,
        'applied': applied,
        'message': message,
    }


def fulfill_paid_change_order(order: ServiceOrder, *, operator=None) -> dict:
    """支付成功后执行升配变更。"""
    if not order.related_instance_id:
        return {'triggered': False, 'reason': 'no_related_instance'}
    if order.order_type not in (
        ServiceOrder.OrderType.UPGRADE,
        ServiceOrder.OrderType.DOWNGRADE,
        ServiceOrder.OrderType.CHANGE,
    ):
        return {'triggered': False, 'reason': 'not_change_order'}

    change = (
        ServiceChangeRecord.objects.select_for_update()
        .filter(related_order=order)
        .order_by('-id')
        .first()
    )
    if change is None:
        return {'triggered': False, 'reason': 'change_not_found'}

    if change.status == ServiceChangeRecord.ChangeStatus.COMPLETED:
        return {'triggered': False, 'reason': 'already_completed'}

    # 降配若尚未到计划时间，仅确认支付态（通常降配无支付）
    if (
        change.change_type == ServiceChangeRecord.ChangeType.DOWNGRADE
        and change.scheduled_at
        and change.scheduled_at > timezone.now()
    ):
        return {'triggered': False, 'reason': 'scheduled_for_later'}

    if change.status == ServiceChangeRecord.ChangeStatus.PENDING:
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.approved_at = timezone.now()
        change.approved_by = operator
        change.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])

    from service_manager.change_provisioning import apply_paid_change

    return apply_paid_change(order, change, operator=operator)
