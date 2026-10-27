"""
accounts/models.py
用户扩展档案 & API 访问令牌
"""
import secrets
import hashlib
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    """扩展用户信息（one-to-one 关联 auth.User）"""

    class AccountType(models.TextChoices):
        SYSTEM = 'system', '系统账号'
        CUSTOMER = 'customer', '归属客户账号'

    class Role(models.TextChoices):
        SUPERADMIN = 'superadmin', '超级管理员'
        ADMIN = 'admin', '管理员'
        CUSTOMER_MANAGER = 'customer_manager', '客户管理员'
        OPERATOR = 'operator', '运维人员'
        SALES = 'sales', '销售人员'
        CUSTOMER_USER = 'customer', '客户用户'
        SUPPLIER = 'supplier', '供应商'
        CHANNEL = 'channel', '渠道'
        READONLY = 'readonly', '只读用户'

    # 兼容旧代码引用
    ROLE_CHOICES = Role.choices
    ACCOUNT_TYPE_CHOICES = AccountType.choices

    user         = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='profile', verbose_name='账号',
    )
    account_type = models.CharField(
        '账号类型', max_length=16,
        choices=AccountType.choices, default=AccountType.SYSTEM,
        help_text='系统账号：内部员工；归属客户账号：绑定单一客户的 portal 登录账号',
    )
    phone        = models.CharField('手机号', max_length=20, blank=True, default='')
    avatar       = models.URLField('头像URL', blank=True, default='')
    department   = models.CharField('部门', max_length=50, blank=True, default='')
    position     = models.CharField('职位', max_length=50, blank=True, default='')
    role         = models.CharField(
        '系统角色', max_length=20,
        choices=Role.choices, default=Role.OPERATOR,
    )
    supplier     = models.ForeignKey(
        'supplier_manager.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='portal_accounts',
        verbose_name='绑定供应商',
        help_text='角色为「供应商」时必填，账号仅能维护该供应商的产品与资源',
    )
    channel      = models.ForeignKey(
        'customer_manager.Customer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='channel_accounts',
        verbose_name='绑定渠道客户',
        help_text='角色为「渠道」时必填，账号仅能维护该渠道名下客户、下单并查看佣金结算',
    )
    remark       = models.TextField('备注', blank=True, default='')
    last_seen_at = models.DateTimeField(
        '最近操作时间', null=True, blank=True,
        help_text='登录或用户操作后更新；超过空闲时限需重新登录',
    )
    created_at   = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at   = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '用户档案'
        verbose_name_plural = '用户档案'
        indexes = [
            models.Index(fields=['account_type']),
            models.Index(fields=['role']),
        ]

    def __str__(self):
        return f'{self.user.username} [{self.get_account_type_display()} · {self.get_role_display()}]'

    @property
    def is_system_account(self) -> bool:
        return self.account_type == self.AccountType.SYSTEM

    @property
    def is_customer_account(self) -> bool:
        return self.account_type == self.AccountType.CUSTOMER

    def get_permissions(self) -> frozenset[str]:
        from .role_permissions import get_permissions_for_user
        return get_permissions_for_user(self)

    def has_permission(self, permission: str) -> bool:
        return permission in self.get_permissions()

    def sync_account_type_from_customer_link(self, save: bool = True) -> bool:
        """
        根据 User ↔ Customer 关联同步 account_type。
        若 User 被 Customer.account 引用，则为归属客户账号。
        """
        linked = hasattr(self.user, 'customer') and self.user.customer is not None
        new_type = self.AccountType.CUSTOMER if linked else self.AccountType.SYSTEM
        changed = self.account_type != new_type
        if changed:
            self.account_type = new_type
            if save:
                self.save(update_fields=['account_type', 'updated_at'])
        return changed


