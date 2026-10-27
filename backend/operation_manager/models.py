"""
operation_manager/models.py
运维管理 — Zabbix 监控对接与资源映射

模型层次：
  ZabbixServer              Zabbix API 连接配置
  MonitoringHostTemplate    主机/监控项创建模板（ICMP / SNMP）
  MonitoringHost            ISP 资源 ↔ Zabbix Host 映射
  MonitoringItem            Zabbix 监控项本地镜像
  MonitoringGraph           Zabbix 图形本地镜像
  MonitoringGraphProfile    可定制图形/计费规则（95计费、聚合方式等）
  MonitoringGraphSeries     定制图形的数据序列（入/出/计费项）
  MonitoringGraphBinding    图形与 POP / VPS / Edge 的展示绑定
  MonitoringTrafficReport   按时间窗口截取的流量/95计费报告
  MonitoringSyncLog         同步与下发操作日志

设计原则：
  - ISPmanager 维护「业务资源 → Zabbix 对象」映射，图形/流量在详情页按绑定展示；
  - Zabbix 为监控数据权威源，本地仅存映射与最近同步快照；
  - POP 默认 ICMP + SNMP（节点负载）；VPS/Edge 以接口流量类图形为主。
"""

from __future__ import annotations

import datetime
import hashlib
import secrets

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════
# § 1  Zabbix 连接配置
# ═══════════════════════════════════════════════════════════════

class ZabbixServer(models.Model):
    """Zabbix Server API 连接配置（支持 Token 或用户名密码）"""

    class AuthType(models.TextChoices):
        API_TOKEN = 'api_token', 'API Token'
        USER_PASS = 'user_pass', '用户名密码'

    name = models.CharField('配置名称', max_length=64, unique=True)
    api_url = models.URLField(
        'API 地址',
        help_text='如 https://zabbix.example.com/api_jsonrpc.php',
    )
    auth_type = models.CharField(
        '认证方式', max_length=16,
        choices=AuthType.choices, default=AuthType.API_TOKEN,
    )
    api_token = models.CharField('API Token', max_length=512, blank=True)
    username = models.CharField('用户名', max_length=128, blank=True)
    password = models.CharField('密码', max_length=256, blank=True)

    verify_ssl = models.BooleanField('校验 SSL 证书', default=True)
    timeout_sec = models.PositiveSmallIntegerField('请求超时(秒)', default=30)

    # Zabbix 侧默认分组/模板（创建 Host 时使用，可为空由模板覆盖）
    default_host_group_id = models.CharField('默认 Host Group ID', max_length=32, blank=True)
    default_host_group_name = models.CharField('默认 Host Group 名称', max_length=128, blank=True)

    is_default = models.BooleanField('默认服务器', default=False)
    is_active = models.BooleanField('启用', default=True)

    last_sync_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    last_sync_status = models.CharField('最近同步状态', max_length=32, blank=True)
    last_sync_message = models.TextField('最近同步消息', blank=True)

    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'Zabbix 服务器'
        verbose_name_plural = 'Zabbix 服务器'
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.auth_type == self.AuthType.API_TOKEN and not (self.api_token or '').strip():
            raise ValidationError({'api_token': 'API Token 认证方式下必须填写 Token'})
        if self.auth_type == self.AuthType.USER_PASS:
            if not (self.username or '').strip():
                raise ValidationError({'username': '用户名密码认证方式下必须填写用户名'})
            if not (self.password or '').strip() and not self.pk:
                raise ValidationError({'password': '新建配置时请填写密码'})

    def save(self, *args, **kwargs):
        if self.is_default:
            ZabbixServer.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 2  监控 Host 模板
# ═══════════════════════════════════════════════════════════════

