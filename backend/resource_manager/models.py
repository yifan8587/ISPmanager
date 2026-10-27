"""
resource_manager/models.py
资源管理模块完整数据模型

层次结构：
  地理位置  : Region → Country → City
  分类体系  : ResourceType(枚举) + Carrier(运营商)
  POP 点   : PopPoint（基本/管理/硬件/网络属性）
  网络基础  : NetworkInterface / RouteTable / StaticRoute / PolicyRoute
  IP 管理  : IPBlock / IPAddress
  VPS     : VPS（硬件/管理/客户/资源属性）
  边缘加速 : EdgeAcceleration + GREConfig / WireguardConfig / Socks5Config
  带宽分配 : BandwidthAllocation
  操作日志 : ResourceOperationLog + record_resource_log()
"""

import datetime
import uuid

from django.db import models
from django.contrib.auth.models import User


# ═══════════════════════════════════════════════════════════════
# § 1  地理位置
# ═══════════════════════════════════════════════════════════════

class Region(models.Model):
    """大区"""

    class RegionCode(models.TextChoices):
        APAC = 'apac', '亚洲'
        EUROPE = 'europe', '欧洲'
        AFRICA = 'africa', '非洲'
        AMERICAS = 'americas', '北美洲'
        SOUTH_AMERICA = 'south_america', '南美洲'
        OCEANIA = 'oceania', '大洋洲'

    code = models.CharField('区域代码', max_length=16, choices=RegionCode.choices, unique=True)
    name = models.CharField('区域名称', max_length=64)
    name_en = models.CharField('英文名称', max_length=64, blank=True)

    class Meta:
        verbose_name = '大区'
        verbose_name_plural = '大区列表'
        ordering = ['code']

    def __str__(self):
        return self.name


class Country(models.Model):
    """国家"""
    region = models.ForeignKey(
        Region, on_delete=models.PROTECT,
        verbose_name='所属大区', related_name='countries',
    )
    name = models.CharField('国家名称', max_length=64)
    name_en = models.CharField('英文名称', max_length=64, blank=True, null=True)

    class Meta:
        verbose_name = '国家'
        verbose_name_plural = '国家列表'
        ordering = ['region', 'name']

    def __str__(self):
        return self.name


