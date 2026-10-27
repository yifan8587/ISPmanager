"""
POP 容量预占账本（C1 骨架）。

下单锁定库存 → 开通消耗 → 失败 / 驳回 / 未付过期释放。
默认禁止超卖；POP 打开 ``allow_oversell`` 时仍记账本，但允许预占超过剩余容量。
历史无预占工单行为不变。供应商库存合并视图不在本步。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from resource_manager.models import PopPoint
from service_manager.models import PopCapacityReservation, ServiceOrder

logger = logging.getLogger(__name__)

DEFAULT_HOLD_HOURS = 72
SPEC_KEYS = ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count')
RESERVABLE_TYPES = {
    ServiceOrder.OrderType.NEW,
    ServiceOrder.OrderType.UPGRADE,
}


class CapacityReservationError(Exception):
    def __init__(self, message: str, shortages: list[str] | None = None, check: dict | None = None):
        super().__init__(message)
        self.shortages = list(shortages or [])
        self.check = check or {}


def _get_setting(key: str, default: Any) -> Any:
    try:
        from system_manager.services import get_setting
        return get_setting(key, default)
    except Exception:  # noqa: BLE001
        return default


def hold_hours() -> int:
    raw = _get_setting('capacity_reservation_hold_hours', DEFAULT_HOLD_HOURS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_HOLD_HOURS


def empty_spec() -> dict[str, int]:
    return {key: 0 for key in SPEC_KEYS}


def normalize_spec(spec: dict | None) -> dict[str, int]:
    raw = spec or {}
    return {key: max(0, int(raw.get(key) or 0)) for key in SPEC_KEYS}


def spec_is_zero(spec: dict | None) -> bool:
    return all(value <= 0 for value in normalize_spec(spec).values())


def scale_spec(spec: dict | None, quantity: int) -> dict[str, int]:
    qty = max(1, int(quantity or 1))
    return {key: value * qty for key, value in normalize_spec(spec).items()}


def subtract_spec(current: dict | None, used: dict | None) -> dict[str, int]:
    left = normalize_spec(current)
    take = normalize_spec(used)
    return {key: max(0, left[key] - take[key]) for key in SPEC_KEYS}


def spec_for_order(order: ServiceOrder) -> dict[str, int]:
    if order.order_type == ServiceOrder.OrderType.UPGRADE:
        from .change_provisioning import change_capacity_spec
        return normalize_spec(change_capacity_spec(order))
    from .provisioning import build_spec
    return scale_spec(build_spec(order), order.requested_quantity or 1)


def should_reserve(order: ServiceOrder) -> bool:
    if order.order_type not in RESERVABLE_TYPES:
        return False
    if not order.preferred_pop_id:
        return False
    return not spec_is_zero(spec_for_order(order))


def current_held(order: ServiceOrder) -> PopCapacityReservation | None:
    return (
        PopCapacityReservation.objects
        .filter(service_order=order, status=PopCapacityReservation.Status.HELD)
        .order_by('-id')
        .first()
    )


def held_usage(pop, exclude_order=None) -> dict[str, int]:
    queryset = PopCapacityReservation.objects.filter(
        pop_point=pop,
        status=PopCapacityReservation.Status.HELD,
    )
    if exclude_order is not None:
        queryset = queryset.exclude(
            service_order_id=getattr(exclude_order, 'pk', exclude_order),
        )
    agg = queryset.aggregate(
        cpu=Sum('cpu_cores'),
        ram=Sum('ram_mb'),
        disk=Sum('disk_gb'),
        bw=Sum('bandwidth_mbps'),
        ip=Sum('ip_count'),
    )
    return {
        'cpu_cores': agg['cpu'] or 0,
        'ram_mb': agg['ram'] or 0,
        'disk_gb': agg['disk'] or 0,
        'bandwidth_mbps': agg['bw'] or 0,
        'ip_count': agg['ip'] or 0,
    }


def order_is_capacity_pinned(order: ServiceOrder) -> bool:
    """已付款或已进入履约的工单，预占不再因超时释放。"""
    if order.status in (
        ServiceOrder.OrderStatus.PROVISIONING,
        ServiceOrder.OrderStatus.ACTIVE,
    ):
        return True
    if order.auto_provision_status in (
        ServiceOrder.AutoProvisionStatus.PROVISIONING,
        ServiceOrder.AutoProvisionStatus.SUCCESS,
        ServiceOrder.AutoProvisionStatus.PAID_PENDING,
        ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
    ):
        return True
    try:
        from .workflow import related_order_billing
        billing = related_order_billing(order)
    except Exception:  # noqa: BLE001
        billing = None
    if billing is not None and billing.status == billing.BillingStatus.PAID:
        return True
    quoted = order.quoted_price
    if (
        order.status == ServiceOrder.OrderStatus.APPROVED
        and (quoted is None or quoted == 0)
    ):
        return True
    return False


def _apply_spec(reservation: PopCapacityReservation, spec: dict) -> None:
    values = normalize_spec(spec)
    reservation.cpu_cores = values['cpu_cores']
    reservation.ram_mb = values['ram_mb']
    reservation.disk_gb = values['disk_gb']
    reservation.bandwidth_mbps = values['bandwidth_mbps']
    reservation.ip_count = values['ip_count']


def _ensure_capacity(pop, spec: dict, *, exclude_order=None) -> dict:
    from product_manager import services as product_services
    return product_services.check_spec_against_pop(
        pop, spec, exclude_order=exclude_order,
    )


@transaction.atomic
def reserve_for_order(order: ServiceOrder, *, force: bool = False) -> PopCapacityReservation | None:
    """为工单预占 POP 容量。幂等：同一工单已有预占则按新规格刷新。"""
    if order.order_type not in RESERVABLE_TYPES:
        release_reservation(order, reason='工单类型不占用容量')
        return None
    if not order.preferred_pop_id:
        release_reservation(order, reason='工单已取消期望节点')
        return None

    spec = spec_for_order(order)
    if spec_is_zero(spec):
        release_reservation(order, reason='工单规格无需预占')
        return None

    consumed = PopCapacityReservation.objects.filter(
        service_order=order,
        status=PopCapacityReservation.Status.CONSUMED,
    ).exists()
    if consumed and not force:
        return (
            PopCapacityReservation.objects
            .filter(service_order=order, status=PopCapacityReservation.Status.CONSUMED)
            .order_by('-id')
            .first()
        )

    pop = PopPoint.objects.select_for_update().get(pk=order.preferred_pop_id)
    check = _ensure_capacity(pop, spec, exclude_order=order)
    product = getattr(order, 'product', None)
    allow_backorder = bool(getattr(product, 'allow_backorder', False))
    shortages = list(check.get('shortages') or [])
    # 新购库存不足仍允许下单并记账，但标记为待人工交付，支付后不自动开通。
    # 超卖节点维持原策略。升配等非新购在未开超卖/预售时仍拒绝超额预占。
    if not check.get('ok') and not pop.allow_oversell:
        if order.order_type == ServiceOrder.OrderType.NEW:
            _mark_await_manual_stock(order, shortages)
        elif not allow_backorder:
            raise CapacityReservationError(
                '；'.join(shortages) or '节点库存不足，无法预占',
                shortages=shortages,
                check=check,
            )

    reservation = current_held(order)
    now = timezone.now()
    pinned = order_is_capacity_pinned(order)
    expires_at = None if pinned else now + timedelta(hours=hold_hours())

    if reservation is None:
        reservation = PopCapacityReservation(
            pop_point=pop,
            service_order=order,
            status=PopCapacityReservation.Status.HELD,
        )
    elif reservation.pop_point_id != pop.pk:
        reservation.pop_point = pop

    _apply_spec(reservation, spec)
    reservation.status = PopCapacityReservation.Status.HELD
    reservation.released_at = None
    reservation.release_reason = ''
    if reservation.expires_at is None or not pinned:
        reservation.expires_at = expires_at
    if pinned:
        reservation.expires_at = None
    reservation.save()
    return reservation


def _mark_await_manual_stock(order: ServiceOrder, shortages: list[str]) -> None:
    """记下这张新购单是在库存不足时成交的，后续不得自动开通。"""
    snapshot = dict(order.request_snapshot or {})
    delivery = dict(snapshot.get('delivery') or {})
    delivery['await_manual_stock'] = True
    delivery['is_backorder'] = True
    delivery['stock_shortages'] = list(shortages or [])
    snapshot['delivery'] = delivery
    order.request_snapshot = snapshot
    order.save(update_fields=['request_snapshot', 'updated_at'])


def order_awaits_manual_stock(order: ServiceOrder) -> bool:
    delivery = (getattr(order, 'request_snapshot', None) or {}).get('delivery') or {}
    return bool(delivery.get('await_manual_stock'))


def pin_reservation(order: ServiceOrder) -> PopCapacityReservation | None:
    """付款或零金额履约后取消预占超时。"""
    reservation = current_held(order)
    if reservation is None or reservation.expires_at is None:
        return reservation
    reservation.expires_at = None
    reservation.save(update_fields=['expires_at', 'updated_at'])
    return reservation


def consume_reservation(order: ServiceOrder, spec: dict | None = None) -> PopCapacityReservation | None:
    """开通/交付成功后消耗预占。传入 ``spec`` 时按单份扣减，便于数量 > 1。"""
    reservation = current_held(order)
    if reservation is None:
        return None
    now = timezone.now()
    remaining = (
        empty_spec() if spec is None
        else subtract_spec(reservation.spec_dict(), spec)
    )
    if spec_is_zero(remaining):
        reservation.status = PopCapacityReservation.Status.CONSUMED
        reservation.consumed_at = now
        reservation.expires_at = None
        _apply_spec(reservation, remaining)
        reservation.save(update_fields=[
            'status', 'consumed_at', 'expires_at',
            *SPEC_KEYS, 'updated_at',
        ])
        return reservation
    _apply_spec(reservation, remaining)
    reservation.save(update_fields=[*SPEC_KEYS, 'updated_at'])
    return reservation


def release_reservation(order: ServiceOrder, reason: str = '') -> PopCapacityReservation | None:
    reservation = current_held(order)
    if reservation is None:
        return None
    reservation.status = PopCapacityReservation.Status.RELEASED
    reservation.released_at = timezone.now()
    reservation.release_reason = (reason or '释放预占')[:255]
    reservation.expires_at = None
    reservation.save(update_fields=[
        'status', 'released_at', 'release_reason', 'expires_at', 'updated_at',
    ])
    return reservation


def process_expired_reservations(
    *,
    now=None,
    dry_run: bool = False,
    pop_id: int | None = None,
) -> dict:
    """释放超时未付的预占。已付款 / 履约中的工单即使过期也不释放。"""
    now = now or timezone.now()
    queryset = (
        PopCapacityReservation.objects
        .select_related('service_order', 'pop_point')
        .filter(
            status=PopCapacityReservation.Status.HELD,
            expires_at__isnull=False,
            expires_at__lte=now,
        )
    )
    if pop_id:
        queryset = queryset.filter(pop_point_id=pop_id)

    stats = {
        'scanned': 0,
        'released': 0,
        'skipped': 0,
        'dry_run': dry_run,
        'planned': [],
    }
    for reservation in queryset.iterator():
        stats['scanned'] += 1
        order = reservation.service_order
        if order_is_capacity_pinned(order):
            stats['skipped'] += 1
            if not dry_run and reservation.expires_at is not None:
                reservation.expires_at = None
                reservation.save(update_fields=['expires_at', 'updated_at'])
            continue
        line = (
            f'{order.order_no} @ {reservation.pop_point_id} '
            f'到期 {reservation.expires_at}'
        )
        stats['planned'].append(line)
        if dry_run:
            stats['released'] += 1
            continue
        release_reservation(order, reason='未支付预占已超时释放')
        stats['released'] += 1
    return stats


def reservation_payload(reservation: PopCapacityReservation | None) -> dict | None:
    if reservation is None:
        return None
    return {
        'id': reservation.id,
        'status': reservation.status,
        'status_label': reservation.get_status_display(),
        'pop_id': reservation.pop_point_id,
        **reservation.spec_dict(),
        'expires_at': reservation.expires_at,
        'held_at': reservation.held_at,
        'consumed_at': reservation.consumed_at,
        'released_at': reservation.released_at,
        'release_reason': reservation.release_reason,
    }


def latest_reservation(order: ServiceOrder) -> PopCapacityReservation | None:
    items = list(order.capacity_reservations.all())
    if not items:
        return None
    held = next((item for item in items if item.status == PopCapacityReservation.Status.HELD), None)
    return held or max(items, key=lambda item: item.id)
