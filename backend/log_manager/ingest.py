from __future__ import annotations

from .services import record_audit_log


def ingest_resource_operation_log(instance) -> None:
    record_audit_log(
        module='resource',
        action_type=instance.action_type,
        description=instance.description,
        operator=instance.operator,
        target_model=instance.target_model,
        target_id=instance.target_id,
        target_repr=instance.target_repr,
        detail={'old': instance.old_value, 'new': instance.new_value},
        ip_address=instance.ip_address,
        source='resource_operation',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_service_operation_log(instance) -> None:
    target = instance.service_order or instance.service_instance
    if instance.service_order_id:
        target_model, target_id = 'ServiceOrder', instance.service_order_id
    elif instance.service_instance_id:
        target_model, target_id = 'ServiceInstance', instance.service_instance_id
    else:
        target_model, target_id = '', None
    record_audit_log(
        module='service',
        action_type=instance.action_type,
        description=instance.description,
        operator=instance.operator,
        target_model=target_model,
        target_id=target_id,
        target_repr=str(target) if target else '',
        detail={'old': instance.old_value, 'new': instance.new_value},
        ip_address=instance.ip_address,
        source='service_operation',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_customer_operation_log(instance) -> None:
    billing_targets = {'CustomerBilling'}
    module = 'billing' if (
        instance.target_model in billing_targets or instance.action_type == 'payment'
    ) else 'customer'
    target_repr = ''
    if instance.customer_id:
        target_repr = str(instance.customer) if instance.customer else f'客户#{instance.customer_id}'
    record_audit_log(
        module=module,
        action_type=instance.action_type,
        description=instance.description,
        operator=instance.operator,
        target_model=instance.target_model,
        target_id=instance.target_id,
        target_repr=target_repr,
        detail={'old': instance.old_value, 'new': instance.new_value},
        ip_address=instance.ip_address,
        source='customer_operation',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_product_publish_log(instance) -> None:
    product = getattr(instance, 'product', None)
    label = str(product) if product else f'产品#{instance.product_id}'
    message = instance.message or f'{instance.get_action_display()} {label}'
    record_audit_log(
        module='product',
        action_type=instance.action,
        description=message,
        operator=instance.operator,
        target_model='Product',
        target_id=instance.product_id,
        target_repr=label,
        detail={
            'from_status': instance.from_status,
            'to_status': instance.to_status,
            'message': instance.message,
        },
        source='product_publish',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_monitoring_sync_log(instance) -> None:
    record_audit_log(
        module='operation',
        action_type=instance.action_type,
        description=instance.message,
        operator=instance.operator,
        target_model='MonitoringHost' if instance.monitoring_host_id else 'ZabbixServer',
        target_id=instance.monitoring_host_id or instance.zabbix_server_id,
        target_repr=str(instance.monitoring_host or instance.zabbix_server or ''),
        detail={
            'status': instance.status,
            'detail': instance.detail,
            'duration_ms': instance.duration_ms,
        },
        source='monitoring_sync',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_supplier_sync_log(instance) -> None:
    supplier = getattr(instance, 'supplier', None)
    label = str(supplier) if supplier else f'供应商#{instance.supplier_id}'
    message = instance.message or f'{instance.get_action_display()} {label}'
    record_audit_log(
        module='supplier',
        action_type=instance.action,
        description=message[:512],
        operator=instance.operator,
        target_model='Supplier',
        target_id=instance.supplier_id,
        target_repr=label,
        detail={
            'result': instance.result,
            'received_count': instance.received_count,
            'created_count': instance.created_count,
            'updated_count': instance.updated_count,
            'api_id': instance.api_id,
        },
        source='supplier_sync',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


def ingest_im_message_log(instance) -> None:
    title = instance.title or f'IM 发送 → {instance.target or "-"}'
    record_audit_log(
        module='system',
        action_type='notify',
        description=title,
        operator=None,
        operator_name='系统',
        target_model='IMChannel',
        target_id=instance.channel_id,
        target_repr=str(instance.channel or instance.channel_type or ''),
        detail={
            'event': instance.event,
            'target': instance.target,
            'success': instance.success,
            'response': (instance.response or '')[:500],
            'content': (instance.content or '')[:500],
        },
        source='im_message',
        source_ref=str(instance.pk),
        created_at=instance.created_at,
    )


INGESTORS: tuple[tuple[str, str, callable], ...] = (
    ('resource_manager', 'ResourceOperationLog', ingest_resource_operation_log),
    ('service_manager', 'ServiceOperationLog', ingest_service_operation_log),
    ('customer_manager', 'CustomerOperationLog', ingest_customer_operation_log),
    ('product_manager', 'ProductPublishLog', ingest_product_publish_log),
    ('operation_manager', 'MonitoringSyncLog', ingest_monitoring_sync_log),
    ('supplier_manager', 'SupplierSyncLog', ingest_supplier_sync_log),
    ('system_manager', 'IMMessageLog', ingest_im_message_log),
)


def ingest_existing_logs(*, limit: int | None = None) -> dict[str, int]:
    """回填历史分模块日志。已存在 (source, source_ref) 的记录会被跳过。"""
    from django.apps import apps

    from .models import AuditLog

    stats: dict[str, int] = {}
    for app_label, model_name, ingest in INGESTORS:
        model = apps.get_model(app_label, model_name)
        source = {
            'ResourceOperationLog': 'resource_operation',
            'ServiceOperationLog': 'service_operation',
            'CustomerOperationLog': 'customer_operation',
            'ProductPublishLog': 'product_publish',
            'MonitoringSyncLog': 'monitoring_sync',
            'SupplierSyncLog': 'supplier_sync',
            'IMMessageLog': 'im_message',
        }[model_name]
        existing = set(
            AuditLog.objects.filter(source=source).exclude(source_ref='')
            .values_list('source_ref', flat=True)
        )
        qs = model.objects.all().order_by('pk')
        count = 0
        for instance in qs.iterator(chunk_size=200):
            if str(instance.pk) in existing:
                continue
            ingest(instance)
            count += 1
            if limit is not None and count >= limit:
                break
        stats[model_name] = count
    return stats
