"""
customer_manager/billing_engine.py
按月 + 按天比例的账单计费引擎。

规则：
  - 以「账单月份(YYYY-MM)」为单位，汇总该客户当月所有服务实例的费用。
  - 服务在当月开通满整月 → 按整月费用计；未满整月 → 按 (当月使用天数 / 当月总天数) × 整月费用 计。
  - 测试服务默认不计入账单金额，但单独统计列示；转入计费后按计费起始日参与计费。
  - 各实例费用按其币种折算到账单结算币种（默认人民币）后汇总。
"""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from system_manager import currency_services as cs


class BillingGenerationBlocked(ValueError):
    """账单存在不能被覆盖的支付交易时抛出。"""


def month_range(bill_month: str):
    """'YYYY-MM' → (first_day, last_day, days_in_month)"""
    year, month = (int(x) for x in bill_month.split('-'))
    days_in_month = calendar.monthrange(year, month)[1]
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, days_in_month)
    return first, last, days_in_month


def _q2(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _instance_label(inst) -> str:
    res = None
    if inst.vps_id and inst.vps:
        res = f'VPS {inst.vps.name}'
    elif inst.edge_id and inst.edge:
        res = f'边缘加速 {inst.edge.name}'
    elif inst.bandwidth_alloc_id and inst.bandwidth_alloc:
        res = f'带宽 {inst.bandwidth_alloc.allocated_mbps}Mbps'
    return f'[{inst.instance_no}] {res or inst.get_service_type_display()}'


# 规格维度展示元数据：键 → (标签, 单位, 升级规则资源键)
_SPEC_DIMS = [
    ('cpu_cores', 'CPU', '核', 'cpu'),
    ('ram_mb', '内存', 'MB', 'ram'),
    ('disk_gb', '硬盘', 'GB', 'disk'),
    ('bandwidth_mbps', '带宽', 'Mbps', 'bandwidth'),
    ('ip_count', 'IP', '个', 'ip'),
    ('nic_count', '网卡', '个', 'nic'),
]
_KIND_LABEL = {'cpu': 'CPU', 'ram': '内存', 'disk': '硬盘', 'bandwidth': '带宽', 'ip': 'IP', 'nic': '网卡'}


def _build_detail(*, basis, spec, base_spec, breakdown, base_fee, overage_fee, unit_fee,
                  days_used, days_in_month, is_prorated, ratio, source_currency,
                  amount_original, currency, exchange_rate, amount, is_trial):
    """构造一条账单明细的「详情快照」，供前端展开展示完整计费逻辑。"""
    basis_label = {'product': '产品定价（基础+超出）', 'manual': '人工固定月费', 'quoted': '工单报价'}.get(basis, basis)

    # 规格对比（实际 vs 模版基准），仅产品定价时有
    dims = []
    if spec is not None and base_spec is not None:
        for key, label, unit, _kind in _SPEC_DIMS:
            actual = int(spec.get(key, 0) or 0)
            base = int(base_spec.get(key, 0) or 0)
            if actual == 0 and base == 0:
                continue
            dims.append({
                'label': label, 'unit': unit,
                'base': base, 'actual': actual,
                'over': max(0, actual - base),
            })

    # 超出阶梯逐项（resource_kind + 升级单位数 + 月租增量）
    over_items = [{
        'label': _KIND_LABEL.get(b.get('resource_kind'), b.get('resource_kind')),
        'resource_kind': b.get('resource_kind'),
        'upgrade_qty': b.get('upgrade_qty'),
        'monthly_delta': b.get('monthly_delta'),
    } for b in (breakdown or [])]

    return {
        'basis': basis,
        'basis_label': basis_label,
        'spec_dims': dims,
        'overage_items': over_items,
        'base_monthly': str(_q2(base_fee)),
        'overage_monthly': str(_q2(overage_fee)),
        'unit_monthly_fee': str(_q2(unit_fee)),
        'days_used': days_used,
        'days_in_month': days_in_month,
        'is_prorated': is_prorated,
        'ratio': f'{ratio.quantize(Decimal("0.0001"))}',
        'source_currency': source_currency,
        'amount_original': str(_q2(amount_original)),
        'currency': currency,
        'exchange_rate': str(exchange_rate),
        'amount': str(_q2(amount)),
        'is_trial': is_trial,
    }


def _res_segment(start_dt, end_dt, first, last):
    """计算资源在账单月内的计费区间 (seg_start, seg_end)；不在服务期内返回 (None, None)。"""
    eff_start = start_dt.date() if start_dt else first
    end = end_dt.date() if end_dt else None
    seg_start = max(first, eff_start)
    seg_end = min(last, end) if end else last
    if seg_start > seg_end:
        return None, None
    return seg_start, seg_end


def _resource_item(*, resource, kind, label, service_type, instance_no,
                   start_dt, end_dt, first, last, days_in_month, currency, rates):
    """根据「关联产品」的资源构造一条账单明细（基础 + 超出，按天比例）。

    资源未关联产品 / 不在当月服务期内时返回 None。
    """
    from product_manager import services as product_services

    pb = product_services.compute_resource_billing(resource, kind)
    if pb is None:
        return None
    seg_start, seg_end = _res_segment(start_dt, end_dt, first, last)
    if seg_start is None:
        return None

    days_used = (seg_end - seg_start).days + 1
    is_prorated = days_used < days_in_month
    ratio = Decimal(days_used) / Decimal(days_in_month) if is_prorated else Decimal('1')

    unit_fee = Decimal(str(pb['monthly_price']))
    base_fee = Decimal(str(pb['base_monthly']))
    overage_fee = Decimal(str(pb['overage_monthly']))
    source_currency = (pb['currency'] or cs.CNY).upper()

    amount_original = _q2(unit_fee * ratio)
    amount_converted = cs.convert(amount_original, source_currency, currency, rates)
    unit_one = cs.convert(Decimal('1'), source_currency, currency, rates)

    detail = _build_detail(
        basis='product', spec=pb.get('spec'), base_spec=pb.get('base_spec'),
        breakdown=pb.get('breakdown') or [],
        base_fee=base_fee, overage_fee=overage_fee, unit_fee=unit_fee,
        days_used=days_used, days_in_month=days_in_month, is_prorated=is_prorated,
        ratio=ratio, source_currency=source_currency, amount_original=amount_original,
        currency=currency, exchange_rate=unit_one, amount=amount_converted, is_trial=False,
    )
    return {
        'service_instance': None,
        'instance_no': instance_no,
        'service_type': service_type,
        'description': label,
        'product_id': pb['product_id'],
        'product_name': pb['product_name'],
        'period_start': seg_start.isoformat(),
        'period_end': seg_end.isoformat(),
        'days_used': days_used,
        'days_in_month': days_in_month,
        'is_prorated': is_prorated,
        'is_trial': False,
        'unit_monthly_fee': str(_q2(unit_fee)),
        'base_monthly_fee': str(_q2(base_fee)),
        'overage_monthly_fee': str(_q2(overage_fee)),
        'source_currency': source_currency,
        'exchange_rate': str(unit_one),
        'amount_original': str(amount_original),
        'currency': currency,
        'amount': str(amount_converted),
        'detail': detail,
    }


def _customer_resource_items(customer, first, last, days_in_month, currency, rates,
                             covered_vps, covered_edge):
    """汇总「直接分配给客户、关联了产品、且未被服务实例承载」的 VPS / 边缘加速资源。"""
    from resource_manager.models import VPS, EdgeAcceleration

    out = []

    vps_qs = (
        VPS.objects
        .filter(customer=customer, product__isnull=False)
        .exclude(id__in=covered_vps)
        .exclude(status=VPS.VPSStatus.DELETING)
        .select_related('product')
        .prefetch_related('ip_addresses')
    )
    for vps in vps_qs:
        item = _resource_item(
            resource=vps, kind='vps',
            label=f'[{vps.vps_no}] VPS {vps.name}',
            service_type='vps', instance_no=vps.vps_no or '',
            start_dt=vps.start_time, end_dt=vps.expire_time,
            first=first, last=last, days_in_month=days_in_month,
            currency=currency, rates=rates,
        )
        if item:
            out.append(item)

    edge_qs = (
        EdgeAcceleration.objects
        .filter(customer=customer, product__isnull=False)
        .exclude(id__in=covered_edge)
        .select_related('product')
        .prefetch_related('ip_addresses')
    )
    for edge in edge_qs:
        item = _resource_item(
            resource=edge, kind='edge',
            label=f'[{edge.edge_no}] 边缘加速 {edge.name}',
            service_type='edge', instance_no=edge.edge_no or '',
            start_dt=edge.start_time, end_dt=edge.expire_time,
            first=first, last=last, days_in_month=days_in_month,
            currency=currency, rates=rates,
        )
        if item:
            out.append(item)

    return out


def compute_customer_month(customer, bill_month: str, target_currency: str | None = None) -> dict:
    """计算某客户某月的计费明细（不落库）。

    数据来源（合并去重）：
      1) 客户工单下的服务实例 ServiceInstance（人工月费 / 产品定价 / 工单报价）；
      2) 直接分配给客户、关联了产品、但未被服务实例承载的 VPS / 边缘加速资源
         —— 按产品「基础规格 + 超出阶梯」定价，按当月服务天数比例计费。
    """
    from service_manager.models import ServiceInstance

    first, last, days_in_month = month_range(bill_month)
    rates = cs.get_rates()
    currency = (target_currency or rates['default_currency'] or cs.CNY).upper()

    # 当月可能产生费用的实例：排除仍在开通中(provisioning)且无计费起始的
    instances = (
        ServiceInstance.objects
        .filter(service_order__customer=customer)
        .exclude(status='terminated', terminated_at__date__lt=first)  # 本月之前已终止的排除
        .select_related('service_order', 'vps', 'edge', 'bandwidth_alloc')
    )

    items = []
    billable_total = Decimal('0.00')
    trial_total = Decimal('0.00')
    trial_count = 0
    # 记录已被服务实例承载的资源，避免与「直接分配」资源重复计费
    covered_vps: set[int] = set()
    covered_edge: set[int] = set()

    for inst in instances:
        if inst.vps_id:
            covered_vps.add(inst.vps_id)
        if inst.edge_id:
            covered_edge.add(inst.edge_id)

        eff_start = inst.billing_effective_start()
        if eff_start is None:
            continue
        end = inst.service_end_date()

        seg_start = max(first, eff_start)
        seg_end = min(last, end) if end else last
        if seg_start > seg_end:
            continue  # 当月未在服务期内

        days_used = (seg_end - seg_start).days + 1
        is_prorated = days_used < days_in_month

        unit_fee = inst.effective_monthly_fee()
        source_currency = inst.effective_fee_currency()

        # 产品定价明细（基础 + 超出）；人工月费/工单报价时归并到基础
        pb = inst.product_billing()
        is_manual = bool(inst.monthly_fee and inst.monthly_fee > 0)
        if pb is not None and not is_manual:
            base_fee = Decimal(str(pb['base_monthly']))
            overage_fee = Decimal(str(pb['overage_monthly']))
            product_id = pb['product_id']
            product_name = pb['product_name']
            basis = 'product'
            spec = pb.get('spec')
            base_spec = pb.get('base_spec')
            breakdown = pb.get('breakdown') or []
        else:
            base_fee = unit_fee
            overage_fee = Decimal('0')
            product_id = None
            product_name = ''
            basis = 'manual' if is_manual else 'quoted'
            spec = None
            base_spec = None
            breakdown = []

        if is_prorated:
            ratio = Decimal(days_used) / Decimal(days_in_month)
        else:
            ratio = Decimal('1')
        amount_original = _q2(unit_fee * ratio)

        item_rates = cs.rates_from_snapshot(
            getattr(inst.service_order, 'fx_snapshot', None),
        ) or rates
        amount_converted = cs.convert(amount_original, source_currency, currency, item_rates)

        # 折算汇率（原始币种 → 结算币种）；有工单快照时不跟出账日实时汇率
        unit_one = cs.convert(Decimal('1'), source_currency, currency, item_rates)

        is_trial = bool(inst.is_trial)
        detail = _build_detail(
            basis=basis, spec=spec, base_spec=base_spec, breakdown=breakdown,
            base_fee=base_fee, overage_fee=overage_fee, unit_fee=unit_fee,
            days_used=days_used, days_in_month=days_in_month, is_prorated=is_prorated,
            ratio=ratio, source_currency=source_currency, amount_original=amount_original,
            currency=currency, exchange_rate=unit_one, amount=amount_converted,
            is_trial=is_trial,
        )
        item = {
            'service_instance': inst.id,
            'instance_no': inst.instance_no,
            'service_type': inst.service_type,
            'description': _instance_label(inst),
            'product_id': product_id,
            'product_name': product_name,
            'period_start': seg_start.isoformat(),
            'period_end': seg_end.isoformat(),
            'days_used': days_used,
            'days_in_month': days_in_month,
            'is_prorated': is_prorated,
            'is_trial': is_trial,
            'unit_monthly_fee': str(_q2(unit_fee)),
            'base_monthly_fee': str(_q2(base_fee)),
            'overage_monthly_fee': str(_q2(overage_fee)),
            'source_currency': source_currency,
            'exchange_rate': str(unit_one),
            'amount_original': str(amount_original),
            'currency': currency,
            'amount': str(amount_converted),
            'detail': detail,
        }
        items.append(item)

        if is_trial:
            trial_total += amount_converted
            trial_count += 1
        else:
            billable_total += amount_converted

    # ── 直接分配给客户、关联了产品、但未被服务实例承载的资源 ──────────
    for item in _customer_resource_items(
        customer, first, last, days_in_month, currency, rates,
        covered_vps, covered_edge,
    ):
        items.append(item)
        billable_total += Decimal(item['amount'])

    from .channel_services import customer_promotion_quote

    promotion = customer_promotion_quote(customer, billable_total)
    return {
        'customer': customer.id,
        'customer_name': customer.name,
        'bill_month': bill_month,
        'currency': currency,
        'currency_symbol': cs.currency_symbol(currency),
        'days_in_month': days_in_month,
        'gross_total': str(promotion['gross_amount']),
        'promotion_discount_amount': str(promotion['discount_amount']),
        'promotion_code': promotion['promotion_code'],
        'billable_total': str(promotion['amount']),
        'trial_total': str(_q2(trial_total)),
        'trial_count': trial_count,
        'item_count': len(items),
        'items': items,
    }


@transaction.atomic
def generate_bill(customer, bill_month: str, user=None, due_days: int = 30,
                  target_currency: str | None = None):
    """根据计费明细生成/更新该客户当月账单（仅计费项计入金额，测试项作为明细保留）。"""
    from .models import (
        BillingItem, CustomerBilling, CustomerOperationLog, record_operation_log,
    )
    from .channel_services import apply_billing_promotion

    result = compute_customer_month(customer, bill_month, target_currency)
    currency = result['currency']
    gross_amount = Decimal(result.get('gross_total', result['billable_total']))

    _, last, _ = month_range(bill_month)
    due_date = last + datetime.timedelta(days=due_days)

    billing = (CustomerBilling.objects.select_for_update()
               .filter(
                   customer=customer,
                   bill_month=bill_month,
                   auto_generated=True,
                   billing_kind=CustomerBilling.BillingKind.MONTHLY,
               )
               .order_by('id')
               .first())
    created = billing is None
    old_value = None
    if billing is None:
        billing = CustomerBilling(
            customer=customer, bill_month=bill_month,
            amount=gross_amount, currency=currency,
            auto_generated=True,
            billing_kind=CustomerBilling.BillingKind.MONTHLY,
            due_date=due_date,
        )
        apply_billing_promotion(billing, gross_amount)
        billing.save()
    else:
        if billing.status == CustomerBilling.BillingStatus.PAID:
            # 已支付的账单不再覆盖
            return billing, result, False
        if billing.payment_transactions.filter(
            status__in=['created', 'pending', 'processing'],
        ).exists():
            raise BillingGenerationBlocked(
                f'账单 [{billing.bill_no}] 存在支付中的在线交易，不能重新生成或覆盖金额',
            )
        old_value = {
            'amount': str(billing.amount),
            'currency': billing.currency,
            'due_date': billing.due_date.isoformat() if billing.due_date else None,
        }
        billing.currency = currency
        billing.due_date = due_date
        apply_billing_promotion(billing, gross_amount)
        billing.save(update_fields=[
            'amount', 'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'channel_referral', 'currency', 'due_date', 'updated_at',
        ])

    # 重建月结明细；带流量报告的超额行不删（即使误挂在月结单上）
    billing.items.filter(traffic_report__isnull=True).delete()
    bulk = []
    for it in result['items']:
        bulk.append(BillingItem(
            billing=billing,
            service_instance_id=it['service_instance'],
            instance_no=it['instance_no'],
            description=it['description'],
            service_type=it['service_type'],
            product_id=it.get('product_id'),
            product_name=it.get('product_name', ''),
            period_start=it['period_start'],
            period_end=it['period_end'],
            days_used=it['days_used'],
            days_in_month=it['days_in_month'],
            is_prorated=it['is_prorated'],
            is_trial=it['is_trial'],
            unit_monthly_fee=Decimal(it['unit_monthly_fee']),
            base_monthly_fee=Decimal(it.get('base_monthly_fee', it['unit_monthly_fee'])),
            overage_monthly_fee=Decimal(it.get('overage_monthly_fee', '0')),
            source_currency=it['source_currency'],
            exchange_rate=Decimal(it['exchange_rate']),
            amount_original=Decimal(it['amount_original']),
            currency=it['currency'],
            amount=Decimal(it['amount']),
            detail=it.get('detail') or {},
        ))
    BillingItem.objects.bulk_create(bulk)
    traffic_total = Decimal('0')
    for leftover in billing.items.exclude(traffic_report_id=None):
        traffic_total += Decimal(str(leftover.amount or 0))
    if traffic_total:
        billing.amount = _q2(Decimal(str(billing.amount or 0)) + traffic_total)
        billing.gross_amount = _q2(Decimal(str(billing.gross_amount or 0)) + traffic_total)
        billing.save(update_fields=['amount', 'gross_amount', 'updated_at'])
    record_operation_log(
        operator=user,
        action_type=(
            CustomerOperationLog.ActionType.CREATE
            if created else CustomerOperationLog.ActionType.UPDATE
        ),
        description=(
            f'{"生成" if created else "重新生成"}自动账单 '
            f'[{billing.bill_no}] {billing.currency}{billing.amount}'
        ),
        customer=customer,
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=billing.pk,
        old_value=old_value,
        new_value={
            'bill_no': billing.bill_no,
            'bill_month': billing.bill_month,
            'amount': str(billing.amount),
            'gross_amount': str(billing.gross_amount),
            'promotion_discount_amount': str(billing.promotion_discount_amount),
            'promotion_code': billing.promotion_code,
            'currency': billing.currency,
            'due_date': billing.due_date.isoformat() if billing.due_date else None,
            'auto_generated': True,
        },
    )
    return billing, result, True


@transaction.atomic
def mark_overdue_billings(today: datetime.date | None = None) -> int:
    """
    将到期日前仍未结清的账单标记为逾期，并写入客户操作日志。

    IM 通知由 CustomerOperationLog 的统一信号在事务提交后按
    ``billing_overdue`` 规则投递；因此不会在任务重试时重复推送。
    """
    from .models import CustomerBilling, CustomerOperationLog, record_operation_log

    today = today or timezone.localdate()
    billings = (
        CustomerBilling.objects.select_for_update()
        .select_related('customer')
        .filter(
            status=CustomerBilling.BillingStatus.UNPAID,
            due_date__lt=today,
        )
    )
    count = 0
    for billing in billings:
        old_value = {
            'status': billing.status,
            'paid_amount': str(billing.paid_amount),
            'due_date': billing.due_date.isoformat(),
        }
        billing.status = CustomerBilling.BillingStatus.OVERDUE
        billing.save(update_fields=['status', 'updated_at'])
        record_operation_log(
            operator=None,
            action_type=CustomerOperationLog.ActionType.STATUS_CHANGE,
            description=(
                f'账单 [{billing.bill_no}] 已逾期：'
                f'到期日 {billing.due_date.isoformat()}，'
                f'未结清 {billing.currency}{billing.outstanding_amount}'
            ),
            customer=billing.customer,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=billing.pk,
            old_value=old_value,
            new_value={
                'status': billing.status,
                'paid_amount': str(billing.paid_amount),
                'due_date': billing.due_date.isoformat(),
                'outstanding_amount': str(billing.outstanding_amount),
            },
        )
        count += 1
    return count