class MonitoringHostTemplate(models.Model):
    """
    创建 Zabbix Host 时使用的模板。
    POP 点：ICMP 可用性 + SNMP 系统/接口负载；
    VPS/Edge：可按接口流量模板扩展。
    """

    class TargetKind(models.TextChoices):
        POP = 'pop', 'POP 节点'
        VPS = 'vps', 'VPS'
        EDGE = 'edge', '边缘加速'
        GENERIC = 'generic', '通用'

    class MonitorMode(models.TextChoices):
        ICMP = 'icmp', 'ICMP'
        SNMP = 'snmp', 'SNMP'
        ICMP_SNMP = 'icmp_snmp', 'ICMP + SNMP'

    name = models.CharField('模板名称', max_length=128, unique=True)
    target_kind = models.CharField(
        '适用资源类型', max_length=16,
        choices=TargetKind.choices, default=TargetKind.POP,
    )
    monitor_mode = models.CharField(
        '监控方式', max_length=16,
        choices=MonitorMode.choices, default=MonitorMode.ICMP_SNMP,
    )
    zabbix_server = models.ForeignKey(
        ZabbixServer, on_delete=models.PROTECT,
        verbose_name='Zabbix 服务器', related_name='host_templates',
    )

    # Zabbix 侧 ID（字符串，兼容大整数）
    zabbix_host_group_ids = models.JSONField(
        'Host Group IDs', default=list, blank=True,
        help_text='如 ["2","4"]',
    )
    zabbix_template_ids = models.JSONField(
        'Template IDs', default=list, blank=True,
        help_text='Link 到 Host 的 Template ID 列表',
    )
    zabbix_interface_type = models.PositiveSmallIntegerField(
        'Agent 接口类型', default=2,
        help_text='1=Agent 2=SNMP 3=IPMI 4=JMX；POP SNMP 通常为 2',
    )
    snmp_community_macro = models.CharField(
        'SNMP Community 宏', max_length=64, blank=True,
        help_text='留空则使用资源 snmp_community 或 {$SNMP_COMMUNITY}',
    )
    icmp_use_mgmt_ip = models.BooleanField(
        'ICMP 使用管理 IP', default=True,
        help_text='POP/VPS 默认 ping mgmt_ip',
    )

    auto_create_items = models.BooleanField('同步后自动发现 Items', default=True)
    auto_create_graphs = models.BooleanField('同步后自动发现 Graphs', default=True)
    graph_name_patterns = models.JSONField(
        '图形名称匹配规则', default=list, blank=True,
        help_text='正则或关键字列表，如 ["CPU", "Memory", "Traffic", "流量"]',
    )

    is_active = models.BooleanField('启用', default=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '监控 Host 模板'
        verbose_name_plural = '监控 Host 模板'
        ordering = ['target_kind', 'name']

    def __str__(self):
        return f'{self.name} ({self.get_target_kind_display()})'


# ═══════════════════════════════════════════════════════════════
# § 3  监控 Host（资源 ↔ Zabbix Host）
# ═══════════════════════════════════════════════════════════════

class MonitoringHost(models.Model):
    """
    Zabbix 主机本地镜像。

    主机来源有两种：
      - 由本系统创建并下发到 Zabbix（host_kind=pop 时可关联 POP 节点）；
      - 从 Zabbix 服务器同步而来的现有主机（host_kind=generic，无业务资源）。

    VPS / 边缘加速不再各自拥有监控主机，而是通过 MonitoringGraphBinding
    关联到某台监控主机的监控项 / 图形。
    """

    class TargetType(models.TextChoices):
        # 资源类型枚举（供图形绑定 / 流量报告复用，pop/vps/edge）
        POP = 'pop', 'POP 节点'
        VPS = 'vps', 'VPS'
        EDGE = 'edge', '边缘加速'

    class HostKind(models.TextChoices):
        POP = 'pop', 'POP 节点'
        GENERIC = 'generic', '通用主机'

    class SyncStatus(models.TextChoices):
        PENDING = 'pending', '待同步'
        SYNCING = 'syncing', '同步中'
        SYNCED = 'synced', '已同步'
        ERROR = 'error', '同步失败'
        DISABLED = 'disabled', '已停用'

    zabbix_server = models.ForeignKey(
        ZabbixServer, on_delete=models.PROTECT,
        verbose_name='Zabbix 服务器', related_name='monitoring_hosts',
    )
    template = models.ForeignKey(
        MonitoringHostTemplate, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='创建模板', related_name='monitoring_hosts',
    )

    host_kind = models.CharField(
        '主机类型', max_length=8,
        choices=HostKind.choices, default=HostKind.GENERIC,
    )
    # 兼容旧字段（不再用于业务判断）
    target_type = models.CharField('资源类型', max_length=8, blank=True, default='')
    pop_point = models.OneToOneField(
        'resource_manager.PopPoint', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='POP 节点', related_name='monitoring_host',
    )
    service_pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='服务 POP 点',
        related_name='scoped_monitoring_hosts',
        help_text='通用监控主机归属 POP，VPS/边缘加速关联时按 POP 过滤',
    )

    # Zabbix 侧缓存统计（轻量同步，不必拉取全部明细）
    zabbix_item_count = models.PositiveIntegerField('Zabbix 监控项数', null=True, blank=True)
    zabbix_graph_count = models.PositiveIntegerField('Zabbix 图形数', null=True, blank=True)

    # Zabbix 侧
    zabbix_hostid = models.CharField('Zabbix Host ID', max_length=32, blank=True, db_index=True)
    hostname = models.CharField('Zabbix Hostname', max_length=128, blank=True)
    visible_name = models.CharField('可见名称', max_length=256, blank=True)
    monitored_ip = models.GenericIPAddressField(
        '监控 IP', null=True, blank=True,
        help_text='ICMP/SNMP 探测地址，通常等于 mgmt_ip',
    )
    snmp_community = models.CharField('SNMP Community', max_length=128, blank=True)
    snmp_version = models.CharField(
        'SNMP 版本', max_length=4, default='2',
        choices=[('1', 'SNMPv1'), ('2', 'SNMPv2'), ('3', 'SNMPv3')],
    )
    snmp_port = models.PositiveIntegerField('SNMP 端口', default=161)

    # 创建 Zabbix Host 时选用的群组 / 模板（字符串 ID，兼容大整数）
    zabbix_group_ids = models.JSONField(
        'Host Group IDs', default=list, blank=True,
        help_text='Zabbix 主机群组 ID 列表，如 ["2"]',
    )
    zabbix_template_ids = models.JSONField(
        'Template IDs', default=list, blank=True,
        help_text='链接到主机的模板 ID 列表，如 Linux by SNMP',
    )

    enable_icmp = models.BooleanField('启用 ICMP', default=True)
    enable_snmp = models.BooleanField('启用 SNMP', default=True)
    is_enabled = models.BooleanField('监控启用', default=True)

    sync_status = models.CharField(
        '同步状态', max_length=16,
        choices=SyncStatus.choices, default=SyncStatus.PENDING,
    )
    last_sync_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    last_sync_error = models.TextField('最近同步错误', blank=True)
    zabbix_raw = models.JSONField('Zabbix 原始快照', default=dict, blank=True)

    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '监控 Host'
        verbose_name_plural = '监控 Host 列表'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['host_kind', 'sync_status']),
            models.Index(fields=['zabbix_server', 'zabbix_hostid']),
        ]

    def __str__(self):
        return self.visible_name or self.hostname or f'MonitoringHost#{self.pk}'

    def clean(self):
        super().clean()
        if self.host_kind == self.HostKind.GENERIC:
            self.pop_point = None
        else:
            self.service_pop_point = None

    @property
    def scoped_pop_point_id(self):
        """用于 VPS/Edge 过滤：POP 主机取 pop_point，通用主机取 service_pop_point。"""
        if self.host_kind == self.HostKind.POP:
            return self.pop_point_id
        return self.service_pop_point_id

    @property
    def resource(self):
        return self.pop_point

    @property
    def resource_label(self) -> str:
        if self.pop_point_id and self.pop_point:
            return f'[{self.pop_point.code}] {self.pop_point.name}'
        return self.visible_name or self.hostname or '-'

    def mark_syncing(self):
        self.sync_status = self.SyncStatus.SYNCING
        self.last_sync_error = ''
        self.save(update_fields=['sync_status', 'last_sync_error', 'updated_at'])

    def mark_synced(self, *, hostid: str | None = None, raw: dict | None = None):
        self.sync_status = self.SyncStatus.SYNCED
        self.last_sync_at = timezone.now()
        self.last_sync_error = ''
        if hostid:
            self.zabbix_hostid = str(hostid)
        if raw is not None:
            self.zabbix_raw = raw
        self.save(update_fields=[
            'sync_status', 'last_sync_at', 'last_sync_error',
            'zabbix_hostid', 'zabbix_raw', 'updated_at',
        ])

    def mark_error(self, message: str):
        self.sync_status = self.SyncStatus.ERROR
        self.last_sync_error = (message or '')[:2000]
        self.last_sync_at = timezone.now()
        self.save(update_fields=['sync_status', 'last_sync_error', 'last_sync_at', 'updated_at'])


