"""
系统业务数据备份 / 还原 / 在线升级。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tarfile
import threading
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections, connections
from django.utils import timezone

from .version import (
    compare_versions,
    format_version_display,
    get_system_version,
    normalize_version,
    project_root,
    write_version,
)

logger = logging.getLogger(__name__)

BACKUP_FORMAT = 'ispmanager-backup'
PACKAGE_NAME = 'ISPmanager'
BLOCKED_OVERLAY_PARTS = {
    'venv', '.venv', 'node_modules', '__pycache__', '.git', 'media',
    'staticfiles', 'system_backups', 'system_upgrades',
}
BLOCKED_OVERLAY_NAMES = {'.env', 'ispmanager.env'}
ALLOWED_ARCHIVE_SUFFIX = ('.tar.gz', '.tgz', '.tar')
_SKIPPABLE_OVERLAY_DIRS = {'ReadMe', 'readme', 'docs'}
_SEMVER_LINE = re.compile(r'^\s*v?\s*\d+(?:\.\d+){0,3}\s*$', re.IGNORECASE)


class MaintenanceError(Exception):
    pass


def backup_root() -> Path:
    path = Path(getattr(settings, 'SYSTEM_BACKUP_DIR', Path(settings.MEDIA_ROOT) / 'system_backups'))
    path.mkdir(parents=True, exist_ok=True)
    return path


def upgrade_root() -> Path:
    path = Path(getattr(settings, 'SYSTEM_UPGRADE_DIR', Path(settings.MEDIA_ROOT) / 'system_upgrades'))
    path.mkdir(parents=True, exist_ok=True)
    return path


def format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            if unit == 'B':
                return f'{int(value)} {unit}'
            return f'{value:.1f} {unit}'
        value /= 1024
    return f'{size} B'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save_upload(uploaded, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open('wb') as fh:
        for chunk in uploaded.chunks():
            fh.write(chunk)
            size += len(chunk)
    return size


def safe_archive_name(name: str) -> str:
    base = os.path.basename(name or '')
    return base.replace('\x00', '').strip() or 'package.tar.gz'


def is_archive_name(name: str) -> bool:
    lower = (name or '').lower()
    return lower.endswith(ALLOWED_ARCHIVE_SUFFIX)


def has_running_job() -> bool:
    from .models import SystemBackup, SystemUpgradePackage
    return (
        SystemBackup.objects.filter(
            status__in=[SystemBackup.Status.PENDING, SystemBackup.Status.RUNNING]
        ).exists()
        or SystemUpgradePackage.objects.filter(
            status=SystemUpgradePackage.Status.APPLYING
        ).exists()
    )


def _in_sync_mode() -> bool:
    return getattr(settings, 'SYSTEM_MAINTENANCE_SYNC', False)


def _thread_db_guard():
    if not _in_sync_mode():
        close_old_connections()


def run_background(func, *args, **kwargs):
    if _in_sync_mode():
        func(*args, **kwargs)
        return

    def _wrapper():
        close_old_connections()
        try:
            func(*args, **kwargs)
        except Exception:
            logger.exception('系统维护后台任务失败')
        finally:
            close_old_connections()

    threading.Thread(target=_wrapper, daemon=True).start()


def _append_log(obj, line: str, field: str = 'apply_log'):
    text = (getattr(obj, field, '') or '')
    stamp = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
    setattr(obj, field, f'{text}[{stamp}] {line}\n')
    obj.save(update_fields=[field])


def _db_conf() -> dict:
    return settings.DATABASES['default']


def _mysql_defaults_file(conf: dict) -> str:
    fd, path = tempfile.mkstemp(prefix='isp-my-', suffix='.cnf')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write('[client]\n')
        fh.write(f'host={conf.get("HOST") or "127.0.0.1"}\n')
        fh.write(f'port={conf.get("PORT") or 3306}\n')
        fh.write(f'user={conf.get("USER") or "root"}\n')
        fh.write(f'password={conf.get("PASSWORD") or ""}\n')
        fh.write('default-character-set=utf8mb4\n')
    os.chmod(path, 0o600)
    return path


def _is_mysql(conf: dict | None = None) -> bool:
    engine = (conf or _db_conf()).get('ENGINE', '')
    return 'mysql' in engine


def export_database(dest_dir: Path) -> dict:
    """导出业务库，返回 manifest 片段。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    conf = _db_conf()
    if _is_mysql(conf) and shutil.which('mysqldump'):
        sql_path = dest_dir / 'data.sql'
        defaults = _mysql_defaults_file(conf)
        try:
            cmd = [
                'mysqldump',
                f'--defaults-extra-file={defaults}',
                '--single-transaction',
                '--routines',
                '--triggers',
                '--hex-blob',
                '--default-character-set=utf8mb4',
                conf.get('NAME') or '',
            ]
            with sql_path.open('wb') as fh:
                result = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
            if result.returncode != 0:
                err = result.stderr.decode('utf-8', 'replace')
                raise MaintenanceError(f'mysqldump 失败：{err[:800]}')
        finally:
            Path(defaults).unlink(missing_ok=True)
        return {
            'engine': 'mysql',
            'payload': 'data.sql',
            'payload_format': 'mysql_sql',
            'database': conf.get('NAME') or '',
        }

    json_path = dest_dir / 'data.json'
    with json_path.open('w', encoding='utf-8') as fh:
        call_command(
            'dumpdata',
            '--natural-foreign',
            '--natural-primary',
            exclude=[
                'contenttypes',
                'auth.permission',
                'sessions',
                'admin.logentry',
                'token_blacklist',
            ],
            format='json',
            indent=2,
            stdout=fh,
        )
    return {
        'engine': conf.get('ENGINE', ''),
        'payload': 'data.json',
        'payload_format': 'django_dumpdata',
        'database': conf.get('NAME') or '',
    }


