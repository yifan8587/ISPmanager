"""
operation_manager/serializers.py
运维监控 — Zabbix 映射与详情页图形 API 序列化器

说明：
  - 写操作 Serializer 与只读 Detail Serializer 分离；
  - 密码/Token 字段 write_only，列表不返回敏感信息；
  - ResourceMonitoringPanelSerializer 供 POP/VPS/Edge 详情页直接嵌入监控面板数据。
"""

import math

from rest_framework import serializers

from resource_manager.models import PopPoint, VPS, EdgeAcceleration

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
    PopTelemetryCredential,
    WebSSHCommandTemplate,
)


# ═══════════════════════════════════════════════════════════════
# § 1  Zabbix 服务器
# ═══════════════════════════════════════════════════════════════

class ZabbixServerListSerializer(serializers.ModelSerializer):
    auth_type_label = serializers.CharField(source='get_auth_type_display', read_only=True)

    class Meta:
        model = ZabbixServer
        fields = (
            'id', 'name', 'api_url', 'auth_type', 'auth_type_label',
            'is_default', 'is_active',
            'default_host_group_id', 'default_host_group_name',
            'last_sync_at', 'last_sync_status', 'last_sync_message',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'last_sync_at', 'last_sync_status', 'last_sync_message',
            'created_at', 'updated_at',
        )


class ZabbixServerWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = ZabbixServer
        fields = (
            'id', 'name', 'api_url', 'auth_type',
            'api_token', 'username', 'password',
            'verify_ssl', 'timeout_sec',
            'default_host_group_id', 'default_host_group_name',
            'is_default', 'is_active', 'remark',
        )
        read_only_fields = ('id',)

    def validate(self, attrs):
        auth_type = attrs.get('auth_type') or (
            self.instance.auth_type if self.instance else ZabbixServer.AuthType.API_TOKEN
        )
        token = attrs.get('api_token', serializers.empty)
        username = attrs.get('username', serializers.empty)
        password = attrs.get('password', serializers.empty)

        if auth_type == ZabbixServer.AuthType.API_TOKEN:
            if token is serializers.empty and self.instance:
                token = self.instance.api_token
            if not (token or '').strip():
                raise serializers.ValidationError({'api_token': '请填写 API Token'})
        else:
            user = username if username is not serializers.empty else (
                self.instance.username if self.instance else ''
            )
            if not (user or '').strip():
                raise serializers.ValidationError({'username': '请填写用户名'})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password', None)
        if 'api_token' in validated_data and not validated_data['api_token']:
            validated_data.pop('api_token', None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        token = validated_data.pop('api_token', None)
        if password:
            instance.password = password
        if token is not None and token != '':
            instance.api_token = token
        return super().update(instance, validated_data)


# ═══════════════════════════════════════════════════════════════
# § 2  Host 模板
# ═══════════════════════════════════════════════════════════════

class MonitoringHostTemplateSerializer(serializers.ModelSerializer):
    target_kind_label = serializers.CharField(source='get_target_kind_display', read_only=True)
    monitor_mode_label = serializers.CharField(source='get_monitor_mode_display', read_only=True)
    zabbix_server_name = serializers.CharField(source='zabbix_server.name', read_only=True)

    class Meta:
        model = MonitoringHostTemplate
        fields = (
            'id', 'name', 'target_kind', 'target_kind_label',
            'monitor_mode', 'monitor_mode_label',
            'zabbix_server', 'zabbix_server_name',
            'zabbix_host_group_ids', 'zabbix_template_ids',
            'zabbix_interface_type', 'snmp_community_macro', 'icmp_use_mgmt_ip',
            'auto_create_items', 'auto_create_graphs', 'graph_name_patterns',
            'is_active', 'remark', 'created_at', 'updated_at',
        )


# ═══════════════════════════════════════════════════════════════
# § 3  监控 Host / Item / Graph
# ═══════════════════════════════════════════════════════════════

class MonitoringItemSerializer(serializers.ModelSerializer):
    value_type_label = serializers.CharField(source='get_value_type_display', read_only=True)
    traffic_direction_label = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringItem
        fields = (
            'id', 'monitoring_host', 'zabbix_itemid', 'item_key', 'name', 'units',
            'value_type', 'value_type_label',
            'traffic_direction', 'traffic_direction_label', 'interface_name',
            'last_value', 'last_clock', 'is_enabled', 'is_primary_traffic',
            'synced_at',
        )
        read_only_fields = ('zabbix_itemid', 'synced_at')

    def get_traffic_direction_label(self, obj):
        labels = {'in': '入站', 'out': '出站', 'both': '双向', 'unknown': '未知'}
        return labels.get(obj.traffic_direction, obj.traffic_direction)


class MonitoringGraphProfileSerializer(serializers.ModelSerializer):
    profile_type_label = serializers.CharField(source='get_profile_type_display', read_only=True)
    aggregation_label = serializers.CharField(source='get_aggregation_display', read_only=True)
    billing_mode_label = serializers.CharField(source='get_billing_mode_display', read_only=True)

    class Meta:
        model = MonitoringGraphProfile
        fields = (
            'id', 'code', 'name', 'profile_type', 'profile_type_label',
            'aggregation', 'aggregation_label', 'percentile_n',
            'billing_mode', 'billing_mode_label', 'unit_display',
            'sample_align_sec', 'item_key_in_patterns', 'item_key_out_patterns',
            'interface_name_pattern', 'stack_series', 'fill_missing',
            'convert_counter_to_rate', 'is_system', 'is_active', 'remark',
            'created_at', 'updated_at',
        )
        read_only_fields = ('is_system', 'created_at', 'updated_at')


class MonitoringGraphSeriesSerializer(serializers.ModelSerializer):
    series_role_label = serializers.CharField(source='get_series_role_display', read_only=True)
    item_key = serializers.CharField(source='monitoring_item.item_key', read_only=True, default='')

    class Meta:
        model = MonitoringGraphSeries
        fields = (
            'id', 'graph', 'monitoring_item', 'item_key',
            'series_role', 'series_role_label', 'label', 'color',
            'display_order', 'multiply_factor', 'is_visible',
        )


class MonitoringGraphSerializer(serializers.ModelSerializer):
    graph_kind_label = serializers.CharField(source='get_graph_kind_display', read_only=True)
    source_type_label = serializers.CharField(source='get_source_type_display', read_only=True)
    profile_name = serializers.CharField(source='profile.name', read_only=True, default='')
    effective_title = serializers.CharField(read_only=True)
    item_ids = serializers.PrimaryKeyRelatedField(
        source='items', many=True, queryset=MonitoringItem.objects.all(), required=False,
    )
    series = MonitoringGraphSeriesSerializer(source='series_list', many=True, read_only=True)

    class Meta:
        model = MonitoringGraph
        fields = (
            'id', 'monitoring_host', 'source_type', 'source_type_label',
            'profile', 'profile_name', 'zabbix_graphid', 'name', 'display_title', 'effective_title',
            'graph_kind', 'graph_kind_label',
            'width', 'height', 'interface_name', 'custom_config', 'billing_enabled',
            'item_ids', 'zabbix_itemids', 'series',
            'is_enabled', 'is_default', 'synced_at',
        )
        read_only_fields = ('zabbix_graphid', 'zabbix_itemids', 'synced_at')


class MonitoringGraphWriteSerializer(serializers.ModelSerializer):
    item_ids = serializers.PrimaryKeyRelatedField(
        source='items', many=True, queryset=MonitoringItem.objects.all(), required=False,
    )

    class Meta:
        model = MonitoringGraph
        fields = (
            'id', 'monitoring_host', 'source_type', 'profile',
            'name', 'display_title', 'graph_kind',
            'width', 'height', 'interface_name', 'custom_config', 'billing_enabled',
            'item_ids', 'is_enabled', 'is_default',
        )

    def create(self, validated_data):
        items = validated_data.pop('items', [])
        obj = super().create(validated_data)
        if items:
            obj.items.set(items)
        if obj.source_type == MonitoringGraph.SourceType.CUSTOM and obj.billing_enabled:
            obj.graph_kind = MonitoringGraph.GraphKind.BILLING
            obj.save(update_fields=['graph_kind'])
        return obj

    def update(self, instance, validated_data):
        items = validated_data.pop('items', None)
        obj = super().update(instance, validated_data)
        if items is not None:
            obj.items.set(items)
        return obj


class MonitoringGraphBriefSerializer(serializers.ModelSerializer):
    """详情页图形列表（轻量）"""
    graph_kind_label = serializers.CharField(source='get_graph_kind_display', read_only=True)
    source_type = serializers.CharField(read_only=True)
    effective_title = serializers.CharField(read_only=True)
    chart_data_url = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringGraph
        fields = (
            'id', 'source_type', 'zabbix_graphid', 'name', 'display_title', 'effective_title',
            'graph_kind', 'graph_kind_label', 'interface_name', 'billing_enabled',
            'profile', 'width', 'height', 'is_default', 'chart_data_url',
        )

    def get_chart_data_url(self, obj):
        request = self.context.get('request')
        path = f'/api/operation/monitoring-graphs/{obj.pk}/chart-data/'
        return request.build_absolute_uri(path) if request else path


class MonitoringHostListSerializer(serializers.ModelSerializer):
    host_kind_label = serializers.CharField(source='get_host_kind_display', read_only=True)
    sync_status_label = serializers.CharField(source='get_sync_status_display', read_only=True)
    resource_label = serializers.CharField(read_only=True)
    pop_point_label = serializers.SerializerMethodField()
    service_pop_point_label = serializers.SerializerMethodField()
    zabbix_server_name = serializers.CharField(source='zabbix_server.name', read_only=True)
    graph_count = serializers.SerializerMethodField()
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringHost
        fields = (
            'id', 'host_kind', 'host_kind_label', 'resource_label',
            'pop_point', 'pop_point_label', 'service_pop_point', 'service_pop_point_label',
            'zabbix_server', 'zabbix_server_name', 'template',
            'hostname', 'visible_name', 'zabbix_hostid', 'monitored_ip',
            'snmp_community', 'snmp_version', 'snmp_port',
            'zabbix_group_ids', 'zabbix_template_ids',
            'enable_icmp', 'enable_snmp', 'is_enabled',
            'sync_status', 'sync_status_label', 'last_sync_at', 'last_sync_error',
            'zabbix_item_count', 'zabbix_graph_count',
            'graph_count', 'item_count',
            'created_at', 'updated_at',
        )

    def get_pop_point_label(self, obj):
        if obj.pop_point_id and obj.pop_point:
            return f'[{obj.pop_point.code}] {obj.pop_point.name}'
        return ''

    def get_service_pop_point_label(self, obj):
        if obj.service_pop_point_id and obj.service_pop_point:
            return f'[{obj.service_pop_point.code}] {obj.service_pop_point.name}'
        return ''

    def get_graph_count(self, obj):
        local = obj.graphs.count()
        remote = obj.zabbix_graph_count
        return max(local, remote) if remote is not None else local

    def get_item_count(self, obj):
        local = obj.items.count()
        remote = obj.zabbix_item_count
        return max(local, remote) if remote is not None else local


class MonitoringHostDetailSerializer(MonitoringHostListSerializer):
    items = MonitoringItemSerializer(many=True, read_only=True)
    graphs = MonitoringGraphBriefSerializer(many=True, read_only=True)

    class Meta(MonitoringHostListSerializer.Meta):
        fields = MonitoringHostListSerializer.Meta.fields + ('items', 'graphs', 'zabbix_raw', 'remark')


class MonitoringHostWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoringHost
        fields = (
            'id', 'host_kind', 'pop_point', 'service_pop_point',
            'zabbix_server', 'template',
            'hostname', 'visible_name', 'monitored_ip', 'snmp_community',
            'snmp_version', 'snmp_port', 'zabbix_group_ids', 'zabbix_template_ids',
            'enable_icmp', 'enable_snmp', 'is_enabled', 'remark',
        )
        read_only_fields = ('id',)

    def validate(self, attrs):
        host_kind = attrs.get('host_kind') or (
            self.instance.host_kind if self.instance else MonitoringHost.HostKind.GENERIC
        )
        if host_kind == MonitoringHost.HostKind.GENERIC:
            attrs['pop_point'] = None
        else:
            attrs['service_pop_point'] = None
        return attrs


# ═══════════════════════════════════════════════════════════════
# § 4  图形绑定 & 详情页面板
# ═══════════════════════════════════════════════════════════════

class MonitoringGraphBindingSerializer(serializers.ModelSerializer):
    graph_name = serializers.CharField(source='graph.name', read_only=True, default='')
    item_name = serializers.CharField(source='monitoring_item.name', read_only=True, default='')
    bind_kind = serializers.CharField(read_only=True)
    panel_slot_label = serializers.CharField(source='get_panel_slot_display', read_only=True)
    monitoring_host_name = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringGraphBinding
        fields = (
            'id', 'graph', 'graph_name', 'monitoring_item', 'item_name',
            'monitoring_host', 'monitoring_host_name', 'bind_kind', 'target_type',
            'pop_point', 'vps', 'edge',
            'panel_slot', 'panel_slot_label', 'panel_title',
            'display_order', 'is_visible',
        )

    def get_monitoring_host_name(self, obj):
        host = obj.effective_host
        if not host:
            return ''
        return host.visible_name or host.hostname

    def validate(self, attrs):
        graph = attrs.get('graph') or (self.instance.graph if self.instance else None)
        item = attrs.get('monitoring_item') or (
            self.instance.monitoring_item if self.instance else None
        )
        if not graph and not item:
            raise serializers.ValidationError('请绑定监控图形或监控项')

        from .resource_monitoring import validate_monitoring_host_for_resource

        target_type = attrs.get('target_type') or (self.instance.target_type if self.instance else None)
        vps = attrs.get('vps') if 'vps' in attrs else (self.instance.vps if self.instance else None)
        edge = attrs.get('edge') if 'edge' in attrs else (self.instance.edge if self.instance else None)
        monitoring_host = attrs.get('monitoring_host') or (
            self.instance.monitoring_host if self.instance else None
        )
        host = monitoring_host
        if not host and graph:
            host = graph.monitoring_host
        if not host and item:
            host = item.monitoring_host
        pop_id = None
        if target_type == 'vps' and vps:
            pop_id = vps.pop_point_id
        elif target_type == 'edge' and edge:
            pop_id = edge.pop_point_id
        if host and pop_id:
            try:
                validate_monitoring_host_for_resource(monitoring_host=host, pop_point_id=pop_id)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return attrs


class MonitoringPanelGraphSerializer(serializers.Serializer):
    """详情页单个图形卡片"""
    binding_id = serializers.IntegerField(required=False, allow_null=True)
    graph_id = serializers.IntegerField()
    source_type = serializers.CharField(required=False)
    zabbix_graphid = serializers.CharField(required=False, allow_blank=True)
    title = serializers.CharField()
    graph_kind = serializers.CharField()
    graph_kind_label = serializers.CharField()
    interface_name = serializers.CharField(required=False, allow_blank=True)
    billing_enabled = serializers.BooleanField(required=False)
    profile_code = serializers.CharField(required=False, allow_blank=True)
    width = serializers.IntegerField()
    height = serializers.IntegerField()
    chart_url = serializers.CharField(required=False, allow_blank=True)
    items = MonitoringItemSerializer(many=True, required=False)


class ResourceMonitoringPanelSerializer(serializers.Serializer):
    """
    POP / VPS / Edge 详情页监控面板聚合数据。
    由 views 组装，Serializer 仅定义结构。
    """
    enabled = serializers.BooleanField()
    target_type = serializers.CharField()
    resource_id = serializers.IntegerField()
    monitoring_host_id = serializers.IntegerField(required=False, allow_null=True)
    sync_status = serializers.CharField(required=False, allow_blank=True)
    last_sync_at = serializers.DateTimeField(required=False, allow_null=True)
    monitored_ip = serializers.IPAddressField(required=False, allow_null=True)
    panels = serializers.DictField(
        child=MonitoringPanelGraphSerializer(many=True),
        help_text='按 panel_slot 分组的图形列表，如 overview/traffic/system',
    )
    primary_items = MonitoringItemSerializer(many=True, required=False)


# ═══════════════════════════════════════════════════════════════
# § 5  POP 品质监控
# ═══════════════════════════════════════════════════════════════

class PopQualityProbeSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    alert_kind_label = serializers.CharField(source='get_alert_kind_display', read_only=True)

    class Meta:
        model = PopQualityProbe
        fields = (
            'id', 'monitor', 'status', 'status_label',
            'icmp_target', 'icmp_sent', 'icmp_received', 'icmp_packet_loss_pct',
            'icmp_min_ms', 'icmp_avg_ms', 'icmp_max_ms', 'icmp_success',
            'http_target', 'http_status_code', 'http_response_ms', 'http_success',
            'error_message', 'alert_kind', 'alert_kind_label',
            'started_at', 'finished_at', 'created_at',
        )
        read_only_fields = fields


class PopQualityMonitorListSerializer(serializers.ModelSerializer):
    pop_code = serializers.CharField(source='pop_point.code', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_status = serializers.CharField(source='pop_point.status', read_only=True)
    pop_status_label = serializers.CharField(source='pop_point.get_status_display', read_only=True)
    mgmt_ip = serializers.IPAddressField(source='pop_point.mgmt_ip', read_only=True, allow_null=True)
    mgmt_url = serializers.URLField(source='pop_point.mgmt_url', read_only=True, allow_blank=True)
    pop_label = serializers.CharField(read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    is_alert_suppressed = serializers.BooleanField(read_only=True)
    has_probe_target = serializers.BooleanField(read_only=True)

    class Meta:
        model = PopQualityMonitor
        fields = (
            'id', 'pop_point', 'pop_code', 'pop_name', 'pop_label',
            'pop_status', 'pop_status_label', 'mgmt_ip', 'mgmt_url',
            'is_enabled', 'enable_icmp', 'enable_http',
            'probe_interval_seconds', 'timeout_seconds', 'icmp_count',
            'packet_loss_threshold_pct', 'consecutive_breach_threshold',
            'alert_repeat_minutes',
            'status', 'status_label', 'last_probe_at', 'next_probe_at',
            'last_icmp_packet_loss_pct', 'last_icmp_avg_ms',
            'last_http_response_ms', 'last_http_status_code', 'last_error',
            'consecutive_breaches', 'alert_active', 'alert_started_at',
            'last_alert_at', 'last_recovered_at',
            'is_alert_suppressed', 'has_probe_target',
            'remark', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'pop_code', 'pop_name', 'pop_label',
            'pop_status', 'pop_status_label', 'mgmt_ip', 'mgmt_url',
            'status', 'last_probe_at', 'next_probe_at',
            'last_icmp_packet_loss_pct', 'last_icmp_avg_ms',
            'last_http_response_ms', 'last_http_status_code', 'last_error',
            'consecutive_breaches', 'alert_active', 'alert_started_at',
            'last_alert_at', 'last_recovered_at',
            'is_alert_suppressed', 'has_probe_target', 'created_at', 'updated_at',
        )


class PopQualityMonitorDetailSerializer(PopQualityMonitorListSerializer):
    recent_probes = serializers.SerializerMethodField()

    class Meta(PopQualityMonitorListSerializer.Meta):
        fields = PopQualityMonitorListSerializer.Meta.fields + ('recent_probes',)

    def get_recent_probes(self, obj):
        probes = obj.probes.all().order_by('-started_at')[:30]
        return PopQualityProbeSerializer(probes, many=True).data


class PopQualityMonitorWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PopQualityMonitor
        fields = (
            'id', 'pop_point', 'is_enabled', 'enable_icmp', 'enable_http',
            'probe_interval_seconds', 'timeout_seconds', 'icmp_count',
            'packet_loss_threshold_pct', 'consecutive_breach_threshold',
            'alert_repeat_minutes', 'remark',
        )
        read_only_fields = ('id',)

    def validate_pop_point(self, value):
        if self.instance and value.pk != self.instance.pop_point_id:
            raise serializers.ValidationError('已有品质采样的监控对象不可更换 POP，请删除后重新创建。')
        return value

    def validate_probe_interval_seconds(self, value):
        if not 15 <= value <= 86400:
            raise serializers.ValidationError('探测间隔必须在 15 到 86400 秒之间')
        return value

    def validate_timeout_seconds(self, value):
        if not 1 <= value <= 60:
            raise serializers.ValidationError('单次超时必须在 1 到 60 秒之间')
        return value

    def validate_icmp_count(self, value):
        if not 1 <= value <= 20:
            raise serializers.ValidationError('Ping 次数必须在 1 到 20 之间')
        return value

    def validate_packet_loss_threshold_pct(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError('丢包率阈值必须在 0 到 100 之间')
        return value

    def validate_consecutive_breach_threshold(self, value):
        if not 1 <= value <= 100:
            raise serializers.ValidationError('连续超阈次数必须在 1 到 100 之间')
        return value

    def validate_alert_repeat_minutes(self, value):
        if not 1 <= value <= 1440:
            raise serializers.ValidationError('重复告警间隔必须在 1 到 1440 分钟之间')
        return value

    def validate(self, attrs):
        pop = attrs.get('pop_point') or (self.instance.pop_point if self.instance else None)
        enable_icmp = attrs.get(
            'enable_icmp',
            self.instance.enable_icmp if self.instance else True,
        )
        enable_http = attrs.get(
            'enable_http',
            self.instance.enable_http if self.instance else True,
        )
        is_enabled = attrs.get(
            'is_enabled',
            self.instance.is_enabled if self.instance else True,
        )
        has_icmp_target = bool(enable_icmp and pop and pop.mgmt_ip)
        has_http_target = bool(enable_http and pop and pop.mgmt_url)
        if (
            is_enabled
            and pop
            and (enable_icmp or enable_http)
            and not (has_icmp_target or has_http_target)
        ):
            raise serializers.ValidationError(
                '关联 POP 未配置管理 IP 或管理 URL，请先补充资源管理信息或关闭相应探测。',
            )
        return attrs


class PopQualityHistoryQuerySerializer(serializers.Serializer):
    time_from = serializers.DateTimeField(required=False)
    time_till = serializers.DateTimeField(required=False)
    period = serializers.ChoiceField(
        choices=['1h', '6h', '24h', '7d', '30d'],
        default='24h',
        required=False,
    )

    def validate(self, attrs):
        if bool(attrs.get('time_from')) != bool(attrs.get('time_till')):
            raise serializers.ValidationError('time_from 与 time_till 必须同时提供')
        if (
            attrs.get('time_from')
            and attrs.get('time_till')
            and attrs['time_from'] >= attrs['time_till']
        ):
            raise serializers.ValidationError({'time_till': '结束时间必须晚于开始时间'})
        return attrs


# ═══════════════════════════════════════════════════════════════
# § 6  POP 边缘遥测
# ═══════════════════════════════════════════════════════════════

class PopTelemetryCredentialSerializer(serializers.ModelSerializer):
    """管理端读取凭据元数据；刻意不暴露 token_hash 或原始 token。"""

    pop_code = serializers.CharField(source='pop_point.code', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)

    class Meta:
        model = PopTelemetryCredential
        fields = (
            'id', 'pop_point', 'pop_code', 'pop_name', 'token_prefix',
            'is_active', 'rotated_at', 'last_used_at', 'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'pop_code', 'pop_name', 'token_prefix',
            'rotated_at', 'last_used_at', 'created_at', 'updated_at',
        )


class PopTelemetryCredentialWriteSerializer(serializers.ModelSerializer):
    """创建/编辑凭据时允许的非敏感字段。令牌由 ViewSet 专门签发。"""

    class Meta:
        model = PopTelemetryCredential
        fields = ('id', 'pop_point', 'is_active')
        read_only_fields = ('id',)

    def validate_pop_point(self, value):
        if self.instance and value.pk != self.instance.pop_point_id:
            raise serializers.ValidationError('已签发的遥测凭据不可更换 POP，请停用后为新 POP 创建凭据。')
        existing = PopTelemetryCredential.objects.filter(pop_point=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError('该 POP 已有遥测凭据，请使用轮换操作。')
        return value


class EdgeQualitySampleInputSerializer(serializers.Serializer):
    """节点 ingest 的单条样本白名单，central_edge_id 仅兼容节点载荷且绝不信任。"""

    source_sample_id = serializers.CharField(max_length=128, min_length=1)
    interface_name = serializers.CharField(max_length=64, min_length=1)
    target_address = serializers.CharField(max_length=255, min_length=1)
    sampled_at = serializers.DateTimeField()
    latency_ms = serializers.FloatField(allow_null=True, min_value=0)
    packet_loss_pct = serializers.FloatField(allow_null=True, min_value=0, max_value=100)
    jitter_ms = serializers.FloatField(allow_null=True, min_value=0)
    packets_sent = serializers.IntegerField(min_value=0)
    packets_recv = serializers.IntegerField(min_value=0)
    ok = serializers.BooleanField()
    central_edge_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        for field in ('latency_ms', 'packet_loss_pct', 'jitter_ms'):
            value = attrs.get(field)
            if value is not None and not math.isfinite(value):
                raise serializers.ValidationError({field: '必须为有限数值'})
        return attrs


class EdgeQualityIngestRequestSerializer(serializers.Serializer):
    """一次请求最多接收 500 条节点质量采样。"""

    samples = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=False,
        max_length=500,
    )


# ═══════════════════════════════════════════════════════════════
# § 7  同步日志 & 批量操作
# ═══════════════════════════════════════════════════════════════

class MonitoringSyncLogSerializer(serializers.ModelSerializer):
    action_type_label = serializers.CharField(source='get_action_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringSyncLog
        fields = (
            'id', 'zabbix_server', 'monitoring_host',
            'action_type', 'action_type_label',
            'status', 'status_label', 'message', 'detail',
            'operator', 'operator_name', 'duration_ms', 'created_at',
        )

    def get_operator_name(self, obj):
        if not obj.operator_id:
            return '系统'
        return obj.operator.get_full_name() or obj.operator.username


class PopMonitoringProvisionSerializer(serializers.Serializer):
    """为 POP 点批量创建 Zabbix Host（ICMP + SNMP）"""
    pop_point_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
    )
    zabbix_server = serializers.PrimaryKeyRelatedField(
        queryset=ZabbixServer.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        help_text='留空则使用默认 Zabbix 服务器',
    )
    template = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringHostTemplate.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    push_to_zabbix = serializers.BooleanField(default=True)
    sync_graphs = serializers.BooleanField(default=True)


class MonitoringSyncRequestSerializer(serializers.Serializer):
    """触发 Host / Items / Graphs 同步"""
    monitoring_host_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )
    full_sync = serializers.BooleanField(default=False)
    sync_items = serializers.BooleanField(default=True)
    sync_graphs = serializers.BooleanField(default=True)


class ZabbixConnectionTestSerializer(serializers.Serializer):
    zabbix_server = serializers.PrimaryKeyRelatedField(
        queryset=ZabbixServer.objects.all(), required=False, allow_null=True,
    )


class MonitoringHistoryQuerySerializer(serializers.Serializer):
    """查询图形历史数据（对接 history.get / trend.get）"""
    graph_id = serializers.IntegerField(required=False)
    item_id = serializers.IntegerField(required=False)
    time_from = serializers.DateTimeField(required=False)
    time_till = serializers.DateTimeField(required=False)
    period = serializers.ChoiceField(
        choices=['1h', '6h', '24h', '7d', '30d'],
        default='24h',
    )

    def validate(self, attrs):
        if not attrs.get('graph_id') and not attrs.get('item_id'):
            raise serializers.ValidationError('请指定 graph_id 或 item_id')
        return attrs


class GraphDataQuerySerializer(serializers.Serializer):
    time_from = serializers.DateTimeField(required=False)
    time_till = serializers.DateTimeField(required=False)
    period = serializers.ChoiceField(
        choices=['1h', '6h', '24h', '7d', '30d'],
        default='24h',
        required=False,
    )


class TrafficQuerySerializer(serializers.Serializer):
    """按时间截取接口流量 / 95 计费"""
    binding = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringGraphBinding.objects.all(), required=False, allow_null=True,
    )
    monitoring_host = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringHost.objects.all(), required=False, allow_null=True,
    )
    pop_point = serializers.PrimaryKeyRelatedField(
        queryset=PopPoint.objects.all(), required=False, allow_null=True,
    )
    vps = serializers.PrimaryKeyRelatedField(
        queryset=VPS.objects.all(), required=False, allow_null=True,
    )
    edge = serializers.PrimaryKeyRelatedField(
        queryset=EdgeAcceleration.objects.all(), required=False, allow_null=True,
    )
    graph = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringGraph.objects.all(), required=False, allow_null=True,
    )
    profile = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringGraphProfile.objects.filter(is_active=True),
        required=False, allow_null=True,
    )
    in_item = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringItem.objects.all(), required=False, allow_null=True,
    )
    out_item = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringItem.objects.all(), required=False, allow_null=True,
    )
    interface_name = serializers.CharField(required=False, allow_blank=True, default='')
    time_from = serializers.DateTimeField()
    time_till = serializers.DateTimeField()
    title = serializers.CharField(required=False, allow_blank=True, default='')
    persist = serializers.BooleanField(default=False, help_text='是否保存为 TrafficReport')

    def validate(self, attrs):
        if attrs['time_from'] >= attrs['time_till']:
            raise serializers.ValidationError({'time_till': '结束时间必须晚于开始时间'})
        targets = [
            attrs.get('binding'), attrs.get('monitoring_host'),
            attrs.get('pop_point'), attrs.get('vps'), attrs.get('edge'),
        ]
        if not any(targets):
            raise serializers.ValidationError(
                '请指定 binding 或 monitoring_host 或 pop_point / vps / edge',
            )
        return attrs


class MonitoringTrafficReportListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    percentile_mbps = serializers.FloatField(read_only=True)
    traffic_billing = serializers.SerializerMethodField()

    class Meta:
        model = MonitoringTrafficReport
        fields = (
            'id', 'report_no', 'title', 'target_type', 'interface_name',
            'time_from', 'time_till', 'sample_count',
            'percentile_n', 'percentile_bps', 'percentile_mbps',
            'peak_bps', 'avg_bps', 'status', 'status_label', 'created_at',
            'traffic_billing',
        )

    def get_traffic_billing(self, obj):
        from customer_manager.traffic_billing import report_billing_summary
        return report_billing_summary(obj)


class MonitoringTrafficReportSerializer(MonitoringTrafficReportListSerializer):
    profile_name = serializers.CharField(source='profile.name', read_only=True, default='')
    resource_label = serializers.SerializerMethodField()

    class Meta(MonitoringTrafficReportListSerializer.Meta):
        fields = MonitoringTrafficReportListSerializer.Meta.fields + (
            'monitoring_host', 'graph', 'profile', 'profile_name',
            'pop_point', 'vps', 'edge', 'resource_label',
            'in_item', 'out_item', 'billing_mode',
            'total_in_bytes', 'total_out_bytes', 'result', 'error_message',
            'computed_at',
        )

    def get_resource_label(self, obj):
        if obj.pop_point_id:
            return f'[{obj.pop_point.code}] {obj.pop_point.name}'
        if obj.vps_id:
            return f'[{obj.vps.vps_no}] {obj.vps.name}'
        if obj.edge_id:
            return f'[{obj.edge.edge_no}] {obj.edge.name}'
        return '-'


