"""流量归档生成超额账单明细（C3 骨架）。

不写进会被 ``generate_bill()`` 全量重建的月结单，而是独立
``billing_kind=traffic_overage`` 账单。同一报告只出一条明细。
包月 / 超额为 0 / 单价为 0 默认跳过。不出告警、不限速。
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.utils import timezone

from customer_manager.billing_engine import _q2, month_range
from customer_manager.models import BillingItem, CustomerBilling, CustomerOperationLog, record_operation_log
from operation_manager.models import MonitoringTrafficReport
from product_manager.billing_mode import billing_profile, billing_profile_from_order
from product_manager.models import Product
from system_manager import currency_services as cs

logger = logging.getLogger(__name__)

BYTES_PER_GB = Decimal(1024) ** 3
BPS_PER_MBPS = Decimal('1000000')
LOCKED_BILL_STATUSES = {
    CustomerBilling.BillingStatus.PAID,
    CustomerBilling.BillingStatus.REFUNDING,
    CustomerBilling.BillingStatus.REFUNDED,
    CustomerBilling.BillingStatus.CANCELLED,
}
SKIP_LABELS = {
    'already_billed': '该报告已出过超额明细',
    'report_not_ready': '报告尚未算出可用流量数据',
    'no_customer': '报告未绑定可计费客户（如纯 POP 报告）',
    'billing_mode_flat': '产品为包月，不出超额',
    'unknown_billing_mode': '未知计费模式，按包月跳过',
    'no_overage': '未超出承诺带宽或流量包额度',
    'no_overage_price': '超额单价为 0，不出账',
    'error': '出账异常',
}


def _q4(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _as_decimal(value, default='0') -> Decimal:
    if value in (None, ''):
        return Decimal(default)
    return Decimal(str(value))


def _payload(*, status, reason, ok=False, **extra) -> dict:
    data = {
        'ok': ok,
        'status': status,
        'reason': reason,
        'reason_label': SKIP_LABELS.get(reason, reason or ''),
    }
    data.update(extra)
    return data


def _billed_payload(item: BillingItem, *, reason='billed') -> dict:
    billing = item.billing
    return _payload(
        status='billed',
        reason=reason,
        ok=True,
        item_id=item.pk,
        billing_id=billing.pk,
        bill_no=billing.bill_no,
        amount=str(_q2(item.amount)),
        currency=item.currency,
    )


def _report_item(report: MonitoringTrafficReport) -> BillingItem | None:
    try:
        return report.billing_item
    except ObjectDoesNotExist:
        return None


def _local_dt(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def report_bill_month(report: MonitoringTrafficReport) -> str:
    till = _local_dt(report.time_till) or timezone.localtime()
    return till.strftime('%Y-%m')


def _resolve_resource(report: MonitoringTrafficReport):
    if report.vps_id:
        return report.vps, 'vps'
    if report.edge_id:
        return report.edge, 'edge'
    return None, None


def _resolve_instance(resource):
    if resource is None:
        return None
    try:
        return resource.service_instance
    except ObjectDoesNotExist:
        return None


def resolve_traffic_context(report: MonitoringTrafficReport) -> dict:
    resource, kind = _resolve_resource(report)
    instance = _resolve_instance(resource)
    customer = getattr(resource, 'customer', None) if resource is not None else None
    product = getattr(resource, 'product', None) if resource is not None else None
    order = getattr(instance, 'service_order', None) if instance is not None else None
    if customer is None and order is not None:
        customer = order.customer
    if product is None and order is not None:
        product = order.product
    if order is not None:
        profile = billing_profile_from_order(order)
    else:
        profile = billing_profile(product)
    return {
        'resource': resource,
        'kind': kind,
        'instance': instance,
        'customer': customer,
        'product': product,
        'order': order,
        'profile': profile or {},
    }


def _overage_base(*, skip_reason=None, billing_mode, unit_price, unit_label='',
                  measured_mbps=None, measured_gb=None, commit_mbps=None,
                  quota_gb=None, overage_qty=None, amount_original=None) -> dict:
    return {
        'skip_reason': skip_reason,
        'billing_mode': billing_mode,
        'overage_qty': overage_qty if overage_qty is not None else Decimal('0'),
        'amount_original': amount_original if amount_original is not None else Decimal('0.00'),
        'unit_price': unit_price,
        'unit_label': unit_label,
        'measured_mbps': measured_mbps,
        'measured_gb': measured_gb,
        'commit_mbps': commit_mbps,
        'quota_gb': quota_gb,
    }


def compute_traffic_overage(report: MonitoringTrafficReport, profile: dict) -> dict:
    """按产品计费模式计算超额数量与原始金额。不落库。"""
    mode = (profile or {}).get('billing_mode') or Product.BillingMode.FLAT
    unit_price = _as_decimal((profile or {}).get('overage_unit_price'))
    commit = _as_decimal((profile or {}).get('commit_mbps'))
    quota = (profile or {}).get('transfer_quota_gb')
    quota = _as_decimal(quota) if quota not in (None, '') else None

    measured_mbps = None
    measured_gb = None
    overage_qty = Decimal('0')
    unit_label = ''

    if mode == Product.BillingMode.FLAT:
        return _overage_base(
            skip_reason='billing_mode_flat', billing_mode=mode,
            unit_price=unit_price, commit_mbps=commit, quota_gb=quota,
        )
    if mode not in (
        Product.BillingMode.P95,
        Product.BillingMode.AVG,
        Product.BillingMode.TRANSFER_QUOTA,
    ):
        return _overage_base(
            skip_reason='unknown_billing_mode', billing_mode=mode,
            unit_price=unit_price, commit_mbps=commit, quota_gb=quota,
        )

    if report.status in (
        MonitoringTrafficReport.ReportStatus.PENDING,
        MonitoringTrafficReport.ReportStatus.COMPUTING,
        MonitoringTrafficReport.ReportStatus.FAILED,
    ):
        return _overage_base(
            skip_reason='report_not_ready', billing_mode=mode,
            unit_price=unit_price, commit_mbps=commit, quota_gb=quota,
        )

    if mode == Product.BillingMode.P95:
        if report.percentile_bps is None:
            return _overage_base(
                skip_reason='report_not_ready', billing_mode=mode,
                unit_price=unit_price, unit_label='Mbps',
                commit_mbps=commit, quota_gb=quota,
            )
        measured_mbps = _as_decimal(report.percentile_bps) / BPS_PER_MBPS
        overage_qty = max(Decimal('0'), measured_mbps - commit)
        unit_label = 'Mbps'
    elif mode == Product.BillingMode.AVG:
        if report.avg_bps is None:
            return _overage_base(
                skip_reason='report_not_ready', billing_mode=mode,
                unit_price=unit_price, unit_label='Mbps',
                commit_mbps=commit, quota_gb=quota,
            )
        measured_mbps = _as_decimal(report.avg_bps) / BPS_PER_MBPS
        overage_qty = max(Decimal('0'), measured_mbps - commit)
        unit_label = 'Mbps'
    else:
        if report.total_in_bytes is None and report.total_out_bytes is None:
            return _overage_base(
                skip_reason='report_not_ready', billing_mode=mode,
                unit_price=unit_price, unit_label='GB',
                commit_mbps=commit, quota_gb=quota,
            )
        used_bytes = _as_decimal(report.total_in_bytes) + _as_decimal(report.total_out_bytes)
        measured_gb = used_bytes / BYTES_PER_GB
        quota_gb = quota if quota is not None else Decimal('0')
        overage_qty = max(Decimal('0'), measured_gb - quota_gb)
        unit_label = 'GB'

    overage_qty = _q4(overage_qty)
    if overage_qty <= 0:
        skip_reason = 'no_overage'
        amount_original = Decimal('0.00')
    elif unit_price <= 0:
        skip_reason = 'no_overage_price'
        amount_original = Decimal('0.00')
    else:
        amount_original = _q2(overage_qty * unit_price)
        skip_reason = 'no_overage' if amount_original <= 0 else None

    return {
        'skip_reason': skip_reason,
        'billing_mode': mode,
        'overage_qty': overage_qty,
        'amount_original': amount_original,
        'unit_price': unit_price,
        'unit_label': unit_label,
        'measured_mbps': _q4(measured_mbps) if measured_mbps is not None else None,
        'measured_gb': _q4(measured_gb) if measured_gb is not None else None,
        'commit_mbps': commit,
        'quota_gb': int(quota) if quota is not None else None,
    }


def _bill_has_inflight_payment(billing: CustomerBilling) -> bool:
    return billing.payment_transactions.filter(
        status__in=['created', 'pending', 'processing'],
    ).exists()


def _find_open_traffic_bill(customer, bill_month: str, currency: str):
    qs = (
        CustomerBilling.objects.select_for_update()
        .filter(
            customer=customer,
            bill_month=bill_month,
            auto_generated=True,
            billing_kind=CustomerBilling.BillingKind.TRAFFIC_OVERAGE,
            currency=currency,
            status__in=(
                CustomerBilling.BillingStatus.UNPAID,
                CustomerBilling.BillingStatus.OVERDUE,
            ),
        )
        .order_by('id')
    )
    for billing in qs:
        if _bill_has_inflight_payment(billing):
            continue
        if billing.status in LOCKED_BILL_STATUSES:
            continue
        return billing
    return None


def _create_traffic_bill(customer, bill_month: str, currency: str, amount: Decimal):
    _, last, _ = month_range(bill_month)
    billing = CustomerBilling(
        customer=customer,
        bill_month=bill_month,
        amount=amount,
        gross_amount=amount,
        promotion_discount_amount=Decimal('0.00'),
        currency=currency,
        auto_generated=True,
        billing_kind=CustomerBilling.BillingKind.TRAFFIC_OVERAGE,
        due_date=last + datetime.timedelta(days=30),
        remark='流量超额（自动）',
    )
    billing.save()
    return billing


def _resource_label(ctx: dict) -> str:
    resource = ctx.get('resource')
    kind = ctx.get('kind')
    if kind == 'vps' and resource is not None:
        return f'VPS {getattr(resource, "name", "") or getattr(resource, "vps_no", "")}'
    if kind == 'edge' and resource is not None:
        return f'边缘加速 {getattr(resource, "name", "") or getattr(resource, "edge_no", "")}'
    return '流量超额'


def _mode_label(profile: dict, mode: str) -> str:
    return (profile or {}).get('billing_mode_label') or {
        Product.BillingMode.P95: '95 计费',
        Product.BillingMode.AVG: '平均带宽',
        Product.BillingMode.TRANSFER_QUOTA: '流量包',
    }.get(mode, mode)


@transaction.atomic
def generate_traffic_billing_item(report, *, dry_run=False, user=None) -> dict:
    """把一份流量报告写成独立超额账单明细。幂等。"""
    if isinstance(report, int):
        report = MonitoringTrafficReport.objects.select_related(
            'vps', 'vps__product', 'vps__customer',
            'edge', 'edge__product', 'edge__customer',
        ).get(pk=report)
    else:
        report = MonitoringTrafficReport.objects.select_related(
            'vps', 'vps__product', 'vps__customer',
            'edge', 'edge__product', 'edge__customer',
        ).get(pk=report.pk)

    existing = _report_item(report)
    if existing is not None:
        return _billed_payload(existing, reason='already_billed')

    ctx = resolve_traffic_context(report)
    if ctx['customer'] is None:
        return _payload(status='skipped', reason='no_customer')

    computed = compute_traffic_overage(report, ctx['profile'])
    if computed['skip_reason']:
        return _payload(status='skipped', reason=computed['skip_reason'])

    source_currency = (getattr(ctx['product'], 'currency', None) or cs.default_currency()).upper()
    rates = cs.get_rates()
    if ctx['order'] is not None:
        rates = cs.rates_from_snapshot(getattr(ctx['order'], 'fx_snapshot', None)) or rates
    target_currency = (rates.get('default_currency') or cs.CNY).upper()
    amount_original = computed['amount_original']
    amount = cs.convert(amount_original, source_currency, target_currency, rates)
    exchange_rate = cs.convert(Decimal('1'), source_currency, target_currency, rates)

    extra = {
        'overage_qty': str(computed['overage_qty']),
        'unit_label': computed['unit_label'],
        'amount': str(amount),
        'currency': target_currency,
        'billing_mode': computed['billing_mode'],
    }
    if dry_run:
        return _payload(status='planned', reason='billed', ok=True, **extra)

    try:
        billing = _find_open_traffic_bill(ctx['customer'], report_bill_month(report), target_currency)
        created_bill = billing is None
        if billing is None:
            billing = _create_traffic_bill(
                ctx['customer'], report_bill_month(report), target_currency, amount,
            )
        else:
            billing.amount = _q2(_as_decimal(billing.amount) + amount)
            billing.gross_amount = _q2(_as_decimal(billing.gross_amount or 0) + amount)
            billing.save(update_fields=['amount', 'gross_amount', 'updated_at'])

        start = _local_dt(report.time_from)
        till = _local_dt(report.time_till)
        period_start = start.date() if start else None
        period_end = till.date() if till else None
        days_used = 0
        if period_start and period_end and period_end >= period_start:
            days_used = (period_end - period_start).days + 1
        _, _, days_in_month = month_range(report_bill_month(report))
        instance = ctx['instance']
        product = ctx['product']
        mode = computed['billing_mode']
        description = (
            f'流量超额 [{report.report_no}] {_resource_label(ctx)} '
            f'{_mode_label(ctx["profile"], mode)} 超出 '
            f'{computed["overage_qty"]} {computed["unit_label"]}'
        )
        detail = {
            'billing_kind': CustomerBilling.BillingKind.TRAFFIC_OVERAGE,
            'report_no': report.report_no,
            'report_id': report.pk,
            'billing_mode': mode,
            'billing_mode_label': _mode_label(ctx['profile'], mode),
            'measured_mbps': (
                str(computed['measured_mbps']) if computed['measured_mbps'] is not None else None
            ),
            'measured_gb': (
                str(computed['measured_gb']) if computed['measured_gb'] is not None else None
            ),
            'commit_mbps': str(_q4(computed['commit_mbps'])),
            'quota_gb': computed['quota_gb'],
            'overage_qty': str(computed['overage_qty']),
            'overage_unit': computed['unit_label'],
            'overage_unit_price': str(computed['unit_price']),
            'source_currency': source_currency,
            'exchange_rate': str(exchange_rate),
            'amount_original': str(amount_original),
            'amount': str(amount),
            'currency': target_currency,
        }
        item = BillingItem.objects.create(
            billing=billing,
            service_instance=instance,
            instance_no=getattr(instance, 'instance_no', '') or '',
            description=description[:256],
            service_type=ctx['kind'] or '',
            product=product,
            product_name=getattr(product, 'name', '') or '',
            period_start=period_start,
            period_end=period_end,
            days_used=days_used,
            days_in_month=days_in_month,
            is_prorated=False,
            is_trial=False,
            unit_monthly_fee=_q2(computed['unit_price']),
            base_monthly_fee=Decimal('0.00'),
            overage_monthly_fee=amount_original,
            source_currency=source_currency,
            exchange_rate=exchange_rate,
            amount_original=amount_original,
            currency=target_currency,
            amount=amount,
            detail=detail,
            traffic_report=report,
        )
    except IntegrityError:
        existing = BillingItem.objects.filter(traffic_report=report).select_related('billing').first()
        if existing is not None:
            return _billed_payload(existing, reason='already_billed')
        raise

    record_operation_log(
        operator=user,
        action_type=(
            CustomerOperationLog.ActionType.CREATE
            if created_bill else CustomerOperationLog.ActionType.UPDATE
        ),
        description=(
            f'{"生成" if created_bill else "追加"}流量超额账单 '
            f'[{billing.bill_no}] {report.report_no} {target_currency}{amount}'
        ),
        customer=ctx['customer'],
        target_model=CustomerOperationLog.TargetModel.BILLING,
        target_id=billing.pk,
        new_value={
            'bill_no': billing.bill_no,
            'bill_month': billing.bill_month,
            'billing_kind': billing.billing_kind,
            'report_no': report.report_no,
            'amount': str(billing.amount),
            'item_amount': str(amount),
            'currency': target_currency,
        },
    )
    return _billed_payload(item)


def closed_report_cutoff(*, include_open_month=False, now=None):
    """默认可归档截止：当前本地自然月月初。本月窗口留给人工或下月日终。"""
    now = now or timezone.localtime()
    if include_open_month:
        return now
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def archive_closed_traffic_reports(
    *,
    dry_run=False,
    report_id=None,
    include_open_month=False,
) -> dict:
    """把已完成且窗口已关闭的流量报告标成已归档。不生成报告、不调监控。"""
    qs = MonitoringTrafficReport.objects.filter(
        status=MonitoringTrafficReport.ReportStatus.DONE,
    )
    if report_id:
        qs = qs.filter(pk=report_id)
    else:
        qs = qs.filter(time_till__lt=closed_report_cutoff(include_open_month=include_open_month))

    stats = {'scanned': 0, 'archived': 0, 'planned': []}
    ids = list(qs.order_by('id').values_list('pk', flat=True))
    for pk in ids:
        report = MonitoringTrafficReport.objects.get(pk=pk)
        stats['scanned'] += 1
        if dry_run:
            stats['archived'] += 1
            stats['planned'].append(f'{report.report_no} planned_archive')
            continue
        report.status = MonitoringTrafficReport.ReportStatus.ARCHIVED
        report.save(update_fields=['status'])
        stats['archived'] += 1
        stats['planned'].append(f'{report.report_no} archived')
    return stats


def process_traffic_billing_cycle(
    *,
    dry_run=False,
    report_id=None,
    bill_month=None,
    archive=True,
    include_open_month=False,
    user=None,
) -> dict:
    """日终：先归档已关闭窗口的完成报告，再补扫未出账的归档报告。"""
    archive_stats = {'scanned': 0, 'archived': 0, 'planned': []}
    if archive:
        archive_stats = archive_closed_traffic_reports(
            dry_run=dry_run,
            report_id=report_id,
            include_open_month=include_open_month,
        )
    bill_stats = scan_archived_traffic_reports(
        dry_run=dry_run,
        report_id=report_id,
        bill_month=bill_month,
        user=user,
    )
    planned = list(archive_stats.get('planned') or []) + list(bill_stats.get('planned') or [])
    return {
        **bill_stats,
        'archive_scanned': archive_stats.get('scanned') or 0,
        'archived': archive_stats.get('archived') or 0,
        'planned': planned,
    }


def scan_archived_traffic_reports(
    *,
    dry_run=False,
    report_id=None,
    bill_month=None,
    user=None,
) -> dict:
    qs = MonitoringTrafficReport.objects.select_related(
        'vps', 'vps__product', 'vps__customer',
        'edge', 'edge__product', 'edge__customer',
    ).filter(billing_item__isnull=True)
    if report_id:
        qs = qs.filter(pk=report_id)
    else:
        qs = qs.filter(status=MonitoringTrafficReport.ReportStatus.ARCHIVED)
    if bill_month:
        qs = qs.filter(time_till__year=int(bill_month[:4]), time_till__month=int(bill_month[5:7]))

    stats = {
        'scanned': 0,
        'billed': 0,
        'skipped': 0,
        'errors': 0,
        'planned': [],
    }
    for report in qs.order_by('id'):
        stats['scanned'] += 1
        try:
            result = generate_traffic_billing_item(report, dry_run=dry_run, user=user)
        except Exception:  # noqa: BLE001
            logger.exception('流量报告 %s 出账失败', report.report_no)
            stats['errors'] += 1
            stats['planned'].append(f'{report.report_no} error')
            continue
        status = result.get('status')
        if status in ('billed', 'planned'):
            stats['billed'] += 1
        else:
            stats['skipped'] += 1
        stats['planned'].append(
            f'{report.report_no} {status} {result.get("reason")} '
            f'{result.get("bill_no") or ""} {result.get("amount") or ""}'
        )
    return stats


def report_billing_summary(report) -> dict | None:
    item = _report_item(report)
    if item is None:
        return None
    return _billed_payload(item, reason='already_billed')
