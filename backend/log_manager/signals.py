from __future__ import annotations

import logging

from django.db.models.signals import post_save

from .ingest import (
    ingest_customer_operation_log,
    ingest_im_message_log,
    ingest_monitoring_sync_log,
    ingest_product_publish_log,
    ingest_resource_operation_log,
    ingest_service_operation_log,
    ingest_supplier_sync_log,
)

logger = logging.getLogger(__name__)


def _safe(ingest, instance, created: bool) -> None:
    if not created:
        return
    try:
        ingest(instance)
    except Exception:
        logger.exception('汇聚审计日志失败: %s#%s', instance.__class__.__name__, instance.pk)


def _on_resource(sender, instance, created, **kwargs):
    _safe(ingest_resource_operation_log, instance, created)


def _on_service(sender, instance, created, **kwargs):
    _safe(ingest_service_operation_log, instance, created)


def _on_customer(sender, instance, created, **kwargs):
    _safe(ingest_customer_operation_log, instance, created)


def _on_product(sender, instance, created, **kwargs):
    _safe(ingest_product_publish_log, instance, created)


def _on_monitoring(sender, instance, created, **kwargs):
    _safe(ingest_monitoring_sync_log, instance, created)


def _on_supplier(sender, instance, created, **kwargs):
    _safe(ingest_supplier_sync_log, instance, created)


def _on_im(sender, instance, created, **kwargs):
    _safe(ingest_im_message_log, instance, created)


def connect_audit_signals() -> None:
    from customer_manager.models import CustomerOperationLog
    from operation_manager.models import MonitoringSyncLog
    from product_manager.models import ProductPublishLog
    from resource_manager.models import ResourceOperationLog
    from service_manager.models import ServiceOperationLog
    from supplier_manager.models import SupplierSyncLog
    from system_manager.models import IMMessageLog

    post_save.connect(_on_resource, sender=ResourceOperationLog, dispatch_uid='audit.resource')
    post_save.connect(_on_service, sender=ServiceOperationLog, dispatch_uid='audit.service')
    post_save.connect(_on_customer, sender=CustomerOperationLog, dispatch_uid='audit.customer')
    post_save.connect(_on_product, sender=ProductPublishLog, dispatch_uid='audit.product')
    post_save.connect(_on_monitoring, sender=MonitoringSyncLog, dispatch_uid='audit.operation')
    post_save.connect(_on_supplier, sender=SupplierSyncLog, dispatch_uid='audit.supplier')
    post_save.connect(_on_im, sender=IMMessageLog, dispatch_uid='audit.im')
