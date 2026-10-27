"""
accounts/permission_service.py
权限目录与角色权限的数据库读写、初始化与序列化
"""
from __future__ import annotations

from django.db import transaction

from .models import PermissionDefinition, PermissionGroup, PermissionPage, RolePermissionGrant, UserProfile
from . import role_permissions as static


def db_is_initialized() -> bool:
    return PermissionDefinition.objects.exists()


def align_channel_role_grants() -> int:
    """渠道角色授权与静态矩阵对齐，收回产品/资源等不应开放的权限。"""
    desired = static.expand_permissions(static.ROLE_PERMISSIONS.get('channel', frozenset()))
    qs = RolePermissionGrant.objects.filter(role='channel')
    removed, _ = qs.exclude(permission_id__in=desired).delete()
    existing = set(RolePermissionGrant.objects.filter(role='channel').values_list('permission_id', flat=True))
    missing = desired - existing
    added = 0
    if missing:
        active_codes = PermissionDefinition.objects.filter(
            code__in=missing, is_active=True,
        ).values_list('code', flat=True)
        objs = [
            RolePermissionGrant(role='channel', permission_id=code)
            for code in active_codes
        ]
        RolePermissionGrant.objects.bulk_create(objs, ignore_conflicts=True)
        added = len(objs)
    return removed + added


def ensure_permission_defaults(force: bool = False) -> dict:
    """从内置定义初始化/重置权限目录与角色授权。"""
    if db_is_initialized() and not force:
        return {'initialized': True, 'created': 0, 'skipped': True}

    with transaction.atomic():
        if force:
            RolePermissionGrant.objects.all().delete()
            PermissionDefinition.objects.all().delete()
            PermissionPage.objects.all().delete()
            PermissionGroup.objects.all().delete()

        groups_created = 0
        pages_created = 0
        perms_created = 0
        grants_created = 0

        sort_group = 0
        for module_key, mod in static.PAGE_REGISTRY.items():
            sort_group += 10
            group, g_created = PermissionGroup.objects.update_or_create(
                key=module_key,
                defaults={
                    'label': mod['label'],
                    'description': mod.get('description', ''),
                    'sort_order': sort_group,
                    'is_builtin': True,
                },
            )
            if g_created:
                groups_created += 1

            sort_page = 0
            for page_key, page in mod['pages'].items():
                sort_page += 1
                page_obj, p_created = PermissionPage.objects.update_or_create(
                    group=group,
                    key=page_key,
                    defaults={
                        'label': page['label'],
                        'route': page.get('route', ''),
                        'sort_order': sort_page,
                        'is_builtin': True,
                    },
                )
                if p_created:
                    pages_created += 1

                for action in ('view', 'manage'):
                    code = static.page_permission(module_key, page_key, action)
                    label = static.PAGE_PERMISSIONS.get(code) or static.action_label(action, target=page['label'])
                    _, c = PermissionDefinition.objects.update_or_create(
                        code=code,
                        defaults={
                            'label': label,
                            'action': action,
                            'scope': PermissionDefinition.Scope.PAGE,
                            'group_key': module_key,
                            'page_key': page_key,
                            'is_builtin': True,
                            'is_active': True,
                            'sort_order': sort_page * 10 + (0 if action == 'view' else 1),
                        },
                    )
                    if c:
                        perms_created += 1

            for action, label_prefix in (('view', '查看'), ('manage', '管理')):
                code = f'{module_key}.{action}'
                label = f'{label_prefix}{mod["label"]}'
                _, c = PermissionDefinition.objects.update_or_create(
                    code=code,
                    defaults={
                        'label': label,
                        'action': action,
                        'scope': PermissionDefinition.Scope.MODULE,
                        'group_key': module_key,
                        'is_builtin': True,
                        'is_active': True,
                        'sort_order': 0 if action == 'view' else 1,
                    },
                )
                if c:
                    perms_created += 1

        # 系统配置相关权限
        setting_defs = [
            ('system.settings.view', '查看参数配置页', 'view', PermissionDefinition.Scope.SETTING_PAGE, '', '', ''),
            ('system.settings.manage', '管理参数配置页', 'manage', PermissionDefinition.Scope.SETTING_PAGE, '', '', ''),
        ]
        sort_cat = 0
        for cat, cat_label in static.SETTING_CATEGORY_LABELS.items():
            sort_cat += 1
            for action, prefix in (('view', '查看'), ('manage', '修改')):
                code = static.setting_category_permission(cat, action)
                setting_defs.append((
                    code, f'{prefix}{cat_label}', action,
                    PermissionDefinition.Scope.SETTING_CATEGORY, '', cat, '',
                ))
            for key, meta in static.SETTING_KEY_META.items():
                if meta['category'] != cat:
                    continue
                for action, prefix in (('view', '查看'), ('manage', '修改')):
                    code = static.setting_key_permission(key, action)
                    setting_defs.append((
                        code, f'{prefix}「{meta["label"]}」', action,
                        PermissionDefinition.Scope.SETTING_KEY, '', cat, key,
                    ))

        sort_setting = 0
        for code, label, action, scope, page_key, cat, skey in setting_defs:
            sort_setting += 1
            _, c = PermissionDefinition.objects.update_or_create(
                code=code,
                defaults={
                    'label': label,
                    'action': action,
                    'scope': scope,
                    'group_key': 'system',
                    'page_key': page_key or 'settings',
                    'setting_category': cat,
                    'setting_key': skey,
                    'is_builtin': True,
                    'is_active': True,
                    'sort_order': sort_setting,
                },
            )
            if c:
                perms_created += 1

        # 角色授权
        for role_def in static.ROLE_DEFINITIONS:
            role = role_def.value
            if RolePermissionGrant.objects.filter(role=role).exists() and not force:
                continue
            if force:
                RolePermissionGrant.objects.filter(role=role).delete()
            base_codes = static.ROLE_PERMISSIONS.get(role, frozenset())
            expanded = static.expand_permissions(base_codes)
            active_codes = PermissionDefinition.objects.filter(
                code__in=expanded, is_active=True,
            ).values_list('code', flat=True)
            grant_objs = [
                RolePermissionGrant(role=role, permission_id=code)
                for code in active_codes
            ]
            RolePermissionGrant.objects.bulk_create(grant_objs, ignore_conflicts=True)
            grants_created += len(grant_objs)

    return {
        'initialized': True,
        'skipped': False,
        'groups_created': groups_created,
        'pages_created': pages_created,
        'permissions_created': perms_created,
        'grants_created': grants_created,
    }