# ═══════════════════════════════════════════════════════════════
# § 4  监控项镜像
# ═══════════════════════════════════════════════════════════════

class MonitoringItem(models.Model):
    """Zabbix Item 本地镜像，供图形绑定与最新值展示"""

    class ValueType(models.TextChoices):
        FLOAT = '0', 'Numeric(float)'
        CHAR = '1', 'Character'
        LOG = '2', 'Log'
        UNSIGNED = '3', 'Numeric(unsigned)'
        TEXT = '4', 'Text'

    monitoring_host = models.ForeignKey(
        MonitoringHost, on_delete=models.CASCADE,
        verbose_name='监控 Host', related_name='items',
    )
    zabbix_itemid = models.CharField('Zabbix Item ID', max_length=32, db_index=True)
    item_key = models.CharField('Item Key', max_length=256, db_index=True)
    name = models.CharField('名称', max_length=512)
    units = models.CharField('单位', max_length=64, blank=True)
    value_type = models.CharField(
        '值类型', max_length=4,
        choices=ValueType.choices, default=ValueType.FLOAT,
    )

    last_value = models.CharField('最近值', max_length=512, blank=True)
    last_clock = models.DateTimeField('最近采集时间', null=True, blank=True)
    is_enabled = models.BooleanField('启用', default=True)
    is_primary_traffic = models.BooleanField(
        '主流量项', default=False,
        help_text='VPS/Edge 详情页默认展示的入出流量 Item',
    )
    traffic_direction = models.CharField(
        '流量方向', max_length=8, blank=True,
        choices=[
            ('in', '入站'),
            ('out', '出站'),
            ('both', '双向'),
            ('unknown', '未知'),
        ],
        default='unknown',
    )
    interface_name = models.CharField(
        '接口名', max_length=64, blank=True,
        help_text='从 Item Key/名称解析，如 eth0、gre0；用于按接口截取流量',
    )

    zabbix_raw = models.JSONField('Zabbix 原始快照', default=dict, blank=True)
    synced_at = models.DateTimeField('同步时间', null=True, blank=True)

    class Meta:
        verbose_name = '监控项'
        verbose_name_plural = '监控项列表'
        ordering = ['monitoring_host', 'name']
        unique_together = [('monitoring_host', 'zabbix_itemid')]
        indexes = [
            models.Index(fields=['monitoring_host', 'item_key']),
            models.Index(fields=['is_primary_traffic']),
            models.Index(fields=['monitoring_host', 'interface_name']),
            models.Index(fields=['traffic_direction']),
        ]

    def __str__(self):
        return f'{self.name} ({self.item_key})'


# ═══════════════════════════════════════════════════════════════
# § 5  定制图形 / 计费规则
# ═══════════════════════════════════════════════════════════════

