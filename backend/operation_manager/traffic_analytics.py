"""
接口流量时序截取、95/99 峰值计费计算。
"""

from __future__ import annotations

import fnmatch
import math
import re
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.utils import timezone

from .models import (
    MonitoringGraph,
    MonitoringGraphProfile,
    MonitoringHost,
    MonitoringItem,
    MonitoringTrafficReport,
    infer_interface_name,
    infer_traffic_direction,
)
from .zabbix_client import ZabbixClient, ZabbixError, get_default_zabbix_server


def _to_unix(dt: datetime) -> int:
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return int(dt.timestamp())


def _from_unix(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=dt_timezone.utc)


def unit_convert_bps(value_bps: float, unit: str) -> float:
    div = {
        'bps': 1,
        'Kbps': 1_000,
        'Mbps': 1_000_000,
        'Gbps': 1_000_000_000,
    }.get(unit, 1_000_000)
    return round2(value_bps / div)


def round2(value: float | int | None) -> float | None:
    """统一保留两位小数"""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _units_lower(item: MonitoringItem) -> str:
    return (item.units or '').strip().lower()


def _item_has_change_per_second(item: MonitoringItem) -> bool:
    """Zabbix 预处理「每秒变化」后，history 存的是速率而非累计计数器"""
    raw = item.zabbix_raw or {}
    for step in raw.get('preprocessing') or []:
        t = str(step.get('type', ''))
        if t in ('10', 'change_per_second'):
            return True
    return False


def _item_preprocessed_as_bps(item: MonitoringItem) -> bool:
    """预处理链含「每秒变化 + ×8」时，history 值已是 bit/s"""
    raw = item.zabbix_raw or {}
    has_cps = has_mul8 = False
    for step in raw.get('preprocessing') or []:
        t = str(step.get('type', ''))
        params = str(step.get('params') or '').strip()
        if t in ('10', 'change_per_second'):
            has_cps = True
        if t in ('1', 'multiplier') and params == '8':
            has_mul8 = True
    if has_cps and has_mul8:
        return True
    if has_cps:
        return _units_lower(item) in ('bps', 'bit/s', 'bits/s', 'bits/sec', '')
    return False


def item_value_mode(item: MonitoringItem) -> str:
    """
    判断监控项 history 值的语义：
      - bps: 已是 bit/s（含 Zabbix 预处理后的 net.if.*）
      - bytes_per_sec: 字节/秒，需 *8 转 bps
      - byte_counter: 原始字节计数器，需差分换算
      - raw: 普通指标（CPU/内存/ICMP 等）
    """
    u = _units_lower(item)
    key = (item.item_key or '').lower()

    if u in ('bps', 'bit/s', 'bits/s', 'bits/sec'):
        return 'bps'
    if u in ('b/s', 'bytes/s', 'bytes/sec'):
        return 'bytes_per_sec'

    if _item_preprocessed_as_bps(item):
        return 'bps'
    if _item_has_change_per_second(item):
        if 'octets' in key or 'net.if.' in key:
            return 'bytes_per_sec'
        return 'raw'

    if str(item.value_type) == MonitoringItem.ValueType.UNSIGNED:
        if 'octets' in key or ('net.if.' in key and item.traffic_direction in ('in', 'out', 'unknown')):
            return 'byte_counter'
        if u in ('b', 'byte', 'bytes'):
            return 'byte_counter'

    if item.traffic_direction in ('in', 'out') and str(item.value_type) == MonitoringItem.ValueType.FLOAT:
        return 'bps'

    return 'raw'


