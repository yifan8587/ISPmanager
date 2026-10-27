"""
PVE 虚机生命周期服务：通过节点 vpopmanager 的 pvemanager API 对 POP 点上的
虚拟机执行电源 / 创建 / 克隆 / 删除等操作，实现 ISPmanager 中心统一运维。

约定：
  - 同步虚机（VPS.is_synced=True）记录 source_ref（节点 VM 主键）与
    source_host_id（节点 PVE 主机），生命周期调用据此定位节点对象。
  - 所有写操作支持 dry_run（默认 True）：仅返回将执行的计划，不实际操作节点。
  - 写操作成功后立即刷新本 POP 的虚机缓存，使中心数据与节点一致。
"""

import logging
import re
import threading

from django.db import transaction
from django.utils import timezone

from .models import PopPoint, VPS
from .pemanager_client import PemanagerClient, PemanagerError, pop_has_api_config

logger = logging.getLogger(__name__)

_VM_NAME_SAFE_RE = re.compile(r'[^A-Za-z0-9-]+')


def sanitize_vm_name(name: str, default: str = 'vm') -> str:
    """与 vpopmanager 一致：PVE 要求 RFC1123 hostname。"""
    cleaned = _VM_NAME_SAFE_RE.sub('-', (name or '').strip())
    cleaned = re.sub(r'-{2,}', '-', cleaned).strip('-')
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f'vm-{cleaned}'.strip('-') or default
    return (cleaned[:63].rstrip('-') or default)

_POWER_ACTIONS = ('start', 'stop', 'restart', 'shutdown')
_FILE_TEMPLATE_TYPES = ('cloud', 'backup', 'vztmpl', 'iso')


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _template_filename(tpl: dict) -> str:
    filename = str(tpl.get('filename') or '').strip()
    if filename:
        return filename.rsplit('/', 1)[-1]
    volume = str(tpl.get('volume') or '').strip()
    return volume.rsplit('/', 1)[-1] if volume else ''


def _storage_content(item: dict) -> str:
    return str(item.get('content') or '')


def _is_image_storage(item: dict) -> bool:
    content = _storage_content(item)
    return 'images' in content or 'rootdir' in content


def _is_backup_only_storage(item: dict) -> bool:
    content = _storage_content(item)
    return 'backup' in content and not _is_image_storage(item)


def _volume_matches(item: dict, filename: str, volume: str) -> bool:
    volid = str(item.get('volid') or item.get('volume') or '')
    if not volid:
        return False
    if volume and volid == volume:
        return True
    name = volid.rsplit('/', 1)[-1]
    return bool(filename) and name == filename


def _pick_image_storage(storages: list, node: str, current: str = '') -> str:
    scoped = [
        item for item in storages or []
        if isinstance(item, dict)
        and _is_image_storage(item)
        and (not node or not item.get('node') or item.get('node') == node)
    ]
    if current and any(item.get('storage') == current and not _is_backup_only_storage(item) for item in scoped):
        return current
    preferred = next(
        (
            item.get('storage')
            for item in scoped
            if item.get('storage') and (
                str(item.get('type') or '') in ('lvmthin', 'lvm', 'zfspool', 'rbd', 'cephfs')
                or 'lvm' in str(item.get('storage') or '')
            )
        ),
        '',
    )
    if preferred:
        return preferred
    first = next((item.get('storage') for item in scoped if item.get('storage')), '')
    return first or current


def locate_template_volume(client, host_id, tpl: dict, candidate_nodes: list[str] | None = None) -> dict | None:
    """在候选 PVE 节点上查找模板备份/镜像文件，返回实际所在节点。"""
    source = tpl.get('source_type') or ''
    if source not in _FILE_TEMPLATE_TYPES:
        return None
    filename = _template_filename(tpl)
    volume = str(tpl.get('volume') or '').strip()
    storage = str(tpl.get('storage') or '').strip() or 'local'
    if not filename and not volume:
        return None
    nodes = []
    for name in [tpl.get('node'), *(candidate_nodes or [])]:
        name = str(name or '').strip()
        if name and name not in nodes:
            nodes.append(name)
    if not nodes or not host_id:
        return None
    for node in nodes:
        items = []
        try:
            if source in ('cloud', 'iso'):
                content = 'iso'
            elif source == 'vztmpl':
                content = 'vztmpl'
            else:
                content = 'backup'
            items = client.host_storage_content(host_id, node, storage, content) or []
        except (PemanagerError, AttributeError, TypeError):
            items = []
        if not items and source == 'backup':
            try:
                items = client.host_backups(host_id, node, storage) or []
            except (PemanagerError, AttributeError, TypeError):
                items = []
        for item in items:
            if isinstance(item, dict) and _volume_matches(item, filename, volume):
                return {
                    'node': node,
                    'storage': storage,
                    'volume': item.get('volid') or volume,
                    'filename': filename,
                }
    return None