class MonitoringGraphProfile(models.Model):
    """
    可复用的监控图形与计费规则定义。
    典型场景：接口流量时序图、95 峰值计费、入/出叠加图。
    """

    class ProfileType(models.TextChoices):
        TIMESERIES = 'timeseries', '时序流量图'
        PERCENTILE_BILLING = 'percentile_billing', '95/99 峰值计费'
        STACKED_TRAFFIC = 'stacked_traffic', '入出叠加图'
        SINGLE_METRIC = 'single_metric', '单指标图'

    class Aggregation(models.TextChoices):
        AVG = 'avg', '平均值'
        MAX = 'max', '最大值'
        MIN = 'min', '最小值'
        SUM = 'sum', '求和'
        PEAK = 'peak', '峰值'
        P95 = 'p95', '95 分位'
        P99 = 'p99', '99 分位'

    class BillingMode(models.TextChoices):
        """95 计费：每个采样点先取入/出较大值，再算分位数"""
        MAX_IN_OUT = 'max_in_out', '每采样 max(入,出) 再取分位'
        IN_ONLY = 'in_only', '仅入站'
        OUT_ONLY = 'out_only', '仅出站'
        SUM_IN_OUT = 'sum_in_out', '入+出求和再取分位'

    class UnitDisplay(models.TextChoices):
        BPS = 'bps', 'bps'
        KBPS = 'Kbps', 'Kbps'
        MBPS = 'Mbps', 'Mbps'
        GBPS = 'Gbps', 'Gbps'

    code = models.CharField('规则编码', max_length=64, unique=True)
    name = models.CharField('规则名称', max_length=128)
    profile_type = models.CharField(
        '类型', max_length=24,
        choices=ProfileType.choices, default=ProfileType.TIMESERIES,
    )
    aggregation = models.CharField(
        '聚合方式', max_length=16,
        choices=Aggregation.choices, default=Aggregation.P95,
    )
    percentile_n = models.PositiveSmallIntegerField(
        '分位值 N', default=95,
        help_text='95 计费填 95；99 填 99',
    )
    billing_mode = models.CharField(
        '计费口径', max_length=16,
        choices=BillingMode.choices, default=BillingMode.MAX_IN_OUT,
    )
    unit_display = models.CharField(
        '展示单位', max_length=8,
        choices=UnitDisplay.choices, default=UnitDisplay.MBPS,
    )
    sample_align_sec = models.PositiveIntegerField(
        '采样对齐(秒)', default=300,
        help_text='与 Zabbix 采集间隔对齐，常用 300（5 分钟）',
    )

    item_key_in_patterns = models.JSONField(
        '入站 Item Key 匹配', default=list, blank=True,
        help_text='如 ["net.if.in*", "*ifHCInOctets*"]',
    )
    item_key_out_patterns = models.JSONField(
        '出站 Item Key 匹配', default=list, blank=True,
        help_text='如 ["net.if.out*", "*ifHCOutOctets*"]',
    )
    interface_name_pattern = models.CharField(
        '接口名过滤(正则)', max_length=128, blank=True,
        help_text='如 ^eth0$|^gre\\d+$，留空表示全部接口',
    )
    stack_series = models.BooleanField('叠加展示入/出', default=True)
    fill_missing = models.CharField(
        '缺失采样', max_length=8,
        choices=[('zero', '补零'), ('skip', '跳过')],
        default='skip',
    )
    convert_counter_to_rate = models.BooleanField(
        '计数器转速率', default=True,
        help_text='SNMP 字节计数器自动换算为 bps',
    )

    is_system = models.BooleanField('系统预置', default=False)
    is_active = models.BooleanField('启用', default=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '监控图形规则'
        verbose_name_plural = '监控图形规则'
        ordering = ['profile_type', 'name']

    def __str__(self):
        return f'[{self.code}] {self.name}'


class MonitoringGraph(models.Model):
    """Zabbix 图形镜像或 ISP 定制流量图形"""

    class GraphKind(models.TextChoices):
        SYSTEM = 'system', '系统负载'
        TRAFFIC = 'traffic', '流量'
        ICMP = 'icmp', 'ICMP/可用性'
        BILLING = 'billing', '计费/95峰值'
        CUSTOM = 'custom', '自定义'

    class SourceType(models.TextChoices):
        ZABBIX = 'zabbix', 'Zabbix 原生图形'
        CUSTOM = 'custom', '定制图形'

    monitoring_host = models.ForeignKey(
        MonitoringHost, on_delete=models.CASCADE,
        verbose_name='监控 Host', related_name='graphs',
    )
    source_type = models.CharField(
        '来源', max_length=16,
        choices=SourceType.choices, default=SourceType.ZABBIX,
    )
    profile = models.ForeignKey(
        MonitoringGraphProfile, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='图形规则', related_name='graphs',
    )
    zabbix_graphid = models.CharField(
        'Zabbix Graph ID', max_length=32, blank=True, db_index=True,
    )
    name = models.CharField('图形名称', max_length=512)
    display_title = models.CharField(
        '展示标题', max_length=256, blank=True,
        help_text='留空则用 name',
    )
    graph_kind = models.CharField(
        '图形分类', max_length=16,
        choices=GraphKind.choices, default=GraphKind.CUSTOM,
    )
    width = models.PositiveSmallIntegerField('宽度', default=900)
    height = models.PositiveSmallIntegerField('高度', default=200)

    # 定制图形：接口过滤、规则覆盖
    interface_name = models.CharField(
        '绑定接口', max_length=64, blank=True,
        help_text='定制流量图绑定的接口名，如 eth0 / gre100',
    )
    custom_config = models.JSONField(
        '定制配置', default=dict, blank=True,
        help_text='覆盖 profile 的参数，如 {"percentile_n":95,"billing_mode":"max_in_out"}',
    )
    billing_enabled = models.BooleanField(
        '启用计费统计', default=False,
        help_text='详情页展示 95 峰值等计费指标',
    )

    items = models.ManyToManyField(
        MonitoringItem, blank=True,
        verbose_name='关联监控项', related_name='graphs',
    )
    zabbix_itemids = models.JSONField(
        'Zabbix Item IDs', default=list, blank=True,
        help_text='graph.get 返回的 gitems itemid 列表',
    )

    is_enabled = models.BooleanField('启用', default=True)
    is_default = models.BooleanField(
        '默认展示', default=False,
        help_text='资源详情页默认选中的图形',
    )
    zabbix_raw = models.JSONField('Zabbix 原始快照', default=dict, blank=True)
    synced_at = models.DateTimeField('同步时间', null=True, blank=True)

    class Meta:
        verbose_name = '监控图形'
        verbose_name_plural = '监控图形列表'
        ordering = ['monitoring_host', 'graph_kind', 'name']

    def __str__(self):
        return self.display_title or self.name

    @property
    def effective_title(self) -> str:
        return self.display_title or self.name


class MonitoringGraphSeries(models.Model):
    """定制图形中的序列（入站/出站/计费）"""

    class SeriesRole(models.TextChoices):
        IN = 'in', '入站'
        OUT = 'out', '出站'
        BILLABLE = 'billable', '计费合成'
        CUSTOM = 'custom', '自定义'

    graph = models.ForeignKey(
        MonitoringGraph, on_delete=models.CASCADE,
        verbose_name='所属图形', related_name='series_list',
    )
    monitoring_item = models.ForeignKey(
        MonitoringItem, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='监控项', related_name='graph_series',
    )
    series_role = models.CharField(
        '序列角色', max_length=16,
        choices=SeriesRole.choices, default=SeriesRole.CUSTOM,
    )
    label = models.CharField('图例名称', max_length=128, blank=True)
    color = models.CharField('颜色', max_length=16, blank=True, help_text='如 #409EFF')
    display_order = models.PositiveSmallIntegerField('排序', default=100)
    multiply_factor = models.FloatField('倍率', default=1.0)
    is_visible = models.BooleanField('展示', default=True)

    class Meta:
        verbose_name = '图形序列'
        verbose_name_plural = '图形序列'
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.label or f'{self.get_series_role_display()}#{self.pk}'


class MonitoringTrafficReport(models.Model):
    """
    按时间窗口截取的接口流量 / 95 计费报告。
    可保存供账单核对，也可即时查询不持久化（API 可选 persist=true）。
    """

    class ReportStatus(models.TextChoices):
        PENDING = 'pending', '待计算'
        COMPUTING = 'computing', '计算中'
        DONE = 'done', '已完成'
        FAILED = 'failed', '失败'
        ARCHIVED = 'archived', '已归档'

    report_no = models.CharField('报告编号', max_length=32, unique=True, editable=False)
    title = models.CharField('标题', max_length=256, blank=True)

    target_type = models.CharField(
        '资源类型', max_length=8,
        choices=MonitoringHost.TargetType.choices,
    )
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.CASCADE,
        null=True, blank=True, related_name='traffic_reports',
    )
    vps = models.ForeignKey(
        'resource_manager.VPS', on_delete=models.CASCADE,
        null=True, blank=True, related_name='traffic_reports',
    )
    edge = models.ForeignKey(
        'resource_manager.EdgeAcceleration', on_delete=models.CASCADE,
        null=True, blank=True, related_name='traffic_reports',
    )
    monitoring_host = models.ForeignKey(
        MonitoringHost, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='traffic_reports',
    )
    graph = models.ForeignKey(
        MonitoringGraph, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='traffic_reports',
    )
    profile = models.ForeignKey(
        MonitoringGraphProfile, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='traffic_reports',
    )

    interface_name = models.CharField('接口名', max_length=64, blank=True)
    in_item = models.ForeignKey(
        MonitoringItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reports_as_in',
    )
    out_item = models.ForeignKey(
        MonitoringItem, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='reports_as_out',
    )

    time_from = models.DateTimeField('开始时间')
    time_till = models.DateTimeField('结束时间')

    # 计算结果摘要（bps，原始值；展示时按 unit 换算）
    sample_count = models.PositiveIntegerField('采样点数', default=0)
    peak_bps = models.DecimalField('峰值(bps)', max_digits=20, decimal_places=4, null=True, blank=True)
    avg_bps = models.DecimalField('平均(bps)', max_digits=20, decimal_places=4, null=True, blank=True)
    percentile_bps = models.DecimalField(
        '分位值(bps)', max_digits=20, decimal_places=4, null=True, blank=True,
        help_text='95 计费结果（bps）',
    )
    percentile_n = models.PositiveSmallIntegerField('分位 N', default=95)
    billing_mode = models.CharField('计费口径', max_length=16, blank=True)
    total_in_bytes = models.DecimalField('入站总字节', max_digits=24, decimal_places=0, null=True, blank=True)
    total_out_bytes = models.DecimalField('出站总字节', max_digits=24, decimal_places=0, null=True, blank=True)

    status = models.CharField(
        '状态', max_length=16,
        choices=ReportStatus.choices, default=ReportStatus.PENDING,
    )
    error_message = models.TextField('错误信息', blank=True)
    result = models.JSONField(
        '完整结果', default=dict, blank=True,
        help_text='timeseries、billing、meta 等结构化数据',
    )

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    computed_at = models.DateTimeField('计算完成时间', null=True, blank=True)

    class Meta:
        verbose_name = '流量/计费报告'
        verbose_name_plural = '流量/计费报告'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['target_type', 'time_from', 'time_till']),
            models.Index(fields=['monitoring_host', 'interface_name']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return self.title or self.report_no

    def save(self, *args, **kwargs):
        if not self.report_no:
            prefix = 'TR'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = MonitoringTrafficReport.objects.filter(
                report_no__startswith=f'{prefix}{date_str}'
            ).order_by('-report_no').first()
            seq = int(last.report_no[-4:]) + 1 if last else 1
            self.report_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def percentile_mbps(self):
        if self.percentile_bps is None:
            return None
        return float(self.percentile_bps) / 1_000_000


# ═══════════════════════════════════════════════════════════════
# § 6  图形绑定
# ═══════════════════════════════════════════════════════════════

class MonitoringGraphBinding(models.Model):
    """
    将监控图形绑定到业务资源详情页展示位。
    通常 monitoring_host 已关联资源，本表用于同一 Host 多图排序/分组。
    """

    class PanelSlot(models.TextChoices):
        OVERVIEW = 'overview', '概览'
        TRAFFIC = 'traffic', '流量'
        SYSTEM = 'system', '系统负载'
        ICMP = 'icmp', '可用性'
        CUSTOM = 'custom', '自定义'

    graph = models.ForeignKey(
        MonitoringGraph, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='监控图形', related_name='bindings',
    )
    monitoring_item = models.ForeignKey(
        MonitoringItem, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='监控项', related_name='bindings',
        help_text='可绑定单个监控项（与图形二选一）',
    )
    monitoring_host = models.ForeignKey(
        MonitoringHost, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='监控主机', related_name='resource_bindings',
    )
    target_type = models.CharField(
        '展示资源类型', max_length=8,
        choices=MonitoringHost.TargetType.choices,
    )
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='POP 节点', related_name='monitor_graph_bindings',
    )
    vps = models.ForeignKey(
        'resource_manager.VPS', on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='VPS', related_name='monitor_graph_bindings',
    )
    edge = models.ForeignKey(
        'resource_manager.EdgeAcceleration', on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='边缘加速', related_name='monitor_graph_bindings',
    )

    panel_slot = models.CharField(
        '展示位', max_length=16,
        choices=PanelSlot.choices, default=PanelSlot.OVERVIEW,
    )
    panel_title = models.CharField('面板标题', max_length=128, blank=True)
    display_order = models.PositiveSmallIntegerField('排序', default=100)
    is_visible = models.BooleanField('详情页可见', default=True)

    class Meta:
        verbose_name = '监控图形绑定'
        verbose_name_plural = '监控图形绑定'
        ordering = ['display_order', 'id']
        indexes = [
            models.Index(fields=['target_type', 'panel_slot']),
        ]

    def __str__(self):
        return self.panel_title or (self.graph and self.graph.name) or (
            self.monitoring_item and self.monitoring_item.name) or f'Binding#{self.pk}'

    @property
    def bind_kind(self) -> str:
        return 'graph' if self.graph_id else 'item'

    @property
    def effective_host(self):
        if self.monitoring_host_id:
            return self.monitoring_host
        if self.graph_id:
            return self.graph.monitoring_host
        if self.monitoring_item_id:
            return self.monitoring_item.monitoring_host
        return None

    def clean(self):
        super().clean()
        if not self.graph_id and not self.monitoring_item_id:
            raise ValidationError('请绑定监控图形或监控项')
        if self.target_type == MonitoringHost.TargetType.POP and not self.pop_point_id:
            raise ValidationError({'pop_point': 'target_type=pop 时必须指定 POP'})
        if self.target_type == MonitoringHost.TargetType.VPS and not self.vps_id:
            raise ValidationError({'vps': 'target_type=vps 时必须指定 VPS'})
        if self.target_type == MonitoringHost.TargetType.EDGE and not self.edge_id:
            raise ValidationError({'edge': 'target_type=edge 时必须指定 Edge'})

    def save(self, *args, **kwargs):
        if not self.monitoring_host_id:
            host = self.effective_host
            if host:
                self.monitoring_host = host
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 7  同步日志
# ═══════════════════════════════════════════════════════════════

