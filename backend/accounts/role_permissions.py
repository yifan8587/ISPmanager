"""
accounts/role_permissions.py
权限组定义 & 角色 → 权限映射

权限码层级（由粗到细，上级可覆盖下级）：
  1. 模块级：resource.view / resource.manage
  2. 页面级：resource.pop.view / resource.pop.manage
  3. 配置分类：system.settings.general.view
  4. 配置项：system.setting.site_name.manage

原子动作仅两种，语义固定：
  · view   = 只读（浏览列表/详情）
  · manage = 写操作（新增/修改/删除/审批/下发/执行）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from system_manager.default_settings import DEFAULT_SYSTEM_SETTINGS


# ── 原子动作说明（前后端统一展示）──────────────────────────────

ACTION_META: dict[str, dict] = {
    'view': {
        'key': 'view',
        'label': '查看',
        'short_label': '查',
        'description': '只读：可浏览列表与详情，不可新增、修改、删除或执行操作',
        'examples': ['打开页面', '查看详情', '导出只读报表'],
    },
    'manage': {
        'key': 'manage',
        'label': '管理',
        'short_label': '管',
        'description': '写操作：新增、修改、删除、审批、下发、执行等高风险操作',
        'examples': ['创建/编辑', '删除', '审批通过', '节点下发', '电源操作'],
    },
}


# ── 模块级权限常量（兼容旧版）──────────────────────────────────

class Perm:
    ACCOUNTS_VIEW = 'accounts.view'
    ACCOUNTS_MANAGE = 'accounts.manage'
    CUSTOMER_VIEW = 'customer.view'
    CUSTOMER_MANAGE = 'customer.manage'
    BILLING_VIEW = 'billing.view'
    BILLING_MANAGE = 'billing.manage'
    RESOURCE_VIEW = 'resource.view'
    RESOURCE_MANAGE = 'resource.manage'
    SERVICE_VIEW = 'service.view'
    SERVICE_MANAGE = 'service.manage'
    PRODUCT_VIEW = 'product.view'
    PRODUCT_MANAGE = 'product.manage'
    SUPPLIER_VIEW = 'supplier.view'
    SUPPLIER_MANAGE = 'supplier.manage'
    OPERATION_VIEW = 'operation.view'
    OPERATION_MANAGE = 'operation.manage'
    SYSTEM_VIEW = 'system.view'
    SYSTEM_MANAGE = 'system.manage'


MODULE_PERMISSIONS: frozenset[str] = frozenset({
    Perm.ACCOUNTS_VIEW, Perm.ACCOUNTS_MANAGE,
    Perm.CUSTOMER_VIEW, Perm.CUSTOMER_MANAGE,
    Perm.BILLING_VIEW, Perm.BILLING_MANAGE,
    Perm.RESOURCE_VIEW, Perm.RESOURCE_MANAGE,
    Perm.SERVICE_VIEW, Perm.SERVICE_MANAGE,
    Perm.PRODUCT_VIEW, Perm.PRODUCT_MANAGE,
    Perm.SUPPLIER_VIEW, Perm.SUPPLIER_MANAGE,
    Perm.OPERATION_VIEW, Perm.OPERATION_MANAGE,
    Perm.SYSTEM_VIEW, Perm.SYSTEM_MANAGE,
})

VIEW_PERMISSIONS: frozenset[str] = frozenset(
    p for p in MODULE_PERMISSIONS if p.endswith('.view')
)
MANAGE_PERMISSIONS: frozenset[str] = frozenset(
    p for p in MODULE_PERMISSIONS if p.endswith('.manage')
)


# ── 页面注册表（原子页面权限）──────────────────────────────────

PAGE_REGISTRY: dict[str, dict] = {
    'accounts': {
        'label': '账号管理',
        'description': '系统用户、角色权限与 API Token',
        'pages': {
            'user': {
                'label': '系统用户',
                'route': '/system/users',
                'description': '创建/禁用系统账号、分配角色、重置密码、管理 Token',
            },
            'role': {
                'label': '角色与权限',
                'route': '/system/roles',
                'description': '维护权限目录、配置角色授权、查看本人权限',
            },
        },
    },
    'customer': {
        'label': '客户管理',
        'description': '客户档案、渠道推广、合同与联系记录',
        'pages': {
            'list': {'label': '客户列表', 'route': '/customer/list', 'description': '客户档案查询与维护'},
            'channel': {'label': '渠道推广与佣金', 'route': '/customer/channels', 'description': '渠道规则、佣金与结算'},
            'contract': {'label': '合同管理', 'route': '/customer/contracts', 'description': '合同登记与状态'},
            'detail': {'label': '客户详情', 'route': '/customer/:id', 'description': '单个客户详情与跟进'},
        },
    },
    'billing': {
        'label': '账单管理',
        'description': '客户账单、收款与计费明细',
        'pages': {
            'list': {'label': '账单列表', 'route': '/customer/billings', 'description': '账单查询、收款登记与核销'},
        },
    },
    'resource': {
        'label': '资源管理',
        'description': 'POP、IP、VPS、边缘加速与带宽资源',
        'pages': {
            'pop': {'label': 'POP 节点', 'route': '/resource/pop', 'description': '节点档案、状态与容量'},
            'pop_deploy': {'label': 'POP 部署', 'route': '/resource/pop/:id/deploy', 'description': '节点部署与配置下发'},
            'ip': {'label': 'IP 管理', 'route': '/resource/ip', 'description': 'IP 块、地址分配与隧道 IP 段'},
            'vps': {'label': 'VPS 管理', 'route': '/resource/vps', 'description': 'VPS 实例档案与规格'},
            'vps_console': {'label': 'VPS 控制台', 'route': '/resource/vps/:id/console', 'description': '电源、VNC、密码等高危运维'},
            'edge': {'label': '边缘加速', 'route': '/resource/edge', 'description': '边缘加速实例与协议配置'},
            'bandwidth': {'label': '带宽管理', 'route': '/resource/bandwidth', 'description': '带宽分配与限速'},
        },
    },
    'operation': {
        'label': '运维管理',
        'description': '监控、流量计费与运维工具',
        'pages': {
            'monitor_hosts': {'label': '监控主机', 'route': '/operation/monitoring/hosts', 'description': '监控主机同步与绑定'},
            'pop_quality': {'label': 'POP 品质监控', 'route': '/operation/monitoring/pop-quality', 'description': '节点品质探测与告警'},
            'monitor_graphs': {'label': '监控图形', 'route': '/operation/monitoring/hosts/:id/graphs', 'description': '时序图与监控项'},
            'traffic': {'label': '流量截取计费', 'route': '/operation/monitoring/traffic', 'description': '流量截取与 95 计费报告'},
            'traffic_profile': {'label': '计费规则', 'route': '/operation/monitoring/profiles', 'description': '流量图与计费规则模版'},
            'zabbix': {'label': 'Zabbix 服务器', 'route': '/operation/monitoring/zabbix', 'description': 'Zabbix 连接与同步'},
            'webssh': {'label': 'WebSSH', 'route': '/operation/tools/webssh', 'description': '浏览器终端登录（高危）'},
            'template': {'label': '命令模版', 'route': '/operation/tools/templates', 'description': '运维命令模版库'},
        },
    },
    'service': {
        'label': '服务管理',
        'description': '服务请求、履约、实例、变更、官网咨询与技术工单',
        'pages': {
            'overview': {'label': '服务全景', 'route': '/service/overview', 'description': '服务运营总览'},
            'order': {'label': '服务工单', 'route': '/service/orders', 'description': '服务请求审批与开通'},
            'inquiry': {'label': '官网咨询', 'route': '/service/inquiries', 'description': '官网留言与客服跟进'},
            'ticket': {'label': '技术工单', 'route': '/service/tickets', 'description': '客户技术/滥用/财务/接入工单与 SLA'},
            'instance': {'label': '服务实例', 'route': '/service/instances', 'description': '已开通实例与生命周期'},
            'selfservice': {'label': '自助任务', 'route': '/service/self-service', 'description': '门户重装/快照任务与镜像目录'},
            'change': {'label': '变更记录', 'route': '/service/changes', 'description': '升降配变更审批与执行'},
        },
    },
    'product': {
        'label': '产品管理',
        'description': '产品规格、模版、定价与发布',
        'pages': {
            'spec': {'label': '产品规格', 'route': '/product/specs', 'description': '规格维度定义'},
            'template': {'label': '产品模版', 'route': '/product/templates', 'description': '可复用产品模版'},
            'product': {'label': '产品列表', 'route': '/product/products', 'description': '上架产品与定价'},
            'publish': {'label': '产品发布', 'route': '/product/publish', 'description': '产品上架/下架发布'},
        },
    },
    'supplier': {
        'label': '供应商管理',
        'description': '供应商档案、API 对接与资源同步',
        'pages': {
            'list': {'label': '供应商列表', 'route': '/supplier/list', 'description': '供应商档案'},
            'api': {'label': '供应商 API', 'route': '/supplier/apis', 'description': '对接凭证与接口'},
            'resource': {'label': '供应商资源', 'route': '/supplier/resources', 'description': '外部资源同步'},
            'account': {'label': '供应商账号', 'route': '/supplier/list', 'description': '为供应商创建仅可维护产品/POP/IP 的登录账号'},
            'billing': {'label': '供应商账单结算', 'route': '/supplier/bills', 'description': '供应商侧账单查看、结算比例与满额结算'},
        },
    },
    'system': {
        'label': '系统设置',
        'description': '全局参数、通知对接、操作日志、版本与备份升级',
        'pages': {
            'settings': {'label': '参数配置', 'route': '/system/settings', 'description': '站点、对外客服、账单、安全、对内通知等参数'},
            'notify': {'label': '通知 / IM 对接', 'route': '/system/notifications', 'description': '对内告警通道与推送规则，不用于门户客服展示'},
            'log': {'label': '操作日志', 'route': '/system/logs', 'description': '全站操作审计：按模块查看时间、人员、内容与详情'},
            'maintenance': {
                'label': '系统维护',
                'route': '/system/maintenance',
                'description': '系统版本、业务数据备份还原与在线升级',
            },
            'os_template': {
                'label': '系统模版',
                'route': '/system/os-templates',
                'description': 'VPS 系统名称、版本、备份文件与下载地址，供产品与门户共用',
            },
        },
    },
}

SETTING_CATEGORY_LABELS: dict[str, str] = {
    'general': '基本设置',
    'support': '对外客服',
    'billing': '账单设置',
    'security': '安全设置',
    'notification': '对内通知',
    'integration': '集成设置',
}

SETTING_KEY_META: dict[str, dict] = {
    item['key']: {
        'label': item['label'],
        'category': item['category'],
        'description': item.get('description', ''),
    }
    for item in DEFAULT_SYSTEM_SETTINGS
}


def page_permission(module: str, page: str, action: str) -> str:
    return f'{module}.{page}.{action}'


def setting_category_permission(category: str, action: str) -> str:
    return f'system.settings.{category}.{action}'


def setting_key_permission(key: str, action: str) -> str:
    return f'system.setting.{key}.{action}'


def action_label(action: str, *, target: str = '', short: bool = False) -> str:
    meta = ACTION_META.get(action) or {'label': action, 'short_label': action}
    base = meta['short_label'] if short else meta['label']
    if not target:
        return base
    if action == 'view':
        return f'查看「{target}」'
    return f'管理「{target}」'


def _build_page_permissions() -> dict[str, str]:
    perms: dict[str, str] = {}
    for module, mod in PAGE_REGISTRY.items():
        for page_key, page in mod['pages'].items():
            perms[page_permission(module, page_key, 'view')] = action_label('view', target=page['label'])
            perms[page_permission(module, page_key, 'manage')] = (
                f"{action_label('manage', target=page['label'])}（新增/修改/删除/执行）"
            )
    return perms


def _build_setting_permissions() -> dict[str, str]:
    perms: dict[str, str] = {}
    perms['system.settings.view'] = '查看「参数配置」页'
    perms['system.settings.manage'] = '管理「参数配置」页（可改全部参数）'
    for cat, label in SETTING_CATEGORY_LABELS.items():
        perms[setting_category_permission(cat, 'view')] = f'查看「{label}」分类'
        perms[setting_category_permission(cat, 'manage')] = f'修改「{label}」分类下参数'
    for key, meta in SETTING_KEY_META.items():
        perms[setting_key_permission(key, 'view')] = f"查看参数「{meta['label']}」"
        perms[setting_key_permission(key, 'manage')] = f"修改参数「{meta['label']}」"
    return perms


PAGE_PERMISSIONS: dict[str, str] = _build_page_permissions()
SETTING_PERMISSIONS: dict[str, str] = _build_setting_permissions()
ATOMIC_PERMISSIONS: frozenset[str] = frozenset(PAGE_PERMISSIONS) | frozenset(SETTING_PERMISSIONS)
ALL_PERMISSIONS: frozenset[str] = MODULE_PERMISSIONS | ATOMIC_PERMISSIONS


def serialize_action_meta() -> list[dict]:
    return list(ACTION_META.values())


# ── 权限层级解析 ──────────────────────────────────────────────

def _module_pages(module: str) -> Iterable[str]:
    from .permission_service import db_is_initialized, get_module_page_keys
    if db_is_initialized():
        return get_module_page_keys(module)
    return PAGE_REGISTRY.get(module, {}).get('pages', {}).keys()


def expand_permissions(perms: Iterable[str]) -> frozenset[str]:
    """将模块级权限展开为页面/配置原子权限，并补全 manage→view。"""
    expanded: set[str] = set()
    for perm in perms:
        expanded.add(perm)
        parts = perm.split('.')
        if len(parts) == 2:
            module, action = parts
            for page in _module_pages(module):
                expanded.add(page_permission(module, page, action))
            if module == 'system':
                expanded.add(f'system.settings.{action}')
                for cat in SETTING_CATEGORY_LABELS:
                    expanded.add(setting_category_permission(cat, action))
                for key in SETTING_KEY_META:
                    expanded.add(setting_key_permission(key, action))
        elif len(parts) == 3 and perm.endswith('.manage'):
            expanded.add(perm[:-7] + '.view')
        elif len(parts) == 4 and parts[1] == 'settings' and perm.endswith('.manage'):
            expanded.add(perm[:-7] + '.view')
        elif len(parts) == 4 and parts[1] == 'setting' and perm.endswith('.manage'):
            expanded.add(perm[:-7] + '.view')
    return frozenset(expanded)


def permission_satisfies(held: str, required: str) -> bool:
    """判断 held 权限是否满足 required 权限要求。"""
    if held == required:
        return True
    if required.endswith('.view') and held == f'{required[:-5]}.manage':
        return True

    h = held.split('.')
    r = required.split('.')

    # 模块级 → 页面/配置
    if len(h) == 2 and len(r) >= 3:
        if h[0] == r[0] and h[1] == r[-1]:
            return True

    # 页面级 → 模块级（反向，用于 API 仍校验 module.manage）
    if len(h) == 3 and len(r) == 2:
        return h[0] == r[0] and h[2] == r[1]

    # system.settings.manage → system.settings.general.manage / system.setting.key.manage
    if len(h) == 3 and h[0] == 'system' and h[1] == 'settings' and len(r) == 4:
        if r[1] == 'settings' and h[2] == r[3]:
            return True
        if r[1] == 'setting' and h[2] == r[3]:
            return True

    # system.settings.{cat}.manage → system.setting.{key}.manage
    if len(h) == 4 and h[1] == 'settings' and len(r) == 4 and r[1] == 'setting':
        key = r[2]
        from .permission_service import get_setting_key_category
        cat = get_setting_key_category(key)
        return bool(cat and cat == h[2] and h[3] == r[3])

    return False


def _filter_customer_safe_permissions(perms: Iterable[str]) -> frozenset[str]:
    """归属客户账号仅保留客户/账单/服务/资源域权限，防止误授后台管理权限。"""
    safe: set[str] = set()
    for code in perms:
        if any(code == p or code.startswith(p) for p in CUSTOMER_SAFE_PERMISSION_PREFIXES):
            safe.add(code)
    return frozenset(safe)


def resolve_customer_account_role(profile) -> str:
    """客户账号有效角色：非法或空角色回退为门户默认角色。"""
    role = getattr(profile, 'role', '') or ''
    if role in CUSTOMER_ACCOUNT_ALLOWED_ROLES:
        return role
    return CUSTOMER_ACCOUNT_DEFAULT_ROLE


def user_effective_permissions(profile) -> frozenset[str]:
    from .permission_service import db_is_initialized, get_all_active_permission_codes, get_role_permissions as db_get_role_permissions

    user = profile.user
    if user.is_superuser:
        if db_is_initialized():
            return get_all_active_permission_codes()
        return ALL_PERMISSIONS
    if profile.account_type == 'customer':
        role = resolve_customer_account_role(profile)
        raw = db_get_role_permissions(role) if db_is_initialized() else ROLE_PERMISSIONS.get(role, frozenset())
        if not raw:
            raw = ROLE_PERMISSIONS.get(role, CUSTOMER_ACCOUNT_PERMISSIONS)
        return expand_permissions(_filter_customer_safe_permissions(raw))
    perms = set(db_get_role_permissions(profile.role))
    if user.is_staff:
        perms |= {Perm.ACCOUNTS_VIEW, Perm.ACCOUNTS_MANAGE}
    return expand_permissions(perms)


# ── 权限组（供前端展示）────────────────────────────────────────

def _serialize_module_group(module_key: str, mod: dict) -> dict:
    pages = []
    for page_key, page in mod['pages'].items():
        pages.append({
            'key': page_key,
            'label': page['label'],
            'route': page.get('route', ''),
            'description': page.get('description', ''),
            'permissions': [
                {
                    'code': page_permission(module_key, page_key, 'view'),
                    'label': action_label('view', target=page['label']),
                    'action': 'view',
                    'action_label': ACTION_META['view']['label'],
                    'action_hint': ACTION_META['view']['description'],
                },
                {
                    'code': page_permission(module_key, page_key, 'manage'),
                    'label': f"{action_label('manage', target=page['label'])}（新增/修改/删除/执行）",
                    'action': 'manage',
                    'action_label': ACTION_META['manage']['label'],
                    'action_hint': ACTION_META['manage']['description'],
                },
            ],
        })
    group = {
        'key': module_key,
        'label': mod['label'],
        'description': mod['description'],
        'pages': pages,
        'permissions': [
            {
                'code': f'{module_key}.view',
                'label': f"查看整个「{mod['label']}」模块",
                'action': 'view',
                'scope': 'module',
                'action_label': ACTION_META['view']['label'],
                'action_hint': '勾选后覆盖本模块全部页面的查看权限',
            },
            {
                'code': f'{module_key}.manage',
                'label': f"管理整个「{mod['label']}」模块",
                'action': 'manage',
                'scope': 'module',
                'action_label': ACTION_META['manage']['label'],
                'action_hint': '勾选后覆盖本模块全部页面的管理权限',
            },
        ],
    }
    if module_key == 'system':
        settings_pages = [{
            'key': 'settings',
            'label': '参数配置',
            'route': '/system/settings',
            'description': '站点、账单、安全、通知等系统参数',
            'permissions': [
                {
                    'code': 'system.settings.view',
                    'label': '查看「参数配置」页',
                    'action': 'view',
                    'action_label': ACTION_META['view']['label'],
                    'action_hint': ACTION_META['view']['description'],
                },
                {
                    'code': 'system.settings.manage',
                    'label': '管理「参数配置」页（可改全部参数）',
                    'action': 'manage',
                    'action_label': ACTION_META['manage']['label'],
                    'action_hint': ACTION_META['manage']['description'],
                },
            ],
            'categories': [
                {
                    'key': cat,
                    'label': label,
                    'permissions': [
                        {
                            'code': setting_category_permission(cat, 'view'),
                            'label': f'查看「{label}」分类',
                            'action': 'view',
                            'action_label': ACTION_META['view']['label'],
                            'action_hint': ACTION_META['view']['description'],
                        },
                        {
                            'code': setting_category_permission(cat, 'manage'),
                            'label': f'修改「{label}」分类下参数',
                            'action': 'manage',
                            'action_label': ACTION_META['manage']['label'],
                            'action_hint': ACTION_META['manage']['description'],
                        },
                    ],
                    'settings': [
                        {
                            'key': key,
                            'label': meta['label'],
                            'description': meta.get('description', ''),
                            'permissions': [
                                {
                                    'code': setting_key_permission(key, 'view'),
                                    'label': f"查看参数「{meta['label']}」",
                                    'action': 'view',
                                    'action_label': ACTION_META['view']['label'],
                                    'action_hint': ACTION_META['view']['description'],
                                },
                                {
                                    'code': setting_key_permission(key, 'manage'),
                                    'label': f"修改参数「{meta['label']}」",
                                    'action': 'manage',
                                    'action_label': ACTION_META['manage']['label'],
                                    'action_hint': ACTION_META['manage']['description'],
                                },
                            ],
                        }
                        for key, meta in SETTING_KEY_META.items()
                        if meta['category'] == cat
                    ],
                }
                for cat, label in SETTING_CATEGORY_LABELS.items()
            ],
        }]
        # 保留其它 system 页面（如通知），并确保 settings 使用带分类结构的版本
        other_pages = [p for p in pages if p['key'] != 'settings']
        group['pages'] = settings_pages + other_pages
    return group


PERMISSION_GROUPS: dict[str, dict] = {
    key: _serialize_module_group(key, mod)
    for key, mod in PAGE_REGISTRY.items()
}


# ── 角色 → 权限组映射 ────────────────────────────────────────

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'superadmin': ALL_PERMISSIONS,
    'admin': ALL_PERMISSIONS,
    'customer_manager': frozenset({
        Perm.CUSTOMER_VIEW, Perm.CUSTOMER_MANAGE,
        Perm.BILLING_VIEW, Perm.BILLING_MANAGE,
        Perm.SERVICE_VIEW,
        page_permission('service', 'order', 'view'),
        page_permission('service', 'instance', 'view'),
        page_permission('service', 'selfservice', 'view'),
        page_permission('service', 'ticket', 'view'),
        page_permission('service', 'ticket', 'manage'),
        Perm.RESOURCE_VIEW,
        page_permission('resource', 'pop', 'view'),
        page_permission('resource', 'vps', 'view'),
    }),
    'operator': frozenset({
        Perm.RESOURCE_VIEW, Perm.RESOURCE_MANAGE,
        Perm.OPERATION_VIEW, Perm.OPERATION_MANAGE,
        Perm.SERVICE_VIEW, Perm.SERVICE_MANAGE,
        Perm.PRODUCT_VIEW, Perm.PRODUCT_MANAGE,
        Perm.SUPPLIER_VIEW, Perm.SUPPLIER_MANAGE,
    }),
    'sales': frozenset({
        Perm.CUSTOMER_VIEW, Perm.CUSTOMER_MANAGE,
        Perm.BILLING_VIEW,
        Perm.SERVICE_VIEW,
        page_permission('service', 'order', 'view'),
        page_permission('service', 'instance', 'view'),
        page_permission('service', 'selfservice', 'view'),
        page_permission('service', 'ticket', 'view'),
        Perm.PRODUCT_VIEW,
        Perm.SUPPLIER_VIEW,
    }),
    'supplier': frozenset({
        page_permission('product', 'product', 'view'),
        page_permission('product', 'product', 'manage'),
        page_permission('product', 'template', 'view'),
        page_permission('resource', 'pop', 'view'),
        page_permission('resource', 'pop', 'manage'),
        page_permission('resource', 'ip', 'view'),
        page_permission('resource', 'ip', 'manage'),
        page_permission('supplier', 'billing', 'view'),
    }),
    'channel': frozenset({
        page_permission('customer', 'list', 'view'),
        page_permission('customer', 'list', 'manage'),
        page_permission('customer', 'detail', 'view'),
        page_permission('customer', 'detail', 'manage'),
        page_permission('customer', 'channel', 'view'),
        page_permission('billing', 'list', 'view'),
        page_permission('service', 'order', 'view'),
        page_permission('service', 'order', 'manage'),
        page_permission('service', 'instance', 'view'),
    }),
    'readonly': VIEW_PERMISSIONS,
    # 门户默认角色：客户名下服务查看/管理 + 修改变更
    'customer': frozenset({
        Perm.CUSTOMER_VIEW,
        page_permission('customer', 'detail', 'view'),
        page_permission('customer', 'detail', 'manage'),
        Perm.BILLING_VIEW,
        page_permission('billing', 'list', 'view'),
        page_permission('billing', 'list', 'manage'),
        Perm.SERVICE_VIEW,
        Perm.SERVICE_MANAGE,
        page_permission('service', 'overview', 'view'),
        page_permission('service', 'order', 'view'),
        page_permission('service', 'order', 'manage'),
        page_permission('service', 'ticket', 'view'),
        page_permission('service', 'ticket', 'manage'),
        page_permission('service', 'instance', 'view'),
        page_permission('service', 'instance', 'manage'),
        page_permission('service', 'change', 'view'),
        page_permission('service', 'change', 'manage'),
        Perm.RESOURCE_VIEW,
        page_permission('resource', 'vps', 'view'),
        page_permission('resource', 'vps', 'manage'),
        page_permission('resource', 'edge', 'view'),
        page_permission('resource', 'edge', 'manage'),
    }),
}

# 客户账号安全白名单前缀（即使角色矩阵误配也不开放后台管理域）
CUSTOMER_SAFE_PERMISSION_PREFIXES: tuple[str, ...] = (
    'customer.',
    'billing.',
    'service.',
    'resource.',
)

# 兼容旧引用：客户账号默认能力（与角色 customer 默认授权对齐）
CUSTOMER_ACCOUNT_PERMISSIONS: frozenset[str] = ROLE_PERMISSIONS['customer']

# 归属客户账号允许绑定的角色；默认注册使用 customer
CUSTOMER_ACCOUNT_DEFAULT_ROLE: str = 'customer'
CUSTOMER_ACCOUNT_ALLOWED_ROLES: frozenset[str] = frozenset({'customer', 'readonly'})

ROLE_LABELS: dict[str, str] = {
    'superadmin': '超级管理员',
    'admin': '管理员',
    'customer_manager': '客户管理员',
    'operator': '运维人员',
    'sales': '销售人员',
    'customer': '客户用户',
    'supplier': '供应商',
    'channel': '渠道',
    'readonly': '只读用户',
}


@dataclass(frozen=True)
class RoleDefinition:
    value: str
    label: str
    description: str
    permission_groups: tuple[str, ...]
    permissions: frozenset[str]


ROLE_DEFINITIONS: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        'superadmin', '超级管理员',
        '拥有系统全部权限，可管理账号与所有业务模块',
        tuple(PAGE_REGISTRY.keys()),
        ROLE_PERMISSIONS['superadmin'],
    ),
    RoleDefinition(
        'admin', '管理员',
        '拥有全部业务权限，可管理系统用户',
        tuple(PAGE_REGISTRY.keys()),
        ROLE_PERMISSIONS['admin'],
    ),
    RoleDefinition(
        'customer_manager', '客户管理员',
        '负责客户档案、合同、账单及售前售后支撑',
        ('customer', 'billing', 'service', 'resource'),
        ROLE_PERMISSIONS['customer_manager'],
    ),
    RoleDefinition(
        'operator', '运维人员',
        '负责 POP/VPS/IP/带宽等资源、监控运维与服务履约',
        ('resource', 'operation', 'service', 'product', 'supplier'),
        ROLE_PERMISSIONS['operator'],
    ),
    RoleDefinition(
        'sales', '销售人员',
        '负责客户拓展与跟进，可查看账单、产品与服务概况（只读账单）',
        ('customer', 'billing', 'service', 'product'),
        ROLE_PERMISSIONS['sales'],
    ),
    RoleDefinition(
        'customer', '客户用户',
        '门户默认角色：管理名下服务实例、发起升降配变更、查看账单与资源',
        ('customer', 'billing', 'service', 'resource'),
        ROLE_PERMISSIONS['customer'],
    ),
    RoleDefinition(
        'supplier', '供应商',
        '仅维护本供应商产品、POP、IP/隧道，并查看对应账单与结算',
        ('product', 'resource', 'supplier'),
        ROLE_PERMISSIONS['supplier'],
    ),
    RoleDefinition(
        'channel', '渠道',
        '绑定渠道客户：可给本渠道及名下客户下单、新增客户与登录账号，仅查看名下消费、佣金与结算',
        ('customer', 'billing', 'service'),
        ROLE_PERMISSIONS['channel'],
    ),
    RoleDefinition(
        'readonly', '只读用户',
        '所有业务模块仅查看，不可变更任何数据',
        tuple(PAGE_REGISTRY.keys()),
        ROLE_PERMISSIONS['readonly'],
    ),
)


def get_role_permissions(role: str) -> frozenset[str]:
    from .permission_service import db_is_initialized, get_role_permissions as db_get_role_permissions
    if db_is_initialized():
        return db_get_role_permissions(role)
    return ROLE_PERMISSIONS.get(role, frozenset())


def get_permissions_for_user(profile) -> frozenset[str]:
    return user_effective_permissions(profile)


def user_has_permission(profile, permission: str) -> bool:
    if profile.user.is_superuser:
        return True
    effective = user_effective_permissions(profile)
    return any(permission_satisfies(held, permission) for held in effective)


def user_has_any_permission(profile, permissions: Iterable[str]) -> bool:
    return any(user_has_permission(profile, p) for p in permissions)


def check_setting_manage_permission(profile, setting_key: str) -> bool:
    return user_has_permission(profile, setting_key_permission(setting_key, 'manage'))


def _role_has_permission(role_perms: frozenset[str], code: str) -> bool:
    expanded = expand_permissions(role_perms)
    return any(permission_satisfies(held, code) for held in expanded)


def _collect_group_permissions_for_role(role_perms: frozenset[str], group_key: str) -> list[dict]:
    group = PERMISSION_GROUPS[group_key]
    collected: list[dict] = []
    for perm in group['permissions']:
        if _role_has_permission(role_perms, perm['code']):
            collected.append(perm)
    for page in group.get('pages', []):
        for perm in page.get('permissions', []):
            if _role_has_permission(role_perms, perm['code']):
                collected.append({**perm, 'page': page['label']})
        for cat in page.get('categories', []):
            for perm in cat.get('permissions', []):
                if _role_has_permission(role_perms, perm['code']):
                    collected.append({**perm, 'page': page['label'], 'category': cat['label']})
            for setting in cat.get('settings', []):
                for perm in setting.get('permissions', []):
                    if _role_has_permission(role_perms, perm['code']):
                        collected.append({
                            **perm,
                            'page': page['label'],
                            'category': cat['label'],
                            'setting': setting['label'],
                        })
    return collected


def serialize_role_catalog() -> list[dict]:
    from .permission_service import db_is_initialized, ensure_permission_defaults, serialize_role_catalog_from_db
    if db_is_initialized():
        return serialize_role_catalog_from_db()
    ensure_permission_defaults()
    if db_is_initialized():
        return serialize_role_catalog_from_db()
    catalog = []
    for role in ROLE_DEFINITIONS:
        groups = []
        for group_key in role.permission_groups:
            group = PERMISSION_GROUPS[group_key]
            perms = _collect_group_permissions_for_role(role.permissions, group_key)
            if not perms:
                continue
            pages = []
            for page in group.get('pages', []):
                page_perms = [
                    p for p in perms
                    if p.get('page') == page['label'] or (
                        not p.get('page') and p['code'].startswith(f'{group_key}.')
                    )
                ]
                page_entry = {
                    'key': page['key'],
                    'label': page['label'],
                    'route': page.get('route', ''),
                    'permissions': [
                        {'code': p['code'], 'label': p['label'], 'action': p['action']}
                        for p in page_perms if p['code'].count('.') >= 2
                    ],
                }
                if page.get('categories'):
                    page_entry['categories'] = page['categories']
                if page_entry['permissions'] or page.get('categories'):
                    pages.append(page_entry)
            groups.append({
                'key': group_key,
                'label': group['label'],
                'description': group['description'],
                'permissions': [
                    {'code': p['code'], 'label': p['label'], 'action': p['action']}
                    for p in perms if p.get('scope') == 'module'
                ],
                'pages': pages,
            })
        catalog.append({
            'value': role.value,
            'label': role.label,
            'description': role.description,
            'permissions': sorted(expand_permissions(role.permissions)),
            'permission_groups': groups,
        })
    return catalog


def serialize_permission_groups() -> list[dict]:
    from .permission_service import db_is_initialized, ensure_permission_defaults, serialize_permission_groups_from_db
    if db_is_initialized():
        return serialize_permission_groups_from_db()
    ensure_permission_defaults()
    if db_is_initialized():
        return serialize_permission_groups_from_db()
    return list(PERMISSION_GROUPS.values())
