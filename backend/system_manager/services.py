"""系统配置读取/写入辅助。"""
from __future__ import annotations

from typing import Any

from .models import SystemSetting


def get_setting(key: str, default: Any = None) -> Any:
    return SystemSetting.get_value(key, default=default)


def set_setting(key: str, value: Any, user=None) -> SystemSetting:
    return SystemSetting.set_value(key, value, user=user)


def get_settings_map(keys: list[str] | None = None) -> dict[str, Any]:
    qs = SystemSetting.objects.all()
    if keys:
        qs = qs.filter(key__in=keys)
    return {s.key: s.get_typed_value() for s in qs}