class MonitoringSyncLog(models.Model):
    """Zabbix 同步 / Host 创建 / 图形发现操作日志"""

    class ActionType(models.TextChoices):
        TEST_CONNECTION = 'test_connection', '测试连接'
        CREATE_HOST = 'create_host', '创建 Host'
        UPDATE_HOST = 'update_host', '更新 Host'
        DELETE_HOST = 'delete_host', '删除 Host'
        SYNC_ITEMS = 'sync_items', '同步 Items'
        SYNC_GRAPHS = 'sync_graphs', '同步 Graphs'
        SYNC_HISTORY = 'sync_history', '同步历史数据'
        TRAFFIC_QUERY = 'traffic_query', '流量查询'
        BILLING_COMPUTE = 'billing_compute', '计费计算'
        BIND_GRAPHS = 'bind_graphs', '绑定图形'
        FULL_SYNC = 'full_sync', '全量同步'
        OTHER = 'other', '其他'

    class Status(models.TextChoices):
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'

    zabbix_server = models.ForeignKey(
        ZabbixServer, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Zabbix 服务器', related_name='sync_logs',
    )
    monitoring_host = models.ForeignKey(
        MonitoringHost, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='监控 Host', related_name='sync_logs',
    )
    action_type = models.CharField('操作类型', max_length=32, choices=ActionType.choices)
    status = models.CharField('状态', max_length=16, choices=Status.choices)
    message = models.CharField('摘要', max_length=512)
    detail = models.JSONField('详情', default=dict, blank=True)

    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='操作人',
    )
    duration_ms = models.PositiveIntegerField('耗时(ms)', null=True, blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        verbose_name = '监控同步日志'
        verbose_name_plural = '监控同步日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action_type', 'created_at']),
            models.Index(fields=['monitoring_host', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.get_status_display()}] {self.get_action_type_display()} - {self.message[:40]}'