def resolve_template_create_payload(client, payload: dict, *, hosts=None, templates=None) -> dict:
    """对齐 vpopmanager：备份/模板文件按模板所在节点查找，并补齐下载地址。

    ISPmanager 对话框容易把 PVE 节点填成主机名（如 singapore-pve1），
    而备份实际在集群节点名（如 s79157）的 local:backup 上。
    """
    data = dict(payload or {})
    template_id = _as_int(data.get('template_id'))
    if not template_id:
        return data
    try:
        templates = templates if templates is not None else (client.list_templates() or [])
    except (PemanagerError, AttributeError, TypeError):
        templates = []
    tpl = next(
        (
            item for item in templates
            if isinstance(item, dict) and _as_int(item.get('id')) == template_id
        ),
        None,
    )
    if not tpl:
        return data

    host_id = _as_int(data.get('host_id')) or _as_int(tpl.get('host'))
    if host_id:
        data['host_id'] = host_id
    source = tpl.get('source_type') or ''
    tpl_node = str(tpl.get('node') or '').strip()
    requested_node = str(data.get('node') or '').strip()

    if hosts is None:
        try:
            hosts = client.list_pve_hosts() or []
        except (PemanagerError, AttributeError, TypeError):
            hosts = []
    host = next(
        (item for item in hosts if isinstance(item, dict) and _as_int(item.get('id')) == host_id),
        None,
    )
    host_nodes = []
    storages = []
    if host:
        for node in host.get('nodes') or []:
            name = node.get('node') if isinstance(node, dict) else str(node or '')
            if name:
                host_nodes.append(name)
        storages = list(host.get('storages') or [])
    if host_id and not storages:
        try:
            storages = client.host_storages(host_id) or []
        except (PemanagerError, AttributeError, TypeError):
            storages = []

    candidate_nodes = [requested_node, tpl_node, *host_nodes]
    located = None
    if source in _FILE_TEMPLATE_TYPES:
        try:
            located = locate_template_volume(client, host_id, tpl, candidate_nodes)
        except (PemanagerError, AttributeError, TypeError):
            located = None

    if located:
        data['node'] = located['node']
    elif tpl_node and (source in _FILE_TEMPLATE_TYPES or source == 'template_vm' or not requested_node):
        data['node'] = tpl_node
    elif requested_node:
        data['node'] = requested_node

    download_url = str(data.get('download_url') or tpl.get('download_url') or '').strip()
    if download_url:
        data['download_url'] = download_url

    if source in ('cloud', 'backup', 'vztmpl'):
        data['storage'] = _pick_image_storage(storages, data.get('node') or '', data.get('storage') or '')
    return data


def _merge_template_nodes(hosts: list, templates: list) -> None:
    """把模板记录里的真实 PVE 节点名补进主机下拉，避免只剩主机名兜底。"""
    by_host = {}
    for tpl in templates or []:
        if not isinstance(tpl, dict):
            continue
        host_id = _as_int(tpl.get('host'))
        node = str(tpl.get('node') or '').strip()
        if host_id and node:
            by_host.setdefault(host_id, set()).add(node)
    for host in hosts or []:
        if not isinstance(host, dict):
            continue
        host_id = _as_int(host.get('id'))
        nodes = list(host.get('nodes') or [])
        existing = {
            item.get('node') if isinstance(item, dict) else str(item or '')
            for item in nodes
        }
        live = [
            item.get('node')
            for item in nodes
            if isinstance(item, dict)
            and item.get('node')
            and item.get('status') not in ('unknown', 'template')
        ]
        for node in sorted(by_host.get(host_id, set())):
            if node not in existing:
                nodes.append({'node': node, 'status': 'template'})
                existing.add(node)
        host['nodes'] = nodes
        preferred = ''
        if live:
            preferred = live[0]
        elif by_host.get(host_id):
            preferred = sorted(by_host[host_id])[0]
        elif host.get('default_node') in existing:
            preferred = host.get('default_node')
        elif nodes:
            first = nodes[0]
            preferred = first.get('node') if isinstance(first, dict) else str(first or '')
        host['preferred_node'] = preferred or host.get('default_node') or ''