def _history_to_bps_points(raw: list[dict], mode: str) -> list[dict]:
    """将 history/trend 原始点转为 bps 序列（clock 升序）"""
    rows = sorted(raw, key=lambda p: int(p.get('clock') or 0))
    if mode == 'byte_counter':
        return counters_to_bps_series(rows)

    out: list[dict] = []
    for p in rows:
        try:
            clock = int(p['clock'])
            val = float(p.get('value_avg', p.get('value', 0)))
        except (KeyError, TypeError, ValueError):
            continue
        if mode == 'bytes_per_sec':
            bps = val * 8
        elif mode == 'bps':
            bps = val
        else:
            continue
        out.append({'clock': clock, 'bps': round2(bps) or 0.0})
    return out


def counters_to_bps_series(points: list[dict]) -> list[dict]:
    """
    将 Zabbix history uint 原始字节计数器转为 bps 序列。
    points: [{clock, value}, ...] 按 clock 升序
    """
    out: list[dict] = []
    prev = None
    # 32/64 位 SNMP 计数器回绕上限（HC=64bit, legacy=32bit）
    wrap_limits = (2 ** 64 - 1, 2 ** 32 - 1, 2 ** 31 - 1)

    for p in sorted(points, key=lambda x: int(x.get('clock') or 0)):
        try:
            clock = int(p['clock'])
            val = float(p['value'])
        except (KeyError, TypeError, ValueError):
            continue
        if prev is not None:
            dt = clock - prev['clock']
            if dt <= 0:
                prev = {'clock': clock, 'value': val}
                continue
            delta = val - prev['value']
            if delta < 0:
                for limit in wrap_limits:
                    if prev['value'] <= limit:
                        delta = (limit - prev['value']) + val + 1
                        if delta >= 0:
                            break
                if delta < 0:
                    delta = val
            bps = delta * 8 / dt
            out.append({'clock': clock, 'bps': round2(bps) or 0.0, 'bytes_delta': delta})
        prev = {'clock': clock, 'value': val}
    return out


def percentile_value(values: list[float], n: int = 95) -> float | None:
    """升序排列后取第 N 分位（ISP 95 计费常用算法）"""
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, math.ceil(n / 100.0 * len(sorted_vals)) - 1))
    return round2(sorted_vals[idx])


