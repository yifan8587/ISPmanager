"""
system_manager/models.py
系统配置项（键值 + 分类 + 类型）
"""
import json

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .payment_crypto import decrypt_payment_config, encrypt_payment_config
from .payment_providers import (
    PAYMENT_PROVIDER_CHOICES,
    get_provider_spec,
    public_config_view,
    validate_provider_config,
)


class SystemSetting(models.Model):
    """系统配置项"""

    class Category(models.TextChoices):
        GENERAL = 'general', '基础设置'
        SUPPORT = 'support', '对外客服'
        SECURITY = 'security', '安全设置'
        BILLING = 'billing', '账单设置'
        NOTIFICATION = 'notification', '对内通知'
        INTEGRATION = 'integration', '集成设置'

    class ValueType(models.TextChoices):
        STRING = 'string', '字符串'
        TEXT = 'text', '多行文本'
        INTEGER = 'integer', '整数'
        DECIMAL = 'decimal', '小数'
        BOOLEAN = 'boolean', '布尔'
        JSON = 'json', 'JSON'

    key = models.CharField('配置键', max_length=64, unique=True, db_index=True)
    category = models.CharField(
        '分类', max_length=32,
        choices=Category.choices, default=Category.GENERAL,
    )
    label = models.CharField('显示名称', max_length=128)
    description = models.TextField('说明', blank=True, default='')
    value_type = models.CharField(
        '值类型', max_length=16,
        choices=ValueType.choices, default=ValueType.STRING,
    )
    value = models.TextField('配置值', blank=True, default='')
    default_value = models.TextField('默认值', blank=True, default='')
    options = models.JSONField(
        '可选值', null=True, blank=True,
        help_text='下拉选项，如 [{"label":"开启","value":"true"}]',
    )
    sort_order = models.PositiveSmallIntegerField('排序', default=0)
    is_public = models.BooleanField(
        '公开可读', default=False,
        help_text='无需 system.view 权限即可读取（如站点名称）',
    )
    is_editable = models.BooleanField('允许编辑', default=True)
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='最后修改人', related_name='updated_system_settings',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'
        ordering = ['category', 'sort_order', 'key']
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['is_public']),
        ]

    def __str__(self):
        return f'{self.label} ({self.key})'

    def clean(self):
        try:
            self.get_typed_value()
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationError({'value': f'配置值与类型 {self.value_type} 不匹配：{exc}'}) from exc

    def get_raw_value(self) -> str:
        return self.value if self.value != '' else self.default_value

    def get_typed_value(self):
        raw = self.get_raw_value()
        if self.value_type == self.ValueType.STRING:
            return raw
        if self.value_type == self.ValueType.TEXT:
            return raw
        if self.value_type == self.ValueType.INTEGER:
            return int(raw) if raw != '' else 0
        if self.value_type == self.ValueType.DECIMAL:
            from decimal import Decimal
            return Decimal(raw) if raw != '' else Decimal('0')
        if self.value_type == self.ValueType.BOOLEAN:
            return str(raw).lower() in ('1', 'true', 'yes', 'on')
        if self.value_type == self.ValueType.JSON:
            return json.loads(raw) if raw else {}
        return raw

    @classmethod
    def get_value(cls, key: str, default=None):
        try:
            setting = cls.objects.get(key=key)
        except cls.DoesNotExist:
            return default
        return setting.get_typed_value()

    @classmethod
    def set_value(cls, key: str, value, user=None):
        setting, _ = cls.objects.get_or_create(
            key=key,
            defaults={
                'label': key,
                'category': cls.Category.GENERAL,
                'value_type': cls.ValueType.STRING,
            },
        )
        if setting.value_type == cls.ValueType.BOOLEAN:
            setting.value = 'true' if value else 'false'
        elif setting.value_type == cls.ValueType.JSON:
            setting.value = json.dumps(value, ensure_ascii=False)
        else:
            setting.value = str(value)
        if user:
            setting.updated_by = user
        setting.save(update_fields=['value', 'updated_by', 'updated_at'])
        return setting


