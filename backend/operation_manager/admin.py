from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ZabbixServer,
    MonitoringHostTemplate,
    MonitoringHost,
    MonitoringItem,
    MonitoringGraph,
    MonitoringGraphProfile,
    MonitoringGraphSeries,
    MonitoringGraphBinding,
    MonitoringTrafficReport,
    MonitoringSyncLog,
    PopQualityMonitor,
    PopQualityProbe,
)


def badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;'
        'border-radius:4px;font-size:12px;white-space:nowrap;">{}</span>',
        color, text,
    )


SYNC_STATUS_COLORS = {
    'pending': '#6c757d',
    'syncing': '#17a2b8',
    'synced': '#28a745',
    'error': '#dc3545',
    'disabled': '#adb5bd',
}
LOG_STATUS_COLORS = {
    'success': '#28a745',
    'partial': '#ffc107',
    'failed': '#dc3545',
}
QUALITY_STATUS_COLORS = {
    'healthy': '#28a745',
    'degraded': '#ffc107',
    'unreachable': '#dc3545',
    'error': '#dc3545',
    'skipped': '#6c757d',
    'disabled': '#adb5bd',
    'unknown': '#6c757d',
}
GRAPH_KIND_COLORS = {
    'system': '#6f42c1',
    'traffic': '#007bff',
    'icmp': '#20c997',
    'billing': '#fd7e14',
    'custom': '#6c757d',
}


@admin.register(ZabbixServer)
class ZabbixServerAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'api_url', 'auth_type', 'is_default', 'is_active',
        'last_sync_status', 'last_sync_at',
    )
    list_filter = ('is_active', 'is_default', 'auth_type')
    search_fields = ('name', 'api_url', 'default_host_group_name')
    readonly_fields = ('last_sync_at', 'last_sync_status', 'last_sync_message', 'created_at', 'updated_at')
    fieldsets = (
        ('基本', {
            'fields': ('name', 'api_url', 'is_default', 'is_active', 'remark'),
        }),
        ('认证', {
            'fields': ('auth_type', 'api_token', 'username', 'password'),
        }),
        ('连接', {
            'fields': ('verify_ssl', 'timeout_sec'),
        }),
        ('Zabbix 默认', {
            'fields': ('default_host_group_id', 'default_host_group_name'),
        }),
        ('同步状态', {
            'fields': ('last_sync_at', 'last_sync_status', 'last_sync_message'),
        }),
        ('时间', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(MonitoringHostTemplate)
class MonitoringHostTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'target_kind', 'monitor_mode', 'zabbix_server',
        'auto_create_items', 'auto_create_graphs', 'is_active',
    )
    list_filter = ('target_kind', 'monitor_mode', 'is_active', 'zabbix_server')
    search_fields = ('name', 'remark')
    filter_horizontal = ()
    fieldsets = (
        ('基本', {'fields': ('name', 'target_kind', 'monitor_mode', 'zabbix_server', 'is_active', 'remark')}),
        ('Zabbix 模板', {
            'fields': (
                'zabbix_host_group_ids', 'zabbix_template_ids',
                'zabbix_interface_type', 'snmp_community_macro', 'icmp_use_mgmt_ip',
            ),
        }),
        ('自动发现', {
            'fields': ('auto_create_items', 'auto_create_graphs', 'graph_name_patterns'),
        }),
    )


class MonitoringItemInline(admin.TabularInline):
    model = MonitoringItem
    extra = 0
    fields = ('name', 'item_key', 'units', 'last_value', 'last_clock', 'is_primary_traffic', 'is_enabled')
    readonly_fields = ('last_value', 'last_clock')
    show_change_link = True


class MonitoringGraphInline(admin.TabularInline):
    model = MonitoringGraph
    extra = 0
    fields = ('name', 'graph_kind', 'zabbix_graphid', 'is_default', 'is_enabled')
    show_change_link = True


@admin.register(MonitoringGraphProfile)
class MonitoringGraphProfileAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'profile_type', 'aggregation', 'percentile_n',
        'billing_mode', 'unit_display', 'is_system', 'is_active',
    )
    list_filter = ('profile_type', 'aggregation', 'is_active', 'is_system')
    search_fields = ('code', 'name')
    readonly_fields = ('created_at', 'updated_at')


class MonitoringGraphSeriesInline(admin.TabularInline):
    model = MonitoringGraphSeries
    extra = 0
    autocomplete_fields = ('monitoring_item',)