# ═══════════════════════════════════════════════════════════════
# § 8  POP 品质监控
# ═══════════════════════════════════════════════════════════════

class PopQualityMonitor(models.Model):
    """
    POP 管理入口的独立品质监控配置与当前状态。

    探测目标不在本表重复存储，而是每次从关联 POP 的 ``mgmt_ip`` / ``mgmt_url``
    读取，避免管理地址变更后监控仍指向旧地址。采样记录保留本次实际使用的目标，
    便于审计历史数据。
    """

    class Status(models.TextChoices):
        UNKNOWN = 'unknown', '未探测'
        HEALTHY = 'healthy', '正常'
        DEGRADED = 'degraded', '品质下降'
        UNREACHABLE = 'unreachable', '不可达'
        ERROR = 'error', '探测异常'
        SKIPPED = 'skipped', '已跳过'
        DISABLED = 'disabled', '已停用'

    pop_point = models.OneToOneField(
        'resource_manager.PopPoint',
        on_delete=models.CASCADE,
        related_name='quality_monitor',
        verbose_name='POP 节点',
    )
    is_enabled = models.BooleanField('启用品质监控', default=True)
    enable_icmp = models.BooleanField('启用 ICMP 探测', default=True)
    enable_http = models.BooleanField('启用管理 URL 探测', default=True)
    probe_interval_seconds = models.PositiveIntegerField('探测间隔(秒)', default=60)
    timeout_seconds = models.PositiveSmallIntegerField('单次超时(秒)', default=5)
    icmp_count = models.PositiveSmallIntegerField('每轮 Ping 次数', default=4)

    packet_loss_threshold_pct = models.FloatField('丢包告警阈值(%)', default=20.0)
    consecutive_breach_threshold = models.PositiveSmallIntegerField('连续超阈次数', default=2)
    alert_repeat_minutes = models.PositiveSmallIntegerField('重复告警间隔(分钟)', default=15)

    status = models.CharField(
        '当前状态', max_length=16,
        choices=Status.choices, default=Status.UNKNOWN,
    )
    last_probe_at = models.DateTimeField('最近探测时间', null=True, blank=True)
    next_probe_at = models.DateTimeField('下次探测时间', null=True, blank=True)
    probe_lock_until = models.DateTimeField('探测租约到期', null=True, blank=True)
    last_icmp_packet_loss_pct = models.FloatField('最近丢包率(%)', null=True, blank=True)
    last_icmp_avg_ms = models.FloatField('最近 ICMP 平均时延(ms)', null=True, blank=True)
    last_http_response_ms = models.FloatField('最近 HTTP 响应时间(ms)', null=True, blank=True)
    last_http_status_code = models.PositiveSmallIntegerField('最近 HTTP 状态码', null=True, blank=True)
    last_error = models.TextField('最近错误', blank=True)

    consecutive_breaches = models.PositiveSmallIntegerField('当前连续超阈次数', default=0)
    alert_active = models.BooleanField('告警中', default=False)
    alert_started_at = models.DateTimeField('告警开始时间', null=True, blank=True)
    last_alert_at = models.DateTimeField('最近告警时间', null=True, blank=True)
    last_recovered_at = models.DateTimeField('最近恢复时间', null=True, blank=True)

    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'POP 品质监控'
        verbose_name_plural = 'POP 品质监控'
        ordering = ['pop_point__code', 'id']
        indexes = [
            models.Index(fields=['is_enabled', 'next_probe_at']),
            models.Index(fields=['probe_lock_until']),
            models.Index(fields=['status', 'last_probe_at']),
        ]

    def __str__(self):
        return f'[{self.pop_point.code}] {self.pop_point.name}'

    @property
    def pop_label(self) -> str:
        return f'[{self.pop_point.code}] {self.pop_point.name}'

    @property
    def is_alert_suppressed(self) -> bool:
        """维护中的 POP 继续采样，但不产生新的品质告警。"""
        return self.pop_point.status == 'maintenance'

    @property
    def has_probe_target(self) -> bool:
        return bool(
            (self.enable_icmp and self.pop_point.mgmt_ip)
            or (self.enable_http and self.pop_point.mgmt_url)
        )