def _connect(pop: PopPoint):
    return PemanagerClient.connect_pop(pop)


def _refresh_vms(pop: PopPoint, client: PemanagerClient):
    """操作后刷新本 POP 的同步虚机缓存。"""
    from .sync_service import _sync_vms  # 延迟导入避免循环
    try:
        items = client.vms()
        with transaction.atomic():
            return _sync_vms(pop, items, timezone.now())
    except Exception:  # noqa: BLE001
        return None


def _require_ref(vps: VPS):
    if not vps.source_ref:
        raise PemanagerError(
            f'VPS [{vps.vps_no}] 未关联节点虚机（缺少 source_ref），'
            f'请先执行「从节点同步」后再操作。'
        )


# ═══════════════════════════════════════════════════════════════
# 电源操作
# ═══════════════════════════════════════════════════════════════

def vm_power(vps: VPS, action: str, dry_run: bool = True):
    if action not in _POWER_ACTIONS:
        raise PemanagerError(f'无效电源操作：{action}，可选 {_POWER_ACTIONS}')
    _require_ref(vps)
    if dry_run:
        return {'dry_run': True, 'action': action, 'vm_id': vps.source_ref,
                'vps': vps.name, 'note': f'将对节点虚机执行 {action}'}
    client, user = _connect(vps.pop_point)
    result = client.vm_action(vps.source_ref, action)
    refreshed = _refresh_vms(vps.pop_point, client)
    return {'ok': True, 'action': action, 'node_user': user,
            'result': result, 'refreshed': refreshed}


# ═══════════════════════════════════════════════════════════════
# 创建 / 模板创建 / 克隆 / 删除
# ═══════════════════════════════════════════════════════════════

def _prepare_create_payload(payload: dict) -> dict:
    data = dict(payload or {})
    if data.get('newname'):
        data['newname'] = sanitize_vm_name(str(data['newname']))
    if data.get('name'):
        data['name'] = sanitize_vm_name(str(data['name']))
    for key in ('ip', 'gateway', 'prefix', 'ip6', 'gw6'):
        if data.get(key) in (None, ''):
            data.pop(key, None)
    cloud_init = data.get('cloud_init')
    if isinstance(cloud_init, dict):
        data['cloud_init'] = {
            key: value for key, value in cloud_init.items()
            if value not in (None, '')
        }
    return data


def _finish_node_create(pop, client, user, result):
    """节点 create_from_template 返回 202 任务时不要立刻同步虚机列表。"""
    if isinstance(result, dict) and result.get('job_id') and result.get('status') != 'succeeded':
        return {
            'ok': True, 'async': True, 'node_user': user,
            'job_id': result.get('job_id'),
            'status': result.get('status'),
            'progress': result.get('progress') or '已提交节点任务',
            'result': result,
        }
    refreshed = _refresh_vms(pop, client)
    return {'ok': True, 'node_user': user, 'result': result, 'refreshed': refreshed}


def create_vm(pop: PopPoint, payload: dict, dry_run: bool = True):
    payload = _prepare_create_payload(payload)
    if dry_run:
        return {'dry_run': True, 'payload': payload,
                'note': '将在节点上创建新虚机'}
    client, user = _connect(pop)
    result = client.create_vm(payload)
    refreshed = _refresh_vms(pop, client)
    return {'ok': True, 'node_user': user, 'result': result, 'refreshed': refreshed}


def create_from_template(pop: PopPoint, payload: dict, dry_run: bool = True):
    payload = _prepare_create_payload(payload)
    client, user = _connect(pop)
    payload = resolve_template_create_payload(client, payload)
    if dry_run:
        return {'dry_run': True, 'payload': payload,
                'note': '将从模板/备份创建虚机'}
    result = client.create_vm_from_template(payload)
    return _finish_node_create(pop, client, user, result)


def wait_for_create_job(pop: PopPoint, job_id: str, timeout=2100):
    client, user = _connect(pop)
    job = client.wait_for_pve_job(job_id, timeout=timeout)
    refreshed = _refresh_vms(pop, client)
    return {'ok': True, 'node_user': user, 'refreshed': refreshed, **job}


