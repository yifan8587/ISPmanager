"""
supplier_manager/models.py
供应商管理模块数据模型

模型层次：
  Supplier            供应商详细信息
  SupplierAPI         供应商对接 API（自动同步资源/产品/POP）
  SupplierPopBinding  供应商 ↔ POP 点关联
  SupplierResource    供应商资源（由 API 自动接收，可映射到本地 POP / 产品）
  SupplierSyncLog     同步操作日志

说明：
  为避免与 product_manager 形成循环外键依赖，本模块不直接外键引用 product_manager，
  对产品的关联通过整数 mapped_product_id 弱引用维护（同步映射用途）。
"""
import datetime

from django.contrib.auth.models import User
from django.db import models


class SupplierType(models.TextChoices):
    IDC = 'idc', 'IDC 机房'
    CLOUD = 'cloud', '云厂商'
    ISP = 'isp', '运营商/ISP'
    AGENT = 'agent', '代理商'
    OTHER = 'other', '其他'


class SupplierStatus(models.TextChoices):
    ACTIVE = 'active', '合作中'
    SUSPENDED = 'suspended', '暂停合作'
    DISABLED = 'disabled', '停用'
    PENDING = 'pending', '待审核'


# ═══════════════════════════════════════════════════════════════
# § 1  供应商
# ═══════════════════════════════════════════════════════════════

class Supplier(models.Model):
    """供应商基本信息"""

    supplier_no = models.CharField('供应商编号', max_length=32, unique=True, editable=False, null=True, blank=True)
    name = models.CharField('供应商名称', max_length=128)
    short_name = models.CharField('简称', max_length=64, blank=True)
    supplier_type = models.CharField(
        '供应商类型', max_length=16,
        choices=SupplierType.choices, default=SupplierType.IDC,
    )
    status = models.CharField(
        '状态', max_length=16,
        choices=SupplierStatus.choices, default=SupplierStatus.PENDING,
    )

    # ── 联系信息 ──────────────────────────────────────────────
    contact_name = models.CharField('联系人', max_length=64, blank=True)
    contact_phone = models.CharField('联系电话', max_length=32, blank=True)
    contact_email = models.EmailField('联系邮箱', blank=True)
    contact_im = models.CharField('IM(QQ/微信/TG)', max_length=128, blank=True)

    # ── 公司信息 ──────────────────────────────────────────────
    company_name = models.CharField('公司全称', max_length=256, blank=True)
    unified_credit_code = models.CharField('统一社会信用代码', max_length=32, blank=True)
    country = models.CharField('国家/地区', max_length=64, blank=True)
    address = models.CharField('地址', max_length=256, blank=True)
    website = models.CharField('官网', max_length=256, blank=True)

    # ── 账务 / 评价 ───────────────────────────────────────────
    settlement_currency = models.CharField('结算币种', max_length=8, default='CNY')
    settlement_rate = models.DecimalField(
        '结算比例(%)', max_digits=5, decimal_places=2, default=90,
        help_text='用户实付金额中结算给供应商的比例，例如 90 表示结算 90%',
    )
    settlement_min_amount = models.DecimalField(
        '满额结算金额', max_digits=14, decimal_places=2, default=100,
        help_text='待结算金额达到该阈值后可发起结算；0 表示不自动满额、需手工结算',
    )
    balance = models.DecimalField('账户余额', max_digits=14, decimal_places=2, default=0)
    credit_limit = models.DecimalField('授信额度', max_digits=14, decimal_places=2, default=0)
    rating = models.PositiveSmallIntegerField('信誉评分', default=0, help_text='0-100')

    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人', related_name='created_suppliers',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '供应商'
        verbose_name_plural = '供应商列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['supplier_type']),
        ]

    def __str__(self):
        return f'[{self.supplier_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.supplier_no:
            prefix = 'SUP'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = Supplier.objects.filter(
                supplier_no__startswith=f'{prefix}{date_str}'
            ).order_by('-supplier_no').first()
            seq = int(last.supplier_no[-4:]) + 1 if last and last.supplier_no else 1
            self.supplier_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 2  供应商 API（对接/自动同步）
# ═══════════════════════════════════════════════════════════════

