"""
accounts/serializers.py
用户管理 & API Token 序列化器
"""
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import UserProfile, APIToken
from .permissions import build_user_auth_context, get_effective_permissions
from .role_permissions import (
    CUSTOMER_ACCOUNT_ALLOWED_ROLES,
    CUSTOMER_ACCOUNT_DEFAULT_ROLE,
    ROLE_LABELS,
)


# ─────────────────────────────────────────────────────────────
# UserProfile Serializer
# ─────────────────────────────────────────────────────────────

class UserProfileSerializer(serializers.ModelSerializer):
    role_label = serializers.CharField(source='get_role_display', read_only=True)
    account_type_label = serializers.CharField(source='get_account_type_display', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'phone', 'avatar', 'department', 'position',
            'role', 'role_label', 'account_type', 'account_type_label', 'remark',
        ]


# ─────────────────────────────────────────────────────────────
# User Serializers
# ─────────────────────────────────────────────────────────────

class UserListSerializer(serializers.ModelSerializer):
    """用户列表（只读简要信息）"""
    full_name         = serializers.SerializerMethodField()
    role              = serializers.SerializerMethodField()
    role_label        = serializers.SerializerMethodField()
    account_type      = serializers.SerializerMethodField()
    account_type_label = serializers.SerializerMethodField()
    department        = serializers.SerializerMethodField()
    phone             = serializers.SerializerMethodField()
    position          = serializers.SerializerMethodField()
    permissions       = serializers.SerializerMethodField()
    customer_id       = serializers.SerializerMethodField()
    customer_name     = serializers.SerializerMethodField()
    supplier_id       = serializers.SerializerMethodField()
    supplier_name     = serializers.SerializerMethodField()
    channel_id        = serializers.SerializerMethodField()
    channel_name      = serializers.SerializerMethodField()
    reputation_mode   = serializers.SerializerMethodField()
    reputation_quota  = serializers.SerializerMethodField()
    reputation_balance = serializers.SerializerMethodField()
    token_count       = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'full_name',
            'is_staff', 'is_superuser', 'is_active',
            'account_type', 'account_type_label',
            'role', 'role_label', 'permissions',
            'department', 'phone', 'position',
            'customer_id', 'customer_name',
            'supplier_id', 'supplier_name',
            'channel_id', 'channel_name',
            'reputation_mode', 'reputation_quota', 'reputation_balance',
            'date_joined', 'last_login', 'token_count',
        ]

    def _profile(self, obj):
        try:
            return obj.profile
        except UserProfile.DoesNotExist:
            return None

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def get_role(self, obj):
        p = self._profile(obj)
        return p.role if p else UserProfile.Role.OPERATOR

    def get_role_label(self, obj):
        p = self._profile(obj)
        if p:
            return p.get_role_display()
        return ROLE_LABELS.get(UserProfile.Role.OPERATOR, '运营人员')

    def get_account_type(self, obj):
        p = self._profile(obj)
        return p.account_type if p else UserProfile.AccountType.SYSTEM

    def get_account_type_label(self, obj):
        p = self._profile(obj)
        if p:
            return p.get_account_type_display()
        return '系统账号'

    def get_department(self, obj):
        p = self._profile(obj)
        return p.department if p else ''

    def get_phone(self, obj):
        p = self._profile(obj)
        return p.phone if p else ''

    def get_position(self, obj):
        p = self._profile(obj)
        return p.position if p else ''

    def get_permissions(self, obj):
        return sorted(get_effective_permissions(obj))

    def get_customer_id(self, obj):
        customer = getattr(obj, 'customer', None)
        return customer.id if customer else None

    def get_customer_name(self, obj):
        customer = getattr(obj, 'customer', None)
        return customer.name if customer else None

    def get_supplier_id(self, obj):
        p = self._profile(obj)
        return p.supplier_id if p and p.supplier_id else None

    def get_supplier_name(self, obj):
        p = self._profile(obj)
        supplier = getattr(p, 'supplier', None) if p else None
        return supplier.name if supplier else None

    def get_channel_id(self, obj):
        p = self._profile(obj)
        return p.channel_id if p and p.channel_id else None

    def get_channel_name(self, obj):
        p = self._profile(obj)
        channel = getattr(p, 'channel', None) if p else None
        return channel.name if channel else None

    def _reputation(self, obj):
        from customer_manager.reputation import serialize_reputation
        return serialize_reputation(getattr(obj, 'customer', None))

    def get_reputation_mode(self, obj):
        return self._reputation(obj).get('reputation_mode')

    def get_reputation_quota(self, obj):
        return self._reputation(obj).get('reputation_quota')

    def get_reputation_balance(self, obj):
        return self._reputation(obj).get('reputation_balance')

    def get_token_count(self, obj):
        return obj.api_tokens.filter(is_active=True).count()


