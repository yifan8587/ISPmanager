"""
服务管理模块 IM 通知辅助。

统一通过 system_manager.im_services.notify 推送，事件映射到系统管理中的通知规则。
失败不阻断主业务流程。
"""
from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def notify_service_event(
    event: str,
    title: str = '',
    body: str = '',
    *,
    context: dict | None = None,
) -> None:
    try:
        import threading
        payload = dict(context or {})

        def _send():
            from system_manager import im_services
            im_services.notify(event, title, body, context=payload)

        def _deliver():
            threading.Thread(target=_send, name='isp-notify', daemon=True).start()

        transaction.on_commit(_deliver)
    except Exception:  # noqa: BLE001
        logger.warning('服务通知发送失败 event=%s title=%s', event, title, exc_info=True)


def _inquiry_context(inquiry, subject: str, *, detail: str = '') -> dict:
    contact = ' / '.join(filter(None, [inquiry.phone, inquiry.email])) or '未提供'
    action_map = {
        '提交需求': 'create',
        '指派处理人': 'assign',
        '回复需求': 'reply',
        '关闭需求': 'close',
    }
    return {
        'module': '官网支持',
        'action': action_map.get(subject, 'update'),
        'action_label': subject,
        'target_type': inquiry.get_inquiry_type_display(),
        'target_name': inquiry.inquiry_no,
        'target_id': inquiry.pk,
        'contact_name': inquiry.contact_name,
        'company_name': inquiry.company_name,
        'contact': contact,
        'preferred_contact': inquiry.preferred_contact,
        'inquiry_type': inquiry.get_inquiry_type_display(),
        'inquiry_status': inquiry.get_status_display(),
        'handler_name': _user_name(inquiry.handler) if inquiry.handler else '',
        'operator_name': (
            _user_name(inquiry.replied_by)
            if inquiry.replied_by
            else (inquiry.contact_name or '访客')
        ),
        'requirements': inquiry.requirements,
        'detail': detail or inquiry.requirements,
        'occurred_at': inquiry.updated_at or inquiry.created_at,
    }


def notify_inquiry_event(inquiry) -> None:
    notify_service_event(
        'service_inquiry',
        context=_inquiry_context(inquiry, '提交需求'),
    )


def notify_inquiry_update_event(inquiry, subject: str, *, detail: str = '') -> None:
    notify_service_event(
        'service_inquiry',
        context=_inquiry_context(inquiry, subject, detail=detail),
    )


def _ticket_context(ticket, subject: str, *, detail: str = '') -> dict:
    action_map = {
        '提交工单': 'create',
        '指派处理人': 'assign',
        '回复工单': 'reply',
        '客户回复': 'customer_reply',
        '解决工单': 'resolve',
        '关闭工单': 'close',
        '工单升级': 'escalate',
        '响应超时': 'sla_response',
        '解决超时': 'sla_resolve',
    }
    return {
        'module': '技术工单',
        'action': action_map.get(subject, 'update'),
        'action_label': subject,
        'target_type': ticket.get_category_display(),
        'target_name': ticket.ticket_no,
        'target_id': ticket.pk,
        'customer_name': ticket.customer.name,
        'customer_no': ticket.customer.customer_no,
        'customer_email': ticket.customer.contact_email or '',
        'ticket_no': ticket.ticket_no,
        'ticket_subject': ticket.subject,
        'ticket_category': ticket.get_category_display(),
        'ticket_priority': ticket.get_priority_display(),
        'ticket_status': ticket.get_status_display(),
        'handler_name': _user_name(ticket.handler) if ticket.handler else '',
        'operator_name': _user_name(ticket.handler or ticket.created_by),
        'first_response_due': ticket.first_response_due,
        'resolve_due': ticket.resolve_due,
        'escalation_level': ticket.escalation_level,
        'detail': detail or ticket.subject,
        'occurred_at': ticket.updated_at or ticket.created_at,
    }


def notify_ticket_event(ticket, subject: str, *, detail: str = '') -> None:
    notify_service_event(
        'service_ticket',
        context=_ticket_context(ticket, subject, detail=detail),
    )