def align_series(
    in_series: list[dict],
    out_series: list[dict],
    *,
    align_sec: int = 300,
) -> list[dict]:
    """按时间桶对齐入/出 bps"""
    buckets: dict[int, dict] = {}

    def _bucket(ts: int) -> int:
        return (ts // align_sec) * align_sec

    for p in in_series:
        b = _bucket(int(p['clock']))
        buckets.setdefault(b, {'clock': b, 'in_bps': 0.0, 'out_bps': 0.0})
        buckets[b]['in_bps'] = max(buckets[b]['in_bps'], float(p.get('bps', 0)))

    for p in out_series:
        b = _bucket(int(p['clock']))
        buckets.setdefault(b, {'clock': b, 'in_bps': 0.0, 'out_bps': 0.0})
        buckets[b]['out_bps'] = max(buckets[b]['out_bps'], float(p.get('bps', 0)))

    rows = sorted(buckets.values(), key=lambda x: x['clock'])
    for r in rows:
        r['in_bps'] = round2(r['in_bps']) or 0.0
        r['out_bps'] = round2(r['out_bps']) or 0.0
        r['max_bps'] = round2(max(r['in_bps'], r['out_bps'])) or 0.0
        r['sum_bps'] = round2(r['in_bps'] + r['out_bps']) or 0.0
        r['time'] = _from_unix(r['clock']).isoformat()
    return rows


def billing_samples(
    aligned: list[dict],
    *,
    billing_mode: str,
) -> list[float]:
    mode = billing_mode or MonitoringGraphProfile.BillingMode.MAX_IN_OUT
    if mode == MonitoringGraphProfile.BillingMode.IN_ONLY:
        return [float(r['in_bps']) for r in aligned]
    if mode == MonitoringGraphProfile.BillingMode.OUT_ONLY:
        return [float(r['out_bps']) for r in aligned]
    if mode == MonitoringGraphProfile.BillingMode.SUM_IN_OUT:
        return [float(r['sum_bps']) for r in aligned]
    return [float(r['max_bps']) for r in aligned]


def compute_traffic_stats(
    aligned: list[dict],
    *,
    percentile_n: int = 95,
    billing_mode: str = MonitoringGraphProfile.BillingMode.MAX_IN_OUT,
) -> dict[str, Any]:
    samples = billing_samples(aligned, billing_mode=billing_mode)
    in_vals = [float(r['in_bps']) for r in aligned]
    out_vals = [float(r['out_bps']) for r in aligned]
    max_vals = [float(r['max_bps']) for r in aligned]

    p95 = percentile_value(samples, percentile_n)
    p95_in = percentile_value(in_vals, percentile_n)
    p95_out = percentile_value(out_vals, percentile_n)
    return {
        'sample_count': len(aligned),
        'peak_bps': round2(max(max_vals) if max_vals else None),
        'avg_bps': round2(sum(samples) / len(samples)) if samples else None,
        'percentile_n': percentile_n,
        'percentile_bps': round2(p95),
        'percentile_in_bps': round2(p95_in),
        'percentile_out_bps': round2(p95_out),
        'peak_in_bps': round2(max(in_vals) if in_vals else None),
        'peak_out_bps': round2(max(out_vals) if out_vals else None),
        'avg_in_bps': round2(sum(in_vals) / len(in_vals)) if in_vals else None,
        'avg_out_bps': round2(sum(out_vals) / len(out_vals)) if out_vals else None,
        'total_in_bytes': round2(sum((r['in_bps'] / 8) * 300 for r in aligned)),
        'total_out_bytes': round2(sum((r['out_bps'] / 8) * 300 for r in aligned)),
    }


def _match_patterns(text: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    for pat in patterns:
        if fnmatch.fnmatch(text.lower(), pat.lower()) or pat.lower() in text.lower():
            return True
    return False


def _item_matches_interface(item: MonitoringItem, interface_name: str) -> bool:
    """接口名匹配：字段、Item Key、名称或解析出的接口名。"""
    if not interface_name:
        return True
    iface = interface_name.strip().lower()
    if not iface:
        return True
    if (item.interface_name or '').strip().lower() == iface:
        return True
    inferred = infer_interface_name(item.item_key, item.name)
    if inferred and inferred.lower() == iface:
        return True
    blob = f'{item.item_key} {item.name}'.lower()
    return iface in blob


def _classify_traffic_item(item: MonitoringItem) -> str:
    direction = (item.traffic_direction or '').strip()
    if direction in ('in', 'out', 'both'):
        return direction
    return infer_traffic_direction(item.item_key, item.name)


def infer_interface_from_graph(graph: MonitoringGraph) -> str:
    """从图形配置、名称或关联监控项解析接口名。"""
    if graph.interface_name:
        return graph.interface_name.strip()
    name = graph.effective_title or graph.name or ''
    m = re.search(r'Interface\s+([^():\[]+)', name, re.I)
    if m:
        return m.group(1).strip()
    for item in _resolve_graph_items(graph)[:6]:
        iface = (item.interface_name or '').strip() or infer_interface_name(item.item_key, item.name)
        if iface:
            return iface
    return ''


def resolve_traffic_items_from_graph(
    graph: MonitoringGraph,
    *,
    interface_name: str = '',
) -> tuple[MonitoringItem | None, MonitoringItem | None]:
    """
    从图形绑定的 Zabbix item（graph.items / zabbix_itemids）解析入/出站监控项。
    优先使用图形内 item，再回退到主机全量 item + 接口名匹配。
    """
    graph_items = list(_resolve_graph_items(graph))
    in_item = out_item = None

    for item in graph_items:
        direction = _classify_traffic_item(item)
        if direction in ('in', 'both') and not in_item:
            in_item = item
        if direction in ('out', 'both') and not out_item:
            out_item = item

    if graph_items and (not in_item or not out_item):
        for item in graph_items:
            text = f'{item.item_key} {item.name}'.lower()
            if not in_item and any(k in text for k in (
                'if.in', 'ifin', 'incoming', 'ingress', '入站', 'received', 'download',
            )):
                in_item = item
            if not out_item and any(k in text for k in (
                'if.out', 'ifout', 'outgoing', 'egress', '出站', 'sent', 'upload',
            )):
                out_item = item

    if len(graph_items) == 2 and (not in_item or not out_item):
        a, b = graph_items[0], graph_items[1]
        da, db = _classify_traffic_item(a), _classify_traffic_item(b)
        if da == 'in' and db == 'out':
            in_item, out_item = a, b
        elif da == 'out' and db == 'in':
            in_item, out_item = b, a
        elif not in_item and not out_item:
            in_item, out_item = a, b

    iface = interface_name or graph.interface_name or infer_interface_from_graph(graph)
    if not in_item or not out_item:
        ri, ro = resolve_traffic_items(
            graph.monitoring_host,
            interface_name=iface,
            in_item_id=in_item.pk if in_item else None,
            out_item_id=out_item.pk if out_item else None,
        )
        in_item = in_item or ri
        out_item = out_item or ro
    return in_item, out_item


def resolve_traffic_items(
    host: MonitoringHost,
    *,
    interface_name: str = '',
    profile: MonitoringGraphProfile | None = None,
    in_item_id: int | None = None,
    out_item_id: int | None = None,
) -> tuple[MonitoringItem | None, MonitoringItem | None]:
    if in_item_id:
        in_item = host.items.filter(pk=in_item_id).first()
    else:
        in_item = None
    if out_item_id:
        out_item = host.items.filter(pk=out_item_id).first()
    else:
        out_item = None
    if in_item and out_item:
        return in_item, out_item

    iface_pattern = None
    if not interface_name and profile and profile.interface_name_pattern:
        iface_pattern = re.compile(profile.interface_name_pattern, re.I)

    in_patterns = (profile.item_key_in_patterns if profile else []) or [
        'net.if.in*', '*ifHCInOctets*', '*ifInOctets*',
    ]
    out_patterns = (profile.item_key_out_patterns if profile else []) or [
        'net.if.out*', '*ifHCOutOctets*', '*ifOutOctets*',
    ]

    candidates = host.items.filter(is_enabled=True)
    for item in candidates:
        if interface_name and not _item_matches_interface(item, interface_name):
            continue
        if iface_pattern:
            blob = f'{item.interface_name} {item.item_key} {item.name}'
            if not iface_pattern.search(blob):
                continue
        direction = item.traffic_direction or infer_traffic_direction(item.item_key, item.name)
        if not in_item and (
            direction == 'in' or _match_patterns(item.item_key, in_patterns)
        ):
            in_item = item
        if not out_item and (
            direction == 'out' or _match_patterns(item.item_key, out_patterns)
        ):
            out_item = item
    return in_item, out_item


def _history_type_for_item(item: MonitoringItem) -> int:
    vt = str(item.value_type or '0')
    return 3 if vt == MonitoringItem.ValueType.UNSIGNED else 0


def _use_trend(time_from: datetime, time_till: datetime) -> bool:
    """超过 48 小时优先用 trend（Zabbix history 保留期有限）"""
    return (time_till - time_from).total_seconds() >= 48 * 3600


def _fetch_zabbix_raw_series(
    client: ZabbixClient,
    item: MonitoringItem,
    *,
    time_from: datetime,
    time_till: datetime,
) -> list[dict]:
    """从 Zabbix 拉取 history 或 trend 原始序列（已按 clock 升序）"""
    t_from = _to_unix(time_from)
    t_till = _to_unix(time_till)
    itemid = str(item.zabbix_itemid)

    if _use_trend(time_from, time_till):
        trends = client.trend_get([itemid], time_from=t_from, time_till=t_till)
        if trends:
            return sorted(trends, key=lambda p: int(p.get('clock') or 0))

    history_type = _history_type_for_item(item)
    raw = client.history_get(
        [itemid],
        time_from=t_from,
        time_till=t_till,
        history_type=history_type,
    )
    return sorted(raw or [], key=lambda p: int(p.get('clock') or 0))


def _format_raw_value(item: MonitoringItem, val: float) -> tuple[float, str]:
    """将 Zabbix 原始值转为展示值及单位（保留两位小数）"""
    u = (item.units or '').strip()
    ul = u.lower()
    mode = item_value_mode(item)

    if mode in ('bps', 'bytes_per_sec', 'byte_counter'):
        return round2(val) or 0.0, u or 'bps'

    if ul in ('b', 'byte', 'bytes') and val >= 1024 ** 3:
        return round2(val / (1024 ** 3)) or 0.0, 'GiB'
    if ul in ('b', 'byte', 'bytes') and val >= 1024 ** 2:
        return round2(val / (1024 ** 2)) or 0.0, 'MiB'
    if ul in ('b', 'byte', 'bytes') and val >= 1024:
        return round2(val / 1024) or 0.0, 'KiB'

    return round2(val) or 0.0, u


def fetch_item_history_bps(
    client: ZabbixClient,
    item: MonitoringItem,
    *,
    time_from: datetime,
    time_till: datetime,
    convert_counter: bool = True,
) -> list[dict]:
    """
    拉取监控项并转为 bps 序列。
    自动识别：Zabbix 预处理速率 / 原始字节计数器 / 已是 bps 的 float 项。
    """
    mode = item_value_mode(item)
    if not convert_counter and mode == 'byte_counter':
        mode = 'bps'

    raw = _fetch_zabbix_raw_series(client, item, time_from=time_from, time_till=time_till)
    if mode == 'byte_counter':
        # trend 的 value_avg 对计数器无意义，回退 history
        if _use_trend(time_from, time_till) and raw and 'value_avg' in (raw[0] or {}):
            raw = sorted(
                client.history_get(
                    [str(item.zabbix_itemid)],
                    time_from=_to_unix(time_from),
                    time_till=_to_unix(time_till),
                    history_type=3,
                ) or [],
                key=lambda p: int(p.get('clock') or 0),
            )
        return counters_to_bps_series(raw)

    return _history_to_bps_points(raw, mode)


def fetch_item_history_values(
    client: ZabbixClient,
    item: MonitoringItem,
    *,
    time_from: datetime,
    time_till: datetime,
) -> tuple[list[dict], str]:
    """拉取监控项历史值（CPU/内存/ICMP 等），自动单位缩放并保留两位小数"""
    raw = _fetch_zabbix_raw_series(client, item, time_from=time_from, time_till=time_till)
    points: list[dict] = []
    display_unit = (item.units or '').strip()

    for p in raw:
        try:
            clock = int(p['clock'])
            val = float(p.get('value_avg', p.get('value', 0)))
        except (KeyError, TypeError, ValueError):
            continue
        disp_val, disp_unit = _format_raw_value(item, val)
        if disp_unit:
            display_unit = disp_unit
        points.append({
            'clock': clock,
            'value': disp_val,
            'time': _from_unix(clock).isoformat(),
        })
    return points, display_unit


def _resolve_graph_items(graph: MonitoringGraph):
    """获取图形关联的本地监控项"""
    host = graph.monitoring_host
    items = graph.items.all()
    if items.exists():
        return items
    if graph.zabbix_itemids:
        return host.items.filter(zabbix_itemid__in=[str(x) for x in graph.zabbix_itemids])
    return host.items.none()


def get_item_chart_data(
    item: MonitoringItem,
    *,
    time_from: datetime,
    time_till: datetime,
) -> dict[str, Any]:
    """单个监控项历史数据（供资源详情页按监控项关联展示）。"""
    host = item.monitoring_host
    server = host.zabbix_server or get_default_zabbix_server()
    if not server:
        raise ValueError('未配置 Zabbix 服务器')

    client = ZabbixClient(server)
    client.login()

    mode = item_value_mode(item)
    if mode in ('bps', 'bytes_per_sec', 'byte_counter'):
        bps_series = fetch_item_history_bps(
            client, item, time_from=time_from, time_till=time_till, convert_counter=True,
        )
        points = [
            {
                'clock': p['clock'],
                'value': round2((p.get('bps') or 0) / 1_000_000) or 0.0,
                'time': _from_unix(p['clock']).isoformat(),
            }
            for p in bps_series
        ]
        unit = 'Mbps'
    else:
        points, unit = fetch_item_history_values(
            client, item, time_from=time_from, time_till=time_till,
        )

    if not points:
        raise ValueError('所选时间范围内无历史数据')

    return {
        'chart_type': 'generic',
        'meta': {
            'item_id': item.pk,
            'title': item.name,
            'interface_name': item.interface_name,
            'time_from': time_from.isoformat(),
            'time_till': time_till.isoformat(),
        },
        'series': [{
            'name': item.name,
            'unit': unit,
            'item_id': item.pk,
            'color': '#409EFF',
            'points': points,
        }],
    }


def get_generic_graph_chart_data(
    graph: MonitoringGraph,
    *,
    time_from: datetime,
    time_till: datetime,
) -> dict[str, Any]:
    """
    Zabbix 原生系统/ICMP 等图形：按 graph 关联 item 拉 history 原始值。
    返回 chart_type=generic，供前端多序列折线图渲染。
    """
    host = graph.monitoring_host
    items = list(_resolve_graph_items(graph)[:12])
    if not items:
        raise ValueError('图形未关联监控项，请先同步监控主机')

    server = host.zabbix_server or get_default_zabbix_server()
    if not server:
        raise ValueError('未配置 Zabbix 服务器')

    client = ZabbixClient(server)
    client.login()

    palette = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399', '#9B59B6', '#1ABC9C', '#34495E']
    series_out: list[dict] = []
    for idx, item in enumerate(items):
        points, unit = fetch_item_history_values(
            client, item, time_from=time_from, time_till=time_till,
        )
        series_out.append({
            'name': item.name,
            'unit': unit,
            'item_id': item.pk,
            'color': palette[idx % len(palette)],
            'points': points,
        })

    if not any(s['points'] for s in series_out):
        raise ValueError('所选时间范围内无历史数据')

    return {
        'chart_type': 'generic',
        'meta': {
            'graph_id': graph.pk,
            'graph_kind': graph.graph_kind,
            'title': graph.effective_title,
            'time_from': time_from.isoformat(),
            'time_till': time_till.isoformat(),
        },
        'series': series_out,
    }


def query_traffic(
    *,
    monitoring_host: MonitoringHost,
    time_from: datetime,
    time_till: datetime,
    profile: MonitoringGraphProfile | None = None,
    graph: MonitoringGraph | None = None,
    interface_name: str = '',
    in_item: MonitoringItem | None = None,
    out_item: MonitoringItem | None = None,
    persist: bool = False,
    title: str = '',
    operator=None,
    pop_point=None,
    vps=None,
    edge=None,
) -> dict[str, Any]:
    """即时查询或持久化流量/95 计费报告"""
    if graph:
        profile = graph.profile or profile
        interface_name = interface_name or graph.interface_name or ''
        if graph.custom_config:
            profile_overrides = graph.custom_config
        else:
            profile_overrides = {}
    else:
        profile_overrides = {}

    if not profile:
        profile = MonitoringGraphProfile.objects.filter(
            code='traffic_p95_default', is_active=True,
        ).first()

    align_sec = (profile.sample_align_sec if profile else 300) or 300
    percentile_n = profile_overrides.get('percentile_n') or (profile.percentile_n if profile else 95)
    billing_mode = profile_overrides.get('billing_mode') or (
        profile.billing_mode if profile else MonitoringGraphProfile.BillingMode.MAX_IN_OUT
    )
    unit = profile_overrides.get('unit_display') or (
        profile.unit_display if profile else MonitoringGraphProfile.UnitDisplay.MBPS
    )
    convert_counter = profile.convert_counter_to_rate if profile else True

    iface = interface_name or (infer_interface_from_graph(graph) if graph else '')
    if graph:
        interface_name = iface
        gin, gout = resolve_traffic_items_from_graph(graph, interface_name=iface)
        in_item = in_item or gin
        out_item = out_item or gout

    in_item, out_item = resolve_traffic_items(
        monitoring_host,
        interface_name=interface_name or iface,
        profile=profile,
        in_item_id=in_item.pk if in_item else None,
        out_item_id=out_item.pk if out_item else None,
    )
    if not in_item and not out_item:
        if graph and not list(_resolve_graph_items(graph)):
            raise ValueError('图形未关联监控项，请先在监控主机上执行同步')
        raise ValueError('未找到匹配的入/出站监控项，请确认图形/监控项已同步且包含流量序列')

    server = monitoring_host.zabbix_server or get_default_zabbix_server()
    if not server:
        raise ValueError('未配置 Zabbix 服务器')

    client = ZabbixClient(server)
    client.login()

    in_series: list[dict] = []
    out_series: list[dict] = []
    if in_item:
        in_series = fetch_item_history_bps(
            client, in_item, time_from=time_from, time_till=time_till,
            convert_counter=convert_counter,
        )
    if out_item:
        out_series = fetch_item_history_bps(
            client, out_item, time_from=time_from, time_till=time_till,
            convert_counter=convert_counter,
        )

    aligned = align_series(in_series, out_series, align_sec=align_sec)
    stats = compute_traffic_stats(
        aligned, percentile_n=int(percentile_n), billing_mode=billing_mode,
    )

    result = {
        'chart_type': 'traffic',
        'meta': {
            'monitoring_host_id': monitoring_host.pk,
            'interface_name': interface_name or (in_item.interface_name if in_item else ''),
            'time_from': time_from.isoformat(),
            'time_till': time_till.isoformat(),
            'profile_code': profile.code if profile else None,
            'billing_mode': billing_mode,
            'percentile_n': percentile_n,
            'unit': unit,
            'in_item_id': in_item.pk if in_item else None,
            'out_item_id': out_item.pk if out_item else None,
            'in_item_key': in_item.item_key if in_item else None,
            'out_item_key': out_item.item_key if out_item else None,
        },
        'timeseries': aligned,
        'billing': {
            **stats,
            'percentile_mbps': unit_convert_bps(stats['percentile_bps'], 'Mbps')
            if stats.get('percentile_bps') is not None else None,
            'percentile_in_mbps': unit_convert_bps(stats['percentile_in_bps'], 'Mbps')
            if stats.get('percentile_in_bps') is not None else None,
            'percentile_out_mbps': unit_convert_bps(stats['percentile_out_bps'], 'Mbps')
            if stats.get('percentile_out_bps') is not None else None,
            'peak_mbps': unit_convert_bps(stats['peak_bps'], 'Mbps')
            if stats.get('peak_bps') is not None else None,
            'peak_in_mbps': unit_convert_bps(stats['peak_in_bps'], 'Mbps')
            if stats.get('peak_in_bps') is not None else None,
            'peak_out_mbps': unit_convert_bps(stats['peak_out_bps'], 'Mbps')
            if stats.get('peak_out_bps') is not None else None,
            'avg_mbps': unit_convert_bps(stats['avg_bps'], 'Mbps')
            if stats.get('avg_bps') is not None else None,
            'avg_in_mbps': unit_convert_bps(stats['avg_in_bps'], 'Mbps')
            if stats.get('avg_in_bps') is not None else None,
            'avg_out_mbps': unit_convert_bps(stats['avg_out_bps'], 'Mbps')
            if stats.get('avg_out_bps') is not None else None,
        },
        'series': {
            'in': [{
                'clock': p['clock'],
                'bps': round2(p.get('bps', 0)) or 0.0,
                'time': _from_unix(p['clock']).isoformat(),
            } for p in in_series],
            'out': [{
                'clock': p['clock'],
                'bps': round2(p.get('bps', 0)) or 0.0,
                'time': _from_unix(p['clock']).isoformat(),
            } for p in out_series],
        },
    }

    report = None
    if persist:
        # 计费报告必须固化业务资源归属，不能仅凭监控 Host 反推（通用 Host 可服务多个资源）。
        if vps is not None:
            report_target_type = MonitoringHost.TargetType.VPS
            report_pop = getattr(vps, 'pop_point', None)
        elif edge is not None:
            report_target_type = MonitoringHost.TargetType.EDGE
            report_pop = getattr(edge, 'pop_point', None)
        else:
            report_target_type = MonitoringHost.TargetType.POP
            report_pop = pop_point or monitoring_host.pop_point
        report = MonitoringTrafficReport.objects.create(
            title=title or f'{interface_name or "接口"} 流量 {time_from.date()} ~ {time_till.date()}',
            target_type=report_target_type,
            pop_point=report_pop,
            vps=vps,
            edge=edge,
            monitoring_host=monitoring_host,
            graph=graph,
            profile=profile,
            interface_name=interface_name or (in_item.interface_name if in_item else ''),
            in_item=in_item,
            out_item=out_item,
            time_from=time_from,
            time_till=time_till,
            sample_count=stats.get('sample_count') or 0,
            peak_bps=stats.get('peak_bps'),
            avg_bps=stats.get('avg_bps'),
            percentile_bps=stats.get('percentile_bps'),
            percentile_n=int(percentile_n),
            billing_mode=billing_mode,
            total_in_bytes=int(stats.get('total_in_bytes') or 0),
            total_out_bytes=int(stats.get('total_out_bytes') or 0),
            status=MonitoringTrafficReport.ReportStatus.DONE,
            result=result,
            created_by=operator,
            computed_at=timezone.now(),
        )
        result['report_id'] = report.pk
        result['report_no'] = report.report_no

    return result


def get_graph_chart_data(
    graph: MonitoringGraph,
    *,
    time_from: datetime,
    time_till: datetime,
) -> dict[str, Any]:
    """定制图形 / 流量计费 / Zabbix 原生图形统一取数入口"""
    # 定制图形或绑定计费 profile → 流量+95计费
    if graph.source_type == MonitoringGraph.SourceType.CUSTOM or graph.profile_id:
        return query_traffic(
            monitoring_host=graph.monitoring_host,
            time_from=time_from,
            time_till=time_till,
            profile=graph.profile,
            graph=graph,
            interface_name=graph.interface_name,
        )

    traffic_kinds = (
        MonitoringGraph.GraphKind.TRAFFIC,
        MonitoringGraph.GraphKind.BILLING,
    )
    if graph.graph_kind in traffic_kinds or graph.billing_enabled:
        in_item, out_item = resolve_traffic_items_from_graph(
            graph, interface_name=graph.interface_name,
        )
        try:
            result = query_traffic(
                monitoring_host=graph.monitoring_host,
                time_from=time_from,
                time_till=time_till,
                graph=graph,
                interface_name=graph.interface_name or infer_interface_from_graph(graph),
                in_item=in_item,
                out_item=out_item,
            )
            result['chart_type'] = 'traffic'
            return result
        except ValueError:
            if graph.graph_kind in traffic_kinds:
                raise

    # 系统负载 / ICMP / 其他 Zabbix 原生图形
    return get_generic_graph_chart_data(
        graph, time_from=time_from, time_till=time_till,
    )
