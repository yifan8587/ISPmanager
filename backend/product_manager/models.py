"""
product_manager/models.py
产品管理模块数据模型

模型层次：
  ProductSpec        产品规格（VPS：CPU/内存/硬盘/网卡/带宽/IP；边缘加速：类型/带宽/IP）
  ProductTemplate    产品模版（如 VPS 1C1G / 2C4G 等预设规格）
  TemplatePricing    模版定价（对应 POP 点的月租 / 信誉值定价）
  Product            产品（关联 POP + 模版 + 供应商，含基础定价）
  ProductUpgradeRule 阶梯式定制升级定价规则
  ProductPublishLog  产品发布 / 上下线日志

资源容量/库存的动态计算见 services.py。
"""
import datetime

from django.contrib.auth.models import User
from django.db import models


class ProductCategory(models.TextChoices):
    VPS = 'vps', 'VPS 主机'
    EDGE = 'edge', '边缘加速'


class AccelType(models.TextChoices):
    GRE = 'gre', 'GRE'
    WIREGUARD = 'wireguard', 'WireGuard'


# 公共规格字段抽象（VPS + 边缘加速共用）
class SpecFieldsMixin(models.Model):
    cpu_cores = models.PositiveSmallIntegerField('CPU 核心', default=1)
    ram_mb = models.PositiveIntegerField('内存(MB)', default=1024)
    disk_gb = models.PositiveIntegerField('硬盘(GB)', default=20)
    nic_count = models.PositiveSmallIntegerField('网卡数量', default=1)
    bandwidth_mbps = models.PositiveIntegerField('带宽(Mbps)', default=0)
    ip_count = models.PositiveSmallIntegerField('IP 数量', default=1)
    accel_type = models.CharField(
        '加速类型', max_length=64, blank=True,
        help_text='仅边缘加速有效，可多选 GRE/WireGuard，逗号分隔。客户购买后自行选择对接模式。',
    )

    class Meta:
        abstract = True

    @property
    def accel_types_list(self) -> list[str]:
        from .accel import parse_accel_types
        return parse_accel_types(self.accel_type)

    def get_accel_type_display(self) -> str:
        from .accel import accel_types_label
        return accel_types_label(self.accel_type)

    def spec_dict(self) -> dict:
        return {
            'cpu_cores': self.cpu_cores,
            'ram_mb': self.ram_mb,
            'disk_gb': self.disk_gb,
            'nic_count': self.nic_count,
            'bandwidth_mbps': self.bandwidth_mbps,
            'ip_count': self.ip_count,
            'accel_type': self.accel_type,
            'accel_types': self.accel_types_list,
        }


# ═══════════════════════════════════════════════════════════════
# § 1  产品规格
# ═══════════════════════════════════════════════════════════════

