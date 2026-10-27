import datetime
import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator


# ─────────────────────────────────────────────────────────────
# 枚举选项
# ─────────────────────────────────────────────────────────────

class CustomerType(models.TextChoices):
    PERSONAL = 'personal', '个人客户'
    ENTERPRISE = 'enterprise', '企业客户'
    CHANNEL = 'channel', '渠道客户'
    PROVIDER = 'provider', '供应商客户'
    OTHER = 'other', '其他'


class CustomerStatus(models.TextChoices):
    ACTIVE = 'active', '正常'


class IMType(models.TextChoices):
    NONE = '', '未设置'
    TELEGRAM = 'telegram', 'Telegram'
    WECOM = 'wecom', '企业微信'
    DINGTALK = 'dingtalk', '钉钉'
    WECHAT = 'wechat', '微信'
    WHATSAPP = 'whatsapp', 'WhatsApp'
    QQ = 'qq', 'QQ'
    OTHER = 'other', '其他'


# ─────────────────────────────────────────────────────────────
# 客户
# ─────────────────────────────────────────────────────────────

class Customer(models.Model):
    """客户基本信息"""
    customer_no = models.CharField('客户编号', max_length=32, unique=True, editable=False)
    name = models.CharField('客户名称', max_length=128)
    customer_type = models.CharField(
        '客户类型', max_length=16,
        choices=CustomerType.choices,
        default=CustomerType.PERSONAL,
    )
    status = models.CharField(
        '状态', max_length=16,
        choices=CustomerStatus.choices,
        default=CustomerStatus.ACTIVE,
    )
    # 联系信息
    contact_name = models.CharField('联系人', max_length=64)
    contact_phone = models.CharField('联系电话', max_length=20, blank=True, null=True)
    contact_email = models.EmailField('联系邮箱', blank=True, null=True)
    # IM 账号见 CustomerIMAccount（一个客户可有多个 IM 账号）
    # 企业信息
    company_name = models.CharField('公司名称', max_length=256, blank=True, null=True)
    company_name_short = models.CharField('公司简称', max_length=64, blank=True, null=True)
    unified_credit_code = models.CharField('统一社会信用代码', max_length=32, blank=True, null=True)
    legal_representative = models.CharField('法定代表人', max_length=64, blank=True, null=True)
    # 地址信息
    province = models.CharField('省份', max_length=32, blank=True, null=True)
    city = models.CharField('城市', max_length=32, blank=True, null=True)
    address = models.CharField('详细地址', max_length=256, blank=True, null=True)
    class ReputationMode(models.TextChoices):
        NONE = 'none', '关闭'
        ONE_TIME = 'one_time', '一次性'
        MONTHLY = 'monthly', '按月更新'

    # 账务信息
    balance = models.DecimalField('账户余额', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    credit_limit = models.DecimalField('信用额度', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    reputation_mode = models.CharField(
        '信誉值模式',
        max_length=16,
        choices=ReputationMode.choices,
        default=ReputationMode.NONE,
        help_text='关闭则不可信誉值支付；一次性用完为止；按月更新每月初重置为额度',
    )
    reputation_quota = models.DecimalField(
        '信誉值额度', max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    reputation_balance = models.DecimalField(
        '信誉值余额', max_digits=12, decimal_places=2, default=Decimal('0.00'),
    )
    reputation_period = models.CharField(
        '信誉值账期', max_length=7, blank=True, default='',
        help_text='按月模式记录最近重置的 YYYY-MM',
    )
    # 关联账号
    account = models.OneToOneField(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联账号', related_name='customer',
    )
    # 销售负责人
    sales_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='销售负责人', related_name='managed_customers',
    )
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '客户'
        verbose_name_plural = '客户列表'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer_no']),
            models.Index(fields=['contact_phone']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'[{self.customer_no}] {self.name}'

    def save(self, *args, **kwargs):
        if not self.customer_no:
            prefix = 'C'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = Customer.objects.filter(
                customer_no__startswith=f'{prefix}{date_str}'
            ).order_by('-customer_no').first()
            seq = int(last.customer_no[-4:]) + 1 if last else 1
            self.customer_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────
# 渠道推广与推荐归属
# ─────────────────────────────────────────────────────────────

class ChannelProfile(models.Model):
    """渠道客户的推广规则；每个渠道客户仅维护一个有效推广码。"""

    customer = models.OneToOneField(
        Customer,
        on_delete=models.CASCADE,
        verbose_name='渠道客户',
        related_name='channel_profile',
    )
    promotion_code = models.CharField(
        '推广优惠码',
        max_length=32,
        unique=True,
        db_index=True,
    )
    promotion_discount_rate = models.DecimalField(
        '推广优惠比例(%)',
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='推荐客户账单优惠比例，例如 10 表示九折',
    )
    commission_rate = models.DecimalField(
        '渠道提成比例(%)',
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='按推荐客户实际支付后的净账单金额计算',
    )
    reputation_grant_mode = models.CharField(
        '注册授信模式',
        max_length=16,
        choices=Customer.ReputationMode.choices,
        default=Customer.ReputationMode.NONE,
        help_text='通过该优惠码注册的客户获得的信誉值模式',
    )
    reputation_grant_amount = models.DecimalField(
        '注册授信额度',
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='例如 100 表示注册后获得 100 信誉值，可用于一次或按月信誉支付',
    )
    is_active = models.BooleanField('启用推广', default=True)
    remark = models.TextField('渠道规则备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '渠道推广规则'
        verbose_name_plural = '渠道推广规则'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active', 'promotion_code']),
        ]

    def __str__(self):
        return f'{self.customer.name} - {self.promotion_code}'

    def clean(self):
        if self.customer_id and self.customer.customer_type != CustomerType.CHANNEL:
            raise ValidationError({'customer': '只有渠道客户可以配置推广码'})

    def save(self, *args, **kwargs):
        self.promotion_code = (self.promotion_code or '').strip().upper()
        super().save(*args, **kwargs)


class ChannelPayoutProfile(models.Model):
    """渠道收款资料；账号仅以加密密文持久化。"""

    class AccountType(models.TextChoices):
        BANK = 'bank', '银行账户'
        ALIPAY = 'alipay', '支付宝'
        WECHAT = 'wechat', '微信'
        PAYPAL = 'paypal', 'PayPal'
        OTHER = 'other', '其他'

    channel = models.OneToOneField(
        Customer,
        on_delete=models.CASCADE,
        related_name='channel_payout_profile',
        verbose_name='渠道客户',
    )
    entity_name = models.CharField('收款主体', max_length=256)
    account_type = models.CharField('账户类型', max_length=16, choices=AccountType.choices)
    bank_name = models.CharField('开户银行', max_length=128, blank=True)
    bank_branch = models.CharField('开户支行', max_length=256, blank=True)
    recipient_name = models.CharField('收款人', max_length=128)
    tax_id = models.CharField('税号', max_length=64, blank=True)
    account_encrypted = models.TextField('加密收款账号')
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '渠道收款资料'
        verbose_name_plural = '渠道收款资料'

    def clean(self):
        if self.channel_id and self.channel.customer_type != CustomerType.CHANNEL:
            raise ValidationError({'channel': '只有渠道客户可以配置收款资料'})
        if self.account_type == self.AccountType.BANK and not self.bank_name:
            raise ValidationError({'bank_name': '银行账户必须填写开户银行'})

    def set_account(self, account: str):
        from system_manager.payment_crypto import encrypt_payment_config
        value = str(account or '').strip()
        if not value:
            raise ValidationError({'account': '请填写收款账号'})
        self.account_encrypted = encrypt_payment_config({'account': value})

    def get_account(self) -> str:
        from system_manager.payment_crypto import decrypt_payment_config
        return str(decrypt_payment_config(self.account_encrypted).get('account') or '')

    @property
    def masked_account(self) -> str:
        value = self.get_account()
        if len(value) <= 4:
            return '*' * len(value)
        visible = 4 if len(value) >= 8 else 2
        return f'{"*" * (len(value) - visible)}{value[-visible:]}'

    def snapshot(self) -> dict:
        return {
            'entity_name': self.entity_name,
            'account_type': self.account_type,
            'bank_name': self.bank_name,
            'bank_branch': self.bank_branch,
            'recipient_name': self.recipient_name,
            'tax_id': self.tax_id,
            'masked_account': self.masked_account,
            'is_active': self.is_active,
        }


class CustomerReferral(models.Model):
    """推荐关系快照。注册绑定后不可改写，以保证优惠和佣金规则可审计。"""

    customer = models.OneToOneField(
        Customer,
        on_delete=models.PROTECT,
        verbose_name='推荐客户',
        related_name='channel_referral',
    )
    channel = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        verbose_name='所属渠道',
        related_name='referred_customer_referrals',
    )
    promotion_code = models.CharField('使用的推广码', max_length=32)
    promotion_discount_rate = models.DecimalField(
        '优惠比例快照(%)',
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    commission_rate = models.DecimalField(
        '提成比例快照(%)',
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    bound_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='绑定人',
        related_name='bound_customer_referrals',
    )
    created_at = models.DateTimeField('绑定时间', auto_now_add=True)

    class Meta:
        verbose_name = '客户渠道归属'
        verbose_name_plural = '客户渠道归属'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', 'created_at']),
            models.Index(fields=['promotion_code']),
        ]

    def __str__(self):
        return f'{self.customer.name} ← {self.channel.name} ({self.promotion_code})'

    def clean(self):
        if self.customer_id and self.channel_id and self.customer_id == self.channel_id:
            raise ValidationError({'channel': '客户不能推荐自己'})
        if self.channel_id and self.channel.customer_type != CustomerType.CHANNEL:
            raise ValidationError({'channel': '推荐来源必须是渠道客户'})


# ─────────────────────────────────────────────────────────────
# 客户 IM 账号（一个客户可有多个）
# ─────────────────────────────────────────────────────────────

class CustomerIMAccount(models.Model):
    """客户 IM 账号，便于后续 IM 对接通知（一个客户可绑定多个）。"""

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        verbose_name='客户', related_name='im_accounts',
    )
    im_type = models.CharField(
        'IM 类型', max_length=16,
        choices=IMType.choices, default=IMType.TELEGRAM,
    )
    account = models.CharField('IM 账号/ID', max_length=128, blank=True,
                               help_text='个人账号、Telegram chat_id、企业微信成员账号等')
    group = models.CharField('IM 群组', max_length=128, blank=True,
                             help_text='对接的群组名称或群 ID')
    label = models.CharField('用途/备注名', max_length=64, blank=True,
                             help_text='如：财务、技术、负责人')
    is_primary = models.BooleanField('主账号', default=False)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '客户 IM 账号'
        verbose_name_plural = '客户 IM 账号'
        ordering = ['-is_primary', 'id']
        indexes = [
            models.Index(fields=['customer', 'is_primary']),
            models.Index(fields=['im_type']),
        ]

    def __str__(self):
        return f'{self.customer.name} - {self.get_im_type_display()} {self.account or self.group}'


# ─────────────────────────────────────────────────────────────
# 联系记录
# ─────────────────────────────────────────────────────────────

class ContactRecord(models.Model):
    """客户联系/跟进记录"""

    class ContactType(models.TextChoices):
        PHONE = 'phone', '电话沟通'
        EMAIL = 'email', '邮件往来'
        VISIT = 'visit', '上门拜访'
        ONLINE = 'online', '在线沟通'
        OTHER = 'other', '其他'

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        verbose_name='客户', related_name='contact_records',
    )
    contact_type = models.CharField('联系方式', max_length=16, choices=ContactType.choices)
    contact_time = models.DateTimeField('联系时间')
    summary = models.TextField('沟通摘要')
    handler = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, verbose_name='处理人',
    )
    next_follow_time = models.DateTimeField('下次跟进时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '联系记录'
        verbose_name_plural = '联系记录'
        ordering = ['-contact_time']

    def __str__(self):
        return (
            f'{self.customer.name} - '
            f'{self.get_contact_type_display()} '
            f'({self.contact_time.strftime("%Y-%m-%d")})'
        )


# ─────────────────────────────────────────────────────────────
# 合同
# ─────────────────────────────────────────────────────────────

class Contract(models.Model):
    """客户合同"""

    class ContractStatus(models.TextChoices):
        DRAFT = 'draft', '草稿'
        REVIEWING = 'reviewing', '审核中'
        ACTIVE = 'active', '生效中'
        EXPIRED = 'expired', '已到期'
        TERMINATED = 'terminated', '已终止'

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        verbose_name='客户', related_name='contracts',
    )
    contract_no = models.CharField('合同编号', max_length=64, unique=True, editable=False)
    contract_name = models.CharField('合同名称', max_length=256, null=True, blank=True)
    status = models.CharField(
        '合同状态', max_length=16,
        choices=ContractStatus.choices,
        default=ContractStatus.DRAFT,
    )
    amount = models.DecimalField('合同金额', max_digits=14, decimal_places=2)
    start_date = models.DateField('合同开始日期')
    end_date = models.DateField('合同结束日期')
    sign_date = models.DateField('签订日期', null=True, blank=True)
    file_url = models.CharField('合同文件地址', max_length=512, blank=True)
    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, verbose_name='创建人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '合同'
        verbose_name_plural = '合同列表'
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.contract_no}] {self.contract_name}'

    def save(self, *args, **kwargs):
        if not self.contract_no:
            prefix = 'CT'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = Contract.objects.filter(
                contract_no__startswith=f'{prefix}{date_str}'
            ).order_by('-contract_no').first()
            seq = int(last.contract_no[-4:]) + 1 if last else 1
            self.contract_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────
