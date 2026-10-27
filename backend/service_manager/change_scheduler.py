"""到期变更执行器：扫描已预约的降配/变更并落库规格。

升配支付后走 ``apply_paid_change``；降配只写 ``scheduled_at``，需本模块按日扫描。
节点侧 resize / 下发仍属骨架边界：规格落库后可按系统参数开一张跟进工单。
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from system_manager.services import get_setting

from .models import ServiceChangeRecord, SupportTicket
from .support_ticket import OPEN_STATUSES, create_support_ticket

logger = logging.getLogger(__name__)

FOLLOWUP_SETTING_KEY = 'change_followup_ticket'


def followup_ticket_enabled() -> bool:
    return bool(get_setting(FOLLOWUP_SETTING_KEY, True))


def ensure_change_followup_ticket(change: ServiceChangeRecord, *, operator=None, enabled=None):
    """升/降配落库后补一张运维跟进工单（可关）。幂等：同变更号未关闭则复用。"""
    if enabled is None:
        enabled = followup_ticket_enabled()
    if not enabled:
        return None
    if change.change_type not in (
        ServiceChangeRecord.ChangeType.UPGRADE,
        ServiceChangeRecord.ChangeType.DOWNGRADE,
    ):
        return None
    instance = change.service_instance
    if instance is None:
        return None
    order = instance.service_order
    customer = getattr(order, 'customer', None)
    if customer is None:
        return None

    existing = (
        SupportTicket.objects.filter(
            related_instance=instance,
            subject__contains=change.change_no,
            status__in=OPEN_STATUSES,
        )
        .order_by('id')
        .first()
    )
    if existing:
        return existing

    kind = change.get_change_type_display()
    subject = f'变更待节点下发 {change.change_no}（{kind}）'
    description = (
        f'变更 {change.change_no} 的规格与月费已写入档案，节点侧尚未自动 resize / 下发。\n'
        f'请在 Hypervisor 或边缘节点完成调整后关闭本工单。\n'
        f'实例：{instance.instance_no}'
    )
    try:
        return create_support_ticket(
            customer=customer,
            subject=subject,
            description=description,
            category=SupportTicket.Category.TECHNICAL,
            priority=SupportTicket.Priority.P3,
            source=SupportTicket.Source.ADMIN,
            created_by=operator,
            related_instance=instance,
            write_contact=False,
            notify=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning('变更 %s 跟进工单创建失败', change.change_no, exc_info=True)
        return None


def due_scheduled_changes(now=None):
    now = now or timezone.now()
    return (
        ServiceChangeRecord.objects.select_related(
            'service_instance',
            'related_order',
            'service_instance__service_order',
        )
        .filter(
            status__in=(
                ServiceChangeRecord.ChangeStatus.APPROVED,
                ServiceChangeRecord.ChangeStatus.IN_PROGRESS,
            ),
            scheduled_at__isnull=False,
            scheduled_at__lte=now,
        )
        .order_by('scheduled_at', 'id')
    )


def process_due_scheduled_changes(*, now=None, dry_run=False, create_followup_ticket=None) -> dict:
    """执行已到期的预约变更（主要是降配）。"""
    now = now or timezone.now()
    if create_followup_ticket is None:
        create_followup_ticket = followup_ticket_enabled()

    stats = {
        'dry_run': bool(dry_run),
        'scanned': 0,
        'executed': 0,
        'skipped': 0,
        'errors': 0,
        'planned': [],
        'tickets': 0,
    }

    for change in due_scheduled_changes(now):
        stats['scanned'] += 1
        line = (
            f'{change.change_no} {change.get_change_type_display()} '
            f'计划 {change.scheduled_at} 实例 {change.service_instance_id}'
        )
        if dry_run:
            stats['planned'].append(line)
            stats['skipped'] += 1
            continue

        try:
            with transaction.atomic():
                locked = (
                    ServiceChangeRecord.objects.select_for_update()
                    .select_related('related_order', 'service_instance')
                    .get(pk=change.pk)
                )
                if locked.status == ServiceChangeRecord.ChangeStatus.COMPLETED:
                    stats['skipped'] += 1
                    continue
                if locked.scheduled_at and locked.scheduled_at > now:
                    stats['skipped'] += 1
                    continue

                from .workflow import change_unpaid_reason
                unpaid = change_unpaid_reason(locked)
                if unpaid:
                    stats['skipped'] += 1
                    stats['planned'].append(f'{line} 跳过：{unpaid}')
                    continue

                order = locked.related_order
                if order is not None:
                    from .change_provisioning import apply_paid_change
                    outcome = apply_paid_change(
                        order, locked, create_followup_ticket=create_followup_ticket,
                    )
                    if not outcome.get('triggered'):
                        stats['skipped'] += 1
                        stats['planned'].append(
                            f'{line} 未执行：{outcome.get("reason") or "skipped"}'
                        )
                        continue
                    ticket_no = outcome.get('followup_ticket_no')
                else:
                    from .change_executor import execute_change
                    execute_change(locked)
                    ticket = ensure_change_followup_ticket(
                        locked, enabled=create_followup_ticket,
                    )
                    ticket_no = getattr(ticket, 'ticket_no', None)

                if ticket_no:
                    stats['tickets'] += 1
                stats['executed'] += 1
                stats['planned'].append(f'{line} 已执行')
        except Exception as exc:  # noqa: BLE001
            logger.exception('预约变更 %s 执行失败', change.change_no)
            stats['errors'] += 1
            stats['planned'].append(f'{line} 失败：{exc}')

    return stats