def _selfservice_context(job, subject: str, *, detail: str = '') -> dict:
    action_map = {
        '提交重装': 'reinstall',
        '提交快照': 'snapshot_create',
        '提交恢复': 'snapshot_restore',
        '任务完成': 'complete',
        '任务失败': 'fail',
        '取消任务': 'cancel',
    }
    vps = job.vps
    customer = getattr(vps, 'customer', None)
    instance = job.service_instance
    return {
        'module': '客户自助',
        'action': action_map.get(subject, 'update'),
        'action_label': subject,
        'target_type': job.get_job_type_display(),
        'target_name': job.job_no,
        'target_id': job.pk,
        'customer_name': customer.name if customer else '',
        'customer_no': customer.customer_no if customer else '',
        'vps_no': vps.vps_no or '',
        'instance_no': instance.instance_no if instance else '',
        'job_no': job.job_no,
        'job_type': job.get_job_type_display(),
        'job_status': job.get_status_display(),
        'operator_name': _user_name(job.requested_by),
        'detail': detail or job.result_message,
        'occurred_at': job.updated_at or job.created_at,
    }


def notify_selfservice_event(job, subject: str, *, detail: str = '') -> None:
    notify_service_event(
        'service_selfservice',
        context=_selfservice_context(job, subject, detail=detail),
    )


def _user_name(user) -> str:
    if not user:
        return '系统'
    return user.get_full_name() or user.username


def _order_context(order, subject: str, detail: str = '') -> dict:
    action_map = {
        '新建工单': 'create_order',
        '提交待审批': 'submit_order',
        '审批通过': 'approve_order',
        '审批驳回': 'reject_order',
        '指派处理人': 'assign_handler',
        '工单信息已更新': 'update',
        '删除工单': 'delete',
        '资源不足待人工': 'failed',
        '自动开通失败': 'failed',
        '自动开通成功': 'success',
        '变更开通成功': 'success',
        '变更资源不足待人工': 'failed',
        '变更已付款待人工': 'failed',
        '门户服务变更': 'change',
        '门户下单': 'create_order',
        '门户直付订单已确认': 'create_order',
        '门户变更申请': 'change',
        '门户一键续费': 'renew',
        '续费已生效': 'renew',
        '生命周期阶段变更': 'stage_change',
        '工单资源交付同步': 'update',
    }
    return {
        'module': '服务管理',
        'action': action_map.get(subject, 'update'),
        'action_label': subject,
        'target_type': '服务工单',
        'target_name': order.order_no,
        'target_id': order.pk,
        'customer_name': order.customer.name,
        'customer_no': order.customer.customer_no,
        'customer_email': order.customer.contact_email or '',
        'order_no': order.order_no,
        'order_type': order.get_order_type_display(),
        'business_type': order.get_business_type_display(),
        'order_status': order.get_status_display(),
        'lifecycle_stage': order.get_current_stage_display(),
        'pop_name': order.preferred_pop.name if order.preferred_pop_id else '',
        'operator_name': _user_name(
            order.approved_by if subject in {'审批通过', '审批驳回'}
            else order.handler if subject == '指派处理人'
            else order.created_by
        ),
        'handler_name': _user_name(order.handler) if order.handler else '',
        'approver_name': _user_name(order.approved_by) if order.approved_by else '',
        'quoted_price': order.quoted_price,
        'spec_summary': ' / '.join(filter(None, [
            f'{order.requested_vcpu}C' if order.requested_vcpu else '',
            f'{order.requested_ram_mb}MB' if order.requested_ram_mb else '',
            f'{order.requested_disk_gb}GB' if order.requested_disk_gb else '',
            f'{order.requested_bandwidth_mbps}Mbps' if order.requested_bandwidth_mbps else '',
            f'{order.requested_ip_count} IP' if order.requested_ip_count else '',
        ])),
        'requirements': order.requirements,
        'requested_vcpu': order.requested_vcpu,
        'requested_ram_mb': order.requested_ram_mb,
        'requested_disk_gb': order.requested_disk_gb,
        'requested_bandwidth_mbps': order.requested_bandwidth_mbps,
        'requested_ip_count': order.requested_ip_count,
        'detail': detail or order.requirements,
        'occurred_at': order.updated_at,
    }