class City(models.Model):
    """城市"""
    country = models.ForeignKey(
        Country, on_delete=models.PROTECT,
        verbose_name='所属国家', related_name='cities',
    )
    name = models.CharField('城市名称', max_length=64)
    name_en = models.CharField('英文名称', max_length=64, blank=True)
    timezone = models.CharField('时区', max_length=64, blank=True, help_text='如 Asia/Tokyo')
    latitude = models.DecimalField('纬度', max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField('经度', max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        verbose_name = '城市'
        verbose_name_plural = '城市列表'
        ordering = ['country', 'name']

    def __str__(self):
        return f'{self.name} / {self.country.name}'


class AvailabilityZone(models.Model):
    """城市下的可用区，默认可空；POP / 产品通过 POP 引用。"""

    city = models.ForeignKey(
        City, on_delete=models.PROTECT,
        verbose_name='所属城市', related_name='availability_zones',
    )
    name = models.CharField('可用区名称', max_length=64)
    name_en = models.CharField('英文名称', max_length=64, blank=True)
    code = models.CharField(
        '可用区代码', max_length=32, blank=True, default='',
        help_text='可选，如 tokyo-a；同一城市内不重复',
    )
    remark = models.CharField('备注', max_length=256, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '可用区'
        verbose_name_plural = '可用区列表'
        ordering = ['city', 'name']
        constraints = [
            models.UniqueConstraint(fields=['city', 'name'], name='uniq_az_city_name'),
        ]

    def __str__(self):
        return f'{self.name} / {self.city.name}'


# ═══════════════════════════════════════════════════════════════
# § 2  资源类型 & 运营商
# ═══════════════════════════════════════════════════════════════

class ResourceType(models.TextChoices):
    """历史枚举（已拆分为 NetworkType / AsnNature / IPAttribute），保留以兼容旧引用。"""
    NATIVE = 'native', '原生'
    HOME_BROADBAND = 'home_broadband', '家宽'
    BROADCAST = 'broadcast', '广播'
    ISP = 'isp', 'ISP'
    DUAL_ISP = 'dual_isp', '双 ISP'
    IDC = 'idc', 'IDC'


class NetworkType(models.TextChoices):
    """POP 点网络类型"""
    HOME_BROADBAND = 'home_broadband', '家宽'
    IDC = 'idc', 'IDC'


class AsnNature(models.TextChoices):
    """ASN / 企业性质（IP 地址块与产品展示均用此枚举）"""
    ISP = 'isp', '单ISP'
    DUAL_ISP = 'dual_isp', '双ISP'
    NON_ISP = 'non_isp', '非ISP'


class IPAttribute(models.TextChoices):
    """IP 属性"""
    NATIVE = 'native', '原生'
    BROADCAST = 'broadcast', '广播'


class Carrier(models.Model):
    """运营商 / ISP"""
    name = models.CharField('运营商名称', max_length=128)
    code = models.CharField('运营商代码', max_length=32, unique=True, help_text='如 NTT, PCCW, HGC', null=True, blank=True)
    regions = models.ManyToManyField(
        Region, blank=True,
        verbose_name='覆盖大区', related_name='carriers',
    )
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '运营商'
        verbose_name_plural = '运营商列表'
        ordering = ['code']

    def __str__(self):
        return f'[{self.code}] {self.name}'


class NetworkTypeOption(models.Model):
    """网络类型目录（IDC / 家宽等，可动态增减）。"""

    code = models.SlugField('代码', max_length=32, unique=True, help_text='如 idc、home_broadband')
    name = models.CharField('名称', max_length=64)
    sort_order = models.IntegerField('排序', default=0)
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '网络类型'
        verbose_name_plural = '网络类型列表'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.name


# ═══════════════════════════════════════════════════════════════
# § 3  POP 点
# ═══════════════════════════════════════════════════════════════

class PopPoint(models.Model):
    """POP 点（接入节点）"""

    class PopStatus(models.TextChoices):
        ONLINE = 'online', '在线'
        OFFLINE = 'offline', '离线'
        MAINTENANCE = 'maintenance', '维护中'
        DECOMMISSIONED = 'decommissioned', '已下线'

    class PopType(models.TextChoices):
        POP = 'pop', 'POP'
        PE = 'pe', 'PE'
        VDC = 'vdc', 'VDC'
        OTHER = 'other', '其他'

    class OSType(models.TextChoices):
        DEBIAN = 'debian', 'Debian'
        UBUNTU = 'ubuntu', 'Ubuntu'
        CENTOS = 'centos', 'CentOS'
        RHEL = 'rhel', 'RHEL'
        WINDOWS = 'windows', 'Windows Server'
        OTHER = 'other', '其他'

    class ConfigMode(models.TextChoices):
        AUTO = 'auto', '自动配置'
        MANUAL = 'manual', '手动配置'

    class IPDeliveryMode(models.TextChoices):
        PUBLIC = 'public', '公网直连'
        NAT = 'nat', 'NAT 交付'

    # ── 基本属性 ──────────────────────────────────────────────
    name = models.CharField('节点名称', max_length=128)
    code = models.CharField('节点代码', max_length=32, unique=True, editable=False, null=True, blank=True)
    pop_type = models.CharField(
        '节点类型', max_length=8,
        choices=PopType.choices, default=PopType.POP,
    )
    status = models.CharField(
        '状态', max_length=20,
        choices=PopStatus.choices, default=PopStatus.ONLINE,
    )
    city = models.ForeignKey(
        City, on_delete=models.PROTECT,
        verbose_name='所在城市', related_name='pop_points',
    )
    availability_zone = models.ForeignKey(
        'AvailabilityZone', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='可用区', related_name='pop_points',
        help_text='可选，须属于所在城市',
    )
    network_type = models.CharField(
        '网络类型', max_length=20,
        choices=NetworkType.choices, default=NetworkType.IDC,
    )
    asn_nature = models.CharField(
        'ASN/企业性质', max_length=16,
        choices=AsnNature.choices, default=AsnNature.ISP,
        help_text='单ISP / 双ISP / 非ISP',
    )
    ip_attribute = models.CharField(
        'IP 属性', max_length=20,
        choices=IPAttribute.choices, default=IPAttribute.NATIVE,
        help_text='原生 / 广播，默认原生',
    )
    carriers = models.ManyToManyField(
        Carrier, blank=True,
        verbose_name='运营商', related_name='pop_points',
    )

    # ── 供应商 ────────────────────────────────────────────────
    # 使用字符串引用避免与 supplier_manager 形成循环导入
    supplier = models.ForeignKey(
        'supplier_manager.Supplier', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='供应商', related_name='pop_points',
    )
    supplier_external_id = models.CharField(
        '供应商侧POP标识', max_length=128, blank=True,
        help_text='供应商系统中对应的 POP 标识，便于后续对接',
    )

    # ── 配置模式 ──────────────────────────────────────────────
    config_mode = models.CharField(
        '配置模式', max_length=8,
        choices=ConfigMode.choices, default=ConfigMode.MANUAL,
        help_text='自动：工单提交后资源足够则自动下发开通；手动：需人工处理',
    )
    allow_oversell = models.BooleanField(
        '允许超售',
        default=False,
        help_text='关闭时下单预占必须落在剩余有效容量内；打开后仍记账本，但允许预占超过剩余容量',
    )
    ip_delivery_mode = models.CharField(
        'IP 交付方式',
        max_length=16,
        choices=IPDeliveryMode.choices,
        default=IPDeliveryMode.PUBLIC,
        db_index=True,
        help_text='公网直连：业务 IP 可直接路由到客户；NAT 交付：仅能经节点 NAT 访问，产品与门户需提示',
    )

    # ── 管理属性 ──────────────────────────────────────────────
    mgmt_ip = models.GenericIPAddressField('管理 IP', null=True, blank=True)
    ssh_port = models.PositiveIntegerField('SSH 端口', default=22)
    mgmt_username = models.CharField('管理用户名', max_length=64, blank=True)
    mgmt_password = models.CharField('管理密码', max_length=256, blank=True)
    mgmt_token = models.CharField('API Token', max_length=512, blank=True)
    mgmt_url = models.URLField('管理 URL', blank=True)
    # 节点上 pemanager 面板的 API 基址 / 登录凭据（用于部署后数据同步）
    api_base_url = models.CharField('节点 API 基址', max_length=256, blank=True,
                                    help_text='如 http://1.2.3.4 或 https://pe.example.com')
    api_username = models.CharField('节点 API 用户名', max_length=64, blank=True)
    api_password = models.CharField('节点 API 密码', max_length=256, blank=True)
    api_verify_ssl = models.BooleanField(
        '校验节点 HTTPS 证书',
        default=False,
        help_text='节点面板多为自签证书，且常按 IP 访问。关闭时仍走 HTTPS，只是不校验证书。已部署正式域名证书时可打开。',
    )
    snmp_community = models.CharField('SNMP 团体名', max_length=128, blank=True)
    snmp_version = models.CharField(
        'SNMP 版本', max_length=4, blank=True,
        choices=[('v1', 'v1'), ('v2c', 'v2c'), ('v3', 'v3')],
        default='v2c',
    )

    # ── 硬件属性 ──────────────────────────────────────────────
    cpu_model = models.CharField('CPU 型号', max_length=128, blank=True, null=True)
    cpu_cores = models.PositiveSmallIntegerField('CPU 核数', null=True, blank=True)
    ram_gb = models.PositiveIntegerField('内存(GB)', null=True, blank=True)
    disk_gb = models.PositiveIntegerField('硬盘总容量(GB)', null=True, blank=True,
                                          help_text='用于产品规划/部署时测算可开通产品数量')
    disk_info = models.CharField('硬盘信息', max_length=256, blank=True, help_text='如 2×480GB SSD')
    os_type = models.CharField(
        '操作系统', max_length=16,
        choices=OSType.choices, blank=True,
    )
    os_version = models.CharField('系统版本', max_length=64, blank=True, null=True)


    # ── 网络资源属性 ──────────────────────────────────────────
    total_bandwidth_mbps = models.PositiveIntegerField('总带宽(Mbps)', default=0)
    used_bandwidth_mbps = models.PositiveIntegerField('已用带宽(Mbps)', default=0)

    # ── 部署 / 同步状态 ───────────────────────────────────────
    class DeployStatus(models.TextChoices):
        UNDEPLOYED = 'undeployed', '未部署'
        DEPLOYING = 'deploying', '部署中'
        DEPLOYED = 'deployed', '已部署'
        FAILED = 'failed', '部署失败'

    deploy_status = models.CharField(
        '部署状态', max_length=16,
        choices=DeployStatus.choices, default=DeployStatus.UNDEPLOYED,
    )
    agent_version = models.CharField('节点软件版本', max_length=64, blank=True)
    last_deploy_at = models.DateTimeField('最近部署时间', null=True, blank=True)
    last_sync_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    # ── 配置下发待处理标记（前端增删改资源后置位，成功下发后清除） ──
    config_dirty = models.BooleanField('有未下发配置变更', default=False)
    config_dirty_at = models.DateTimeField('最近配置变更时间', null=True, blank=True)
    last_push_at = models.DateTimeField('最近配置下发时间', null=True, blank=True)

    # ── 其他 ──────────────────────────────────────────────────
    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_pops',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'POP 点'
        verbose_name_plural = 'POP 点列表'
        ordering = ['city__country__region', 'city', 'name']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['pop_type']),
            models.Index(fields=['network_type']),
            models.Index(fields=['asn_nature']),
            models.Index(fields=['city']),
        ]

    def __str__(self):
        return f'[{self.code}] {self.name} ({self.city})'

    def clean(self):
        super().clean()
        if self.availability_zone_id and self.city_id:
            if self.availability_zone.city_id != self.city_id:
                from django.core.exceptions import ValidationError
                raise ValidationError({'availability_zone': '可用区必须属于所选城市'})

    def save(self, *args, **kwargs):
        if not self.code:
            prefix = 'POP'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = PopPoint.objects.filter(
                code__startswith=f'{prefix}{date_str}'
            ).order_by('-code').first()
            seq = int(last.code[-4:]) + 1 if last else 1
            self.code = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def allocated_bandwidth_mbps(self) -> int:
        """自动汇总该 POP 下所有 VPS + 边缘加速的带宽分配（Mbps）"""
        from django.db.models import Sum
        vps_bw  = self.vps_list.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        edge_bw = self.edge_accelerations.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        return vps_bw + edge_bw

    @property
    def available_bandwidth_mbps(self) -> int:
        """剩余可用带宽 = 总带宽 - 已分配带宽"""
        return max(0, self.total_bandwidth_mbps - self.allocated_bandwidth_mbps)

    # ── 硬件资源已分配汇总（用于产品规划/库存测算）──────────────
    @property
    def allocated_vcpu(self) -> int:
        """该 POP 下全部 VPS 已分配的 vCPU 之和"""
        from django.db.models import Sum
        return self.vps_list.aggregate(t=Sum('vcpu'))['t'] or 0

    @property
    def allocated_ram_gb(self) -> int:
        """该 POP 下全部 VPS 已分配内存之和（GB，向上取整）"""
        from django.db.models import Sum
        ram_mb = self.vps_list.aggregate(t=Sum('ram_mb'))['t'] or 0
        return -(-ram_mb // 1024) if ram_mb else 0

    @property
    def allocated_disk_gb(self) -> int:
        """该 POP 下全部 VPS 已分配硬盘之和（GB）"""
        from django.db.models import Sum
        return self.vps_list.aggregate(t=Sum('disk_gb'))['t'] or 0


# ═══════════════════════════════════════════════════════════════
# § 4  网络基础设施
# ═══════════════════════════════════════════════════════════════

class NetworkInterface(models.Model):
    """网络接口（挂载在 POP 点或 VPS 上）"""

    class InterfaceType(models.TextChoices):
        PHYSICAL = 'physical', '物理接口'
        VLAN = 'vlan', 'VLAN'
        BOND = 'bond', 'Bond/LACP'
        BRIDGE = 'bridge', 'Bridge'
        LOOPBACK = 'loopback', 'Loopback'
        TUNNEL = 'tunnel', 'Tunnel'
        DUMMY = 'dummy', 'Dummy'

    class InterfaceStatus(models.TextChoices):
        UP = 'up', 'UP'
        DOWN = 'down', 'DOWN'
        UNKNOWN = 'unknown', '未知'

    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='所属 POP 点', related_name='interfaces',
    )
    # VPS 接口引用在 VPS 模型中通过 related_name 实现
    ifname = models.CharField('接口名称', max_length=32, help_text='如 eth0, bond0, vlan100', null=True, blank=True)
    interface_type = models.CharField(
        '接口类型', max_length=16,
        choices=InterfaceType.choices, default=InterfaceType.PHYSICAL,
    )
    mac_address = models.CharField('MAC 地址', max_length=17, blank=True, null=True)
    mtu = models.PositiveIntegerField('MTU', default=1500, null=True, blank=True)
    speed_mbps = models.PositiveIntegerField('接口速率(Mbps)', null=True, blank=True)
    status = models.CharField(
        '状态', max_length=8,
        choices=InterfaceStatus.choices, default=InterfaceStatus.UP,
    )
    is_management = models.BooleanField('是否管理口', default=False, null=True, blank=True)
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    vlan_id = models.PositiveSmallIntegerField('VLAN ID', default=0, null=True, blank=True)
    parent_interface = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='父接口', related_name='sub_interfaces',
    )
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True,
                                    help_text='由节点 pemanager 同步生成，可被同步对账自动清理')
    # netplan 下发意图（kind + spec），保存后可通过 push_to_node 写入节点 apply-config
    netplan_config = models.JSONField('Netplan 配置', default=dict, blank=True, null=True,
                                      help_text='{"kind":"ether","spec":{...}}，用于下发到节点')
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True, null=True, blank=True)

    class Meta:
        verbose_name = '网络接口'
        verbose_name_plural = '网络接口列表'
        ordering = ['pop_point', 'ifname']

    def __str__(self):
        owner = self.pop_point or '—'
        return f'{owner} / {self.ifname}'