class PaymentChannel(models.Model):
    """在线支付通道配置。

    ``encrypted_config`` 只保存经 Fernet 加密的服务端凭据，序列化器不会返回
    私钥、API key 或 webhook secret。
    """

    provider = models.CharField('支付渠道', max_length=32, choices=PAYMENT_PROVIDER_CHOICES)
    name = models.CharField('显示名称', max_length=64)
    enabled = models.BooleanField('启用', default=False)
    sandbox = models.BooleanField('沙箱模式', default=True)
    allowed_currencies = models.JSONField(
        '允许币种', default=list, blank=True,
        help_text='留空表示不限制；例如 ["CNY", "USD"]',
    )
    settlement_currency = models.CharField(
        '渠道收款币种',
        max_length=8,
        blank=True,
        default='',
        help_text='网关实际下单币种；留空时优先使用账单币种，否则使用允许币种第一项',
    )
    encrypted_config = models.TextField('加密配置', blank=True, default='', editable=False)
    sort_order = models.PositiveSmallIntegerField('排序', default=0)
    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '在线支付渠道'
        verbose_name_plural = '在线支付渠道'
        ordering = ['sort_order', 'provider', 'id']
        indexes = [
            models.Index(fields=['enabled', 'sort_order']),
            models.Index(fields=['provider']),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_provider_display()})'

    def get_config(self) -> dict:
        return decrypt_payment_config(self.encrypted_config)

    def set_config(self, config: dict) -> None:
        self.encrypted_config = encrypt_payment_config(config)

    def config_view(self) -> dict:
        return public_config_view(self.provider, self.get_config())

    @property
    def provider_label(self) -> str:
        return get_provider_spec(self.provider)['label']

    def is_currency_allowed(self, currency: str) -> bool:
        allowed = [str(item).upper() for item in (self.allowed_currencies or []) if item]
        return not allowed or str(currency).upper() in allowed

    def clean(self):
        super().clean()
        try:
            validate_provider_config(
                self.provider, self.get_config(), enabled=self.enabled,
            )
        except ValueError as exc:
            raise ValidationError({'encrypted_config': str(exc)}) from exc


# ═══════════════════════════════════════════════════════════════
# § IM 通知对接（Telegram / 企业微信应用 / 企业微信客服 / 钉钉 / 自定义 Webhook）
# ═══════════════════════════════════════════════════════════════

class IMChannelType(models.TextChoices):
    TELEGRAM = 'telegram', 'Telegram'
    WECOM = 'wecom', '企业微信'
    WECOM_WEBHOOK = 'wecom_webhook', '企业微信群机器人 Webhook'
    WECOM_KF = 'wecom_kf', '企业微信客服'
    WECOM_AIBOT = 'wecom_aibot', '企业微信智能机器人'
    DINGTALK = 'dingtalk', '钉钉'
    WEBHOOK = 'webhook', '自定义 Webhook'


class IMTestStatus(models.TextChoices):
    NEVER = 'never', '未测试'
    SUCCESS = 'success', '成功'
    FAILED = 'failed', '失败'


