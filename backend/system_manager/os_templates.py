"""系统模版目录：产品、门户与开通共用同一数据源。"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from django.utils.text import slugify


_CLOUD_IMAGE_EXTS = ('.qcow2', '.img', '.raw', '.qcow2.xz', '.img.xz')
_CLOUD_IMAGE_TOKENS = (
    'cloudimg', 'genericcloud', 'generic-cloud', 'cloud-image',
    'nocloud', 'openstack',
)
_VZDUMP_EXTS = ('.vma', '.vma.zst', '.vma.gz', '.vma.lzo')

# 与 vpopmanager.pvemanager.cloud_catalog.DEFAULT_CLOUD_IMAGES 对齐，供中心预置 / 升级旧 vma。
DEFAULT_CLOUD_CATALOG: list[dict] = [
    {
        'code': 'ubuntu-24.04',
        'name': 'Ubuntu 24.04 (Noble)',
        'url': 'https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img',
        'filename': 'noble-server-cloudimg-amd64.img',
        'default_user': 'ubuntu',
    },
    {
        'code': 'ubuntu-22.04',
        'name': 'Ubuntu 22.04 (Jammy)',
        'url': 'https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img',
        'filename': 'jammy-server-cloudimg-amd64.img',
        'default_user': 'ubuntu',
    },
    {
        'code': 'debian-12',
        'name': 'Debian 12 (Bookworm)',
        'url': 'https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2',
        'filename': 'debian-12-genericcloud-amd64.qcow2',
        'default_user': 'debian',
    },
    {
        'code': 'debian-13',
        'name': 'Debian 13 (Trixie)',
        'url': 'https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2',
        'filename': 'debian-13-genericcloud-amd64.qcow2',
        'default_user': 'debian',
    },
    {
        'code': 'almalinux-9',
        'name': 'AlmaLinux 9',
        'url': 'https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2',
        'filename': 'AlmaLinux-9-GenericCloud-latest.x86_64.qcow2',
        'default_user': 'almalinux',
    },
    {
        'code': 'rockylinux-9',
        'name': 'Rocky Linux 9',
        'url': 'https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2',
        'filename': 'Rocky-9-GenericCloud-Base.latest.x86_64.qcow2',
        'default_user': 'rocky',
    },
    {
        'code': 'centos-7',
        'name': 'CentOS 7',
        'url': 'https://cloud.centos.org/centos/7/images/CentOS-7-x86_64-GenericCloud-2009.qcow2',
        'filename': 'CentOS-7-x86_64-GenericCloud-2009.qcow2',
        'default_user': 'cloud-user',
    },
    {
        'code': 'centos-8',
        'name': 'CentOS 8',
        'url': 'https://cloud.centos.org/centos/8/x86_64/images/CentOS-8-GenericCloud-8.4.2105-20210603.0.x86_64.qcow2',
        'filename': 'CentOS-8-GenericCloud-8.4.2105-20210603.0.x86_64.qcow2',
        'default_user': 'cloud-user',
    },
]


def is_vzdump_filename(name: str) -> bool:
    raw = (name or '').lower().split('?', 1)[0]
    fn = raw.rsplit('/', 1)[-1]
    return 'vzdump' in raw or fn.endswith(_VZDUMP_EXTS)


def is_cloud_image_filename(name: str) -> bool:
    raw = (name or '').lower().split('?', 1)[0]
    fn = raw.rsplit('/', 1)[-1]
    if not fn or fn.endswith('.iso') or is_vzdump_filename(fn):
        return False
    if fn.endswith(_CLOUD_IMAGE_EXTS):
        return True
    return any(token in fn for token in _CLOUD_IMAGE_TOKENS)


def normalize_os_code(code: str) -> str:
    """ubuntu-22.04 / ubuntu-2204 / Ubuntu 22.04 → ubuntu2204，便于中心与 POP 对齐。"""
    return re.sub(r'[^a-z0-9]+', '', (code or '').lower())


def is_stale_os_template(template) -> bool:
    """仍按 vzdump / PVE 备份登记、不能直接 Cloud 开通的目录项。"""
    fn = getattr(template, 'backup_filename', '') or ''
    source = getattr(template, 'source_type', '') or ''
    if is_vzdump_filename(fn):
        return True
    return source == 'backup'


def is_deployable_os_template(template) -> bool:
    if template is None or not getattr(template, 'enabled', True):
        return False
    if is_stale_os_template(template):
        return False
    source = getattr(template, 'source_type', '') or ''
    fn = getattr(template, 'backup_filename', '') or ''
    if source == 'iso' and not is_cloud_image_filename(fn):
        return False
    return bool((getattr(template, 'download_url', '') or '').strip())


def infer_cloud_ciuser(name: str) -> str:
    text = (name or '').lower()
    if 'ubuntu' in text:
        return 'ubuntu'
    if 'debian' in text:
        return 'debian'
    if 'alma' in text:
        return 'almalinux'
    if 'rocky' in text:
        return 'rocky'
    if any(token in text for token in ('centos', 'rhel', 'fedora')):
        return 'cloud-user'
    return 'ubuntu'


def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url or '').path or '')
    name = path.rstrip('/').rsplit('/', 1)[-1] if path else ''
    return name.strip()


def infer_os_type(name: str) -> str:
    text = (name or '').lower()
    if 'ubuntu' in text:
        return 'ubuntu'
    if 'debian' in text:
        return 'debian'
    if 'centos' in text:
        return 'centos'
    if 'windows' in text:
        return 'windows'
    if any(token in text for token in ('rhel', 'redhat', 'rocky', 'alma')):
        return 'rhel'
    return 'other'


def make_unique_code(name: str, version: str, *, exclude_pk=None) -> str:
    from .models import SystemOsTemplate

    raw = slugify(f'{name}-{version}') or slugify(name) or 'os'
    raw = re.sub(r'[^a-z0-9-]+', '', raw).strip('-')[:28] or 'os'
    code = raw
    seq = 2
    while True:
        qs = SystemOsTemplate.objects.filter(code=code)
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        if not qs.exists():
            return code
        suffix = f'-{seq}'
        code = f'{raw[: 32 - len(suffix)]}{suffix}'
        seq += 1


def match_os_template(value, *, queryset=None):
    """按 id / code / 显示名 / 文件名解析系统模版。"""
    from .models import SystemOsTemplate

    qs = queryset if queryset is not None else SystemOsTemplate.objects.all()
    if value in (None, ''):
        return None
    if isinstance(value, SystemOsTemplate):
        return value
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        found = qs.filter(pk=int(value)).first()
        if found:
            return found
    text = str(value).strip()
    if not text:
        return None
    found = qs.filter(code__iexact=text).first()
    if found:
        return found
    compact = re.sub(r'\s+', ' ', text)
    for item in qs.order_by('sort_order', 'id'):
        if item.display_label.lower() == compact.lower():
            return item
        if item.backup_filename and item.backup_filename.lower() == compact.lower():
            return item
    lowered = compact.lower()
    for item in qs.order_by('sort_order', 'id'):
        if item.name.lower() == lowered or f'{item.name} {item.version}'.lower() == lowered:
            return item
        if item.version and item.version.lower() in lowered and item.name.lower() in lowered:
            return item
    return None


def assigned_os_templates(product):
    if not getattr(product, 'pk', None):
        return []
    manager = getattr(product, 'os_templates', None)
    if manager is None:
        return []
    return list(manager.all())


def product_os_templates(product, *, enabled_only=True, deployable_only=False):
    """产品可选系统版本：已绑定则用绑定集，否则返回全部启用模版。"""
    from .models import SystemOsTemplate

    assigned = assigned_os_templates(product)
    if assigned:
        items = [item for item in assigned if (not enabled_only or item.enabled)]
        if deployable_only:
            items = [item for item in items if is_deployable_os_template(item)]
        items.sort(key=lambda item: (item.sort_order, item.id))
        return items
    qs = SystemOsTemplate.objects.all()
    if enabled_only:
        qs = qs.filter(enabled=True)
    items = list(qs.order_by('sort_order', 'id'))
    if deployable_only:
        items = [item for item in items if is_deployable_os_template(item)]
    return items


def product_allows_os_template(product, template) -> bool:
    if template is None:
        return True
    if not is_deployable_os_template(template):
        return False
    assigned = [item for item in assigned_os_templates(product) if item.enabled]
    if not assigned:
        return True
    return any(item.pk == template.pk for item in assigned)


def sync_os_image(template):
    """把中心模版同步到门户重装用的 OsImage，保持同一套系统版本。"""
    from service_manager.models import OsImage

    fields = {
        'code': template.code,
        'name': template.display_label,
        'os_type': template.os_type or infer_os_type(template.name),
        'version': template.version,
        'enabled': template.enabled,
        'sort_order': template.sort_order,
        'node_template_ref': (template.backup_filename or '')[:255],
        'description': (template.download_url or '')[:255],
        'os_template': template,
    }
    existing = OsImage.objects.filter(os_template=template).first()
    if existing is None:
        existing = OsImage.objects.filter(code=template.code).first()
    if existing is None:
        return OsImage.objects.create(**fields)
    for key, value in fields.items():
        setattr(existing, key, value)
    existing.save(update_fields=[*fields.keys(), 'updated_at'])
    return existing


def default_user_for_template(template) -> str:
    return infer_cloud_ciuser(
        f'{getattr(template, "name", "")} {getattr(template, "backup_filename", "")} {getattr(template, "code", "")}'
    )


def _split_os_name_version(name: str, code: str = '') -> tuple[str, str]:
    text = (name or '').strip()
    match = re.match(r'^(.+?)\s+(\d+(?:\.\d+)*)(?:\s*\(.*\))?$', text)
    if match:
        return match.group(1).strip(), match.group(2)
    if code and re.search(r'\d', code):
        return text or code, code
    return text or 'Linux', code or 'cloud'


def find_existing_os_template(*, code='', filename='', url='', name='', version=''):
    from .models import SystemOsTemplate

    qs = SystemOsTemplate.objects.all()
    if code:
        found = qs.filter(code=code).first()
        if found:
            return found
        compact = normalize_os_code(code)
        if compact:
            for row in qs.only('id', 'code'):
                if normalize_os_code(row.code) == compact:
                    return qs.filter(pk=row.pk).first()
    if filename:
        found = qs.filter(backup_filename=filename).first()
        if found:
            return found
    if url:
        found = qs.filter(download_url=url).first()
        if found:
            return found
    if name and version:
        return qs.filter(name=name, version=version).first()
    return None


def apply_pop_catalog_item(item: dict) -> tuple[object | None, str]:
    """写入或升级一条 Cloud 目录。返回 (模版, created|updated|kept|skipped)。

    已由管理员配好的 Cloud 条目不覆盖；仍是 vzdump/.vma.zst 的同名版本会升级。
    """
    from django.db import IntegrityError

    from .models import SystemOsTemplate

    if not isinstance(item, dict):
        return None, 'skipped'
    url = str(item.get('url') or item.get('download_url') or '').strip()
    filename = str(item.get('filename') or '').strip() or filename_from_url(url)
    code = str(item.get('code') or '').strip()[:32]
    if not url or not filename or is_vzdump_filename(filename):
        return None, 'skipped'
    display = str(item.get('name') or code or filename).strip()
    name, version = _split_os_name_version(display, code)
    existing = find_existing_os_template(
        code=code, filename=filename, url=url, name=name, version=version,
    )
    if existing is not None:
        if not is_stale_os_template(existing) and (existing.download_url or '').strip():
            return existing, 'kept'
        existing.backup_filename = filename[:255]
        existing.download_url = url[:512]
        existing.source_type = SystemOsTemplate.SourceType.CLOUD
        if not existing.os_type or existing.os_type == SystemOsTemplate.OSType.OTHER:
            existing.os_type = infer_os_type(display)
        if not (existing.remark or '').strip():
            existing.remark = '由 POP Cloud 目录同步'
        existing.save()
        return existing, 'updated'
    payload = {
        'name': name[:128],
        'version': (version or code or 'cloud')[:64],
        'backup_filename': filename[:255],
        'download_url': url[:512],
        'os_type': infer_os_type(display),
        'source_type': SystemOsTemplate.SourceType.CLOUD,
        'enabled': True,
        'remark': '由 POP Cloud 目录同步',
    }
    if code:
        payload['code'] = code
    try:
        return SystemOsTemplate.objects.create(**payload), 'created'
    except IntegrityError:
        found = find_existing_os_template(
            code=code, filename=filename, url=url, name=payload['name'], version=payload['version'],
        )
        return found, 'kept' if found else 'skipped'


def upsert_os_template_from_pop_item(item: dict):
    """把 POP os-catalog 条目写入中心模版（已存在的 Cloud 配置不覆盖）。"""
    obj, _action = apply_pop_catalog_item(item)
    return obj


def apply_catalog_items(items) -> dict:
    created, upgraded, kept = [], [], []
    catalog = [item for item in (items or []) if isinstance(item, dict)]
    for item in catalog:
        obj, action = apply_pop_catalog_item(item)
        code = getattr(obj, 'code', '') or str(item.get('code') or '')
        if action == 'created' and code:
            created.append(code)
        elif action == 'updated' and code:
            upgraded.append(code)
        elif action == 'kept' and code:
            kept.append(code)
    return {
        'items': catalog,
        'created_codes': created,
        'upgraded_codes': upgraded,
        'kept_codes': kept,
        'created_count': len(created),
        'upgraded_count': len(upgraded),
        'catalog_count': len(catalog),
    }


def sync_os_catalog_from_pop(pop) -> list[dict]:
    """同步 POP Cloud OS 到中心模版目录，返回节点目录原文。"""
    return sync_os_catalog_from_pop_report(pop)['items']


def sync_os_catalog_from_pop_report(pop) -> dict:
    from resource_manager.pve_ops import fetch_pop_os_catalog

    report = apply_catalog_items(fetch_pop_os_catalog(pop))
    report['pop_id'] = getattr(pop, 'pk', None)
    report['pop_name'] = getattr(pop, 'name', '') or ''
    return report


def seed_default_os_templates() -> dict:
    """按官方 Cloud 目录预置/升级中心模版（不覆盖已配好的 Cloud URL）。"""
    return apply_catalog_items(DEFAULT_CLOUD_CATALOG)


def annotate_os_templates_for_portal(templates, catalog_items=None) -> list[dict]:
    """给门户 OS 列表补充默认用户、节点是否已就绪、缺失时是否可下载。"""
    from .serializers import SystemOsTemplateBriefSerializer

    catalog_items = [item for item in (catalog_items or []) if isinstance(item, dict)]
    by_code = {
        str(item.get('code') or '').strip().lower(): item
        for item in catalog_items if item.get('code')
    }
    by_file = {
        str(item.get('filename') or '').strip().lower(): item
        for item in catalog_items if item.get('filename')
    }
    rows = []
    for template in templates:
        data = SystemOsTemplateBriefSerializer(template).data
        match = by_code.get((template.code or '').strip().lower())
        if match is None:
            match = by_file.get((template.backup_filename or '').strip().lower())
        default_user = default_user_for_template(template)
        if match and match.get('default_user'):
            default_user = str(match['default_user'])
        ready = bool(match and match.get('ready'))
        data['default_user'] = default_user
        data['ready_on_pop'] = ready
        data['will_download'] = (not ready) and bool(getattr(template, 'download_url', ''))
        data['os_family'] = (match or {}).get('os_family') or template.os_type
        rows.append(data)
    return rows


def sync_all_os_images() -> int:
    from .models import SystemOsTemplate

    count = 0
    for template in SystemOsTemplate.objects.all():
        sync_os_image(template)
        count += 1
    return count