class RouteTable(models.Model):
    """路由表"""

    class RouteTableOwner(models.TextChoices):
        POP = 'pop', 'POP 点'
        VPS = 'vps', 'VPS'

    owner_type = models.CharField(
        '归属类型', max_length=8,
        choices=RouteTableOwner.choices, default=RouteTableOwner.POP,
    )
    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='所属 POP 点', related_name='route_tables',
    )
    # vps FK 在 VPS 模型定义后通过字符串引用
    vps = models.ForeignKey(
        'VPS', on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='所属 VPS', related_name='route_tables',
    )
    table_id = models.PositiveIntegerField('路由表 ID', help_text='Linux 路由表 ID')
    name = models.CharField('路由表名称', max_length=64, null=True, blank=True)
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = '路由表'
        verbose_name_plural = '路由表列表'
        ordering = ['table_id']

    def __str__(self):
        owner = self.pop_point or self.vps or '—'
        return f'{owner} / table {self.table_id} ({self.name})'


class StaticRoute(models.Model):
    """静态路由条目"""
    route_table = models.ForeignKey(
        RouteTable, on_delete=models.CASCADE,
        verbose_name='所属路由表', related_name='static_routes',
    )
    destination = models.CharField('目标网络', max_length=50, help_text='CIDR 格式，如 0.0.0.0/0')
    gateway = models.GenericIPAddressField('网关', null=True, blank=True)
    interface = models.ForeignKey(
        NetworkInterface, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='出接口', related_name='static_routes',
    )
    metric = models.PositiveIntegerField('Metric', default=0, null=True, blank=True)
    is_active = models.BooleanField('已生效', default=True, null=True, blank=True)
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = '静态路由'
        verbose_name_plural = '静态路由列表'
        ordering = ['metric', 'destination']

    def __str__(self):
        return f'{self.destination} via {self.gateway or self.interface}'