def notify_order_event(order, subject: str, *, detail: str = '', event: str = 'service_order') -> None:
    notify_service_event(event, context=_order_context(order, subject, detail))


def notify_instance_event(instance, subject: str, *, detail: str = '', event: str = 'service_change') -> None:
    order = instance.service_order
    action_map = {
        '服务已激活': 'activate',
        '服务已暂停': 'suspend',
        '服务已恢复': 'resume',
        '服务已终止': 'terminate',
        '测试转正计费': 'convert_billing',
        '服务实例已创建': 'provision',
        '服务实例信息已更新': 'update',
        '服务实例已删除': 'delete',
        '服务 IP 已分配': 'assign_ip',
        '服务 IP 已释放': 'release_ip',
        '自动开通激活': 'success',
        '人工资源交付完成': 'execute',
        '资源管理侧 IP 已分配': 'assign_ip',
        '资源管理侧 IP 已释放': 'release_ip',
        '资源管理侧带宽已同步': 'update',
        '门户协议配置已更新': 'config_change',
        '门户配置已下发': 'deploy',
        '门户电源操作': 'status_change',
        '门户更新 Root 密码档案': 'config_change',
    }
    notify_service_event(
        event,
        context={
            'module': '服务管理',
            'action': action_map.get(subject, 'update'),
            'action_label': subject,
            'target_type': instance.get_service_type_display(),
            'target_name': instance.instance_no,
            'target_id': instance.pk,
            'customer_name': order.customer.name,
            'customer_no': order.customer.customer_no,
            'customer_email': order.customer.contact_email or '',
            'order_no': order.order_no,
            'instance_no': instance.instance_no,
            'vps_no': instance.vps.vps_no if instance.vps_id else '',
            'edge_no': instance.edge.edge_no if instance.edge_id else '',
            'service_type': instance.get_service_type_display(),
            'service_status': instance.get_status_display(),
            'monthly_fee': instance.effective_monthly_fee(),
            'currency': instance.effective_fee_currency(),
            'is_trial': instance.is_trial,
            'operator_name': _user_name(order.handler or order.created_by),
            'detail': detail,
            'occurred_at': instance.updated_at,
        },
    )


def notify_change_event(record, subject: str, *, detail: str = '', event: str = 'service_change') -> None:
    instance = record.service_instance
    order = record.related_order or instance.service_order
    action_map = {
        '变更申请已提交': 'change',
        '变更审批通过': 'approve',
        '变更审批驳回': 'reject',
        '变更已驳回': 'reject',
        '变更已取消': 'cancel',
        '变更已执行完成': 'execute',
        '变更已执行（终止）': 'terminate',
        '变更已回滚': 'rollback',
        '变更资源不足待人工': 'failed',
        '变更已付款待人工': 'failed',
        '门户服务变更已提交': 'change',
    }
    notify_service_event(
        event,
        context={
            'module': '服务管理',
            'action': action_map.get(subject, 'change'),
            'action_label': subject,
            'target_type': '服务变更单',
            'target_name': record.change_no,
            'target_id': record.pk,
            'customer_name': order.customer.name,
            'customer_no': order.customer.customer_no,
            'customer_email': order.customer.contact_email or '',
            'order_no': order.order_no,
            'instance_no': instance.instance_no,
            'vps_no': instance.vps.vps_no if instance.vps_id else '',
            'edge_no': instance.edge.edge_no if instance.edge_id else '',
            'change_no': record.change_no,
            'change_type': record.get_change_type_display(),
            'change_status': record.get_status_display(),
            'requested_by': _user_name(record.requested_by),
            'approver_name': _user_name(record.approved_by) if record.approved_by else '',
            'handler_name': _user_name(record.handler) if record.handler else '',
            'executed_by': _user_name(record.executed_by) if record.executed_by else '',
            'operator_name': _user_name(
                record.executed_by or record.approved_by or record.handler or record.requested_by
            ),
            'change_reason': record.change_reason,
            'price_delta': record.price_delta,
            'detail': detail or record.change_reason,
            'occurred_at': record.updated_at,
        },
    )
