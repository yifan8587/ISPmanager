from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum

from .models import (
    Region, Country, City, AvailabilityZone,
    Carrier, NetworkTypeOption,
    PopPoint,
    NetworkInterface, RouteTable, StaticRoute, PolicyRoute,
    IPBlock, IPAddress,
    VPS, VPSNetworkInterface,
    EdgeAcceleration, GREConfig, WireguardConfig, Socks5Config,
    BandwidthAllocation,
    ResourceOperationLog,
    record_resource_log, resource_model_snapshot,
)


# ─────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────

def badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;'
        'border-radius:4px;font-size:12px;white-space:nowrap;">{}</span>',
        color, text,
    )


POP_STATUS_COLORS = {
    'online': '#28a745', 'offline': '#dc3545',
    'maintenance': '#ffc107', 'decommissioned': '#6c757d',
}
VPS_STATUS_COLORS = {
    'running': '#28a745', 'stopped': '#6c757d',
    'suspended': '#ffc107', 'creating': '#17a2b8',
    'deleting': '#fd7e14', 'error': '#dc3545',
}
EDGE_STATUS_COLORS = {
    'active': '#28a745', 'stopped': '#6c757d',
    'pending': '#ffc107', 'error': '#dc3545',
}
IP_STATUS_COLORS = {
    'available': '#28a745', 'assigned': '#007bff',
    'reserved': '#fd7e14', 'blocked': '#dc3545', 'system': '#6c757d',
}
RES_TYPE_COLORS = {
    'native': '#007bff', 'home_broadband': '#6f42c1',
    'broadcast': '#fd7e14', 'isp': '#17a2b8',
    'dual_isp': '#20c997', 'idc': '#e83e8c', 'non_isp': '#6c757d',
}
ACTION_COLORS = {
    'create': '#28a745', 'update': '#007bff', 'delete': '#dc3545',
    'status_change': '#fd7e14', 'deploy': '#6f42c1', 'restart': '#ffc107',
    'ip_assign': '#17a2b8', 'ip_release': '#6c757d',
    'bw_alloc': '#20c997', 'config_change': '#e83e8c',
    'import': '#adb5bd', 'export': '#adb5bd', 'other': '#adb5bd',
}


# ═══════════════════════════════════════════════════════════════
# § 1  地理位置
# ═══════════════════════════════════════════════════════════════

class CountryInline(admin.TabularInline):
    model = Country
    extra = 0
    fields = ('name', 'name_en')
    show_change_link = True


class CityInline(admin.TabularInline):
    model = City
    extra = 0
    fields = ('name', 'name_en', 'timezone')
    show_change_link = True


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'name_en', 'country_count')
    inlines = [CountryInline]
    search_fields = ('name', 'name_en', 'code')

    def country_count(self, obj):
        return obj.countries.count()
    country_count.short_description = '国家数'


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('name', 'name_en', 'region', 'city_count')
    list_filter = ('region',)
    search_fields = ('name', 'name_en')
    inlines = [CityInline]

    def city_count(self, obj):
        return obj.cities.count()
    city_count.short_description = '城市数'


class AvailabilityZoneInline(admin.TabularInline):
    model = AvailabilityZone
    extra = 0
    fields = ('name', 'name_en', 'code', 'remark')
    show_change_link = True


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ('name', 'name_en', 'country', 'timezone', 'pop_count')
    list_filter = ('country__region', 'country')
    search_fields = ('name', 'name_en', 'country__name')
    inlines = [AvailabilityZoneInline]

    def pop_count(self, obj):
        return obj.pop_points.count()
    pop_count.short_description = 'POP 数'


@admin.register(AvailabilityZone)
class AvailabilityZoneAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'city', 'pop_count')
    list_filter = ('city__country__region', 'city__country', 'city')
    search_fields = ('name', 'name_en', 'code', 'city__name')

    def pop_count(self, obj):
        return obj.pop_points.count()
    pop_count.short_description = 'POP 数'


# ═══════════════════════════════════════════════════════════════
# § 2  运营商
# ═══════════════════════════════════════════════════════════════

@admin.register(Carrier)
class CarrierAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'pop_count', 'created_at')
    search_fields = ('code', 'name')
    filter_horizontal = ('regions',)
    fieldsets = (
        ('基本信息', {'fields': ('code', 'name', 'remark')}),
        ('覆盖大区', {'fields': ('regions',)}),
    )

    def pop_count(self, obj):
        return obj.pop_points.count()
    pop_count.short_description = 'POP 数'


