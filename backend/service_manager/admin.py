"""
service_manager/admin.py

工单按业务类型分离：
  ServiceOrderAdmin      全部工单（概览/搜索）
  VPSServiceOrderAdmin   VPS 主机工单（过滤 + VPS 专属表单）
  EdgeServiceOrderAdmin  边缘加速工单（过滤 + 协议专属表单 + JS 动态切换）
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from .models import (
    ServiceOrder, ServiceInstance, ServiceExpiryAction,
    ServiceAllocation, ServiceChangeRecord, ServiceOperationLog,
    ServiceLifecycleRecord, ServiceProvisionHook, PopCapacityReservation,
    ServiceManualDelivery, SupportTicket, SupportTicketMessage,
    OsImage, InstanceSnapshot, SelfServiceJob,
    VPSServiceOrder, EdgeServiceOrder,
    _log_service,
)


# ─────────────────────────────────────────────────────────────
# 颜色工具
# ─────────────────────────────────────────────────────────────

def badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;'
        'border-radius:4px;font-size:12px;white-space:nowrap;">{}</span>',
        color, text,
    )


BUSINESS_TYPE_COLORS = {
    'vps':         '#007bff',
    'edge_gre':    '#6f42c1',
    'edge_wg':     '#5a3e99',
    'edge_socks5': '#fd7e14',
    'ip':          '#17a2b8',
    'bandwidth':   '#28a745',
}
ORDER_STATUS_COLORS = {
    'draft': '#adb5bd', 'pending': '#ffc107', 'approved': '#17a2b8',
    'provisioning': '#007bff', 'active': '#28a745',
    'suspend_pending': '#fd7e14', 'suspended': '#dc3545',
    'terminate_pending': '#e83e8c', 'terminated': '#6c757d',
    'rejected': '#343a40',
}
INSTANCE_STATUS_COLORS = {
    'provisioning': '#17a2b8', 'active': '#28a745', 'suspended': '#ffc107',
    'terminated': '#6c757d', 'expired': '#dc3545', 'error': '#e83e8c',
}
CHANGE_STATUS_COLORS = {
    'pending': '#ffc107', 'approved': '#17a2b8', 'in_progress': '#007bff',
    'completed': '#28a745', 'rejected': '#dc3545', 'cancelled': '#6c757d',
}
ALLOC_ACTION_COLORS = {
    'allocate': '#28a745', 'activate': '#007bff', 'modify': '#fd7e14',
    'suspend': '#ffc107', 'resume': '#17a2b8', 'terminate': '#dc3545',
    'renew': '#6f42c1', 'ip_add': '#20c997', 'ip_remove': '#e83e8c',
    'bw_change': '#fd7e14', 'other': '#adb5bd',
}
LOG_ACTION_COLORS = {
    'create_order': '#28a745', 'approve_order': '#17a2b8', 'reject_order': '#dc3545',
    'provision': '#007bff', 'activate': '#28a745', 'suspend': '#ffc107',
    'resume': '#17a2b8', 'terminate': '#dc3545', 'renew': '#6f42c1',
    'change': '#fd7e14', 'assign_ip': '#20c997', 'release_ip': '#e83e8c',
    'bw_adjust': '#fd7e14', 'other': '#adb5bd',
}


# ═══════════════════════════════════════════════════════════════
# § 1  服务工单 — 公共 Inline
# ═══════════════════════════════════════════════════════════════

class ServiceInstanceInline(admin.TabularInline):
    model = ServiceInstance
    extra = 0
    fields = (
        'instance_no', 'service_type', 'status',
        'vps', 'edge', 'bandwidth_alloc',
        'provisioned_at', 'expire_time', 'suspend_at', 'recycle_at',
    )
    readonly_fields = ('instance_no',)
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return obj is not None


class OrderLogInline(admin.TabularInline):
    model = ServiceOperationLog
    fk_name = 'service_order'
    extra = 0
    fields = ('action_type', 'description', 'operator', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


# ═══════════════════════════════════════════════════════════════
# § 2a  服务工单 — 基类 Admin（公共方法 + save 逻辑）
# ═══════════════════════════════════════════════════════════════

class ServiceOrderBaseAdmin(admin.ModelAdmin):
    """所有工单 Admin 的公共基类"""
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True
    inlines = [ServiceInstanceInline, OrderLogInline]

    # ── 公共 list_display 工具方法 ────────────────────────────

    def business_type_badge(self, obj):
        return badge(
            obj.get_business_type_display(),
            BUSINESS_TYPE_COLORS.get(obj.business_type, '#6c757d'),
        )
    business_type_badge.short_description = '业务类型'
    business_type_badge.admin_order_field = 'business_type'

    def order_type_badge(self, obj):
        colors = {
            'new': '#28a745', 'renew': '#17a2b8', 'upgrade': '#007bff',
            'downgrade': '#ffc107', 'terminate': '#dc3545', 'change': '#fd7e14',
        }
        return badge(obj.get_order_type_display(), colors.get(obj.order_type, '#6c757d'))
    order_type_badge.short_description = '工单类型'
    order_type_badge.admin_order_field = 'order_type'

    def status_badge(self, obj):
        return badge(obj.get_status_display(), ORDER_STATUS_COLORS.get(obj.status, '#6c757d'))
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def quoted_price_display(self, obj):
        if obj.quoted_price is None:
            return '—'
        return format_html(
            '<span style="color:#007bff;">¥{}</span>', f'{obj.quoted_price:,.2f}',
        )
    quoted_price_display.short_description = '报价'

    # ── 公共 save 逻辑（审批自动填充 + 日志）────────────────

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = obj.created_by or request.user
        old_status = None
        if change:
            old_status = ServiceOrder.objects.get(pk=obj.pk).status
        super().save_model(request, obj, form, change)
        if not change:
            _log_service(
                operator=request.user,
                action_type=ServiceOperationLog.ActionType.CREATE_ORDER,
                description=f'创建工单 [{obj.order_no}] {obj.get_business_type_display()}',
                service_order=obj, request=request,
            )
        elif old_status and old_status != obj.status:
            if obj.status == 'approved':
                obj.approved_by = obj.approved_by or request.user
                obj.approved_at = obj.approved_at or timezone.now()
                obj.save(update_fields=['approved_by', 'approved_at'])
                _log_service(
                    operator=request.user,
                    action_type=ServiceOperationLog.ActionType.APPROVE_ORDER,
                    description=f'审批工单 [{obj.order_no}]',
                    service_order=obj, request=request,
                    old_value={'status': old_status}, new_value={'status': obj.status},
                )
            elif obj.status == 'rejected':
                _log_service(
                    operator=request.user,
                    action_type=ServiceOperationLog.ActionType.REJECT_ORDER,
                    description=f'驳回工单 [{obj.order_no}] 原因：{obj.reject_reason}',
                    service_order=obj, request=request,
                )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'customer', 'contract', 'product',
            'preferred_pop', 'created_by', 'approved_by',
        )


# ═══════════════════════════════════════════════════════════════
# § 2b  全部工单（概览视图）
# ═══════════════════════════════════════════════════════════════

@admin.register(ServiceOrder)
class ServiceOrderAdmin(ServiceOrderBaseAdmin):
    """全部工单一览 — 支持按业务类型快速过滤"""
    list_display = (
        'order_no', 'customer', 'business_type_badge',
        'order_type_badge', 'status_badge',
        'product', 'preferred_pop',
        'quoted_price_display', 'service_start', 'service_end',
        'created_by', 'created_at',
    )
    list_filter = ('business_type', 'status', 'order_type', 'billing_cycle', 'created_at')
    search_fields = (
        'order_no', 'customer__name', 'customer__customer_no', 'requirements',
    )
    readonly_fields = ('order_no', 'fx_quote_currency', 'fx_locked_at', 'fx_snapshot', 'created_at', 'updated_at')

    # 全量 fieldsets（含所有类型字段，按类型折叠）
    fieldsets = (
        ('工单基本信息', {
            'fields': (
                'order_no', 'business_type', 'order_type', 'status',
                'customer', 'contract', 'product', 'preferred_pop',
            ),
        }),
        ('VPS 需求规格', {
            'classes': ('collapse',),
            'fields': (
                'requested_vcpu', 'requested_ram_mb',
                'requested_disk_gb', 'requested_os',
            ),
        }),
        ('边缘加速 · 通用需求', {
            'classes': ('collapse',),
            'fields': ('requested_bandwidth_mbps', 'requested_ip_count'),
        }),
        ('边缘加速 · GRE 配置', {
            'classes': ('collapse',),
            'fields': ('edge_local_ip', 'edge_remote_ip'),
        }),
        ('边缘加速 · WireGuard 配置', {
            'classes': ('collapse',),
            'fields': ('edge_wg_port', 'edge_wg_keepalive',
                       'edge_wg_allowed_ips', 'edge_wg_client_pubkey'),
        }),
        ('边缘加速 · Socks5 配置', {
            'classes': ('collapse',),
            'fields': ('edge_socks5_port', 'edge_socks5_user', 'edge_socks5_pass'),
        }),
        ('需求说明', {
            'fields': ('requirements',),
        }),
        ('服务期限与费用', {
            'fields': (
                'service_start', 'service_end', 'billing_cycle', 'quoted_price',
                'fx_quote_currency', 'fx_locked_at', 'fx_snapshot',
            ),
        }),
        ('审批信息', {
            'classes': ('collapse',),
            'fields': ('approved_by', 'approved_at', 'reject_reason'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('created_by', 'remark', 'created_at', 'updated_at'),
        }),
    )


# ═══════════════════════════════════════════════════════════════
# § 2c  VPS 服务工单（代理模型 Admin）
# ═══════════════════════════════════════════════════════════════

@admin.register(VPSServiceOrder)
class VPSServiceOrderAdmin(ServiceOrderBaseAdmin):
    """
    VPS 主机工单 — 专属表单，仅显示 VPS 相关字段。
    新建时自动将 business_type 设为 vps。
    """
    list_display = (
        'order_no', 'customer', 'order_type_badge', 'status_badge',
        'vps_spec_summary', 'preferred_pop',
        'quoted_price_display', 'service_start', 'service_end',
        'created_by', 'created_at',
    )
    list_filter = ('status', 'order_type', 'billing_cycle', 'created_at')
    search_fields = (
        'order_no', 'customer__name', 'customer__customer_no',
        'requirements', 'requested_os',
    )
    readonly_fields = ('order_no', 'business_type', 'fx_quote_currency', 'fx_locked_at', 'fx_snapshot', 'created_at', 'updated_at')

    fieldsets = (
        ('工单基本信息', {
            'fields': (
                'order_no', 'business_type', 'order_type', 'status',
                'customer', 'contract', 'product',
            ),
        }),
        ('VPS 资源需求', {
            'classes': ('vps-section',),
            'fields': (
                'preferred_pop',
                'requested_vcpu', 'requested_ram_mb',
                'requested_disk_gb', 'requested_os',
                'requested_bandwidth_mbps', 'requested_ip_count',
            ),
        }),
        ('需求说明', {
            'fields': ('requirements',),
        }),
        ('服务期限与费用', {
            'fields': (
                'service_start', 'service_end', 'billing_cycle', 'quoted_price',
                'fx_quote_currency', 'fx_locked_at', 'fx_snapshot',
            ),
        }),
        ('审批信息', {
            'classes': ('collapse',),
            'fields': ('approved_by', 'approved_at', 'reject_reason'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('created_by', 'remark', 'created_at', 'updated_at'),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(business_type='vps')

    def save_model(self, request, obj, form, change):
        obj.business_type = ServiceOrder.BusinessType.VPS
        super().save_model(request, obj, form, change)

    def vps_spec_summary(self, obj):
        parts = []
        if obj.requested_vcpu:    parts.append(f'{obj.requested_vcpu}C')
        if obj.requested_ram_mb:  parts.append(f'{obj.requested_ram_mb}MB')
        if obj.requested_disk_gb: parts.append(f'{obj.requested_disk_gb}GB')
        if obj.requested_os:      parts.append(obj.requested_os)
        if obj.requested_bandwidth_mbps: parts.append(f'{obj.requested_bandwidth_mbps}M')
        if obj.requested_ip_count: parts.append(f'{obj.requested_ip_count}IP')
        return ' / '.join(parts) if parts else '—'
    vps_spec_summary.short_description = 'VPS 需求规格'


# ═══════════════════════════════════════════════════════════════
# § 2d  边缘加速工单（代理模型 Admin，JS 动态切换协议字段）
# ═══════════════════════════════════════════════════════════════

@admin.register(EdgeServiceOrder)
class EdgeServiceOrderAdmin(ServiceOrderBaseAdmin):
    """
    边缘加速工单 — 根据所选协议（GRE / WireGuard / Socks5）
    通过 JS 动态显示对应配置字段。
    """
    list_display = (
        'order_no', 'customer', 'edge_protocol_badge',
        'order_type_badge', 'status_badge',
        'edge_spec_summary', 'preferred_pop',
        'quoted_price_display', 'service_start', 'service_end',
        'created_by', 'created_at',
    )
    list_filter = ('business_type', 'status', 'order_type', 'billing_cycle', 'created_at')
    search_fields = (
        'order_no', 'customer__name', 'customer__customer_no', 'requirements',
    )
    readonly_fields = ('order_no', 'fx_quote_currency', 'fx_locked_at', 'fx_snapshot', 'created_at', 'updated_at')

    # ── fieldsets 中通过 classes 标记各协议专属区块 ──────────
    fieldsets = (
        ('工单基本信息', {
            'fields': (
                'order_no',
                'business_type',    # JS 监听此字段切换下方区块
                'order_type', 'status',
                'customer', 'contract', 'product',
            ),
        }),
        # 边缘加速通用（所有协议都显示）
        ('资源需求（通用）', {
            'classes': ('edge-common-section',),
            'fields': (
                'preferred_pop',
                'requested_bandwidth_mbps',
                'requested_ip_count',
            ),
        }),
        # GRE 专属
        ('GRE 隧道配置', {
            'classes': ('edge-gre-section',),
            'fields': ('edge_local_ip', 'edge_remote_ip'),
        }),
        # WireGuard 专属
        ('WireGuard 配置', {
            'classes': ('edge-wg-section',),
            'fields': (
                'edge_wg_port', 'edge_wg_keepalive',
                'edge_wg_allowed_ips', 'edge_wg_client_pubkey',
            ),
        }),
        # Socks5 专属
        ('Socks5 配置', {
            'classes': ('edge-socks5-section',),
            'fields': ('edge_socks5_port', 'edge_socks5_user', 'edge_socks5_pass'),
        }),
        ('需求说明', {
            'fields': ('requirements',),
        }),
        ('服务期限与费用', {
            'fields': (
                'service_start', 'service_end', 'billing_cycle', 'quoted_price',
                'fx_quote_currency', 'fx_locked_at', 'fx_snapshot',
            ),
        }),
        ('审批信息', {
            'classes': ('collapse',),
            'fields': ('approved_by', 'approved_at', 'reject_reason'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('created_by', 'remark', 'created_at', 'updated_at'),
        }),
    )

    class Media:
        js = ('service_manager/js/order_form.js',)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            business_type__in=['edge_gre', 'edge_wg', 'edge_socks5'],
        )

    def edge_protocol_badge(self, obj):
        return badge(
            obj.get_business_type_display(),
            BUSINESS_TYPE_COLORS.get(obj.business_type, '#6c757d'),
        )
    edge_protocol_badge.short_description = '加速协议'
    edge_protocol_badge.admin_order_field = 'business_type'

    def edge_spec_summary(self, obj):
        parts = []
        if obj.requested_bandwidth_mbps:
            parts.append(f'{obj.requested_bandwidth_mbps}M')
        if obj.requested_ip_count:
            parts.append(f'{obj.requested_ip_count}IP')
        if obj.business_type == 'edge_gre' and obj.edge_local_ip:
            parts.append(f'本端:{obj.edge_local_ip}')
        if obj.business_type == 'edge_wg' and obj.edge_wg_port:
            parts.append(f'端口:{obj.edge_wg_port}')
        if obj.business_type == 'edge_socks5' and obj.edge_socks5_port:
            parts.append(f'Socks5:{obj.edge_socks5_port}')
        return ' / '.join(parts) if parts else '—'
    edge_spec_summary.short_description = '加速需求'


# ═══════════════════════════════════════════════════════════════
# § 3  服务实例
# ═══════════════════════════════════════════════════════════════

class AllocationInline(admin.TabularInline):
    model = ServiceAllocation
    extra = 0
    fields = ('action_type', 'description', 'resource_type', 'resource_repr', 'operator', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


class ChangeRecordInline(admin.TabularInline):
    model = ServiceChangeRecord
    extra = 0
    fields = ('change_no', 'change_type', 'status', 'change_reason', 'scheduled_at', 'executed_at')
    readonly_fields = ('change_no',)
    show_change_link = True


class InstanceLogInline(admin.TabularInline):
    model = ServiceOperationLog
    fk_name = 'service_instance'
    extra = 0
    fields = ('action_type', 'description', 'operator', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


class ExpiryActionInline(admin.TabularInline):
    model = ServiceExpiryAction
    extra = 0
    fields = ('action', 'lead_days', 'due_on', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ServiceInstance)
class ServiceInstanceAdmin(admin.ModelAdmin):
    list_display = (
        'instance_no', 'customer_display', 'service_type_badge', 'status_badge',
        'resource_display', 'ip_count', 'expire_time', 'suspend_at', 'recycle_at',
        'provisioned_at',
    )
    list_filter = ('status', 'service_type', 'created_at')
    search_fields = (
        'instance_no',
        'service_order__order_no',
        'service_order__customer__name',
        'vps__vps_no', 'edge__edge_no',
    )
    readonly_fields = (
        'instance_no', 'created_at', 'updated_at',
        'provisioned_at', 'activated_at', 'suspended_at', 'terminated_at',
    )
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True
    filter_horizontal = ('ip_addresses',)
    inlines = [AllocationInline, ChangeRecordInline, ExpiryActionInline, InstanceLogInline]

    fieldsets = (
        ('基本信息', {
            'fields': ('instance_no', 'service_order', 'service_type', 'status', 'remark'),
        }),
        ('资源绑定', {
            'fields': ('vps', 'edge', 'bandwidth_alloc', 'ip_addresses'),
        }),
        ('规格快照', {
            'classes': ('collapse',),
            'fields': ('spec_snapshot',),
        }),
        ('到期日程', {
            'fields': ('expire_time', 'grace_days', 'suspend_at', 'recycle_at'),
        }),
        ('生命周期', {
            'fields': (
                'provisioned_at', 'activated_at', 'suspended_at', 'terminated_at',
                'provisioned_by', 'terminated_by',
            ),
        }),
        ('时间', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def customer_display(self, obj):
        return obj.service_order.customer.name
    customer_display.short_description = '客户'

    def service_type_badge(self, obj):
        colors = {
            'vps': '#007bff', 'edge': '#6f42c1', 'ip': '#17a2b8', 'bandwidth': '#28a745',
        }
        return badge(obj.get_service_type_display(), colors.get(obj.service_type, '#6c757d'))
    service_type_badge.short_description = '服务类型'
    service_type_badge.admin_order_field = 'service_type'

    def status_badge(self, obj):
        return badge(obj.get_status_display(), INSTANCE_STATUS_COLORS.get(obj.status, '#6c757d'))
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def resource_display(self, obj):
        if obj.vps:
            return format_html('<small>[VPS] {}</small>', obj.vps.name)
        if obj.edge:
            return format_html('<small>[EDGE] {}</small>', obj.edge.name)
        if obj.bandwidth_alloc:
            return format_html('<small>[BW] {}Mbps</small>', obj.bandwidth_alloc.allocated_mbps)
        return '—'
    resource_display.short_description = '绑定资源'

    def ip_count(self, obj):
        cnt = obj.ip_addresses.count()
        return cnt if cnt else '—'
    ip_count.short_description = 'IP 数'

    def save_model(self, request, obj, form, change):
        old_status = None
        expire_changed = not change
        if change:
            old = ServiceInstance.objects.get(pk=obj.pk)
            old_status = old.status
            expire_changed = (
                old.expire_time != obj.expire_time or old.grace_days != obj.grace_days
            )
        if expire_changed and obj.expire_time:
            from service_manager.expiry_engine import apply_schedule_to_instance
            apply_schedule_to_instance(obj, force=True)
        super().save_model(request, obj, form, change)
        if not change:
            _log_service(
                operator=request.user,
                action_type=ServiceOperationLog.ActionType.PROVISION,
                description=f'创建服务实例 [{obj.instance_no}]',
                service_order=obj.service_order,
                service_instance=obj, request=request,
            )
        elif old_status and old_status != obj.status:
            action_map = {
                'active': ServiceOperationLog.ActionType.ACTIVATE,
                'suspended': ServiceOperationLog.ActionType.SUSPEND,
                'terminated': ServiceOperationLog.ActionType.TERMINATE,
            }
            _log_service(
                operator=request.user,
                action_type=action_map.get(obj.status, ServiceOperationLog.ActionType.OTHER),
                description=f'实例 [{obj.instance_no}] 状态: {old_status} → {obj.status}',
                service_order=obj.service_order,
                service_instance=obj, request=request,
                old_value={'status': old_status}, new_value={'status': obj.status},
            )

    @admin.action(description='激活所选服务实例')
    def action_activate(self, request, queryset):
        count = sum(
            1 for inst in queryset.filter(status__in=['provisioning', 'suspended'])
            if not inst.activate(operator=request.user) or True
        )
        self.message_user(request, f'已激活 {count} 个服务实例')

    @admin.action(description='暂停所选服务实例')
    def action_suspend(self, request, queryset):
        count = sum(
            1 for inst in queryset.filter(status='active')
            if not inst.suspend(operator=request.user) or True
        )
        self.message_user(request, f'已暂停 {count} 个服务实例')

    @admin.action(description='终止所选服务实例（释放资源）')
    def action_terminate(self, request, queryset):
        count = sum(
            1 for inst in queryset.exclude(status='terminated')
            if not inst.terminate(operator=request.user) or True
        )
        self.message_user(request, f'已终止 {count} 个服务实例，资源已释放')

    actions = [action_activate, action_suspend, action_terminate]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'service_order__customer', 'vps', 'edge', 'bandwidth_alloc',
            'provisioned_by', 'terminated_by',
        ).prefetch_related('ip_addresses')


# ═══════════════════════════════════════════════════════════════
# § 4  资源分配记录
# ═══════════════════════════════════════════════════════════════

@admin.register(ServiceAllocation)
class ServiceAllocationAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'service_instance', 'action_type_badge',
        'description_short', 'resource_type', 'resource_repr', 'operator',
    )
    list_filter = ('action_type', 'created_at', 'resource_type')
    search_fields = (
        'description', 'resource_repr',
        'service_instance__instance_no',
        'service_instance__service_order__customer__name',
    )
    readonly_fields = (
        'service_instance', 'action_type', 'description',
        'before_state', 'after_state',
        'resource_type', 'resource_id', 'resource_repr',
        'operator', 'created_at',
    )
    date_hierarchy = 'created_at'
    list_per_page = 30

    fieldsets = (
        ('操作信息', {'fields': ('created_at', 'action_type', 'service_instance', 'operator')}),
        ('资源详情', {'fields': ('resource_type', 'resource_id', 'resource_repr', 'description')}),
        ('状态变更', {'classes': ('collapse',), 'fields': ('before_state', 'after_state')}),
        ('备注',     {'classes': ('collapse',), 'fields': ('remark',)}),
    )

    def action_type_badge(self, obj):
        return badge(obj.get_action_type_display(), ALLOC_ACTION_COLORS.get(obj.action_type, '#adb5bd'))
    action_type_badge.short_description = '操作类型'
    action_type_badge.admin_order_field = 'action_type'

    def description_short(self, obj):
        return obj.description[:60] + '…' if len(obj.description) > 60 else obj.description
    description_short.short_description = '操作描述'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'service_instance__service_order__customer', 'operator',
        )


# ═══════════════════════════════════════════════════════════════
# § 5  服务变更记录
# ═══════════════════════════════════════════════════════════════

@admin.register(ServiceChangeRecord)
class ServiceChangeRecordAdmin(admin.ModelAdmin):
    list_display = (
        'change_no', 'service_instance', 'customer_display',
        'change_type_badge', 'status_badge', 'price_delta_display',
        'scheduled_at', 'executed_at', 'requested_by',
    )
    list_filter = ('change_type', 'status', 'created_at')
    search_fields = (
        'change_no', 'change_reason',
        'service_instance__instance_no',
        'service_instance__service_order__customer__name',
    )
    readonly_fields = ('change_no', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    save_on_top = True

    fieldsets = (
        ('变更基本信息', {
            'fields': ('change_no', 'service_instance', 'change_type',
                       'status', 'change_reason', 'change_detail'),
        }),
        ('变更规格', {'fields': ('before_spec', 'after_spec')}),
        ('费用与时间', {'fields': ('price_delta', 'scheduled_at', 'executed_at')}),
        ('审批', {
            'classes': ('collapse',),
            'fields': ('approved_by', 'approved_at', 'reject_reason'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('requested_by', 'remark', 'created_at', 'updated_at'),
        }),
    )

    def customer_display(self, obj):
        return obj.service_instance.service_order.customer.name
    customer_display.short_description = '客户'

    def change_type_badge(self, obj):
        colors = {
            'upgrade': '#28a745', 'downgrade': '#ffc107', 'extend': '#17a2b8',
            'ip_change': '#007bff', 'bw_change': '#fd7e14',
            'protocol_change': '#6f42c1', 'migrate': '#e83e8c', 'other': '#6c757d',
        }
        return badge(obj.get_change_type_display(), colors.get(obj.change_type, '#6c757d'))
    change_type_badge.short_description = '变更类型'
    change_type_badge.admin_order_field = 'change_type'

    def status_badge(self, obj):
        return badge(obj.get_status_display(), CHANGE_STATUS_COLORS.get(obj.status, '#6c757d'))
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def price_delta_display(self, obj):
        if obj.price_delta is None:
            return '—'
        color = '#28a745' if obj.price_delta >= 0 else '#dc3545'
        sign = '+' if obj.price_delta >= 0 else ''
        return format_html(
            '<span style="color:{};font-weight:bold;">{}¥{}</span>',
            color, sign, f'{obj.price_delta:,.2f}',
        )
    price_delta_display.short_description = '价差'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.requested_by = obj.requested_by or request.user
        old_status = None
        if change:
            old_status = ServiceChangeRecord.objects.get(pk=obj.pk).status
        super().save_model(request, obj, form, change)
        if old_status and old_status != obj.status:
            if obj.status == 'approved':
                obj.approved_by = obj.approved_by or request.user
                obj.approved_at = obj.approved_at or timezone.now()
                obj.save(update_fields=['approved_by', 'approved_at'])
            _log_service(
                operator=request.user,
                action_type=ServiceOperationLog.ActionType.CHANGE,
                description=f'变更单 [{obj.change_no}] 状态: {old_status} → {obj.status}',
                service_instance=obj.service_instance,
                service_order=obj.service_instance.service_order,
                request=request,
            )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'service_instance__service_order__customer',
            'requested_by', 'approved_by',
        )


# ═══════════════════════════════════════════════════════════════
# § 6  服务操作日志（只读）
# ═══════════════════════════════════════════════════════════════

@admin.register(ServiceOperationLog)
class ServiceOperationLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'action_type_badge', 'description_short',
        'service_order', 'service_instance', 'operator',
    )
    list_filter = ('action_type', 'created_at', 'operator')
    search_fields = (
        'description', 'service_order__order_no',
        'service_instance__instance_no', 'operator__username',
    )
    readonly_fields = (
        'operator', 'action_type', 'description',
        'service_order', 'service_instance',
        'old_value', 'new_value', 'ip_address', 'created_at',
    )
    date_hierarchy = 'created_at'
    list_per_page = 30

    fieldsets = (
        ('操作信息', {'fields': ('created_at', 'action_type', 'operator', 'ip_address')}),
        ('关联对象', {'fields': ('service_order', 'service_instance', 'description')}),
        ('变更详情', {'classes': ('collapse',), 'fields': ('old_value', 'new_value')}),
    )

    def action_type_badge(self, obj):
        return badge(obj.get_action_type_display(), LOG_ACTION_COLORS.get(obj.action_type, '#adb5bd'))
    action_type_badge.short_description = '操作类型'
    action_type_badge.admin_order_field = 'action_type'

    def description_short(self, obj):
        return obj.description[:60] + '…' if len(obj.description) > 60 else obj.description
    description_short.short_description = '操作描述'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'operator', 'service_order__customer', 'service_instance',
        )


@admin.register(ServiceLifecycleRecord)
class ServiceLifecycleRecordAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'service_order', 'stage', 'status',
        'submitted_by', 'handled_by', 'approved_by',
        'completed_at', 'created_at',
    )
    list_filter = ('stage', 'status')
    search_fields = ('service_order__order_no', 'title', 'detail')
    readonly_fields = (
        'service_order', 'service_instance', 'change_record',
        'resource_before', 'resource_after', 'fee_snapshot',
        'created_at', 'updated_at',
    )


@admin.register(ServiceProvisionHook)
class ServiceProvisionHookAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'hook_type', 'is_active', 'endpoint_url', 'adapter_key')
    list_filter = ('event', 'hook_type', 'is_active')
    search_fields = ('name', 'endpoint_url', 'adapter_key')


@admin.register(ServiceManualDelivery)
class ServiceManualDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'service_order', 'reason', 'status',
        'token_prefix', 'last_callback_at', 'completed_at',
    )
    list_filter = ('reason', 'status')
    search_fields = ('service_order__order_no', 'token_prefix', 'last_callback_note')
    readonly_fields = (
        'service_order', 'reason', 'status', 'items',
        'token_hash', 'token_prefix', 'token_rotated_at',
        'last_callback_at', 'last_callback_note', 'resource_edge',
        'completed_at', 'created_at', 'updated_at',
    )


class SupportTicketMessageInline(admin.TabularInline):
    model = SupportTicketMessage
    extra = 0
    readonly_fields = ('author_type', 'is_internal', 'content', 'author', 'created_at')


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = (
        'ticket_no', 'customer', 'category', 'priority', 'status',
        'handler', 'first_response_due', 'resolve_due',
        'sla_response_breached', 'sla_resolve_breached', 'created_at',
    )
    list_filter = ('category', 'priority', 'status', 'source', 'sla_response_breached')
    search_fields = ('ticket_no', 'subject', 'customer__name')
    readonly_fields = (
        'ticket_no', 'first_responded_at', 'resolved_at', 'closed_at',
        'escalated_at', 'created_at', 'updated_at',
    )
    inlines = [SupportTicketMessageInline]


@admin.register(OsImage)
class OsImageAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'os_type', 'version', 'enabled', 'sort_order')
    list_filter = ('os_type', 'enabled')
    search_fields = ('code', 'name')


@admin.register(InstanceSnapshot)
class InstanceSnapshotAdmin(admin.ModelAdmin):
    list_display = ('snapshot_no', 'vps', 'name', 'status', 'created_at', 'restored_at')
    list_filter = ('status',)
    search_fields = ('snapshot_no', 'name', 'vps__vps_no')
    readonly_fields = ('snapshot_no', 'created_at', 'updated_at', 'restored_at')


@admin.register(SelfServiceJob)
class SelfServiceJobAdmin(admin.ModelAdmin):
    list_display = ('job_no', 'job_type', 'status', 'vps', 'progress', 'created_at', 'finished_at')
    list_filter = ('job_type', 'status')
    search_fields = ('job_no', 'vps__vps_no', 'result_message')
    readonly_fields = (
        'job_no', 'request_payload', 'created_at', 'updated_at',
        'started_at', 'finished_at',
    )


@admin.register(PopCapacityReservation)
class PopCapacityReservationAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'service_order', 'pop_point', 'status',
        'cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count',
        'expires_at', 'created_at',
    )
    list_filter = ('status',)
    search_fields = ('service_order__order_no', 'pop_point__name', 'pop_point__code')
    readonly_fields = (
        'pop_point', 'service_order', 'status',
        'cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count',
        'expires_at', 'held_at', 'consumed_at', 'released_at', 'release_reason',
        'created_at', 'updated_at',
    )