class UserCreateSerializer(serializers.Serializer):
    """创建用户"""
    username     = serializers.CharField(max_length=150)
    password     = serializers.CharField(write_only=True, min_length=8)
    email        = serializers.EmailField(required=False, allow_blank=True, default='')
    first_name   = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    last_name    = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    is_staff     = serializers.BooleanField(default=False)
    is_superuser = serializers.BooleanField(default=False)
    account_type = serializers.ChoiceField(
        choices=UserProfile.AccountType.choices,
        default=UserProfile.AccountType.SYSTEM,
    )
    customer_id  = serializers.IntegerField(required=False, allow_null=True, default=None)
    supplier_id  = serializers.IntegerField(required=False, allow_null=True, default=None)
    channel_id   = serializers.IntegerField(required=False, allow_null=True, default=None)
    # Profile
    phone        = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    department   = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    position     = serializers.CharField(max_length=50, required=False, allow_blank=True, default='')
    role         = serializers.ChoiceField(choices=UserProfile.Role.choices, default=UserProfile.Role.OPERATOR)
    remark       = serializers.CharField(required=False, allow_blank=True, default='')
    reputation_mode = serializers.CharField(required=False, allow_blank=True)
    reputation_quota = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    reputation_balance = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('用户名已存在')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        account_type = data.get('account_type', UserProfile.AccountType.SYSTEM)
        customer_id = data.get('customer_id')
        role = data.get('role', UserProfile.Role.OPERATOR)

        if account_type == UserProfile.AccountType.CUSTOMER:
            if not customer_id:
                raise serializers.ValidationError({'customer_id': '归属客户账号必须指定关联客户'})
            if role not in CUSTOMER_ACCOUNT_ALLOWED_ROLES:
                allowed = '、'.join(ROLE_LABELS.get(r, r) for r in sorted(CUSTOMER_ACCOUNT_ALLOWED_ROLES))
                raise serializers.ValidationError({
                    'role': f'归属客户账号仅允许角色：{allowed}',
                })
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('归属客户账号不能设置为管理员')
        elif customer_id:
            raise serializers.ValidationError({'customer_id': '仅归属客户账号可绑定客户'})

        if customer_id:
            from customer_manager.models import Customer
            try:
                customer = Customer.objects.get(pk=customer_id)
            except Customer.DoesNotExist:
                raise serializers.ValidationError({'customer_id': '客户不存在'})
            if customer.account_id:
                raise serializers.ValidationError({'customer_id': '该客户已绑定其他登录账号'})
            data['_customer'] = customer

        supplier_id = data.get('supplier_id')
        if role == UserProfile.Role.SUPPLIER:
            if not supplier_id:
                raise serializers.ValidationError({'supplier_id': '供应商角色必须绑定供应商'})
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('供应商账号不能设置为管理员')
        if supplier_id:
            from supplier_manager.models import Supplier
            try:
                data['_supplier'] = Supplier.objects.get(pk=supplier_id)
            except Supplier.DoesNotExist:
                raise serializers.ValidationError({'supplier_id': '供应商不存在'})

        channel_id = data.get('channel_id')
        if role == UserProfile.Role.CHANNEL:
            if not channel_id:
                raise serializers.ValidationError({'channel_id': '渠道角色必须绑定渠道客户'})
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('渠道账号不能设置为管理员')
        if channel_id:
            from customer_manager.models import Customer, CustomerType
            try:
                channel = Customer.objects.get(pk=channel_id)
            except Customer.DoesNotExist:
                raise serializers.ValidationError({'channel_id': '渠道客户不存在'})
            if channel.customer_type != CustomerType.CHANNEL:
                raise serializers.ValidationError({'channel_id': '只能绑定类型为「渠道客户」的档案'})
            data['_channel'] = channel

        return data

    def create(self, validated_data):
        customer = validated_data.pop('_customer', None)
        supplier = validated_data.pop('_supplier', None)
        channel = validated_data.pop('_channel', None)
        customer_id = validated_data.pop('customer_id', None)
        validated_data.pop('supplier_id', None)
        validated_data.pop('channel_id', None)
        reputation_payload = {
            key: validated_data.pop(key)
            for key in ('reputation_mode', 'reputation_quota', 'reputation_balance')
            if key in validated_data
        }
        profile_fields = {
            k: validated_data.pop(k, '')
            for k in ['phone', 'department', 'position', 'remark']
        }
        account_type = validated_data.pop('account_type', UserProfile.AccountType.SYSTEM)
        role = validated_data.pop('role', UserProfile.Role.OPERATOR)
        if account_type == UserProfile.AccountType.CUSTOMER and role not in CUSTOMER_ACCOUNT_ALLOWED_ROLES:
            role = CUSTOMER_ACCOUNT_DEFAULT_ROLE
        password = validated_data.pop('password')

        user = User(**validated_data)
        user.set_password(password)
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        for k, v in profile_fields.items():
            setattr(profile, k, v)
        profile.role = role
        profile.account_type = account_type
        profile.supplier = supplier if role == UserProfile.Role.SUPPLIER else None
        profile.channel = channel if role == UserProfile.Role.CHANNEL else None
        profile.save()

        if customer:
            customer.account = user
            customer.save(update_fields=['account', 'updated_at'])
            from .signals import sync_profile_account_type
            sync_profile_account_type(user)
            if reputation_payload:
                from customer_manager.reputation import apply_reputation_settings
                apply_reputation_settings(
                    customer,
                    mode=reputation_payload.get('reputation_mode'),
                    quota=reputation_payload.get('reputation_quota'),
                    balance=reputation_payload.get('reputation_balance'),
                )

        return user


