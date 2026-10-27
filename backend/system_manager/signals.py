"""应用启动时确保内置配置项存在，并监听变更/异动事件转发到 IM。"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_migrate, post_save
from django.dispatch import receiver

from .default_settings import DEFAULT_SYSTEM_SETTINGS
from .models import IMChannel, NotificationRule, PaymentChannel, SystemSetting

logger = logging.getLogger(__name__)


@receiver(post_migrate)
def ensure_default_settings(sender, **kwargs):
    if sender.name != 'system_manager':
        return
    for item in DEFAULT_SYSTEM_SETTINGS:
        defaults = {
            'category': item['category'],
            'label': item['label'],
            'description': item.get('description', ''),
            'value_type': item['value_type'],
            'default_value': item.get('default_value', ''),
            'value': item.get('default_value', ''),
            'options': item.get('options'),
            'is_public': item.get('is_public', False),
            'is_editable': item.get('is_editable', True),
            'sort_order': item.get('sort_order', 0),
        }
        obj, created = SystemSetting.objects.get_or_create(key=item['key'], defaults=defaults)
        if created:
            continue
        changed = []
        for field in (
            'category', 'label', 'description', 'value_type',
            'default_value', 'options', 'is_public', 'sort_order',
        ):
            new_value = defaults[field]
            if getattr(obj, field) != new_value:
                setattr(obj, field, new_value)
                changed.append(field)
        if changed:
            obj.save(update_fields=changed)


# ─────────────────────────────────────────────────────────────
# 变更/异动 → IM 通知转发（全部弱依赖，失败不影响主流程）
# ─────────────────────────────────────────────────────────────

def _user_name(user):
    if not user:
        return '系统'
    return user.get_full_name() or user.username


def _safe_notify(event, title='', body='', *, context=None, dedupe_key=''):
    """
    在数据库事务提交后再投递，且不阻塞当前请求。

    所有变更通知来源都是审计日志；若事务随后回滚，不能把未成功提交的数据先
    推送出去。邮件和 IM 可能要等外部超时，放到后台发送，避免保存接口被拖到超时。
    """
    import threading
    payload = dict(context or {})

    def _send():
        from . import im_services
        im_services.notify(
            event,
            title,
            body,
            context=payload,
            dedupe_key=dedupe_key,
        )

    def _deliver():
        threading.Thread(target=_send, name='isp-notify', daemon=True).start()

    transaction.on_commit(_deliver)


def _invalidate_support_cache_if_needed(instance):
    if str(getattr(instance, 'key', '')).startswith('support_'):
        from .support_public import invalidate_public_support_cache
        invalidate_public_support_cache()


@receiver(post_save, sender=SystemSetting, dispatch_uid='support_public_cache_invalidate')
def _on_support_setting_save(sender, instance, **kwargs):
    _invalidate_support_cache_if_needed(instance)


@receiver(post_delete, sender=SystemSetting, dispatch_uid='support_public_cache_invalidate_del')
def _on_support_setting_delete(sender, instance, **kwargs):
    _invalidate_support_cache_if_needed(instance)


@receiver(post_save, sender=SystemSetting, dispatch_uid='im_system_setting_change')
def _on_system_setting_change(sender, instance, created, **kwargs):
    """参数变更只推送名称和操作者，避免配置值（含潜在敏感信息）外泄。"""
    from .default_settings import SECRET_SETTING_KEYS
    if (
        created
        or instance.key.startswith('notify_')
        or instance.key.startswith('smtp_')
        or instance.key in SECRET_SETTING_KEYS
    ):
        return
    _safe_notify(
        'system_setting_change',
        context={
            'module': '系统管理',
            'action': 'update',
            'action_label': f'更新「{instance.label or instance.key}」',
            'target_type': '系统参数',
            'target_name': instance.label or instance.key,
            'target_id': instance.pk,
            'operator_name': _user_name(instance.updated_by),
            'detail': instance.description or '系统参数已更新；通知不包含参数值。',
        },
    )


def _notify_system_config_change(instance, *, created, target_type, target_name, detail):
    _safe_notify(
        'system_setting_change',
        context={
            'module': '系统管理',
            'action': 'create' if created else 'update',
            'action_label': f'{"新建" if created else "更新"}{target_type}',
            'target_type': target_type,
            'target_name': target_name,
            'target_id': instance.pk,
            'operator_name': _user_name(
                getattr(instance, 'updated_by', None) or getattr(instance, 'created_by', None)
            ),
            'detail': detail,
        },
    )


@receiver(post_save, sender=IMChannel, dispatch_uid='im_im_channel_change')
def _on_im_channel_change(sender, instance, created, **kwargs):
    _notify_system_config_change(
        instance,
        created=created,
        target_type='IM 通知通道',
        target_name=instance.name,
        detail=(
            f'通道类型：{instance.get_channel_type_display()}；'
            f'状态：{"启用" if instance.enabled else "停用"}。'
            '通知不包含 Webhook 地址、Token 或 Secret。'
        ),
    )


@receiver(post_save, sender=PaymentChannel, dispatch_uid='im_payment_channel_change')
def _on_payment_channel_change(sender, instance, created, **kwargs):
    _notify_system_config_change(
        instance,
        created=created,
        target_type='在线支付渠道',
        target_name=instance.name,
        detail=(
            f'支付方式：{instance.get_provider_display()}；'
            f'状态：{"启用" if instance.enabled else "停用"}；'
            f'模式：{"沙箱" if instance.sandbox else "生产"}。'
            '通知不包含支付凭据。'
        ),
    )


@receiver(post_save, sender=NotificationRule, dispatch_uid='im_notification_rule_change')
def _on_notification_rule_change(sender, instance, created, **kwargs):
    _notify_system_config_change(
        instance,
        created=created,
        target_type='通知规则',
        target_name=instance.name,
        detail=(
            f'事件：{instance.get_event_display()}；'
            f'状态：{"启用" if instance.enabled else "停用"}；'
            f'格式：{instance.get_format_mode_display()}。'
        ),
    )


def _resource_event(log):
    """根据资源审计对象路由至更细粒度的事件，其他仍归入通用资源异动。"""
    target = log.target_model
    try:
        from resource_manager.models import ResourceOperationLog
        targets = ResourceOperationLog.TargetModel
        if target == targets.VPS:
            return 'vps_change'
        if target == targets.EDGE:
            return 'edge_acceleration_change'
        if target == targets.POP:
            return 'pop_change'
        if target in (targets.IP_ADDRESS, targets.IP_BLOCK):
            return 'ip_change'
        if target == targets.BW_ALLOC or log.action_type == ResourceOperationLog.ActionType.BW_ALLOC:
            return 'bandwidth_change'
    except Exception:  # noqa: BLE001
        logger.debug('无法解析资源通知事件类型', exc_info=True)
    return 'resource_change'


def _resource_context(log):
    vps = log.vps
    edge = log.edge
    customer = (
        getattr(vps, 'customer', None)
        or getattr(edge, 'customer', None)
    )
    return {
        'module': '资源管理',
        'action': log.action_type,
        'action_label': log.get_action_type_display(),
        'target_type': log.get_target_model_display(),
        'target_name': log.target_repr or log.get_target_model_display(),
        'target_id': log.target_id,
        'operator_name': _user_name(log.operator),
        'pop_name': log.pop_point.name if log.pop_point_id else '',
        'customer_name': customer.name if customer else '',
        'customer_no': customer.customer_no if customer else '',
        'vps_no': vps.vps_no if vps else '',
        'edge_no': edge.edge_no if edge else '',
        'detail': log.description,
        'old_value': log.old_value,
        'new_value': log.new_value,
        'occurred_at': log.created_at,
    }


def _customer_event(log):
    try:
        from customer_manager.models import CustomerOperationLog
        targets = CustomerOperationLog.TargetModel
        if log.target_model == targets.CONTACT_RECORD:
            return 'customer_contact'
        if log.target_model == targets.CONTRACT:
            return 'customer_contract'
        if log.target_model == targets.BILLING:
            if (log.new_value or {}).get('status') == 'overdue':
                return 'billing_overdue'
            if log.action_type == CustomerOperationLog.ActionType.PAYMENT:
                return 'billing_payment'
            return 'billing_change'
    except Exception:  # noqa: BLE001
        logger.debug('无法解析客户通知事件类型', exc_info=True)
    return 'customer_change'


def _customer_context(log):
    customer = log.customer
    target_name = customer.name if customer else log.description
    contract_no = ''
    bill_no = ''
    action = log.action_type
    action_label = log.get_action_type_display()
    payment_status = ''
    billing_status = ''
    amount = ''
    try:
        from customer_manager.models import Contract, CustomerBilling, CustomerOperationLog
        if log.target_model == CustomerOperationLog.TargetModel.CONTRACT and log.target_id:
            contract = Contract.objects.filter(pk=log.target_id).only('contract_no').first()
            contract_no = contract.contract_no if contract else ''
        elif log.target_model == CustomerOperationLog.TargetModel.BILLING and log.target_id:
            bill = CustomerBilling.objects.filter(pk=log.target_id).only(
                'bill_no', 'amount', 'currency',
            ).first()
            bill_no = bill.bill_no if bill else ''
            target_name = bill_no or target_name
            if bill and bill.amount is not None:
                amount = f'{bill.amount} {getattr(bill, "currency", "") or ""}'.strip()
            billing_status = str((log.new_value or {}).get('status') or '')
            if billing_status == 'overdue':
                action = 'overdue'
                action_label = '账单逾期'
            payment_status = str((log.new_value or {}).get('payment_status') or '')
            if payment_status:
                action = payment_status
                action_label = {
                    'succeeded': '支付成功',
                    'failed': '支付失败',
                    'cancelled': '支付取消',
                    'expired': '支付过期',
                    'pending': '支付处理中',
                }.get(payment_status, payment_status)
    except Exception:  # noqa: BLE001
        logger.debug('无法读取客户通知关联对象', exc_info=True)
    return {
        'module': '客户管理',
        'action': action,
        'action_label': action_label,
        'target_type': log.get_target_model_display(),
        'target_name': target_name,
        'target_id': log.target_id,
        'customer_name': customer.name if customer else '',
        'customer_no': customer.customer_no if customer else '',
        'customer_email': (customer.contact_email or '') if customer else '',
        'contract_no': contract_no,
        'bill_no': bill_no,
        'billing_status': billing_status,
        'payment_status': payment_status,
        'amount': amount or (log.new_value or {}).get('amount') or (log.new_value or {}).get('paid_amount') or '',
        'operator_name': _user_name(log.operator),
        'detail': log.description,
        'old_value': log.old_value,
        'new_value': log.new_value,
        'occurred_at': log.created_at,
    }


def connect_change_signals():
    """在 AppConfig.ready() 中调用，连接其它模块的变更/异动信号。"""
    # 资源异动（ResourceOperationLog）
    try:
        from resource_manager.models import ResourceOperationLog

        @receiver(post_save, sender=ResourceOperationLog, dispatch_uid='im_resource_oplog')
        def _on_resource_oplog(sender, instance, created, **kwargs):
            if not created:
                return
            _safe_notify(_resource_event(instance), context=_resource_context(instance))
    except Exception:  # noqa: BLE001
        logger.exception('未连接 ResourceOperationLog 信号，资源变更将无法推送 IM')

    # 产品发布/上下线（ProductPublishLog）
    try:
        from product_manager.models import ProductPublishLog

        @receiver(post_save, sender=ProductPublishLog, dispatch_uid='im_product_publish')
        def _on_product_publish(sender, instance, created, **kwargs):
            if not created:
                return
            _safe_notify(
                'product_publish',
                context={
                    'module': '产品管理',
                    'action': instance.action,
                    'action_label': instance.get_action_display(),
                    'target_type': '产品',
                    'target_name': f'{instance.product.name}（{instance.product.product_no}）',
                    'target_id': instance.product_id,
                    'operator_name': _user_name(instance.operator),
                    'detail': instance.message or f'{instance.from_status} → {instance.to_status}',
                    'old_value': {'status': instance.from_status},
                    'new_value': {'status': instance.to_status},
                    'occurred_at': instance.created_at,
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception('未连接 ProductPublishLog 信号')

    # 供应商资源同步（SupplierSyncLog）
    try:
        from supplier_manager.models import SupplierSyncLog

        @receiver(post_save, sender=SupplierSyncLog, dispatch_uid='im_supplier_sync')
        def _on_supplier_sync(sender, instance, created, **kwargs):
            if not created:
                return
            _safe_notify(
                'supplier_sync',
                context={
                    'module': '供应商管理',
                    'action': instance.action,
                    'action_label': instance.get_action_display(),
                    'target_type': '供应商',
                    'target_name': instance.supplier.name,
                    'target_id': instance.supplier_id,
                    'operator_name': '系统同步',
                    'detail': (
                        f'结果：{instance.get_result_display()}；收到 {instance.received_count}，'
                        f'新增 {instance.created_count}，更新 {instance.updated_count}'
                    ),
                    'occurred_at': instance.created_at,
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception('未连接 SupplierSyncLog 信号')

    # 仅将 Zabbix 同步失败/部分失败作为系统告警推送，成功同步不产生噪音。
    try:
        from operation_manager.models import MonitoringSyncLog

        @receiver(post_save, sender=MonitoringSyncLog, dispatch_uid='im_monitoring_sync_alert')
        def _on_monitoring_sync_alert(sender, instance, created, **kwargs):
            if not created or instance.status == MonitoringSyncLog.Status.SUCCESS:
                return
            host = instance.monitoring_host
            server = instance.zabbix_server
            target_name = (
                (host.visible_name or host.hostname) if host
                else server.name if server else '监控同步任务'
            )
            _safe_notify(
                'system_alert',
                context={
                    'module': '运维监控',
                    'action': 'alert',
                    'action_label': (
                        '监控同步失败'
                        if instance.status == MonitoringSyncLog.Status.FAILED
                        else '监控同步部分失败'
                    ),
                    'target_type': 'Zabbix 监控',
                    'target_name': target_name,
                    'target_id': instance.pk,
                    'operator_name': _user_name(instance.operator),
                    'detail': (
                        f'{instance.get_action_type_display()}：{instance.message}'
                    ),
                    'new_value': {
                        'status': instance.status,
                        'duration_ms': instance.duration_ms,
                        'detail': instance.detail,
                    },
                    'occurred_at': instance.created_at,
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception('未连接 MonitoringSyncLog 信号')

    # 客户、合同、联系记录、账单与收款均已写入 CustomerOperationLog；统一从审计
    # 日志生成通知，可同时覆盖 API、后台管理和支付回调的变更路径。
    try:
        from customer_manager.models import CustomerOperationLog

        @receiver(post_save, sender=CustomerOperationLog, dispatch_uid='im_customer_oplog')
        def _on_customer_oplog(sender, instance, created, **kwargs):
            if created:
                event = _customer_event(instance)
                if event == 'billing_overdue':
                    from .services import get_setting
                    if not get_setting('notify_billing_overdue', True):
                        return
                _safe_notify(event, context=_customer_context(instance))
    except Exception:  # noqa: BLE001
        logger.exception('未连接 CustomerOperationLog 信号')

    # 生命周期阶段由独立记录承载，不能只依赖工单/实例状态；这里单独推送，让
    # “需求确认、测试、正式使用、终止”等阶段能配置不同的通知规则。
    try:
        from service_manager.models import ServiceLifecycleRecord

        @receiver(post_save, sender=ServiceLifecycleRecord, dispatch_uid='im_service_lifecycle')
        def _on_service_lifecycle(sender, instance, created, **kwargs):
            if not created:
                return
            order = instance.service_order
            service_instance = instance.service_instance
            _safe_notify(
                'service_lifecycle',
                context={
                    'module': '服务管理',
                    'action': instance.stage,
                    'action_label': instance.get_stage_display(),
                    'target_type': '生命周期阶段',
                    'target_name': instance.title or instance.get_stage_display(),
                    'target_id': instance.pk,
                    'customer_name': order.customer.name,
                    'customer_no': order.customer.customer_no,
                    'order_no': order.order_no,
                    'instance_no': service_instance.instance_no if service_instance else '',
                    'operator_name': _user_name(instance.handled_by or instance.submitted_by),
                    'detail': instance.detail or instance.reject_reason or instance.remark,
                    'old_value': instance.resource_before,
                    'new_value': instance.resource_after,
                    'occurred_at': instance.created_at,
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception('未连接 ServiceLifecycleRecord 信号')
