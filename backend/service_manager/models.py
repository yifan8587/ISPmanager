"""
service_manager/models.py
服务管理模块 — 连接客户管理与资源管理的业务层

模型层次：
  ServiceOrder             服务请求（客户需求）
  ServiceInstance          服务实例（已分配的具体资源）
  ServiceAllocation        资源分配/回收明细记录
  ServiceChangeRecord      服务变更申请与记录
  ServiceLifecycleRecord   全生命周期阶段记录（下单→需求→测试→使用→变更→终止）
  ServiceProvisionHook     外部开通/对接 API 钩子预留
  ServiceOperationLog      服务模块统一操作日志

核心联动：
  - ServiceInstance.activate()   → 更新关联资源 customer / status
  - ServiceInstance.suspend()    → 冻结资源
  - ServiceInstance.terminate()  → 释放资源，IP 回收，带宽归还
  - 生命周期阶段推进时写入提交人/处理人/审批人与资源前后快照
"""

import datetime
import uuid
from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.db.models import Model
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════
# § 1  服务请求
# ═══════════════════════════════════════════════════════════════

class ServiceOrder(models.Model):
    """客户服务请求：基于产品目录发起，由履约、实例、变更和账务共同闭环。"""

    class BillingCycle(models.TextChoices):
        MONTHLY = 'monthly', '按月'
        QUARTERLY = 'quarterly', '按季'
        SEMI_ANNUAL = 'semi_annual', '半年'
        ANNUAL = 'annual', '按年'
        ONE_TIME = 'one_time', '一次性'
        ON_DEMAND = 'on_demand', '按需'

    class OrderStatus(models.TextChoices):
        DRAFT = 'draft', '草稿'
        PENDING = 'pending', '待审批'
        APPROVED = 'approved', '已审批'
        PROVISIONING = 'provisioning', '开通中'
        ACTIVE = 'active', '服务中'
        SUSPEND_PENDING = 'suspend_pending', '待暂停'
        SUSPENDED = 'suspended', '已暂停'
        TERMINATE_PENDING = 'terminate_pending', '待终止'
        TERMINATED = 'terminated', '已终止'
        REJECTED = 'rejected', '已驳回'

    class OrderType(models.TextChoices):
        NEW = 'new', '新开服务'
        RENEW = 'renew', '续期'
        UPGRADE = 'upgrade', '升级'
        DOWNGRADE = 'downgrade', '降级'
        TERMINATE = 'terminate', '终止'
        CHANGE = 'change', '变更'

    class BusinessType(models.TextChoices):
        VPS = 'vps', 'VPS 主机'
        EDGE_GRE = 'edge_gre', '边缘加速 · GRE'
        EDGE_WG = 'edge_wg', '边缘加速 · WireGuard'
        EDGE_SOCKS5 = 'edge_socks5', '边缘加速 · Socks5'
        IP = 'ip', 'IP 资源'
        BANDWIDTH = 'bandwidth', '带宽资源'

    class LifecycleStage(models.TextChoices):
        ORDER = 'order', '下单'
        REQUIREMENT = 'requirement', '需求确认'
        TEST = 'test', '测试'
        USAGE = 'usage', '正式使用'
        CHANGE = 'change', '变更'
        TERMINATION = 'termination', '终止'

    class OrderSource(models.TextChoices):
        PORTAL = 'portal', '客户门户'
        INQUIRY = 'inquiry', '官网咨询转工单'
        ADMIN = 'admin', '后台创建'
        API = 'api', '外部 API'
        SYSTEM = 'system', '系统自动'

    order_no = models.CharField('工单编号', max_length=32, unique=True, editable=False)
    order_type = models.CharField('工单类型', max_length=16, choices=OrderType.choices, default=OrderType.NEW)
    business_type = models.CharField(
        '业务类型', max_length=16,
        choices=BusinessType.choices, default=BusinessType.VPS,
        help_text='VPS 主机 / 边缘加速(GRE·WireGuard·Socks5) / IP / 带宽',
    )
    status = models.CharField(
        '状态', max_length=20,
        choices=OrderStatus.choices, default=OrderStatus.DRAFT,
    )
    current_stage = models.CharField(
        '当前生命周期阶段', max_length=20,
        choices=LifecycleStage.choices, default=LifecycleStage.ORDER,
    )
    source = models.CharField(
        '来源', max_length=16,
        choices=OrderSource.choices, default=OrderSource.ADMIN,
    )
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.PROTECT,
        verbose_name='客户', related_name='service_orders',
    )
    contract = models.ForeignKey(
        'customer_manager.Contract',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联合同', related_name='service_orders',
    )
    product = models.ForeignKey(
        'product_manager.Product',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联产品', related_name='service_orders',
    )
    related_instance = models.ForeignKey(
        'ServiceInstance',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联服务实例', related_name='related_orders',
        help_text='变更/终止类工单关联的原服务实例',
    )
    legacy_plan_snapshot = models.JSONField(
        '历史套餐快照', default=dict, blank=True,
        help_text='ServicePlan 退役前写入的只读审计数据，不参与报价、开通或计费。',
    )

    # ── 通用资源需求 ──────────────────────────────────────────
    preferred_pop = models.ForeignKey(
        'resource_manager.PopPoint',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='期望 POP 节点', related_name='preferred_orders',
    )
    requested_bandwidth_mbps = models.PositiveIntegerField('需求带宽(Mbps)', null=True, blank=True)
    requested_ip_count = models.PositiveSmallIntegerField('需求IP数', null=True, blank=True)
    requested_quantity = models.PositiveSmallIntegerField(
        '需求数量',
        default=1,
        help_text='同一规格需要交付的服务实例数量',
    )
    requirements = models.TextField('需求描述', blank=True)
    request_snapshot = models.JSONField(
        '客户请求快照',
        default=dict,
        blank=True,
        help_text='保存客户门户或外部接口提交时的产品、规格与报价快照，供审计追溯。',
    )

    # ── VPS 专属需求 ──────────────────────────────────────────
    requested_vcpu = models.PositiveSmallIntegerField('需求 vCPU', null=True, blank=True)
    requested_ram_mb = models.PositiveIntegerField('需求内存(MB)', null=True, blank=True)
    requested_disk_gb = models.PositiveIntegerField('需求硬盘(GB)', null=True, blank=True)
    requested_os = models.CharField(
        '期望操作系统', max_length=64, blank=True,
        help_text='如 Ubuntu 22.04 / CentOS 7 / Debian 12',
    )
    requested_os_template = models.ForeignKey(
        'system_manager.SystemOsTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_orders',
        verbose_name='期望系统模版',
    )

    # ── 边缘加速通用需求 ──────────────────────────────────────
    # GRE 专属
    edge_local_ip = models.GenericIPAddressField(
        '本端 IP (GRE)', null=True, blank=True,
        help_text='GRE 隧道本端地址',
    )
    edge_remote_ip = models.GenericIPAddressField(
        '对端 IP (GRE)', null=True, blank=True,
        help_text='GRE 隧道对端地址',
    )
    # WireGuard 专属
    edge_wg_port = models.PositiveIntegerField(
        'WG 监听端口', null=True, blank=True, default=51820,
    )
    edge_wg_keepalive = models.PositiveSmallIntegerField(
        'WG 保活间隔(s)', null=True, blank=True, default=25,
    )
    edge_wg_allowed_ips = models.TextField(
        'WG 允许 IP 段', blank=True,
        help_text='每行一条，如 0.0.0.0/0 或 10.0.0.0/8',
    )
    edge_wg_client_pubkey = models.CharField(
        '客户端公钥 (WG)', max_length=64, blank=True,
    )
    # Socks5 专属
    edge_socks5_port = models.PositiveIntegerField(
        'Socks5 端口', null=True, blank=True, default=1080,
    )
    edge_socks5_user = models.CharField(
        'Socks5 认证用户名', max_length=64, blank=True,
    )
    edge_socks5_pass = models.CharField(
        'Socks5 认证密码', max_length=128, blank=True,
    )

    # ── 服务期限与费用 ────────────────────────────────────────
    service_start = models.DateField('服务开始日', null=True, blank=True)
    service_end = models.DateField('服务结束日', null=True, blank=True)
    billing_cycle = models.CharField(
        '计费周期', max_length=16,
        choices=BillingCycle.choices, default=BillingCycle.MONTHLY,
        blank=True,
    )
    quoted_price = models.DecimalField('报价', max_digits=10, decimal_places=2, null=True, blank=True)
    fx_quote_currency = models.CharField(
        '报价币种快照', max_length=8, blank=True,
        help_text='下单时锁定的报价/产品币种',
    )
    fx_snapshot = models.JSONField(
        '下单汇率快照', default=dict, blank=True,
        help_text='锁定当时的 to_cny 汇率表，供后续支付换算与月结出账使用',
    )
    fx_locked_at = models.DateTimeField('汇率锁定时间', null=True, blank=True)
    expected_delivery_at = models.DateTimeField(
        '预计交付时间', null=True, blank=True,
        help_text='下单时根据产品预估开通时间或节点交付能力计算',
    )

    # ── 审批 ──────────────────────────────────────────────────
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='审批人', related_name='approved_orders',
    )
    approved_at = models.DateTimeField('审批时间', null=True, blank=True)
    reject_reason = models.CharField('驳回原因', max_length=512, blank=True)

    # ── 自动开通 ──────────────────────────────────────────────
    class AutoProvisionStatus(models.TextChoices):
        NONE = 'none', '未触发'
        PAYMENT_PENDING = 'payment_pending', '等待付款'
        PROVISIONING = 'provisioning', '自动开通中'
        PAID_PENDING = 'paid_pending', '已付款待人工'
        SUCCESS = 'success', '自动开通成功'
        INSUFFICIENT = 'insufficient', '资源不足待人工'
        FAILED = 'failed', '自动开通失败'

    auto_provision_status = models.CharField(
        '自动开通状态', max_length=16,
        choices=AutoProvisionStatus.choices, default=AutoProvisionStatus.NONE,
    )
    auto_provision_message = models.TextField('自动开通信息', blank=True)

    # ── 操作人 / 处理人 ───────────────────────────────────────
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='创建人/提交人', related_name='created_orders',
    )
    handler = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='当前处理人', related_name='handling_orders',
    )
    assigned_at = models.DateTimeField('指派时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务工单'
        verbose_name_plural = '服务工单列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['customer']),
            models.Index(fields=['order_type']),
            models.Index(fields=['business_type']),
            models.Index(fields=['current_stage']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'[{self.order_no}] {self.get_business_type_display()} - {self.customer.name}'

    def save(self, *args, **kwargs):
        if not self.order_no:
            prefix = 'SO'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = ServiceOrder.objects.filter(
                order_no__startswith=f'{prefix}{date_str}'
            ).order_by('-order_no').first()
            seq = int(last.order_no[-4:]) + 1 if last else 1
            self.order_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def is_vps(self):
        return self.business_type == self.BusinessType.VPS

    @property
    def is_edge(self):
        return self.business_type in (
            self.BusinessType.EDGE_GRE,
            self.BusinessType.EDGE_WG,
            self.BusinessType.EDGE_SOCKS5,
        )


class PopCapacityReservation(models.Model):
    """POP 容量预占账本：下单锁定 → 开通消耗 → 失败/取消/过期释放。"""

    class Status(models.TextChoices):
        HELD = 'held', '预占'
        CONSUMED = 'consumed', '已消耗'
        RELEASED = 'released', '已释放'

    pop_point = models.ForeignKey(
        'resource_manager.PopPoint',
        on_delete=models.CASCADE,
        verbose_name='POP 节点',
        related_name='capacity_reservations',
    )
    service_order = models.ForeignKey(
        ServiceOrder,
        on_delete=models.CASCADE,
        verbose_name='服务工单',
        related_name='capacity_reservations',
    )
    status = models.CharField(
        '状态',
        max_length=16,
        choices=Status.choices,
        default=Status.HELD,
    )
    cpu_cores = models.PositiveIntegerField('预占 vCPU', default=0)
    ram_mb = models.PositiveIntegerField('预占内存(MB)', default=0)
    disk_gb = models.PositiveIntegerField('预占硬盘(GB)', default=0)
    bandwidth_mbps = models.PositiveIntegerField('预占带宽(Mbps)', default=0)
    ip_count = models.PositiveIntegerField('预占 IP 数', default=0)
    expires_at = models.DateTimeField('预占到期时间', null=True, blank=True)
    held_at = models.DateTimeField('预占时间', auto_now_add=True)
    consumed_at = models.DateTimeField('消耗时间', null=True, blank=True)
    released_at = models.DateTimeField('释放时间', null=True, blank=True)
    release_reason = models.CharField('释放原因', max_length=255, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'POP 容量预占'
        verbose_name_plural = 'POP 容量预占'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['service_order'],
                condition=models.Q(status='held'),
                name='uniq_held_capacity_reservation_per_order',
            ),
        ]
        indexes = [
            models.Index(fields=['pop_point', 'status']),
            models.Index(fields=['status', 'expires_at']),
            models.Index(fields=['service_order', 'status']),
        ]

    def __str__(self):
        return f'{self.service_order.order_no} @ {self.pop_point_id} ({self.status})'

    def spec_dict(self) -> dict:
        return {
            'cpu_cores': int(self.cpu_cores or 0),
            'ram_mb': int(self.ram_mb or 0),
            'disk_gb': int(self.disk_gb or 0),
            'bandwidth_mbps': int(self.bandwidth_mbps or 0),
            'ip_count': int(self.ip_count or 0),
        }


# ─────────────────────────────────────────────────────────────
# 代理模型：用于 Admin 按业务类型分离展示
# ─────────────────────────────────────────────────────────────

class VPSServiceOrder(ServiceOrder):
    """VPS 服务工单（代理模型）"""
    class Meta:
        proxy = True
        verbose_name = 'VPS 服务工单'
        verbose_name_plural = 'VPS 服务工单列表'


class EdgeServiceOrder(ServiceOrder):
    """边缘加速工单（代理模型，含 GRE / WireGuard / Socks5）"""
    class Meta:
        proxy = True
        verbose_name = '边缘加速工单'
        verbose_name_plural = '边缘加速工单列表'


# ═══════════════════════════════════════════════════════════════
# § 3  服务实例（已分配的具体资源）
# ═══════════════════════════════════════════════════════════════

class ServiceInquiry(models.Model):
    """官网访客提交的咨询与测试申请，由服务团队统一跟进。"""

    class InquiryType(models.TextChoices):
        HELP = 'help', '需要帮助'
        TRIAL = 'trial', '申请测试'

    class InquiryStatus(models.TextChoices):
        NEW = 'new', '待处理'
        PROCESSING = 'processing', '跟进中'
        REPLIED = 'replied', '已回复'
        CLOSED = 'closed', '已关闭'

    inquiry_no = models.CharField('咨询编号', max_length=32, unique=True, editable=False)
    inquiry_type = models.CharField('咨询类型', max_length=16, choices=InquiryType.choices)
    status = models.CharField('处理状态', max_length=16, choices=InquiryStatus.choices, default=InquiryStatus.NEW)
    contact_name = models.CharField('联系人', max_length=64)
    company_name = models.CharField('公司/组织', max_length=128, blank=True)
    phone = models.CharField('联系电话', max_length=32, blank=True)
    email = models.EmailField('联系邮箱', blank=True)
    preferred_contact = models.CharField('偏好联系方式', max_length=32, blank=True)
    requirements = models.TextField('需求内容')
    source_url = models.CharField('提交页面', max_length=512, blank=True)
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='service_inquiries',
        verbose_name='关联客户',
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_service_inquiries',
        verbose_name='提交账号',
    )
    related_order = models.ForeignKey(
        ServiceOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='source_inquiries',
        verbose_name='转入服务工单',
    )
    converted_at = models.DateTimeField('转工单时间', null=True, blank=True)
    handler = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='handled_service_inquiries', verbose_name='处理人',
    )
    reply_content = models.TextField('回复内容', blank=True)
    replied_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='replied_service_inquiries', verbose_name='回复人',
    )
    replied_at = models.DateTimeField('回复时间', null=True, blank=True)
    created_at = models.DateTimeField('提交时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务咨询'
        verbose_name_plural = '服务咨询'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['inquiry_type', 'created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.inquiry_no:
            self.inquiry_no = f'INQ{timezone.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.inquiry_no} · {self.contact_name}'


class ServiceInquiryReply(models.Model):
    """官网咨询的每次跟进记录，保留回复、内部备注与指派历史。"""

    class EntryType(models.TextChoices):
        REPLY = 'reply', '客户回复'
        NOTE = 'note', '内部跟进'
        ASSIGN = 'assign', '指派处理人'
        CONVERT = 'convert', '转服务工单'
        CONVERT_TICKET = 'convert_ticket', '转技术工单'

    inquiry = models.ForeignKey(
        ServiceInquiry,
        on_delete=models.CASCADE,
        verbose_name='关联咨询',
        related_name='reply_records',
    )
    entry_type = models.CharField(
        '记录类型',
        max_length=16,
        choices=EntryType.choices,
        default=EntryType.REPLY,
    )
    content = models.TextField('内容')
    status_after = models.CharField(
        '操作后状态',
        max_length=16,
        choices=ServiceInquiry.InquiryStatus.choices,
        default=ServiceInquiry.InquiryStatus.PROCESSING,
    )
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='操作人',
        related_name='service_inquiry_reply_records',
    )
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        verbose_name = '官网咨询跟进记录'
        verbose_name_plural = '官网咨询跟进记录'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['inquiry', 'created_at']),
            models.Index(fields=['entry_type', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.get_entry_type_display()}] {self.inquiry.inquiry_no}'