def import_database(extract_dir: Path, manifest: dict):
    payload = manifest.get('payload') or ''
    fmt = manifest.get('payload_format') or ''
    src = extract_dir / payload if payload else None
    if src is None or not src.is_file():
        sql = extract_dir / 'data.sql'
        js = extract_dir / 'data.json'
        if sql.is_file():
            src, fmt = sql, 'mysql_sql'
        elif js.is_file():
            src, fmt = js, 'django_dumpdata'
        else:
            raise MaintenanceError('备份包内没有 data.sql / data.json')

    connections.close_all()
    conf = _db_conf()
    if fmt == 'mysql_sql':
        if not shutil.which('mysql'):
            raise MaintenanceError('系统未安装 mysql 客户端，无法还原 SQL 备份')
        defaults = _mysql_defaults_file(conf)
        try:
            with src.open('rb') as fh:
                result = subprocess.run(
                    ['mysql', f'--defaults-extra-file={defaults}', conf.get('NAME') or ''],
                    stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
            if result.returncode != 0:
                err = result.stderr.decode('utf-8', 'replace')
                raise MaintenanceError(f'mysql 还原失败：{err[:800]}')
        finally:
            Path(defaults).unlink(missing_ok=True)
        return

    call_command('loaddata', str(src))


def _safe_members(tar: tarfile.TarFile):
    for member in tar.getmembers():
        name = (member.name or '').replace('\\', '/')
        if name.startswith('/') or name.startswith('..') or '/../' in f'/{name}/':
            raise MaintenanceError(f'压缩包含非法路径：{member.name}')
        parts = Path(name).parts
        if any(part == '..' for part in parts):
            raise MaintenanceError(f'压缩包含非法路径：{member.name}')
        yield member


def extract_archive(archive: Path, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:*') as tar:
        members = list(_safe_members(tar))
        kwargs = {'path': dest, 'members': members}
        try:
            tar.extractall(filter='data', **kwargs)
        except TypeError:
            tar.extractall(**kwargs)


def read_json_if_exists(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _is_metadata_version_path(name: str) -> bool:
    path = name.replace('\\', '/')
    return path.endswith('deploy/VERSION') or '/deploy/VERSION' in path


def _semver_from_version_text(raw: str) -> str:
    if not (raw or '').strip():
        return ''
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith('#'):
            continue
        if text.lower().startswith('version='):
            text = text.split('=', 1)[1].strip()
        if _SEMVER_LINE.match(text):
            return normalize_version(text)
    return ''


def inspect_upgrade_package(archive: Path) -> dict:
    with tarfile.open(archive, 'r:*') as tar:
        names = [m.name.replace('\\', '/') for m in tar.getmembers() if m.isfile()]
        version_text = ''
        manifest = {}
        for name in names:
            base = os.path.basename(name)
            if base == 'manifest.json' and name.count('/') <= 3:
                extracted = tar.extractfile(name)
                if extracted is None:
                    continue
                raw = extracted.read().decode('utf-8', 'replace')
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and (parsed.get('version') or not manifest):
                        manifest = parsed
                except json.JSONDecodeError:
                    pass
            if base == 'VERSION' and name.count('/') <= 3 and not _is_metadata_version_path(name):
                extracted = tar.extractfile(name)
                if extracted is None:
                    continue
                raw = extracted.read().decode('utf-8', 'replace')
                parsed = _semver_from_version_text(raw)
                if parsed:
                    version_text = parsed
        version = normalize_version(
            manifest.get('version') or version_text,
            default='',
        )
        if not version:
            raise MaintenanceError('更新包缺少 VERSION 或 manifest.json 中的 version')
        return {
            'version': version,
            'display': format_version_display(version),
            'min_version': normalize_version(manifest.get('min_version'), default='') if manifest.get('min_version') else '',
            'changelog': str(manifest.get('changelog') or manifest.get('description') or '').strip(),
            'name': str(manifest.get('name') or PACKAGE_NAME),
            'type': str(manifest.get('type') or 'update'),
            'has_apply_script': any(os.path.basename(n) == 'apply.sh' for n in names),
            'files': len(names),
        }


def validate_upgrade_against_current(info: dict):
    current = get_system_version()['version']
    min_version = info.get('min_version') or ''
    target = info.get('version') or ''
    if min_version and compare_versions(current, min_version) < 0:
        raise MaintenanceError(
            f'当前版本 {format_version_display(current)} 低于更新包要求的最低版本 '
            f'{format_version_display(min_version)}'
        )
    if target and compare_versions(target, current) < 0:
        raise MaintenanceError(
            f'更新包版本 {format_version_display(target)} 低于当前版本 '
            f'{format_version_display(current)}，拒绝降级'
        )
    if target and compare_versions(target, current) == 0:
        raise MaintenanceError(f'更新包版本与当前版本相同（{format_version_display(current)}）')


def overlay_file_allowed(rel_path: str) -> bool:
    normalized = rel_path.replace('\\', '/')
    while normalized.startswith('./'):
        normalized = normalized[2:]
    if normalized.startswith('/') or normalized.startswith('../') or '/../' in f'/{normalized}/':
        return False
    parts = Path(normalized).parts
    if not parts or any(part == '..' for part in parts):
        return False
    if any(part in BLOCKED_OVERLAY_PARTS for part in parts):
        return False
    if Path(normalized).name in BLOCKED_OVERLAY_NAMES:
        return False
    if normalized in ('deploy/.env', 'backend/.env'):
        return False
    return True


def overlay_path_skippable(rel_path: str) -> bool:
    """文档等非运行文件：属主是 root 时跳过，不阻断程序升级。"""
    normalized = (rel_path or '').replace('\\', '/').lstrip('./')
    parts = Path(normalized).parts
    if parts and parts[0] in _SKIPPABLE_OVERLAY_DIRS:
        return True
    name = Path(normalized).name.lower()
    return name.endswith(('.md', '.rst', '.txt')) and (
        len(parts) == 1 or (parts and parts[0] in _SKIPPABLE_OVERLAY_DIRS)
    )


def overlay_app_user() -> str:
    return (
        (os.environ.get('APP_USER') or '').strip()
        or str(getattr(settings, 'SYSTEM_OVERLAY_USER', '') or '').strip()
        or 'ispmanager'
    )


def overlay_permission_message(denied: list[str], dest_root: Path) -> str:
    dest_root = Path(dest_root)
    user = overlay_app_user()
    group = (os.environ.get('APP_GROUP') or '').strip() or user
    parent = dest_root.parent
    script = dest_root / 'deploy' / 'scripts' / 'fix-overlay-permissions.sh'
    sample = '、'.join(denied[:4])
    extra = f' 等 {len(denied)} 个文件' if len(denied) > 4 else ''
    if script.is_file():
        fix = f'sudo bash {script}'
    else:
        targets = [
            dest_root / 'backend',
            dest_root / 'frontend',
            dest_root / 'ReadMe',
            dest_root / 'deploy',
            parent / 'ISPweb',
        ]
        existing = ' '.join(str(p) for p in targets if p.exists()) or str(dest_root)
        fix = f'sudo chown -R {user}:{group} {existing}'
    return (
        f'无法覆盖 {sample}{extra}：服务用户无权写入（Errno 13）。'
        f'请在服务器执行 `{fix}` 后重新升级；'
        f'或解压 ispmanager-update-*.tar.gz 后执行 '
        f'`sudo bash /tmp/isp-update/update.sh --root {parent}`。'
    )


def _ensure_owner_writable(target: Path) -> None:
    if not target.exists():
        return
    if os.access(target, os.W_OK):
        return
    try:
        os.chmod(target, stat.S_IMODE(target.stat().st_mode) | 0o200)
    except OSError:
        pass
    if os.access(target, os.W_OK):
        return
    try:
        target.unlink()
    except OSError:
        pass


def replace_overlay_file(src: Path, target: Path) -> None:
    """覆盖目标文件：不沿用源文件的只读位，避免下次升级再 Permission denied。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_owner_writable(target)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix='.isp-upg-',
        suffix=target.suffix or '.tmp',
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, 'wb') as handle, src.open('rb') as src_fh:
            shutil.copyfileobj(src_fh, handle)
        dest_mode = 0o755 if (src.stat().st_mode & 0o111) else 0o644
        os.chmod(tmp_path, dest_mode)
        try:
            os.replace(tmp_path, target)
        except PermissionError:
            _ensure_owner_writable(target)
            os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _package_payload_root(extract_dir: Path) -> Path:
    if (extract_dir / 'VERSION').is_file() or (extract_dir / 'manifest.json').is_file() \
            or (extract_dir / 'backend').is_dir() or (extract_dir / 'apply.sh').is_file():
        return extract_dir
    children = [p for p in extract_dir.iterdir() if p.is_dir() and not p.name.startswith('.')]
    if len(children) == 1:
        child = children[0]
        if (child / 'VERSION').is_file() or (child / 'backend').is_dir() or (child / 'apply.sh').is_file():
            return child
        nested = child / PACKAGE_NAME
        if nested.is_dir():
            return nested
    isp = extract_dir / PACKAGE_NAME
    if isp.is_dir():
        return isp
    return extract_dir


def discover_overlay_roots(extract_dir: Path, dest_root: Path) -> list[tuple[Path, Path]]:
    """发布包可能是 ISPmanager + ISPweb 同级，必须同时覆盖两端。"""
    dest_root = Path(dest_root)
    parent = dest_root.parent
    candidates = [extract_dir]
    children = [p for p in extract_dir.iterdir() if p.is_dir() and not p.name.startswith('.')]
    if len(children) == 1:
        candidates.insert(0, children[0])

    for root in candidates:
        isp = root / PACKAGE_NAME
        web = root / 'ISPweb'
        if isp.is_dir() and ((isp / 'backend').is_dir() or (isp / 'VERSION').is_file()):
            pairs = [(isp, dest_root)]
            if web.is_dir():
                pairs.append((web, parent / 'ISPweb'))
            return pairs
        if (root / 'backend').is_dir() or (root / 'VERSION').is_file() or (root / 'apply.sh').is_file():
            pairs = [(root, dest_root)]
            sibling = root.parent / 'ISPweb'
            if sibling.is_dir() and sibling.resolve() != root.resolve():
                pairs.append((sibling, parent / 'ISPweb'))
            return pairs
    payload = _package_payload_root(extract_dir)
    return [(payload, dest_root)]


def overlay_upgrade_payload(extract_dir: Path, dest_root: Path) -> int:
    copied = 0
    for src, dest in discover_overlay_roots(extract_dir, dest_root):
        copied += overlay_extracted_files(src, dest)
    return copied


def find_apply_script(extract_dir: Path) -> Path | None:
    for src, _dest in discover_overlay_roots(extract_dir, project_root()):
        candidate = src / 'apply.sh'
        if candidate.is_file():
            return candidate
        parent_script = src.parent / 'apply.sh'
        if src.name == PACKAGE_NAME and parent_script.is_file():
            return parent_script
    root_script = extract_dir / 'apply.sh'
    return root_script if root_script.is_file() else None


def apply_code_upgrade(dest_root: Path, log_cb=None):
    """覆盖文件后执行 pip / migrate / collectstatic / 权限同步。失败则抛错。"""
    dest_root = Path(dest_root)
    backend = dest_root / 'backend'
    venv_pip = dest_root / 'venv' / 'bin' / 'pip'
    req = backend / 'requirements.txt'
    env = os.environ.copy()

    def _log(line: str):
        if log_cb:
            log_cb(line)

    if req.is_file() and venv_pip.is_file():
        result = subprocess.run(
            [str(venv_pip), 'install', '-r', str(req), '--upgrade', '--upgrade-strategy', 'only-if-needed'],
            cwd=str(backend),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=1800,
            check=False,
        )
        _log((result.stdout or '')[-2000:])
        if result.returncode != 0:
            raise MaintenanceError(f'安装 Python 依赖失败（退出码 {result.returncode}）')

    try:
        call_command('migrate', interactive=False, verbosity=0, skip_checks=True)
        _log('数据库迁移完成')
    except TypeError:
        call_command('migrate', interactive=False, verbosity=0)
        _log('数据库迁移完成')
    except Exception as exc:
        raise MaintenanceError(f'数据库迁移失败：{exc}') from exc

    try:
        call_command('collectstatic', interactive=False, verbosity=0)
        _log('静态资源已收集')
    except Exception as exc:
        raise MaintenanceError(f'collectstatic 失败：{exc}') from exc

    for cmd in ('sync_permissions', 'sync_audit_logs'):
        try:
            call_command(cmd, verbosity=0)
            _log(f'{cmd} 完成')
        except Exception as exc:
            _log(f'{cmd} 跳过：{exc}')


def overlay_extracted_files(payload_root: Path, dest_root: Path) -> int:
    copied = 0
    skipped = []
    denied = []
    dest_root = Path(dest_root)
    for src in payload_root.rglob('*'):
        if not src.is_file():
            continue
        rel = src.relative_to(payload_root).as_posix()
        if rel in ('VERSION', 'manifest.json', 'apply.sh', 'UPDATE.md'):
            continue
        if not overlay_file_allowed(rel):
            continue
        target = dest_root / rel
        try:
            replace_overlay_file(src, target)
            copied += 1
        except (PermissionError, OSError) as exc:
            errno = getattr(exc, 'errno', None)
            if not isinstance(exc, PermissionError) and errno not in (1, 13):
                raise
            if overlay_path_skippable(rel):
                skipped.append(rel)
                logger.warning('升级覆盖跳过无权限文件：%s', target)
                continue
            denied.append(str(target))
    if skipped:
        logger.info('升级跳过 %s 个文档/无权限文件', len(skipped))
    if denied:
        raise MaintenanceError(overlay_permission_message(denied, dest_root))
    return copied


def _run_apply_script(script: Path, dest_root: Path, log_cb=None) -> str:
    env = os.environ.copy()
    env['ISPMANAGER_ROOT'] = str(dest_root)
    env['ISPMANAGER_VERSION'] = get_system_version()['version']
    result = subprocess.run(
        ['bash', str(script), str(dest_root)],
        cwd=str(script.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=1800,
        check=False,
    )
    output = result.stdout or ''
    if log_cb:
        log_cb(output)
    if result.returncode != 0:
        raise MaintenanceError(f'apply.sh 退出码 {result.returncode}')
    return output


def schedule_restart():
    if not getattr(settings, 'SYSTEM_UPGRADE_RESTART', True):
        return
    cmd = (getattr(settings, 'SYSTEM_UPGRADE_RESTART_CMD', '') or '').strip()
    if not cmd:
        return

    def _restart():
        try:
            subprocess.run(cmd, shell=True, timeout=60, check=False)
        except Exception:
            logger.exception('升级后重启服务失败：%s', cmd)

    timer = threading.Timer(2.0, _restart)
    timer.daemon = True
    timer.start()


def create_backup_record(*, backup_type: str, note: str = '', user=None, name: str = ''):
    from .models import SystemBackup
    version = get_system_version()['version']
    stamp = timezone.now().strftime('%Y%m%d-%H%M%S')
    display = format_version_display(version)
    record = SystemBackup.objects.create(
        name=name or f'业务数据备份 {display} {stamp}',
        backup_type=backup_type,
        status=SystemBackup.Status.PENDING,
        source_version=version,
        note=note or '',
        created_by=user,
    )
    return record


def perform_backup(backup_id: int):
    from .models import SystemBackup
    _thread_db_guard()
    backup = SystemBackup.objects.filter(pk=backup_id).first()
    if not backup:
        return
    backup.status = SystemBackup.Status.RUNNING
    backup.save(update_fields=['status'])
    work = Path(tempfile.mkdtemp(prefix='isp-bak-'))
    try:
        payload = export_database(work)
        version = get_system_version()
        manifest = {
            'format': BACKUP_FORMAT,
            'format_version': 1,
            'backup_type': backup.backup_type,
            'created_at': timezone.now().isoformat(),
            'source_version': version['version'],
            'source_version_display': version['display'],
            'note': backup.note,
            **payload,
        }
        (work / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8',
        )
        (work / 'VERSION').write_text(f"{version['version']}\n", encoding='utf-8')
        stamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        file_name = f"ispmanager-data-V{version['version']}-{stamp}.tar.gz"
        dest = backup_root() / f'{backup.pk}-{file_name}'
        with tarfile.open(dest, 'w:gz') as tar:
            for item in work.iterdir():
                tar.add(item, arcname=item.name)
        backup.file_name = file_name
        backup.storage_path = str(dest)
        backup.file_size = dest.stat().st_size
        backup.checksum_sha256 = sha256_file(dest)
        backup.status = SystemBackup.Status.SUCCESS
        backup.error_message = ''
        backup.finished_at = timezone.now()
        backup.save(update_fields=[
            'file_name', 'storage_path', 'file_size', 'checksum_sha256',
            'status', 'error_message', 'finished_at', 'source_version',
        ])
    except Exception as exc:
        logger.exception('创建系统备份失败 #%s', backup_id)
        backup.status = SystemBackup.Status.FAILED
        backup.error_message = str(exc)[:2000]
        backup.finished_at = timezone.now()
        backup.save(update_fields=['status', 'error_message', 'finished_at'])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def start_backup(*, backup_type: str = 'data', note: str = '', user=None, name: str = ''):
    if has_running_job():
        raise MaintenanceError('已有备份或升级任务进行中，请稍后再试')
    record = create_backup_record(backup_type=backup_type, note=note, user=user, name=name)
    run_background(perform_backup, record.pk)
    return record


def resolve_backup_file(backup) -> Path:
    path = Path(backup.storage_path) if backup.storage_path else backup_root() / backup.file_name
    if not path.is_file():
        raise MaintenanceError('备份文件不存在或已被删除')
    return path


def inspect_backup_archive(archive: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix='isp-bak-insp-'))
    try:
        extract_archive(archive, work)
        payload_root = work
        if not (work / 'manifest.json').is_file():
            children = [p for p in work.iterdir() if p.is_dir()]
            if len(children) == 1 and (children[0] / 'manifest.json').is_file():
                payload_root = children[0]
        manifest = read_json_if_exists(payload_root / 'manifest.json')
        if manifest.get('format') and manifest.get('format') != BACKUP_FORMAT:
            raise MaintenanceError('不是 ISPmanager 业务数据备份包')
        if not (payload_root / 'data.sql').is_file() and not (payload_root / 'data.json').is_file() \
                and not manifest.get('payload'):
            raise MaintenanceError('备份包缺少业务数据文件')
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def perform_restore(backup_id: int, *, safety_backup_id: int | None = None):
    from .models import SystemBackup
    _thread_db_guard()
    backup = SystemBackup.objects.filter(pk=backup_id).first()
    if not backup:
        return
    try:
        archive = resolve_backup_file(backup)
        work = Path(tempfile.mkdtemp(prefix='isp-rst-'))
        try:
            extract_archive(archive, work)
            payload_root = work
            if not (work / 'manifest.json').is_file():
                children = [p for p in work.iterdir() if p.is_dir()]
                if len(children) == 1:
                    payload_root = children[0]
            manifest = read_json_if_exists(payload_root / 'manifest.json')
            import_database(payload_root, manifest)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        logger.info(
            '系统备份 #%s 已还原（安全备份 #%s）',
            backup_id, safety_backup_id,
        )
        schedule_restart()
    except Exception:
        logger.exception('还原系统备份失败 #%s', backup_id)
        raise


def start_restore(backup, *, user=None):
    if backup.status != backup.Status.SUCCESS:
        raise MaintenanceError('只能还原已成功的备份')
    resolve_backup_file(backup)
    if has_running_job():
        raise MaintenanceError('已有备份或升级任务进行中，请稍后再试')
    safety = start_backup(
        backup_type='auto',
        note=f'还原备份 #{backup.pk} 前的自动备份',
        user=user,
        name=f'还原前自动备份 {timezone.now().strftime("%Y%m%d-%H%M%S")}',
    )

    def _wait_and_restore():
        _thread_db_guard()
        from .models import SystemBackup
        deadline = timezone.now() + timedelta(minutes=30)
        while timezone.now() < deadline:
            safety.refresh_from_db()
            if safety.status == SystemBackup.Status.SUCCESS:
                try:
                    perform_restore(backup.pk, safety_backup_id=safety.pk)
                except Exception as exc:
                    safety.refresh_from_db()
                    safety.error_message = f'还原失败：{exc}'[:2000]
                    safety.save(update_fields=['error_message'])
                    raise
                return
            if safety.status == SystemBackup.Status.FAILED:
                raise MaintenanceError(f'还原前自动备份失败：{safety.error_message}')
            import time
            time.sleep(1 if _in_sync_mode() else 2)
        raise MaintenanceError('还原前自动备份超时')

    run_background(_wait_and_restore)
    return safety


def delete_backup_file(backup):
    if backup.storage_path:
        path = Path(backup.storage_path)
        if path.is_file() and str(path.resolve()).startswith(str(backup_root().resolve())):
            path.unlink(missing_ok=True)


def register_uploaded_backup(*, uploaded, note: str = '', user=None):
    name = safe_archive_name(uploaded.name)
    if not is_archive_name(name):
        raise MaintenanceError('仅支持 .tar.gz / .tgz / .tar 备份包')
    max_size = int(getattr(settings, 'SYSTEM_BACKUP_MAX_UPLOAD', 2 * 1024 * 1024 * 1024))
    if uploaded.size and uploaded.size > max_size:
        raise MaintenanceError(f'文件超过上限 {max_size // (1024 * 1024)} MB')
    dest = backup_root() / f'upload-{timezone.now().strftime("%Y%m%d%H%M%S")}-{name}'
    size = save_upload(uploaded, dest)
    try:
        inspect_backup_archive(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    from .models import SystemBackup
    record = SystemBackup.objects.create(
        name=f'上传备份 {name}',
        backup_type=SystemBackup.BackupType.UPLOAD,
        status=SystemBackup.Status.SUCCESS,
        file_name=name,
        storage_path=str(dest),
        file_size=size,
        checksum_sha256=sha256_file(dest),
        source_version='',
        note=note or '',
        created_by=user,
        finished_at=timezone.now(),
    )
    return record


def register_upgrade_package(*, uploaded, user=None):
    name = safe_archive_name(uploaded.name)
    if not is_archive_name(name):
        raise MaintenanceError('仅支持 .tar.gz / .tgz / .tar 更新包')
    max_size = int(getattr(settings, 'SYSTEM_UPGRADE_MAX_UPLOAD', 500 * 1024 * 1024))
    if uploaded.size and uploaded.size > max_size:
        raise MaintenanceError(f'文件超过上限 {max_size // (1024 * 1024)} MB')
    dest = upgrade_root() / f'{timezone.now().strftime("%Y%m%d%H%M%S")}-{name}'
    size = save_upload(uploaded, dest)
    from .models import SystemUpgradePackage
    record = SystemUpgradePackage.objects.create(
        filename=name,
        storage_path=str(dest),
        file_size=size,
        checksum_sha256=sha256_file(dest),
        uploaded_by=user,
        status=SystemUpgradePackage.Status.UPLOADED,
    )
    try:
        info = inspect_upgrade_package(dest)
        validate_upgrade_against_current(info)
        record.package_version = info['version']
        record.min_version = info.get('min_version') or ''
        record.changelog = info.get('changelog') or ''
        record.to_version = info['version']
        record.status = SystemUpgradePackage.Status.READY
        record.save(update_fields=[
            'package_version', 'min_version', 'changelog', 'to_version', 'status',
        ])
    except MaintenanceError as exc:
        record.status = SystemUpgradePackage.Status.REJECTED
        record.error_message = str(exc)[:2000]
        record.save(update_fields=['status', 'error_message'])
        raise
    except Exception as exc:
        record.status = SystemUpgradePackage.Status.REJECTED
        record.error_message = f'更新包校验失败：{exc}'[:2000]
        record.save(update_fields=['status', 'error_message'])
        raise MaintenanceError(record.error_message) from exc
    return record


def perform_upgrade(package_id: int):
    from .models import SystemUpgradePackage
    _thread_db_guard()
    pkg = SystemUpgradePackage.objects.filter(pk=package_id).first()
    if not pkg:
        return
    dest_root = project_root()
    work = Path(tempfile.mkdtemp(prefix='isp-upg-'))
    try:
        _append_log(pkg, f'开始升级：{pkg.filename} → {format_version_display(pkg.package_version)}')
        archive = Path(pkg.storage_path)
        if not archive.is_file():
            raise MaintenanceError('更新包文件不存在')
        extract_archive(archive, work)
        copied = overlay_upgrade_payload(work, dest_root)
        _append_log(pkg, f'覆盖项目文件 {copied} 个（含门户/管理端）')
        apply_sh = find_apply_script(work)
        allow_script = getattr(settings, 'SYSTEM_UPGRADE_ALLOW_APPLY_SCRIPT', True)
        if apply_sh is not None and apply_sh.is_file() and allow_script:
            _append_log(pkg, f'执行 apply.sh {apply_sh}')
            output = _run_apply_script(apply_sh, dest_root)
            if output:
                _append_log(pkg, output[-4000:])
        else:
            apply_code_upgrade(dest_root, log_cb=lambda line: _append_log(pkg, line))
        current = get_system_version()['version']
        target = pkg.package_version or pkg.to_version
        if target and compare_versions(current, target) != 0:
            write_version(target)
            _append_log(pkg, f'已写入版本 {format_version_display(target)}')
        pkg.status = SystemUpgradePackage.Status.SUCCESS
        pkg.error_message = ''
        pkg.finished_at = timezone.now()
        pkg.to_version = target
        pkg.save(update_fields=['status', 'error_message', 'finished_at', 'to_version', 'apply_log'])
        schedule_restart()
        _append_log(pkg, '升级完成，已安排服务重启')
    except Exception as exc:
        logger.exception('系统升级失败 #%s', package_id)
        pkg.status = SystemUpgradePackage.Status.FAILED
        pkg.error_message = str(exc)[:2000]
        pkg.finished_at = timezone.now()
        pkg.save(update_fields=['status', 'error_message', 'finished_at'])
        _append_log(pkg, f'失败：{exc}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def start_upgrade(package, *, user=None):
    from .models import SystemUpgradePackage
    if package.status not in (
        SystemUpgradePackage.Status.READY,
        SystemUpgradePackage.Status.FAILED,
    ):
        raise MaintenanceError('该更新包当前状态不可执行升级')
    if has_running_job():
        raise MaintenanceError('已有备份或升级任务进行中，请稍后再试')
    info = inspect_upgrade_package(Path(package.storage_path))
    validate_upgrade_against_current(info)
    current = get_system_version()['version']
    safety = start_backup(
        backup_type='auto',
        note=f'升级到 {format_version_display(info["version"])} 前的自动备份',
        user=user,
        name=f'升级前自动备份 {timezone.now().strftime("%Y%m%d-%H%M%S")}',
    )
    package.status = SystemUpgradePackage.Status.APPLYING
    package.from_version = current
    package.to_version = info['version']
    package.applied_by = user
    package.applied_at = timezone.now()
    package.auto_backup = safety
    package.error_message = ''
    package.save(update_fields=[
        'status', 'from_version', 'to_version', 'applied_by', 'applied_at',
        'auto_backup', 'error_message',
    ])

    def _wait_and_apply():
        _thread_db_guard()
        from .models import SystemBackup
        deadline = timezone.now() + timedelta(minutes=30)
        import time
        while timezone.now() < deadline:
            safety.refresh_from_db()
            if safety.status == SystemBackup.Status.SUCCESS:
                perform_upgrade(package.pk)
                return
            if safety.status == SystemBackup.Status.FAILED:
                pkg = SystemUpgradePackage.objects.filter(pk=package.pk).first()
                if pkg:
                    pkg.status = SystemUpgradePackage.Status.FAILED
                    pkg.error_message = f'升级前自动备份失败：{safety.error_message}'[:2000]
                    pkg.finished_at = timezone.now()
                    pkg.save(update_fields=['status', 'error_message', 'finished_at'])
                return
            time.sleep(1 if _in_sync_mode() else 2)
        pkg = SystemUpgradePackage.objects.filter(pk=package.pk).first()
        if pkg and pkg.status == SystemUpgradePackage.Status.APPLYING:
            pkg.status = SystemUpgradePackage.Status.FAILED
            pkg.error_message = '升级前自动备份超时'
            pkg.finished_at = timezone.now()
            pkg.save(update_fields=['status', 'error_message', 'finished_at'])

    run_background(_wait_and_apply)
    return package


def delete_upgrade_file(package):
    if package.storage_path:
        path = Path(package.storage_path)
        if path.is_file() and str(path.resolve()).startswith(str(upgrade_root().resolve())):
            path.unlink(missing_ok=True)


def finalize_pending_upgrades():
    """进程启动时：若 VERSION 已到目标版本，把「升级中」记为成功。"""
    from django.db.utils import OperationalError, ProgrammingError
    try:
        from .models import SystemUpgradePackage
        current = get_system_version()['version']
        qs = SystemUpgradePackage.objects.filter(status=SystemUpgradePackage.Status.APPLYING)
        for pkg in qs:
            target = pkg.to_version or pkg.package_version
            if target and compare_versions(current, target) >= 0:
                pkg.status = SystemUpgradePackage.Status.SUCCESS
                pkg.error_message = ''
                pkg.finished_at = pkg.finished_at or timezone.now()
                pkg.save(update_fields=['status', 'error_message', 'finished_at'])
    except (OperationalError, ProgrammingError):
        return
    except Exception:
        logger.exception('收口未完成升级记录失败')


def maintenance_summary() -> dict:
    finalize_pending_upgrades()
    from .models import SystemBackup, SystemUpgradePackage
    latest_backup = SystemBackup.objects.filter(status=SystemBackup.Status.SUCCESS).first()
    latest_upgrade = SystemUpgradePackage.objects.filter(
        status=SystemUpgradePackage.Status.SUCCESS
    ).first()
    version = get_system_version()
    return {
        **version,
        'backup_count': SystemBackup.objects.count(),
        'upgrade_count': SystemUpgradePackage.objects.count(),
        'latest_backup_at': latest_backup.finished_at if latest_backup else None,
        'latest_upgrade_at': latest_upgrade.finished_at if latest_upgrade else None,
        'latest_upgrade_version': latest_upgrade.to_version if latest_upgrade else '',
        'busy': has_running_job(),
    }