@admin.register(NetworkTypeOption)
class NetworkTypeOptionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'sort_order', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    ordering = ('sort_order', 'id')


# ═══════════════════════════════════════════════════════════════
# § 3  POP 点
# ═══════════════════════════════════════════════════════════════

class NetworkInterfaceInline(admin.TabularInline):
    model = NetworkInterface
    extra = 0
    fields = ('ifname', 'interface_type', 'status', 'speed_mbps', 'mtu', 'is_management')
    show_change_link = True


class IPBlockInline(admin.TabularInline):
    model = IPBlock
    extra = 0
    fields = ('cidr', 'ip_version', 'carrier', 'ip_attribute', 'network_type', 'asn_nature', 'is_active')
    show_change_link = True


class BandwidthAllocationInline(admin.TabularInline):
    model = BandwidthAllocation
    extra = 0
    fields = ('target_type', 'vps', 'edge', 'allocated_mbps', 'burst_mbps', 'is_active')
    show_change_link = True


class PopOperationLogInline(admin.TabularInline):
    model = ResourceOperationLog
    fk_name = 'pop_point'
    extra = 0
    fields = ('action_type', 'target_model', 'description', 'operator', 'ip_address', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PopPoint)
class PopPointAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'name', 'pop_type_badge', 'city_info', 'network_type_badge', 'asn_nature_badge',
        'ip_attribute',
        'status_badge', 'deploy_badge', 'bw_display', 'carrier_list',
        'vps_count', 'created_at',
    )
    list_filter = (
        'pop_type', 'status', 'deploy_status', 'network_type', 'asn_nature', 'ip_attribute',
        'city__country__region', 'city__country',
    )
    search_fields = ('code', 'name', 'mgmt_ip', 'city__name', 'city__country__name')
    readonly_fields = ('code', 'created_at', 'updated_at', 'available_bw_display',
                       'last_deploy_at', 'last_sync_at', 'agent_version')
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True
    filter_horizontal = ('carriers',)
    inlines = [NetworkInterfaceInline, IPBlockInline, BandwidthAllocationInline, PopOperationLogInline]

    fieldsets = (
        ('基本属性', {
            'fields': ('code', 'name', 'pop_type', 'status', 'city', 'availability_zone', 'network_type', 'asn_nature', 'ip_attribute', 'carriers', 'remark'),
        }),
        ('管理属性', {
            'fields': (
                'mgmt_ip', 'ssh_port', 'mgmt_username', 'mgmt_password',
                'mgmt_token', 'mgmt_url',
                'snmp_community', 'snmp_version',
            ),
        }),
        ('节点 API / 部署', {
            'fields': (
                'api_base_url', 'api_username', 'api_password', 'api_verify_ssl',
                'deploy_status', 'agent_version', 'last_deploy_at', 'last_sync_at',
            ),
        }),
        ('硬件属性', {
            'classes': ('collapse',),
            'fields': (
                'cpu_model', 'cpu_cores', 'ram_gb', 'disk_info',
                'os_type', 'os_version',
            ),
        }),
        ('网络资源属性', {
            'fields': ('total_bandwidth_mbps', 'used_bandwidth_mbps', 'available_bw_display'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('created_by', 'created_at', 'updated_at'),
        }),
    )

    def city_info(self, obj):
        return format_html(
            '{} / {}',
            obj.city.country.name,
            obj.city.name,
        )
    city_info.short_description = '位置'

    def pop_type_badge(self, obj):
        color = {
            'pop': '#0d6efd',
            'pe': '#6610f2',
            'vdc': '#20c997',
            'other': '#6c757d',
        }.get(obj.pop_type, '#6c757d')
        return badge(obj.get_pop_type_display(), color)
    pop_type_badge.short_description = '节点类型'
    pop_type_badge.admin_order_field = 'pop_type'

    def network_type_badge(self, obj):
        color = RES_TYPE_COLORS.get(obj.network_type, '#6c757d')
        return badge(obj.get_network_type_display(), color)
    network_type_badge.short_description = '网络类型'
    network_type_badge.admin_order_field = 'network_type'

    def asn_nature_badge(self, obj):
        color = RES_TYPE_COLORS.get(obj.asn_nature, '#6c757d')
        return badge(obj.get_asn_nature_display(), color)
    asn_nature_badge.short_description = 'ASN/企业性质'
    asn_nature_badge.admin_order_field = 'asn_nature'

    def status_badge(self, obj):
        color = POP_STATUS_COLORS.get(obj.status, '#6c757d')
        return badge(obj.get_status_display(), color)
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def deploy_badge(self, obj):
        color = {
            'undeployed': '#6c757d',
            'deploying': '#ffc107',
            'deployed': '#28a745',
            'failed': '#dc3545',
        }.get(obj.deploy_status, '#6c757d')
        return badge(obj.get_deploy_status_display(), color)
    deploy_badge.short_description = '部署'
    deploy_badge.admin_order_field = 'deploy_status'

    def bw_display(self, obj):
        # 使用自动汇总属性（VPS + 边缘加速），而非已废弃的 used_bandwidth_mbps 列
        used = obj.allocated_bandwidth_mbps
        pct = (used / obj.total_bandwidth_mbps * 100
               if obj.total_bandwidth_mbps else 0)
        color = '#dc3545' if pct > 90 else '#ffc107' if pct > 70 else '#28a745'
        return format_html(
            '<span style="color:{};">{}/{} Mbps ({}%)</span>',
            color,
            used, obj.total_bandwidth_mbps, f'{pct:.0f}',
        )
    bw_display.short_description = '带宽使用'

    def available_bw_display(self, obj):
        return f'{obj.available_bandwidth_mbps} Mbps'
    available_bw_display.short_description = '可用带宽'

    def carrier_list(self, obj):
        carriers = obj.carriers.all()
        return ', '.join(c.code for c in carriers[:3]) + ('…' if carriers.count() > 3 else '')
    carrier_list.short_description = '运营商'

    def vps_count(self, obj):
        return obj.vps_list.count()
    vps_count.short_description = 'VPS 数'

    def save_model(self, request, obj, form, change):
        if change:
            old = PopPoint.objects.get(pk=obj.pk)
            old_snap = resource_model_snapshot(old)
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=(
                    ResourceOperationLog.ActionType.STATUS_CHANGE
                    if old.status != obj.status
                    else ResourceOperationLog.ActionType.UPDATE
                ),
                description=f'更新 POP 点 [{obj.code}] {obj.name}',
                target_model=ResourceOperationLog.TargetModel.POP,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj,
                old_value=old_snap, new_value=resource_model_snapshot(obj),
                request=request,
            )
        else:
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=ResourceOperationLog.ActionType.CREATE,
                description=f'新建 POP 点 [{obj.code}] {obj.name}',
                target_model=ResourceOperationLog.TargetModel.POP,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj,
                request=request,
            )

    def delete_model(self, request, obj):
        snap = resource_model_snapshot(obj)
        record_resource_log(
            operator=request.user,
            action_type=ResourceOperationLog.ActionType.DELETE,
            description=f'删除 POP 点 [{obj.code}] {obj.name}',
            target_model=ResourceOperationLog.TargetModel.POP,
            target_id=obj.pk, target_repr=str(obj),
            old_value=snap, request=request,
        )
        super().delete_model(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('carriers').select_related(
            'city__country__region', 'created_by'
        )


# ═══════════════════════════════════════════════════════════════
# § 4  网络基础设施
# ═══════════════════════════════════════════════════════════════

class StaticRouteInline(admin.TabularInline):
    model = StaticRoute
    extra = 0
    fields = ('destination', 'gateway', 'interface', 'metric', 'is_active', 'description')
    show_change_link = True


@admin.register(NetworkInterface)
class NetworkInterfaceAdmin(admin.ModelAdmin):
    list_display = (
        'ifname', 'interface_type', 'pop_point',
        'status_display', 'speed_mbps', 'mtu', 'is_management',
    )
    list_filter = ('interface_type', 'status', 'is_management')
    search_fields = ('ifname', 'pop_point__name', 'mac_address', 'description')
    fieldsets = (
        ('基本信息', {'fields': ('pop_point', 'ifname', 'interface_type', 'status', 'description')}),
        ('配置', {'fields': ('mac_address', 'mtu', 'speed_mbps', 'vlan_id', 'parent_interface', 'is_management')}),
    )

    def status_display(self, obj):
        color = '#28a745' if obj.status == 'up' else '#dc3545'
        return badge(obj.get_status_display(), color)
    status_display.short_description = '状态'


@admin.register(RouteTable)
class RouteTableAdmin(admin.ModelAdmin):
    list_display = ('table_id', 'name', 'owner_type', 'pop_point', 'vps', 'route_count')
    list_filter = ('owner_type',)
    search_fields = ('name', 'pop_point__name', 'vps__name')
    inlines = [StaticRouteInline]

    def route_count(self, obj):
        return obj.static_routes.count()
    route_count.short_description = '路由条数'


@admin.register(PolicyRoute)
class PolicyRouteAdmin(admin.ModelAdmin):
    list_display = (
        'priority', 'pop_point', 'vps',
        'src_prefix', 'dst_prefix', 'action', 'lookup_table', 'is_active',
    )
    list_filter = ('action', 'is_active')
    search_fields = ('pop_point__name', 'vps__name', 'src_prefix', 'dst_prefix')


# ═══════════════════════════════════════════════════════════════
# § 5  IP 地址管理
# ═══════════════════════════════════════════════════════════════

class IPAddressInline(admin.TabularInline):
    model = IPAddress
    extra = 0
    fields = ('address', 'prefix_len', 'status', 'assigned_vps', 'assigned_edge', 'is_primary')
    show_change_link = True


@admin.register(IPBlock)
class IPBlockAdmin(admin.ModelAdmin):
    list_display = (
        'cidr', 'ip_version', 'pop_point', 'carrier',
        'ip_attribute_badge', 'network_type', 'asn_nature', 'ip_stats', 'is_active',
    )
    list_filter = ('ip_version', 'ip_attribute', 'network_type', 'asn_nature', 'is_active', 'carrier')
    search_fields = ('cidr', 'pop_point__name', 'carrier__name', 'description')
    inlines = [IPAddressInline]

    def ip_attribute_badge(self, obj):
        if not obj.ip_attribute:
            return '—'
        color = RES_TYPE_COLORS.get(obj.ip_attribute, '#6c757d')
        return badge(obj.get_ip_attribute_display(), color)
    ip_attribute_badge.short_description = 'IP 属性'

    def ip_stats(self, obj):
        total = obj.ip_addresses.count()
        assigned = obj.ip_addresses.filter(status='assigned').count()
        return format_html(
            '<span>{}/{}</span>',
            assigned, total,
        )
    ip_stats.short_description = '已用/总计'


@admin.register(IPAddress)
class IPAddressAdmin(admin.ModelAdmin):
    list_display = (
        'address', 'prefix_len', 'ip_block', 'status_badge',
        'assigned_vps', 'assigned_edge', 'is_primary',
    )
    list_filter = ('status', 'is_primary', 'ip_block__ip_version')
    search_fields = ('address', 'ip_block__cidr', 'assigned_vps__name', 'assigned_edge__name')

    def status_badge(self, obj):
        color = IP_STATUS_COLORS.get(obj.status, '#6c757d')
        return badge(obj.get_status_display(), color)
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def save_model(self, request, obj, form, change):
        old_status = None
        if change:
            old = IPAddress.objects.get(pk=obj.pk)
            old_status = old.status
        super().save_model(request, obj, form, change)
        if not change or old_status != obj.status:
            action = (
                ResourceOperationLog.ActionType.IP_ASSIGN
                if obj.status == 'assigned'
                else ResourceOperationLog.ActionType.IP_RELEASE
                if obj.status == 'available' and old_status == 'assigned'
                else ResourceOperationLog.ActionType.UPDATE
            )
            record_resource_log(
                operator=request.user,
                action_type=action,
                description=f'IP {obj.address} 状态变更为 {obj.get_status_display()}',
                target_model=ResourceOperationLog.TargetModel.IP_ADDRESS,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj.ip_block.pop_point,
                vps=obj.assigned_vps,
                edge=obj.assigned_edge,
                request=request,
            )


# ═══════════════════════════════════════════════════════════════
# § 6  VPS
# ═══════════════════════════════════════════════════════════════

class VPSNetworkInterfaceInline(admin.TabularInline):
    model = VPSNetworkInterface
    extra = 0
    fields = ('ifname', 'mac_address', 'mtu', 'is_primary')


class VPSIPAddressInline(admin.TabularInline):
    model = IPAddress
    fk_name = 'assigned_vps'
    extra = 0
    fields = ('address', 'prefix_len', 'is_primary', 'status')
    readonly_fields = ('address', 'prefix_len', 'status')
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class VPSOperationLogInline(admin.TabularInline):
    model = ResourceOperationLog
    fk_name = 'vps'
    extra = 0
    fields = ('action_type', 'target_model', 'description', 'operator', 'ip_address', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(VPS)
class VPSAdmin(admin.ModelAdmin):
    list_display = (
        'vps_no', 'name', 'status_badge', 'pop_point',
        'hw_summary', 'bandwidth_display', 'customer',
        'start_time', 'expire_time',
    )
    list_filter = (
        'status', 'os_type',
        'pop_point__city__country__region',
        'pop_point__network_type',
    )
    search_fields = (
        'vps_no', 'name', 'mgmt_ip',
        'customer__name', 'pop_point__name',
    )
    readonly_fields = ('vps_no', 'created_at', 'updated_at')
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True
    inlines = [VPSNetworkInterfaceInline, VPSIPAddressInline, VPSOperationLogInline]

    fieldsets = (
        ('基本属性', {
            'fields': ('vps_no', 'name', 'status', 'pop_point', 'remark'),
        }),
        ('硬件属性', {
            'fields': ('vcpu', 'ram_mb', 'disk_gb', 'nic_count'),
        }),
        ('管理属性', {
            'fields': ('mgmt_ip', 'mgmt_username', 'mgmt_password', 'mgmt_port', 'os_type', 'os_version', 'kernel_version'),
        }),
        ('网络资源', {
            'fields': ('bandwidth_mbps',),
        }),
        ('客户关联', {
            'fields': ('customer', 'contract'),
        }),
        ('生命周期', {
            'fields': ('start_time', 'expire_time', 'created_by', 'created_at', 'updated_at'),
        }),
    )

    def status_badge(self, obj):
        color = VPS_STATUS_COLORS.get(obj.status, '#6c757d')
        return badge(obj.get_status_display(), color)
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def hw_summary(self, obj):
        return format_html(
            '<small>{}vCPU / {}MB / {}GB</small>',
            obj.vcpu, obj.ram_mb, obj.disk_gb,
        )
    hw_summary.short_description = '硬件规格'

    def bandwidth_display(self, obj):
        return f'{obj.bandwidth_mbps} Mbps'
    bandwidth_display.short_description = '带宽'

    def save_model(self, request, obj, form, change):
        if change:
            old = VPS.objects.get(pk=obj.pk)
            old_snap = resource_model_snapshot(old)
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=(
                    ResourceOperationLog.ActionType.STATUS_CHANGE
                    if old.status != obj.status
                    else ResourceOperationLog.ActionType.UPDATE
                ),
                description=f'更新 VPS [{obj.vps_no}] {obj.name}',
                target_model=ResourceOperationLog.TargetModel.VPS,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj.pop_point, vps=obj,
                old_value=old_snap, new_value=resource_model_snapshot(obj),
                request=request,
            )
        else:
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=ResourceOperationLog.ActionType.CREATE,
                description=f'新建 VPS [{obj.vps_no}] {obj.name}',
                target_model=ResourceOperationLog.TargetModel.VPS,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj.pop_point, vps=obj,
                request=request,
            )

    def delete_model(self, request, obj):
        snap = resource_model_snapshot(obj)
        record_resource_log(
            operator=request.user,
            action_type=ResourceOperationLog.ActionType.DELETE,
            description=f'删除 VPS [{obj.vps_no}] {obj.name}',
            target_model=ResourceOperationLog.TargetModel.VPS,
            target_id=obj.pk, target_repr=str(obj),
            pop_point=obj.pop_point,
            old_value=snap, request=request,
        )
        super().delete_model(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'pop_point__city', 'customer', 'created_by'
        )