class IMChannel(models.Model):
    """IM 通道配置：对接 Telegram、企业微信应用/客服、钉钉或自定义 Webhook。"""

    name = models.CharField('通道名称', max_length=64)
    channel_type = models.CharField('通道类型', max_length=16, choices=IMChannelType.choices)
    enabled = models.BooleanField('启用', default=True)

    # ── 通用 Webhook（钉钉群机器人 / 自定义）──
    webhook_url = models.CharField('Webhook 地址', max_length=512, blank=True)
    secret = models.CharField('签名密钥', max_length=256, blank=True,
                              help_text='钉钉加签密钥 / 通用签名（可选）')

    # ── Telegram ──
    bot_token = models.CharField('Telegram Bot Token', max_length=128, blank=True)

    # ── 企业微信自建应用模式 ──
    corp_id = models.CharField('企业ID(CorpID)', max_length=128, blank=True)
    agent_id = models.CharField('应用AgentID', max_length=64, blank=True)
    corp_secret = models.CharField(
        '应用 Secret', max_length=256, blank=True,
        help_text='仅用于服务端调用企业微信接口；接口读取时不返回明文。',
    )
    wecom_secret_env = models.CharField(
        '企业微信 Secret 环境变量名', max_length=128, blank=True,
        help_text='例如 WECOM_OPS_SECRET；自建应用只从该服务器环境变量读取 Secret。',
    )

    # ── 企业微信客服模式 ──
    wecom_kf_id = models.CharField(
        '客服账号 OpenKfId', max_length=128, blank=True,
        help_text='企业微信客服账号标识，例如客服链接中的 kfid；用于 kf/send_msg 的 open_kfid。',
    )
    wecom_kf_url = models.CharField(
        '客服入口链接', max_length=512, blank=True,
        help_text='客户可访问的企业微信客服链接，例如 https://work.weixin.qq.com/kfid/xxx。',
    )

    # ── 企业微信智能机器人（长连接）──
    wecom_aibot_id = models.CharField(
        '智能机器人 BotID', max_length=128, blank=True,
        help_text='企业微信智能机器人配置页中的 BotID，不是 CorpID 或 AgentID。',
    )
    wecom_aibot_secret = models.CharField(
        '智能机器人长连接 Secret', max_length=256, blank=True,
        help_text='仅用于 aibot_subscribe 长连接鉴权；接口读取时不返回明文。',
    )

    # ── 接收目标 ──
    default_target = models.CharField(
        '默认接收标识', max_length=256, blank=True,
        help_text='对内告警接收标识，不会展示到客户门户。Telegram=chat_id；企业微信=成员账号(touser)；企业微信客服=external_userid；钉钉=@手机号',
    )
    recipients = models.JSONField(
        '接口人列表', default=list, blank=True,
        help_text='[{"name":"张三","target":"chat_id或touser或手机号"}]',
    )

    # ── 测试状态 ──
    last_test_at = models.DateTimeField('最近测试时间', null=True, blank=True)
    last_test_status = models.CharField('最近测试结果', max_length=16,
                                        choices=IMTestStatus.choices, default=IMTestStatus.NEVER)
    last_test_message = models.CharField('最近测试信息', max_length=512, blank=True)

    remark = models.TextField('备注', blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = 'IM 通道'
        verbose_name_plural = 'IM 通道'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.get_channel_type_display()})'

    def target_list(self) -> list[str]:
        """汇总该通道全部接收目标（接口人 + 默认）。"""
        if self.channel_type == IMChannelType.WECOM_WEBHOOK:
            return []
        if self.channel_type in (IMChannelType.WECOM, IMChannelType.WECOM_AIBOT):
            return [target.strip() for target in self.default_target.replace(',', '|').split('|') if target.strip()]
        targets = []
        for r in (self.recipients or []):
            t = (r or {}).get('target') if isinstance(r, dict) else None
            if t:
                targets.append(str(t).strip())
        if self.default_target:
            targets.extend([t.strip() for t in self.default_target.split(',') if t.strip()])
        # 去重保序
        seen, result = set(), []
        for t in targets:
            if t and t not in seen:
                seen.add(t)
                result.append(t)
        return result


class NotificationEvent(models.TextChoices):
    RESOURCE_CHANGE = 'resource_change', '资源异动'
    VPS_CHANGE = 'vps_change', 'VPS 变更'
    EDGE_ACCELERATION_CHANGE = 'edge_acceleration_change', '边缘加速变更'
    POP_CHANGE = 'pop_change', 'POP 节点变更'
    IP_CHANGE = 'ip_change', 'IP 资源变更'
    BANDWIDTH_CHANGE = 'bandwidth_change', '带宽变更'
    PRODUCT_PUBLISH = 'product_publish', '产品发布/上下线'
    SERVICE_CHANGE = 'service_change', '服务变更'
    SERVICE_ORDER = 'service_order', '服务工单'
    SERVICE_INQUIRY = 'service_inquiry', '官网咨询/测试申请'
    SERVICE_TICKET = 'service_ticket', '客户技术工单'
    SERVICE_SELFSERVICE = 'service_selfservice', '客户自助重装/快照'
    SERVICE_LIFECYCLE = 'service_lifecycle', '服务生命周期'
    SERVICE_PROVISION = 'service_provision', '服务开通'
    SERVICE_EXPIRY = 'service_expiry', '服务到期'
    CUSTOMER_CHANGE = 'customer_change', '客户变更'
    CUSTOMER_CONTACT = 'customer_contact', '客户联系记录'
    CUSTOMER_CONTRACT = 'customer_contract', '客户合同变更'
    BILLING_CHANGE = 'billing_change', '客户账单变更'
    BILLING_PAYMENT = 'billing_payment', '客户收款'
    BILLING_OVERDUE = 'billing_overdue', '账单逾期'
    SUPPLIER_SYNC = 'supplier_sync', '供应商资源同步'
    SYSTEM_SETTING_CHANGE = 'system_setting_change', '系统参数变更'
    SYSTEM_ALERT = 'system_alert', '系统告警'
    CUSTOM = 'custom', '自定义/测试'


