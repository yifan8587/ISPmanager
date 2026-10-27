"""
服务全生命周期阶段推进。

阶段顺序：下单 → 需求确认 → 测试 → 正式使用 → 变更 → 终止
每个阶段记录提交人 / 处理人 / 审批人，以及资源前后与资费快照。
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .models import (
    ServiceLifecycleRecord, ServiceOperationLog, ServiceOrder,
    _log_service, service_snapshot,
)
from .notify_helpers import notify_order_event


STAGE_TITLES = {
    ServiceOrder.LifecycleStage.ORDER: '客户下单',
    ServiceOrder.LifecycleStage.REQUIREMENT: '需求确认',
    ServiceOrder.LifecycleStage.TEST: '测试交付',
    ServiceOrder.LifecycleStage.USAGE: '正式使用',
    ServiceOrder.LifecycleStage.CHANGE: '服务变更',
    ServiceOrder.LifecycleStage.TERMINATION: '服务终止',
}


def fee_snapshot_for(instance=None, order=None) -> dict:
    data = {}
    if order is not None:
        data.update({
            'order_no': order.order_no,
            'quoted_price': str(order.quoted_price) if order.quoted_price is not None else None,
            'billing_cycle': order.billing_cycle,
            'service_start': str(order.service_start) if order.service_start else None,
            'service_end': str(order.service_end) if order.service_end else None,
        })
    if instance is not None:
        data.update({
            'instance_no': instance.instance_no,
            'is_trial': instance.is_trial,
            'monthly_fee': str(instance.effective_monthly_fee()),
            'currency': instance.effective_fee_currency(),
            'billing_start_date': str(instance.billing_start_date) if instance.billing_start_date else None,
            'trial_start': str(instance.trial_start) if instance.trial_start else None,
            'trial_end': str(instance.trial_end) if instance.trial_end else None,
        })
    return data


@transaction.atomic
def ensure_stage(
    order: ServiceOrder,
    stage: str,
    *,
    submitted_by=None,
    handled_by=None,
    approved_by=None,
    status: str = ServiceLifecycleRecord.StageStatus.COMPLETED,
    service_instance=None,
    change_record=None,
    detail: str = '',
    resource_before=None,
    resource_after=None,
    fee_snapshot=None,
    reject_reason: str = '',
    remark: str = '',
    set_current: bool = True,
    notify: bool = True,
) -> ServiceLifecycleRecord:
    """创建或更新指定阶段记录，并可选推进工单 current_stage。"""
    now = timezone.now()
    record = (
        ServiceLifecycleRecord.objects
        .filter(service_order=order, stage=stage, change_record=change_record)
        .order_by('-id')
        .first()
    )
    if record is None:
        record = ServiceLifecycleRecord(
            service_order=order,
            stage=stage,
            title=STAGE_TITLES.get(stage, stage),
        )

    record.service_instance = service_instance or record.service_instance
    record.change_record = change_record or record.change_record
    record.status = status
    record.detail = detail or record.detail
    record.remark = remark or record.remark
    record.reject_reason = reject_reason or record.reject_reason

    if submitted_by and not record.submitted_by_id:
        record.submitted_by = submitted_by
        record.submitted_at = record.submitted_at or now
    if handled_by:
        record.handled_by = handled_by
        record.started_at = record.started_at or now
    if approved_by:
        record.approved_by = approved_by
        record.approved_at = now
    if status in (
        ServiceLifecycleRecord.StageStatus.COMPLETED,
        ServiceLifecycleRecord.StageStatus.REJECTED,
        ServiceLifecycleRecord.StageStatus.SKIPPED,
    ):
        record.completed_at = now

    if resource_before is not None:
        record.resource_before = resource_before
    elif service_instance and not record.resource_before:
        record.resource_before = service_snapshot(service_instance)

    if resource_after is not None:
        record.resource_after = resource_after
    elif (
        service_instance
        and status == ServiceLifecycleRecord.StageStatus.COMPLETED
    ):
        record.resource_after = service_snapshot(service_instance)

    if fee_snapshot is not None:
        record.fee_snapshot = fee_snapshot
    else:
        record.fee_snapshot = fee_snapshot_for(instance=service_instance, order=order)

    record.save()

    if set_current and order.current_stage != stage:
        old_stage = order.current_stage
        order.current_stage = stage
        order.save(update_fields=['current_stage', 'updated_at'])
        _log_service(
            operator=handled_by or approved_by or submitted_by,
            action_type=ServiceOperationLog.ActionType.STAGE_CHANGE,
            description=f'工单 [{order.order_no}] 阶段 {old_stage} → {stage}',
            service_order=order,
            service_instance=service_instance,
            old_value={'stage': old_stage},
            new_value={'stage': stage, 'status': status},
        )
        if notify:
            notify_order_event(
                order, '生命周期阶段变更',
                detail=f'{STAGE_TITLES.get(old_stage, old_stage)} → {STAGE_TITLES.get(stage, stage)}',
                event='service_lifecycle',
            )
    return record


def bootstrap_order_lifecycle(order: ServiceOrder, *, submitted_by=None) -> ServiceLifecycleRecord:
    """工单创建/提交时写入「下单」阶段。"""
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.ORDER,
        submitted_by=submitted_by or order.created_by,
        status=ServiceLifecycleRecord.StageStatus.COMPLETED
        if order.status != ServiceOrder.OrderStatus.DRAFT
        else ServiceLifecycleRecord.StageStatus.IN_PROGRESS,
        detail=order.requirements or '客户提交服务需求',
        fee_snapshot=fee_snapshot_for(order=order),
        set_current=True,
        notify=False,
    )


def mark_requirement_stage(order: ServiceOrder, *, handled_by=None, approved_by=None) -> ServiceLifecycleRecord:
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.REQUIREMENT,
        submitted_by=order.created_by,
        handled_by=handled_by,
        approved_by=approved_by,
        status=ServiceLifecycleRecord.StageStatus.COMPLETED,
        detail='需求确认并通过审批',
        fee_snapshot=fee_snapshot_for(order=order),
    )


def mark_test_stage(order: ServiceOrder, instance, *, handled_by=None) -> ServiceLifecycleRecord:
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.TEST,
        submitted_by=order.created_by,
        handled_by=handled_by,
        service_instance=instance,
        status=ServiceLifecycleRecord.StageStatus.IN_PROGRESS
        if instance.is_trial else ServiceLifecycleRecord.StageStatus.COMPLETED,
        detail='测试开通/试用中' if instance.is_trial else '开通完成（非测试）',
        resource_after=service_snapshot(instance),
        fee_snapshot=fee_snapshot_for(instance=instance, order=order),
    )


def mark_usage_stage(order: ServiceOrder, instance, *, handled_by=None) -> ServiceLifecycleRecord:
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.USAGE,
        submitted_by=order.created_by,
        handled_by=handled_by,
        service_instance=instance,
        status=ServiceLifecycleRecord.StageStatus.COMPLETED,
        detail='正式使用 / 计费中',
        resource_after=service_snapshot(instance),
        fee_snapshot=fee_snapshot_for(instance=instance, order=order),
    )


def mark_change_stage(order, instance, change_record, *, submitted_by=None, handled_by=None,
                      resource_before=None, resource_after=None, status=None) -> ServiceLifecycleRecord:
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.CHANGE,
        submitted_by=submitted_by or change_record.requested_by,
        handled_by=handled_by or change_record.handler or change_record.executed_by,
        approved_by=change_record.approved_by,
        service_instance=instance,
        change_record=change_record,
        status=status or ServiceLifecycleRecord.StageStatus.IN_PROGRESS,
        detail=change_record.change_reason,
        resource_before=resource_before or change_record.before_spec,
        resource_after=resource_after or change_record.after_spec,
        fee_snapshot={
            **fee_snapshot_for(instance=instance, order=order),
            'price_delta': str(change_record.price_delta) if change_record.price_delta is not None else None,
            'change_no': change_record.change_no,
        },
    )


def mark_termination_stage(order, instance, *, handled_by=None, detail='') -> ServiceLifecycleRecord:
    return ensure_stage(
        order,
        ServiceOrder.LifecycleStage.TERMINATION,
        submitted_by=order.created_by,
        handled_by=handled_by or instance.terminated_by,
        service_instance=instance,
        status=ServiceLifecycleRecord.StageStatus.COMPLETED,
        detail=detail or '服务终止，资源已释放',
        resource_after=service_snapshot(instance),
        fee_snapshot=fee_snapshot_for(instance=instance, order=order),
    )