# 账单
# ─────────────────────────────────────────────────────────────

class CustomerBilling(models.Model):
    """客户账单"""

    class BillingStatus(models.TextChoices):
        UNPAID = 'unpaid', '待支付'
        PAID = 'paid', '已支付'
        OVERDUE = 'overdue', '逾期'
        CANCELLED = 'cancelled', '已取消'
        REFUNDING = 'refunding', '退款中'
        REFUNDED = 'refunded', '已退款'

    class PaymentMethod(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', '银行转账'
        ALIPAY = 'alipay', '支付宝'
        WECHAT = 'wechat', '微信支付'
        PAYPAL = 'paypal', 'PayPal'
        STRIPE = 'stripe', 'Stripe'
        AIRWALLEX = 'airwallex', 'Airwallex'
        UNIONPAY = 'unionpay', '银联在线支付'
        CASH = 'cash', '现金'
        CREDIT_PAYMENT = 'credit_payment', '信用支付'
        REPUTATION = 'reputation', '信誉值支付'
        BALANCE = 'balance', '账户余额'
        OTHER = 'other', '其他'

    class BillingKind(models.TextChoices):
        MONTHLY = 'monthly', '月结'
        TRAFFIC_OVERAGE = 'traffic_overage', '流量超额'
        WALLET_RECHARGE = 'wallet_recharge', '余额充值'

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        verbose_name='客户', related_name='billings',
    )
    service_order = models.OneToOneField(
        'service_manager.ServiceOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='关联预付工单',
        related_name='payment_billing',
        help_text='仅用于审批后生成的专属预付账单；月度账单留空',
    )
    bill_no = models.CharField('账单编号', max_length=64, unique=True, editable=False)
    bill_month = models.CharField('账单月份', max_length=7, help_text='格式: YYYY-MM')
    amount = models.DecimalField('账单金额', max_digits=12, decimal_places=2)
    gross_amount = models.DecimalField(
        '优惠前金额',
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='按产品/报价计算的原始应收金额',
    )
    promotion_discount_amount = models.DecimalField(
        '推广优惠金额',
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    promotion_code = models.CharField('推广优惠码快照', max_length=32, blank=True)
    channel_referral = models.ForeignKey(
        CustomerReferral,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='渠道归属快照',
        related_name='billings',
    )
    currency = models.CharField('结算币种', max_length=8, default='CNY')
    auto_generated = models.BooleanField('系统生成', default=False,
                                         help_text='按月由计费引擎汇总生成')
    billing_kind = models.CharField(
        '账单类型',
        max_length=24,
        choices=BillingKind.choices,
        default=BillingKind.MONTHLY,
        help_text='月结由 generate_bill 重建；流量超额与余额充值独立出账，避免被月结删除',
    )
    paid_amount = models.DecimalField('已付金额', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    refunded_amount = models.DecimalField(
        '已退金额', max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='不回滚已付金额；退款成功后记在此字段',
    )
    refund_reason = models.CharField('退款原因', max_length=32, blank=True)
    refunded_at = models.DateTimeField('退款完成时间', null=True, blank=True)
    status = models.CharField(
        '状态', max_length=16,
        choices=BillingStatus.choices,
        default=BillingStatus.UNPAID,
    )
    payment_method = models.CharField(
        '支付方式', max_length=16,
        choices=PaymentMethod.choices,
        blank=True,
    )
    due_date = models.DateField('到期日期')
    paid_at = models.DateTimeField('支付时间', null=True, blank=True)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '账单'
        verbose_name_plural = '账单列表'
        ordering = ['-bill_month', '-created_at']
        indexes = [
            models.Index(fields=['customer', 'bill_month', 'billing_kind']),
        ]

    def __str__(self):
        return f'[{self.bill_no}] {self.customer.name} {self.bill_month} ¥{self.amount}'

    def save(self, *args, **kwargs):
        if not self.bill_no:
            prefix = 'B'
            date_str = datetime.date.today().strftime('%Y%m%d')
            last = CustomerBilling.objects.filter(
                bill_no__startswith=f'{prefix}{date_str}'
            ).order_by('-bill_no').first()
            seq = int(last.bill_no[-4:]) + 1 if last else 1
            self.bill_no = f'{prefix}{date_str}{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def outstanding_amount(self):
        return Decimal(str(self.amount or 0)) - Decimal(str(self.paid_amount or 0))


class CustomerBalanceLedger(models.Model):
    """客户账户余额流水。"""

    class EntryType(models.TextChoices):
        RECHARGE = 'recharge', '充值入账'
        APPLY = 'apply', '抵扣账单'
        ADJUST = 'adjust', '人工调整'
        REFUND = 'refund', '充值退回'

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        verbose_name='客户', related_name='balance_ledgers',
    )
    entry_type = models.CharField('类型', max_length=16, choices=EntryType.choices)
    delta = models.DecimalField('变动金额', max_digits=12, decimal_places=2)
    balance_after = models.DecimalField('变动后余额', max_digits=12, decimal_places=2)
    currency = models.CharField('币种', max_length=8, default='CNY')
    related_billing = models.ForeignKey(
        CustomerBilling, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联账单', related_name='balance_ledgers',
    )
    remark = models.CharField('备注', max_length=256, blank=True)
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='操作人',
    )
    created_at = models.DateTimeField('记录时间', auto_now_add=True)

    class Meta:
        verbose_name = '客户余额流水'
        verbose_name_plural = '客户余额流水'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['customer', 'created_at']),
            models.Index(fields=['related_billing', 'entry_type']),
        ]

    def __str__(self):
        return f'{self.customer_id} {self.entry_type} {self.delta}'