class SupplierAPI(models.Model):
    """供应商对接 API 配置"""

    class AuthType(models.TextChoices):
        NONE = 'none', '无认证'
        TOKEN = 'token', 'Token (Authorization: Token)'
        BEARER = 'bearer', 'Bearer (JWT)'
        BASIC = 'basic', 'Basic 认证'
        APIKEY = 'apikey', 'API Key (自定义头)'

    class SyncStatus(models.TextChoices):
        NEVER = 'never', '从未同步'
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='apis',
    )
    name = models.CharField('接口名称', max_length=128, default='默认接口')
    base_url = models.CharField('API 基址', max_length=256, help_text='如 https://api.vendor.com')
    auth_type = models.CharField(
        '认证方式', max_length=16,
        choices=AuthType.choices, default=AuthType.TOKEN,
    )
    api_key = models.CharField('API Key/Token', max_length=512, blank=True)
    api_header_name = models.CharField('自定义认证头名', max_length=64, blank=True,
                                       help_text='auth_type=apikey 时使用，如 X-Api-Key')
    username = models.CharField('用户名', max_length=128, blank=True)
    password = models.CharField('密码', max_length=256, blank=True)
    extra_headers = models.JSONField('附加请求头', default=dict, blank=True)

    # ── 同步端点（相对 base_url 的路径） ──────────────────────
    resources_path = models.CharField('资源列表路径', max_length=256, blank=True, default='/api/resources/')
    products_path = models.CharField('产品列表路径', max_length=256, blank=True, default='/api/products/')
    pops_path = models.CharField('POP列表路径', max_length=256, blank=True, default='/api/pops/')

    verify_ssl = models.BooleanField('校验 SSL', default=True)
    timeout_seconds = models.PositiveSmallIntegerField('超时(秒)', default=20)

    # ── 同步策略 ──────────────────────────────────────────────
    sync_enabled = models.BooleanField('启用自动同步', default=False)
    sync_interval_minutes = models.PositiveIntegerField('同步间隔(分钟)', default=60)
    sync_resources = models.BooleanField('同步资源', default=True)
    sync_products = models.BooleanField('同步产品', default=False)
    sync_pops = models.BooleanField('同步POP', default=False)
    is_active = models.BooleanField('启用', default=True)

    last_sync_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    last_sync_status = models.CharField(
        '最近同步状态', max_length=16,
        choices=SyncStatus.choices, default=SyncStatus.NEVER,
    )
    last_sync_message = models.CharField('最近同步信息', max_length=512, blank=True)

    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '供应商 API'
        verbose_name_plural = '供应商 API 列表'
        ordering = ['supplier', 'name']

    def __str__(self):
        return f'{self.supplier.name} / {self.name}'


# ═══════════════════════════════════════════════════════════════
# § 3  供应商 ↔ POP 点关联
# ═══════════════════════════════════════════════════════════════

class SupplierPopBinding(models.Model):
    """供应商与本地 POP 点的关联（供产品/资源对应同步）"""

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='pop_bindings',
    )
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.CASCADE,
        verbose_name='POP 点', related_name='supplier_bindings',
    )
    external_pop_id = models.CharField('供应商侧POP标识', max_length=128, blank=True)
    external_pop_name = models.CharField('供应商侧POP名称', max_length=128, blank=True)
    is_active = models.BooleanField('生效中', default=True)
    remark = models.CharField('备注', max_length=256, blank=True)
    synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '供应商POP关联'
        verbose_name_plural = '供应商POP关联'
        ordering = ['supplier', 'pop_point']
        constraints = [
            models.UniqueConstraint(fields=['supplier', 'pop_point'], name='uniq_supplier_pop'),
        ]

    def __str__(self):
        return f'{self.supplier.name} ↔ {self.pop_point}'


# ═══════════════════════════════════════════════════════════════
# § 4  供应商资源（自动接收 + 本地映射）
# ═══════════════════════════════════════════════════════════════

