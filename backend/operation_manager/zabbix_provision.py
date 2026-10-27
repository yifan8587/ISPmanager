"""
Zabbix 主机下发与同步服务。

职责：
  - 拉取 Zabbix 主机群组 / 模板（供前端下拉）；
  - 将 MonitoringHost 配置（ICMP/SNMP 接口、群组、模板、宏）创建/更新到 Zabbix；
  - 同步 Items / Graphs 到本地镜像，并自动绑定到资源详情页面板。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.utils import timezone

from .models import (
    MonitoringGraph,
    MonitoringGraphBinding,
    MonitoringHost,
    MonitoringItem,
    infer_interface_name,
    infer_traffic_direction,
    suggested_graph_kind,
)
from .zabbix_client import ZabbixClient, ZabbixError


# Zabbix 接口类型
IFACE_AGENT = 1
IFACE_SNMP = 2


def fetch_host_groups(server, search: str | None = None) -> list[dict]:
    client = ZabbixClient(server)
    client.login()
    return [
        {'groupid': g['groupid'], 'name': g['name']}
        for g in client.hostgroup_get(search=search)
    ]


def fetch_templates(server, search: str | None = None) -> list[dict]:
    client = ZabbixClient(server)
    client.login()
    return [
        {'templateid': t['templateid'], 'name': t['name']}
        for t in client.template_get(search=search)
    ]


def _build_interfaces(host: MonitoringHost) -> list[dict]:
    """根据 ICMP/SNMP 开关构建 Zabbix 主机接口。"""
    ip = (host.monitored_ip or '').strip()
    interfaces: list[dict] = []
    if host.enable_snmp and ip:
        interfaces.append({
            'type': IFACE_SNMP,
            'main': 1,
            'useip': 1,
            'ip': ip,
            'dns': '',
            'port': str(host.snmp_port or 161),
            'details': {
                'version': int(host.snmp_version or 2),
                'bulk': 1,
                'community': host.snmp_community or '{$SNMP_COMMUNITY}',
            },
        })
    # ICMP / 通用：保证至少有一个带 IP 的接口（icmpping 依赖接口 IP）
    if ip and (host.enable_icmp or not interfaces):
        interfaces.append({
            'type': IFACE_AGENT,
            'main': 1,
            'useip': 1,
            'ip': ip,
            'dns': '',
            'port': '10050',
        })
    return interfaces


def provision_host(host: MonitoringHost) -> dict[str, Any]:
    """
    创建或更新 Zabbix 主机。返回 {'hostid', 'action': 'create'|'update'}。
    """
    server = host.zabbix_server
    if not server:
        raise ValueError('未配置 Zabbix 服务器')
    if not (host.monitored_ip or '').strip():
        raise ValueError('请填写监控 IP（ICMP/SNMP 探测地址）')
    if not host.zabbix_group_ids:
        raise ValueError('请至少选择一个 Zabbix 主机群组')

    client = ZabbixClient(server)
    client.login()

    groups = [{'groupid': str(g)} for g in host.zabbix_group_ids]
    templates = [{'templateid': str(t)} for t in (host.zabbix_template_ids or [])]
    macros = []
    if host.snmp_community:
        macros.append({'macro': '{$SNMP_COMMUNITY}', 'value': host.snmp_community})
    visible = host.visible_name or host.hostname

    # 复用已有主机：优先按 hostid，其次按 hostname 探测
    existing_id = (host.zabbix_hostid or '').strip()
    if not existing_id:
        found = client.host_get(host=host.hostname)
        if found:
            existing_id = str(found[0]['hostid'])

    if existing_id:
        params = {
            'hostid': existing_id,
            'name': visible,
            'groups': groups,
            'templates': templates,
        }
        if macros:
            params['macros'] = macros
        client.host_update(params)
        action = 'update'
        hostid = existing_id
    else:
        params = {
            'host': host.hostname,
            'name': visible,
            'groups': groups,
            'interfaces': _build_interfaces(host),
            'templates': templates,
        }
        if macros:
            params['macros'] = macros
        result = client.host_create(params)
        ids = result.get('hostids') or []
        if not ids:
            raise ZabbixError('Zabbix 未返回 hostid，主机创建失败')
        hostid = str(ids[0])
        action = 'create'

    raw = client.host_get(hostids=[hostid], select_interfaces=True)
    host.mark_synced(hostid=hostid, raw=(raw[0] if raw else {}))
    return {'hostid': hostid, 'action': action}


def _to_dt(clock) -> datetime | None:
    try:
        c = int(clock)
    except (TypeError, ValueError):
        return None
    if c <= 0:
        return None
    return datetime.fromtimestamp(c, tz=dt_timezone.utc)


def sync_items(host: MonitoringHost) -> dict[str, int]:
    """从 Zabbix 拉取 item 列表并 upsert 本地镜像。"""
    if not host.zabbix_hostid:
        raise ValueError('主机尚未在 Zabbix 创建，请先同步主机')
    client = ZabbixClient(host.zabbix_server)
    client.login()
    items = client.item_get(hostids=[host.zabbix_hostid])

    created = updated = 0
    seen_ids: list[str] = []
    for it in items:
        itemid = str(it.get('itemid'))
        seen_ids.append(itemid)
        item_key = it.get('key_') or ''
        name = it.get('name') or item_key
        direction = infer_traffic_direction(item_key, name)
        iface = infer_interface_name(item_key, name)
        defaults = {
            'item_key': item_key,
            'name': name[:512],
            'units': (it.get('units') or '')[:64],
            'value_type': str(it.get('value_type') or '0'),
            'last_value': (it.get('lastvalue') or '')[:512],
            'last_clock': _to_dt(it.get('lastclock')),
            'traffic_direction': direction,
            'interface_name': iface[:64],
            'zabbix_raw': it,
            'synced_at': timezone.now(),
        }
        obj, is_created = MonitoringItem.objects.update_or_create(
            monitoring_host=host, zabbix_itemid=itemid, defaults=defaults,
        )
        created += int(is_created)
        updated += int(not is_created)

    # 标记主流量项：每个接口的 in/out
    _mark_primary_traffic(host)
    return {'created': created, 'updated': updated, 'total': len(seen_ids)}


def _mark_primary_traffic(host: MonitoringHost):
    traffic_items = host.items.filter(traffic_direction__in=['in', 'out'])
    by_iface: dict[str, dict[str, MonitoringItem]] = {}
    for it in traffic_items:
        by_iface.setdefault(it.interface_name or '', {})[it.traffic_direction] = it
    primary_ids = []
    for _iface, dirs in by_iface.items():
        for d in ('in', 'out'):
            if d in dirs:
                primary_ids.append(dirs[d].pk)
    host.items.filter(pk__in=primary_ids).update(is_primary_traffic=True)


def _slot_for_kind(kind: str) -> str:
    return {
        MonitoringGraph.GraphKind.TRAFFIC: MonitoringGraphBinding.PanelSlot.TRAFFIC,
        MonitoringGraph.GraphKind.BILLING: MonitoringGraphBinding.PanelSlot.TRAFFIC,
        MonitoringGraph.GraphKind.SYSTEM: MonitoringGraphBinding.PanelSlot.SYSTEM,
        MonitoringGraph.GraphKind.ICMP: MonitoringGraphBinding.PanelSlot.ICMP,
    }.get(kind, MonitoringGraphBinding.PanelSlot.OVERVIEW)


def sync_graphs(host: MonitoringHost, *, auto_bind: bool = True) -> dict[str, int]:
    """从 Zabbix 拉取 graph 列表并 upsert，本地自动绑定到资源详情页。"""
    if not host.zabbix_hostid:
        raise ValueError('主机尚未在 Zabbix 创建，请先同步主机')
    client = ZabbixClient(host.zabbix_server)
    client.login()
    graphs = client.graph_get(hostids=[host.zabbix_hostid])

    created = updated = bound = 0
    for g in graphs:
        graphid = str(g.get('graphid'))
        name = g.get('name') or f'graph-{graphid}'
        gitems = g.get('gitems') or g.get('graphitems') or []
        itemids = [str(gi.get('itemid')) for gi in gitems if gi.get('itemid')]
        kind = suggested_graph_kind(name)
        defaults = {
            'source_type': MonitoringGraph.SourceType.ZABBIX,
            'name': name[:512],
            'graph_kind': kind,
            'width': int(g.get('width') or 900),
            'height': int(g.get('height') or 200),
            'zabbix_itemids': itemids,
            'billing_enabled': kind in (
                MonitoringGraph.GraphKind.TRAFFIC, MonitoringGraph.GraphKind.BILLING,
            ),
            'zabbix_raw': g,
            'synced_at': timezone.now(),
        }
        obj, is_created = MonitoringGraph.objects.update_or_create(
            monitoring_host=host, zabbix_graphid=graphid, defaults=defaults,
        )
        created += int(is_created)
        updated += int(not is_created)

        # 关联本地 item 镜像
        if itemids:
            local_items = host.items.filter(zabbix_itemid__in=itemids)
            if local_items.exists():
                obj.items.set(local_items)

        # 仅 POP 主机自动绑定到对应 POP 详情页；其余主机交由用户在资源详情页手动关联
        if auto_bind and host.host_kind == MonitoringHost.HostKind.POP and host.pop_point_id:
            _binding, b_created = MonitoringGraphBinding.objects.get_or_create(
                graph=obj,
                target_type=MonitoringHost.TargetType.POP,
                pop_point=host.pop_point,
                defaults={
                    'monitoring_host': host,
                    'panel_slot': _slot_for_kind(kind),
                    'panel_title': obj.effective_title,
                    'is_visible': True,
                },
            )
            bound += int(b_created)

    return {'created': created, 'updated': updated, 'bound': bound, 'total': len(graphs)}


def sync_host_full(host: MonitoringHost, *, push: bool = True) -> dict[str, Any]:
    """同步主机：可选下发到 Zabbix，再拉取 items / graphs。"""
    result: dict[str, Any] = {}
    if push:
        result['host'] = provision_host(host)
    result['items'] = sync_items(host)
    result['graphs'] = sync_graphs(host)
    try:
        update_host_inventory_counts(host)
    except (ValueError, ZabbixError):
        pass
    return result


def update_host_inventory_counts(host: MonitoringHost, client: ZabbixClient | None = None) -> dict[str, int]:
    """仅向 Zabbix 查询项/图数量并缓存，不拉取明细。"""
    if not host.zabbix_hostid:
        return {'item_count': 0, 'graph_count': 0}
    if client is None:
        client = ZabbixClient(host.zabbix_server)
        client.login()
    item_count = client.item_count([host.zabbix_hostid])
    graph_count = client.graph_count([host.zabbix_hostid])
    host.zabbix_item_count = item_count
    host.zabbix_graph_count = graph_count
    host.save(update_fields=['zabbix_item_count', 'zabbix_graph_count', 'updated_at'])
    return {'item_count': item_count, 'graph_count': graph_count}


def _upsert_zabbix_host_record(server, zh: dict) -> tuple[MonitoringHost, bool]:
    """将 Zabbix 主机写入本地清单（不拉取监控项/图形明细）。"""
    hostid = str(zh.get('hostid'))
    hostname = zh.get('host') or f'host-{hostid}'
    visible = zh.get('name') or hostname
    ip = ''
    for itf in (zh.get('interfaces') or []):
        if str(itf.get('main')) == '1' and itf.get('ip'):
            ip = itf['ip']
            break
    if not ip:
        for itf in (zh.get('interfaces') or []):
            if itf.get('ip'):
                ip = itf['ip']
                break

    defaults = {
        'hostname': hostname[:128],
        'visible_name': visible[:256],
        'monitored_ip': ip or None,
        'zabbix_raw': zh,
    }
    host, created = MonitoringHost.objects.update_or_create(
        zabbix_server=server, zabbix_hostid=hostid,
        defaults={
            'hostname': hostname[:128],
            'visible_name': visible[:256],
            'monitored_ip': ip or None,
            'zabbix_raw': zh,
        },
    )
    if created:
        host.host_kind = MonitoringHost.HostKind.GENERIC
        host.save(update_fields=['host_kind'])
    host.mark_synced(hostid=hostid, raw=zh)
    return host, created


def sync_server_inventory_light(server, *, operator=None) -> dict[str, Any]:
    """
    轻量同步：仅拉取 Zabbix 主机清单 + 已登记监控主机的项/图数量统计。
    监控项/图形明细由后台任务 sync_registered_hosts_items_graphs 缓慢拉取。
    """
    client = ZabbixClient(server)
    client.login()
    zbx_hosts = client.host_get(select_interfaces=True)

    host_created = host_updated = 0
    for zh in zbx_hosts:
        host, created = _upsert_zabbix_host_record(server, zh)
        host_created += int(created)
        host_updated += int(not created)

    counts_updated = 0
    for host in MonitoringHost.objects.filter(zabbix_server=server).exclude(zabbix_hostid=''):
        try:
            update_host_inventory_counts(host, client)
            counts_updated += 1
        except (ValueError, ZabbixError):
            pass

    registered = MonitoringHost.objects.filter(zabbix_server=server, is_enabled=True).count()
    server.last_sync_at = timezone.now()
    server.last_sync_status = 'running'
    server.last_sync_message = (
        f'已同步主机清单 {len(zbx_hosts)} 台（新增 {host_created}）；'
        f'已登记 {registered} 台监控主机的项/图统计；明细后台同步中'
    )
    server.save(update_fields=['last_sync_at', 'last_sync_status', 'last_sync_message'])

    return {
        'hosts': len(zbx_hosts),
        'host_created': host_created,
        'host_updated': host_updated,
        'counts_updated': counts_updated,
        'registered_hosts': registered,
        'background': True,
    }


def sync_registered_hosts_items_graphs(server, *, delay_sec: float = 2.0) -> dict[str, Any]:
    """对已登记且启用的监控主机逐台拉取 items/graphs（限速）。"""
    hosts = list(
        MonitoringHost.objects.filter(
            zabbix_server=server, is_enabled=True,
        ).exclude(zabbix_hostid='').order_by('id')
    )
    ok = failed = items_total = graphs_total = 0
    for idx, host in enumerate(hosts):
        if idx > 0 and delay_sec > 0:
            time.sleep(delay_sec)
        try:
            host.mark_syncing()
            it = sync_items(host)
            gr = sync_graphs(host, auto_bind=False)
            update_host_inventory_counts(host)
            host.mark_synced()
            ok += 1
            items_total += it.get('total', 0)
            graphs_total += gr.get('total', 0)
        except (ValueError, ZabbixError) as exc:
            host.mark_error(str(exc))
            failed += 1

    server.last_sync_at = timezone.now()
    server.last_sync_status = 'success'
    server.last_sync_message = (
        f'后台同步完成：{ok} 台成功，{failed} 台失败；'
        f'监控项 {items_total}，图形 {graphs_total}'
    )
    server.save(update_fields=['last_sync_at', 'last_sync_status', 'last_sync_message'])
    return {
        'hosts_ok': ok,
        'hosts_failed': failed,
        'items_total': items_total,
        'graphs_total': graphs_total,
    }


def sync_server_inventory(server, *, operator=None) -> dict[str, Any]:
    """兼容入口：轻量清单 + 同步已登记主机明细（同步阻塞，供管理命令使用）。"""
    result = sync_server_inventory_light(server, operator=operator)
    detail = sync_registered_hosts_items_graphs(server)
    result.update(detail)
    result['background'] = False
    return result