class PolicyRoute(models.Model):
    """策略路由规则（ip rule）"""
    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='所属 POP 点', related_name='policy_routes',
    )
    vps = models.ForeignKey(
        'VPS', on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='所属 VPS', related_name='policy_routes',
    )
    priority = models.PositiveIntegerField('优先级', help_text='数字越小优先级越高')
    src_prefix = models.CharField('源地址前缀', max_length=50, blank=True, help_text='CIDR 或空（any）')
    dst_prefix = models.CharField('目标地址前缀', max_length=50, blank=True, null=True)
    fwmark = models.CharField('FWMark', max_length=32, blank=True, null=True)
    lookup_table = models.ForeignKey(
        RouteTable, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='查找路由表',
    )
    action = models.CharField(
        '动作', max_length=16,
        choices=[('lookup', 'lookup'), ('blackhole', 'blackhole'), ('unreachable', 'unreachable')],
        default='lookup',
    )
    is_active = models.BooleanField('已生效', default=True, null=True, blank=True)
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = '策略路由'
        verbose_name_plural = '策略路由列表'
        ordering = ['priority']

    def __str__(self):
        owner = self.pop_point or self.vps or '—'
        src = self.src_prefix or 'any'
        return f'{owner} prio {self.priority}: from {src} lookup {self.lookup_table}'


# ═══════════════════════════════════════════════════════════════
# § 5  IP 地址管理
# ═══════════════════════════════════════════════════════════════

class IPBlock(models.Model):
    """IP 地址块（挂载到 POP 点）"""

    class IPVersion(models.TextChoices):
        V4 = 'v4', 'IPv4'
        V6 = 'v6', 'IPv6'

    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        verbose_name='所属 POP 点', related_name='ip_blocks',
    )
    cidr = models.CharField('CIDR', max_length=50, help_text='如 103.x.x.x/24')
    ip_version = models.CharField(
        'IP 版本', max_length=2,
        choices=IPVersion.choices, default=IPVersion.V4,
    )
    carrier = models.ForeignKey(
        Carrier, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='归属运营商',
    )
    ip_attribute = models.CharField(
        'IP 属性', max_length=20,
        choices=IPAttribute.choices, blank=True,
        help_text='原生 / 广播',
    )
    network_type = models.CharField(
        '网络类型', max_length=32, default='idc', db_index=True,
        help_text='对应网络类型目录代码，如 idc / home_broadband',
    )
    asn_nature = models.CharField(
        'ASN/企业性质', max_length=16,
        choices=AsnNature.choices, default=AsnNature.ISP, db_index=True,
        help_text='单ISP / 双ISP / 非ISP，默认单ISP',
    )
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    gateway = models.GenericIPAddressField(
        '本段网关', null=True, blank=True,
        help_text='该 CIDR 默认网关，供后续 VPS 网卡配置使用；单 IP 可覆盖',
    )
    is_active = models.BooleanField('启用', default=True, null=True, blank=True)
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = 'IP 地址块'
        verbose_name_plural = 'IP 地址块列表'
        ordering = ['pop_point', 'cidr']

    def __str__(self):
        return f'{self.cidr} ({self.pop_point.name})'

    @property
    def prefix_len(self) -> int | None:
        try:
            import ipaddress
            return ipaddress.ip_network(self.cidr, strict=False).prefixlen
        except (ValueError, TypeError):
            return None

    @property
    def netmask(self) -> str:
        from .ip_net import netmask_from_prefix
        plen = self.prefix_len
        return netmask_from_prefix(plen, self.ip_version) if plen is not None else ''