class UserUpdateSerializer(serializers.Serializer):
    """更新用户（管理员）"""
    email        = serializers.EmailField(required=False, allow_blank=True)
    first_name   = serializers.CharField(max_length=50, required=False, allow_blank=True)
    last_name    = serializers.CharField(max_length=50, required=False, allow_blank=True)
    is_staff     = serializers.BooleanField(required=False)
    is_superuser = serializers.BooleanField(required=False)
    is_active    = serializers.BooleanField(required=False)
    account_type = serializers.ChoiceField(choices=UserProfile.AccountType.choices, required=False)
    customer_id  = serializers.IntegerField(required=False, allow_null=True)
    supplier_id  = serializers.IntegerField(required=False, allow_null=True)
    channel_id   = serializers.IntegerField(required=False, allow_null=True)
    # Profile
    phone        = serializers.CharField(max_length=20, required=False, allow_blank=True)
    department   = serializers.CharField(max_length=50, required=False, allow_blank=True)
    position     = serializers.CharField(max_length=50, required=False, allow_blank=True)
    role         = serializers.ChoiceField(choices=UserProfile.Role.choices, required=False)
    remark       = serializers.CharField(required=False, allow_blank=True)
    avatar       = serializers.URLField(required=False)
    reputation_mode = serializers.CharField(required=False, allow_blank=True)
    reputation_quota = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    reputation_balance = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    def validate(self, data):
        instance = self.instance
        profile = getattr(instance, 'profile', None)
        account_type = data.get('account_type', getattr(profile, 'account_type', UserProfile.AccountType.SYSTEM))
        role = data.get('role', getattr(profile, 'role', UserProfile.Role.OPERATOR))

        if account_type == UserProfile.AccountType.CUSTOMER or (
            profile and profile.account_type == UserProfile.AccountType.CUSTOMER
        ):
            effective_role = role
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('归属客户账号不能设置为管理员')
            if effective_role not in CUSTOMER_ACCOUNT_ALLOWED_ROLES:
                allowed = '、'.join(ROLE_LABELS.get(r, r) for r in sorted(CUSTOMER_ACCOUNT_ALLOWED_ROLES))
                raise serializers.ValidationError({
                    'role': f'归属客户账号仅允许角色：{allowed}',
                })

        if 'customer_id' in data and data['customer_id']:
            from customer_manager.models import Customer
            try:
                customer = Customer.objects.get(pk=data['customer_id'])
            except Customer.DoesNotExist:
                raise serializers.ValidationError({'customer_id': '客户不存在'})
            if customer.account_id and customer.account_id != instance.id:
                raise serializers.ValidationError({'customer_id': '该客户已绑定其他登录账号'})
            data['_customer'] = customer

        effective_role = role
        supplier_id = data.get('supplier_id', getattr(profile, 'supplier_id', None))
        if effective_role == UserProfile.Role.SUPPLIER:
            if not supplier_id:
                raise serializers.ValidationError({'supplier_id': '供应商角色必须绑定供应商'})
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('供应商账号不能设置为管理员')
        if 'supplier_id' in data and data['supplier_id']:
            from supplier_manager.models import Supplier
            try:
                data['_supplier'] = Supplier.objects.get(pk=data['supplier_id'])
            except Supplier.DoesNotExist:
                raise serializers.ValidationError({'supplier_id': '供应商不存在'})

        channel_id = data.get('channel_id', getattr(profile, 'channel_id', None))
        if effective_role == UserProfile.Role.CHANNEL:
            if not channel_id:
                raise serializers.ValidationError({'channel_id': '渠道角色必须绑定渠道客户'})
            if data.get('is_staff') or data.get('is_superuser'):
                raise serializers.ValidationError('渠道账号不能设置为管理员')
        if 'channel_id' in data and data['channel_id']:
            from customer_manager.models import Customer, CustomerType
            try:
                channel = Customer.objects.get(pk=data['channel_id'])
            except Customer.DoesNotExist:
                raise serializers.ValidationError({'channel_id': '渠道客户不存在'})
            if channel.customer_type != CustomerType.CHANNEL:
                raise serializers.ValidationError({'channel_id': '只能绑定类型为「渠道客户」的档案'})
            data['_channel'] = channel

        return data

    def update(self, instance, validated_data):
        customer = validated_data.pop('_customer', None)
        supplier = validated_data.pop('_supplier', None)
        channel = validated_data.pop('_channel', None)
        customer_unbind = 'customer_id' in validated_data and validated_data.pop('customer_id') is None
        supplier_unbind = 'supplier_id' in validated_data and validated_data.pop('supplier_id') is None
        channel_unbind = 'channel_id' in validated_data and validated_data.pop('channel_id') is None

        profile_keys = ['phone', 'department', 'position', 'role', 'remark', 'avatar', 'account_type']
        profile_data = {k: validated_data.pop(k) for k in profile_keys if k in validated_data}
        reputation_payload = {
            key: validated_data.pop(key)
            for key in ('reputation_mode', 'reputation_quota', 'reputation_balance')
            if key in validated_data
        }

        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()

        if profile_data or supplier is not None or supplier_unbind or channel is not None or channel_unbind:
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            for k, v in profile_data.items():
                setattr(profile, k, v)
            if profile.role == UserProfile.Role.SUPPLIER:
                if supplier is not None:
                    profile.supplier = supplier
                elif supplier_unbind:
                    profile.supplier = None
            else:
                profile.supplier = None
            if profile.role == UserProfile.Role.CHANNEL:
                if channel is not None:
                    profile.channel = channel
                elif channel_unbind:
                    profile.channel = None
            else:
                profile.channel = None
            profile.save()

        if customer:
            customer.account = instance
            customer.save(update_fields=['account', 'updated_at'])
        elif customer_unbind and hasattr(instance, 'customer'):
            instance.customer.account = None
            instance.customer.save(update_fields=['account', 'updated_at'])

        target = customer or getattr(instance, 'customer', None)
        if reputation_payload and target is not None:
            from customer_manager.reputation import apply_reputation_settings
            apply_reputation_settings(
                target,
                mode=reputation_payload.get('reputation_mode'),
                quota=reputation_payload.get('reputation_quota'),
                balance=reputation_payload.get('reputation_balance'),
            )

        from .signals import sync_profile_account_type
        sync_profile_account_type(instance)
        return instance