class SupportTicket(models.Model):
    """客户技术工单，与服务开通工单分离；带 SLA 响应/解决时效。"""

    class Category(models.TextChoices):
        TECHNICAL = 'technical', '技术故障'
        ABUSE = 'abuse', '滥用投诉'
        BILLING = 'billing', '财务'
        ACCESS = 'access', '接入'

    class Priority(models.TextChoices):
        P1 = 'p1', 'P1 紧急'
        P2 = 'p2', 'P2 高'
        P3 = 'p3', 'P3 普通'
        P4 = 'p4', 'P4 低'

    class Status(models.TextChoices):
        OPEN = 'open', '待处理'
        IN_PROGRESS = 'in_progress', '处理中'
        PENDING_CUSTOMER = 'pending_customer', '待客户回复'
        WAITING = 'waiting', '挂起'
        RESOLVED = 'resolved', '已解决'
        CLOSED = 'closed', '已关闭'

    class Source(models.TextChoices):
        PORTAL = 'portal', '客户门户'
        ADMIN = 'admin', '管理后台'
        INQUIRY = 'inquiry', '官网咨询转入'

    ticket_no = models.CharField('工单编号', max_length=32, unique=True, editable=False)
    customer = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.PROTECT,
        related_name='support_tickets',
        verbose_name='客户',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_support_tickets',
        verbose_name='创建人',
    )
    category = models.CharField(
        '分类', max_length=16, choices=Category.choices, default=Category.TECHNICAL,
    )
    priority = models.CharField(
        '优先级', max_length=8, choices=Priority.choices, default=Priority.P3,
    )
    status = models.CharField(
        '状态', max_length=20, choices=Status.choices, default=Status.OPEN,
    )
    subject = models.CharField('主题', max_length=200)
    description = models.TextField('问题描述')
    related_instance = models.ForeignKey(
        'service_manager.ServiceInstance',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_tickets',
        verbose_name='关联实例',
    )
    related_inquiry = models.OneToOneField(
        ServiceInquiry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='related_ticket',
        verbose_name='来源咨询',
    )
    source = models.CharField(
        '来源', max_length=16, choices=Source.choices, default=Source.PORTAL,
    )
    handler = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='handled_support_tickets',
        verbose_name='处理人',
    )
    first_response_due = models.DateTimeField('首次响应截止', null=True, blank=True)
    resolve_due = models.DateTimeField('解决截止', null=True, blank=True)
    first_responded_at = models.DateTimeField('首次响应时间', null=True, blank=True)
    resolved_at = models.DateTimeField('解决时间', null=True, blank=True)
    closed_at = models.DateTimeField('关闭时间', null=True, blank=True)
    escalated_at = models.DateTimeField('升级时间', null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField('升级级别', default=0)
    sla_response_breached = models.BooleanField('响应已超时', default=False)
    sla_resolve_breached = models.BooleanField('解决已超时', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '技术工单'
        verbose_name_plural = '技术工单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'priority', 'created_at']),
            models.Index(fields=['customer', 'created_at']),
            models.Index(fields=['first_response_due']),
            models.Index(fields=['resolve_due']),
        ]

    def save(self, *args, **kwargs):
        if not self.ticket_no:
            self.ticket_no = f'ST{timezone.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ticket_no} · {self.subject}'

    @property
    def is_open(self) -> bool:
        return self.status not in {self.Status.RESOLVED, self.Status.CLOSED}


class SupportTicketMessage(models.Model):
    """技术工单往来；内部备注不对客户可见。"""

    class AuthorType(models.TextChoices):
        CUSTOMER = 'customer', '客户'
        STAFF = 'staff', '客服'

    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name='messages',
        verbose_name='工单',
    )
    author_type = models.CharField(
        '作者类型', max_length=16, choices=AuthorType.choices,
    )
    is_internal = models.BooleanField('内部备注', default=False)
    content = models.TextField('内容')
    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_ticket_messages',
        verbose_name='作者',
    )
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        verbose_name = '技术工单消息'
        verbose_name_plural = '技术工单消息'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['ticket', 'created_at']),
        ]

    def __str__(self):
        return f'{self.ticket.ticket_no} · {self.get_author_type_display()}'