class IPAddress(models.Model):
    """单个 IP 地址条目"""

    class IPStatus(models.TextChoices):
        AVAILABLE = 'available', '可用'
        ASSIGNED = 'assigned', '已分配'
        RESERVED = 'reserved', '预留'
        BLOCKED = 'blocked', '封禁'
        SYSTEM = 'system', '系统保留'

    ip_block = models.ForeignKey(
        IPBlock, on_delete=models.CASCADE,
        verbose_name='所属地址块', related_name='ip_addresses',
    )
    address = models.GenericIPAddressField('IP 地址', null=True, blank=True)
    prefix_len = models.PositiveSmallIntegerField('前缀长度', default=32, null=True, blank=True)
    gateway = models.GenericIPAddressField(
        '网关', null=True, blank=True,
        help_text='留空则使用所属地址块的本段网关',
    )
    status = models.CharField(
        '状态', max_length=16,
        choices=IPStatus.choices, default=IPStatus.AVAILABLE,
    )
    # 分配目标（VPS 或边缘加速）
    assigned_vps = models.ForeignKey(
        'VPS', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='分配 VPS', related_name='ip_addresses',
    )
    assigned_edge = models.ForeignKey(
        'EdgeAcceleration', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='分配边缘加速', related_name='ip_addresses',
    )
    is_primary = models.BooleanField('是否主 IP', default=False, null=True, blank=True)
    # ── 计费关联产品（独立 IP 产品）──────────────────────────
    product = models.ForeignKey(
        'product_manager.Product',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联产品', related_name='billed_ips',
    )
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    subnet_label = models.CharField(
        '网段标签', max_length=128, blank=True, default='',
        help_text='与节点 resourcemanage.subnet_label 对齐',
    )
    interface_code = models.CharField(
        '接口标识', max_length=128, blank=True, default='',
        help_text='节点 interface_code，如 wg-edge-xxx / vm-3-net0',
    )
    node_route_ref = models.CharField(
        '节点路由引用', max_length=64, blank=True, default='',
        help_text='边缘 IP 联动下发时在节点创建的 desired-route id',
    )
    assigned_at = models.DateTimeField('分配时间', null=True, blank=True)
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = 'IP 地址'
        verbose_name_plural = 'IP 地址列表'
        ordering = ['ip_block', 'address']
        unique_together = [('ip_block', 'address')]
        indexes = [
            models.Index(fields=['address']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.address}/{self.prefix_len} [{self.get_status_display()}]'

    @property
    def effective_gateway(self) -> str:
        if self.gateway:
            return str(self.gateway)
        block_gw = getattr(self.ip_block, 'gateway', None)
        return str(block_gw) if block_gw else ''

    @property
    def effective_prefix_len(self) -> int:
        if self.prefix_len:
            return int(self.prefix_len)
        block_plen = getattr(self.ip_block, 'prefix_len', None)
        return int(block_plen) if block_plen else 32

    @property
    def netmask(self) -> str:
        from .ip_net import netmask_from_prefix
        version = 'v6' if (self.address and ':' in str(self.address)) else 'v4'
        if getattr(self.ip_block, 'ip_version', '') == 'v6':
            version = 'v6'
        return netmask_from_prefix(self.effective_prefix_len, version)


class TunnelIPPool(models.Model):
    """隧道 IP 地址段（点对点 p2p 池）

    对齐 vpopmanager.resourcemanage.TunnelIPPool：用于「边缘加速隧道接口」
    （GRE / VXLAN / WireGuard）的点对点 IP 自动分配。在父网段（如 10.21.0.0/24）内
    按 point_prefixlen（默认 /30）依次切块，每块两个可用地址（本端/对端），下一块步进 4。

    该模型为 ISPmanager 中央侧的隧道地址段登记：
      - 可在中央新建后「下发到节点」（push_to_node）在节点侧创建同名池；
      - 也可由节点同步回填（is_synced=True，source_ref 记录节点池 id）。
    p2p 块占用情况基于本 POP 下 IPAddress 的状态实时计算（见 tunnel_ip 服务）。
    """

    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        verbose_name='所属 POP 点', related_name='tunnel_ip_pools',
    )
    name = models.CharField('名称', max_length=128)
    cidr = models.CharField(
        '地址段 CIDR', max_length=64,
        help_text='父网段，如 10.21.0.0/24；将在其内按 p2p 前缀切块分配',
    )
    point_prefixlen = models.PositiveSmallIntegerField(
        'p2p 子网前缀', default=30,
        help_text='点对点子网掩码长度，默认 30（每块 .1/.2、下块 .5/.6，步进 4）',
    )
    customer = models.ForeignKey(
        'customer_manager.Customer', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='归属客户', related_name='tunnel_ip_pools',
        help_text='留空表示公共池；指定后建议仅分配给该客户的隧道',
    )
    is_active = models.BooleanField('启用', default=True)
    remark = models.CharField('备注', max_length=512, blank=True, default='')
    # 与节点对接
    is_synced = models.BooleanField('节点同步导入', default=False)
    source_ref = models.CharField(
        '节点池标识', max_length=64, blank=True, default='',
        help_text='节点 resourcemanage 中对应的 TunnelIPPool id',
    )
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '隧道 IP 地址段'
        verbose_name_plural = '隧道 IP 地址段列表'
        ordering = ['pop_point', 'name']
        unique_together = [('pop_point', 'name')]

    def __str__(self):
        return f'{self.name} ({self.cidr})'

    # ── 校验 ────────────────────────────────────────────────
    def clean(self):
        import ipaddress
        from django.core.exceptions import ValidationError
        super().clean()
        try:
            net = ipaddress.ip_network(self.cidr, strict=False)
        except ValueError as exc:
            raise ValidationError({'cidr': f'不是合法的 CIDR：{exc}'}) from exc
        # 归一化为网络地址书写（10.21.0.5/24 → 10.21.0.0/24）
        self.cidr = str(net)
        max_prefix = 32 if net.version == 4 else 128
        if self.point_prefixlen <= net.prefixlen:
            raise ValidationError(
                {'point_prefixlen': f'p2p 前缀（/{self.point_prefixlen}）必须比地址段前缀（/{net.prefixlen}）更长'}
            )
        if self.point_prefixlen > max_prefix:
            raise ValidationError({'point_prefixlen': f'p2p 前缀不能超过 /{max_prefix}'})
        if net.version == 4 and self.point_prefixlen not in (30, 31):
            raise ValidationError({'point_prefixlen': 'IPv4 点对点前缀通常为 /30（或 /31）'})

    # ── 计算 ────────────────────────────────────────────────
    def network(self):
        import ipaddress
        return ipaddress.ip_network(self.cidr, strict=False)

    def total_blocks(self) -> int:
        try:
            net = self.network()
        except ValueError:
            return 0
        return max(0, 2 ** (self.point_prefixlen - net.prefixlen))


# ═══════════════════════════════════════════════════════════════
# § 6  VPS
# ═══════════════════════════════════════════════════════════════

class VPS(models.Model):
    """虚拟专用服务器"""

    class VPSStatus(models.TextChoices):
        RUNNING = 'running', '运行中'
        STOPPED = 'stopped', '已停止'
        SUSPENDED = 'suspended', '已暂停'
        CREATING = 'creating', '创建中'
        DELETING = 'deleting', '删除中'
        ERROR = 'error', '故障'

    class OSType(models.TextChoices):
        DEBIAN = 'debian', 'Debian'
        UBUNTU = 'ubuntu', 'Ubuntu'
        CENTOS = 'centos', 'CentOS'
        RHEL = 'rhel', 'RHEL'
        WINDOWS = 'windows', 'Windows Server'
        OTHER = 'other', '其他'

    # ── 基本属性 ──────────────────────────────────────────────
    vps_no = models.CharField('VPS ID', max_length=32, unique=True, editable=False, null=True, blank=True)
    name = models.CharField('VPS 名称', max_length=128)
    status = models.CharField(
        '状态', max_length=16,
        choices=VPSStatus.choices, default=VPSStatus.CREATING,
    )
    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.PROTECT,
        verbose_name='所属 POP 点', related_name='vps_list',
    )

    # ── 硬件属性 ──────────────────────────────────────────────
    vcpu = models.PositiveSmallIntegerField('vCPU 核数', default=1)
    ram_mb = models.PositiveIntegerField('内存(MB)', default=512)
    disk_gb = models.PositiveIntegerField('硬盘(GB)', default=20)
    nic_count = models.PositiveSmallIntegerField('网卡数量', default=1)

    # ── 管理属性 ──────────────────────────────────────────────
    mgmt_ip = models.GenericIPAddressField('管理 IP', null=True, blank=True)
    mgmt_username = models.CharField('管理用户名', max_length=64, blank=True, default='root')
    mgmt_password = models.CharField('管理密码', max_length=256, blank=True)
    mgmt_port = models.PositiveSmallIntegerField('SSH 端口', default=22)
    os_type = models.CharField(
        '操作系统', max_length=16,
        choices=OSType.choices, blank=True,
    )
    os_version = models.CharField('系统版本', max_length=64, blank=True)
    kernel_version = models.CharField('内核版本', max_length=64, blank=True)

    # ── 网络资源属性 ──────────────────────────────────────────
    bandwidth_mbps = models.PositiveIntegerField('带宽限制(Mbps)', default=100)

    # ── 客户属性 ──────────────────────────────────────────────
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联客户', related_name='vps_list',
    )
    contract = models.ForeignKey(
        'customer_manager.Contract',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联合同', related_name='vps_list',
    )
    # ── 计费关联产品（基础规格定价 + 超出部分阶梯计费）────────────
    product = models.ForeignKey(
        'product_manager.Product',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联产品', related_name='billed_vps',
        help_text='关联产品后按产品基础规格定价，超出部分按阶梯升级计费',
    )

    # ── 生命周期 ──────────────────────────────────────────────
    start_time = models.DateTimeField('开通时间', null=True, blank=True)
    expire_time = models.DateTimeField('到期时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    # ── 节点同步（来自 vpopmanager PVE 虚机） ──────────────────
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True,
                                    help_text='由节点 PVE 虚机同步生成，可被同步对账自动清理')
    source_node = models.CharField('来源 PVE 节点', max_length=128, blank=True)
    source_vmid = models.PositiveIntegerField('来源 VMID', null=True, blank=True)
    source_ref = models.CharField('节点 VM 主键', max_length=64, blank=True,
                                  help_text='节点 pvemanager 的 VM 记录主键，用于生命周期调用')
    source_host_id = models.CharField('节点 PVE 主机 ID', max_length=64, blank=True)
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_vps',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'VPS'
        verbose_name_plural = 'VPS 列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['customer']),
            models.Index(fields=['pop_point']),
        ]

    def __str__(self):
        return f'[{self.vps_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.vps_no:
            prefix = 'VPS'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = VPS.objects.filter(
                vps_no__startswith=f'{prefix}{date_str}'
            ).order_by('-vps_no').first()
            seq = int(last.vps_no[-4:]) + 1 if last else 1
            self.vps_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


