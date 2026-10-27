"""
系统版本号：以项目根目录 VERSION 文件为准，缺省回退 V1.1。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings

DEFAULT_VERSION = '1.1'
PRODUCT_NAME = 'ISPmanager'
_VERSION_RE = re.compile(r'^\s*v?\s*(\d+(?:\.\d+){0,3})\s*$', re.IGNORECASE)


def project_root() -> Path:
    configured = getattr(settings, 'ISPMANAGER_ROOT', None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR).resolve().parent


def version_file_path() -> Path:
    configured = getattr(settings, 'ISPMANAGER_VERSION_FILE', None)
    if configured:
        return Path(configured)
    root = project_root()
    for candidate in (root / 'VERSION', Path(settings.BASE_DIR) / 'VERSION'):
        if candidate.is_file():
            return candidate
    return root / 'VERSION'


def parse_version(raw: str | None) -> tuple[int, ...]:
    text = (raw or '').strip()
    if not text:
        return (0,)
    match = _VERSION_RE.match(text.splitlines()[0])
    if not match:
        digits = re.findall(r'\d+', text)
        if not digits:
            return (0,)
        return tuple(int(x) for x in digits[:4])
    return tuple(int(part) for part in match.group(1).split('.'))


def normalize_version(raw: str | None, default: str = DEFAULT_VERSION) -> str:
    parsed = parse_version(raw)
    if parsed == (0,) and not (raw or '').strip():
        return default
    match = _VERSION_RE.match((raw or '').strip().splitlines()[0] if raw else '')
    if match:
        return match.group(1)
    if parsed != (0,):
        return '.'.join(str(x) for x in parsed)
    return default


def format_version_display(raw: str | None) -> str:
    version = normalize_version(raw)
    return f'V{version}' if version else f'V{DEFAULT_VERSION}'


def compare_versions(left: str | None, right: str | None) -> int:
    a = parse_version(left)
    b = parse_version(right)
    width = max(len(a), len(b), 1)
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def read_version_text() -> tuple[str, str]:
    path = version_file_path()
    try:
        if path.is_file():
            raw = path.read_text(encoding='utf-8').strip().splitlines()[0].strip()
            if raw:
                return normalize_version(raw), 'file'
    except OSError:
        pass
    env = os.environ.get('ISPMANAGER_VERSION', '').strip()
    if env:
        return normalize_version(env), 'env'
    return DEFAULT_VERSION, 'default'


def write_version(version: str) -> Path:
    path = version_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_version(version)
    path.write_text(f'{normalized}\n', encoding='utf-8')
    return path


def get_system_version() -> dict:
    version, source = read_version_text()
    path = version_file_path()
    return {
        'product': PRODUCT_NAME,
        'version': version,
        'display': format_version_display(version),
        'source': source,
        'version_file': str(path) if path.exists() else '',
    }
