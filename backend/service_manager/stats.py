"""
服务管理统计：客户维度 / 时间段 / 资费汇总。
"""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date


def _parse_date_bound(value, *, end=False):
    if not value:
        return None
    if hasattr(value, 'year') and not isinstance(value, str):
        d = value
    else:
        d = parse_date(str(value)[:10])
    if not d:
        return None
    if end:
        return timezone.make_aware(datetime.combine(d, time.max))
    return timezone.make_aware(datetime.combine(d, time.min))


def apply_time_filters(qs, *, start=None, end=None, field='created_at'):
    start_dt = _parse_date_bound(start, end=False)
    end_dt = _parse_date_bound(end, end=True)
    if start_dt:
        qs = qs.filter(**{f'{field}__gte': start_dt})
    if end_dt:
        qs = qs.filter(**{f'{field}__lte': end_dt})
    return qs


def service_overview(*, customer_id=None, start=None, end=None) -> dict:
    from .models import (
        ServiceChangeRecord, ServiceInstance, ServiceLifecycleRecord, ServiceOrder,
    )

    orders = ServiceOrder.objects.all()
    instances = ServiceInstance.objects.select_related('service_order')
    changes = ServiceChangeRecord.objects.select_related('service_instance__service_order')
    stages = ServiceLifecycleRecord.objects.select_related('service_order')

    if customer_id:
        orders = orders.filter(customer_id=customer_id)
        instances = instances.filter(service_order__customer_id=customer_id)
        changes = changes.filter(service_instance__service_order__customer_id=customer_id)
        stages = stages.filter(service_order__customer_id=customer_id)

    orders = apply_time_filters(orders, start=start, end=end)
    instances = apply_time_filters(instances, start=start, end=end)
    changes = apply_time_filters(changes, start=start, end=end)
    stages = apply_time_filters(stages, start=start, end=end)

    by_status = dict(orders.values_list('status').annotate(c=Count('id')).order_by())
    by_stage = dict(orders.values_list('current_stage').annotate(c=Count('id')).order_by())
    by_type = dict(orders.values_list('order_type').annotate(c=Count('id')).order_by())
    by_business = dict(orders.values_list('business_type').annotate(c=Count('id')).order_by())
    instance_by_status = dict(instances.values_list('status').annotate(c=Count('id')).order_by())

    # 资费：活跃实例月费合计 + 工单报价 + 变更差额
    active_instances = instances.filter(status=ServiceInstance.InstanceStatus.ACTIVE)
    monthly_fee_total = Decimal('0')
    trial_count = 0
    billing_count = 0
    fee_by_currency: dict[str, Decimal] = {}
    for inst in active_instances.iterator():
        fee = inst.effective_monthly_fee()
        cur = inst.effective_fee_currency()
        monthly_fee_total += fee
        fee_by_currency[cur] = fee_by_currency.get(cur, Decimal('0')) + fee
        if inst.is_trial:
            trial_count += 1
        else:
            billing_count += 1

    quoted_total = orders.aggregate(t=Sum('quoted_price'))['t'] or Decimal('0')
    change_delta = changes.filter(
        status=ServiceChangeRecord.ChangeStatus.COMPLETED,
    ).aggregate(t=Sum('price_delta'))['t'] or Decimal('0')

    # 客户维度汇总
    customer_rows = list(
        orders.values('customer_id', 'customer__name', 'customer__customer_no')
        .annotate(
            order_count=Count('id'),
            quoted_sum=Sum('quoted_price'),
            active_orders=Count('id', filter=Q(status='active')),
        )
        .order_by('-order_count')[:50]
    )

    # 按日趋势
    daily = list(
        orders.annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    monthly = list(
        orders.annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(count=Count('id'), quoted=Sum('quoted_price'))
        .order_by('month')
    )

    stage_records = dict(stages.values_list('stage').annotate(c=Count('id')).order_by())

    return {
        'filters': {
            'customer_id': int(customer_id) if customer_id else None,
            'start': start,
            'end': end,
        },
        'orders': {
            'total': orders.count(),
            'by_status': by_status,
            'by_stage': by_stage,
            'by_order_type': by_type,
            'by_business_type': by_business,
            'total_quoted_price': float(quoted_total),
        },
        'instances': {
            'total': instances.count(),
            'by_status': instance_by_status,
            'active_count': instance_by_status.get('active', 0),
            'trial_count': trial_count,
            'billing_count': billing_count,
        },
        'changes': {
            'total': changes.count(),
            'completed': changes.filter(status='completed').count(),
            'pending': changes.filter(status='pending').count(),
            'price_delta_total': float(change_delta),
        },
        'fees': {
            'active_monthly_fee_total': float(monthly_fee_total),
            'by_currency': {k: float(v) for k, v in fee_by_currency.items()},
            'quoted_total': float(quoted_total),
            'change_price_delta_total': float(change_delta),
            'estimated_receivable_hint': float(monthly_fee_total),
        },
        'lifecycle_stage_records': stage_records,
        'customers': [
            {
                'customer_id': r['customer_id'],
                'customer_name': r['customer__name'],
                'customer_no': r['customer__customer_no'],
                'order_count': r['order_count'],
                'active_orders': r['active_orders'],
                'quoted_sum': float(r['quoted_sum'] or 0),
            }
            for r in customer_rows
        ],
        'trends': {
            'daily_orders': [
                {'date': r['day'].isoformat() if r['day'] else None, 'count': r['count']}
                for r in daily
            ],
            'monthly_orders': [
                {
                    'month': r['month'].strftime('%Y-%m') if r['month'] else None,
                    'count': r['count'],
                    'quoted': float(r['quoted'] or 0),
                }
                for r in monthly
            ],
        },
    }


def _orders_for_stats(*, start=None, end=None):
    from .models import ServiceOrder
    return apply_time_filters(
        ServiceOrder.objects.select_related('customer', 'preferred_pop'),
        start=start, end=end,
    )


def stats_by_customer(*, start=None, end=None) -> list[dict]:
    rows = (
        _orders_for_stats(start=start, end=end)
        .values('customer_id', 'customer__customer_no', 'customer__name')
        .annotate(
            service_count=Count('id'),
            active_count=Count('id', filter=Q(status='active')),
            testing_count=Count('id', filter=Q(current_stage='test')),
            quoted_total=Sum('quoted_price'),
        )
        .order_by('-service_count', 'customer__name')
    )
    return [{
        'customer_id': row['customer_id'],
        'customer_no': row['customer__customer_no'],
        'customer_name': row['customer__name'],
        'service_count': row['service_count'],
        'active_count': row['active_count'],
        'testing_count': row['testing_count'],
        'quoted_total': float(row['quoted_total'] or 0),
    } for row in rows]


def stats_by_pop(*, start=None, end=None) -> list[dict]:
    rows = (
        _orders_for_stats(start=start, end=end)
        .values('preferred_pop_id', 'preferred_pop__code', 'preferred_pop__name')
        .annotate(
            service_count=Count('id'),
            active_count=Count('id', filter=Q(status='active')),
            quoted_total=Sum('quoted_price'),
        )
        .order_by('-service_count', 'preferred_pop__name')
    )
    return [{
        'pop_id': row['preferred_pop_id'],
        'pop_code': row['preferred_pop__code'] or '',
        'pop_name': row['preferred_pop__name'] or '未指定 POP',
        'service_count': row['service_count'],
        'active_count': row['active_count'],
        'quoted_total': float(row['quoted_total'] or 0),
    } for row in rows]


def stats_by_type(*, start=None, end=None) -> list[dict]:
    rows = (
        _orders_for_stats(start=start, end=end)
        .values('business_type')
        .annotate(
            service_count=Count('id'),
            active_count=Count('id', filter=Q(status='active')),
            quoted_total=Sum('quoted_price'),
        )
        .order_by('business_type')
    )
    return [{
        'business_type': row['business_type'],
        'service_count': row['service_count'],
        'active_count': row['active_count'],
        'quoted_total': float(row['quoted_total'] or 0),
    } for row in rows]