class NotificationFormatMode(models.TextChoices):
    COMPACT = 'compact', '精简格式'
    DETAILED = 'detailed', '详细格式'
    CUSTOM = 'custom', '自定义模板'


class NotificationRule(models.Model):
    """通知规则：一个或多个变更事件 → 推送到哪些 IM 通道。"""

    name = models.CharField('规则名称', max_length=64)
    # ``event`` 保留为兼容旧 API、筛选和排序所用的主事件；实际匹配以 ``events``
    # 为准。新规则始终把第一个事件同步写入该字段。
    event = models.CharField('主触发事件', max_length=32, choices=NotificationEvent.choices)
    events = models.JSONField(
        '触发事件列表', default=list, blank=True,
        help_text='至少选择一个事件；事件命中其中任一项即触发本规则。',
    )
    enabled = models.BooleanField('启用', default=True)
    channels = models.ManyToManyField(
        IMChannel, related_name='rules', verbose_name='推送通道', blank=True,
    )
    action_filters = models.JSONField(
        '兼容动作筛选', default=list, blank=True,
        help_text='旧规则兼容字段；新规则请使用按事件动作筛选。',
    )
    event_action_filters = models.JSONField(
        '按事件动作筛选', default=dict, blank=True,
        help_text='键为事件代码，值为动作代码列表；空列表表示该事件的全部动作。',
    )
    format_mode = models.CharField(
        '通知格式', max_length=16, choices=NotificationFormatMode.choices,
        default=NotificationFormatMode.DETAILED,
    )
    title_template = models.CharField(
        '标题模板', max_length=256, blank=True,
        help_text='可选，支持事件变量；留空使用事件的默认标题格式。',
    )
    template = models.TextField(
        '正文模板', blank=True,
        help_text='自定义正文，支持前端列出的事件变量；留空使用精简或详细默认格式。',
    )
    dedupe_seconds = models.PositiveSmallIntegerField(
        '重复抑制窗口(秒)', default=0,
        help_text='0 表示不抑制；同一事件、对象和动作在窗口内仅发送一次。',
    )
    remark = models.CharField('备注', max_length=256, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '通知规则'
        verbose_name_plural = '通知规则'
        ordering = ['event', '-created_at']

    def __str__(self):
        return f'{self.name} [{", ".join(self.event_values())}]'

    def event_values(self) -> list[str]:
        """返回去重后的事件列表，并兼容旧规则尚未回填 events 的情况。"""
        raw_events = self.events if isinstance(self.events, list) else []
        values = []
        valid_events = {value for value, _label in NotificationEvent.choices}
        for value in raw_events:
            value = str(value).strip()
            if value in valid_events and value not in values:
                values.append(value)
        if not values and self.event:
            values.append(self.event)
        return values

    def matches_event(self, event: str) -> bool:
        from .notification_templates import event_is_subscribed
        return event_is_subscribed(self.event_values(), event)

    def action_filters_for_event(self, event: str) -> list[str]:
        """读取某个事件的专属动作筛选；子事件未配置时回退父事件，再回退旧字段。"""
        from .notification_templates import parent_events_of
        filters = self.event_action_filters if isinstance(self.event_action_filters, dict) else {}
        for key in (event, *parent_events_of(event)):
            values = filters.get(key)
            if isinstance(values, list):
                return [str(value) for value in values if str(value).strip()]
        return [
            str(value) for value in (self.action_filters or [])
            if str(value).strip()
        ]


class IMMessageLog(models.Model):
    """IM 消息发送日志。"""

    channel = models.ForeignKey(
        IMChannel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='message_logs', verbose_name='通道',
    )
    channel_type = models.CharField('通道类型', max_length=16, blank=True)
    event = models.CharField('事件', max_length=32, blank=True)
    target = models.CharField('接收目标', max_length=256, blank=True)
    title = models.CharField('标题', max_length=256, blank=True)
    content = models.TextField('内容', blank=True)
    success = models.BooleanField('是否成功', default=False)
    response = models.TextField('响应/错误', blank=True)
    created_at = models.DateTimeField('时间', auto_now_add=True)

    class Meta:
        verbose_name = 'IM 消息日志'
        verbose_name_plural = 'IM 消息日志'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} → {self.target} ({"OK" if self.success else "FAIL"})'


