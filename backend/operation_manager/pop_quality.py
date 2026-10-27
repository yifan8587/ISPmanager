"""
POP 管理入口品质探测、状态评估与告警。

此模块独立于 Zabbix：它直接检查资源管理中 POP 的管理 IP 和管理 URL，
用于快速掌握管理面连通性。Zabbix 监控图形继续作为主机/业务监控数据源。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from resource_manager.models import PopPoint

from .models import PopQualityMonitor, PopQualityProbe

logger = logging.getLogger(__name__)

_PING_SUMMARY_RE = re.compile(
    r'(?P<sent>\d+)\s+packets?\s+transmitted,\s+'
    r'(?P<received>\d+)\s+(?:packets?\s+)?received,\s+'
    r'(?P<loss>[\d.]+)%\s+packet\s+loss',
    re.IGNORECASE,
)
_PING_RTT_RE = re.compile(
    r'(?:rtt|round-trip)[^=]*=\s*'
    r'(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)',
    re.IGNORECASE,
)
_CURL_METRICS_RE = re.compile(r'(?P<code>\d{3})\s+(?P<seconds>[\d.]+)\s*$')


class ProbeInProgressError(RuntimeError):
    """同一 POP 已被另一个探测任务占用。"""


def probe_tools_status() -> dict[str, bool]:
    """返回运行 POP 探测所需命令的可用状态。"""
    return {
        'ping': bool(shutil.which('ping')),
        'curl': bool(shutil.which('curl')),
    }


def _command_env() -> dict[str, str]:
    env = dict(os.environ)
    # 解析 ping/curl 输出依赖稳定的英文格式。
    env.update({'LC_ALL': 'C', 'LANG': 'C'})
    return env


def _compact_error(text: str, fallback: str) -> str:
    text = (text or '').strip().replace('\x00', '')
    return text[:1000] or fallback


def probe_icmp(target: str, *, count: int, timeout_seconds: int) -> dict[str, Any]:
    """调用 ping，返回可序列化的丢包与时延结果。"""
    result: dict[str, Any] = {
        'target': target,
        'sent': None,
        'received': None,
        'packet_loss_pct': None,
        'min_ms': None,
        'avg_ms': None,
        'max_ms': None,
        'success': None,
        'error': '',
    }
    if not target:
        return result

    count = max(1, min(int(count or 4), 20))
    timeout_seconds = max(1, min(int(timeout_seconds or 5), 60))
    command = ['ping', '-n', '-q', '-c', str(count), '-W', str(timeout_seconds), '--', target]
    if ':' in target:
        command.insert(1, '-6')

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(timeout_seconds * count + 5, 10),
            check=False,
            env=_command_env(),
        )
    except FileNotFoundError:
        result['error'] = '服务器未安装 ping，无法执行 ICMP 探测'
        return result
    except subprocess.TimeoutExpired:
        result['error'] = f'ICMP 探测超时（>{timeout_seconds * count + 5} 秒）'
        return result
    except OSError as exc:
        result['error'] = f'ICMP 探测启动失败：{exc}'
        return result

    output = '\n'.join(value for value in (completed.stdout, completed.stderr) if value)
    summary = _PING_SUMMARY_RE.search(output)
    if summary:
        result.update({
            'sent': int(summary.group('sent')),
            'received': int(summary.group('received')),
            'packet_loss_pct': float(summary.group('loss')),
            'success': int(summary.group('received')) > 0,
        })
        rtt = _PING_RTT_RE.search(output)
        if rtt:
            result.update({
                'min_ms': float(rtt.group('min')),
                'avg_ms': float(rtt.group('avg')),
                'max_ms': float(rtt.group('max')),
            })
        return result

    result['error'] = _compact_error(
        completed.stderr or completed.stdout,
        f'ping 命令返回异常（退出码 {completed.returncode}）',
    )
    return result


def probe_http(target: str, *, timeout_seconds: int) -> dict[str, Any]:
    """调用 curl 检查管理 URL 的 HTTP(S) 响应码与耗时。"""
    result: dict[str, Any] = {
        'target': target,
        'status_code': None,
        'response_ms': None,
        'success': None,
        'error': '',
    }
    if not target:
        return result

    parsed = urlparse(target)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        result['error'] = '管理 URL 必须是有效的 http:// 或 https:// 地址'
        return result

    timeout_seconds = max(1, min(int(timeout_seconds or 5), 60))
    command = [
        'curl',
        '--silent',
        '--show-error',
        '--location',
        '--max-redirs', '3',
        '--connect-timeout', str(timeout_seconds),
        '--max-time', str(timeout_seconds),
        '--output', os.devnull,
        '--write-out', '%{http_code} %{time_total}',
        '--',
        target,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 5,
            check=False,
            env=_command_env(),
        )
    except FileNotFoundError:
        result['error'] = '服务器未安装 curl，无法执行管理 URL 探测'
        return result
    except subprocess.TimeoutExpired:
        result['error'] = f'管理 URL 探测超时（>{timeout_seconds} 秒）'
        return result
    except OSError as exc:
        result['error'] = f'管理 URL 探测启动失败：{exc}'
        return result

    match = _CURL_METRICS_RE.search(completed.stdout or '')
    if match:
        status_code = int(match.group('code'))
        result.update({
            'status_code': status_code or None,
            'response_ms': round(float(match.group('seconds')) * 1000, 3),
            # 401/403 等代表管理入口可达，只是需要认证；5xx 才视为服务异常。
            'success': 100 <= status_code < 500,
        })
        if completed.returncode and not result['success']:
            result['error'] = _compact_error(
                completed.stderr,
                f'curl 命令返回异常（退出码 {completed.returncode}）',
            )
        return result

    result['error'] = _compact_error(
        completed.stderr or completed.stdout,
        f'curl 命令返回异常（退出码 {completed.returncode}）',
    )
    return result


def discover_pop_quality_monitors(*, include_maintenance: bool = True) -> dict[str, int]:
    """
    为拥有管理 IP 或管理 URL 的 POP 创建监控配置。

    已存在配置不会被覆盖，便于运维人员保留每个 POP 的个性化阈值和开关。
    """
    pop_qs = PopPoint.objects.exclude(status=PopPoint.PopStatus.DECOMMISSIONED).filter(
        Q(mgmt_ip__isnull=False) | ~Q(mgmt_url=''),
    )
    if not include_maintenance:
        pop_qs = pop_qs.exclude(status=PopPoint.PopStatus.MAINTENANCE)

    created = existing = 0
    for pop in pop_qs.iterator():
        _monitor, was_created = PopQualityMonitor.objects.get_or_create(
            pop_point=pop,
            defaults={
                'enable_icmp': bool(pop.mgmt_ip),
                'enable_http': bool(pop.mgmt_url),
            },
        )
        created += int(was_created)
        existing += int(not was_created)
    return {
        'created': created,
        'existing': existing,
        'eligible_pop_count': created + existing,
    }


def _lease_seconds(monitor: PopQualityMonitor) -> int:
    timeout = max(int(monitor.timeout_seconds or 5), 1)
    icmp_budget = timeout * max(int(monitor.icmp_count or 1), 1) if monitor.enable_icmp else 0
    http_budget = timeout if monitor.enable_http else 0
    # ping/curl 进程各有额外等待时间；留足余量才能避免租约在探测尚未结束时失效。
    return max(icmp_budget + http_budget + 60, 60)


def _reserve_monitor(
    monitor_id: int,
    *,
    now=None,
) -> PopQualityMonitor:
    """用数据库行锁为一次探测领取租约，防止多个守护实例重复探测。"""
    now = now or timezone.now()
    with transaction.atomic():
        monitor = (
            PopQualityMonitor.objects
            .select_for_update()
            .select_related('pop_point')
            .get(pk=monitor_id)
        )
        lock_active = monitor.probe_lock_until and monitor.probe_lock_until > now
        if lock_active:
            raise ProbeInProgressError('该 POP 正在执行品质探测，请稍后重试')

        monitor.probe_lock_until = now + timedelta(seconds=_lease_seconds(monitor))
        monitor.next_probe_at = now + timedelta(seconds=max(monitor.probe_interval_seconds, 15))
        monitor.save(update_fields=['probe_lock_until', 'next_probe_at', 'updated_at'])
        return monitor


def claim_due_monitors(*, now=None, batch_size: int = 20) -> list[int]:
    """领取到期监控对象，供常驻命令安全地跨进程消费。"""
    now = now or timezone.now()
    batch_size = max(1, min(int(batch_size), 200))
    due_ids: list[int] = []
    with transaction.atomic():
        monitors = list(
            PopQualityMonitor.objects
            .select_for_update()
            .filter(is_enabled=True)
            .filter(Q(next_probe_at__isnull=True) | Q(next_probe_at__lte=now))
            .filter(Q(probe_lock_until__isnull=True) | Q(probe_lock_until__lte=now))
            .exclude(pop_point__status=PopPoint.PopStatus.DECOMMISSIONED)
            .order_by('next_probe_at', 'id')[:batch_size]
        )
        for monitor in monitors:
            monitor.probe_lock_until = now + timedelta(seconds=_lease_seconds(monitor))
            monitor.next_probe_at = now + timedelta(
                seconds=max(monitor.probe_interval_seconds, 15),
            )
            monitor.save(update_fields=['probe_lock_until', 'next_probe_at', 'updated_at'])
            due_ids.append(monitor.pk)
    return due_ids


def _probe_status(
    *,
    has_target: bool,
    icmp_result: dict[str, Any] | None,
    http_result: dict[str, Any] | None,
    threshold: float,
) -> str:
    if not has_target:
        return PopQualityProbe.Status.SKIPPED
    if icmp_result and icmp_result.get('packet_loss_pct') is not None:
        loss = float(icmp_result['packet_loss_pct'])
        if loss >= 100:
            return PopQualityProbe.Status.FAILED
        if loss >= threshold:
            return PopQualityProbe.Status.DEGRADED
    if icmp_result and icmp_result.get('error'):
        if http_result and http_result.get('success'):
            return PopQualityProbe.Status.DEGRADED
        return PopQualityProbe.Status.ERROR
    if http_result and http_result.get('success') is False:
        return PopQualityProbe.Status.DEGRADED if icmp_result else PopQualityProbe.Status.FAILED
    if http_result and http_result.get('error'):
        return PopQualityProbe.Status.DEGRADED if icmp_result and icmp_result.get('success') else PopQualityProbe.Status.ERROR
    return PopQualityProbe.Status.SUCCESS


def _monitor_status(probe_status: str) -> str:
    mapping = {
        PopQualityProbe.Status.SUCCESS: PopQualityMonitor.Status.HEALTHY,
        PopQualityProbe.Status.DEGRADED: PopQualityMonitor.Status.DEGRADED,
        PopQualityProbe.Status.FAILED: PopQualityMonitor.Status.UNREACHABLE,
        PopQualityProbe.Status.SKIPPED: PopQualityMonitor.Status.SKIPPED,
        PopQualityProbe.Status.ERROR: PopQualityMonitor.Status.ERROR,
    }
    return mapping.get(probe_status, PopQualityMonitor.Status.ERROR)


def _unexpected_probe_result(
    *,
    target: str,
    probe_type: str,
    error: Exception,
) -> dict[str, Any]:
    """将探测实现中的意外异常降级为可审计的失败采样。"""
    message = f'{probe_type} 探测内部异常：{error}'
    if probe_type == 'ICMP':
        return {
            'target': target,
            'sent': None,
            'received': None,
            'packet_loss_pct': None,
            'min_ms': None,
            'avg_ms': None,
            'max_ms': None,
            'success': None,
            'error': message,
        }
    return {
        'target': target,
        'status_code': None,
        'response_ms': None,
        'success': None,
        'error': message,
    }


def _monitor_snapshot(monitor: PopQualityMonitor) -> dict[str, Any]:
    return {
        'status': monitor.status,
        'packet_loss_pct': monitor.last_icmp_packet_loss_pct,
        'icmp_avg_ms': monitor.last_icmp_avg_ms,
        'http_response_ms': monitor.last_http_response_ms,
        'http_status_code': monitor.last_http_status_code,
        'consecutive_breaches': monitor.consecutive_breaches,
        'alert_active': monitor.alert_active,
    }


def _probe_snapshot(
    monitor: PopQualityMonitor,
    probe: PopQualityProbe,
) -> dict[str, Any]:
    return {
        'status': monitor.status,
        'mgmt_ip': probe.icmp_target,
        'mgmt_url': probe.http_target,
        'packet_loss_pct': probe.icmp_packet_loss_pct,
        'icmp_avg_ms': probe.icmp_avg_ms,
        'icmp_min_ms': probe.icmp_min_ms,
        'icmp_max_ms': probe.icmp_max_ms,
        'http_response_ms': probe.http_response_ms,
        'http_status_code': probe.http_status_code,
        'packet_loss_threshold_pct': monitor.packet_loss_threshold_pct,
        'consecutive_breaches': monitor.consecutive_breaches,
        'alert_active': monitor.alert_active,
        'probe_status': probe.status,
    }


def _alert_detail(
    monitor: PopQualityMonitor,
    probe: PopQualityProbe,
    *,
    recovered: bool,
) -> str:
    prefix = 'POP 丢包已恢复' if recovered else 'POP 丢包率超过阈值'
    loss = (
        f'{probe.icmp_packet_loss_pct:.2f}%'
        if probe.icmp_packet_loss_pct is not None else '未获取'
    )
    latency = (
        f'{probe.icmp_avg_ms:.2f} ms'
        if probe.icmp_avg_ms is not None else '未获取'
    )
    return (
        f'{prefix}：POP={monitor.pop_label}，管理 IP={probe.icmp_target or "未配置"}，'
        f'当前丢包={loss}，阈值={monitor.packet_loss_threshold_pct:.2f}%，'
        f'连续次数={monitor.consecutive_breaches}，ICMP 平均响应={latency}。'
    )


def _schedule_alert_notification(
    monitor: PopQualityMonitor,
    probe: PopQualityProbe,
    *,
    old_value: dict[str, Any],
    action: str,
) -> None:
    """在事务提交后走已有 system_alert 通知规则。"""
    from system_manager.im_services import notify

    recovered = action == PopQualityProbe.AlertKind.RECOVERED
    context = {
        'module': '运维品质监控',
        'action': 'recovered' if recovered else 'alert',
        'action_label': 'POP 丢包恢复' if recovered else 'POP 丢包告警',
        'target_type': 'POP 节点',
        'target_name': monitor.pop_label,
        'target_id': monitor.pop_point_id,
        'pop_name': monitor.pop_point.name,
        'operator_name': '系统品质监控',
        'detail': _alert_detail(monitor, probe, recovered=recovered),
        'old_value': old_value,
        'new_value': _probe_snapshot(monitor, probe),
        'occurred_at': probe.finished_at or timezone.now(),
    }
    dedupe_key = (
        f'pop-quality:{monitor.pop_point_id}:packet-loss:'
        f'{"recovered" if recovered else "alert"}'
    )
    transaction.on_commit(
        lambda: notify('system_alert', context=context, dedupe_key=dedupe_key),
    )


def _update_alert_state(
    monitor: PopQualityMonitor,
    probe: PopQualityProbe,
    *,
    now,
) -> str:
    """
    根据有效 ICMP 数据推进品质告警状态机。

    返回要写入采样的 alert_kind；维护期或没有有效 ICMP 指标时不改变告警状态。
    """
    loss = probe.icmp_packet_loss_pct
    if monitor.is_alert_suppressed or loss is None:
        return PopQualityProbe.AlertKind.NONE

    breach = float(loss) >= float(monitor.packet_loss_threshold_pct)
    if breach:
        monitor.consecutive_breaches += 1
        threshold_reached = (
            monitor.consecutive_breaches >= monitor.consecutive_breach_threshold
        )
        repeat_due = (
            not monitor.last_alert_at
            or now >= monitor.last_alert_at + timedelta(minutes=monitor.alert_repeat_minutes)
        )
        if threshold_reached and (not monitor.alert_active or repeat_due):
            monitor.alert_active = True
            monitor.alert_started_at = monitor.alert_started_at or now
            monitor.last_alert_at = now
            return PopQualityProbe.AlertKind.ALERT
        return PopQualityProbe.AlertKind.NONE

    monitor.consecutive_breaches = 0
    if monitor.alert_active:
        monitor.alert_active = False
        monitor.last_recovered_at = now
        return PopQualityProbe.AlertKind.RECOVERED
    return PopQualityProbe.AlertKind.NONE


def _record_skipped_probe(
    monitor: PopQualityMonitor,
    *,
    status: str,
    message: str,
    started_at,
    monitor_status: str | None = None,
) -> PopQualityProbe:
    probe = PopQualityProbe.objects.create(
        monitor=monitor,
        status=status,
        icmp_target=monitor.pop_point.mgmt_ip or '',
        http_target=monitor.pop_point.mgmt_url or '',
        error_message=message,
        started_at=started_at,
        finished_at=timezone.now(),
    )
    monitor.status = monitor_status or _monitor_status(status)
    monitor.last_probe_at = probe.finished_at
    monitor.last_error = message
    monitor.probe_lock_until = None
    monitor.save(update_fields=[
        'status', 'last_probe_at', 'last_error', 'probe_lock_until', 'updated_at',
    ])
    return probe


def run_monitor_probe(
    monitor_id: int,
    *,
    claimed: bool = False,
) -> PopQualityProbe:
    """
    对一个 POP 执行完整探测、持久化采样并在必要时投递 IM 告警。

    ``claimed=True`` 表示调用方已经通过 ``claim_due_monitors`` 获取数据库租约。
    """
    started_at = timezone.now()
    if not claimed:
        _reserve_monitor(monitor_id, now=started_at)

    monitor = (
        PopQualityMonitor.objects
        .select_related('pop_point')
        .get(pk=monitor_id)
    )
    if not monitor.is_enabled:
        return _record_skipped_probe(
            monitor,
            status=PopQualityProbe.Status.SKIPPED,
            message='该 POP 品质监控已停用',
            started_at=started_at,
            monitor_status=PopQualityMonitor.Status.DISABLED,
        )
    if monitor.pop_point.status == PopPoint.PopStatus.DECOMMISSIONED:
        return _record_skipped_probe(
            monitor,
            status=PopQualityProbe.Status.SKIPPED,
            message='POP 已下线，跳过品质探测',
            started_at=started_at,
        )

    icmp_target = monitor.pop_point.mgmt_ip or ''
    http_target = monitor.pop_point.mgmt_url or ''
    has_target = bool(
        (monitor.enable_icmp and icmp_target)
        or (monitor.enable_http and http_target)
    )
    if not has_target:
        return _record_skipped_probe(
            monitor,
            status=PopQualityProbe.Status.SKIPPED,
            message='未配置可用的管理 IP 或管理 URL',
            started_at=started_at,
        )

    icmp_result = None
    if monitor.enable_icmp and icmp_target:
        try:
            icmp_result = probe_icmp(
                icmp_target,
                count=monitor.icmp_count,
                timeout_seconds=monitor.timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception('ICMP quality probe crashed monitor_id=%s', monitor.pk)
            icmp_result = _unexpected_probe_result(
                target=icmp_target,
                probe_type='ICMP',
                error=exc,
            )

    http_result = None
    if monitor.enable_http and http_target:
        try:
            http_result = probe_http(http_target, timeout_seconds=monitor.timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.exception('HTTP quality probe crashed monitor_id=%s', monitor.pk)
            http_result = _unexpected_probe_result(
                target=http_target,
                probe_type='HTTP',
                error=exc,
            )
    errors = [
        result.get('error')
        for result in (icmp_result, http_result)
        if result and result.get('error')
    ]
    probe_status = _probe_status(
        has_target=has_target,
        icmp_result=icmp_result,
        http_result=http_result,
        threshold=float(monitor.packet_loss_threshold_pct),
    )
    finished_at = timezone.now()

    with transaction.atomic():
        monitor = (
            PopQualityMonitor.objects
            .select_for_update()
            .select_related('pop_point')
            .get(pk=monitor_id)
        )
        if not monitor.is_enabled:
            return _record_skipped_probe(
                monitor,
                status=PopQualityProbe.Status.SKIPPED,
                message='探测执行期间品质监控已被停用',
                started_at=started_at,
                monitor_status=PopQualityMonitor.Status.DISABLED,
            )
        if monitor.pop_point.status == PopPoint.PopStatus.DECOMMISSIONED:
            return _record_skipped_probe(
                monitor,
                status=PopQualityProbe.Status.SKIPPED,
                message='探测执行期间 POP 已下线，结果未计入品质状态',
                started_at=started_at,
            )
        old_value = _monitor_snapshot(monitor)
        probe = PopQualityProbe.objects.create(
            monitor=monitor,
            status=probe_status,
            icmp_target=icmp_target,
            icmp_sent=icmp_result.get('sent') if icmp_result else None,
            icmp_received=icmp_result.get('received') if icmp_result else None,
            icmp_packet_loss_pct=icmp_result.get('packet_loss_pct') if icmp_result else None,
            icmp_min_ms=icmp_result.get('min_ms') if icmp_result else None,
            icmp_avg_ms=icmp_result.get('avg_ms') if icmp_result else None,
            icmp_max_ms=icmp_result.get('max_ms') if icmp_result else None,
            icmp_success=icmp_result.get('success') if icmp_result else None,
            http_target=http_target,
            http_status_code=http_result.get('status_code') if http_result else None,
            http_response_ms=http_result.get('response_ms') if http_result else None,
            http_success=http_result.get('success') if http_result else None,
            error_message='；'.join(errors)[:2000],
            started_at=started_at,
            finished_at=finished_at,
        )
        monitor.status = _monitor_status(probe_status)
        monitor.last_probe_at = finished_at
        monitor.last_icmp_packet_loss_pct = probe.icmp_packet_loss_pct
        monitor.last_icmp_avg_ms = probe.icmp_avg_ms
        monitor.last_http_response_ms = probe.http_response_ms
        monitor.last_http_status_code = probe.http_status_code
        monitor.last_error = probe.error_message
        monitor.probe_lock_until = None
        alert_kind = _update_alert_state(
            monitor,
            probe,
            now=finished_at,
        )
        probe.alert_kind = alert_kind
        probe.save(update_fields=['alert_kind'])
        monitor.save(update_fields=[
            'status', 'last_probe_at', 'last_icmp_packet_loss_pct',
            'last_icmp_avg_ms', 'last_http_response_ms', 'last_http_status_code',
            'last_error', 'probe_lock_until', 'consecutive_breaches',
            'alert_active', 'alert_started_at', 'last_alert_at',
            'last_recovered_at', 'updated_at',
        ])
        if alert_kind:
            _schedule_alert_notification(
                monitor,
                probe,
                old_value=old_value,
                action=alert_kind,
            )
    return probe


def run_due_probes(*, batch_size: int = 20) -> list[PopQualityProbe]:
    """执行一批到期的探测任务；单个 POP 的失败不阻断其他 POP。"""
    probes = []
    for monitor_id in claim_due_monitors(batch_size=batch_size):
        try:
            probes.append(run_monitor_probe(monitor_id, claimed=True))
        except Exception:  # noqa: BLE001
            logger.exception('POP 品质探测失败 monitor_id=%s', monitor_id)
            PopQualityMonitor.objects.filter(pk=monitor_id).update(probe_lock_until=None)
    return probes


def purge_old_probes(*, retention_days: int = 90) -> int:
    """删除过期采样，控制高频时序数据体积。"""
    retention_days = max(1, int(retention_days))
    threshold = timezone.now() - timedelta(days=retention_days)
    deleted, _detail = PopQualityProbe.objects.filter(started_at__lt=threshold).delete()
    return deleted