class ProductSpec(SpecFieldsMixin):
    """可复用的产品规格定义"""

    spec_no = models.CharField('规格编号', max_length=32, unique=True, editable=False, null=True, blank=True)
    name = models.CharField('规格名称', max_length=128)
    category = models.CharField(
        '产品类别', max_length=8,
        choices=ProductCategory.choices, default=ProductCategory.VPS,
    )
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联 POP 点', related_name='product_specs',
    )
    supplier = models.ForeignKey(
        'supplier_manager.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联供应商', related_name='product_specs',
    )
    is_active = models.BooleanField('启用', default=True)
    remark = models.CharField('备注', max_length=512, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_product_specs',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '产品规格'
        verbose_name_plural = '产品规格列表'
        ordering = ['category', 'name']

    def __str__(self):
        return f'[{self.spec_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.spec_no:
            prefix = 'SPEC'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = ProductSpec.objects.filter(
                spec_no__startswith=f'{prefix}{date_str}'
            ).order_by('-spec_no').first()
            seq = int(last.spec_no[-4:]) + 1 if last and last.spec_no else 1
            self.spec_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 2  产品模版
# ═══════════════════════════════════════════════════════════════

class ProductTemplate(SpecFieldsMixin):
    """产品模版（预设规格，如 VPS 1C1G）"""

    template_no = models.CharField('模版编号', max_length=32, unique=True, editable=False, null=True, blank=True)
    name = models.CharField('模版名称', max_length=128, help_text='如 VPS 1C1G / 2C4G')
    category = models.CharField(
        '产品类别', max_length=8,
        choices=ProductCategory.choices, default=ProductCategory.VPS,
    )
    spec = models.ForeignKey(
        ProductSpec, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='基准规格', related_name='templates',
    )
    is_active = models.BooleanField('启用', default=True)
    description = models.CharField('描述', max_length=512, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_product_templates',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '产品模版'
        verbose_name_plural = '产品模版列表'
        ordering = ['category', 'name']

    def __str__(self):
        return f'[{self.template_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.template_no:
            prefix = 'TPL'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = ProductTemplate.objects.filter(
                template_no__startswith=f'{prefix}{date_str}'
            ).order_by('-template_no').first()
            seq = int(last.template_no[-4:]) + 1 if last and last.template_no else 1
            self.template_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


class TemplatePricing(models.Model):
    """模版在某个 POP 点的定价（月租 / 信誉值）"""

    template = models.ForeignKey(
        ProductTemplate, on_delete=models.CASCADE,
        verbose_name='模版', related_name='pricings',
    )
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.CASCADE,
        verbose_name='POP 点', related_name='template_pricings',
    )
    monthly_price = models.DecimalField('月租价', max_digits=12, decimal_places=2, default=0)
    reputation_price = models.DecimalField('信誉值定价', max_digits=12, decimal_places=2, default=0)
    setup_price = models.DecimalField('一次性安装费', max_digits=12, decimal_places=2, default=0)
    currency = models.CharField('币种', max_length=8, default='USD')
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '模版定价'
        verbose_name_plural = '模版定价列表'
        ordering = ['template', 'pop_point']
        constraints = [
            models.UniqueConstraint(fields=['template', 'pop_point'], name='uniq_template_pop_pricing'),
        ]

    def __str__(self):
        return f'{self.template.name}@{self.pop_point} ${self.monthly_price}/月'


# ═══════════════════════════════════════════════════════════════
# § 3  产品
# ═══════════════════════════════════════════════════════════════

class Product(SpecFieldsMixin):
    """可售产品（绑定 POP + 模版 + 供应商，含定价与发布状态）"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        ONLINE = 'online', '已上线'
        OFFLINE = 'offline', '已下线'
        ARCHIVED = 'archived', '已归档'

    product_no = models.CharField('产品编号', max_length=32, unique=True, editable=False, null=True, blank=True)
    name = models.CharField('产品名称', max_length=128)
    category = models.CharField(
        '产品类别', max_length=8,
        choices=ProductCategory.choices, default=ProductCategory.VPS,
    )
    status = models.CharField('状态', max_length=12, choices=Status.choices, default=Status.DRAFT)

    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.PROTECT,
        verbose_name='关联 POP 点', related_name='products',
    )
    template = models.ForeignKey(
        ProductTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联模版', related_name='products',
    )
    supplier = models.ForeignKey(
        'supplier_manager.Supplier', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联供应商', related_name='products',
    )

    # ── 基础定价 ──────────────────────────────────────────────
    monthly_price = models.DecimalField('月租价', max_digits=12, decimal_places=2, default=0)
    reputation_price = models.DecimalField('信誉值定价', max_digits=12, decimal_places=2, default=0)
    setup_price = models.DecimalField('一次性安装费', max_digits=12, decimal_places=2, default=0)
    currency = models.CharField('币种', max_length=8, default='USD')

    class BillingMode(models.TextChoices):
        FLAT = 'flat', '包月'
        P95 = '95th', '95 计费'
        AVG = 'avg', '平均带宽'
        TRANSFER_QUOTA = 'transfer_quota', '流量包'

    billing_mode = models.CharField(
        '计费模式',
        max_length=16,
        choices=BillingMode.choices,
        default=BillingMode.FLAT,
        help_text='包月只收月租；95/平均按承诺带宽出基础月租，超额单价留给流量出账；流量包按包含流量计',
    )
    transfer_quota_gb = models.PositiveIntegerField(
        '流量包额度(GB/月)',
        null=True,
        blank=True,
        help_text='仅流量包模式使用；每月包含的传输量',
    )
    overage_unit_price = models.DecimalField(
        '超额单价',
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text='流量包为每 GB；95/平均为超出承诺带宽后的每 Mbps。归档流量报告时按此出超额明细',
    )

    # ── 门户展示 ──────────────────────────────────────────────
    sample_ip = models.CharField(
        '示例 IP', max_length=255, blank=True, default='',
        help_text='门户展示用的示例地址，可填单个或多个（逗号分隔）',
    )
    sample_ip_visible = models.BooleanField(
        '显示示例 IP', default=False,
        help_text='勾选后在客户门户产品页展示示例 IP',
    )
    estimated_provision_hours = models.PositiveIntegerField(
        '预估开通时间(小时)', null=True, blank=True,
        help_text='库存不足仍上线时填写；客户付款后据此提示预计交付时间',
    )
    allow_backorder = models.BooleanField(
        '允许缺货售卖', default=False,
        help_text='库存不足时仍允许上线和下单，按预估开通时间交付',
    )
    allow_reputation_pay = models.BooleanField(
        '允许信誉值支付', default=False,
        help_text='勾选后，已开通信誉值的客户可用信誉值支付本产品；否则只能走其他支付方式',
    )
    os_templates = models.ManyToManyField(
        'system_manager.SystemOsTemplate',
        blank=True,
        related_name='products',
        verbose_name='可选系统版本',
        help_text='VPS 产品可选的系统模版；不选表示门户展示全部已启用模版',
    )

    # ── 展示 / 发布 ───────────────────────────────────────────
    sort_order = models.IntegerField('排序', default=0)
    is_featured = models.BooleanField('推荐', default=False)
    is_hot_sale = models.BooleanField(
        '热卖',
        default=False,
        help_text='默认不勾选。勾选后门户展示热卖；未勾选时，已采购满 2 个在服服务也会自动提示热卖',
    )
    description = models.TextField('产品描述', blank=True)
    remark = models.TextField('内部备注', blank=True)
    published_at = models.DateTimeField('上线时间', null=True, blank=True)
    offline_at = models.DateTimeField('下线时间', null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_products',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '产品'
        verbose_name_plural = '产品列表'
        ordering = ['sort_order', '-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['category']),
            models.Index(fields=['pop_point']),
            models.Index(fields=['billing_mode']),
        ]

    def __str__(self):
        return f'[{self.product_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.product_no:
            prefix = 'PRD'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = Product.objects.filter(
                product_no__startswith=f'{prefix}{date_str}'
            ).order_by('-product_no').first()
            seq = int(last.product_no[-4:]) + 1 if last and last.product_no else 1
            self.product_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


class PortalCountryShortcut(models.Model):
    """门户首页「快速查找」展示的国家，由产品管理自行勾选与排序。"""

    country = models.OneToOneField(
        'resource_manager.Country', on_delete=models.CASCADE,
        verbose_name='国家', related_name='portal_shortcut',
    )
    display_name = models.CharField(
        '显示名称', max_length=64, blank=True,
        help_text='留空则使用国家本身的名称',
    )
    sort_order = models.PositiveIntegerField('排序', default=0)
    is_active = models.BooleanField('在门户显示', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '门户快速查找国家'
        verbose_name_plural = '门户快速查找国家'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return self.display_name or str(self.country)

    @property
    def label(self) -> str:
        return (self.display_name or '').strip() or getattr(self.country, 'name', '') or ''


class ProductUpgradeRule(models.Model):
    """阶梯式定制升级定价规则（按资源单位逐档计价）"""

    class ResourceKind(models.TextChoices):
        CPU = 'cpu', 'CPU 核'
        RAM = 'ram', '内存(GB)'
        DISK = 'disk', '硬盘(GB)'
        BANDWIDTH = 'bandwidth', '带宽(Mbps)'
        IP = 'ip', 'IP 数量'
        NIC = 'nic', '网卡数量'

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        verbose_name='产品', related_name='upgrade_rules',
    )
    resource_kind = models.CharField('资源类型', max_length=16, choices=ResourceKind.choices)
    # 阶梯区间：在 [min_qty, max_qty] 升级数量内，按 unit_price 计价
    min_qty = models.PositiveIntegerField('阶梯起始数量', default=0,
                                          help_text='超出基础规格的升级数量下界（含）')
    max_qty = models.PositiveIntegerField('阶梯结束数量', null=True, blank=True,
                                          help_text='升级数量上界（含）；留空表示不封顶')
    unit_price = models.DecimalField('每单位月租', max_digits=12, decimal_places=4, default=0)
    unit_reputation = models.DecimalField('每单位信誉值', max_digits=12, decimal_places=4, default=0)
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '阶梯升级定价'
        verbose_name_plural = '阶梯升级定价'
        ordering = ['product', 'resource_kind', 'min_qty']

    def __str__(self):
        upper = self.max_qty if self.max_qty is not None else '∞'
        return f'{self.product.name} {self.get_resource_kind_display()} [{self.min_qty}-{upper}] ${self.unit_price}'


class ProductPublishLog(models.Model):
    """产品发布 / 上下线操作日志"""

    class Action(models.TextChoices):
        PUBLISH = 'publish', '发布'
        ONLINE = 'online', '上线'
        OFFLINE = 'offline', '下线'
        ARCHIVE = 'archive', '归档'
        UPDATE = 'update', '更新'

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        verbose_name='产品', related_name='publish_logs',
    )
    action = models.CharField('动作', max_length=12, choices=Action.choices)
    from_status = models.CharField('原状态', max_length=12, blank=True)
    to_status = models.CharField('新状态', max_length=12, blank=True)
    message = models.CharField('说明', max_length=512, blank=True)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='操作人',
    )
    created_at = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '产品发布日志'
        verbose_name_plural = '产品发布日志'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_action_display()}] {self.product.name}'