class PopQualityProbe(models.Model):
    """一次 POP ICMP/管理 URL 综合探测的不可变采样记录。"""

    class Status(models.TextChoices):
        SUCCESS = 'success', '正常'
        DEGRADED = 'degraded', '品质下降'
        FAILED = 'failed', '失败'
        SKIPPED = 'skipped', '已跳过'
        ERROR = 'error', '探测异常'

    class AlertKind(models.TextChoices):
        NONE = '', '无'
        ALERT = 'alert', '告警'
        RECOVERED = 'recovered', '恢复'

    monitor = models.ForeignKey(
        PopQualityMonitor,
        on_delete=models.CASCADE,
        related_name='probes',
        verbose_name='品质监控',
    )
    status = models.CharField('探测状态', max_length=16, choices=Status.choices)
    icmp_target = models.CharField('ICMP 目标', max_length=64, blank=True)
    icmp_sent = models.PositiveSmallIntegerField('ICMP 发送数', null=True, blank=True)
    icmp_received = models.PositiveSmallIntegerField('ICMP 接收数', null=True, blank=True)
    icmp_packet_loss_pct = models.FloatField('ICMP 丢包率(%)', null=True, blank=True)
    icmp_min_ms = models.FloatField('ICMP 最小时延(ms)', null=True, blank=True)
    icmp_avg_ms = models.FloatField('ICMP 平均时延(ms)', null=True, blank=True)
    icmp_max_ms = models.FloatField('ICMP 最大时延(ms)', null=True, blank=True)
    icmp_success = models.BooleanField('ICMP 可达', null=True, blank=True)

    http_target = models.CharField('HTTP 目标', max_length=512, blank=True)
    http_status_code = models.PositiveSmallIntegerField('HTTP 状态码', null=True, blank=True)
    http_response_ms = models.FloatField('HTTP 响应时间(ms)', null=True, blank=True)
    http_success = models.BooleanField('HTTP 可用', null=True, blank=True)

    error_message = models.TextField('错误信息', blank=True)
    alert_kind = models.CharField(
        '告警状态变化', max_length=16,
        choices=AlertKind.choices, blank=True, default=AlertKind.NONE,
    )
    started_at = models.DateTimeField('探测开始时间', default=timezone.now)
    finished_at = models.DateTimeField('探测结束时间', null=True, blank=True)
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        verbose_name = 'POP 品质采样'
        verbose_name_plural = 'POP 品质采样'
        ordering = ['-started_at', '-id']
        indexes = [
            models.Index(fields=['monitor', 'started_at']),
            models.Index(fields=['status', 'started_at']),
            models.Index(fields=['alert_kind', 'started_at']),
        ]

    def __str__(self):
        return f'{self.monitor} {self.started_at:%Y-%m-%d %H:%M:%S}'


# ═══════════════════════════════════════════════════════════════
# § 9  边缘遥测凭据与采样
# ═══════════════════════════════════════════════════════════════

class PopTelemetryCredential(models.Model):
    """
    单个 POP 节点用于上报边缘链路质量的专用令牌。

    原始令牌永不落库；仅在创建或轮换 API 的响应中短暂返回一次。节点请求通过
    ``X-POP-Telemetry-Token`` 携带原始值，中心仅比较 SHA-256 摘要。
    """

    pop_point = models.OneToOneField(
        'resource_manager.PopPoint',
        on_delete=models.CASCADE,
        related_name='telemetry_credential',
        verbose_name='POP 节点',
    )
    token_hash = models.CharField(
        '令牌 SHA-256 摘要',
        max_length=64,
        unique=True,
        editable=False,
    )
    token_prefix = models.CharField(
        '令牌前缀',
        max_length=16,
        editable=False,
        help_text='仅用于管理员识别令牌，不可用于认证',
    )
    is_active = models.BooleanField('启用', default=True)
    rotated_at = models.DateTimeField('最近轮换时间', null=True, blank=True)
    last_used_at = models.DateTimeField('最近使用时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'POP 遥测凭据'
        verbose_name_plural = 'POP 遥测凭据'
        ordering = ['pop_point__code', 'id']

    def __str__(self):
        return f'[{self.pop_point.code}] telemetry {self.token_prefix}…'

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256((token or '').encode('utf-8')).hexdigest()

    @staticmethod
    def generate_token() -> str:
        # 前缀方便人工区分令牌用途；随机主体提供超过 256 bit 的熵。
        return f'ptt_{secrets.token_urlsafe(32)}'

    def rotate_token(self) -> str:
        """生成新令牌并只保存摘要；调用方负责将原始值仅返回给操作者一次。"""
        token = self.generate_token()
        self.token_hash = self.hash_token(token)
        self.token_prefix = token[:16]
        self.rotated_at = timezone.now()
        self.is_active = True
        return token


class EdgeInterfaceQualitySample(models.Model):
    """由 POP 节点上报的边缘隧道接口链路质量原始样本。"""

    pop = models.ForeignKey(
        'resource_manager.PopPoint',
        on_delete=models.CASCADE,
        related_name='edge_quality_samples',
        verbose_name='上报 POP',
    )
    edge = models.ForeignKey(
        'resource_manager.EdgeAcceleration',
        on_delete=models.CASCADE,
        related_name='quality_samples',
        verbose_name='边缘加速',
    )
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='edge_quality_samples',
        verbose_name='客户',
    )
    interface_name = models.CharField('节点接口名', max_length=64)
    target_address = models.CharField('探测目标', max_length=255)
    source_sample_id = models.CharField('节点样本标识', max_length=128)
    sampled_at = models.DateTimeField('采样时间')
    received_at = models.DateTimeField('接收时间', default=timezone.now)
    latency_ms = models.FloatField('延迟(ms)', null=True, blank=True)
    packet_loss_pct = models.FloatField('丢包率(%)', null=True, blank=True)
    jitter_ms = models.FloatField('抖动(ms)', null=True, blank=True)
    packets_sent = models.PositiveIntegerField('发送包数', default=0)
    packets_recv = models.PositiveIntegerField('接收包数', default=0)
    ok = models.BooleanField('采样成功', default=True)
    raw = models.JSONField(
        '脱敏原始样本',
        default=dict,
        blank=True,
        help_text='仅保存白名单遥测字段，绝不保存节点令牌',
    )

    class Meta:
        verbose_name = '边缘接口品质样本'
        verbose_name_plural = '边缘接口品质样本'
        ordering = ['-sampled_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['pop', 'source_sample_id'],
                name='uniq_edge_quality_pop_source_sample',
            ),
        ]
        indexes = [
            models.Index(fields=['edge', 'sampled_at']),
            models.Index(fields=['customer', 'sampled_at']),
        ]

    def __str__(self):
        return f'{self.interface_name} @ {self.sampled_at:%Y-%m-%d %H:%M:%S}'