def sync_permission_catalog() -> dict:
    """增量同步页面/权限目录，并为静态角色矩阵中的新权限补齐授权（只增不删）。"""
    if not db_is_initialized():
        return ensure_permission_defaults()

    created_codes: list[str] = []
    pages_created = 0
    perms_created = 0
    grants_created = 0

    with transaction.atomic():
        sort_group = 0
        for module_key, mod in static.PAGE_REGISTRY.items():
            sort_group += 10
            group, _ = PermissionGroup.objects.update_or_create(
                key=module_key,
                defaults={
                    'label': mod['label'],
                    'description': mod.get('description', ''),
                    'sort_order': sort_group,
                    'is_builtin': True,
                },
            )
            sort_page = 0
            for page_key, page in mod['pages'].items():
                sort_page += 1
                _, p_created = PermissionPage.objects.update_or_create(
                    group=group,
                    key=page_key,
                    defaults={
                        'label': page['label'],
                        'route': page.get('route', ''),
                        'sort_order': sort_page,
                        'is_builtin': True,
                    },
                )
                if p_created:
                    pages_created += 1
                for action in ('view', 'manage'):
                    code = static.page_permission(module_key, page_key, action)
                    label = static.PAGE_PERMISSIONS.get(code) or static.action_label(action, target=page['label'])
                    _, c = PermissionDefinition.objects.update_or_create(
                        code=code,
                        defaults={
                            'label': label,
                            'action': action,
                            'scope': PermissionDefinition.Scope.PAGE,
                            'group_key': module_key,
                            'page_key': page_key,
                            'is_builtin': True,
                            'is_active': True,
                            'sort_order': sort_page * 10 + (0 if action == 'view' else 1),
                        },
                    )
                    if c:
                        perms_created += 1
                        created_codes.append(code)
            for action, label_prefix in (('view', '查看'), ('manage', '管理')):
                code = f'{module_key}.{action}'
                _, c = PermissionDefinition.objects.update_or_create(
                    code=code,
                    defaults={
                        'label': f'{label_prefix}{mod["label"]}',
                        'action': action,
                        'scope': PermissionDefinition.Scope.MODULE,
                        'group_key': module_key,
                        'is_builtin': True,
                        'is_active': True,
                        'sort_order': 0 if action == 'view' else 1,
                    },
                )
                if c:
                    perms_created += 1
                    created_codes.append(code)

        created_set = set(created_codes)
        for role, base_codes in static.ROLE_PERMISSIONS.items():
            desired = static.expand_permissions(base_codes)
            if role in ('supplier', 'channel') and not RolePermissionGrant.objects.filter(role=role).exists():
                active_codes = PermissionDefinition.objects.filter(
                    code__in=desired, is_active=True,
                ).values_list('code', flat=True)
                objs = [
                    RolePermissionGrant(role=role, permission_id=code)
                    for code in active_codes
                ]
                RolePermissionGrant.objects.bulk_create(objs, ignore_conflicts=True)
                grants_created += len(objs)
                continue
            if not created_set:
                continue
            to_add = desired & created_set
            if not to_add:
                continue
            active_codes = PermissionDefinition.objects.filter(
                code__in=to_add, is_active=True,
            ).values_list('code', flat=True)
            objs = [
                RolePermissionGrant(role=role, permission_id=code)
                for code in active_codes
            ]
            RolePermissionGrant.objects.bulk_create(objs, ignore_conflicts=True)
            grants_created += len(objs)

        grants_created += align_channel_role_grants()

    return {
        'initialized': True,
        'pages_created': pages_created,
        'permissions_created': perms_created,
        'grants_created': grants_created,
        'created_codes': created_codes,
    }


