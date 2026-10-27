"""
accounts/permissions.py
自定义 DRF 权限类 & 用户权限工具函数
"""
from __future__ import annotations

from typing import Iterable

from rest_framework.permissions import BasePermission

from .models import UserProfile
from .role_permissions import (
    Perm,
    get_permissions_for_user,
    user_has_any_permission,
    user_has_permission,
)


# ── 工具函数 ──────────────────────────────────────────────────

def get_user_profile(user) -> UserProfile | None:
    """获取用户档案，不存在时返回 None。"""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def ensure_user_profile(user) -> UserProfile:
    """确保用户档案存在并返回。"""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def get_effective_permissions(user) -> frozenset[str]:
    profile = get_user_profile(user)
    if profile is None:
        if user and user.is_superuser:
            from .role_permissions import ALL_PERMISSIONS
            return ALL_PERMISSIONS
        return frozenset()
    return get_permissions_for_user(profile)


def check_permission(user, permission: str) -> bool:
    if user and user.is_superuser:
        return True
    profile = get_user_profile(user)
    if profile is None:
        return False
    return user_has_permission(profile, permission)


def check_any_permission(user, permissions: Iterable[str]) -> bool:
    if user and user.is_superuser:
        return True
    profile = get_user_profile(user)
    if profile is None:
        return False
    return user_has_any_permission(profile, permissions)


def is_system_account(user) -> bool:
    profile = get_user_profile(user)
    return profile is None or profile.account_type == UserProfile.AccountType.SYSTEM


def is_customer_account(user) -> bool:
    profile = get_user_profile(user)
    return profile is not None and profile.account_type == UserProfile.AccountType.CUSTOMER


def get_user_customer(user):
    """归属客户账号关联的 Customer 实例；系统账号返回 None。"""
    if not is_customer_account(user):
        return None
    return getattr(user, 'customer', None)


def is_supplier_role(user) -> bool:
    """系统供应商角色（非超管）。用于数据隔离与菜单收敛。"""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return False
    profile = get_user_profile(user)
    return bool(profile and (
        profile.role == UserProfile.Role.SUPPLIER or profile.supplier_id
    ))


def get_user_supplier(user):
    """供应商角色绑定的 Supplier；其他角色返回 None。"""
    if not is_supplier_role(user):
        return None
    profile = get_user_profile(user)
    return getattr(profile, 'supplier', None) if profile else None


def is_channel_role(user) -> bool:
    """系统渠道角色（非超管）。用于数据隔离与菜单收敛。"""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return False
    profile = get_user_profile(user)
    return bool(profile and (
        profile.role == UserProfile.Role.CHANNEL or profile.channel_id
    ))


def get_user_channel(user):
    """渠道角色绑定的渠道客户；其他角色返回 None。"""
    if not is_channel_role(user):
        return None
    profile = get_user_profile(user)
    return getattr(profile, 'channel', None) if profile else None


def require_bound_channel(user):
    if not is_channel_role(user):
        return None
    channel = get_user_channel(user)
    if channel is None:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('渠道账号未绑定渠道客户，无法操作')
    return channel


def channel_owned_customer_ids(channel) -> list:
    if channel is None:
        return []
    from customer_manager.models import CustomerReferral
    ids = [channel.pk]
    ids.extend(
        CustomerReferral.objects.filter(channel=channel).values_list('customer_id', flat=True)
    )
    return ids


def channel_owns_customer(channel, customer) -> bool:
    if not channel or customer is None:
        return False
    customer_id = getattr(customer, 'pk', customer)
    return customer_id in set(channel_owned_customer_ids(channel))


def deny_channel_finance_write(user, message='渠道账号仅可查看消费、佣金与结算，不能登记或作废'):
    from rest_framework.exceptions import PermissionDenied
    if is_channel_role(user):
        raise PermissionDenied(message)


def apply_supplier_scope(request, queryset, q_fn):
    """供应商账号仅能看到 q_fn(supplier) 命中的数据。"""
    from django.db.models import Q

    user = getattr(request, 'user', None)
    if not is_supplier_role(user):
        return queryset
    supplier = get_user_supplier(user)
    q = q_fn(supplier)
    if q is None:
        return queryset.none()
    if q == Q(pk__in=[]):
        return queryset.none()
    return queryset.filter(q).distinct()


def supplier_owned_q(supplier, *lookups):
    from django.db.models import Q

    if supplier is None:
        return Q(pk__in=[])
    q = Q()
    for lookup in lookups:
        q |= Q(**{lookup: supplier})
    return q


def require_bound_supplier(user):
    """供应商角色必须已绑定供应商；未绑定则拒绝。非供应商返回 None。"""
    if not is_supplier_role(user):
        return None
    supplier = get_user_supplier(user)
    if supplier is None:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('供应商账号未绑定供应商，无法操作')
    return supplier


def supplier_owns_pop(supplier, pop) -> bool:
    if not supplier or pop is None:
        return False
    if getattr(pop, 'supplier_id', None) == supplier.pk:
        return True
    bindings = getattr(pop, 'supplier_bindings', None)
    if bindings is None:
        return False
    return bindings.filter(supplier=supplier).exists()


def supplier_scope_pop_q(supplier):
    from django.db.models import Q

    if supplier is None:
        return Q(pk__in=[])
    return Q(supplier=supplier) | Q(supplier_bindings__supplier=supplier)