class WeComAIBotMessage(models.Model):
    """智能机器人长连接待发送队列，由常驻连接进程实际投递。"""

    class Status(models.TextChoices):
        PENDING = 'pending', '待发送'
        SENDING = 'sending', '发送中'
        SENT = 'sent', '已发送'
        FAILED = 'failed', '发送失败'

    channel = models.ForeignKey(
        IMChannel, on_delete=models.CASCADE, related_name='aibot_messages',
        verbose_name='智能机器人通道',
    )
    target = models.CharField('接收成员 UserID', max_length=256)
    title = models.CharField('标题', max_length=256, blank=True)
    content = models.TextField('内容')
    event = models.CharField('事件', max_length=32, blank=True)
    status = models.CharField('状态', max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField('尝试次数', default=0)
    response = models.TextField('响应/错误', blank=True)
    sent_at = models.DateTimeField('发送时间', null=True, blank=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '企业微信智能机器人消息'
        verbose_name_plural = '企业微信智能机器人消息'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['channel', 'status', 'created_at']),
        ]

    def __str__(self):
        return f'{self.channel.name} → {self.target} ({self.get_status_display()})'


class SystemBackup(models.Model):
    """业务数据备份记录（库转储归档）。"""

    class BackupType(models.TextChoices):
        DATA = 'data', '业务数据'
        AUTO = 'auto', '升级前自动备份'
        UPLOAD = 'upload', '上传备份'

    class Status(models.TextChoices):
        PENDING = 'pending', '排队中'
        RUNNING = 'running', '进行中'
        SUCCESS = 'success', '成功'
        FAILED = 'failed', '失败'

    name = models.CharField('名称', max_length=160)
    backup_type = models.CharField(
        '类型', max_length=16, choices=BackupType.choices, default=BackupType.DATA,
    )
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.PENDING,
    )
    file_name = models.CharField('文件名', max_length=255, blank=True, default='')
    storage_path = models.CharField('存储路径', max_length=512, blank=True, default='')
    file_size = models.BigIntegerField('文件大小', default=0)
    checksum_sha256 = models.CharField('SHA256', max_length=64, blank=True, default='')
    source_version = models.CharField('备份时版本', max_length=32, blank=True, default='')
    note = models.CharField('备注', max_length=255, blank=True, default='')
    error_message = models.TextField('错误信息', blank=True, default='')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='system_backups', verbose_name='操作人',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)

    class Meta:
        verbose_name = '系统备份'
        verbose_name_plural = '系统备份'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['backup_type']),
        ]

    def __str__(self):
        return self.name