# ─────────────────────────────────────────────────────────────
# 渠道佣金与结算
# ─────────────────────────────────────────────────────────────

class ChannelSettlement(models.Model):
    """渠道佣金的人工结算批次；不同币种不可混合结算。"""

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        CONFIRMED = 'confirmed', '已确认'
        PAID = 'paid', '已打款'
        CANCELLED = 'cancelled', '已取消'

    channel = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        verbose_name='渠道客户',
        related_name='channel_settlements',
    )
    channel_no = models.CharField('渠道编号快照', max_length=32, blank=True)
    channel_name = models.CharField('渠道名称快照', max_length=128, blank=True)
    settlement_no = models.CharField('结算单号', max_length=32, unique=True, editable=False)
    currency = models.CharField('结算币种', max_length=8, default='CNY')
    total_amount = models.DecimalField('结算金额', max_digits=12, decimal_places=2, default=0)
    commission_count = models.PositiveIntegerField('佣金笔数', default=0)
    period_start = models.DateField('结算周期开始', null=True, blank=True)
    period_end = models.DateField('结算周期结束', null=True, blank=True)
    status = models.CharField('状态', max_length=16, choices=Status.choices, default=Status.DRAFT)
    payout_profile_snapshot = models.JSONField('收款资料快照', default=dict, blank=True)
    payment_method = models.CharField('打款方式', max_length=32, blank=True)
    payment_reference = models.CharField('打款流水号/凭证号', max_length=128, blank=True)
    remark = models.TextField('备注', blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='创建人',
        related_name='created_channel_settlements',
    )
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='确认人',
        related_name='confirmed_channel_settlements',
    )
    paid_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='打款人',
        related_name='paid_channel_settlements',
    )
    confirmed_at = models.DateTimeField('确认时间', null=True, blank=True)
    paid_at = models.DateTimeField('打款时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '渠道佣金结算单'
        verbose_name_plural = '渠道佣金结算单'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', 'status', 'currency']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f'[{self.settlement_no}] {self.channel.name} {self.currency}{self.total_amount}'

    def save(self, *args, **kwargs):
        if not self.settlement_no:
            self.settlement_no = f'STL{uuid.uuid4().hex.upper()[:20]}'
        super().save(*args, **kwargs)


class ChannelCommission(models.Model):
    """一张已完成支付的推荐客户账单至多产生一笔渠道佣金。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待结算'
        SETTLING = 'settling', '结算中'
        PAID = 'paid', '已结算'
        VOID = 'void', '已作废'

    channel = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        verbose_name='渠道客户',
        related_name='channel_commissions',
    )
    referral = models.ForeignKey(
        CustomerReferral,
        on_delete=models.PROTECT,
        verbose_name='客户渠道归属',
        related_name='commissions',
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        verbose_name='推荐客户',
        related_name='referral_commissions',
    )
    customer_no = models.CharField('客户编号快照', max_length=32, blank=True)
    customer_name = models.CharField('客户名称快照', max_length=128, blank=True)
    billing = models.OneToOneField(
        CustomerBilling,
        on_delete=models.PROTECT,
        verbose_name='来源账单',
        related_name='channel_commission',
    )
    settlement = models.ForeignKey(
        ChannelSettlement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='佣金结算单',
        related_name='commissions',
    )
    bill_no = models.CharField('账单编号快照', max_length=64)
    gross_amount = models.DecimalField('优惠前金额快照', max_digits=12, decimal_places=2, default=0)
    promotion_discount_amount = models.DecimalField(
        '推广优惠金额快照',
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    billing_amount = models.DecimalField('账单金额快照', max_digits=12, decimal_places=2, default=0)
    billing_period = models.CharField('账期快照', max_length=32, blank=True)
    billing_paid_at = models.DateTimeField('账单支付时间快照', null=True, blank=True)
    service_items_snapshot = models.JSONField('服务项目快照', default=list, blank=True)
    payment_summary_snapshot = models.JSONField('支付摘要快照', default=dict, blank=True)
    promotion_code = models.CharField('推广码快照', max_length=32, blank=True)
    commission_base_amount = models.DecimalField(
        '提成基数',
        max_digits=12,
        decimal_places=2,
        help_text='推荐客户实际应付的优惠后账单金额',
    )
    commission_rate = models.DecimalField('提成比例快照(%)', max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField('提成金额', max_digits=12, decimal_places=2)
    currency = models.CharField('币种', max_length=8, default='CNY')
    status = models.CharField('状态', max_length=16, choices=Status.choices, default=Status.PENDING)
    void_reason = models.TextField('作废原因', blank=True)
    earned_at = models.DateTimeField('产生时间', auto_now_add=True)
    settled_at = models.DateTimeField('结算完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '渠道佣金明细'
        verbose_name_plural = '渠道佣金明细'
        ordering = ['-earned_at']
        indexes = [
            models.Index(fields=['channel', 'status', 'currency']),
            models.Index(fields=['customer', 'earned_at']),
            models.Index(fields=['status', 'earned_at']),
        ]

    def __str__(self):
        return f'[{self.bill_no}] {self.channel.name} {self.currency}{self.commission_amount}'


class ChannelFinanceAccount(models.Model):
    """渠道按币种维护的财务余额。"""

    channel = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='channel_finance_accounts',
        verbose_name='渠道客户',
    )
    currency = models.CharField('币种', max_length=8, default='CNY')
    balance = models.DecimalField('余额', max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '渠道财务账户'
        verbose_name_plural = '渠道财务账户'
        constraints = [
            models.UniqueConstraint(
                fields=['channel', 'currency'],
                name='uniq_channel_finance_account_currency',
            ),
        ]

    def save(self, *args, **kwargs):
        self.currency = str(self.currency or 'CNY').upper()
        super().save(*args, **kwargs)


class ImmutableLedgerQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('财务流水不可修改')

    def delete(self):
        raise ValidationError('财务流水不可删除')


class ChannelFinanceLedger(models.Model):
    """渠道余额不可变流水；仅允许追加。"""

    class EntryType(models.TextChoices):
        ACCRUAL = 'accrual', '佣金入账'
        VOID = 'void', '佣金作废'
        SETTLEMENT_CREATED = 'settlement_created', '创建结算'
        SETTLEMENT_CANCELLED = 'settlement_cancelled', '取消结算'
        PAYOUT = 'payout', '结算打款'
        ADJUSTMENT = 'adjustment', '人工调整'

    account = models.ForeignKey(
        ChannelFinanceAccount,
        on_delete=models.PROTECT,
        related_name='ledgers',
        verbose_name='财务账户',
    )
    entry_type = models.CharField('流水类型', max_length=32, choices=EntryType.choices)
    delta = models.DecimalField('余额变动', max_digits=14, decimal_places=2)
    balance_after = models.DecimalField('变动后余额', max_digits=14, decimal_places=2)
    source_type = models.CharField('来源类型', max_length=64)
    source_id = models.CharField('来源标识', max_length=64, blank=True)
    snapshot = models.JSONField('业务快照', default=dict, blank=True)
    operator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='channel_finance_ledgers',
        verbose_name='操作人',
    )
    idempotency_key = models.CharField('幂等键', max_length=128, unique=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    objects = ImmutableLedgerQuerySet.as_manager()

    class Meta:
        verbose_name = '渠道财务流水'
        verbose_name_plural = '渠道财务流水'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['account', 'created_at']),
            models.Index(fields=['entry_type', 'created_at']),
            models.Index(fields=['source_type', 'source_id']),
        ]

    def save(self, *args, **kwargs):
        # _state.adding 在迁移写入及正常追加时均为 True。
        if not self._state.adding:
            raise ValidationError('财务流水不可修改')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('财务流水不可删除')


# ─────────────────────────────────────────────────────────────
# 在线支付交易
# ─────────────────────────────────────────────────────────────

class PaymentTransaction(models.Model):
    """一次可审计、可幂等完成的在线支付交易。"""

    class Status(models.TextChoices):
        CREATED = 'created', '已创建'
        PENDING = 'pending', '等待付款'
        PROCESSING = 'processing', '处理中'
        SUCCEEDED = 'succeeded', '支付成功'
        FAILED = 'failed', '支付失败'
        CANCELLED = 'cancelled', '已取消'
        EXPIRED = 'expired', '已过期'
        REFUNDING = 'refunding', '退款中'
        REFUNDED = 'refunded', '已退款'
        PENDING_MANUAL = 'pending_manual', '待人工退款'

    class RefundReason(models.TextChoices):
        PROVISION_FAILED = 'provision_failed', '开通失败'
        UPGRADE_UNEXECUTED = 'upgrade_unexecuted', '升配未执行'
        MANUAL = 'manual', '人工退款'

    billing = models.ForeignKey(
        CustomerBilling, on_delete=models.PROTECT,
        verbose_name='账单', related_name='payment_transactions',
    )
    channel = models.ForeignKey(
        'system_manager.PaymentChannel', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='支付渠道', related_name='payment_transactions',
    )
    transaction_no = models.CharField('支付交易号', max_length=64, unique=True, editable=False)
    provider = models.CharField('支付服务商', max_length=32)
    amount = models.DecimalField('支付金额', max_digits=12, decimal_places=2)
    currency = models.CharField('支付币种', max_length=8)
    billing_amount = models.DecimalField(
        '账单入账金额',
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='按账单币种计算的本次付款金额',
    )
    billing_currency = models.CharField(
        '账单币种',
        max_length=8,
        blank=True,
        default='',
    )
    exchange_rate = models.DecimalField(
        '支付换算汇率',
        max_digits=18,
        decimal_places=8,
        default=1,
        help_text='1 单位账单币种可兑换的支付币种金额',
    )
    status = models.CharField(
        '交易状态', max_length=16, choices=Status.choices, default=Status.CREATED,
    )
    provider_order_id = models.CharField('服务商订单号', max_length=128, null=True, blank=True, db_index=True)
    provider_payment_id = models.CharField('服务商支付流水号', max_length=128, null=True, blank=True, db_index=True)
    checkout_payload = models.JSONField('下单响应', default=dict, blank=True)
    provider_response = models.JSONField('最近服务商响应', default=dict, blank=True)
    failure_reason = models.TextField('失败原因', blank=True)
    callback_count = models.PositiveIntegerField('回调次数', default=0)
    last_callback_at = models.DateTimeField('最后回调时间', null=True, blank=True)
    last_reconciled_at = models.DateTimeField('最后对账时间', null=True, blank=True)
    last_reconcile_status = models.CharField('最后对账结果', max_length=24, blank=True)
    last_reconcile_error = models.CharField('最后对账错误', max_length=256, blank=True)
    initiated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='发起人', related_name='initiated_payment_transactions',
    )
    return_url = models.CharField('支付完成跳转地址', max_length=1024, blank=True)
    expires_at = models.DateTimeField('到期时间', null=True, blank=True)
    paid_at = models.DateTimeField('成功时间', null=True, blank=True)
    refund_reason = models.CharField('退款原因', max_length=32, blank=True)
    refunded_amount = models.DecimalField('退款金额', max_digits=12, decimal_places=2, default=Decimal('0.00'))
    provider_refund_id = models.CharField('服务商退款单号', max_length=128, blank=True, default='')
    refund_idempotency_key = models.CharField(
        '退款幂等键', max_length=64, unique=True, null=True, blank=True,
    )
    refund_requested_at = models.DateTimeField('退款发起时间', null=True, blank=True)
    refunded_at = models.DateTimeField('退款完成时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '在线支付交易'
        verbose_name_plural = '在线支付交易'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['billing', 'status', 'created_at']),
            models.Index(fields=['provider', 'provider_order_id']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f'[{self.transaction_no}] {self.provider} {self.currency}{self.amount}'

    def save(self, *args, **kwargs):
        if not self.transaction_no:
            self.transaction_no = f'PAY{uuid.uuid4().hex.upper()}'
        super().save(*args, **kwargs)


class PaymentWebhookEvent(models.Model):
    """已验签支付回调的审计与幂等记录。"""

    class ProcessingStatus(models.TextChoices):
        RECEIVED = 'received', '已接收'
        PROCESSED = 'processed', '已处理'
        IGNORED = 'ignored', '已忽略'
        FAILED = 'failed', '处理失败'

    provider = models.CharField('支付服务商', max_length=32)
    event_id = models.CharField('服务商事件 ID', max_length=128)
    event_type = models.CharField('事件类型', max_length=128, blank=True)
    resource_id = models.CharField('资源 ID', max_length=128, blank=True)
    transmission_id = models.CharField('传输 ID', max_length=128, blank=True)
    verification_status = models.CharField('验签状态', max_length=32, default='SUCCESS')
    status = models.CharField(
        '处理状态',
        max_length=16,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    channel = models.ForeignKey(
        'system_manager.PaymentChannel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
        verbose_name='支付渠道',
    )
    payment = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
        verbose_name='支付交易',
    )
    payload = models.JSONField('已验签事件', default=dict, blank=True)
    error_message = models.TextField('处理错误', blank=True)
    processed_at = models.DateTimeField('处理时间', null=True, blank=True)
    created_at = models.DateTimeField('接收时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '支付回调事件'
        verbose_name_plural = '支付回调事件'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'event_id'],
                name='uniq_payment_webhook_provider_event',
            ),
        ]
        indexes = [
            models.Index(fields=['provider', 'event_type', 'created_at']),
            models.Index(fields=['status', 'created_at']),
        ]


# ─────────────────────────────────────────────────────────────
# 账单明细（按服务实例逐条计费）
# ─────────────────────────────────────────────────────────────

class BillingItem(models.Model):
    """账单明细行：对应某个服务实例在账单月内的计费记录。"""

    billing = models.ForeignKey(
        CustomerBilling, on_delete=models.CASCADE,
        verbose_name='所属账单', related_name='items',
    )
    service_instance = models.ForeignKey(
        'service_manager.ServiceInstance', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='服务实例', related_name='billing_items',
    )
    instance_no = models.CharField('实例编号', max_length=32, blank=True)
    description = models.CharField('计费说明', max_length=256, blank=True)
    service_type = models.CharField('服务类型', max_length=16, blank=True)

    # 关联产品（按产品基础规格 + 超出部分计费时记录来源产品）
    product = models.ForeignKey(
        'product_manager.Product', on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联产品', related_name='billing_items',
    )
    product_name = models.CharField('产品名称', max_length=128, blank=True)

    # 计费周期
    period_start = models.DateField('计费起始', null=True, blank=True)
    period_end = models.DateField('计费结束', null=True, blank=True)
    days_used = models.PositiveSmallIntegerField('计费天数', default=0)
    days_in_month = models.PositiveSmallIntegerField('整月天数', default=30)
    is_prorated = models.BooleanField('按天比例', default=False)
    is_trial = models.BooleanField('测试服务', default=False)

    # 金额（原始币种 + 折算后结算币种）
    unit_monthly_fee = models.DecimalField('整月单价', max_digits=12, decimal_places=2, default=0)
    base_monthly_fee = models.DecimalField('基础月租', max_digits=12, decimal_places=2, default=0,
                                           help_text='产品基础规格月租（原始币种）')
    overage_monthly_fee = models.DecimalField('超出部分月租', max_digits=12, decimal_places=2, default=0,
                                              help_text='超出产品模版规格的阶梯升级月租（原始币种）')
    source_currency = models.CharField('原始币种', max_length=8, default='CNY')
    exchange_rate = models.DecimalField('折算汇率', max_digits=12, decimal_places=6, default=1,
                                        help_text='原始币种 → 结算币种 的汇率')
    amount_original = models.DecimalField('原始金额', max_digits=12, decimal_places=2, default=0)
    currency = models.CharField('结算币种', max_length=8, default='CNY')
    amount = models.DecimalField('结算金额', max_digits=12, decimal_places=2, default=0)

    # 计费明细快照：实际规格 / 模版基准规格 / 超出阶梯逐项 / 比例换算，供「详情」查看
    detail = models.JSONField('计费明细快照', default=dict, blank=True)
    traffic_report = models.OneToOneField(
        'operation_manager.MonitoringTrafficReport',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='流量报告',
        related_name='billing_item',
        help_text='流量归档出账的幂等键；同一报告只生成一条明细',
    )

    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '账单明细'
        verbose_name_plural = '账单明细'
        ordering = ['billing', 'id']

    def __str__(self):
        return f'{self.description} {self.currency}{self.amount}'


# ─────────────────────────────────────────────────────────────
# 操作日志
# ─────────────────────────────────────────────────────────────

class CustomerOperationLog(models.Model):
    """客户管理模块操作日志"""

    class ActionType(models.TextChoices):
        CREATE = 'create', '新建'
        UPDATE = 'update', '更新'
        DELETE = 'delete', '删除'
        STATUS_CHANGE = 'status_change', '状态变更'
        PAYMENT = 'payment', '收款'
        CONTRACT_SIGN = 'contract_sign', '合同签订'
        CONTACT = 'contact', '联系跟进'
        IMPORT = 'import', '批量导入'
        EXPORT = 'export', '数据导出'
        OTHER = 'other', '其他'

    class TargetModel(models.TextChoices):
        CUSTOMER = 'Customer', '客户'
        CONTRACT = 'Contract', '合同'
        BILLING = 'CustomerBilling', '账单'
        CONTACT_RECORD = 'ContactRecord', '联系记录'
        CHANNEL_REFERRAL = 'CustomerReferral', '渠道归属'
        CHANNEL_COMMISSION = 'ChannelCommission', '渠道佣金'
        CHANNEL_SETTLEMENT = 'ChannelSettlement', '渠道结算单'

    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='关联客户', related_name='operation_logs',
    )
    operator = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, verbose_name='操作人',
    )
    action_type = models.CharField(
        '操作类型', max_length=20,
        choices=ActionType.choices,
        default=ActionType.OTHER,
    )
    target_model = models.CharField(
        '操作对象', max_length=32,
        choices=TargetModel.choices,
        blank=True,
    )
    target_id = models.PositiveIntegerField('对象ID', null=True, blank=True)
    description = models.CharField('操作描述', max_length=512)
    old_value = models.JSONField('变更前', null=True, blank=True)
    new_value = models.JSONField('变更后', null=True, blank=True)
    ip_address = models.GenericIPAddressField('操作IP', null=True, blank=True)
    created_at = models.DateTimeField('操作时间', auto_now_add=True)

    class Meta:
        verbose_name = '操作日志'
        verbose_name_plural = '操作日志'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer', 'created_at']),
            models.Index(fields=['operator', 'created_at']),
            models.Index(fields=['action_type']),
            models.Index(fields=['target_model', 'target_id']),
        ]

    def __str__(self):
        operator_name = self.operator.get_full_name() or self.operator.username if self.operator else '系统'
        return f'[{self.get_action_type_display()}] {operator_name} - {self.description}'


# ─────────────────────────────────────────────────────────────
# 日志记录工具函数
# ─────────────────────────────────────────────────────────────

def record_operation_log(
    *,
    operator,
    action_type,
    description,
    customer=None,
    target_model='',
    target_id=None,
    old_value=None,
    new_value=None,
    ip_address=None,
    request=None,
):
    """
    记录一条操作日志。

    在 admin.py / views.py 中直接调用：
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.UPDATE,
            description='修改客户状态为暂停',
            customer=customer_obj,
            target_model=CustomerOperationLog.TargetModel.CUSTOMER,
            target_id=customer_obj.pk,
            old_value={'status': 'active'},
            new_value={'status': 'suspended'},
            request=request,
        )
    """
    if request is not None and ip_address is None:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        ip_address = (
            x_forwarded_for.split(',')[0].strip()
            if x_forwarded_for
            else request.META.get('REMOTE_ADDR')
        )
    CustomerOperationLog.objects.create(
        customer=customer,
        operator=operator,
        action_type=action_type,
        target_model=target_model,
        target_id=target_id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
    )


def model_to_dict_simple(instance, fields=None):
    """将模型实例转为简单字典（仅包含基础字段，用于日志 old/new value）"""
    from django.forms.models import model_to_dict as _model_to_dict
    data = {}
    for field in instance._meta.fields:
        if fields and field.name not in fields:
            continue
        val = getattr(instance, field.name)
        if hasattr(val, 'pk'):
            data[field.name] = val.pk
        elif hasattr(val, 'isoformat'):
            data[field.name] = val.isoformat()
        else:
            data[field.name] = str(val) if val is not None else None
    return data