def supplier_scope_product_q(supplier):
    from django.db.models import Q

    if supplier is None:
        return Q(pk__in=[])
    pop_owned = Q(pop_point__supplier=supplier) | Q(pop_point__supplier_bindings__supplier=supplier)
    return Q(supplier=supplier) | (Q(supplier__isnull=True) & pop_owned)


def supplier_scope_via_pop_q(supplier, pop_lookup='pop_point'):
    from django.db.models import Q

    if supplier is None:
        return Q(pk__in=[])
    return (
        Q(**{f'{pop_lookup}__supplier': supplier})
        | Q(**{f'{pop_lookup}__supplier_bindings__supplier': supplier})
    )


def ensure_supplier_pop_binding(pop, supplier):
    """把 POP 挂到本供应商，并写入 SupplierPopBinding。"""
    if not pop or not supplier:
        return
    if pop.supplier_id != supplier.pk:
        pop.supplier = supplier
        pop.save(update_fields=['supplier'])
    from supplier_manager.models import SupplierPopBinding
    SupplierPopBinding.objects.get_or_create(
        supplier=supplier,
        pop_point=pop,
        defaults={'is_active': True},
    )


def deny_supplier_role(user, message='供应商账号无权访问此功能'):
    from rest_framework.exceptions import PermissionDenied

    if is_supplier_role(user):
        raise PermissionDenied(message)


def deny_supplier_write(view, request, message='供应商账号无权修改此数据'):
    if not is_supplier_role(getattr(request, 'user', None)):
        return
    from accounts.drf_mixins import DEFAULT_READ_ACTIONS

    read = view.get_read_actions() if hasattr(view, 'get_read_actions') else DEFAULT_READ_ACTIONS
    if getattr(view, 'action', None) not in read:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(message)


def build_user_auth_context(user) -> dict:
    """构建登录 / me 接口使用的权限上下文。"""
    profile = ensure_user_profile(user)
    permissions = sorted(get_permissions_for_user(profile))
    customer = get_user_customer(user)
    supplier = get_user_supplier(user)
    channel = get_user_channel(user)
    from customer_manager.reputation import serialize_reputation
    reputation = serialize_reputation(customer or channel)
    return {
        'role': profile.role,
        'role_label': profile.get_role_display(),
        'account_type': profile.account_type,
        'account_type_label': profile.get_account_type_display(),
        'permissions': permissions,
        'department': profile.department,
        'phone': profile.phone,
        'position': profile.position,
        'avatar': profile.avatar,
        'customer_id': customer.id if customer else None,
        'customer_name': customer.name if customer else None,
        'supplier_id': supplier.id if supplier else None,
        'supplier_name': supplier.name if supplier else None,
        'is_supplier_account': is_supplier_role(user),
        'channel_id': channel.id if channel else None,
        'channel_name': channel.name if channel else None,
        'is_channel_account': is_channel_role(user),
        **reputation,
    }


# ── DRF 权限类 ────────────────────────────────────────────────

class IsAdminUser(BasePermission):
    """
    系统管理员：超级用户 / is_staff / 拥有 accounts.manage 权限。
    用于用户管理、Token 总览等管理接口。
    """
    message = '仅系统管理员可执行此操作'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        return check_permission(user, Perm.ACCOUNTS_MANAGE)


class IsSuperAdmin(BasePermission):
    """仅 Django 超级管理员（is_superuser）可访问。"""
    message = '仅超级管理员可执行此操作'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class IsSystemAccount(BasePermission):
    """仅系统内部账号（非归属客户 portal 账号）。"""
    message = '归属客户账号无权访问此功能'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and is_system_account(user))


class HasPermission(BasePermission):
    """
    要求拥有指定权限码。
    用法：permission_classes = [IsAuthenticated, HasPermission.with_perm(Perm.CUSTOMER_MANAGE)]
    """
    permission_code: str = ''
    message = '权限不足，无法执行此操作'

    @classmethod
    def with_perm(cls, permission_code: str):
        code = permission_code

        class _HasPermission(cls):
            permission_code = code

        _HasPermission.__name__ = f'HasPermission_{code.replace(".", "_")}'
        return _HasPermission

    def has_permission(self, request, view):
        return check_permission(request.user, self.permission_code)


class HasAnyPermission(BasePermission):
    """拥有给定权限之一即可访问。"""
    permission_codes: tuple[str, ...] = ()
    message = '权限不足，无法执行此操作'

    @classmethod
    def with_perms(cls, *permission_codes: str):
        codes = permission_codes

        class _HasAnyPermission(cls):
            permission_codes = codes

        _HasAnyPermission.__name__ = f'HasAnyPermission_{"_".join(c.replace(".", "-") for c in codes)}'
        return _HasAnyPermission

    def has_permission(self, request, view):
        return check_any_permission(request.user, self.permission_codes)


class RoleRequired(BasePermission):
    """要求用户角色在允许列表中（仅系统账号）。"""
    allowed_roles: tuple[str, ...] = ()
    message = '当前角色无权执行此操作'

    @classmethod
    def with_roles(cls, *roles: str):
        allowed = roles

        class _RoleRequired(cls):
            allowed_roles = allowed

        _RoleRequired.__name__ = f'RoleRequired_{"_".join(allowed)}'
        return _RoleRequired

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        profile = get_user_profile(user)
        if profile is None:
            return False
        if profile.account_type != UserProfile.AccountType.SYSTEM:
            return False
        return profile.role in self.allowed_roles