@admin.register(MonitoringHost)
class MonitoringHostAdmin(admin.ModelAdmin):
    list_display = (
        'resource_label_col', 'host_kind', 'zabbix_server',
        'monitored_ip', 'sync_status_badge', 'enable_icmp', 'enable_snmp', 'last_sync_at',
    )
    list_filter = ('host_kind', 'sync_status', 'enable_icmp', 'enable_snmp', 'zabbix_server')
    search_fields = ('hostname', 'visible_name', 'zabbix_hostid', 'monitored_ip')
    readonly_fields = (
        'zabbix_hostid', 'sync_status', 'last_sync_at', 'last_sync_error',
        'zabbix_raw', 'created_at', 'updated_at', 'resource_label_col',
    )
    autocomplete_fields = ('pop_point', 'service_pop_point', 'zabbix_server', 'template')
    inlines = [MonitoringItemInline, MonitoringGraphInline]
    fieldsets = (
        ('资源绑定', {
            'fields': (
                'host_kind', 'pop_point', 'service_pop_point',
                'resource_label_col', 'template', 'zabbix_server',
            ),
        }),
        ('Zabbix Host', {
            'fields': (
                'hostname', 'visible_name', 'zabbix_hostid',
                'monitored_ip', 'snmp_community',
                'enable_icmp', 'enable_snmp', 'is_enabled',
            ),
        }),
        ('同步', {
            'fields': ('sync_status', 'last_sync_at', 'last_sync_error', 'zabbix_raw'),
        }),
        ('其他', {'fields': ('remark', 'created_by', 'created_at', 'updated_at')}),
    )

    @admin.display(description='监控对象')
    def resource_label_col(self, obj):
        return obj.resource_label

    @admin.display(description='同步状态')
    def sync_status_badge(self, obj):
        color = SYNC_STATUS_COLORS.get(obj.sync_status, '#6c757d')
        return badge(obj.get_sync_status_display(), color)


@admin.register(MonitoringItem)
class MonitoringItemAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'monitoring_host', 'item_key', 'units',
        'last_value', 'last_clock', 'is_primary_traffic', 'is_enabled',
    )
    list_filter = ('is_enabled', 'is_primary_traffic', 'value_type')
    search_fields = ('name', 'item_key', 'zabbix_itemid')
    readonly_fields = ('zabbix_itemid', 'zabbix_raw', 'synced_at')
    autocomplete_fields = ('monitoring_host',)


@admin.register(MonitoringGraph)
class MonitoringGraphAdmin(admin.ModelAdmin):
    list_display = (
        'effective_title_col', 'monitoring_host', 'source_type', 'graph_kind_badge',
        'interface_name', 'billing_enabled', 'zabbix_graphid', 'is_default', 'is_enabled',
    )
    list_filter = ('source_type', 'graph_kind', 'billing_enabled', 'is_default', 'is_enabled')
    search_fields = ('name', 'display_title', 'interface_name', 'zabbix_graphid')
    filter_horizontal = ('items',)
    inlines = [MonitoringGraphSeriesInline]
    readonly_fields = ('zabbix_graphid', 'zabbix_itemids', 'zabbix_raw', 'synced_at')
    autocomplete_fields = ('monitoring_host', 'profile')

    @admin.display(description='标题')
    def effective_title_col(self, obj):
        return obj.effective_title

    @admin.display(description='分类')
    def graph_kind_badge(self, obj):
        color = GRAPH_KIND_COLORS.get(obj.graph_kind, '#6c757d')
        return badge(obj.get_graph_kind_display(), color)


@admin.register(MonitoringGraphSeries)
class MonitoringGraphSeriesAdmin(admin.ModelAdmin):
    list_display = ('graph', 'series_role', 'label', 'monitoring_item', 'display_order', 'is_visible')
    list_filter = ('series_role', 'is_visible')
    autocomplete_fields = ('graph', 'monitoring_item')


@admin.register(MonitoringTrafficReport)
class MonitoringTrafficReportAdmin(admin.ModelAdmin):
    list_display = (
        'report_no', 'title', 'target_type', 'interface_name',
        'time_from', 'time_till', 'percentile_n', 'percentile_mbps_col',
        'status', 'created_at',
    )
    list_filter = ('target_type', 'status', 'percentile_n')
    search_fields = ('report_no', 'title', 'interface_name')
    readonly_fields = (
        'report_no', 'result', 'computed_at', 'created_at',
        'peak_bps', 'avg_bps', 'percentile_bps', 'sample_count',
    )
    date_hierarchy = 'time_from'

    @admin.display(description='95峰值(Mbps)')
    def percentile_mbps_col(self, obj):
        v = obj.percentile_mbps
        return f'{v:.4f}' if v is not None else '-'

    def has_add_permission(self, request):
        return False


