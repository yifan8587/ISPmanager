from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """全站统一审计日志。各模块专用日志经信号汇聚，其余写接口由中间件补记。"""

    class Module(models.TextChoices):
        ACCOUNTS = 'accounts', '账号管理'
        CUSTOMER = 'customer', '客户管理'
        BILLING = 'billing', '账单管理'
        RESOURCE = 'resource', '资源管理'
        SERVICE = 'service', '服务管理'
        PRODUCT = 'product', '产品管理'
        SUPPLIER = 'supplier', '供应商管理'
        OPERATION = 'operation', '运维管理'
        SYSTEM = 'system', '系统设置'
        PORTAL = 'portal', '客户门户'

    class Source(models.TextChoices):
        RESOURCE_OPERATION = 'resource_operation', '资源操作日志'
        SERVICE_OPERATION = 'service_operation', '服务操作日志'
        CUSTOMER_OPERATION = 'customer_operation', '客户操作日志'
        PRODUCT_PUBLISH = 'product_publish', '产品发布日志'
        MONITORING_SYNC = 'monitoring_sync', '监控同步日志'
        SUPPLIER_SYNC = 'supplier_sync', '供应商同步日志'
        IM_MESSAGE = 'im_message', 'IM 消息日志'
        MIDDLEWARE = 'middleware', '接口写操作'
        AUTH = 'auth', '认证'
        MANUAL = 'manual', '手工'

    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='操作人',
    )
    operator_name = models.CharField('操作人姓名', max_length=64, blank=True)
    module = models.CharField('功能模块', max_length=20, choices=Module.choices, db_index=True)
    action_type = models.CharField('操作类型', max_length=32, db_index=True)
    target_model = models.CharField('对象类型', max_length=64, blank=True)
    target_id = models.CharField('对象 ID', max_length=64, blank=True)
    target_repr = models.CharField('对象描述', max_length=256, blank=True)
    description = models.CharField('操作内容', max_length=512)
    detail = models.JSONField('详细信息', default=dict, blank=True)
    ip_address = models.GenericIPAddressField('来源 IP', null=True, blank=True)
    request_path = models.CharField('请求路径', max_length=512, blank=True)
    request_method = models.CharField('请求方法', max_length=8, blank=True)
    source = models.CharField(
        '来源', max_length=32, choices=Source.choices, default=Source.MANUAL,
    )
    source_ref = models.CharField('来源记录 ID', max_length=64, blank=True)
    created_at = models.DateTimeField('操作时间', db_index=True)

    class Meta:
        verbose_name = '审计日志'
        verbose_name_plural = '审计日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['module', 'created_at']),
            models.Index(fields=['action_type', 'created_at']),
            models.Index(fields=['operator', 'created_at']),
            models.Index(fields=['source', 'source_ref']),
            models.Index(fields=['target_model', 'target_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'source_ref'],
                condition=~models.Q(source_ref=''),
                name='uniq_auditlog_source_ref',
            ),
        ]

    def __str__(self):
        name = self.operator_name or '系统'
        return f'[{self.get_module_display()}] {name} - {self.description[:40]}'