def get_module_page_keys(module: str) -> list[str]:
    if db_is_initialized():
        return list(
            PermissionPage.objects.filter(group__key=module)
            .order_by('sort_order', 'key')
            .values_list('key', flat=True)
        )
    return list(static.PAGE_REGISTRY.get(module, {}).get('pages', {}).keys())


def get_setting_key_category(key: str) -> str | None:
    if db_is_initialized():
        defn = PermissionDefinition.objects.filter(
            scope=PermissionDefinition.Scope.SETTING_KEY,
            setting_key=key,
            is_active=True,
        ).first()
        return defn.setting_category if defn else None
    meta = static.SETTING_KEY_META.get(key)
    return meta['category'] if meta else None


def get_all_active_permission_codes() -> frozenset[str]:
    if db_is_initialized():
        return frozenset(
            PermissionDefinition.objects.filter(is_active=True).values_list('code', flat=True)
        )
    return static.ALL_PERMISSIONS


def get_role_permissions(role: str) -> frozenset[str]:
    if not db_is_initialized():
        return static.ROLE_PERMISSIONS.get(role, frozenset())

    codes = list(
        RolePermissionGrant.objects.filter(
            role=role,
            permission__is_active=True,
        ).values_list('permission_id', flat=True)
    )
    if codes:
        return frozenset(codes)
    return static.ROLE_PERMISSIONS.get(role, frozenset())


def set_role_permissions(role: str, permission_codes: list[str]) -> frozenset[str]:
    if role not in dict(UserProfile.Role.choices):
        raise ValueError(f'无效角色: {role}')

    valid_codes = set(
        PermissionDefinition.objects.filter(
            code__in=permission_codes, is_active=True,
        ).values_list('code', flat=True)
    )
    with transaction.atomic():
        RolePermissionGrant.objects.filter(role=role).delete()
        RolePermissionGrant.objects.bulk_create([
            RolePermissionGrant(role=role, permission_id=code)
            for code in valid_codes
        ])
    return frozenset(valid_codes)


