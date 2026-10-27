"""WebSocket 访问判定，与 REST 的账号类型 / 权限码对齐。"""
from __future__ import annotations

from .permissions import (
    check_any_permission,
    get_user_customer,
    is_customer_account,
    is_system_account,
)
from .role_permissions import Perm, page_permission

VPS_CONSOLE_PERMS = (
    'resource.vps.manage',
    page_permission('resource', 'vps_console', 'manage'),
    Perm.RESOURCE_MANAGE,
)
POP_SSH_PERMS = (
    Perm.RESOURCE_MANAGE,
    Perm.OPERATION_MANAGE,
    page_permission('resource', 'pop', 'manage'),
    page_permission('operation', 'webssh', 'manage'),
)
OPERATION_SSH_PERMS = (
    Perm.OPERATION_MANAGE,
    page_permission('operation', 'webssh', 'manage'),
)


def _is_authenticated(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False))


def user_can_open_vps_console(user, vps) -> bool:
    if not _is_authenticated(user):
        return False
    if not check_any_permission(user, VPS_CONSOLE_PERMS):
        return False
    if is_customer_account(user):
        customer = get_user_customer(user)
        return bool(customer and vps.customer_id == customer.pk)
    return bool(user.is_superuser or is_system_account(user))


def user_can_open_pop_ssh(user) -> bool:
    if not _is_authenticated(user) or is_customer_account(user):
        return False
    if not (user.is_superuser or is_system_account(user)):
        return False
    return check_any_permission(user, POP_SSH_PERMS)


def user_can_open_operation_ssh(user) -> bool:
    if not _is_authenticated(user) or is_customer_account(user):
        return False
    if not (user.is_superuser or is_system_account(user)):
        return False
    return check_any_permission(user, OPERATION_SSH_PERMS)


def authorize_vps_console(user, vps_id):
    """返回 ('ok', payload) / ('not_found', None) / ('forbidden', None)。"""
    from resource_manager.models import VPS

    try:
        vps = VPS.objects.select_related('pop_point').get(pk=vps_id)
    except (VPS.DoesNotExist, TypeError, ValueError):
        return 'not_found', None
    if not user_can_open_vps_console(user, vps):
        return 'forbidden', None
    pop = vps.pop_point
    return 'ok', {
        'source_ref': vps.source_ref or '',
        'api_base_url': pop.api_base_url or '',
        'mgmt_ip': pop.mgmt_ip or '',
        'api_username': pop.api_username or pop.mgmt_username or '',
        'api_password': pop.api_password or pop.mgmt_password or '',
        'mgmt_token': pop.mgmt_token or '',
        'api_verify_ssl': bool(getattr(pop, 'api_verify_ssl', False)),
    }