class ServiceInstance(models.Model):
    """
    服务实例 — 一个工单可对应多个实例（如 1 个 VPS + 1 个 EdgeAcceleration）
    实例与资源管理中的具体对象直接关联，并在状态变更时同步更新资源。
    """

    class InstanceStatus(models.TextChoices):
        PROVISIONING = 'provisioning', '开通中'
        ACTIVE = 'active', '运行中'
        SUSPENDED = 'suspended', '已暂停'
        TERMINATED = 'terminated', '已终止'
        EXPIRED = 'expired', '已过期'
        ERROR = 'error', '故障'

    class ServiceType(models.TextChoices):
        VPS = 'vps', 'VPS'
        EDGE_ACCELERATION = 'edge', '边缘加速'
        IP_ADDRESS = 'ip', 'IP 地址'
        BANDWIDTH = 'bandwidth', '带宽'

    instance_no = models.CharField('实例编号', max_length=32, unique=True, editable=False)
    service_order = models.ForeignKey(
        ServiceOrder,
        on_delete=models.PROTECT,
        verbose_name='所属工单', related_name='instances',
    )
    service_type = models.CharField('服务类型', max_length=16, choices=ServiceType.choices)
    status = models.CharField(
        '状态', max_length=16,
        choices=InstanceStatus.choices, default=InstanceStatus.PROVISIONING,
    )

    # ── 资源绑定（按类型设置对应 FK，其余为 NULL）──────────────
    vps = models.OneToOneField(
        'resource_manager.VPS',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联 VPS', related_name='service_instance',
    )
    edge = models.OneToOneField(
        'resource_manager.EdgeAcceleration',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联边缘加速', related_name='service_instance',
    )
    bandwidth_alloc = models.OneToOneField(
        'resource_manager.BandwidthAllocation',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联带宽分配', related_name='service_instance',
    )

    # IP 地址为多对多（一个实例可绑定多个 IP）
    ip_addresses = models.ManyToManyField(
        'resource_manager.IPAddress',
        blank=True,
        verbose_name='关联 IP 地址', related_name='service_instances',
    )

    # ── 规格快照（实例化时拍摄，记录历史）────────────────────────
    spec_snapshot = models.JSONField('规格快照', default=dict, blank=True,
                                     help_text='开通时的资源规格 JSON 快照')

    # ── 计费信息 ──────────────────────────────────────────────
    monthly_fee = models.DecimalField(
        '月费', max_digits=12, decimal_places=2, default=0,
        help_text='该实例整月费用（按所属币种）；为 0 时回退取工单报价',
    )
    fee_currency = models.CharField('费用币种', max_length=8, default='CNY',
                                    help_text='CNY 人民币 / USD 美元 / CREDIT 信誉额')
    is_trial = models.BooleanField('测试服务', default=False,
                                   help_text='测试期间默认不计入账单，可转入计费周期')
    trial_start = models.DateField('测试开始日', null=True, blank=True)
    trial_end = models.DateField('测试结束日', null=True, blank=True)
    billing_start_date = models.DateField(
        '计费起始日', null=True, blank=True,
        help_text='正式计费起始日期；测试转正后置为转入日期',
    )

    # ── 生命周期 ──────────────────────────────────────────────
    provisioned_at = models.DateTimeField('开通时间', null=True, blank=True)
    activated_at = models.DateTimeField('激活时间', null=True, blank=True)
    suspended_at = models.DateTimeField('暂停时间', null=True, blank=True)
    terminated_at = models.DateTimeField('终止时间', null=True, blank=True)
    expire_time = models.DateTimeField('到期时间', null=True, blank=True)
    grace_days = models.PositiveSmallIntegerField(
        '宽限天数', null=True, blank=True,
        help_text='空则使用系统参数 instance_grace_days；到期后到回收日的间隔',
    )
    suspend_at = models.DateTimeField(
        '计划停机时间', null=True, blank=True,
        help_text='默认等于到期时间；到期引擎在此时间后可暂停电源',
    )
    recycle_at = models.DateTimeField(
        '计划回收时间', null=True, blank=True,
        help_text='默认等于到期时间 + 宽限天数；宽限后可释放 IP 并回收',
    )

    provisioned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='开通人', related_name='provisioned_instances',
    )
    terminated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='终止人', related_name='terminated_instances',
    )

    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务实例'
        verbose_name_plural = '服务实例列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['service_type']),
            models.Index(fields=['service_order']),
            models.Index(fields=['expire_time']),
            models.Index(fields=['suspend_at']),
            models.Index(fields=['recycle_at']),
        ]

    def __str__(self):
        return f'[{self.instance_no}] {self.get_service_type_display()} - {self.service_order.customer.name}'

    def save(self, *args, **kwargs):
        if not self.instance_no:
            prefix = 'INS'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = ServiceInstance.objects.filter(
                instance_no__startswith=f'{prefix}{date_str}'
            ).order_by('-instance_no').first()
            seq = int(last.instance_no[-4:]) + 1 if last else 1
            self.instance_no = f'{prefix}{date_str}{seq:04d}'
        if self.expire_time and (not self.suspend_at or not self.recycle_at):
            from service_manager.expiry_engine import apply_schedule_to_instance
            if apply_schedule_to_instance(self, force=False):
                update_fields = kwargs.get('update_fields')
                if update_fields is not None:
                    merged = list(update_fields)
                    for field in ('suspend_at', 'recycle_at'):
                        if field not in merged:
                            merged.append(field)
                    kwargs['update_fields'] = merged
        super().save(*args, **kwargs)

    def effective_grace_days(self) -> int:
        from service_manager.expiry_engine import effective_grace_days
        return effective_grace_days(self)

    # ── 资源联动方法 ───────────────────────────────────────────

    @property
    def customer(self):
        return self.service_order.customer

    def _get_resource(self):
        """返回当前绑定的主资源对象"""
        return self.vps or self.edge or self.bandwidth_alloc

    # ── 计费相关 ──────────────────────────────────────────────

    def billed_resource(self):
        """返回该实例绑定的、可据产品计费的资源 (resource, kind)。"""
        if self.vps_id and self.vps:
            return self.vps, 'vps'
        if self.edge_id and self.edge:
            return self.edge, 'edge'
        if self.bandwidth_alloc_id and self.bandwidth_alloc:
            return self.bandwidth_alloc, 'bandwidth'
        return None, None

    def product_billing(self):
        """若绑定资源已关联产品，返回产品计费明细（基础+超出）；否则 None。"""
        resource, kind = self.billed_resource()
        if resource is None or getattr(resource, 'product_id', None) is None:
            return None
        try:
            from product_manager import services as product_services
            return product_services.compute_resource_billing(resource, kind)
        except Exception:
            return None

    def effective_monthly_fee(self):
        """整月费用。

        优先级：实例自身月费(人工覆盖) > 关联产品定价(基础+超出) > 工单报价。
        """
        from decimal import Decimal
        if self.monthly_fee and self.monthly_fee > 0:
            return Decimal(str(self.monthly_fee))
        pb = self.product_billing()
        if pb is not None:
            return Decimal(str(pb['monthly_price']))
        quoted = getattr(self.service_order, 'quoted_price', None)
        return Decimal(str(quoted)) if quoted else Decimal('0')

    def effective_fee_currency(self):
        """整月费用对应币种。人工月费用 fee_currency；产品定价用产品币种。"""
        if self.monthly_fee and self.monthly_fee > 0:
            return (self.fee_currency or 'CNY').upper()
        pb = self.product_billing()
        if pb is not None:
            return (pb['currency'] or 'CNY').upper()
        return (self.fee_currency or 'CNY').upper()

    def billing_effective_start(self):
        """正式计费起始日期：计费起始日 > 激活日 > 工单服务开始日。"""
        if self.billing_start_date:
            return self.billing_start_date
        if self.activated_at:
            return self.activated_at.date()
        if self.provisioned_at:
            return self.provisioned_at.date()
        return getattr(self.service_order, 'service_start', None)

    def service_end_date(self):
        """服务结束日期：终止日 > 工单服务结束日 > 到期时间。"""
        if self.terminated_at:
            return self.terminated_at.date()
        if getattr(self.service_order, 'service_end', None):
            return self.service_order.service_end
        if self.expire_time:
            return self.expire_time.date()
        return None

    def convert_to_billing(self, operator=None, billing_start=None):
        """把测试服务转入正式计费周期。"""
        before = service_snapshot(self)
        before_fee = {
            'is_trial': self.is_trial,
            'monthly_fee': str(self.effective_monthly_fee()),
            'currency': self.effective_fee_currency(),
            'billing_start_date': str(self.billing_start_date) if self.billing_start_date else None,
        }
        self.is_trial = False
        self.trial_end = self.trial_end or timezone.now().date()
        self.billing_start_date = billing_start or timezone.now().date()
        self.save(update_fields=['is_trial', 'trial_end', 'billing_start_date', 'updated_at'])
        after = service_snapshot(self)
        after_fee = {
            'is_trial': self.is_trial,
            'monthly_fee': str(self.effective_monthly_fee()),
            'currency': self.effective_fee_currency(),
            'billing_start_date': str(self.billing_start_date) if self.billing_start_date else None,
        }
        ServiceAllocation.objects.create(
            service_instance=self,
            action_type=ServiceAllocation.ActionType.MODIFY,
            description=f'测试服务转入计费周期，计费起始 {self.billing_start_date}',
            before_state={**before, 'fee': before_fee},
            after_state={**after, 'fee': after_fee},
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.CONVERT_BILLING,
            description=f'测试服务 [{self.instance_no}] 转入计费周期，起始 {self.billing_start_date}',
            service_instance=self,
            service_order=self.service_order,
            old_value=before_fee,
            new_value=after_fee,
        )

    def activate(self, operator=None):
        """激活实例：更新资源状态并关联客户"""
        now = timezone.now()
        customer = self.customer
        before = service_snapshot(self)

        if self.edge and not self.edge.node_pushed:
            raise ValueError('边缘加速配置尚未成功下发到节点，不能激活服务实例')

        if self.vps:
            self.vps.customer = customer
            self.vps.start_time = self.vps.start_time or now
            self.vps.status = 'running'
            self.vps.save(update_fields=['customer', 'start_time', 'status', 'updated_at'])

        if self.edge:
            self.edge.customer = customer
            self.edge.start_time = self.edge.start_time or now
            self.edge.status = 'active'
            self.edge.save(update_fields=['customer', 'start_time', 'status', 'updated_at'])

        if self.bandwidth_alloc:
            self.bandwidth_alloc.is_active = True
            self.bandwidth_alloc.save(update_fields=['is_active', 'updated_at'])

        # 更新 IP 状态
        for ip in self.ip_addresses.all():
            if ip.status != 'assigned':
                ip.status = 'assigned'
                ip.assigned_at = now
                if self.vps:
                    ip.assigned_vps = self.vps
                if self.edge:
                    ip.assigned_edge = self.edge
                ip.save(update_fields=['status', 'assigned_at', 'assigned_vps', 'assigned_edge'])

        self.status = self.InstanceStatus.ACTIVE
        self.activated_at = now
        self.provisioned_at = self.provisioned_at or now
        if operator:
            self.provisioned_by = operator
        if not self.spec_snapshot:
            self.spec_snapshot = service_snapshot(self)
            self.save(update_fields=[
                'status', 'activated_at', 'provisioned_at', 'provisioned_by',
                'spec_snapshot', 'updated_at',
            ])
        else:
            self.save(update_fields=['status', 'activated_at', 'provisioned_at', 'provisioned_by', 'updated_at'])

        after = service_snapshot(self)
        ServiceAllocation.objects.create(
            service_instance=self,
            action_type=ServiceAllocation.ActionType.ACTIVATE,
            description=f'激活服务实例 {self.instance_no}',
            before_state=before,
            after_state=after,
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.ACTIVATE,
            description=f'激活服务实例 [{self.instance_no}]',
            service_instance=self,
            service_order=self.service_order,
            old_value=before,
            new_value=after,
        )
        sync_service_order_delivery_state(
            self.service_order,
            operator=operator,
            reason=f'实例 {self.instance_no} 已激活',
        )

    def suspend(self, operator=None, reason=''):
        """暂停实例：冻结资源"""
        now = timezone.now()
        before = service_snapshot(self)

        if self.vps:
            self.vps.status = 'suspended'
            self.vps.save(update_fields=['status', 'updated_at'])

        if self.edge:
            self.edge.status = 'stopped'
            self.edge.save(update_fields=['status', 'updated_at'])

        self.status = self.InstanceStatus.SUSPENDED
        self.suspended_at = now
        self.save(update_fields=['status', 'suspended_at', 'updated_at'])

        after = service_snapshot(self)
        ServiceAllocation.objects.create(
            service_instance=self,
            action_type=ServiceAllocation.ActionType.SUSPEND,
            description=f'暂停服务实例 {self.instance_no}，原因：{reason}',
            before_state=before,
            after_state=after,
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.SUSPEND,
            description=f'暂停服务实例 [{self.instance_no}] 原因：{reason}',
            service_instance=self,
            service_order=self.service_order,
            old_value=before,
            new_value=after,
        )
        sync_service_order_delivery_state(
            self.service_order,
            operator=operator,
            reason=f'实例 {self.instance_no} 已暂停',
        )

    def resume(self, operator=None):
        """恢复暂停的实例"""
        before = service_snapshot(self)

        if self.vps:
            self.vps.status = 'running'
            self.vps.save(update_fields=['status', 'updated_at'])

        if self.edge:
            self.edge.status = 'active'
            self.edge.save(update_fields=['status', 'updated_at'])

        self.status = self.InstanceStatus.ACTIVE
        self.suspended_at = None
        self.save(update_fields=['status', 'suspended_at', 'updated_at'])

        after = service_snapshot(self)
        ServiceAllocation.objects.create(
            service_instance=self,
            action_type=ServiceAllocation.ActionType.RESUME,
            description=f'恢复服务实例 {self.instance_no}',
            before_state=before,
            after_state=after,
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.RESUME,
            description=f'恢复服务实例 [{self.instance_no}]',
            service_instance=self,
            service_order=self.service_order,
            old_value=before,
            new_value=after,
        )
        sync_service_order_delivery_state(
            self.service_order,
            operator=operator,
            reason=f'实例 {self.instance_no} 已恢复',
        )

    def terminate(self, operator=None, reason=''):
        """终止实例：释放全部关联资源"""
        now = timezone.now()
        before = service_snapshot(self)

        if self.vps:
            self.vps.customer = None
            self.vps.status = 'stopped'
            self.vps.expire_time = now
            self.vps.save(update_fields=['customer', 'status', 'expire_time', 'updated_at'])

        if self.edge:
            self.edge.customer = None
            self.edge.status = 'stopped'
            self.edge.expire_time = now
            self.edge.save(update_fields=['customer', 'status', 'expire_time', 'updated_at'])

        if self.bandwidth_alloc:
            self.bandwidth_alloc.is_active = False
            self.bandwidth_alloc.save(update_fields=['is_active', 'updated_at'])

        # 释放所有关联 IP
        for ip in self.ip_addresses.all():
            ip.status = 'available'
            ip.assigned_vps = None
            ip.assigned_edge = None
            ip.assigned_at = None
            ip.save(update_fields=['status', 'assigned_vps', 'assigned_edge', 'assigned_at'])

        self.status = self.InstanceStatus.TERMINATED
        self.terminated_at = now
        self.terminated_by = operator
        self.save(update_fields=['status', 'terminated_at', 'terminated_by', 'updated_at'])

        after = service_snapshot(self)
        ServiceAllocation.objects.create(
            service_instance=self,
            action_type=ServiceAllocation.ActionType.TERMINATE,
            description=f'终止服务实例 {self.instance_no}，原因：{reason}',
            before_state=before,
            after_state=after,
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.TERMINATE,
            description=f'终止服务实例 [{self.instance_no}] 原因：{reason}',
            service_instance=self,
            service_order=self.service_order,
            old_value=before,
            new_value=after,
        )
        sync_service_order_delivery_state(
            self.service_order,
            operator=operator,
            reason=f'实例 {self.instance_no} 已终止',
        )