def _serialize_setting_categories(group_key: str) -> list[dict]:
    if group_key != 'system':
        return []
    categories = []
    for cat, label in static.SETTING_CATEGORY_LABELS.items():
        cat_perms = PermissionDefinition.objects.filter(
            scope=PermissionDefinition.Scope.SETTING_CATEGORY,
            setting_category=cat,
            is_active=True,
        ).order_by('sort_order')
        settings = []
        for key, meta in static.SETTING_KEY_META.items():
            if meta['category'] != cat:
                continue
            key_perms = PermissionDefinition.objects.filter(
                scope=PermissionDefinition.Scope.SETTING_KEY,
                setting_key=key,
                is_active=True,
            ).order_by('sort_order')
            settings.append({
                'key': key,
                'label': meta['label'],
                'permissions': [
                    {'code': p.code, 'label': p.label, 'action': p.action, 'is_builtin': p.is_builtin}
                    for p in key_perms
                ],
            })
        categories.append({
            'key': cat,
            'label': label,
            'permissions': [
                {'code': p.code, 'label': p.label, 'action': p.action, 'is_builtin': p.is_builtin}
                for p in cat_perms
            ],
            'settings': settings,
        })
    return categories


def serialize_permission_groups_from_db() -> list[dict]:
    groups = []
    for group in PermissionGroup.objects.prefetch_related('pages').order_by('sort_order', 'key'):
        module_perms = PermissionDefinition.objects.filter(
            group_key=group.key,
            scope=PermissionDefinition.Scope.MODULE,
            is_active=True,
        ).order_by('sort_order')
        pages = []
        for page in group.pages.order_by('sort_order', 'key'):
            page_perms = PermissionDefinition.objects.filter(
                group_key=group.key,
                page_key=page.key,
                scope=PermissionDefinition.Scope.PAGE,
                is_active=True,
            ).order_by('sort_order')
            page_entry = {
                'key': page.key,
                'label': page.label,
                'route': page.route,
                'is_builtin': page.is_builtin,
                'permissions': [
                    {'code': p.code, 'label': p.label, 'action': p.action, 'is_builtin': p.is_builtin}
                    for p in page_perms
                ],
            }
            if group.key == 'system' and page.key == 'settings':
                setting_page_perms = PermissionDefinition.objects.filter(
                    scope=PermissionDefinition.Scope.SETTING_PAGE,
                    is_active=True,
                ).order_by('sort_order')
                page_entry['permissions'] = [
                    {'code': p.code, 'label': p.label, 'action': p.action, 'is_builtin': p.is_builtin}
                    for p in setting_page_perms
                ]
                page_entry['categories'] = _serialize_setting_categories(group.key)
            pages.append(page_entry)

        groups.append({
            'key': group.key,
            'label': group.label,
            'description': group.description,
            'is_builtin': group.is_builtin,
            'pages': pages,
            'permissions': [
                {
                    'code': p.code,
                    'label': p.label,
                    'action': p.action,
                    'scope': 'module',
                    'is_builtin': p.is_builtin,
                }
                for p in module_perms
            ],
        })
    return groups


def _role_has_permission(role_codes: frozenset[str], code: str) -> bool:
    expanded = static.expand_permissions(role_codes)
    return any(static.permission_satisfies(held, code) for held in expanded)