def _match_catalog_template(templates: list, catalog) -> dict | None:
    filename = str(getattr(catalog, 'backup_filename', '') or '').strip().lower()
    url = str(getattr(catalog, 'download_url', '') or '').strip()
    label = str(getattr(catalog, 'display_label', '') or '').strip().lower()
    code = str(getattr(catalog, 'code', '') or '').strip().lower()
    for item in templates or []:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get('name') or '').strip().lower()
        item_file = _template_filename(item).lower()
        item_url = str(item.get('download_url') or '').strip()
        if filename and item_file == filename:
            return item
        if url and item_url == url:
            return item
        if label and (item_name == label or label in item_name):
            return item
        if code and item_name == code:
            return item
    return None


def _pick_backup_storage(storages: list, node: str) -> str:
    scoped = [
        item for item in storages or []
        if isinstance(item, dict)
        and 'backup' in _storage_content(item)
        and (not node or not item.get('node') or item.get('node') == node)
    ]
    for preferred in ('local',):
        hit = next((item.get('storage') for item in scoped if item.get('storage') == preferred), '')
        if hit:
            return hit
    first = next((item.get('storage') for item in scoped if item.get('storage')), '')
    return first or 'local'


def _pick_iso_storage(storages: list, node: str) -> str:
    scoped = [
        item for item in storages or []
        if isinstance(item, dict)
        and 'iso' in _storage_content(item)
        and (not node or not item.get('node') or item.get('node') == node)
    ]
    for preferred in ('local',):
        hit = next((item.get('storage') for item in scoped if item.get('storage') == preferred), '')
        if hit:
            return hit
    first = next((item.get('storage') for item in scoped if item.get('storage')), '')
    return first or 'local'


def _pick_host_node(host: dict) -> str:
    node = str(host.get('preferred_node') or host.get('default_node') or '').strip()
    if node:
        return node
    nodes = host.get('nodes') or []
    if not nodes:
        return ''
    first = nodes[0]
    return str(first.get('node') if isinstance(first, dict) else first or '').strip()


def ensure_os_template_on_pop(pop: PopPoint, catalog, *, timeout=2100) -> dict:
    """若 POP 模板库没有该系统版本，则按中心目录 URL 下载到 PVE 并入库。"""
    if catalog is None:
        raise PemanagerError('未指定系统模版')
    url = str(getattr(catalog, 'download_url', '') or '').strip()
    if not url:
        raise PemanagerError(f'系统模版 {getattr(catalog, "display_label", "")} 缺少下载地址，无法下发到节点')

    client, user = _connect(pop)
    try:
        templates = client.list_templates() or []
    except (PemanagerError, AttributeError, TypeError):
        templates = []
    found = _match_catalog_template(templates, catalog)
    if found:
        try:
            located = locate_template_volume(
                client,
                _as_int(found.get('host')) or _as_int(found.get('host_id')),
                found,
            )
        except (PemanagerError, AttributeError, TypeError):
            located = None
        if located or not url:
            return found

    try:
        hosts = client.list_pve_hosts() or []
    except (PemanagerError, AttributeError, TypeError):
        hosts = []
    if not hosts:
        raise PemanagerError('节点无可用 PVE 主机，无法下载系统模版')
    host = next((item for item in hosts if isinstance(item, dict)), None)
    if host is None:
        raise PemanagerError('节点无可用 PVE 主机，无法下载系统模版')
    host_id = _as_int(host.get('id'))
    node = _pick_host_node(host)
    if not node:
        raise PemanagerError('节点无可用 PVE 节点，无法下载系统模版')
    try:
        storages = host.get('storages') or client.host_storages(host_id, node) or []
    except (PemanagerError, AttributeError, TypeError):
        storages = []
    filename = str(getattr(catalog, 'backup_filename', '') or '').strip()
    source = str(getattr(catalog, 'source_type', '') or 'cloud').strip() or 'cloud'
    from system_manager.os_templates import is_cloud_image_filename, is_vzdump_filename
    if is_vzdump_filename(filename):
        raise PemanagerError(
            f'系统模版 {getattr(catalog, "display_label", "")} 仍是 vzdump/.vma.zst，'
            f'请改为发行版 Cloud 镜像（.qcow2/.img）后再下发'
        )

    if source == 'vztmpl':
        content = 'vztmpl'
        storage = _pick_backup_storage(storages, node)
    elif source == 'iso' and not is_cloud_image_filename(filename):
        content = 'iso'
        storage = _pick_iso_storage(storages, node)
    else:
        content = 'iso'
        storage = _pick_iso_storage(storages, node)
        source = 'cloud'
    payload = {
        'host_id': host_id,
        'node': node,
        'storage': storage,
        'url': url,
        'filename': filename,
        'content': content,
        'source_type': source,
        'register_template': True,
        'template_name': getattr(catalog, 'display_label', '') or getattr(catalog, 'name', ''),
        'wait': True,
        'force': False,
        # 先让节点用 PVE download-url（wget）落到 /var/lib/vz/template/iso/，失败再由中心中转。
        'via_manager': False,
    }
    client.download_template(payload, timeout=timeout)
    try:
        templates = client.list_templates() or []
    except (PemanagerError, AttributeError, TypeError):
        templates = []
    found = _match_catalog_template(templates, catalog)
    if found:
        return found
    raise PemanagerError(
        f'已请求节点下载系统模版 {getattr(catalog, "display_label", "")}，但未能在模板库中定位'
    )