class SystemUpgradePackage(models.Model):
    """系统更新包与升级记录。"""

    class Status(models.TextChoices):
        UPLOADED = 'uploaded', '已上传'
        READY = 'ready', '待升级'
        APPLYING = 'applying', '升级中'
        SUCCESS = 'success', '已完成'
        FAILED = 'failed', '失败'
        REJECTED = 'rejected', '校验失败'

    filename = models.CharField('文件名', max_length=255)
    storage_path = models.CharField('存储路径', max_length=512, blank=True, default='')
    file_size = models.BigIntegerField('文件大小', default=0)
    checksum_sha256 = models.CharField('SHA256', max_length=64, blank=True, default='')
    package_version = models.CharField('包版本', max_length=32, blank=True, default='')
    min_version = models.CharField('最低当前版本', max_length=32, blank=True, default='')
    changelog = models.TextField('更新说明', blank=True, default='')
    status = models.CharField(
        '状态', max_length=16, choices=Status.choices, default=Status.UPLOADED,
    )
    apply_log = models.TextField('升级日志', blank=True, default='')
    error_message = models.TextField('错误信息', blank=True, default='')
    from_version = models.CharField('升级前版本', max_length=32, blank=True, default='')
    to_version = models.CharField('目标版本', max_length=32, blank=True, default='')
    auto_backup = models.ForeignKey(
        SystemBackup, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='upgrade_packages', verbose_name='升级前备份',
    )
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='uploaded_system_upgrades', verbose_name='上传人',
    )
    applied_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='applied_system_upgrades', verbose_name='执行人',
    )
    uploaded_at = models.DateTimeField('上传时间', auto_now_add=True)
    applied_at = models.DateTimeField('开始升级', null=True, blank=True)
    finished_at = models.DateTimeField('完成时间', null=True, blank=True)

    class Meta:
        verbose_name = '系统更新包'
        verbose_name_plural = '系统更新包'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['status', 'uploaded_at']),
        ]

    def __str__(self):
        return self.filename


class SystemOsTemplate(models.Model):
    """中心系统模版目录：产品、门户下单与 POP 下发共用同一套版本。"""

    class OSType(models.TextChoices):
        DEBIAN = 'debian', 'Debian'
        UBUNTU = 'ubuntu', 'Ubuntu'
        CENTOS = 'centos', 'CentOS'
        RHEL = 'rhel', 'RHEL'
        WINDOWS = 'windows', 'Windows Server'
        OTHER = 'other', '其他'

    class SourceType(models.TextChoices):
        CLOUD = 'cloud', 'Cloud 镜像'
        BACKUP = 'backup', 'PVE 备份'
        ISO = 'iso', 'ISO 安装盘'
        VZTMPL = 'vztmpl', 'LXC 模板'

    name = models.CharField('系统名称', max_length=128)
    version = models.CharField('版本', max_length=64)
    backup_filename = models.CharField(
        '镜像文件名', max_length=255,
        help_text='PVE 存储中的 Cloud 镜像文件名，如 noble-server-cloudimg-amd64.img',
    )
    download_url = models.URLField(
        '下载地址', max_length=512,
        help_text='POP 缺失该版本时按此 URL 下载，例如 https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
    )
    code = models.SlugField('模版代码', max_length=32, unique=True, blank=True)
    os_type = models.CharField(
        '系统类型', max_length=16,
        choices=OSType.choices, default=OSType.OTHER,
    )
    source_type = models.CharField(
        '文件类型', max_length=16,
        choices=SourceType.choices, default=SourceType.CLOUD,
    )
    enabled = models.BooleanField('启用', default=True)
    sort_order = models.PositiveSmallIntegerField('排序', default=0)
    remark = models.CharField('备注', max_length=255, blank=True, default='')
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '系统模版'
        verbose_name_plural = '系统模版'
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['name', 'version'], name='uniq_system_os_template_name_version'),
        ]

    def __str__(self):
        return self.display_label

    @property
    def display_label(self) -> str:
        return f'{self.name} {self.version}'.strip()

    def save(self, *args, **kwargs):
        from .os_templates import filename_from_url, infer_os_type, make_unique_code, sync_os_image

        if not (self.backup_filename or '').strip() and self.download_url:
            self.backup_filename = filename_from_url(self.download_url)
        self.backup_filename = (self.backup_filename or '').strip()
        self.download_url = (self.download_url or '').strip()
        if not self.code:
            self.code = make_unique_code(self.name, self.version, exclude_pk=self.pk)
        if not self.os_type or self.os_type == self.OSType.OTHER:
            inferred = infer_os_type(self.name)
            if inferred != self.OSType.OTHER:
                self.os_type = inferred
            elif not self.os_type:
                self.os_type = self.OSType.OTHER
        super().save(*args, **kwargs)
        sync_os_image(self)

    def delete(self, *args, **kwargs):
        from service_manager.models import OsImage

        OsImage.objects.filter(os_template=self).update(enabled=False)
        return super().delete(*args, **kwargs)