class EdgeInterfaceQualityRollup(models.Model):
    """
    边缘接口品质的小时/日聚合。

    原始样本负责近期排障与当前值，汇总记录承担较长时间窗口的门户查询和原始数据保留
    策略，避免客户查询 7 天曲线时扫描大量分钟级样本。
    """

    class BucketKind(models.TextChoices):
        HOUR = 'hour', '小时'
        DAY = 'day', '天'

    pop = models.ForeignKey(
        'resource_manager.PopPoint',
        on_delete=models.CASCADE,
        related_name='edge_quality_rollups',
        verbose_name='上报 POP',
    )
    edge = models.ForeignKey(
        'resource_manager.EdgeAcceleration',
        on_delete=models.CASCADE,
        related_name='quality_rollups',
        verbose_name='边缘加速',
    )
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='edge_quality_rollups',
        verbose_name='客户',
    )
    bucket_kind = models.CharField(
        '聚合粒度',
        max_length=8,
        choices=BucketKind.choices,
    )
    bucket_ts = models.DateTimeField('桶起点')
    sample_count = models.PositiveIntegerField('样本数', default=0)
    ok_samples = models.PositiveIntegerField('成功样本数', default=0)
    latency_avg_ms = models.FloatField('平均延迟(ms)', null=True, blank=True)
    latency_max_ms = models.FloatField('最大延迟(ms)', null=True, blank=True)
    packet_loss_avg_pct = models.FloatField('平均丢包率(%)', null=True, blank=True)
    jitter_avg_ms = models.FloatField('平均抖动(ms)', null=True, blank=True)
    availability_pct = models.FloatField('可用性(%)', null=True, blank=True)
    last_sampled_at = models.DateTimeField('桶内最新采样', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '边缘接口品质汇总'
        verbose_name_plural = '边缘接口品质汇总'
        ordering = ['-bucket_ts', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['pop', 'edge', 'bucket_kind', 'bucket_ts'],
                name='uniq_edge_quality_rollup_bucket',
            ),
        ]
        indexes = [
            models.Index(
                fields=['edge', 'bucket_kind', 'bucket_ts'],
                name='operation_m_edge_id_63da66_idx',
            ),
            models.Index(
                fields=['customer', 'bucket_kind', 'bucket_ts'],
                name='operation_m_custome_d1da12_idx',
            ),
        ]

    def __str__(self):
        return f'{self.edge} {self.bucket_kind} {self.bucket_ts:%Y-%m-%d %H:%M}'


# ═══════════════════════════════════════════════════════════════
# § 10  WebSSH 命令模板
# ═══════════════════════════════════════════════════════════════

class WebSSHCommandTemplate(models.Model):
    """WebSSH 可复用命令模板，支持参数占位符 {key}。"""

    code = models.CharField('编码', max_length=64, unique=True)
    name = models.CharField('名称', max_length=128)
    category = models.CharField('分类', max_length=32, default='系统')
    description = models.TextField('说明', blank=True)
    command = models.TextField('命令模板')
    fields = models.JSONField(
        '参数字段', default=list, blank=True,
        help_text='[{key, label, default, required}]',
    )
    interactive = models.BooleanField(
        '交互命令', default=False,
        help_text='如 passwd，需在终端内继续输入',
    )
    is_system = models.BooleanField('系统内置', default=False)
    is_active = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'WebSSH 命令模板'
        verbose_name_plural = 'WebSSH 命令模板'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name

    def to_payload(self):
        return {
            'id': self.pk,
            'code': self.code,
            'name': self.name,
            'category': self.category,
            'description': self.description,
            'command': self.command,
            'fields': self.fields or [],
            'interactive': self.interactive,
            'is_system': self.is_system,
            'is_active': self.is_active,
            'sort_order': self.sort_order,
        }


# ═══════════════════════════════════════════════════════════════
# § 工具函数
# ═══════════════════════════════════════════════════════════════

def record_monitoring_log(
    *,
    action_type: str,
    status: str,
    message: str,
    zabbix_server=None,
    monitoring_host=None,
    operator=None,
    detail=None,
    duration_ms=None,
):
    """写入监控同步日志（供 services / views 调用）"""
    return MonitoringSyncLog.objects.create(
        zabbix_server=zabbix_server,
        monitoring_host=monitoring_host,
        action_type=action_type,
        status=status,
        message=(message or '')[:512],
        detail=detail or {},
        operator=operator,
        duration_ms=duration_ms,
    )


def build_pop_monitoring_host_defaults(pop_point) -> dict:
    """
    根据 POP 点字段生成 MonitoringHost 初始值（不含 FK）。
    供批量创建 Host 或 API 预览使用。
    """
    return {
        'host_kind': MonitoringHost.HostKind.POP,
        'hostname': f'pop-{pop_point.code}'.lower(),
        'visible_name': f'[{pop_point.code}] {pop_point.name}',
        'monitored_ip': pop_point.mgmt_ip,
        'snmp_community': pop_point.snmp_community or '',
        'enable_icmp': bool(pop_point.mgmt_ip),
        'enable_snmp': bool(pop_point.snmp_community),
    }


def infer_traffic_direction(item_key: str, item_name: str = '') -> str:
    """从 Zabbix Item Key/名称推断流量方向"""
    text = f'{item_key} {item_name}'.lower()
    in_keys = ('net.if.in', 'ifhcinoctets', 'ifinoctets', 'ingress', '入站', '接收')
    out_keys = ('net.if.out', 'ifhcoutoctets', 'ifoutoctets', 'egress', '出站', '发送')
    is_in = any(k in text for k in in_keys)
    is_out = any(k in text for k in out_keys)
    if is_in and is_out:
        return 'both'
    if is_in:
        return 'in'
    if is_out:
        return 'out'
    return 'unknown'


def infer_interface_name(item_key: str, item_name: str = '') -> str:
    """
    从 Item Key 解析接口名。
    如 net.if.in[eth0] → eth0；IF-MIB 名称在 item name 中时常需人工指定。
    """
    import re
    m = re.search(r'\[([^\],]+)', item_key or '')
    if m:
        return m.group(1).strip()
    m2 = re.search(r'(?:interface|ifname|端口)\s*[:：]?\s*(\S+)', item_name or '', re.I)
    if m2:
        return m2.group(1).strip()
    return ''


def suggested_graph_kind(graph_name: str) -> str:
    """根据 Zabbix 图形名称推断分类（供 sync 服务使用）"""
    name = (graph_name or '').lower()
    traffic_keys = ('traffic', '流量', 'bits', 'bandwidth', 'net.if', 'interface')
    system_keys = ('cpu', 'memory', 'mem', 'load', 'disk', 'storage', '处理器', '内存')
    icmp_keys = ('icmp', 'ping', 'availability', '可用', 'loss', 'latency', '延迟')
    billing_keys = ('95', 'percentile', 'billing', '计费', '峰值')
    if any(k in name for k in billing_keys):
        return MonitoringGraph.GraphKind.BILLING
    if any(k in name for k in traffic_keys):
        return MonitoringGraph.GraphKind.TRAFFIC
    if any(k in name for k in system_keys):
        return MonitoringGraph.GraphKind.SYSTEM
    if any(k in name for k in icmp_keys):
        return MonitoringGraph.GraphKind.ICMP
    return MonitoringGraph.GraphKind.CUSTOM
