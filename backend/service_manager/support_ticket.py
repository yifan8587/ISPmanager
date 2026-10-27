"""客户技术工单：SLA 计算、CRM 回写、逾期扫描。

知识库、状态页、IM 超时自动转单不在本步实现；
``support_im_timeout_minutes`` 仅预留参数。
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from customer_manager.models import ContactRecord
from system_manager.services import get_setting

from .models import SupportTicket, SupportTicketMessage

logger = logging.getLogger(__name__)

DEFAULT_SLA_HOURS = {
    'p1': {'response': 1, 'resolve': 4},
    'p2': {'response': 4, 'resolve': 24},
    'p3': {'response': 8, 'resolve': 72},
    'p4': {'response': 24, 'resolve': 168},
}

OPEN_STATUSES = (
    SupportTicket.Status.OPEN,
    SupportTicket.Status.IN_PROGRESS,
    SupportTicket.Status.PENDING_CUSTOMER,
    SupportTicket.Status.WAITING,
)
MAX_ESCALATION_LEVEL = 2


def _int_hours(value, fallback: int) -> int:
    try:
        hours = int(value)
    except (TypeError, ValueError):
        hours = fallback
    return max(1, hours)


def get_sla_hours(priority: str) -> tuple[int, int]:
    raw = get_setting('support_sla_hours', DEFAULT_SLA_HOURS)
    if not isinstance(raw, dict):
        raw = DEFAULT_SLA_HOURS
    defaults = DEFAULT_SLA_HOURS.get(priority) or DEFAULT_SLA_HOURS['p3']
    slot = raw.get(priority) if isinstance(raw.get(priority), dict) else defaults
    response = _int_hours(slot.get('response'), defaults['response'])
    resolve = _int_hours(slot.get('resolve'), defaults['resolve'])
    return response, resolve


def compute_sla_dues(priority: str, created_at=None) -> tuple:
    created = created_at or timezone.now()
    response_hours, resolve_hours = get_sla_hours(priority)
    return (
        created + timedelta(hours=response_hours),
        created + timedelta(hours=resolve_hours),
    )


def apply_sla_dues(ticket: SupportTicket, *, save: bool = True) -> SupportTicket:
    created = ticket.created_at or timezone.now()
    ticket.first_response_due, ticket.resolve_due = compute_sla_dues(
        ticket.priority, created,
    )
    if save and ticket.pk:
        ticket.save(update_fields=['first_response_due', 'resolve_due', 'updated_at'])
    return ticket


def write_contact_record(ticket: SupportTicket, summary: str, *, handler=None) -> ContactRecord:
    return ContactRecord.objects.create(
        customer=ticket.customer,
        contact_type=ContactRecord.ContactType.ONLINE,
        contact_time=timezone.now(),
        summary=summary[:2000],
        handler=handler,
    )


def _notify(ticket: SupportTicket, subject: str, *, detail: str = '') -> None:
    from .notify_helpers import notify_ticket_event
    notify_ticket_event(ticket, subject, detail=detail)


def create_support_ticket(
    *,
    customer,
    subject: str,
    description: str,
    category: str = SupportTicket.Category.TECHNICAL,
    priority: str = SupportTicket.Priority.P3,
    source: str = SupportTicket.Source.PORTAL,
    created_by=None,
    handler=None,
    related_instance=None,
    related_inquiry=None,
    write_contact: bool = True,
    notify: bool = True,
) -> SupportTicket:
    if related_instance is not None:
        order = getattr(related_instance, 'service_order', None)
        if order is None or order.customer_id != customer.pk:
            raise ValidationError({'related_instance': '实例不属于该客户'})
    if related_inquiry is not None and SupportTicket.objects.filter(
        related_inquiry=related_inquiry,
    ).exists():
        raise ValidationError({'related_inquiry': '该咨询已转为技术工单'})

    with transaction.atomic():
        ticket = SupportTicket(
            customer=customer,
            created_by=created_by,
            category=category,
            priority=priority,
            status=SupportTicket.Status.OPEN,
            subject=subject.strip(),
            description=description.strip(),
            related_instance=related_instance,
            related_inquiry=related_inquiry,
            source=source,
            handler=handler,
        )
        ticket.save()
        apply_sla_dues(ticket, save=True)
        SupportTicketMessage.objects.create(
            ticket=ticket,
            author_type=(
                SupportTicketMessage.AuthorType.CUSTOMER
                if source == SupportTicket.Source.PORTAL
                else SupportTicketMessage.AuthorType.STAFF
            ),
            is_internal=False,
            content=ticket.description,
            author=created_by,
        )
        if write_contact:
            write_contact_record(
                ticket,
                f'新建技术工单 {ticket.ticket_no}：{ticket.subject}',
                handler=created_by,
            )
    if notify:
        _notify(ticket, '提交工单', detail=ticket.subject)
    return ticket


def add_staff_message(
    ticket: SupportTicket,
    content: str,
    *,
    author,
    is_internal: bool = False,
    status: str | None = None,
    write_contact: bool = True,
    notify: bool = True,
) -> SupportTicketMessage:
    if ticket.status == SupportTicket.Status.CLOSED and not is_internal:
        raise ValidationError('工单已关闭')
    if status and status not in SupportTicket.Status.values:
        raise ValidationError({'status': '无效状态'})

    now = timezone.now()
    with transaction.atomic():
        message = SupportTicketMessage.objects.create(
            ticket=ticket,
            author_type=SupportTicketMessage.AuthorType.STAFF,
            is_internal=is_internal,
            content=content.strip(),
            author=author,
        )
        updates = []
        if not is_internal and ticket.first_responded_at is None:
            ticket.first_responded_at = now
            updates.append('first_responded_at')
        if status:
            ticket.status = status
            updates.append('status')
            if status == SupportTicket.Status.RESOLVED and not ticket.resolved_at:
                ticket.resolved_at = now
                updates.append('resolved_at')
            elif status == SupportTicket.Status.CLOSED:
                ticket.closed_at = now
                updates.append('closed_at')
                if not ticket.resolved_at:
                    ticket.resolved_at = now
                    updates.append('resolved_at')
        elif not is_internal and ticket.status == SupportTicket.Status.OPEN:
            ticket.status = SupportTicket.Status.IN_PROGRESS
            updates.append('status')
        if updates:
            ticket.save(update_fields=[*updates, 'updated_at'])
        if write_contact and not is_internal:
            write_contact_record(
                ticket,
                f'技术工单 {ticket.ticket_no} 客服回复：{content.strip()[:80]}',
                handler=author,
            )
    if notify and not is_internal:
        _notify(ticket, '回复工单', detail=content)
    return message


def add_customer_message(
    ticket: SupportTicket,
    content: str,
    *,
    author,
    write_contact: bool = True,
    notify: bool = True,
) -> SupportTicketMessage:
    if ticket.status == SupportTicket.Status.CLOSED:
        raise ValidationError('工单已关闭，无法继续回复')

    with transaction.atomic():
        message = SupportTicketMessage.objects.create(
            ticket=ticket,
            author_type=SupportTicketMessage.AuthorType.CUSTOMER,
            is_internal=False,
            content=content.strip(),
            author=author,
        )
        if ticket.status in {
            SupportTicket.Status.PENDING_CUSTOMER,
            SupportTicket.Status.WAITING,
            SupportTicket.Status.RESOLVED,
            SupportTicket.Status.OPEN,
        }:
            ticket.status = SupportTicket.Status.IN_PROGRESS
            ticket.save(update_fields=['status', 'updated_at'])
        if write_contact:
            write_contact_record(
                ticket,
                f'技术工单 {ticket.ticket_no} 客户回复：{content.strip()[:80]}',
                handler=author,
            )
    if notify:
        _notify(ticket, '客户回复', detail=content)
    return message


def assign_ticket(ticket: SupportTicket, handler, *, notify: bool = True) -> SupportTicket:
    ticket.handler = handler
    updates = ['handler']
    if ticket.status == SupportTicket.Status.OPEN:
        ticket.status = SupportTicket.Status.IN_PROGRESS
        updates.append('status')
    ticket.save(update_fields=[*updates, 'updated_at'])
    if notify:
        name = handler.get_full_name() or handler.username
        _notify(ticket, '指派处理人', detail=f'已指派给：{name}')
    return ticket


def resolve_ticket(
    ticket: SupportTicket,
    *,
    author=None,
    content: str = '',
    notify: bool = True,
) -> SupportTicket:
    if ticket.status == SupportTicket.Status.CLOSED:
        raise ValidationError('工单已关闭')
    if content.strip() and author is not None:
        add_staff_message(
            ticket,
            content,
            author=author,
            is_internal=False,
            status=SupportTicket.Status.RESOLVED,
            notify=False,
        )
        ticket.refresh_from_db()
    else:
        now = timezone.now()
        ticket.status = SupportTicket.Status.RESOLVED
        if not ticket.resolved_at:
            ticket.resolved_at = now
        ticket.save(update_fields=['status', 'resolved_at', 'updated_at'])
    if notify:
        _notify(ticket, '解决工单')
    return ticket


def close_ticket(
    ticket: SupportTicket,
    *,
    author=None,
    content: str = '',
    notify: bool = True,
) -> SupportTicket:
    if ticket.status == SupportTicket.Status.CLOSED:
        return ticket
    if content.strip() and author is not None:
        add_staff_message(
            ticket,
            content,
            author=author,
            is_internal=False,
            status=SupportTicket.Status.CLOSED,
            notify=False,
        )
        ticket.refresh_from_db()
    else:
        now = timezone.now()
        ticket.status = SupportTicket.Status.CLOSED
        ticket.closed_at = now
        if not ticket.resolved_at:
            ticket.resolved_at = now
        ticket.save(update_fields=['status', 'closed_at', 'resolved_at', 'updated_at'])
    if notify:
        _notify(ticket, '关闭工单')
    return ticket


def escalate_ticket(ticket: SupportTicket, *, notify: bool = True) -> SupportTicket:
    if ticket.escalation_level >= MAX_ESCALATION_LEVEL:
        raise ValidationError('已达最高升级级别')
    ticket.escalation_level += 1
    ticket.escalated_at = timezone.now()
    ticket.save(update_fields=['escalation_level', 'escalated_at', 'updated_at'])
    if notify:
        _notify(ticket, '工单升级', detail=f'升级级别 {ticket.escalation_level}')
    return ticket


def process_support_sla(
    *,
    now=None,
    dry_run: bool = False,
    apply_escalate: bool | None = None,
    customer_id: int | None = None,
) -> dict[str, Any]:
    """扫描开放工单的 SLA；默认只打标并通知，不自动改派。"""
    now = now or timezone.now()
    notify_enabled = bool(get_setting('support_sla_notify_enabled', True))
    if apply_escalate is None:
        apply_escalate = False

    qs = SupportTicket.objects.filter(status__in=OPEN_STATUSES).select_related(
        'customer', 'handler',
    )
    if customer_id:
        qs = qs.filter(customer_id=customer_id)

    stats = {
        'dry_run': dry_run,
        'scanned': 0,
        'response_breached': 0,
        'resolve_breached': 0,
        'escalated': 0,
        'skipped': 0,
        'planned': [],
    }

    for ticket in qs:
        stats['scanned'] += 1
        response_overdue = (
            ticket.first_responded_at is None
            and ticket.first_response_due
            and now > ticket.first_response_due
        )
        resolve_overdue = (
            ticket.is_open
            and ticket.resolve_due
            and now > ticket.resolve_due
        )
        if not response_overdue and not resolve_overdue:
            stats['skipped'] += 1
            continue

        if response_overdue and not ticket.sla_response_breached:
            line = f'{ticket.ticket_no} 响应超时（截止 {ticket.first_response_due}）'
            stats['planned'].append(line)
            if not dry_run:
                ticket.sla_response_breached = True
                ticket.save(update_fields=['sla_response_breached', 'updated_at'])
                if notify_enabled:
                    _notify(ticket, '响应超时')
                stats['response_breached'] += 1
            else:
                stats['response_breached'] += 1

        if resolve_overdue and not ticket.sla_resolve_breached:
            line = f'{ticket.ticket_no} 解决超时（截止 {ticket.resolve_due}）'
            stats['planned'].append(line)
            if not dry_run:
                ticket.sla_resolve_breached = True
                ticket.save(update_fields=['sla_resolve_breached', 'updated_at'])
                if notify_enabled:
                    _notify(ticket, '解决超时')
                stats['resolve_breached'] += 1
            else:
                stats['resolve_breached'] += 1

        if apply_escalate and resolve_overdue and ticket.escalation_level == 0:
            stats['planned'].append(f'{ticket.ticket_no} 升级至级别 1')
            if not dry_run:
                escalate_ticket(ticket, notify=notify_enabled)
            stats['escalated'] += 1

    return stats