class SupplierResource(models.Model):
    """供应商资源条目（由 API 同步接收，可映射到本地 POP / 产品）"""

    class ResType(models.TextChoices):
        VPS = 'vps', 'VPS'
        EDGE = 'edge', '边缘加速'
        IP = 'ip', 'IP 资源'
        BANDWIDTH = 'bandwidth', '带宽'
        OTHER = 'other', '其他'

    class SyncStatus(models.TextChoices):
        PENDING = 'pending', '待处理'
        SYNCED = 'synced', '已接收'
        MAPPED = 'mapped', '已映射'
        ERROR = 'error', '异常'

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='resources',
    )
    api = models.ForeignKey(
        SupplierAPI, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='来源API', related_name='resources',
    )
    resource_type = models.CharField(
        '资源类型', max_length=16,
        choices=ResType.choices, default=ResType.VPS,
    )
    external_id = models.CharField('供应商侧资源ID', max_length=128)
    name = models.CharField('资源名称', max_length=255, blank=True)

    # ── 规格快照 ──────────────────────────────────────────────
    cpu_cores = models.PositiveIntegerField('CPU 核', null=True, blank=True)
    ram_mb = models.PositiveIntegerField('内存(MB)', null=True, blank=True)
    disk_gb = models.PositiveIntegerField('硬盘(GB)', null=True, blank=True)
    bandwidth_mbps = models.PositiveIntegerField('带宽(Mbps)', null=True, blank=True)
    ip_count = models.PositiveSmallIntegerField('IP数量', null=True, blank=True)

    # ── 定位 / 映射 ───────────────────────────────────────────
    region_text = models.CharField('地区(原文)', max_length=128, blank=True)
    pop_external_id = models.CharField('供应商侧POP标识', max_length=128, blank=True)
    pop_point = models.ForeignKey(
        'resource_manager.PopPoint', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='映射POP点', related_name='supplier_resources',
    )
    # 弱引用 product_manager.Product（避免循环外键）
    mapped_product_id = models.PositiveIntegerField('映射产品ID', null=True, blank=True)

    # ── 成本 ──────────────────────────────────────────────────
    cost_price = models.DecimalField('成本价/月', max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField('币种', max_length=8, default='CNY')

    raw_payload = models.JSONField('原始数据', default=dict, blank=True)
    sync_status = models.CharField(
        '同步状态', max_length=16,
        choices=SyncStatus.choices, default=SyncStatus.PENDING,
    )
    synced_at = models.DateTimeField('最近同步时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '供应商资源'
        verbose_name_plural = '供应商资源列表'
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['supplier', 'external_id'], name='uniq_supplier_resource'),
        ]
        indexes = [
            models.Index(fields=['resource_type']),
            models.Index(fields=['sync_status']),
        ]

    def __str__(self):
        return f'{self.supplier.name} / {self.name or self.external_id}'


# ═══════════════════════════════════════════════════════════════
# § 5  同步日志
# ═══════════════════════════════════════════════════════════════

class SupplierSyncLog(models.Model):
    """供应商 API 同步日志"""

    class Action(models.TextChoices):
        TEST = 'test', '连接测试'
        PULL_RESOURCES = 'pull_resources', '拉取资源'
        PULL_PRODUCTS = 'pull_products', '拉取产品'
        PULL_POPS = 'pull_pops', '拉取POP'
        MAP = 'map', '映射同步'

    class Result(models.TextChoices):
        SUCCESS = 'success', '成功'
        PARTIAL = 'partial', '部分成功'
        FAILED = 'failed', '失败'

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='sync_logs',
    )
    api = models.ForeignKey(
        SupplierAPI, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='API', related_name='sync_logs',
    )
    action = models.CharField('动作', max_length=20, choices=Action.choices)
    result = models.CharField('结果', max_length=16, choices=Result.choices, default=Result.SUCCESS)
    received_count = models.PositiveIntegerField('接收条数', default=0)
    created_count = models.PositiveIntegerField('新增条数', default=0)
    updated_count = models.PositiveIntegerField('更新条数', default=0)
    message = models.TextField('信息', blank=True)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='操作人',
    )
    created_at = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = '供应商同步日志'
        verbose_name_plural = '供应商同步日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['supplier', 'created_at']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f'[{self.get_action_display()}] {self.supplier.name} - {self.get_result_display()}'


def record_sync_log(*, supplier, action, result, api=None,
                    received=0, created=0, updated=0, message='', operator=None):
    """记录一条供应商同步日志。"""
    return SupplierSyncLog.objects.create(
        supplier=supplier, api=api, action=action, result=result,
        received_count=received, created_count=created, updated_count=updated,
        message=str(message)[:2000], operator=operator,
    )


# ═══════════════════════════════════════════════════════════════
# § 6  供应商账单 / 结算
# ═══════════════════════════════════════════════════════════════

