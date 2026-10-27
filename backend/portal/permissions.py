"""portal/permissions.py — 客户门户访问控制"""
from rest_framework.permissions import BasePermission

from accounts.permissions import (
    HasPermission,
    check_permission,
    get_user_customer,
    is_customer_account,
)


class IsCustomer(BasePermission):
    """仅「归属客户账号」或超级管理员可访问。"""
    message = '该接口仅对客户账号开放'

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return bool(user.is_superuser or is_customer_account(user))


def request_customer(request):
    """获取当前请求用户关联的 Customer（无则 None）。"""
    return get_user_customer(request.user)


def customer_can(request, permission: str) -> bool:
    """门户写操作鉴权：客户角色矩阵 + 安全域裁剪。"""
    return check_permission(request.user, permission)


class PortalActionPermissionMixin:
    """按 ViewSet action 叠加角色权限，与后台权限码同一套函数。"""

    permission_classes = [IsCustomer]
    action_permissions = {}

    def get_permissions(self):
        perms = [IsCustomer()]
        action = getattr(self, 'action', None)
        code = self.action_permissions.get(action)
        if code:
            perms.append(HasPermission.with_perm(code)())
        return perms