# ═══════════════════════════════════════════════════════════════
# § 7  边缘加速
# ═══════════════════════════════════════════════════════════════

class GREConfigInline(admin.StackedInline):
    model = GREConfig
    extra = 0
    can_delete = False
    fieldsets = (
        (None, {
            'fields': (
                ('local_ip', 'remote_ip'), 'tunnel_ip',
                ('key', 'ttl'), ('pmtud', 'checksum'), 'extra_options',
            ),
        }),
    )


class WireguardConfigInline(admin.StackedInline):
    model = WireguardConfig
    extra = 0
    can_delete = False
    fieldsets = (
        ('本端配置', {
            'fields': ('interface_ip', 'listen_port', 'private_key', 'public_key', 'dns'),
        }),
        ('Peer 配置', {
            'fields': ('peer_public_key', 'peer_endpoint', 'peer_allowed_ips', 'persistent_keepalive'),
        }),
        ('脚本钩子', {
            'classes': ('collapse',),
            'fields': ('pre_up', 'post_up', 'pre_down', 'post_down'),
        }),
        ('其他', {'classes': ('collapse',), 'fields': ('extra_options',)}),
    )


class Socks5ConfigInline(admin.StackedInline):
    model = Socks5Config
    extra = 0
    can_delete = False
    fieldsets = (
        (None, {
            'fields': (
                ('listen_ip', 'listen_port'),
                ('auth_required', 'username', 'password'),
                ('udp_enabled', 'bind_address'),
                ('max_connections', 'timeout_seconds'),
                'extra_options',
            ),
        }),
    )