def dispatch_pop_os_image_downloads(pop) -> dict:
    """把已启用的 Cloud 模版下载地址交给 POP，由节点 wget 到模板目录。"""
    from system_manager.models import SystemOsTemplate
    from system_manager.os_templates import is_deployable_os_template, seed_default_os_templates

    if getattr(pop, 'pop_type', '') != PopPoint.PopType.POP:
        return {'skipped': True, 'reason': 'not_pop'}
    if not pop_has_api_config(pop):
        return {'skipped': True, 'reason': 'no_api'}
    seed_default_os_templates()
    client, _user = _connect(pop)
    try:
        hosts = client.list_pve_hosts() or []
    except (PemanagerError, AttributeError, TypeError) as exc:
        return {'skipped': True, 'reason': str(exc), 'submitted': [], 'errors': [str(exc)]}
    host = next((item for item in hosts if isinstance(item, dict)), None)
    if host is None:
        return {'skipped': True, 'reason': 'no_pve_host', 'submitted': [], 'errors': ['节点无可用 PVE 主机']}
    host_id = _as_int(host.get('id'))
    node = _pick_host_node(host)
    if not host_id or not node:
        return {'skipped': True, 'reason': 'no_pve_node', 'submitted': [], 'errors': ['节点无可用 PVE 节点']}
    try:
        storages = host.get('storages') or client.host_storages(host_id, node) or []
    except (PemanagerError, AttributeError, TypeError):
        storages = []
    storage = _pick_iso_storage(storages, node)
    submitted, errors = [], []
    templates = SystemOsTemplate.objects.filter(enabled=True).order_by('sort_order', 'id')
    for template in templates:
        if not is_deployable_os_template(template):
            continue
        payload = {
            'host_id': host_id,
            'node': node,
            'storage': storage,
            'url': template.download_url,
            'filename': template.backup_filename,
            'content': 'iso',
            'source_type': 'cloud',
            'register_template': True,
            'template_name': template.display_label,
            'wait': False,
            'force': False,
            'via_manager': False,
        }
        try:
            client.download_template(payload, timeout=60)
            submitted.append(template.code or template.backup_filename)
        except PemanagerError as exc:
            errors.append(f'{template.display_label}: {exc}')
    return {
        'skipped': False,
        'storage': storage,
        'submitted': submitted,
        'errors': errors,
    }


def schedule_pop_os_image_downloads(pop) -> None:
    """新建或补齐 POP 点 API 后，在后台把系统模版 wget 到节点。"""
    if getattr(pop, 'pop_type', '') != PopPoint.PopType.POP or not pop_has_api_config(pop):
        return
    pop_id = pop.pk

    def _run():
        try:
            current = PopPoint.objects.filter(pk=pop_id).first()
            if current is None:
                return
            dispatch_pop_os_image_downloads(current)
        except Exception:  # noqa: BLE001
            logger.exception('POP %s 下发系统镜像失败', pop_id)

    transaction.on_commit(
        lambda: threading.Thread(target=_run, name=f'isp-os-image-{pop_id}', daemon=True).start()
    )


def poll_pve_job(pop: PopPoint, job_id: str):
    client, user = _connect(pop)
    job = client.get_pve_job(job_id)
    refreshed = None
    if job.get('status') == 'succeeded':
        refreshed = _refresh_vms(pop, client)
    return {'ok': True, 'node_user': user, 'refreshed': refreshed, **job}