def serialize_role_catalog_from_db() -> list[dict]:
    groups_data = serialize_permission_groups_from_db()
    catalog = []
    for role_def in static.ROLE_DEFINITIONS:
        role_codes = get_role_permissions(role_def.value)
        expanded = static.expand_permissions(role_codes)
        groups = []
        for group in groups_data:
            if group['key'] not in role_def.permission_groups and role_def.value not in ('superadmin', 'admin', 'readonly'):
                # readonly/superadmin/admin span all groups; others use role_def.permission_groups
                pass
            if group['key'] not in role_def.permission_groups and role_def.value not in ('superadmin', 'admin', 'readonly'):
                continue

            module_perms = [
                p for p in group['permissions']
                if _role_has_permission(role_codes, p['code'])
            ]
            pages = []
            for page in group.get('pages', []):
                page_perms = [
                    p for p in page.get('permissions', [])
                    if _role_has_permission(role_codes, p['code'])
                ]
                page_entry = {
                    'key': page['key'],
                    'label': page['label'],
                    'route': page.get('route', ''),
                    'permissions': page_perms,
                }
                if page.get('categories'):
                    page_entry['categories'] = page['categories']
                if page_perms or page.get('categories'):
                    pages.append(page_entry)
            if module_perms or pages:
                groups.append({
                    'key': group['key'],
                    'label': group['label'],
                    'description': group['description'],
                    'permissions': module_perms,
                    'pages': pages,
                })
        catalog.append({
            'value': role_def.value,
            'label': role_def.label,
            'description': role_def.description,
            'permissions': sorted(expanded),
            'permission_groups': groups,
        })
    return catalog


def get_role_permission_codes(role: str) -> list[str]:
    """返回角色直接授予的权限码（未展开）。"""
    return sorted(get_role_permissions(role))


def update_permission_group(key: str, data: dict) -> PermissionGroup:
    group = PermissionGroup.objects.get(key=key)
    for field in ('label', 'description', 'sort_order'):
        if field in data:
            setattr(group, field, data[field])
    group.save()
    return group


def update_permission_page(group_key: str, page_key: str, data: dict) -> PermissionPage:
    page = PermissionPage.objects.get(group__key=group_key, key=page_key)
    for field in ('label', 'route', 'sort_order'):
        if field in data:
            setattr(page, field, data[field])
    page.save()
    if 'label' in data:
        _sync_page_permission_labels(group_key, page_key, page.label)
    return page


def _sync_page_permission_labels(group_key: str, page_key: str, page_label: str) -> None:
    for action, prefix in (('view', '查看'), ('manage', '管理')):
        code = static.page_permission(group_key, page_key, action)
        PermissionDefinition.objects.filter(code=code).update(label=f'{prefix}{page_label}')


def create_permission_page(group_key: str, page_key: str, label: str, route: str = '') -> PermissionPage:
    group = PermissionGroup.objects.get(key=group_key)
    if PermissionPage.objects.filter(group=group, key=page_key).exists():
        raise ValueError(f'页面 {group_key}.{page_key} 已存在')
    sort_order = group.pages.count() + 1
    page = PermissionPage.objects.create(
        group=group,
        key=page_key,
        label=label,
        route=route,
        sort_order=sort_order,
        is_builtin=False,
    )
    for action, prefix in (('view', '查看'), ('manage', '管理')):
        code = static.page_permission(group_key, page_key, action)
        PermissionDefinition.objects.create(
            code=code,
            label=f'{prefix}{label}',
            action=action,
            scope=PermissionDefinition.Scope.PAGE,
            group_key=group_key,
            page_key=page_key,
            is_builtin=False,
            is_active=True,
            sort_order=sort_order * 10 + (0 if action == 'view' else 1),
        )
    return page


def update_permission_definition(code: str, data: dict) -> PermissionDefinition:
    perm = PermissionDefinition.objects.get(code=code)
    for field in ('label', 'is_active', 'sort_order'):
        if field in data:
            setattr(perm, field, data[field])
    perm.save()
    return perm


def create_custom_permission(code: str, label: str, action: str, group_key: str, **extra) -> PermissionDefinition:
    if PermissionDefinition.objects.filter(code=code).exists():
        raise ValueError(f'权限码 {code} 已存在')
    return PermissionDefinition.objects.create(
        code=code,
        label=label,
        action=action,
        scope=PermissionDefinition.Scope.CUSTOM,
        group_key=group_key,
        page_key=extra.get('page_key', ''),
        is_builtin=False,
        is_active=True,
        sort_order=extra.get('sort_order', 999),
    )