class EdgeIPAddressInline(admin.TabularInline):
    model = IPAddress
    fk_name = 'assigned_edge'
    extra = 0
    fields = ('address', 'prefix_len', 'is_primary', 'status')
    readonly_fields = ('address', 'prefix_len', 'status')
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


class EdgeOperationLogInline(admin.TabularInline):
    model = ResourceOperationLog
    fk_name = 'edge'
    extra = 0
    fields = ('action_type', 'target_model', 'description', 'operator', 'ip_address', 'created_at')
    readonly_fields = fields
    max_num = 0
    can_delete = False
    ordering = ('-created_at',)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(EdgeAcceleration)
class EdgeAccelerationAdmin(admin.ModelAdmin):
    list_display = (
        'edge_no', 'name', 'protocol_badge', 'status_badge',
        'pop_point', 'bandwidth_display', 'customer', 'start_time',
    )
    list_filter = ('protocol', 'status', 'pop_point__city__country__region')
    search_fields = ('edge_no', 'name', 'pop_point__name', 'customer__name')
    readonly_fields = ('edge_no', 'created_at', 'updated_at')
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True

    def get_inline_instances(self, request, obj=None):
        inlines = [EdgeIPAddressInline, EdgeOperationLogInline]
        if obj:
            if obj.protocol == EdgeAcceleration.Protocol.GRE:
                inlines.insert(0, GREConfigInline)
            elif obj.protocol == EdgeAcceleration.Protocol.WIREGUARD:
                inlines.insert(0, WireguardConfigInline)
            elif obj.protocol == EdgeAcceleration.Protocol.SOCKS5:
                inlines.insert(0, Socks5ConfigInline)
        return [i(self.model, self.admin_site) for i in inlines]

    fieldsets = (
        ('基本属性', {
            'fields': ('edge_no', 'name', 'protocol', 'status', 'pop_point', 'remark'),
        }),
        ('网络资源', {
            'fields': ('bandwidth_mbps', 'route_table'),
        }),
        ('客户关联', {
            'fields': ('customer', 'contract'),
        }),
        ('生命周期', {
            'fields': ('start_time', 'expire_time', 'created_by', 'created_at', 'updated_at'),
        }),
    )

    def protocol_badge(self, obj):
        colors = {
            'gre': '#007bff', 'wireguard': '#6f42c1', 'socks5': '#fd7e14',
        }
        color = colors.get(obj.protocol, '#6c757d')
        return badge(obj.get_protocol_display(), color)
    protocol_badge.short_description = '协议'
    protocol_badge.admin_order_field = 'protocol'

    def status_badge(self, obj):
        color = EDGE_STATUS_COLORS.get(obj.status, '#6c757d')
        return badge(obj.get_status_display(), color)
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def bandwidth_display(self, obj):
        return f'{obj.bandwidth_mbps} Mbps'
    bandwidth_display.short_description = '带宽'

    def save_model(self, request, obj, form, change):
        if change:
            old = EdgeAcceleration.objects.get(pk=obj.pk)
            old_snap = resource_model_snapshot(old)
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=(
                    ResourceOperationLog.ActionType.STATUS_CHANGE
                    if old.status != obj.status
                    else ResourceOperationLog.ActionType.CONFIG_CHANGE
                    if old.protocol != obj.protocol
                    else ResourceOperationLog.ActionType.UPDATE
                ),
                description=f'更新边缘加速 [{obj.edge_no}] {obj.name}',
                target_model=ResourceOperationLog.TargetModel.EDGE,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj.pop_point, edge=obj,
                old_value=old_snap, new_value=resource_model_snapshot(obj),
                request=request,
            )
        else:
            super().save_model(request, obj, form, change)
            record_resource_log(
                operator=request.user,
                action_type=ResourceOperationLog.ActionType.CREATE,
                description=f'新建边缘加速 [{obj.edge_no}] {obj.name} ({obj.get_protocol_display()})',
                target_model=ResourceOperationLog.TargetModel.EDGE,
                target_id=obj.pk, target_repr=str(obj),
                pop_point=obj.pop_point, edge=obj,
                request=request,
            )

    def delete_model(self, request, obj):
        snap = resource_model_snapshot(obj)
        record_resource_log(
            operator=request.user,
            action_type=ResourceOperationLog.ActionType.DELETE,
            description=f'删除边缘加速 [{obj.edge_no}] {obj.name}',
            target_model=ResourceOperationLog.TargetModel.EDGE,
            target_id=obj.pk, target_repr=str(obj),
            pop_point=obj.pop_point,
            old_value=snap, request=request,
        )
        super().delete_model(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'pop_point__city', 'customer', 'route_table', 'created_by'
        )