def clone_vm(vps: VPS, payload: dict, dry_run: bool = True):
    _require_ref(vps)
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref, 'payload': payload,
                'note': f'将克隆虚机 {vps.name}'}
    client, user = _connect(vps.pop_point)
    result = client.clone_vm(vps.source_ref, payload)
    refreshed = _refresh_vms(vps.pop_point, client)
    return {'ok': True, 'node_user': user, 'result': result, 'refreshed': refreshed}


def provision_vps(pop: PopPoint, payload: dict, dry_run: bool = True):
    """节点一键开通：OS 目录 + 规格 + 账号密码 + IP。"""
    payload = _prepare_create_payload(payload)
    if dry_run:
        return {'dry_run': True, 'payload': payload, 'note': '将在节点一键开通 VPS'}
    client, user = _connect(pop)
    result = client.vps_provision(payload)
    return _finish_node_create(pop, client, user, result)


def resize_vps(vps: VPS, payload: dict, dry_run: bool = True):
    _require_ref(vps)
    data = dict(payload or {})
    if data.get('memory') and not data.get('memory_mb'):
        data['memory_mb'] = data['memory']
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref, 'payload': data,
                'note': f'将调整虚机 {vps.name} 规格'}
    client, user = _connect(vps.pop_point)
    result = client.vps_resize(vps.source_ref, data)
    refreshed = _refresh_vms(vps.pop_point, client)
    return {'ok': True, 'node_user': user, 'result': result, 'refreshed': refreshed}


def set_vps_credentials(vps: VPS, payload: dict, dry_run: bool = True):
    _require_ref(vps)
    data = dict(payload or {})
    data.setdefault('apply_guest', True)
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref,
                'note': f'将更新虚机 {vps.name} 登录凭据'}
    client, user = _connect(vps.pop_point)
    result = client.vps_credentials(vps.source_ref, data)
    return {'ok': True, 'node_user': user, 'result': result}


def set_vps_network(vps: VPS, payload: dict, dry_run: bool = True):
    _require_ref(vps)
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref, 'payload': payload,
                'note': f'将更新虚机 {vps.name} 网络'}
    client, user = _connect(vps.pop_point)
    result = client.vps_network(vps.source_ref, payload)
    return {'ok': True, 'node_user': user, 'result': result}


def reinstall_vps(vps: VPS, payload: dict, dry_run: bool = True):
    _require_ref(vps)
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref, 'payload': payload,
                'note': f'将重装虚机 {vps.name}'}
    client, user = _connect(vps.pop_point)
    result = client.vps_reinstall(vps.source_ref, payload)
    return _finish_node_create(vps.pop_point, client, user, result)


def allocate_vps_bandwidth(vps: VPS, mbps: int, pool_name: str = '', dry_run: bool = True):
    _require_ref(vps)
    payload = {
        'vm_id': int(vps.source_ref),
        'allocated_mbps': int(mbps),
        'net_index': 0,
    }
    if dry_run:
        return {'dry_run': True, 'payload': payload, 'note': '将下发带宽限速'}
    client, user = _connect(vps.pop_point)
    if pool_name:
        payload['pool_name'] = pool_name
    else:
        pools = client.list_bandwidth_pools() or []
        first = next((p.get('name') for p in pools if isinstance(p, dict) and p.get('name')), '')
        if not first:
            raise PemanagerError('节点无可用带宽池，无法下发限速')
        payload['pool_name'] = first
    result = client.allocate_vm_bandwidth(payload)
    return {'ok': True, 'node_user': user, 'result': result}


def delete_vm(vps: VPS, dry_run: bool = True):
    _require_ref(vps)
    if dry_run:
        return {'dry_run': True, 'vm_id': vps.source_ref,
                'note': f'将删除节点虚机 {vps.name}（不可恢复）'}
    client, user = _connect(vps.pop_point)
    result = client.delete_vm(vps.source_ref)
    refreshed = _refresh_vms(vps.pop_point, client)
    return {'ok': True, 'node_user': user, 'result': result, 'refreshed': refreshed}


# ═══════════════════════════════════════════════════════════════
# 节点资源查询（创建虚机时供前端下拉）
# ═══════════════════════════════════════════════════════════════