class OsImage(models.Model):
    """门户重装可选的操作系统镜像目录。节点模板对接预留。"""

    class OSType(models.TextChoices):
        DEBIAN = 'debian', 'Debian'
        UBUNTU = 'ubuntu', 'Ubuntu'
        CENTOS = 'centos', 'CentOS'
        RHEL = 'rhel', 'RHEL'
        WINDOWS = 'windows', 'Windows Server'
        OTHER = 'other', '其他'

    code = models.CharField('镜像代码', max_length=32, unique=True)
    name = models.CharField('显示名称', max_length=128)
    os_type = models.CharField('系统类型', max_length=16, choices=OSType.choices)
    version = models.CharField('版本', max_length=64, blank=True)
    description = models.CharField('说明', max_length=255, blank=True)
    enabled = models.BooleanField('上架', default=True)
    sort_order = models.PositiveSmallIntegerField('排序', default=0)
    node_template_ref = models.CharField(
        '节点模板引用', max_length=255, blank=True,
        help_text='对应中心系统模版的备份文件名，开通时下发到 POP / PVE',
    )
    os_template = models.OneToOneField(
        'system_manager.SystemOsTemplate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='os_image',
        verbose_name='系统模版',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '操作系统镜像'
        verbose_name_plural = '操作系统镜像'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.name} ({self.code})'


