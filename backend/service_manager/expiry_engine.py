"""
实例到期催缴 / 回收。

正式服务到期前 3 天、测试服务到期前 1 天通知客户和管理后台。
未续费或未转正式时，到期次日收回 VPS、隧道、IP 和带宽。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from service_manager.models import ServiceExpiryAction, ServiceInstance
from service_manager.notify_helpers import notify_service_event

logger = logging.getLogger(__name__)

OPERATOR_NAME = '到期引擎'
DEFAULT_GRACE_DAYS = 1
FORMAL_REMINDER_DAYS = 3
TRIAL_REMINDER_DAYS = 1
DEFAULT_REMINDER_DAYS = (FORMAL_REMINDER_DAYS, TRIAL_REMINDER_DAYS)

_TERMINAL_STATUSES = {
    ServiceInstance.InstanceStatus.TERMINATED,
}
_SUSPEND_STATUSES = {
    ServiceInstance.InstanceStatus.ACTIVE,
}
_RECYCLE_STATUSES = {
    ServiceInstance.InstanceStatus.ACTIVE,
    ServiceInstance.InstanceStatus.SUSPENDED,
    ServiceInstance.InstanceStatus.EXPIRED,
}


def _get_setting(key: str, default: Any) -> Any:
    try:
        from system_manager.services import get_setting
        return get_setting(key, default)
    except Exception:  # noqa: BLE001
        return default


def parse_reminder_days(raw: Any = None) -> list[int]:
    if raw is None:
        raw = _get_setting('instance_reminder_days', '7,3,1')
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = str(raw).replace('，', ',').split(',')
    days: list[int] = []
    for item in values:
        try:
            day = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if day > 0 and day not in days:
            days.append(day)
    return days or list(DEFAULT_REMINDER_DAYS)


def default_grace_days() -> int:
    raw = _get_setting('instance_grace_days', DEFAULT_GRACE_DAYS)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_GRACE_DAYS


def effective_grace_days(instance: ServiceInstance) -> int:
    if instance.grace_days is not None:
        return max(0, int(instance.grace_days))
    return default_grace_days()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def auto_suspend_enabled() -> bool:
    return _as_bool(_get_setting('instance_auto_suspend_on_expire', False), False)


def auto_recycle_enabled() -> bool:
    return _as_bool(_get_setting('instance_auto_recycle_after_grace', True), True)


def reminder_lead_days(instance: ServiceInstance) -> int:
    """正式服务提前 3 天，测试服务提前 1 天。"""
    return TRIAL_REMINDER_DAYS if instance.is_trial else FORMAL_REMINDER_DAYS


def apply_schedule_to_instance(instance: ServiceInstance, *, force: bool = False) -> bool:
    """按到期日与宽限回填计划停机日 / 回收日。``force`` 时覆盖已有日程。"""
    if not instance.expire_time:
        return False
    days = effective_grace_days(instance)
    expected_suspend = instance.expire_time
    expected_recycle = instance.expire_time + timedelta(days=days)
    changed = False
    if force or not instance.suspend_at:
        if instance.suspend_at != expected_suspend:
            instance.suspend_at = expected_suspend
            changed = True
    if force or not instance.recycle_at:
        if instance.recycle_at != expected_recycle:
            instance.recycle_at = expected_recycle
            changed = True
    return changed


def backfill_expire_time_from_resource(instance: ServiceInstance) -> bool:
    if instance.expire_time:
        return False
    resource = instance.vps or instance.edge
    expire = getattr(resource, 'expire_time', None) if resource is not None else None
    if not expire:
        return False
    instance.expire_time = expire
    return True


def _format_dt(value: datetime | None) -> str:
    if not value:
        return ''
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')


def _expire_date(instance: ServiceInstance, now: datetime):
    if not instance.expire_time:
        return None
    expire = instance.expire_time
    if timezone.is_naive(expire):
        expire = timezone.make_aware(expire, timezone.get_current_timezone())
    return timezone.localtime(expire).date()


def _days_left(instance: ServiceInstance, now: datetime) -> int | None:
    expire_date = _expire_date(instance, now)
    if expire_date is None:
        return None
    return (expire_date - timezone.localdate(now)).days


def _action_label(action: str, lead_days: int = 0) -> str:
    if action == ServiceExpiryAction.Action.REMIND:
        return f'到期前 {lead_days} 天催缴'
    return {
        ServiceExpiryAction.Action.EXPIRED: '服务已到期',
        ServiceExpiryAction.Action.SUSPEND: '到期停机',
        ServiceExpiryAction.Action.RECYCLE: '宽限后回收',
    }.get(action, action)


def build_expiry_context(
    instance: ServiceInstance,
    *,
    action: str,
    lead_days: int = 0,
    days_left: int | None = None,
    detail: str = '',
) -> dict[str, Any]:
    customer = instance.service_order.customer
    order = instance.service_order
    return {
        'module': '服务履约',
        'action': action,
        'action_label': _action_label(action, lead_days),
        'customer_name': customer.name,
        'customer_no': getattr(customer, 'customer_no', '') or '',
        'customer_email': getattr(customer, 'contact_email', '') or '',
        'instance_no': instance.instance_no,
        'order_no': order.order_no,
        'service_type': instance.get_service_type_display(),
        'service_status': instance.get_status_display(),
        'target_type': '服务实例',
        'target_name': instance.instance_no,
        'target_id': instance.pk,
        'expire_time': _format_dt(instance.expire_time),
        'grace_days': effective_grace_days(instance),
        'suspend_at': _format_dt(instance.suspend_at),
        'recycle_at': _format_dt(instance.recycle_at),
        'is_trial': bool(instance.is_trial),
        'days_left': '' if days_left is None else days_left,
        'portal_hint': (
            '请到门户「我的服务」转为正式使用并完成支付'
            if instance.is_trial
            else '请到门户「我的服务」续费并完成支付'
        ),
        'operator_name': OPERATOR_NAME,
        'detail': detail or _action_label(action, lead_days),
        'occurred_at': timezone.now(),
    }


def _record_action(
    instance: ServiceInstance,
    action: str,
    *,
    due_on,
    lead_days: int = 0,
    payload: dict | None = None,
    dry_run: bool = False,
) -> bool:
    if dry_run:
        return True
    try:
        with transaction.atomic():
            _, created = ServiceExpiryAction.objects.get_or_create(
                instance=instance,
                action=action,
                due_on=due_on,
                lead_days=lead_days,
                defaults={'payload': payload or {}},
            )
        return created
    except IntegrityError:
        return False


def _resource_summary(instance: ServiceInstance) -> str:
    parts = []
    if instance.vps_id and instance.vps:
        parts.append(f'VPS {instance.vps.vps_no or instance.vps.name}')
    if instance.edge_id and instance.edge:
        parts.append(f'隧道 {instance.edge.edge_no or instance.edge.name}')
    if instance.bandwidth_alloc_id and instance.bandwidth_alloc:
        parts.append(f'带宽 {instance.bandwidth_alloc.allocated_mbps}Mbps')
    addresses = [str(ip.address) for ip in instance.ip_addresses.all() if ip.address]
    if addresses:
        parts.append('IP ' + '、'.join(addresses))
    return '，'.join(parts)


def _notify(instance: ServiceInstance, context: dict[str, Any]) -> None:
    notify_service_event('service_expiry', context=context)


def _persist_schedule(instance: ServiceInstance, fields: list[str], dry_run: bool) -> None:
    if dry_run or not fields:
        return
    instance.save(update_fields=[*fields, 'updated_at'])


def process_instance_expiry(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    apply_suspend: bool | None = None,
    apply_recycle: bool | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """扫描未终止实例，催缴并按开关执行停机 / 回收。"""
    now = now or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())
    reminder_days = parse_reminder_days()
    do_suspend = auto_suspend_enabled() if apply_suspend is None else bool(apply_suspend)
    do_recycle = auto_recycle_enabled() if apply_recycle is None else bool(apply_recycle)

    stats: dict[str, Any] = {
        'now': now.isoformat(),
        'dry_run': dry_run,
        'apply_suspend': do_suspend,
        'apply_recycle': do_recycle,
        'reminder_days': reminder_days,
        'scanned': 0,
        'expire_backfilled': 0,
        'schedule_updated': 0,
        'reminded': 0,
        'expired': 0,
        'suspended': 0,
        'recycled': 0,
        'skipped': 0,
        'errors': 0,
        'planned': [],
    }

    qs = ServiceInstance.objects.exclude(
        status__in=_TERMINAL_STATUSES,
    ).select_related(
        'service_order__customer', 'vps', 'edge',
    ).order_by('id')
    if customer_id:
        qs = qs.filter(service_order__customer_id=customer_id)

    for instance in qs.iterator():
        stats['scanned'] += 1
        try:
            planned = _process_one(
                instance,
                now=now,
                dry_run=dry_run,
                do_suspend=do_suspend,
                do_recycle=do_recycle,
                reminder_days=reminder_days,
                stats=stats,
            )
            if planned:
                stats['planned'].extend(planned)
        except Exception:  # noqa: BLE001
            stats['errors'] += 1
            logger.exception('实例到期处理失败 instance=%s', instance.pk)
    return stats


def _process_one(
    instance: ServiceInstance,
    *,
    now: datetime,
    dry_run: bool,
    do_suspend: bool,
    do_recycle: bool,
    reminder_days: list[int],
    stats: dict[str, Any],
) -> list[str]:
    planned: list[str] = []
    changed_fields: list[str] = []

    if backfill_expire_time_from_resource(instance):
        changed_fields.append('expire_time')
        stats['expire_backfilled'] += 1
        planned.append(f'{instance.instance_no}: 回填到期日')

    if apply_schedule_to_instance(instance, force=instance.grace_days is None):
        if instance.suspend_at:
            changed_fields.append('suspend_at')
        if instance.recycle_at:
            changed_fields.append('recycle_at')
        # 去重保持顺序
        changed_fields = list(dict.fromkeys(changed_fields))
        stats['schedule_updated'] += 1
        planned.append(f'{instance.instance_no}: 回填停机/回收日程')

    _persist_schedule(instance, changed_fields, dry_run)

    if not instance.expire_time:
        stats['skipped'] += 1
        return planned

    due_on = _expire_date(instance, now)
    days_left = _days_left(instance, now)
    if due_on is None or days_left is None:
        stats['skipped'] += 1
        return planned

    lead = reminder_lead_days(instance)
    if days_left == lead:
        if instance.is_trial:
            remind_detail = (
                f'测试服务将于 {_format_dt(instance.expire_time)} 到期。'
                '请到门户「我的服务」转为正式使用并完成支付；'
                '支付完成后写入正式开通日和到期日。'
                '若未转正，到期次日收回 VPS、隧道、IP 和带宽。'
            )
        else:
            remind_detail = (
                f'服务将于 {_format_dt(instance.expire_time)} 到期。'
                '请到门户「我的服务」续费并完成支付，支付成功后到期日自动顺延。'
                '若未续费，到期次日收回 VPS、隧道、IP 和带宽。'
            )
        if _emit(
            instance,
            ServiceExpiryAction.Action.REMIND,
            due_on=due_on,
            lead_days=days_left,
            days_left=days_left,
            dry_run=dry_run,
            detail=remind_detail,
        ):
            stats['reminded'] += 1
            planned.append(f'{instance.instance_no}: T-{days_left} 催缴')

    if days_left <= 0:
        if _emit(
            instance,
            ServiceExpiryAction.Action.EXPIRED,
            due_on=due_on,
            days_left=days_left,
            dry_run=dry_run,
            detail=f'服务已于 {_format_dt(instance.expire_time)} 到期。',
        ):
            stats['expired'] += 1
            planned.append(f'{instance.instance_no}: 已到期通知')

        if (
            do_suspend
            and instance.suspend_at
            and now >= instance.suspend_at
            and instance.status in _SUSPEND_STATUSES
        ):
            if _record_action(
                instance, ServiceExpiryAction.Action.SUSPEND,
                due_on=due_on, dry_run=dry_run,
                payload={'days_left': days_left},
            ):
                if not dry_run:
                    instance.suspend(reason='到期自动停机')
                    _notify(instance, build_expiry_context(
                        instance,
                        action=ServiceExpiryAction.Action.SUSPEND,
                        days_left=days_left,
                        detail=f'已按到期日暂停实例 {instance.instance_no}。',
                    ))
                stats['suspended'] += 1
                planned.append(f'{instance.instance_no}: 到期停机')
                instance.refresh_from_db()

        if (
            do_recycle
            and instance.recycle_at
            and now >= instance.recycle_at
            and instance.status in _RECYCLE_STATUSES
        ):
            if _record_action(
                instance, ServiceExpiryAction.Action.RECYCLE,
                due_on=due_on, dry_run=dry_run,
                payload={'days_left': days_left},
            ):
                if not dry_run:
                    released = _resource_summary(instance)
                    instance.terminate(reason='到期未续费，次日自动收回资源')
                    kind = '测试服务' if instance.is_trial else '正式服务'
                    _notify(instance, build_expiry_context(
                        instance,
                        action=ServiceExpiryAction.Action.RECYCLE,
                        days_left=days_left,
                        detail=(
                            f'{kind} {instance.instance_no} 到期未续费，已收回资源：'
                            f'{released or "无绑定资源"}。'
                        ),
                    ))
                stats['recycled'] += 1
                planned.append(f'{instance.instance_no}: 宽限回收')

    return planned


def _emit(
    instance: ServiceInstance,
    action: str,
    *,
    due_on,
    lead_days: int = 0,
    days_left: int | None = None,
    dry_run: bool,
    detail: str,
) -> bool:
    context = build_expiry_context(
        instance,
        action=action,
        lead_days=lead_days,
        days_left=days_left,
        detail=detail,
    )
    if not _record_action(
        instance, action,
        due_on=due_on, lead_days=lead_days,
        payload={'days_left': days_left, 'detail': detail},
        dry_run=dry_run,
    ):
        return False
    if not dry_run:
        _notify(instance, context)
    return True