def delete_custom_permission(code: str) -> None:
    perm = PermissionDefinition.objects.get(code=code)
    if perm.is_builtin:
        raise ValueError('内置权限不可删除')
    perm.delete()


def sync_builtin_modules(modules: list[str] | None = None) -> dict:
    """增量同步内置模块的权限目录（仅新增/补全，不删除任何已有数据）。

    用于新增业务模块（如产品管理、供应商管理）后，将其权限组/页面/权限定义
    写入数据库，并按内置角色映射补充授权（add-only，保留管理员自定义授权）。
    """
    if not db_is_initialized():
        # 数据库尚未初始化：执行完整初始化即可覆盖新模块
        return ensure_permission_defaults()

    target = set(modules) if modules else set(static.PAGE_REGISTRY.keys())
    groups_created = pages_created = perms_created = grants_created = 0

    with transaction.atomic():
        max_sort = (
            PermissionGroup.objects.order_by('-sort_order')
            .values_list('sort_order', flat=True).first() or 0
        )
        for module_key, mod in static.PAGE_REGISTRY.items():
            if module_key not in target:
                continue
            group, g_created = PermissionGroup.objects.get_or_create(
                key=module_key,
                defaults={
                    'label': mod['label'],
                    'description': mod.get('description', ''),
                    'sort_order': max_sort + 10,
                    'is_builtin': True,
                },
            )
            if g_created:
                groups_created += 1
                max_sort += 10

            sort_page = group.pages.count()
            for page_key, page in mod['pages'].items():
                page_obj, p_created = PermissionPage.objects.get_or_create(
                    group=group, key=page_key,
                    defaults={
                        'label': page['label'],
                        'route': page.get('route', ''),
                        'sort_order': sort_page + 1,
                        'is_builtin': True,
                    },
                )
                if p_created:
                    pages_created += 1
                    sort_page += 1
                for action, prefix in (('view', '查看'), ('manage', '管理')):
                    code = static.page_permission(module_key, page_key, action)
                    _, c = PermissionDefinition.objects.get_or_create(
                        code=code,
                        defaults={
                            'label': f'{prefix}{page["label"]}',
                            'action': action,
                            'scope': PermissionDefinition.Scope.PAGE,
                            'group_key': module_key,
                            'page_key': page_key,
                            'is_builtin': True,
                            'is_active': True,
                            'sort_order': (sort_page + 1) * 10 + (0 if action == 'view' else 1),
                        },
                    )
                    if c:
                        perms_created += 1

            for action, prefix in (('view', '查看'), ('manage', '管理')):
                code = f'{module_key}.{action}'
                _, c = PermissionDefinition.objects.get_or_create(
                    code=code,
                    defaults={
                        'label': f'{prefix}{mod["label"]}',
                        'action': action,
                        'scope': PermissionDefinition.Scope.MODULE,
                        'group_key': module_key,
                        'is_builtin': True,
                        'is_active': True,
                        'sort_order': 0 if action == 'view' else 1,
                    },
                )
                if c:
                    perms_created += 1

        # 按内置角色映射补充授权（add-only）
        module_codes = set()
        for module_key in target:
            module_codes.add(f'{module_key}.view')
            module_codes.add(f'{module_key}.manage')

        for role_def in static.ROLE_DEFINITIONS:
            role = role_def.value
            base_codes = static.ROLE_PERMISSIONS.get(role, frozenset())
            # 仅补充与本次目标模块相关的授权
            relevant = {c for c in static.expand_permissions(base_codes)
                        if c.split('.')[0] in target}
            if not relevant:
                continue
            active_codes = PermissionDefinition.objects.filter(
                code__in=relevant, is_active=True,
            ).values_list('code', flat=True)
            grant_objs = [RolePermissionGrant(role=role, permission_id=c) for c in active_codes]
            created = RolePermissionGrant.objects.bulk_create(grant_objs, ignore_conflicts=True)
            grants_created += len(created)

    return {
        'groups_created': groups_created,
        'pages_created': pages_created,
        'permissions_created': perms_created,
        'grants_created': grants_created,
    }