class InstanceSnapshot(models.Model):
    """VPS 快照档案。本步只记账，不调用 PVE qm snapshot。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '创建中'
        READY = 'ready', '可用'
        RESTORING = 'restoring', '恢复中'
        FAILED = 'failed', '失败'
        EXPIRED = 'expired', '已过期'
        DELETED = 'deleted', '已删除'

    snapshot_no = models.CharField('快照编号', max_length=32, unique=True, editable=False)
    vps = models.ForeignKey(
        'resource_manager.VPS',
        on_delete=models.CASCADE,
        related_name='self_service_snapshots',
        verbose_name='VPS',
    )
    service_instance = models.ForeignKey(
        ServiceInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='self_service_snapshots',
        verbose_name='服务实例',
    )
    name = models.CharField('名称', max_length=128)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    note = models.CharField('备注', max_length=255, blank=True)
    size_mb = models.PositiveIntegerField('大小(MB)', null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_instance_snapshots', verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    expires_at = models.DateTimeField('过期时间', null=True, blank=True)
    restored_at = models.DateTimeField('最近恢复时间', null=True, blank=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '实例快照'
        verbose_name_plural = '实例快照'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['vps', 'status', 'created_at']),
        ]

    def save(self, *args, **kwargs):
        if not self.snapshot_no:
            self.snapshot_no = f'SN{timezone.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.snapshot_no} · {self.name}'


class SelfServiceJob(models.Model):
    """门户重装 / 快照创建 / 快照恢复任务。节点执行预留。"""

    class JobType(models.TextChoices):
        REINSTALL = 'reinstall', '重装系统'
        SNAPSHOT_CREATE = 'snapshot_create', '创建快照'
        SNAPSHOT_RESTORE = 'snapshot_restore', '恢复快照'

    class Status(models.TextChoices):
        PENDING = 'pending', '待处理'
        RUNNING = 'running', '进行中'
        SUCCEEDED = 'succeeded', '已完成'
        FAILED = 'failed', '失败'
        CANCELLED = 'cancelled', '已取消'

    job_no = models.CharField('任务编号', max_length=32, unique=True, editable=False)
    job_type = models.CharField('类型', max_length=20, choices=JobType.choices)
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    vps = models.ForeignKey(
        'resource_manager.VPS',
        on_delete=models.CASCADE,
        related_name='self_service_jobs',
        verbose_name='VPS',
    )
    service_instance = models.ForeignKey(
        ServiceInstance,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='self_service_jobs',
        verbose_name='服务实例',
    )
    os_image = models.ForeignKey(
        OsImage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='self_service_jobs',
        verbose_name='目标镜像',
    )
    snapshot = models.ForeignKey(
        InstanceSnapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='jobs',
        verbose_name='关联快照',
    )
    progress = models.PositiveSmallIntegerField('进度', default=0)
    request_payload = models.JSONField('请求参数', default=dict, blank=True)
    result_message = models.CharField('结果摘要', max_length=255, blank=True)
    node_job_id = models.CharField('节点任务号', max_length=64, blank=True)
    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='requested_self_service_jobs', verbose_name='申请人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    started_at = models.DateTimeField('开始时间', null=True, blank=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '自助任务'
        verbose_name_plural = '自助任务'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['vps', 'status', 'created_at']),
            models.Index(fields=['job_type', 'status']),
        ]

    def save(self, *args, **kwargs):
        if not self.job_no:
            self.job_no = f'SS{timezone.now():%Y%m%d}{uuid.uuid4().hex[:6].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.job_no} · {self.get_job_type_display()}'

    @property
    def is_open(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.RUNNING}


class ServiceExpiryAction(models.Model):
    """实例到期引擎的幂等动作日志，同一到期日同一动作只执行一次。"""

    class Action(models.TextChoices):
        REMIND = 'remind', '催缴提醒'
        EXPIRED = 'expired', '已到期通知'
        SUSPEND = 'suspend', '到期停机'
        RECYCLE = 'recycle', '宽限后回收'

    instance = models.ForeignKey(
        ServiceInstance,
        on_delete=models.CASCADE,
        related_name='expiry_actions',
        verbose_name='服务实例',
    )
    action = models.CharField('动作', max_length=16, choices=Action.choices)
    lead_days = models.PositiveSmallIntegerField(
        '提前天数', default=0,
        help_text='催缴提醒对应 T-N 的 N；到期/停机/回收为 0',
    )
    due_on = models.DateField('对应到期日')
    payload = models.JSONField('上下文', default=dict, blank=True)
    created_at = models.DateTimeField('执行时间', auto_now_add=True)

    class Meta:
        verbose_name = '实例到期动作'
        verbose_name_plural = '实例到期动作'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['instance', 'action', 'due_on', 'lead_days'],
                name='uniq_instance_expiry_action',
            ),
        ]
        indexes = [
            models.Index(fields=['action', 'due_on']),
            models.Index(fields=['instance', 'created_at']),
        ]

    def __str__(self):
        return f'{self.instance.instance_no} {self.action} T-{self.lead_days} {self.due_on}'


# ═══════════════════════════════════════════════════════════════
# § 4  资源分配/回收明细记录
# ═══════════════════════════════════════════════════════════════

class ServiceAllocation(models.Model):
    """资源分配、暂停、恢复、终止的每步明细"""

    class ActionType(models.TextChoices):
        ALLOCATE = 'allocate', '分配资源'
        ACTIVATE = 'activate', '激活服务'
        MODIFY = 'modify', '变更规格'
        SUSPEND = 'suspend', '暂停服务'
        RESUME = 'resume', '恢复服务'
        TERMINATE = 'terminate', '终止/回收'
        RENEW = 'renew', '续期'
        IP_ADD = 'ip_add', '增加 IP'
        IP_REMOVE = 'ip_remove', '移除 IP'
        BW_CHANGE = 'bw_change', '带宽变更'
        OTHER = 'other', '其他'

    service_instance = models.ForeignKey(
        ServiceInstance,
        on_delete=models.CASCADE,
        verbose_name='服务实例', related_name='allocations',
    )
    action_type = models.CharField('操作类型', max_length=16, choices=ActionType.choices)
    description = models.CharField('操作描述', max_length=512)

    # 资源快照（操作前后）
    before_state = models.JSONField('操作前状态', null=True, blank=True)
    after_state = models.JSONField('操作后状态', null=True, blank=True)

    # 操作涉及的具体资源（可选，用于精确追踪）
    resource_type = models.CharField('资源类型', max_length=32, blank=True,
                                      help_text='如 VPS / EdgeAcceleration / IPAddress')
    resource_id = models.PositiveIntegerField('资源 ID', null=True, blank=True)
    resource_repr = models.CharField('资源描述', max_length=256, blank=True)

    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='操作人',
    )
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        verbose_name = '分配记录'
        verbose_name_plural = '分配记录'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service_instance', 'created_at']),
            models.Index(fields=['action_type']),
        ]

    def __str__(self):
        return f'[{self.get_action_type_display()}] {self.service_instance.instance_no} - {self.description[:40]}'


# ═══════════════════════════════════════════════════════════════
# § 5  服务变更申请与记录
# ═══════════════════════════════════════════════════════════════

class ServiceChangeRecord(models.Model):
    """服务变更申请（升降配、续期、IP 变更、带宽调整等）"""

    class ChangeType(models.TextChoices):
        UPGRADE = 'upgrade', '升配'
        DOWNGRADE = 'downgrade', '降配'
        EXTEND = 'extend', '续期'
        IP_CHANGE = 'ip_change', 'IP 变更'
        BW_CHANGE = 'bw_change', '带宽调整'
        PROTOCOL_CHANGE = 'protocol_change', '协议变更'
        MIGRATE = 'migrate', '迁移节点'
        OTHER = 'other', '其他变更'

    class ChangeStatus(models.TextChoices):
        PENDING = 'pending', '待审批'
        APPROVED = 'approved', '已审批'
        IN_PROGRESS = 'in_progress', '变更中'
        COMPLETED = 'completed', '已完成'
        ROLLED_BACK = 'rolled_back', '已回滚'
        REJECTED = 'rejected', '已驳回'
        CANCELLED = 'cancelled', '已取消'

    change_no = models.CharField('变更单号', max_length=32, unique=True, editable=False)
    service_instance = models.ForeignKey(
        ServiceInstance,
        on_delete=models.PROTECT,
        verbose_name='服务实例', related_name='change_records',
    )
    change_type = models.CharField('变更类型', max_length=20, choices=ChangeType.choices)
    status = models.CharField(
        '变更状态', max_length=16,
        choices=ChangeStatus.choices, default=ChangeStatus.PENDING,
    )

    # 变更前后规格
    before_spec = models.JSONField('变更前规格', default=dict, blank=True)
    after_spec = models.JSONField('变更后规格', default=dict, blank=True)

    # 变更说明
    change_reason = models.CharField('变更原因', max_length=512)
    change_detail = models.TextField('变更详情', blank=True)

    # 价格调整
    price_delta = models.DecimalField('价格差额', max_digits=10, decimal_places=2,
                                       null=True, blank=True,
                                       help_text='正数为追加费用，负数为退款')

    # 计划/实际执行时间
    scheduled_at = models.DateTimeField('计划执行时间', null=True, blank=True)
    executed_at = models.DateTimeField('实际执行时间', null=True, blank=True)
    rolled_back_at = models.DateTimeField('回滚时间', null=True, blank=True)

    # ── 关联工单（门户变更会同时生成工单）────────────────────
    related_order = models.ForeignKey(
        ServiceOrder,
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='关联工单', related_name='change_records',
    )

    # 审批
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='审批人', related_name='approved_changes',
    )
    approved_at = models.DateTimeField('审批时间', null=True, blank=True)
    reject_reason = models.CharField('驳回原因', max_length=512, blank=True)

    requested_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='申请人', related_name='requested_changes',
    )
    handler = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='处理人', related_name='handling_changes',
    )
    executed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='执行人', related_name='executed_changes',
    )
    rolled_back_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='回滚人', related_name='rolled_back_changes',
    )
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务变更记录'
        verbose_name_plural = '服务变更记录'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service_instance', 'status']),
            models.Index(fields=['change_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'[{self.change_no}] {self.get_change_type_display()} - {self.service_instance.instance_no}'

    def save(self, *args, **kwargs):
        if not self.change_no:
            prefix = 'CHG'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = ServiceChangeRecord.objects.filter(
                change_no__startswith=f'{prefix}{date_str}'
            ).order_by('-change_no').first()
            seq = int(last.change_no[-4:]) + 1 if last else 1
            self.change_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 5b  服务全生命周期阶段记录
# ═══════════════════════════════════════════════════════════════

class ServiceLifecycleRecord(models.Model):
    """
    记录客户服务从下单→需求→测试→使用→变更→终止各阶段的责任人与资源/资费快照。
    """

    class StageStatus(models.TextChoices):
        PENDING = 'pending', '待处理'
        IN_PROGRESS = 'in_progress', '处理中'
        COMPLETED = 'completed', '已完成'
        REJECTED = 'rejected', '已驳回'
        SKIPPED = 'skipped', '已跳过'

    service_order = models.ForeignKey(
        ServiceOrder, on_delete=models.CASCADE,
        verbose_name='服务工单', related_name='lifecycle_records',
    )
    service_instance = models.ForeignKey(
        ServiceInstance, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='服务实例', related_name='lifecycle_records',
    )
    change_record = models.ForeignKey(
        ServiceChangeRecord, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联变更单', related_name='lifecycle_records',
    )
    stage = models.CharField(
        '阶段', max_length=20, choices=ServiceOrder.LifecycleStage.choices,
    )
    status = models.CharField(
        '阶段状态', max_length=16,
        choices=StageStatus.choices, default=StageStatus.PENDING,
    )
    title = models.CharField('阶段标题', max_length=128, blank=True)
    detail = models.TextField('阶段详情', blank=True)

    submitted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='提交人', related_name='submitted_lifecycle_stages',
    )
    handled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='处理人', related_name='handled_lifecycle_stages',
    )
    approved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='审批人', related_name='approved_lifecycle_stages',
    )

    submitted_at = models.DateTimeField('提交时间', null=True, blank=True)
    started_at = models.DateTimeField('开始处理时间', null=True, blank=True)
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    approved_at = models.DateTimeField('审批时间', null=True, blank=True)

    resource_before = models.JSONField('资源使用情况(前)', default=dict, blank=True)
    resource_after = models.JSONField('资源使用情况(后)', default=dict, blank=True)
    fee_snapshot = models.JSONField(
        '资费快照', default=dict, blank=True,
        help_text='月费、币种、差额、计费起止等',
    )
    reject_reason = models.CharField('驳回原因', max_length=512, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务生命周期阶段'
        verbose_name_plural = '服务生命周期阶段'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['service_order', 'stage']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['service_instance', 'stage']),
        ]

    def __str__(self):
        return f'[{self.service_order.order_no}] {self.get_stage_display()} - {self.get_status_display()}'


# ═══════════════════════════════════════════════════════════════
# § 5c  外部开通/对接 API 钩子（预留）
# ═══════════════════════════════════════════════════════════════

class ServiceProvisionHook(models.Model):
    """
    当本地资源无法自动开通、需对接第三方 API 时的钩子配置。
    实际调用由 hooks.dispatch_provision_hook 处理；此处仅做配置与审计预留。
    """

    class HookEvent(models.TextChoices):
        ORDER_APPROVED = 'order_approved', '工单审批通过'
        PROVISION = 'provision', '开通资源'
        CHANGE_EXECUTE = 'change_execute', '执行变更'
        TERMINATE = 'terminate', '终止服务'
        SUSPEND = 'suspend', '暂停服务'
        RESUME = 'resume', '恢复服务'
        CUSTOM = 'custom', '自定义'

    class HookType(models.TextChoices):
        WEBHOOK = 'webhook', 'Webhook HTTP'
        INTERNAL = 'internal', '内部适配器'
        MANUAL = 'manual', '仅标记待人工对接'

    name = models.CharField('钩子名称', max_length=64)
    event = models.CharField('触发事件', max_length=32, choices=HookEvent.choices)
    hook_type = models.CharField(
        '对接类型', max_length=16,
        choices=HookType.choices, default=HookType.WEBHOOK,
    )
    business_types = models.CharField(
        '适用业务类型', max_length=256, blank=True,
        help_text='逗号分隔，如 vps,edge_gre；留空表示全部',
    )
    endpoint_url = models.URLField('接口地址', blank=True)
    http_method = models.CharField('HTTP 方法', max_length=10, default='POST')
    headers = models.JSONField('请求头', default=dict, blank=True)
    adapter_key = models.CharField(
        '内部适配器标识', max_length=64, blank=True,
        help_text='hook_type=internal 时使用，对应 hooks 中注册的适配器名',
    )
    is_active = models.BooleanField('启用', default=True)
    timeout_seconds = models.PositiveSmallIntegerField('超时(秒)', default=15)
    remark = models.CharField('备注', max_length=256, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '服务开通钩子'
        verbose_name_plural = '服务开通钩子'
        ordering = ['event', 'name']

    def __str__(self):
        return f'{self.name} [{self.get_event_display()}]'

    def matches_business_type(self, business_type: str) -> bool:
        if not self.business_types.strip():
            return True
        allowed = {x.strip() for x in self.business_types.split(',') if x.strip()}
        return business_type in allowed


# ═══════════════════════════════════════════════════════════════
# § 5d  人工交付清单（Socks5 等无法自动下发的工单）
# ═══════════════════════════════════════════════════════════════

class ServiceManualDelivery(models.Model):
    """人开工单的结构化交付清单；节点/运维用回传令牌更新进度。"""

    class Reason(models.TextChoices):
        SOCKS5 = 'socks5', 'Socks5 人工交付'
        MULTI_INSTANCE = 'multi_instance', '多实例逐项交付'
        BANDWIDTH = 'bandwidth', '独立带宽绑定'
        POP_MANUAL = 'pop_manual', '节点手动配置'
        STOCK = 'stock', '库存不足待人工交付'

    class Status(models.TextChoices):
        OPEN = 'open', '待处理'
        IN_PROGRESS = 'in_progress', '进行中'
        COMPLETED = 'completed', '已完成'
        CANCELLED = 'cancelled', '已取消'

    service_order = models.OneToOneField(
        ServiceOrder, on_delete=models.CASCADE,
        verbose_name='服务工单', related_name='manual_delivery',
    )
    reason = models.CharField('转人工原因', max_length=32, choices=Reason.choices)
    status = models.CharField(
        '清单状态', max_length=16,
        choices=Status.choices, default=Status.OPEN,
    )
    items = models.JSONField(
        '清单步骤', default=list, blank=True,
        help_text='[{key,label,required,done,done_at,source}]',
    )
    token_hash = models.CharField(
        '回传令牌 SHA-256', max_length=64, unique=True, editable=False,
    )
    token_prefix = models.CharField(
        '令牌前缀', max_length=16, editable=False, blank=True,
    )
    token_rotated_at = models.DateTimeField('令牌轮换时间', null=True, blank=True)
    last_callback_at = models.DateTimeField('最近回传时间', null=True, blank=True)
    last_callback_note = models.CharField('最近回传摘要', max_length=256, blank=True)
    resource_edge = models.ForeignKey(
        'resource_manager.EdgeAcceleration',
        on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='回传关联边缘资源',
        related_name='manual_deliveries',
    )
    completed_at = models.DateTimeField('完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '人工交付清单'
        verbose_name_plural = '人工交付清单'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['status', 'reason']),
        ]

    def __str__(self):
        return f'[{self.service_order.order_no}] {self.get_reason_display()} {self.get_status_display()}'


# ═══════════════════════════════════════════════════════════════
# § 6  服务操作日志
# ═══════════════════════════════════════════════════════════════

class ServiceOperationLog(models.Model):
    """服务管理模块统一操作日志"""

    class ActionType(models.TextChoices):
        CREATE_ORDER = 'create_order', '创建工单'
        SUBMIT_ORDER = 'submit_order', '提交工单'
        APPROVE_ORDER = 'approve_order', '审批工单'
        REJECT_ORDER = 'reject_order', '驳回工单'
        ASSIGN_HANDLER = 'assign_handler', '指派处理人'
        STAGE_CHANGE = 'stage_change', '阶段变更'
        PROVISION = 'provision', '开通资源'
        ACTIVATE = 'activate', '激活服务'
        SUSPEND = 'suspend', '暂停服务'
        RESUME = 'resume', '恢复服务'
        TERMINATE = 'terminate', '终止服务'
        RENEW = 'renew', '续期服务'
        CHANGE = 'change', '变更服务'
        START_TRIAL = 'start_trial', '开始测试'
        CONVERT_BILLING = 'convert_billing', '测试转正'
        ASSIGN_IP = 'assign_ip', '分配 IP'
        RELEASE_IP = 'release_ip', '释放 IP'
        BW_ADJUST = 'bw_adjust', '调整带宽'
        FEE_ADJUST = 'fee_adjust', '资费调整'
        OTHER = 'other', '其他'

    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='操作人',
    )
    action_type = models.CharField('操作类型', max_length=20, choices=ActionType.choices)
    description = models.CharField('操作描述', max_length=512)

    service_order = models.ForeignKey(
        ServiceOrder, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联工单', related_name='operation_logs',
    )
    service_instance = models.ForeignKey(
        ServiceInstance, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联实例', related_name='operation_logs',
    )

    old_value = models.JSONField('变更前', null=True, blank=True)
    new_value = models.JSONField('变更后', null=True, blank=True)
    ip_address = models.GenericIPAddressField('操作 IP', null=True, blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        verbose_name = '服务操作日志'
        verbose_name_plural = '服务操作日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action_type', 'created_at']),
            models.Index(fields=['service_order', 'created_at']),
            models.Index(fields=['service_instance', 'created_at']),
        ]

    def __str__(self):
        op = self.operator.username if self.operator else '系统'
        return f'[{self.get_action_type_display()}] {op} - {self.description[:40]}'


# ═══════════════════════════════════════════════════════════════
# § 工具函数
# ═══════════════════════════════════════════════════════════════

def json_safe(value, *, _seen=None):
    """把模型实例、日期、Decimal 等转成可写入 JSONField 的结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, Model):
        label = str(value)
        pk = getattr(value, 'pk', None)
        return {
            'id': pk,
            'model': value._meta.label,
            'label': label[:200],
        }
    if _seen is None:
        _seen = set()
    obj_id = id(value)
    if obj_id in _seen:
        return '<circular>'
    if isinstance(value, dict):
        _seen.add(obj_id)
        return {str(key): json_safe(item, _seen=_seen) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        _seen.add(obj_id)
        return [json_safe(item, _seen=_seen) for item in value]
    return str(value)


def _log_service(*, operator, action_type, description,
                 service_order=None, service_instance=None,
                 old_value=None, new_value=None, request=None):
    """内部日志工具，供模型方法和外部调用"""
    ip_address = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip_address = (
            x_forwarded_for.split(',')[0].strip()
            if x_forwarded_for
            else request.META.get('REMOTE_ADDR')
        )
    ServiceOperationLog.objects.create(
        operator=operator,
        action_type=action_type,
        description=description,
        service_order=service_order,
        service_instance=service_instance,
        old_value=json_safe(old_value),
        new_value=json_safe(new_value),
        ip_address=ip_address,
    )


def service_snapshot(instance):
    """获取服务实例的当前资源状态快照"""
    snap = {
        'instance_no': instance.instance_no,
        'service_type': instance.service_type,
        'status': instance.status,
    }
    if instance.vps_id:
        vps = instance.vps
        snap['vps'] = {
            'id': vps.pk, 'vps_no': vps.vps_no, 'name': vps.name,
            'vcpu': vps.vcpu, 'ram_mb': vps.ram_mb, 'disk_gb': vps.disk_gb,
            'status': vps.status, 'bandwidth_mbps': vps.bandwidth_mbps,
        }
    if instance.edge_id:
        edge = instance.edge
        snap['edge'] = {
            'id': edge.pk, 'edge_no': edge.edge_no, 'name': edge.name,
            'protocol': edge.protocol, 'status': edge.status,
            'bandwidth_mbps': edge.bandwidth_mbps,
        }
    if instance.bandwidth_alloc_id:
        bw = instance.bandwidth_alloc
        snap['bandwidth'] = {
            'id': bw.pk, 'allocated_mbps': bw.allocated_mbps, 'is_active': bw.is_active,
        }
    snap['ip_addresses'] = list(
        instance.ip_addresses.values('id', 'address', 'prefix_len', 'status')
    )
    return snap


def order_delivery_summary(order):
    """返回服务工单当前交付情况，供前端列表、详情和审计使用。"""
    instances = list(order.instances.all().order_by('created_at', 'id'))
    status_counts = {}
    type_counts = {}
    resources = []
    total_ips = 0
    total_bandwidth = 0
    for instance in instances:
        status_counts[instance.status] = status_counts.get(instance.status, 0) + 1
        type_counts[instance.service_type] = type_counts.get(instance.service_type, 0) + 1
        ips = [str(ip.address) for ip in instance.ip_addresses.all()]
        total_ips += len(ips)
        if instance.bandwidth_alloc_id and instance.bandwidth_alloc:
            total_bandwidth += int(instance.bandwidth_alloc.allocated_mbps or 0)
        elif instance.vps_id and instance.vps:
            total_bandwidth += int(instance.vps.bandwidth_mbps or 0)
        elif instance.edge_id and instance.edge:
            total_bandwidth += int(instance.edge.bandwidth_mbps or 0)
        resources.append({
            'instance_id': instance.pk,
            'instance_no': instance.instance_no,
            'service_type': instance.service_type,
            'service_type_label': instance.get_service_type_display(),
            'status': instance.status,
            'status_label': instance.get_status_display(),
            'resource_name': (
                instance.vps.name if instance.vps_id and instance.vps
                else instance.edge.name if instance.edge_id and instance.edge
                else f'{instance.bandwidth_alloc.allocated_mbps}Mbps'
                if instance.bandwidth_alloc_id and instance.bandwidth_alloc
                else 'IP 地址资源'
            ),
            'ip_addresses': ips,
        })

    expected = max(int(order.requested_quantity or 1), 1)
    active = status_counts.get(ServiceInstance.InstanceStatus.ACTIVE, 0)
    provisioning = status_counts.get(ServiceInstance.InstanceStatus.PROVISIONING, 0)
    allocated = sum(
        count for instance_status, count in status_counts.items()
        if instance_status not in {
            ServiceInstance.InstanceStatus.TERMINATED,
            ServiceInstance.InstanceStatus.ERROR,
            ServiceInstance.InstanceStatus.EXPIRED,
        }
    )
    if not instances:
        delivery_status = 'not_started'
    elif active >= expected:
        delivery_status = 'delivered'
    elif active or provisioning:
        delivery_status = 'partial'
    elif all(item.status == ServiceInstance.InstanceStatus.TERMINATED for item in instances):
        delivery_status = 'terminated'
    else:
        delivery_status = 'pending'

    return {
        'status': delivery_status,
        'expected_quantity': expected,
        'instance_count': len(instances),
        'allocated_count': allocated,
        'active_count': active,
        'provisioning_count': provisioning,
        'ip_count': total_ips,
        'bandwidth_mbps': total_bandwidth,
        'status_counts': status_counts,
        'type_counts': type_counts,
        'resources': resources,
    }


def sync_service_order_delivery_state(order, *, operator=None, reason=''):
    """根据全部实例的真实状态回写工单，避免单实例操作错误结束整个工单。"""
    from django.db import transaction

    with transaction.atomic():
        locked_order = ServiceOrder.objects.select_for_update().get(pk=order.pk)
        instances = list(
            ServiceInstance.objects.select_for_update()
            .filter(service_order=locked_order)
            .only('id', 'status')
        )
        if not instances:
            return locked_order

        # 草稿、待审批与已驳回工单不能被资源动作隐式越过审批流程。
        if locked_order.status in (
            ServiceOrder.OrderStatus.DRAFT,
            ServiceOrder.OrderStatus.PENDING,
            ServiceOrder.OrderStatus.REJECTED,
        ):
            return locked_order

        statuses = {item.status for item in instances}
        terminal_statuses = {
            ServiceInstance.InstanceStatus.TERMINATED,
            ServiceInstance.InstanceStatus.ERROR,
            ServiceInstance.InstanceStatus.EXPIRED,
        }
        live_statuses = statuses - terminal_statuses
        expected_quantity = max(int(locked_order.requested_quantity or 1), 1)
        active_count = sum(
            item.status == ServiceInstance.InstanceStatus.ACTIVE for item in instances
        )
        if active_count >= expected_quantity:
            new_status = ServiceOrder.OrderStatus.ACTIVE
            new_stage = ServiceOrder.LifecycleStage.USAGE
        elif (
            active_count
            or ServiceInstance.InstanceStatus.PROVISIONING in live_statuses
        ):
            new_status = ServiceOrder.OrderStatus.PROVISIONING
            new_stage = ServiceOrder.LifecycleStage.REQUIREMENT
        elif live_statuses == {ServiceInstance.InstanceStatus.SUSPENDED}:
            new_status = ServiceOrder.OrderStatus.SUSPENDED
            new_stage = ServiceOrder.LifecycleStage.USAGE
        elif not live_statuses:
            new_status = ServiceOrder.OrderStatus.TERMINATED
            new_stage = ServiceOrder.LifecycleStage.TERMINATION
        else:
            return locked_order

        old_value = {
            'status': locked_order.status,
            'current_stage': locked_order.current_stage,
        }
        update_fields = []
        if locked_order.status != new_status:
            locked_order.status = new_status
            update_fields.append('status')
        if locked_order.current_stage != new_stage:
            locked_order.current_stage = new_stage
            update_fields.append('current_stage')
        if update_fields:
            locked_order.save(update_fields=[*update_fields, 'updated_at'])
            _log_service(
                operator=operator,
                action_type=ServiceOperationLog.ActionType.STAGE_CHANGE,
                description=(
                    f'工单 [{locked_order.order_no}] 根据资源交付状态同步：'
                    f'{old_value["status"]} → {new_status}'
                    + (f'（{reason}）' if reason else '')
                ),
                service_order=locked_order,
                old_value=old_value,
                new_value={
                    'status': new_status,
                    'current_stage': new_stage,
                    'instance_statuses': sorted(statuses),
                    'active_count': active_count,
                    'expected_quantity': expected_quantity,
                },
            )
        return locked_order