@admin.register(MonitoringGraphBinding)
class MonitoringGraphBindingAdmin(admin.ModelAdmin):
    list_display = (
        'graph', 'monitoring_item', 'target_type', 'panel_slot', 'panel_title',
        'display_order', 'is_visible',
    )
    list_filter = ('target_type', 'panel_slot', 'is_visible')
    search_fields = ('panel_title', 'graph__name', 'monitoring_item__name')
    autocomplete_fields = ('graph', 'monitoring_item', 'monitoring_host', 'pop_point', 'vps', 'edge')


@admin.register(MonitoringSyncLog)
class MonitoringSyncLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'action_type', 'status_badge',
        'zabbix_server', 'monitoring_host', 'message', 'duration_ms', 'operator',
    )
    list_filter = ('action_type', 'status', 'created_at')
    search_fields = ('message', 'monitoring_host__hostname')
    readonly_fields = (
        'zabbix_server', 'monitoring_host', 'action_type', 'status',
        'message', 'detail', 'operator', 'duration_ms', 'created_at',
    )
    date_hierarchy = 'created_at'

    @admin.display(description='状态')
    def status_badge(self, obj):
        color = LOG_STATUS_COLORS.get(obj.status, '#6c757d')
        return badge(obj.get_status_display(), color)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PopQualityMonitor)
class PopQualityMonitorAdmin(admin.ModelAdmin):
    list_display = (
        'pop_point', 'status_badge', 'is_enabled', 'enable_icmp', 'enable_http',
        'last_icmp_packet_loss_pct', 'last_icmp_avg_ms', 'last_http_response_ms',
        'alert_active', 'last_probe_at',
    )
    list_filter = ('is_enabled', 'enable_icmp', 'enable_http', 'status', 'alert_active')
    search_fields = ('pop_point__code', 'pop_point__name', 'pop_point__mgmt_ip', 'pop_point__mgmt_url')
    autocomplete_fields = ('pop_point',)
    readonly_fields = (
        'status', 'last_probe_at', 'next_probe_at', 'probe_lock_until',
        'last_icmp_packet_loss_pct', 'last_icmp_avg_ms',
        'last_http_response_ms', 'last_http_status_code', 'last_error',
        'consecutive_breaches', 'alert_active', 'alert_started_at',
        'last_alert_at', 'last_recovered_at', 'created_at', 'updated_at',
    )
    fieldsets = (
        ('POP 与探测', {
            'fields': (
                'pop_point', 'is_enabled', 'enable_icmp', 'enable_http',
                'probe_interval_seconds', 'timeout_seconds', 'icmp_count',
            ),
        }),
        ('告警策略', {
            'fields': (
                'packet_loss_threshold_pct', 'consecutive_breach_threshold',
                'alert_repeat_minutes',
            ),
        }),
        ('当前状态', {
            'fields': (
                'status', 'last_probe_at', 'next_probe_at', 'probe_lock_until',
                'last_icmp_packet_loss_pct', 'last_icmp_avg_ms',
                'last_http_response_ms', 'last_http_status_code', 'last_error',
                'consecutive_breaches', 'alert_active', 'alert_started_at',
                'last_alert_at', 'last_recovered_at',
            ),
        }),
        ('其他', {'fields': ('remark', 'created_at', 'updated_at')}),
    )

    @admin.display(description='当前状态')
    def status_badge(self, obj):
        return badge(obj.get_status_display(), QUALITY_STATUS_COLORS.get(obj.status, '#6c757d'))


@admin.register(PopQualityProbe)
class PopQualityProbeAdmin(admin.ModelAdmin):
    list_display = (
        'started_at', 'monitor', 'status', 'icmp_target',
        'icmp_packet_loss_pct', 'icmp_avg_ms', 'http_status_code',
        'http_response_ms', 'alert_kind',
    )
    list_filter = ('status', 'alert_kind', 'started_at')
    search_fields = ('monitor__pop_point__code', 'monitor__pop_point__name', 'icmp_target', 'http_target')
    autocomplete_fields = ('monitor',)
    readonly_fields = (
        'monitor', 'status', 'icmp_target', 'icmp_sent', 'icmp_received',
        'icmp_packet_loss_pct', 'icmp_min_ms', 'icmp_avg_ms', 'icmp_max_ms',
        'icmp_success', 'http_target', 'http_status_code', 'http_response_ms',
        'http_success', 'error_message', 'alert_kind', 'started_at',
        'finished_at', 'created_at',
    )
    date_hierarchy = 'started_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
