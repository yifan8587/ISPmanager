"""
Zabbix 监控数据后台同步（无 Celery 时使用守护线程 + 限速）。

策略：
  - Zabbix 服务器「同步」：仅拉取主机清单 + 更新已登记监控主机的项/图数量；
  - 已登记 MonitoringHost：在后台缓慢拉取监控项 / 图形（逐台间隔，避免压垮 API）；
  - 未登记的一般 Zabbix 主机：不入库监控项 / 图形明细。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.db import close_old_connections

logger = logging.getLogger(__name__)

SYNC_HOST_DELAY_SEC = 2.0

_server_lock = threading.Lock()
_host_lock = threading.Lock()
_running_servers: set[int] = set()
_running_hosts: set[int] = set()


def _run_background(name: str, fn, *args, **kwargs) -> None:
    def wrapper():
        try:
            close_old_connections()
            fn(*args, **kwargs)
        except Exception:
            logger.exception('background sync failed: %s', name)
        finally:
            close_old_connections()

    threading.Thread(target=wrapper, name=name, daemon=True).start()


def is_server_sync_running(server_id: int) -> bool:
    with _server_lock:
        return server_id in _running_servers


def is_host_sync_running(host_id: int) -> bool:
    with _host_lock:
        return host_id in _running_hosts


def enqueue_server_inventory_sync(server_id: int, *, operator_id: int | None = None) -> bool:
    """若未在运行则排队服务器清单 + 已登记主机明细同步。返回是否成功排队。"""
    with _server_lock:
        if server_id in _running_servers:
            return False
        _running_servers.add(server_id)

    _run_background(
        f'zbx-server-sync-{server_id}',
        _server_inventory_job,
        server_id,
        operator_id,
    )
    return True


def enqueue_host_sync(host_id: int, *, push: bool = True, operator_id: int | None = None) -> bool:
    with _host_lock:
        if host_id in _running_hosts:
            return False
        _running_hosts.add(host_id)

    _run_background(
        f'zbx-host-sync-{host_id}',
        _host_sync_job,
        host_id,
        push,
        operator_id,
    )
    return True


def _resolve_operator(operator_id: int | None):
    if not operator_id:
        return None
    from django.contrib.auth.models import User
    return User.objects.filter(pk=operator_id).first()


def _server_inventory_job(server_id: int, operator_id: int | None) -> None:
    from .models import MonitoringSyncLog, ZabbixServer, record_monitoring_log
    from .zabbix_provision import sync_registered_hosts_items_graphs, sync_server_inventory_light

    operator = _resolve_operator(operator_id)
    try:
        server = ZabbixServer.objects.get(pk=server_id)
        result = sync_server_inventory_light(server)
        sync_registered_hosts_items_graphs(server, delay_sec=SYNC_HOST_DELAY_SEC)
        server.refresh_from_db()
        record_monitoring_log(
            action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
            status=MonitoringSyncLog.Status.SUCCESS,
            message=server.last_sync_message or '后台同步完成',
            zabbix_server=server,
            operator=operator,
            detail=result,
        )
    except Exception as exc:
        try:
            server = ZabbixServer.objects.get(pk=server_id)
            server.last_sync_status = 'error'
            server.last_sync_message = str(exc)[:500]
            server.save(update_fields=['last_sync_status', 'last_sync_message'])
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
                status=MonitoringSyncLog.Status.FAILED,
                message=str(exc),
                zabbix_server=server,
                operator=operator,
            )
        except Exception:
            logger.exception('failed to record server sync error')
    finally:
        with _server_lock:
            _running_servers.discard(server_id)


def _host_sync_job(host_id: int, push: bool, operator_id: int | None) -> None:
    from .models import MonitoringHost, MonitoringSyncLog, record_monitoring_log
    from .zabbix_client import ZabbixError
    from .zabbix_provision import sync_host_full, update_host_inventory_counts

    operator = _resolve_operator(operator_id)
    host = MonitoringHost.objects.select_related('zabbix_server').filter(pk=host_id).first()
    if not host:
        with _host_lock:
            _running_hosts.discard(host_id)
        return

    try:
        host.mark_syncing()
        result = sync_host_full(host, push=push)
        try:
            update_host_inventory_counts(host)
        except (ValueError, ZabbixError):
            pass
        record_monitoring_log(
            action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
            status=MonitoringSyncLog.Status.SUCCESS,
            message=(
                f'同步完成 items={result.get("items", {}).get("total", 0)} '
                f'graphs={result.get("graphs", {}).get("total", 0)}'
            ),
            zabbix_server=host.zabbix_server,
            monitoring_host=host,
            operator=operator,
            detail=result,
        )
    except Exception as exc:
        host.mark_error(str(exc))
        record_monitoring_log(
            action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
            status=MonitoringSyncLog.Status.FAILED,
            message=str(exc),
            zabbix_server=host.zabbix_server,
            monitoring_host=host,
            operator=operator,
        )
    finally:
        with _host_lock:
            _running_hosts.discard(host_id)


def get_sync_job_status(server_id: int | None = None, host_id: int | None = None) -> dict[str, Any]:
    status: dict[str, Any] = {}
    if server_id is not None:
        status['server_running'] = is_server_sync_running(server_id)
    if host_id is not None:
        status['host_running'] = is_host_sync_running(host_id)
    return status