# ═══════════════════════════════════════════════════════════════
# § 8  带宽分配
# ═══════════════════════════════════════════════════════════════

@admin.register(BandwidthAllocation)
class BandwidthAllocationAdmin(admin.ModelAdmin):
    list_display = (
        'pop_point', 'target_type', 'target_display',
        'allocated_mbps', 'burst_mbps', 'is_active', 'created_at',
    )
    list_filter = ('target_type', 'is_active', 'pop_point')
    search_fields = ('pop_point__name', 'vps__name', 'edge__name')

    def target_display(self, obj):
        return obj.vps or obj.edge or '—'
    target_display.short_description = '分配目标'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_resource_log(
            operator=request.user,
            action_type=ResourceOperationLog.ActionType.BW_ALLOC,
            description=f'带宽分配 {obj.allocated_mbps}Mbps 给 {obj.vps or obj.edge}',
            target_model=ResourceOperationLog.TargetModel.BW_ALLOC,
            target_id=obj.pk, target_repr=str(obj),
            pop_point=obj.pop_point,
            vps=obj.vps, edge=obj.edge,
            request=request,
        )


# ═══════════════════════════════════════════════════════════════
# § 9  资源操作日志（只读）
# ═══════════════════════════════════════════════════════════════

@admin.register(ResourceOperationLog)
class ResourceOperationLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'action_type_badge', 'target_model',
        'target_repr', 'description', 'operator', 'ip_address',
    )
    list_filter = ('action_type', 'target_model', 'created_at', 'operator')
    search_fields = (
        'description', 'target_repr',
        'pop_point__name', 'vps__name', 'edge__name',
        'operator__username',
    )
    readonly_fields = (
        'operator', 'action_type', 'target_model', 'target_id', 'target_repr',
        'pop_point', 'vps', 'edge',
        'description', 'old_value', 'new_value',
        'ip_address', 'created_at',
    )
    date_hierarchy = 'created_at'
    list_per_page = 30

    fieldsets = (
        ('操作信息', {
            'fields': ('created_at', 'action_type', 'operator', 'ip_address'),
        }),
        ('关联对象', {
            'fields': ('target_model', 'target_id', 'target_repr', 'pop_point', 'vps', 'edge'),
        }),
        ('操作描述', {'fields': ('description',)}),
        ('变更详情', {
            'classes': ('collapse',),
            'fields': ('old_value', 'new_value'),
        }),
    )

    def action_type_badge(self, obj):
        color = ACTION_COLORS.get(obj.action_type, '#adb5bd')
        return badge(obj.get_action_type_display(), color)
    action_type_badge.short_description = '操作类型'
    action_type_badge.admin_order_field = 'action_type'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'operator', 'pop_point', 'vps', 'edge'
        )