class VNCConsoleGrant(models.Model):
    """一次控制台票据。浏览器只拿这个短编号，真正的 PVE 票据留在管理端。"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vps = models.ForeignKey(
        VPS, on_delete=models.CASCADE,
        verbose_name='VPS', related_name='vnc_grants',
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE,
        verbose_name='申请用户', related_name='vnc_grants',
    )
    ticket = models.TextField('PVE 票据')
    password = models.TextField('VNC 密码', blank=True)
    port = models.PositiveIntegerField('VNC 端口')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = 'VNC 控制台票据'
        verbose_name_plural = 'VNC 控制台票据'
        indexes = [
            models.Index(fields=['vps', 'user', 'created_at']),
        ]


class VPSNetworkInterface(models.Model):
    """VPS 网络接口"""
    vps = models.ForeignKey(
        VPS, on_delete=models.CASCADE,
        verbose_name='所属 VPS', related_name='vps_interfaces',
    )
    ifname = models.CharField('接口名称', max_length=32)
    mac_address = models.CharField('MAC 地址', max_length=17, blank=True, null=True)
    mtu = models.PositiveIntegerField('MTU', default=1500, null=True, blank=True)
    is_primary = models.BooleanField('是否主接口', default=False, null=True, blank=True)
    description = models.CharField('描述', max_length=256, blank=True, null=True)
    bandwidth_mbps = models.PositiveIntegerField('带宽限制(Mbps)', default=100, null=True, blank=True)

    class Meta:
        verbose_name = 'VPS 网络接口'
        verbose_name_plural = 'VPS 网络接口'
        ordering = ['-is_primary', 'ifname']

    def __str__(self):
        return f'{self.vps.vps_no} / {self.ifname}'


# ═══════════════════════════════════════════════════════════════
# § 7  边缘加速
# ═══════════════════════════════════════════════════════════════

class EdgeAcceleration(models.Model):
    """边缘加速节点"""

    class Protocol(models.TextChoices):
        GRE = 'gre', 'GRE'
        VXLAN = 'vxlan', 'VXLAN'
        WIREGUARD = 'wireguard', 'WireGuard'
        SOCKS5 = 'socks5', 'Socks5'

    class EdgeStatus(models.TextChoices):
        ACTIVE = 'active', '运行中'
        STOPPED = 'stopped', '已停止'
        PENDING = 'pending', '待配置'
        ERROR = 'error', '故障'

    # ── 基本 ──────────────────────────────────────────────────
    edge_no = models.CharField('加速编号', max_length=32, unique=True, editable=False)
    name = models.CharField('加速节点名称', max_length=128)
    protocol = models.CharField(
        '隧道协议', max_length=16,
        choices=Protocol.choices,
    )
    status = models.CharField(
        '状态', max_length=16,
        choices=EdgeStatus.choices, default=EdgeStatus.PENDING,
    )
    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.PROTECT,
        verbose_name='所属 POP 点', related_name='edge_accelerations',
    )

    # ── 网络资源 ──────────────────────────────────────────────
    bandwidth_mbps = models.PositiveIntegerField('带宽限制(Mbps)', default=100)

    # ── 路由 ──────────────────────────────────────────────────
    route_table = models.ForeignKey(
        RouteTable, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联路由表',
    )

    # ── 客户属性 ──────────────────────────────────────────────
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联客户', related_name='edge_accelerations',
    )
    contract = models.ForeignKey(
        'customer_manager.Contract',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联合同', related_name='edge_accelerations',
    )
    # ── 计费关联产品 ──────────────────────────────────────────
    product = models.ForeignKey(
        'product_manager.Product',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联产品', related_name='billed_edges',
        help_text='关联产品后按产品基础规格定价，超出部分按阶梯升级计费',
    )

    # ── 生命周期 ──────────────────────────────────────────────
    start_time = models.DateTimeField('开通时间', null=True, blank=True)
    expire_time = models.DateTimeField('到期时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    # ── 节点下发状态（GRE/WireGuard 隧道下发到 POP/PE 节点） ────
    # 注意：边缘加速由 ISPmanager 创建并「下发」到节点，语义为「已下发」，
    # 与 VPS/IP 等「从节点同步导入」的 is_synced 语义不同。node_pushed 为规范字段，
    # is_synced 保留为镜像别名以兼容历史前端/过滤器。
    node_pushed = models.BooleanField('节点已下发', default=False, null=True, blank=True,
                                      help_text='隧道配置是否已成功下发到节点（规范字段）')
    is_synced = models.BooleanField('节点已下发(别名)', default=False, null=True, blank=True,
                                    help_text='node_pushed 的兼容别名')
    node_ifname = models.CharField('节点隧道接口名', max_length=64, blank=True,
                                   help_text='下发到节点后对应的 desired-tunnel 接口名')
    last_synced_at = models.DateTimeField('最近下发时间', null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_edges',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '边缘加速'
        verbose_name_plural = '边缘加速列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['protocol']),
            models.Index(fields=['status']),
            models.Index(fields=['customer']),
        ]

    def __str__(self):
        return f'[{self.edge_no}] {self.name} ({self.get_protocol_display()})'

    def save(self, *args, **kwargs):
        if not self.edge_no:
            prefix = 'EDGE'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = EdgeAcceleration.objects.filter(
                edge_no__startswith=f'{prefix}{date_str}'
            ).order_by('-edge_no').first()
            seq = int(last.edge_no[-4:]) + 1 if last else 1
            self.edge_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


class GREConfig(models.Model):
    """GRE 隧道配置"""
    edge = models.OneToOneField(
        EdgeAcceleration, on_delete=models.CASCADE,
        verbose_name='边缘节点', related_name='gre_config',
        limit_choices_to={'protocol': EdgeAcceleration.Protocol.GRE},
    )
    local_ip = models.GenericIPAddressField('本端 IP', help_text='我司对接地址，默认取 POP 管理 IP')
    remote_ip = models.GenericIPAddressField(
        '远端 IP', null=True, blank=True,
        help_text='客户侧公网 IP；开通时预分配可留空，由客户在门户补全',
    )
    tunnel_ip = models.CharField('隧道 IP/CIDR', max_length=50, blank=True, help_text='如 10.10.0.1/30')
    key = models.CharField('GRE Key', max_length=32, blank=True, null=True)
    ttl = models.PositiveSmallIntegerField('TTL', null=True, blank=True,
                                           help_text='可选；留空则不下发 ttl（部分节点 netplan 不支持该键）')
    mtu = models.PositiveIntegerField('MTU', default=1400)
    pmtud = models.BooleanField('启用 PMTUD', default=True, null=True, blank=True)
    checksum = models.BooleanField('启用 Checksum', default=False, null=True, blank=True)
    extra_options = models.JSONField('其他参数', default=dict, blank=True, null=True)

    class Meta:
        verbose_name = 'GRE 配置'
        verbose_name_plural = 'GRE 配置'

    def __str__(self):
        return f'GRE {self.local_ip} ↔ {self.remote_ip}'


class VXLANConfig(models.Model):
    """VXLAN 隧道配置（对齐 vpopmanager netplan_tunnel mode=vxlan）"""
    edge = models.OneToOneField(
        EdgeAcceleration, on_delete=models.CASCADE,
        verbose_name='边缘节点', related_name='vxlan_config',
        limit_choices_to={'protocol': EdgeAcceleration.Protocol.VXLAN},
    )
    vni = models.PositiveIntegerField('VNI', help_text='VXLAN Network Identifier，对应 netplan id')
    local_ip = models.GenericIPAddressField('本端 VTEP IP', null=True, blank=True)
    remote_ip = models.GenericIPAddressField('远端 VTEP IP', null=True, blank=True)
    tunnel_ip = models.CharField('隧道 IP/CIDR', max_length=50, blank=True, help_text='如 10.20.0.1/30')
    port = models.PositiveIntegerField('UDP 端口', default=8472, null=True, blank=True)
    ttl = models.PositiveIntegerField('TTL', default=255, null=True, blank=True)
    mtu = models.PositiveIntegerField('MTU', default=1400)
    extra_options = models.JSONField('其他参数', default=dict, blank=True, null=True)

    class Meta:
        verbose_name = 'VXLAN 配置'
        verbose_name_plural = 'VXLAN 配置'

    def __str__(self):
        return f'VXLAN vni={self.vni} {self.local_ip} ↔ {self.remote_ip}'


class WireguardConfig(models.Model):
    """WireGuard 配置"""
    edge = models.OneToOneField(
        EdgeAcceleration, on_delete=models.CASCADE,
        verbose_name='边缘节点', related_name='wg_config',
        limit_choices_to={'protocol': EdgeAcceleration.Protocol.WIREGUARD},
    )
    listen_port = models.PositiveIntegerField('监听端口', default=51820)
    interface_ip = models.CharField('接口 IP/CIDR', max_length=255,
                                    help_text='如 10.100.0.1/24，多个用逗号分隔')
    private_key = models.CharField('私钥', max_length=512, blank=True)
    public_key = models.CharField('公钥', max_length=512, blank=True)
    fwmark = models.PositiveIntegerField('FwMark', null=True, blank=True)
    mtu = models.PositiveIntegerField('MTU', default=1400, null=True, blank=True)
    # Peer 信息
    peer_public_key = models.CharField('对端公钥', max_length=512, blank=True)
    peer_endpoint = models.CharField('对端地址', max_length=128, blank=True, help_text='IP:Port')
    peer_allowed_ips = models.CharField(
        '允许网段', max_length=512, blank=True,
        help_text='逗号分隔，如 0.0.0.0/0,::/0',
    )
    peer_preshared_key = models.CharField('对端预共享密钥(PSK)', max_length=512, blank=True)
    persistent_keepalive = models.PositiveIntegerField('Keepalive(秒)', default=25)
    dns = models.CharField('DNS', max_length=256, blank=True)
    pre_up = models.TextField('PreUp 脚本', blank=True, null=True)
    post_up = models.TextField('PostUp 脚本', blank=True, null=True)
    pre_down = models.TextField('PreDown 脚本', blank=True, null=True)
    post_down = models.TextField('PostDown 脚本', blank=True, null=True)
    extra_options = models.JSONField('其他参数', default=dict, blank=True, null=True)

    class Meta:
        verbose_name = 'WireGuard 配置'
        verbose_name_plural = 'WireGuard 配置'

    def __str__(self):
        return f'WG {self.interface_ip} (port {self.listen_port})'


class Socks5Config(models.Model):
    """Socks5 代理配置"""
    edge = models.OneToOneField(
        EdgeAcceleration, on_delete=models.CASCADE,
        verbose_name='边缘节点', related_name='socks5_config',
        limit_choices_to={'protocol': EdgeAcceleration.Protocol.SOCKS5},
    )
    listen_ip = models.GenericIPAddressField('监听 IP', default='0.0.0.0')
    listen_port = models.PositiveSmallIntegerField('监听端口', default=1080)
    auth_required = models.BooleanField('需要认证', default=True)
    username = models.CharField('用户名', max_length=64, blank=True)
    password = models.CharField('密码', max_length=256, blank=True)
    udp_enabled = models.BooleanField('启用 UDP', default=True)
    bind_address = models.GenericIPAddressField('出口绑定 IP', null=True, blank=True)
    max_connections = models.PositiveIntegerField('最大连接数', default=1000)
    timeout_seconds = models.PositiveSmallIntegerField('超时(秒)', default=60)
    extra_options = models.JSONField('其他参数', default=dict, blank=True)

    class Meta:
        verbose_name = 'Socks5 配置'
        verbose_name_plural = 'Socks5 配置'

    def __str__(self):
        return f'Socks5 {self.listen_ip}:{self.listen_port}'


# ═══════════════════════════════════════════════════════════════
# § 8  带宽分配
# ═══════════════════════════════════════════════════════════════

class BandwidthAllocation(models.Model):
    """POP 点带宽分配记录"""

    class AllocTarget(models.TextChoices):
        VPS = 'vps', 'VPS'
        EDGE = 'edge', '边缘加速'

    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.CASCADE,
        verbose_name='所属 POP 点', related_name='bandwidth_allocations',
    )
    target_type = models.CharField(
        '分配目标类型', max_length=8,
        choices=AllocTarget.choices,
    )
    vps = models.ForeignKey(
        VPS, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='目标 VPS', related_name='bandwidth_allocs',
    )
    edge = models.ForeignKey(
        EdgeAcceleration, on_delete=models.CASCADE,
        null=True, blank=True,
        verbose_name='目标边缘加速', related_name='bandwidth_allocs',
    )
    allocated_mbps = models.PositiveIntegerField('分配带宽(Mbps)')
    burst_mbps = models.PositiveIntegerField('突发带宽(Mbps)', default=0)
    is_active = models.BooleanField('生效中', default=True)
    # ── 计费关联产品（独立带宽产品按超出部分计费）──────────────
    product = models.ForeignKey(
        'product_manager.Product',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联产品', related_name='billed_bandwidth',
    )
    # ── 节点同步（来自节点 bandwidth-allocations） ────────────
    is_synced = models.BooleanField('节点同步导入', default=False, null=True, blank=True,
                                    help_text='由节点带宽分配同步生成，可被同步对账自动清理')
    source_interface_code = models.CharField('节点接口编码', max_length=128, blank=True,
                                             help_text='节点侧 interface_code，如 vm-3-net0，对账/回写键')
    last_synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '带宽分配'
        verbose_name_plural = '带宽分配列表'
        ordering = ['-created_at']
        constraints = [
            # 同步分配：同一 POP 同一节点接口编码唯一（对账/回写键），防止重复镜像行
            models.UniqueConstraint(
                fields=['pop_point', 'source_interface_code'],
                condition=models.Q(is_synced=True),
                name='uniq_synced_bw_ifcode',
            ),
            # 手工分配：单个 VPS / 边缘加速各自最多一条手工记录（口径与资源带宽字段一致）
            models.UniqueConstraint(
                fields=['vps'],
                condition=models.Q(is_synced=False, vps__isnull=False),
                name='uniq_manual_bw_vps',
            ),
            models.UniqueConstraint(
                fields=['edge'],
                condition=models.Q(is_synced=False, edge__isnull=False),
                name='uniq_manual_bw_edge',
            ),
        ]

    def __str__(self):
        target = self.vps or self.edge or '—'
        return f'{self.pop_point.name} → {target} {self.allocated_mbps}Mbps'


# ═══════════════════════════════════════════════════════════════
# § 9  资源操作日志
# ═══════════════════════════════════════════════════════════════

class ResourceOperationLog(models.Model):
    """资源管理模块统一操作日志"""

    class ActionType(models.TextChoices):
        CREATE = 'create', '新建'
        UPDATE = 'update', '更新'
        DELETE = 'delete', '删除'
        STATUS_CHANGE = 'status_change', '状态变更'
        DEPLOY = 'deploy', '部署'
        RESTART = 'restart', '重启'
        IP_ASSIGN = 'ip_assign', 'IP 分配'
        IP_RELEASE = 'ip_release', 'IP 释放'
        BW_ALLOC = 'bw_alloc', '带宽分配'
        CONFIG_CHANGE = 'config_change', '配置变更'
        IMPORT = 'import', '批量导入'
        EXPORT = 'export', '数据导出'
        OTHER = 'other', '其他'

    class TargetModel(models.TextChoices):
        POP = 'PopPoint', 'POP 点'
        VPS = 'VPS', 'VPS'
        EDGE = 'EdgeAcceleration', '边缘加速'
        IP_BLOCK = 'IPBlock', 'IP 地址块'
        IP_ADDRESS = 'IPAddress', 'IP 地址'
        INTERFACE = 'NetworkInterface', '网络接口'
        ROUTE_TABLE = 'RouteTable', '路由表'
        POLICY_ROUTE = 'PolicyRoute', '策略路由'
        BW_ALLOC = 'BandwidthAllocation', '带宽分配'
        CARRIER = 'Carrier', '运营商'
        REGION = 'Region', '大区'
        COUNTRY = 'Country', '国家'
        CITY = 'City', '城市'
        AVAILABILITY_ZONE = 'AvailabilityZone', '可用区'
        OTHER = 'Other', '其他'

    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        verbose_name='操作人',
    )
    action_type = models.CharField(
        '操作类型', max_length=20,
        choices=ActionType.choices, default=ActionType.OTHER,
    )
    target_model = models.CharField(
        '操作对象类型', max_length=32,
        choices=TargetModel.choices, blank=True,
    )
    target_id = models.PositiveIntegerField('对象 ID', null=True, blank=True)
    target_repr = models.CharField('对象描述', max_length=256, blank=True)
    # 关联核心资源（可选，便于快速过滤）
    pop_point = models.ForeignKey(
        PopPoint, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联 POP 点', related_name='operation_logs',
    )
    vps = models.ForeignKey(
        VPS, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联 VPS', related_name='operation_logs',
    )
    edge = models.ForeignKey(
        EdgeAcceleration, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联边缘加速', related_name='operation_logs',
    )
    description = models.CharField('操作描述', max_length=512)
    old_value = models.JSONField('变更前', null=True, blank=True)
    new_value = models.JSONField('变更后', null=True, blank=True)
    ip_address = models.GenericIPAddressField('操作 IP', null=True, blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        verbose_name = '资源操作日志'
        verbose_name_plural = '资源操作日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action_type', 'created_at']),
            models.Index(fields=['target_model', 'target_id']),
            models.Index(fields=['pop_point', 'created_at']),
            models.Index(fields=['vps', 'created_at']),
        ]

    def __str__(self):
        operator_name = (
            self.operator.get_full_name() or self.operator.username
            if self.operator else '系统'
        )
        return f'[{self.get_action_type_display()}] {operator_name} - {self.description}'


# ─────────────────────────────────────────────────────────────
# 日志记录工具函数
# ─────────────────────────────────────────────────────────────

def record_resource_log(
    *,
    operator,
    action_type,
    description,
    target_model='',
    target_id=None,
    target_repr='',
    pop_point=None,
    vps=None,
    edge=None,
    old_value=None,
    new_value=None,
    ip_address=None,
    request=None,
):
    """
    记录一条资源操作日志，在 admin / views 中直接调用。
    """
    if request is not None and ip_address is None:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip_address = (
            x_forwarded_for.split(',')[0].strip()
            if x_forwarded_for
            else request.META.get('REMOTE_ADDR')
        )
    # 防御性截断，避免超长文本触发数据库 1406 Data too long
    if description:
        description = str(description)[:512]
    if target_repr:
        target_repr = str(target_repr)[:256]
    ResourceOperationLog.objects.create(
        operator=operator,
        action_type=action_type,
        target_model=target_model,
        target_id=target_id,
        target_repr=target_repr,
        pop_point=pop_point,
        vps=vps,
        edge=edge,
        description=description,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
    )


def resource_model_snapshot(instance):
    """将模型实例转为可序列化快照字典（用于日志 old/new_value）"""
    data = {}
    for field in instance._meta.fields:
        val = getattr(instance, field.name)
        if hasattr(val, 'pk'):
            data[field.name] = val.pk
        elif hasattr(val, 'isoformat'):
            data[field.name] = val.isoformat()
        else:
            data[field.name] = str(val) if val is not None else None
    return data