# ═══════════════════════════════════════════════════════════════
# § WebSSH 命令模板
# ═══════════════════════════════════════════════════════════════

class WebSSHCommandTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebSSHCommandTemplate
        fields = (
            'id', 'code', 'name', 'category', 'description', 'command',
            'fields', 'interactive', 'is_system', 'is_active', 'sort_order',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'is_system', 'created_at', 'updated_at')

    def validate_code(self, value):
        code = (value or '').strip()
        if not code:
            raise serializers.ValidationError('编码不能为空')
        if self.instance and self.instance.is_system and code != self.instance.code:
            raise serializers.ValidationError('系统内置模板不可修改编码')
        return code

    def validate_fields(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('fields 必须是数组')
        cleaned = []
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f'第 {idx + 1} 个参数无效')
            key = (item.get('key') or '').strip()
            if not key:
                raise serializers.ValidationError(f'第 {idx + 1} 个参数缺少 key')
            cleaned.append({
                'key': key,
                'label': (item.get('label') or key).strip(),
                'default': item.get('default', ''),
                'required': bool(item.get('required')),
            })
        return cleaned

    def validate(self, attrs):
        if self.instance and self.instance.is_system:
            if 'is_system' in attrs and not attrs['is_system']:
                raise serializers.ValidationError({'is_system': '系统内置模板不可取消标记'})
        return attrs
