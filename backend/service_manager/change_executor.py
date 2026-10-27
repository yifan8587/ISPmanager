"""
服务变更执行器：审批通过后真正落库资源规格，并记录变更前后快照。
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    ServiceAllocation, ServiceChangeRecord, ServiceInstance,
    ServiceLifecycleRecord, ServiceOperationLog, _log_service, service_snapshot,
)
from .lifecycle import mark_change_stage, mark_termination_stage, mark_usage_stage
from .notify_helpers import notify_change_event
from .hooks import dispatch_provision_hooks


class ChangeExecutionError(Exception):
    pass


def _apply_spec_to_instance(instance: ServiceInstance, after_spec: dict) -> None:
    """按 after_spec 更新绑定资源。未知字段忽略。"""
    after_spec = after_spec or {}
    vps_spec = after_spec.get('vps') or after_spec
    edge_spec = after_spec.get('edge') or after_spec
    bw_spec = after_spec.get('bandwidth') or after_spec

    if instance.vps_id and instance.vps:
        vps = instance.vps
        update_fields = ['updated_at']
        for src, dst in (
            ('vcpu', 'vcpu'),
            ('cpu_cores', 'vcpu'),
            ('ram_mb', 'ram_mb'),
            ('disk_gb', 'disk_gb'),
            ('bandwidth_mbps', 'bandwidth_mbps'),
        ):
            if src in vps_spec and vps_spec[src] is not None:
                setattr(vps, dst, int(vps_spec[src]))
                if dst not in update_fields:
                    update_fields.append(dst)
        if len(update_fields) > 1:
            vps.save(update_fields=update_fields)

    if instance.edge_id and instance.edge:
        edge = instance.edge
        update_fields = ['updated_at']
        if 'bandwidth_mbps' in edge_spec and edge_spec['bandwidth_mbps'] is not None:
            edge.bandwidth_mbps = int(edge_spec['bandwidth_mbps'])
            update_fields.append('bandwidth_mbps')
        if 'protocol' in edge_spec and edge_spec['protocol']:
            edge.protocol = edge_spec['protocol']
            update_fields.append('protocol')
        if len(update_fields) > 1:
            edge.save(update_fields=update_fields)

    if instance.bandwidth_alloc_id and instance.bandwidth_alloc:
        bw = instance.bandwidth_alloc
        if 'allocated_mbps' in bw_spec and bw_spec['allocated_mbps'] is not None:
            bw.allocated_mbps = int(bw_spec['allocated_mbps'])
            bw.save(update_fields=['allocated_mbps', 'updated_at'])
        elif 'bandwidth_mbps' in bw_spec and bw_spec['bandwidth_mbps'] is not None:
            bw.allocated_mbps = int(bw_spec['bandwidth_mbps'])
            bw.save(update_fields=['allocated_mbps', 'updated_at'])

    # 月费调整
    if 'monthly_fee' in after_spec and after_spec['monthly_fee'] is not None:
        instance.monthly_fee = Decimal(str(after_spec['monthly_fee']))
        instance.save(update_fields=['monthly_fee', 'updated_at'])
    elif 'price_delta' in after_spec and after_spec['price_delta'] is not None:
        delta = Decimal(str(after_spec['price_delta']))
        instance.monthly_fee = (instance.monthly_fee or Decimal('0')) + delta
        instance.save(update_fields=['monthly_fee', 'updated_at'])


@transaction.atomic
def execute_change(record: ServiceChangeRecord, *, operator=None, apply_resources: bool = True) -> ServiceChangeRecord:
    """
    执行变更：
      1. 拍摄变更前快照
      2. 按 after_spec 更新资源（可关闭）
      3. 写入分配记录 / 生命周期 / 通知 / 外部钩子
    """
    if record.status not in (
        ServiceChangeRecord.ChangeStatus.APPROVED,
        ServiceChangeRecord.ChangeStatus.IN_PROGRESS,
    ):
        raise ChangeExecutionError('变更必须处于已审批或进行中状态才能执行')

    from .workflow import change_unpaid_reason

    unpaid = change_unpaid_reason(record)
    if unpaid:
        raise ChangeExecutionError(unpaid)

    instance = record.service_instance
    before = service_snapshot(instance)
    if not record.before_spec:
        record.before_spec = before

    record.status = ServiceChangeRecord.ChangeStatus.IN_PROGRESS
    record.handler = record.handler or operator
    record.save(update_fields=['status', 'handler', 'before_spec', 'updated_at'])

    # 续期：仅调整到期时间
    after_spec = dict(record.after_spec or {})
    if record.change_type == ServiceChangeRecord.ChangeType.EXTEND:
        expire = after_spec.get('expire_time')
        if expire:
            from django.utils.dateparse import parse_datetime
            dt = parse_datetime(expire) if isinstance(expire, str) else expire
            if dt:
                from service_manager.expiry_engine import apply_schedule_to_instance
                from service_manager.renew_billing import sync_resource_expire_time

                instance.expire_time = dt
                apply_schedule_to_instance(instance, force=True)
                instance.save(update_fields=['expire_time', 'suspend_at', 'recycle_at', 'updated_at'])
                sync_resource_expire_time(instance)
    elif record.change_type == ServiceChangeRecord.ChangeType.OTHER and after_spec.get('terminate'):
        instance.terminate(operator=operator, reason=record.change_reason)
        after = service_snapshot(instance)
        record.after_spec = after
        record.status = ServiceChangeRecord.ChangeStatus.COMPLETED
        record.executed_at = timezone.now()
        record.executed_by = operator
        if record.price_delta is None and 'price_delta' in after_spec:
            record.price_delta = Decimal(str(after_spec['price_delta']))
        record.save()
        mark_termination_stage(instance.service_order, instance, handled_by=operator,
                               detail=record.change_reason)
        notify_change_event(record, '变更已执行（终止）')
        dispatch_provision_hooks('terminate', order=instance.service_order, instance=instance, change=record)
        return record
    elif apply_resources:
        _apply_spec_to_instance(instance, after_spec)
        try:
            from .change_provisioning import refresh_instance_spec_snapshot
            refresh_instance_spec_snapshot(instance)
            instance.refresh_from_db()
        except Exception:  # noqa: BLE001
            pass

    after = service_snapshot(instance)
    # 合并业务意图规格到 after_spec，保留执行结果
    merged_after = {**after_spec, **after}
    record.after_spec = merged_after
    record.status = ServiceChangeRecord.ChangeStatus.COMPLETED
    record.executed_at = timezone.now()
    record.executed_by = operator
    if record.price_delta is None and after_spec.get('price_delta') is not None:
        record.price_delta = Decimal(str(after_spec['price_delta']))
    record.save()

    ServiceAllocation.objects.create(
        service_instance=instance,
        action_type=ServiceAllocation.ActionType.MODIFY,
        description=f'执行变更 {record.change_no}：{record.get_change_type_display()}',
        before_state=before,
        after_state=after,
        operator=operator,
        remark=record.change_reason,
    )
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.CHANGE,
        description=f'执行变更 [{record.change_no}] {record.get_change_type_display()}',
        service_instance=instance,
        service_order=instance.service_order,
        old_value=before,
        new_value=after,
    )

    mark_change_stage(
        instance.service_order, instance, record,
        submitted_by=record.requested_by,
        handled_by=operator,
        resource_before=before,
        resource_after=after,
        status=ServiceLifecycleRecord.StageStatus.COMPLETED,
    )
    # 变更完成后回到正式使用阶段（当前阶段仍保留 change 历史记录）
    mark_usage_stage(instance.service_order, instance, handled_by=operator)

    notify_change_event(record, '变更已执行完成')
    dispatch_provision_hooks(
        'change_execute',
        order=instance.service_order,
        instance=instance,
        change=record,
    )
    return record


@transaction.atomic
def rollback_change(record: ServiceChangeRecord, *, operator=None) -> ServiceChangeRecord:
    """基于已保存的 before_spec 回滚已完成的非终止类资源变更。"""
    if record.status != ServiceChangeRecord.ChangeStatus.COMPLETED:
        raise ChangeExecutionError('只有已完成的变更可以回滚')
    if record.after_spec.get('terminate') or (
        record.change_type == ServiceChangeRecord.ChangeType.OTHER
        and record.service_instance.status == ServiceInstance.InstanceStatus.TERMINATED
    ):
        raise ChangeExecutionError('终止类变更不可自动回滚，请通过新开服务流程恢复')
    if not record.before_spec:
        raise ChangeExecutionError('缺少变更前资源快照，无法安全回滚')

    instance = record.service_instance
    before_rollback = service_snapshot(instance)
    _apply_spec_to_instance(instance, record.before_spec)
    after_rollback = service_snapshot(instance)

    record.status = ServiceChangeRecord.ChangeStatus.ROLLED_BACK
    record.rolled_back_at = timezone.now()
    record.rolled_back_by = operator
    record.save(update_fields=[
        'status', 'rolled_back_at', 'rolled_back_by', 'updated_at',
    ])
    ServiceAllocation.objects.create(
        service_instance=instance,
        action_type=ServiceAllocation.ActionType.MODIFY,
        description=f'回滚变更 {record.change_no}：恢复变更前资源快照',
        before_state=before_rollback,
        after_state=after_rollback,
        operator=operator,
        remark=record.change_reason,
    )
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.CHANGE,
        description=f'回滚变更 [{record.change_no}]',
        service_instance=instance,
        service_order=instance.service_order,
        old_value=before_rollback,
        new_value=after_rollback,
    )
    mark_usage_stage(instance.service_order, instance, handled_by=operator)
    notify_change_event(record, '变更已回滚')
    return record