class APIToken(models.Model):
    """
    用户生成的长期 API 访问令牌（类似 GitHub PAT）。
    认证格式：Authorization: Token <raw_token>
    令牌以 SHA-256 散列存储，原始值仅创建时展示一次。
    """
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_tokens', verbose_name='所属用户')
    name        = models.CharField('令牌名称', max_length=100, help_text='用于标识此令牌的描述，如"前端访问"')
    token_hash  = models.CharField('令牌散列', max_length=64, unique=True, db_index=True, editable=False)
    token_prefix = models.CharField('令牌前缀', max_length=8, editable=False, help_text='令牌前8位明文，用于识别')
    expires_at  = models.DateTimeField('过期时间', null=True, blank=True, help_text='留空表示永不过期')
    is_active   = models.BooleanField('是否启用', default=True)
    created_at  = models.DateTimeField('创建时间', auto_now_add=True)
    last_used_at = models.DateTimeField('最后使用时间', null=True, blank=True)
    last_used_ip = models.GenericIPAddressField('最后使用IP', null=True, blank=True)

    class Meta:
        verbose_name = 'API 令牌'
        verbose_name_plural = 'API 令牌'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} / {self.name} ({self.token_prefix}...)'

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        return timezone.now() > self.expires_at

    @property
    def is_valid(self):
        return self.is_active and not self.is_expired

    @classmethod
    def generate(cls, user, name, expires_at=None):
        """
        生成新令牌，返回 (instance, raw_token)。
        raw_token 格式：ispm_<40位随机串>
        """
        raw = 'ispm_' + secrets.token_urlsafe(30)
        token_hash = cls._hash(raw)
        instance = cls.objects.create(
            user=user,
            name=name,
            token_hash=token_hash,
            token_prefix=raw[:8],
            expires_at=expires_at,
        )
        return instance, raw

    @classmethod
    def _hash(cls, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @classmethod
    def authenticate(cls, raw_token: str):
        """根据原始令牌查找并验证，返回 User 或 None。"""
        try:
            token_hash = cls._hash(raw_token)
            token = cls.objects.select_related('user').get(
                token_hash=token_hash,
                is_active=True,
                user__is_active=True,
            )
            if token.is_expired:
                return None, token
            return token.user, token
        except cls.DoesNotExist:
            return None, None

    def update_last_used(self, ip=None):
        self.last_used_at = timezone.now()
        self.last_used_ip = ip
        self.save(update_fields=['last_used_at', 'last_used_ip'])


class PermissionGroup(models.Model):
    """权限功能模块（客户管理、资源管理等）"""
    key = models.CharField('模块标识', max_length=32, unique=True)
    label = models.CharField('模块名称', max_length=100)
    description = models.TextField('说明', blank=True, default='')
    sort_order = models.IntegerField('排序', default=0)
    is_builtin = models.BooleanField('内置', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '权限模块'
        verbose_name_plural = '权限模块'
        ordering = ['sort_order', 'key']

    def __str__(self):
        return self.label


class PermissionPage(models.Model):
    """模块下的管理页面"""
    group = models.ForeignKey(
        PermissionGroup, on_delete=models.CASCADE,
        related_name='pages', verbose_name='所属模块',
    )
    key = models.CharField('页面标识', max_length=64)
    label = models.CharField('页面名称', max_length=100)
    route = models.CharField('路由', max_length=200, blank=True, default='')
    sort_order = models.IntegerField('排序', default=0)
    is_builtin = models.BooleanField('内置', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '权限页面'
        verbose_name_plural = '权限页面'
        ordering = ['sort_order', 'key']
        unique_together = [('group', 'key')]

    def __str__(self):
        return f'{self.group.key}.{self.key}'


class PermissionDefinition(models.Model):
    """原子权限定义"""

    class Scope(models.TextChoices):
        MODULE = 'module', '模块'
        PAGE = 'page', '页面'
        SETTING_PAGE = 'setting_page', '参数配置页'
        SETTING_CATEGORY = 'setting_category', '配置分类'
        SETTING_KEY = 'setting_key', '配置项'
        CUSTOM = 'custom', '自定义'

    code = models.CharField('权限码', max_length=120, primary_key=True)
    label = models.CharField('权限名称', max_length=200)
    action = models.CharField('操作', max_length=16, choices=[('view', '查看'), ('manage', '管理')])
    scope = models.CharField('范围', max_length=32, choices=Scope.choices, default=Scope.PAGE)
    group_key = models.CharField('模块标识', max_length=32, db_index=True)
    page_key = models.CharField('页面标识', max_length=64, blank=True, default='')
    setting_category = models.CharField('配置分类', max_length=32, blank=True, default='')
    setting_key = models.CharField('配置键', max_length=64, blank=True, default='')
    is_builtin = models.BooleanField('内置', default=True)
    is_active = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        verbose_name = '权限定义'
        verbose_name_plural = '权限定义'
        ordering = ['group_key', 'sort_order', 'code']

    def __str__(self):
        return self.code


class RolePermissionGrant(models.Model):
    """角色拥有的权限（可在线配置）"""
    role = models.CharField('角色', max_length=20, db_index=True, choices=UserProfile.Role.choices)
    permission = models.ForeignKey(
        PermissionDefinition, on_delete=models.CASCADE,
        related_name='role_grants', verbose_name='权限',
    )
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        verbose_name = '角色权限'
        verbose_name_plural = '角色权限'
        unique_together = [('role', 'permission')]

    def __str__(self):
        return f'{self.role} → {self.permission_id}'
