from __future__ import annotations

import logging
import re
import threading
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

_tls = threading.local()

SENSITIVE_KEYS = frozenset({
    'password', 'old_password', 'new_password', 'confirm_password',
    'token', 'access', 'refresh', 'secret', 'api_key', 'apikey',
    'authorization', 'private_key', 'passwd', 'current_password',
    'client_secret', 'webhook_secret', 'app_secret',
})

ACTION_TYPE_LABELS: dict[str, str] = {
    'login': '登录',
    'login_failed': '登录失败',
    'change_password': '修改密码',
    'reset_password': '重置密码',
    'create': '新建',
    'update': '更新',
    'delete': '删除',
    'status_change': '状态变更',
    'deploy': '部署',
    'restart': '重启',
    'ip_assign': 'IP 分配',
    'ip_release': 'IP 释放',
    'bw_alloc': '带宽分配',
    'config_change': '配置变更',
    'import': '批量导入',
    'export': '数据导出',
    'create_order': '创建工单',
    'submit_order': '提交工单',
    'approve_order': '审批工单',
    'reject_order': '驳回工单',
    'assign_handler': '指派处理人',
    'stage_change': '阶段变更',
    'provision': '开通资源',
    'activate': '激活服务',
    'suspend': '暂停服务',
    'resume': '恢复服务',
    'terminate': '终止服务',
    'renew': '续期服务',
    'change': '变更服务',
    'start_trial': '开始测试',
    'convert_billing': '测试转正',
    'assign_ip': '分配 IP',
    'release_ip': '释放 IP',
    'bw_adjust': '调整带宽',
    'fee_adjust': '资费调整',
    'payment': '收款',
    'contract_sign': '合同签订',
    'contact': '联系跟进',
    'publish': '发布',
    'online': '上线',
    'offline': '下线',
    'archive': '归档',
    'test_connection': '测试连接',
    'create_host': '创建 Host',
    'update_host': '更新 Host',
    'delete_host': '删除 Host',
    'sync_items': '同步监控项',
    'sync_graphs': '同步图形',
    'sync_history': '同步历史',
    'traffic_query': '流量查询',
    'billing_compute': '计费计算',
    'bind_graphs': '绑定图形',
    'full_sync': '全量同步',
    'test': '连接测试',
    'pull_resources': '拉取资源',
    'pull_products': '拉取产品',
    'pull_pops': '拉取 POP',
    'map': '映射同步',
    'sync': '同步',
    'notify': '消息通知',
    'execute': '执行',
    'other': '其他',
}

MODULE_LABELS: dict[str, str] = {
    'accounts': '账号管理',
    'customer': '客户管理',
    'billing': '账单管理',
    'resource': '资源管理',
    'service': '服务管理',
    'product': '产品管理',
    'supplier': '供应商管理',
    'operation': '运维管理',
    'system': '系统设置',
    'portal': '客户门户',
}

PATH_MODULE_PREFIXES: tuple[tuple[str, str], ...] = (
    ('/api/auth/', 'accounts'),
    ('/api/accounts/', 'accounts'),
    ('/api/resource/', 'resource'),
    ('/api/service/', 'service'),
    ('/api/product/', 'product'),
    ('/api/supplier/', 'supplier'),
    ('/api/operation/', 'operation'),
    ('/api/system/', 'system'),
    ('/api/portal/', 'portal'),
)

BILLING_PATH_HINTS = (
    '/billings', '/payment', '/wallet', '/refund', '/ledger',
)

_TARGET_RE = re.compile(
    r'^/api/[^/]+/([^/]+)/(?:(\d+)(?:/([^/?]+))?)?',
)


def reset_request_logged() -> None:
    _tls.logged = False


def mark_request_logged(request=None) -> None:
    _tls.logged = True
    if request is not None:
        setattr(request, '_audit_logged', True)


def is_request_logged(request=None) -> bool:
    if request is not None and getattr(request, '_audit_logged', False):
        return True
    return bool(getattr(_tls, 'logged', False))


def get_client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()[:45]
    return (request.META.get('REMOTE_ADDR') or '')[:45] or None


def operator_display_name(user) -> str:
    if not user or not getattr(user, 'is_authenticated', True):
        return '系统'
    return (user.get_full_name() or user.username or '系统')[:64]


def sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return '…'
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')[:200]
    if isinstance(value, dict):
        cleaned = {}
        for key, item in list(value.items())[:80]:
            name = str(key).lower()
            if name in SENSITIVE_KEYS or 'password' in name or name.endswith('_secret'):
                cleaned[str(key)] = '***'
            else:
                cleaned[str(key)] = sanitize_payload(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_payload(item, depth=depth + 1) for item in list(value)[:50]]
    return str(value)[:500]


def infer_module_from_path(path: str) -> str:
    path = path or ''
    if path.startswith('/api/customer/'):
        if any(hint in path for hint in BILLING_PATH_HINTS):
            return 'billing'
        return 'customer'
    for prefix, module in PATH_MODULE_PREFIXES:
        if path.startswith(prefix):
            return module
    return 'system'


def infer_action_from_request(method: str, path: str) -> str:
    segment = (path or '').rstrip('/').rsplit('/', 1)[-1]
    special = {
        'reset_password': 'reset_password',
        'change_password': 'change_password',
        'toggle_active': 'status_change',
        'generate_ips': 'ip_assign',
        'publish': 'publish',
        'sync': 'sync',
        'test': 'test',
        'send': 'notify',
        'dispatch': 'notify',
        'bulk_update': 'update',
    }
    if segment in special and not segment.isdigit():
        return special[segment]
    return {
        'POST': 'create',
        'PUT': 'update',
        'PATCH': 'update',
        'DELETE': 'delete',
    }.get((method or '').upper(), 'other')


def parse_api_target(path: str) -> tuple[str, str]:
    match = _TARGET_RE.match(path or '')
    if not match:
        return '', ''
    return match.group(1) or '', match.group(2) or ''


def action_type_label(action_type: str) -> str:
    if not action_type:
        return '其他'
    return ACTION_TYPE_LABELS.get(action_type, action_type)


def record_audit_log(
    *,
    module: str,
    action_type: str,
    description: str,
    operator=None,
    operator_name: str = '',
    target_model: str = '',
    target_id=None,
    target_repr: str = '',
    detail: dict | None = None,
    ip_address: str | None = None,
    request=None,
    request_path: str = '',
    request_method: str = '',
    source: str = 'manual',
    source_ref: str = '',
    created_at=None,
    mark_request: bool = True,
):
    """写入一条审计日志。source + source_ref 非空时幂等。"""
    from .models import AuditLog

    if request is not None:
        if ip_address is None:
            ip_address = get_client_ip(request)
        if not request_path:
            request_path = (getattr(request, 'path', '') or '')[:512]
        if not request_method:
            request_method = getattr(request, 'method', '') or ''
        user = getattr(request, 'user', None)
        if operator is None and user is not None and getattr(user, 'is_authenticated', False):
            operator = user
    if mark_request:
        mark_request_logged(request)

    if operator is not None and not getattr(operator, 'pk', None):
        operator = None
    if not operator_name:
        operator_name = operator_display_name(operator) if operator else '系统'

    payload = {
        'operator': operator,
        'operator_name': str(operator_name)[:64],
        'module': (module or AuditLog.Module.SYSTEM)[:20],
        'action_type': str(action_type or 'other')[:32],
        'target_model': str(target_model or '')[:64],
        'target_id': '' if target_id in (None, '') else str(target_id)[:64],
        'target_repr': str(target_repr or '')[:256],
        'description': str(description or '')[:512],
        'detail': detail if isinstance(detail, dict) else {},
        'ip_address': ip_address or None,
        'request_path': str(request_path or '')[:512],
        'request_method': str(request_method or '')[:8],
        'created_at': created_at or timezone.now(),
    }
    ref = '' if source_ref in (None, '') else str(source_ref)[:64]
    source = str(source or AuditLog.Source.MANUAL)[:32]

    try:
        if ref:
            obj, _created = AuditLog.objects.get_or_create(
                source=source,
                source_ref=ref,
                defaults=payload,
            )
            return obj
        return AuditLog.objects.create(source=source, source_ref='', **payload)
    except Exception:
        logger.exception('写入审计日志失败')
        return None