def _vmid_of(item) -> int | None:
    try:
        return int(item.get('vmid'))
    except (TypeError, ValueError, AttributeError):
        return None


def fetch_pop_os_catalog(pop):
    """拉取 POP 节点 Cloud OS 目录；节点未配置或失败时返回空列表。"""
    from resource_manager.pemanager_client import PemanagerClient, pop_has_api_config

    if pop is None or not pop_has_api_config(pop):
        return []
    try:
        client = PemanagerClient.from_pop(pop, timeout=8)
        client.authenticate()
        items = client.vps_os_catalog() or []
    except (PemanagerError, AttributeError, TypeError, OSError):
        return []
    except Exception:  # noqa: BLE001
        return []
    return [item for item in items if isinstance(item, dict)]


def list_pve_resources(pop: PopPoint):
    """返回节点 PVE 主机 / 节点 / 模板 / 下一个可用 VMID，供创建虚机时选择。

    - 节点名优先取节点实时 PVE 连接（list_nodes）；连不上时回退到
      default_node / 存储节点 / 已有 VM 所在节点，保证前端总能选到节点。
    - next_vmid：节点上已有最大 VMID + 1（至少 100），供前端自动填充，
      避免手工填写导致冲突。
    """
    client, user = _connect(pop)
    hosts = client.list_pve_hosts()

    # 先尽量触发一次节点 VM 同步，使 VMID 反映 PVE 现状（best-effort，连不上则忽略）
    try:
        client.sync_vms()
    except PemanagerError:
        pass
    try:
        all_vms = client.vms()
    except PemanagerError:
        all_vms = []

    def _host_vm_nodes(hid):
        names = set()
        for v in all_vms:
            if isinstance(v, dict) and v.get('host') == hid and v.get('node'):
                names.add(v['node'])
        return names

    result_hosts = []
    for h in hosts or []:
        if not isinstance(h, dict):
            continue
        host_id = h.get('id')
        nodes = []
        node_error = ''
        storages = []
        try:
            nodes = client.list_nodes(host_id) if host_id else []
        except PemanagerError as e:
            node_error = str(e)
            nodes = []
        try:
            # 拉取该主机全部节点的存储，供前端「空白虚机」选择磁盘存储
            storages = client.host_storages(host_id) if host_id else []
        except PemanagerError:
            storages = []
        if not nodes:
            # 兜底：default_node / 存储所在节点 / 已有 VM 所在节点
            names = set()
            if h.get('default_node'):
                names.add(h['default_node'])
            for s in storages:
                if isinstance(s, dict) and s.get('node'):
                    names.add(s['node'])
            names |= _host_vm_nodes(host_id)
            nodes = [{'node': n, 'status': 'unknown'} for n in sorted(filter(None, names))]
        result_hosts.append({
            'id': host_id,
            'name': h.get('name'),
            'host': h.get('host'),
            'default_node': h.get('default_node'),
            'preferred_node': '',
            'last_status': h.get('last_status'),
            'nodes': nodes,
            'storages': storages,
            'node_error': node_error,
        })
    templates = []
    try:
        templates = client.list_templates()
    except PemanagerError:
        templates = []
    _merge_template_nodes(result_hosts, templates)

    # 下一个可用 VMID：所有已知 VM / 模板 VMID 的最大值 + 1（至少从 100 起）
    max_vmid = 99
    for v in all_vms:
        vid = _vmid_of(v)
        if vid is not None:
            max_vmid = max(max_vmid, vid)
    for t in templates or []:
        if isinstance(t, dict):
            tid = t.get('template_vmid')
            if tid:
                try:
                    max_vmid = max(max_vmid, int(tid))
                except (TypeError, ValueError):
                    pass
    next_vmid = max_vmid + 1
    for host in result_hosts:
        hid = host.get('id')
        if not hid:
            continue
        try:
            info = client.vps_next_vmid(hid) or {}
            nid = _as_int(info.get('vmid'))
            if nid:
                next_vmid = max(next_vmid, nid)
                break
        except (PemanagerError, AttributeError, TypeError):
            continue

    os_catalog = []
    try:
        os_catalog = client.vps_os_catalog() or []
    except (PemanagerError, AttributeError, TypeError):
        os_catalog = []

    return {
        'node_user': user,
        'hosts': result_hosts,
        'templates': templates,
        'next_vmid': next_vmid,
        'os_catalog': os_catalog,
    }