class AdminResetPasswordSerializer(serializers.Serializer):
    """管理员重置他人密码"""
    new_password     = serializers.CharField(min_length=8, write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': '两次密码不一致'})
        validate_password(data['new_password'])
        return data


class RoleCatalogSerializer(serializers.Serializer):
    """角色权限目录（只读）"""
    value = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())
    permission_groups = serializers.ListField()


# ─────────────────────────────────────────────────────────────
# APIToken Serializers
# ─────────────────────────────────────────────────────────────

class APITokenListSerializer(serializers.ModelSerializer):
    """令牌列表（不含散列）"""
    username   = serializers.CharField(source='user.username', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    is_valid   = serializers.BooleanField(read_only=True)

    class Meta:
        model = APIToken
        fields = [
            'id', 'username', 'name', 'token_prefix', 'is_active', 'is_expired', 'is_valid',
            'expires_at', 'created_at', 'last_used_at', 'last_used_ip',
        ]


class APITokenCreateSerializer(serializers.Serializer):
    """创建令牌请求"""
    name       = serializers.CharField(max_length=100, help_text='令牌名称，如"CI/CD流水线"')
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None,
                                           help_text='过期时间（ISO 8601），留空永不过期')

    def validate_name(self, value):
        return value.strip()


class APITokenCreateResponseSerializer(serializers.Serializer):
    """创建令牌响应（含一次性 raw token）"""
    id         = serializers.IntegerField()
    name       = serializers.CharField()
    token      = serializers.CharField(help_text='原始令牌，请立即保存，此后不再显示')
    token_prefix = serializers.CharField()
    expires_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


def serialize_auth_user(user) -> dict:
    """登录 / me 使用的完整用户序列化。"""
    ctx = build_user_auth_context(user)
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'full_name': user.get_full_name() or user.username,
        'is_staff': user.is_staff,
        'is_superuser': user.is_superuser,
        'is_active': user.is_active,
        'last_login': user.last_login.isoformat() if user.last_login else None,
        **ctx,
    }