class SupplierBill(models.Model):
    """用户采购供应商产品/服务并完成支付后，按结算比例生成的供应商侧账单。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待结算'
        IN_SETTLEMENT = 'in_settlement', '结算中'
        SETTLED = 'settled', '已结算'
        CANCELLED = 'cancelled', '已取消'

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='bills',
    )
    customer_billing = models.ForeignKey(
        'customer_manager.CustomerBilling', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='客户账单', related_name='supplier_bills',
    )
    billing_item = models.ForeignKey(
        'customer_manager.BillingItem', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='客户账单明细', related_name='supplier_bills',
    )
    service_order = models.ForeignKey(
        'service_manager.ServiceOrder', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='服务工单', related_name='supplier_bills',
    )
    product = models.ForeignKey(
        'product_manager.Product', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='产品', related_name='supplier_bills',
    )
    settlement = models.ForeignKey(
        'SupplierSettlement', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='所属结算单', related_name='bills',
    )

    bill_no = models.CharField('供应商账单号', max_length=32, unique=True, editable=False)
    customer_bill_no = models.CharField('客户账单号', max_length=64, blank=True)
    customer_name = models.CharField('客户名称', max_length=128, blank=True)
    product_name = models.CharField('产品名称', max_length=128, blank=True)
    bill_month = models.CharField('账期', max_length=7, blank=True)

    source_amount = models.DecimalField('用户实付金额', max_digits=14, decimal_places=2, default=0)
    settlement_rate = models.DecimalField('结算比例(%)', max_digits=5, decimal_places=2, default=90)
    settle_amount = models.DecimalField('应结金额', max_digits=14, decimal_places=2, default=0)
    currency = models.CharField('币种', max_length=8, default='CNY')
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    paid_at = models.DateTimeField('客户支付时间', null=True, blank=True)
    remark = models.CharField('备注', max_length=256, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '供应商账单'
        verbose_name_plural = '供应商账单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['supplier', 'status']),
            models.Index(fields=['customer_billing']),
            models.Index(fields=['bill_month']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['billing_item'],
                condition=models.Q(billing_item__isnull=False),
                name='uniq_supplier_bill_item',
            ),
            models.UniqueConstraint(
                fields=['customer_billing', 'product'],
                condition=models.Q(billing_item__isnull=True, product__isnull=False),
                name='uniq_supplier_bill_billing_product',
            ),
        ]

    def __str__(self):
        return f'[{self.bill_no}] {self.supplier_id} {self.settle_amount} {self.currency}'

    def save(self, *args, **kwargs):
        if not self.bill_no:
            prefix = 'SPB'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = SupplierBill.objects.filter(
                bill_no__startswith=f'{prefix}{date_str}'
            ).order_by('-bill_no').first()
            seq = int(last.bill_no[-4:]) + 1 if last and last.bill_no else 1
            self.bill_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


class SupplierSettlement(models.Model):
    """供应商满额/手工结算单。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待打款'
        PAID = 'paid', '已结算'
        CANCELLED = 'cancelled', '已取消'

    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE,
        verbose_name='供应商', related_name='settlements',
    )
    settlement_no = models.CharField('结算单号', max_length=32, unique=True, editable=False)
    amount = models.DecimalField('结算金额', max_digits=14, decimal_places=2, default=0)
    currency = models.CharField('币种', max_length=8, default='CNY')
    bill_count = models.PositiveIntegerField('账单笔数', default=0)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    auto_created = models.BooleanField('满额自动生成', default=False)
    remark = models.CharField('备注', max_length=256, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='发起人', related_name='created_supplier_settlements',
    )
    settled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='确认人', related_name='paid_supplier_settlements',
    )
    settled_at = models.DateTimeField('结算时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '供应商结算单'
        verbose_name_plural = '供应商结算单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['supplier', 'status']),
        ]

    def __str__(self):
        return f'[{self.settlement_no}] {self.supplier_id} {self.amount} {self.currency}'

    def save(self, *args, **kwargs):
        if not self.settlement_no:
            prefix = 'SET'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = SupplierSettlement.objects.filter(
                settlement_no__startswith=f'{prefix}{date_str}'
            ).order_by('-settlement_no').first()
            seq = int(last.settlement_no[-4:]) + 1 if last and last.settlement_no else 1
            self.settlement_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)
