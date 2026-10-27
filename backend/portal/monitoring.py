"""客户门户的真实流量与边缘品质监控数据适配层。"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from operation_manager.models import (
    EdgeInterfaceQualityRollup,
    EdgeInterfaceQualitySample,
    MonitoringGraphBinding,
)
from operation_manager.resource_monitoring import (
    resolve_traffic_from_binding,
    validate_binding_for_resource,
)
from operation_manager.traffic_analytics import query_traffic
from operation_manager.zabbix_client import ZabbixError

logger = logging.getLogger(__name__)

_RESOURCE_BINDING_FIELDS = {
    'vps': 'vps_id',
    'edge': 'edge_id',
}
_MAX_TRAFFIC_WINDOW = timedelta(hours=168)
_MIN_TRAFFIC_WINDOW = timedelta(minutes=1)


def validate_hours(hours: int | str | None) -> int:
    """门户监控窗口只允许 1 到 168 小时。"""
    try:
        value = int(hours if hours is not None else 24)
    except (TypeError, ValueError) as exc:
        raise ValueError('hours 必须是 1 到 168 的整数') from exc
    if not 1 <= value <= 168:
        raise ValueError('hours 必须在 1 到 168 之间')
    return value


def _parse_portal_datetime(value: str | None, *, field: str) -> datetime:
    if value is None or value == '':
        raise ValueError(f'{field} 不能为空')
    raw = str(value).strip()
    parsed = parse_datetime(raw)
    if parsed is None:
        # 兼容前端 datetime-local / 无 T 分隔的本地时间
        parsed = parse_datetime(raw.replace(' ', 'T', 1))
    if parsed is None:
        raise ValueError(f'{field} 时间格式无效，请使用 ISO 8601')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _format_period_label(time_from: datetime, time_till: datetime, *, hours: int | None = None) -> str:
    if hours is not None:
        return f'{hours}h'
    local_tz = timezone.get_current_timezone()

    def _fmt(dt: datetime) -> str:
        local = timezone.localtime(dt, local_tz)
        return local.strftime('%Y-%m-%d %H:%M')

    return f'{_fmt(time_from)} ~ {_fmt(time_till)}'


def resolve_traffic_query_window(
    *,
    hours: int | str | None = None,
    time_from: str | None = None,
    time_till: str | None = None,
) -> tuple[datetime, datetime, str]:
    """
    解析门户流量查询窗口。

    优先使用 time_from + time_till；否则按 hours 相对“当前时间”回溯。
    自定义窗口最长 7 天，与 hours 上限一致。
    """
    has_from = time_from not in (None, '')
    has_till = time_till not in (None, '')
    if has_from or has_till:
        if not (has_from and has_till):
            raise ValueError('time_from 与 time_till 需同时提供')
        start = _parse_portal_datetime(time_from, field='time_from')
        end = _parse_portal_datetime(time_till, field='time_till')
        if start >= end:
            raise ValueError('起始时间必须早于终止时间')
        span = end - start
        if span < _MIN_TRAFFIC_WINDOW:
            raise ValueError('时间范围至少 1 分钟')
        if span > _MAX_TRAFFIC_WINDOW:
            raise ValueError('时间范围最长 7 天')
        # 允许少量时钟偏差，避免“终止时间略超当前”导致查询失败
        if end > timezone.now() + timedelta(minutes=5):
            raise ValueError('终止时间不能晚于当前时间')
        return start, end, _format_period_label(start, end)

    validated_hours = validate_hours(hours)
    end = timezone.now()
    start = end - timedelta(hours=validated_hours)
    return start, end, _format_period_label(start, end, hours=validated_hours)


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _traffic_payload(
    *,
    resource,
    period: str,
    status: str,
    message: str = '',
    series: list | None = None,
    summary: dict | None = None,
    updated_at=None,
    time_from=None,
    time_till=None,
) -> dict:
    return {
        'data_source': 'zabbix',
        'status': status,
        'message': message,
        'updated_at': updated_at,
        'period': period,
        'time_from': time_from.isoformat() if time_from is not None else None,
        'time_till': time_till.isoformat() if time_till is not None else None,
        'bandwidth_limit_mbps': max(_number(getattr(resource, 'bandwidth_mbps', 0), 0), 0),
        'series': series or [],
        'summary': summary or {
            'in_total_gb': 0.0,
            'out_total_gb': 0.0,
            'peak_mbps': 0.0,
            'p95_mbps': 0.0,
            'avg_mbps': 0.0,
        },
    }


def _traffic_binding(resource_kind: str, resource):
    """只从当前资源的可见绑定中选择流量图，绝不接受客户端传入的图/Item/Binding ID。"""
    fk = _RESOURCE_BINDING_FIELDS.get(resource_kind)
    resource_id = getattr(resource, 'pk', None)
    if not fk or not resource_id:
        return None
    bindings = MonitoringGraphBinding.objects.filter(
        target_type=resource_kind,
        is_visible=True,
        **{fk: resource_id},
    ).select_related(
        'monitoring_host',
        'graph',
        'graph__monitoring_host',
        'graph__profile',
        'monitoring_item',
        'monitoring_item__monitoring_host',
    ).prefetch_related('graph__items')
    return (
        bindings.filter(panel_slot=MonitoringGraphBinding.PanelSlot.TRAFFIC)
        .order_by('display_order', 'id')
        .first()
        or bindings.exclude(panel_slot=MonitoringGraphBinding.PanelSlot.TRAFFIC)
        .order_by('display_order', 'id')
        .first()
    )


def traffic_series(
    resource_kind: str,
    resource,
    hours: int = 24,
    *,
    time_from: datetime | str | None = None,
    time_till: datetime | str | None = None,
    period: str | None = None,
) -> dict:
    """
    返回门户约定的真实 Zabbix 流量序列。

    资源对象由已做客户范围过滤的 Portal View 传入，绑定再次用 target_type + 资源主键
    精确校验，因此用户无法借此读取任意 Zabbix 图、Item 或其他客户的绑定。
    """
    if isinstance(time_from, datetime) and isinstance(time_till, datetime):
        start, end = time_from, time_till
        period_label = period or _format_period_label(start, end)
    else:
        start, end, period_label = resolve_traffic_query_window(
            hours=hours,
            time_from=time_from,
            time_till=time_till,
        )
        if period:
            period_label = period

    binding = _traffic_binding(resource_kind, resource)
    if binding is None:
        return _traffic_payload(
            resource=resource,
            period=period_label,
            status='not_configured',
            message='该资源尚未配置可见的流量监控绑定',
            time_from=start,
            time_till=end,
        )

    try:
        validate_binding_for_resource(
            binding,
            target_type=resource_kind,
            resource_id=resource.pk,
        )
        resolved = resolve_traffic_from_binding(binding)
        result = query_traffic(
            monitoring_host=resolved['monitoring_host'],
            time_from=start,
            time_till=end,
            graph=resolved.get('graph'),
            interface_name=resolved.get('interface_name') or '',
            in_item=resolved.get('in_item'),
            out_item=resolved.get('out_item'),
            persist=False,
            title=resolved.get('title') or '',
        )
    except (ValueError, ZabbixError) as exc:
        logger.warning(
            'portal traffic unavailable resource=%s:%s: %s',
            resource_kind,
            getattr(resource, 'pk', None),
            exc,
        )
        return _traffic_payload(
            resource=resource,
            period=period_label,
            status='unavailable',
            message='Zabbix 流量数据暂不可用',
            time_from=start,
            time_till=end,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            'portal traffic query failed resource=%s:%s',
            resource_kind,
            getattr(resource, 'pk', None),
        )
        return _traffic_payload(
            resource=resource,
            period=period_label,
            status='unavailable',
            message='流量数据查询失败，请稍后重试',
            time_from=start,
            time_till=end,
        )

    portal_series = []
    for point in result.get('timeseries') or []:
        time_value = point.get('time')
        if not time_value:
            continue
        portal_series.append({
            'time': time_value,
            'in_mbps': round(_number(point.get('in_bps')) / 1_000_000, 2),
            'out_mbps': round(_number(point.get('out_bps')) / 1_000_000, 2),
        })
    if not portal_series:
        return _traffic_payload(
            resource=resource,
            period=period_label,
            status='no_data',
            message='所选时间范围内暂无流量历史数据',
            time_from=start,
            time_till=end,
        )

    billing = result.get('billing') or {}
    summary = {
        'in_total_gb': round(_number(billing.get('total_in_bytes')) / (1024 ** 3), 4),
        'out_total_gb': round(_number(billing.get('total_out_bytes')) / (1024 ** 3), 4),
        'peak_mbps': round(_number(billing.get('peak_mbps')), 2),
        'p95_mbps': round(_number(billing.get('percentile_mbps')), 2),
        'avg_mbps': round(_number(billing.get('avg_mbps')), 2),
    }
    return _traffic_payload(
        resource=resource,
        period=period_label,
        status='ok',
        series=portal_series,
        summary=summary,
        updated_at=portal_series[-1]['time'],
        time_from=start,
        time_till=end,
    )


def _quality_bucket_seconds(hours: int) -> int:
    if hours <= 6:
        return 60
    if hours <= 24:
        return 300
    if hours <= 72:
        return 900
    return 3600


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _quality_summary(samples) -> dict:
    latencies = [_number(s.latency_ms) for s in samples if s.latency_ms is not None]
    losses = [_number(s.packet_loss_pct) for s in samples if s.packet_loss_pct is not None]
    jitters = [_number(s.jitter_ms) for s in samples if s.jitter_ms is not None]
    availability = [100.0 if s.ok else 0.0 for s in samples]
    return {
        'sample_count': len(samples),
        'avg_latency_ms': _mean(latencies),
        'max_latency_ms': round(max(latencies), 2) if latencies else None,
        'avg_packet_loss_pct': _mean(losses),
        'avg_jitter_ms': _mean(jitters),
        'availability_pct': _mean(availability),
    }


def _quality_rollup_summary(rollups) -> dict:
    sample_count = sum(int(item.sample_count or 0) for item in rollups)
    ok_samples = sum(int(item.ok_samples or 0) for item in rollups)

    def weighted_average(field: str):
        values = [
            (float(getattr(item, field)), int(item.sample_count or 0))
            for item in rollups
            if getattr(item, field) is not None and int(item.sample_count or 0) > 0
        ]
        total_weight = sum(weight for _value, weight in values)
        if not total_weight:
            return None
        return round(sum(value * weight for value, weight in values) / total_weight, 2)

    max_latency = [
        float(item.latency_max_ms)
        for item in rollups
        if item.latency_max_ms is not None
    ]
    return {
        'sample_count': sample_count,
        'avg_latency_ms': weighted_average('latency_avg_ms'),
        'max_latency_ms': round(max(max_latency), 2) if max_latency else None,
        'avg_packet_loss_pct': weighted_average('packet_loss_avg_pct'),
        'avg_jitter_ms': weighted_average('jitter_avg_ms'),
        'availability_pct': round(100 * ok_samples / sample_count, 2) if sample_count else None,
    }


def _quality_rollup_series(rollups) -> list[dict]:
    return [{
        'time': item.bucket_ts.isoformat(),
        'latency_ms': round(_number(item.latency_avg_ms), 2)
        if item.latency_avg_ms is not None else None,
        'packet_loss_pct': round(_number(item.packet_loss_avg_pct), 2)
        if item.packet_loss_avg_pct is not None else None,
        'jitter_ms': round(_number(item.jitter_avg_ms), 2)
        if item.jitter_avg_ms is not None else None,
        'availability_pct': round(_number(item.availability_pct), 2)
        if item.availability_pct is not None else None,
    } for item in rollups]


def _quality_payload(
    *,
    edge,
    hours: int,
    status: str,
    message: str = '',
    series: list | None = None,
    current: dict | None = None,
    summary: dict | None = None,
    updated_at=None,
) -> dict:
    return {
        'data_source': 'vpop_telemetry',
        'status': status,
        'message': message,
        'updated_at': updated_at,
        'period': f'{hours}h',
        'series': series or [],
        'current': current,
        'summary': summary or _quality_summary([]),
    }


def _parse_node_time(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _chart_points(chart) -> list:
    if not isinstance(chart, dict):
        return []
    points = chart.get('points')
    if not isinstance(points, list):
        return []
    return [point for point in points if isinstance(point, dict)]


def _attach_node_charts(payload: dict, node_data: dict) -> dict:
    """把节点监控图表的延迟曲线和接口流量挂到门户品质结果上。"""
    for key in ('latency_chart', 'traffic_chart'):
        chart = node_data.get(key)
        if not isinstance(chart, dict):
            continue
        payload[key] = {**chart, 'points': _chart_points(chart)}
    if node_data.get('node_name'):
        payload['node_name'] = node_data.get('node_name')
    if node_data.get('interface_name') and not payload.get('interface_name'):
        payload['interface_name'] = node_data.get('interface_name')
    return payload


def _charts_have_points(node_data: dict) -> bool:
    return any(_chart_points(node_data.get(key)) for key in ('latency_chart', 'traffic_chart'))


def _latest_chart_time(node_data: dict):
    latest = None
    for key in ('latency_chart', 'traffic_chart'):
        points = _chart_points(node_data.get(key))
        if not points:
            continue
        stamp = points[-1].get('bucket') or points[-1].get('time')
        if stamp:
            latest = stamp
    return latest


def _chart_bucket_for_hours(hours: int) -> str:
    if hours <= 6:
        return 'minute'
    if hours <= 48:
        return 'hour'
    return 'day'


def _service_customer_code(edge) -> str:
    customer = getattr(edge, 'customer', None)
    return str(getattr(customer, 'customer_no', '') or '').strip()


def _monitor_target_score(item: dict, edge, interface_name: str) -> int:
    """监控图表里的目标是否属于这条边缘加速。接口名、中心下发名称或边缘编号命中即可。"""
    name = str(item.get('name') or '')
    source = str(item.get('source_interface') or '').strip()
    remark = str(item.get('remark') or '')
    customer_code = str(item.get('customer') or item.get('customer_code') or '')
    score = 0
    if name == f'isp-edge-quality-{edge.pk}':
        score += 100
    if item.get('central_edge_id') == edge.pk:
        score += 80
    if interface_name and source == interface_name:
        score += 50
    edge_no = str(getattr(edge, 'edge_no', '') or '')
    if edge_no and edge_no in remark:
        score += 20
    expected_customer = _service_customer_code(edge)
    if expected_customer and customer_code == expected_customer and score:
        score += 5
    return score


def _select_service_target(targets, edge, interface_name: str):
    best = None
    best_score = 0
    for item in targets or []:
        if not isinstance(item, dict):
            continue
        score = _monitor_target_score(item, edge, interface_name)
        if score >= 50 and score > best_score:
            best = item
            best_score = score
    return best


def _fill_monitor_charts(client, edge, hours: int, data: dict, interface_name: str) -> dict:
    """按 vpopmanager 运维监控图表的接口，读取属于这条服务的延迟曲线和接口流量。"""
    need_latency = not _chart_points(data.get('latency_chart'))
    need_traffic = not _chart_points(data.get('traffic_chart'))
    if not need_latency and not need_traffic:
        return data
    from resource_manager.pemanager_client import PemanagerError

    bucket = _chart_bucket_for_hours(hours)
    since = (timezone.now() - timedelta(hours=hours)).isoformat()
    target_name = data.get('target_name') or ''
    target_address = data.get('target_address') or ''
    interface_name = (data.get('interface_name') or interface_name or '').strip()
    target = None
    try:
        targets = client.monitor_targets() or []
    except PemanagerError as exc:
        logger.warning('读取节点监控目标失败 edge=%s: %s', getattr(edge, 'pk', None), exc)
        targets = []
    target = _select_service_target(targets, edge, interface_name)
    if target:
        target_name = target.get('name') or target_name
        target_address = target.get('address') or target_address
        if not interface_name:
            interface_name = str(target.get('source_interface') or '').strip()

    if need_latency:
        chart = {
            'bucket': bucket,
            'source': '',
            'target_name': target_name,
            'target_address': target_address,
            'points': [],
        }
        target_id = target.get('id') if target else None
        if target_id:
            try:
                series = client.monitor_target_series(target_id, bucket=bucket, since=since) or {}
            except PemanagerError as exc:
                logger.warning('读取节点延迟图表失败 edge=%s: %s', getattr(edge, 'pk', None), exc)
                series = {}
            if isinstance(series, dict):
                chart['bucket'] = series.get('bucket') or bucket
                chart['source'] = series.get('source') or ''
                chart['points'] = series.get('points') if isinstance(series.get('points'), list) else []
        data['latency_chart'] = chart

    if need_traffic:
        chart = {
            'bucket': bucket,
            'source': '',
            'interface_name': interface_name,
            'points': [],
        }
        if interface_name:
            try:
                series = client.interface_traffic_series(interface_name, bucket=bucket, since=since) or {}
            except PemanagerError as exc:
                logger.warning('读取节点接口流量失败 edge=%s: %s', getattr(edge, 'pk', None), exc)
                series = {}
            if isinstance(series, dict):
                chart['bucket'] = series.get('bucket') or bucket
                chart['source'] = series.get('source') or ''
                chart['interface_name'] = (
                    series.get('interface_name') or series.get('interface') or interface_name
                )
                chart['points'] = series.get('points') if isinstance(series.get('points'), list) else []
        data['traffic_chart'] = chart
    elif isinstance(data.get('traffic_chart'), dict):
        data['traffic_chart']['interface_name'] = (
            data['traffic_chart'].get('interface_name')
            or data['traffic_chart'].get('interface')
            or interface_name
        )
    return data


def _node_edge_quality(edge, hours: int):
    """向所属 POP 的 vpopmanager 读取品质采样、监控图表和对应接口流量。节点不可达时返回 None。"""
    pop = getattr(edge, 'pop_point', None)
    if pop is None:
        return None
    from resource_manager.pemanager_client import (
        PemanagerClient,
        PemanagerError,
        pop_has_api_config,
    )
    if not pop_has_api_config(pop):
        return None
    interface_name = (getattr(edge, 'node_ifname', '') or '').strip()
    try:
        client, _user = PemanagerClient.connect_pop(pop, timeout=8)
    except PemanagerError as exc:
        logger.warning('连接节点 API 失败 edge=%s: %s', getattr(edge, 'pk', None), exc)
        return None
    data = {
        'status': 'no_data',
        'points': [],
        'interface_name': interface_name,
        'node_name': getattr(pop, 'name', '') or '',
    }
    try:
        raw = client.edge_quality_series(edge.pk, hours=hours, interface_name=interface_name)
    except PemanagerError as exc:
        logger.warning('读取节点品质采样失败 edge=%s: %s', getattr(edge, 'pk', None), exc)
        raw = None
    if isinstance(raw, dict):
        for key in ('status', 'points', 'target_name', 'target_address', 'interface_name', 'updated_at'):
            if raw.get(key) not in (None, ''):
                data[key] = raw.get(key)
        for key in ('latency_chart', 'traffic_chart'):
            chart = raw.get(key)
            if _chart_points(chart):
                data[key] = chart
    filled = _fill_monitor_charts(client, edge, hours, data, interface_name)
    filled['node_name'] = data.get('node_name') or ''
    return filled


def _quality_from_node_points(edge, hours: int, node_data: dict) -> dict | None:
    status = str(node_data.get('status') or '')
    raw_points = node_data.get('points') or []
    if status == 'not_configured' or not raw_points:
        if status == 'not_configured':
            return _quality_payload(
                edge=edge,
                hours=hours,
                status='not_configured',
                message='节点品质监控里还没有这条边缘加速',
            )
        return None

    now = timezone.now()
    range_start = now - timedelta(hours=hours)
    parsed = []
    for point in raw_points:
        if not isinstance(point, dict):
            continue
        sampled_at = _parse_node_time(point.get('time'))
        if sampled_at is None or sampled_at < range_start or sampled_at > now + timedelta(minutes=5):
            continue
        parsed.append((sampled_at, point))
    if not parsed:
        return None
    parsed.sort(key=lambda item: item[0])

    bucket_seconds = _quality_bucket_seconds(hours)
    buckets = defaultdict(list)
    for sampled_at, point in parsed:
        bucket = int(sampled_at.timestamp() // bucket_seconds * bucket_seconds)
        buckets[bucket].append((sampled_at, point))

    series = []
    flat = []
    for bucket in sorted(buckets):
        items = buckets[bucket]
        latencies = [_number(point.get('latency_ms')) for _ts, point in items if point.get('latency_ms') is not None]
        losses = [_number(point.get('packet_loss_pct')) for _ts, point in items if point.get('packet_loss_pct') is not None]
        jitters = [_number(point.get('jitter_ms')) for _ts, point in items if point.get('jitter_ms') is not None]
        availability = [
            100.0 if point.get('ok') else 0.0
            for _ts, point in items
        ]
        series.append({
            'time': datetime.fromtimestamp(bucket, tz=dt_timezone.utc).isoformat(),
            'latency_ms': _mean(latencies),
            'packet_loss_pct': _mean(losses),
            'jitter_ms': _mean(jitters),
            'availability_pct': _mean(availability),
        })
        flat.extend(point for _ts, point in items)

    latest_at, latest = parsed[-1]
    current = {
        'time': latest_at.isoformat(),
        'latency_ms': round(_number(latest.get('latency_ms')), 2) if latest.get('latency_ms') is not None else None,
        'packet_loss_pct': (
            round(_number(latest.get('packet_loss_pct')), 2)
            if latest.get('packet_loss_pct') is not None else None
        ),
        'jitter_ms': round(_number(latest.get('jitter_ms')), 2) if latest.get('jitter_ms') is not None else None,
        'availability_pct': 100.0 if latest.get('ok') else 0.0,
    }
    summary = {
        'sample_count': len(flat),
        'avg_latency_ms': _mean([
            _number(point.get('latency_ms')) for point in flat if point.get('latency_ms') is not None
        ]),
        'max_latency_ms': round(max(
            _number(point.get('latency_ms')) for point in flat if point.get('latency_ms') is not None
        ), 2) if any(point.get('latency_ms') is not None for point in flat) else None,
        'avg_packet_loss_pct': _mean([
            _number(point.get('packet_loss_pct')) for point in flat if point.get('packet_loss_pct') is not None
        ]),
        'avg_jitter_ms': _mean([
            _number(point.get('jitter_ms')) for point in flat if point.get('jitter_ms') is not None
        ]),
        'availability_pct': _mean([100.0 if point.get('ok') else 0.0 for point in flat]),
    }
    stale = latest_at < now - timedelta(seconds=3 * 60)
    payload = _quality_payload(
        edge=edge,
        hours=hours,
        status='stale' if stale else 'ok',
        message='最新节点品质样本已超过 3 分钟' if stale else '',
        series=series,
        current=current,
        summary=summary,
        updated_at=latest_at.isoformat(),
    )
    payload['data_source'] = 'vpop_node'
    return payload


def quality_series(edge, hours: int = 24) -> dict:
    """门户品质监控：优先读所属节点 vpopmanager 的品质采样，没有再回退到已上报的中心样本。"""
    hours = validate_hours(hours)
    node_data = _node_edge_quality(edge, hours)
    if node_data:
        node_payload = _quality_from_node_points(edge, hours, node_data)
        if node_payload and node_payload.get('summary', {}).get('sample_count'):
            return _attach_node_charts(node_payload, node_data)
        if _charts_have_points(node_data):
            base = node_payload or _quality_payload(
                edge=edge,
                hours=hours,
                status='ok',
                message='',
                updated_at=_latest_chart_time(node_data),
            )
            if not (base.get('summary') or {}).get('sample_count'):
                base['status'] = 'ok'
                base['message'] = ''
                base['data_source'] = 'vpop_node'
                if not base.get('updated_at'):
                    base['updated_at'] = _latest_chart_time(node_data)
            return _attach_node_charts(base, node_data)
        if node_payload and node_payload.get('status') == 'not_configured':
            local = _local_quality_series(edge, hours)
            if local.get('status') == 'no_data' and not local.get('current'):
                return _attach_node_charts(node_payload, node_data)
            return _attach_node_charts(local, node_data)

    if not (getattr(edge, 'node_ifname', '') or '').strip():
        return _quality_payload(
            edge=edge,
            hours=hours,
            status='not_configured',
            message='该边缘节点尚未下发节点接口，未配置品质遥测',
        )
    return _local_quality_series(edge, hours)


def _local_quality_series(edge, hours: int) -> dict:
    """中心库中由节点上报的品质样本。"""
    now = timezone.now()
    all_samples = EdgeInterfaceQualitySample.objects.filter(edge=edge)
    latest = all_samples.order_by('-sampled_at', '-id').first()
    if latest is None:
        return _quality_payload(
            edge=edge,
            hours=hours,
            status='no_data',
            message='尚未收到节点品质采样',
        )

    range_start = now - timedelta(hours=hours)
    rollups = []
    if hours > 24:
        # 7 天门户曲线优先读取中心小时汇总，未运行汇总任务时仍安全回退到原始样本。
        rollups = list(
            EdgeInterfaceQualityRollup.objects.filter(
                edge=edge,
                bucket_kind=EdgeInterfaceQualityRollup.BucketKind.HOUR,
                bucket_ts__gte=range_start,
                bucket_ts__lte=now,
            ).order_by('bucket_ts', 'id'),
        )

    samples = []
    rollup_covers_range = bool(rollups) and (
        rollups[0].bucket_ts <= range_start + timedelta(hours=1)
    )
    if rollup_covers_range:
        series = _quality_rollup_series(rollups)
        summary = _quality_rollup_summary(rollups)
        has_history = bool(rollups)
    else:
        samples = list(
            all_samples.filter(
                sampled_at__gte=range_start,
                sampled_at__lte=now,
            ).order_by('sampled_at', 'id'),
        )
        bucket_seconds = _quality_bucket_seconds(hours)
        buckets = defaultdict(list)
        for sample in samples:
            bucket = int(sample.sampled_at.timestamp() // bucket_seconds * bucket_seconds)
            buckets[bucket].append(sample)

        series = []
        for bucket in sorted(buckets):
            bucket_samples = buckets[bucket]
            bucket_summary = _quality_summary(bucket_samples)
            series.append({
                'time': datetime.fromtimestamp(bucket, tz=dt_timezone.utc).isoformat(),
                'latency_ms': bucket_summary['avg_latency_ms'],
                'packet_loss_pct': bucket_summary['avg_packet_loss_pct'],
                'jitter_ms': bucket_summary['avg_jitter_ms'],
                'availability_pct': bucket_summary['availability_pct'],
            })
        summary = _quality_summary(samples)
        has_history = bool(samples)

    current = {
        'time': latest.sampled_at.isoformat(),
        'latency_ms': round(_number(latest.latency_ms), 2) if latest.latency_ms is not None else None,
        'packet_loss_pct': (
            round(_number(latest.packet_loss_pct), 2)
            if latest.packet_loss_pct is not None else None
        ),
        'jitter_ms': round(_number(latest.jitter_ms), 2) if latest.jitter_ms is not None else None,
        'availability_pct': 100.0 if latest.ok else 0.0,
    }
    stale = latest.sampled_at < now - timedelta(seconds=3 * 60)
    if stale:
        status = 'stale'
        message = '最新节点品质样本已超过 3 分钟'
    elif not has_history:
        status = 'no_data'
        message = '所选时间范围内暂无品质历史数据'
    else:
        status = 'ok'
        message = ''
    return _quality_payload(
        edge=edge,
        hours=hours,
        status=status,
        message=message,
        series=series,
        current=current,
        summary=summary,
        updated_at=latest.sampled_at.isoformat(),
    )
