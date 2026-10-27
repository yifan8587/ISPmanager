"""
accounts/drf_mixins.py
DRF ViewSet 权限与归属客户数据隔离 Mixin
"""
from __future__ import annotations

from django.db.models import Q, QuerySet
from rest_framework.permissions import IsAuthenticated

from .permissions import (
    HasPermission,
    channel_owned_customer_ids,
    get_user_customer,
    is_channel_role,
    require_bound_channel,
)
from .role_permissions import Perm


# 默认视为「只读/查看」的 action，走 view_permission
DEFAULT_READ_ACTIONS = frozenset({
    'list', 'retrieve', 'logs', 'statistics', 'brief', 'brief_list',
    'map_overview', 'preview_ips', 'bandwidth', 'ip_addresses',
    'pve_resources', 'history', 'graph', 'series', 'traffic',
    'export', 'import_template',
})


class ModulePermissionMixin:
    """
    按 action 区分 view / manage 权限。
    子类设置 view_permission、manage_permission，可选 extra_read_actions。
    """

    view_permission: str = ''
    manage_permission: str = ''
    extra_read_actions: frozenset[str] = frozenset()

    def get_read_actions(self) -> frozenset[str]:
        return DEFAULT_READ_ACTIONS | self.extra_read_actions

    def get_permissions(self):
        if self.action in self.get_read_actions():
            perm_cls = HasPermission.with_perm(self.view_permission)
        else:
            perm_cls = HasPermission.with_perm(self.manage_permission)
        return [IsAuthenticated(), perm_cls()]


class ViewOnlyPermissionMixin:
    """只读 ViewSet：始终要求 view_permission。"""

    view_permission: str = ''

    def get_permissions(self):
        return [
            IsAuthenticated(),
            HasPermission.with_perm(self.view_permission)(),
        ]


class SystemAccountPermissionMixin:
    """
    基础设施类 ViewSet：除模块权限外，要求系统内部账号。
    归属客户 portal 账号不可访问（如 POP 配置、路由、运维监控等）。
    """

    def get_permissions(self):
        from .permissions import IsSystemAccount
        base = super().get_permissions()
        return [IsAuthenticated(), IsSystemAccount()] + base[1:]


class CustomerScopedQuerysetMixin:
    """
    归属客户账号仅能访问与自身 customer 关联的数据。

    配置方式（择一）：
      - customer_scope_field = 'customer'           → filter(customer=...)
      - customer_scope_field = 'self'               → filter(pk=customer.pk)
      - customer_scope_field = 'service_order__customer'
      - 覆写 get_customer_scope_q(customer) 返回 Q 对象
    """

    customer_scope_field: str = 'customer'

    def get_customer_scope_q(self, customer) -> Q | None:
        """子类可覆写以提供复杂 Q 过滤；返回 None 则使用 customer_scope_field。"""
        return None

    def apply_customer_scope(self, queryset: QuerySet) -> QuerySet:
        customer = get_user_customer(self.request.user)
        if customer is not None:
            custom_q = self.get_customer_scope_q(customer)
            if custom_q is not None:
                return queryset.filter(custom_q)
            field = self.customer_scope_field
            if field == 'self':
                return queryset.filter(pk=customer.pk)
            return queryset.filter(**{field: customer})

        if not is_channel_role(self.request.user):
            return queryset
        channel = require_bound_channel(self.request.user)
        ids = channel_owned_customer_ids(channel)
        if not ids:
            return queryset.none()
        field = self.customer_scope_field
        if field == 'self':
            return queryset.filter(pk__in=ids)
        if field == 'channel':
            return queryset.filter(channel=channel)
        if field == 'account__channel':
            return queryset.filter(account__channel=channel)
        if field.endswith('__channel') or field.endswith('_channel'):
            return queryset.filter(**{field: channel})
        if '__' in field:
            return queryset.filter(**{f'{field}__in': ids})
        return queryset.filter(**{f'{field}_id__in': ids})

    def enforce_customer_create_scope(self, validated_data: dict) -> dict:
        """创建/更新时强制写入归属客户（防止 portal 账号越权）。"""
        customer = get_user_customer(self.request.user)
        if customer is None:
            return validated_data
        field = self.customer_scope_field
        if field == 'self':
            return validated_data
        if field.endswith('_id'):
            validated_data[field] = customer.pk
        elif field == 'customer':
            validated_data['customer'] = customer
        elif '__' in field:
            # 如 service_order__customer — 创建场景由业务 serializer 处理
            pass
        return validated_data


# ── 各模块快捷 Mixin ──────────────────────────────────────────

class CustomerModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.CUSTOMER_VIEW
    manage_permission = Perm.CUSTOMER_MANAGE
    extra_read_actions = frozenset({'logs'})


class BillingModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.BILLING_VIEW
    manage_permission = Perm.BILLING_MANAGE


class ResourceModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.RESOURCE_VIEW
    manage_permission = Perm.RESOURCE_MANAGE
    extra_read_actions = frozenset({
        'logs', 'statistics', 'bandwidth', 'ip_addresses', 'preview_ips',
        'map_overview', 'pve_resources', 'vnc_ticket',
    })


class ResourceSystemPermissionMixin(SystemAccountPermissionMixin, ResourceModulePermissionMixin):
    """资源基础设施：仅系统账号 + 资源权限。"""


class ServiceModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.SERVICE_VIEW
    manage_permission = Perm.SERVICE_MANAGE
    extra_read_actions = frozenset({'logs', 'statistics', 'brief', 'brief_list'})


class ServiceSystemPermissionMixin(SystemAccountPermissionMixin, ServiceModulePermissionMixin):
    """服务基础设施（统计 / 账单预览 / 开通 Hook）：仅系统账号。"""


class ProductModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.PRODUCT_VIEW
    manage_permission = Perm.PRODUCT_MANAGE
    extra_read_actions = frozenset({
        'logs', 'statistics', 'brief', 'brief_list',
        'capacity', 'stock', 'quote', 'publish_logs', 'os_templates',
    })


class SupplierModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.SUPPLIER_VIEW
    manage_permission = Perm.SUPPLIER_MANAGE
    extra_read_actions = frozenset({
        'logs', 'statistics', 'brief', 'brief_list', 'sync_logs', 'resources',
        'accounts', 'finance_summary', 'bills',
    })


class OperationModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.OPERATION_VIEW
    manage_permission = Perm.OPERATION_MANAGE
    extra_read_actions = frozenset({'history', 'graph', 'series', 'traffic', 'sync'})


class OperationSystemPermissionMixin(SystemAccountPermissionMixin, OperationModulePermissionMixin):
    """运维模块：仅系统内部账号 + 运维权限。"""


class SystemModulePermissionMixin(ModulePermissionMixin):
    view_permission = Perm.SYSTEM_VIEW
    manage_permission = Perm.SYSTEM_MANAGE
    extra_read_actions = frozenset({'grouped', 'public_settings', 'categories'})


class SystemSystemPermissionMixin(SystemAccountPermissionMixin, SystemModulePermissionMixin):
    """系统设置：仅系统内部账号 + 系统权限。"""


def customer_pop_ids(customer) -> list:
    """客户名下 VPS / 边缘加速涉及的 POP 点 ID。"""
    from resource_manager.models import VPS, EdgeAcceleration
    vps_pops = VPS.objects.filter(customer=customer).values_list('pop_point_id', flat=True)
    edge_pops = EdgeAcceleration.objects.filter(customer=customer).values_list('pop_point_id', flat=True)
    return list(set(vps_pops) | set(edge_pops))
