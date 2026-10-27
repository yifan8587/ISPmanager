"""已付款服务变更的开通：增量资源校验、自动交付或转人工。"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from resource_manager.models import IPAddress
from resource_manager.ip_scope import TUNNEL_BLOCK_TAG as _TUNNEL_BLOCK_TAG

from .change_billing import current_resource_spec, instance_business_ip_queryset
from .change_executor import ChangeExecutionError, execute_change
from .models import (
    ServiceChangeRecord,
    ServiceInstance,
    ServiceOperationLog,
    ServiceOrder,
    _log_service,
)
from .provisioning import _check_resources, capacity_allows, notify_managers, oversell_note


class ChangeProvisionError(Exception):
    pass


def sync_vps_change_to_node(instance: ServiceInstance, *, added=None, released=None) -> dict:
    """变更落库后把 CPU/内存/磁盘/主 IP 下发到节点 PVE。无节点 API 时跳过。"""
    from resource_manager.pemanager_client import PemanagerError, pop_has_api_config
    from resource_manager import pve_ops

    vps = getattr(instance, 'vps', None)
    if vps is None or not vps.source_ref:
        return {'skipped': True, 'reason': 'no_vps_ref'}
    pop = vps.pop_point
    if pop is None or not pop_has_api_config(pop):
        return {'skipped': True, 'reason': 'no_api'}
    warnings = []
    try:
        try:
            pve_ops.resize_vps(vps, {
                'cores': vps.vcpu,
                'memory_mb': vps.ram_mb,
                'disk_gb': vps.disk_gb,
            }, dry_run=False)
        except PemanagerError as exc:
            warnings.append(f'规格：{exc}')
        if vps.bandwidth_mbps:
            try:
                pve_ops.allocate_vps_bandwidth(vps, vps.bandwidth_mbps, dry_run=False)
            except PemanagerError as exc:
                warnings.append(f'带宽：{exc}')
        primary = None
        if instance.ip_addresses.exists():
            primary = instance.ip_addresses.order_by('address').first()
        elif vps.ip_addresses.exists():
            primary = vps.ip_addresses.order_by('address').first()
        if added or released or primary:
            try:
                payload = {'apply_guest': True}
                if primary:
                    payload['ip'] = str(primary.address)
                    payload['prefix'] = (
                        getattr(primary, 'effective_prefix_len', None)
                        or getattr(primary, 'prefix_len', None)
                        or 24
                    )
                    gateway = getattr(primary, 'effective_gateway', None)
                    if gateway:
                        payload['gateway'] = str(gateway)
                else:
                    payload['net_mode'] = 'dhcp'
                pve_ops.set_vps_network(vps, payload, dry_run=False)
            except PemanagerError as exc:
                warnings.append(f'网络：{exc}')
        return {'pushed': True, 'warnings': warnings}
    except Exception as exc:  # noqa: BLE001
        return {'error': str(exc)}


def target_spec_from_change(change: ServiceChangeRecord) -> dict:
    after = change.after_spec or {}
    return {
        'cpu_cores': int(after.get('cpu_cores') or after.get('vcpu') or 0),
        'ram_mb': int(after.get('ram_mb') or 0),
        'disk_gb': int(after.get('disk_gb') or 0),
        'bandwidth_mbps': int(after.get('bandwidth_mbps') or 0),
        'ip_count': int(after.get('ip_count') or 0),
        'nic_count': int(after.get('nic_count') or 0),
    }


def current_ip_count(instance: ServiceInstance) -> int:
    count = instance.ip_addresses.count()
    if count:
        return count
    if instance.vps_id and instance.vps:
        return instance.vps.ip_addresses.count()
    if instance.edge_id and instance.edge:
        return instance.edge.ip_addresses.count()
    return 0


def change_delta_spec(instance: ServiceInstance, target: dict) -> dict:
    current = current_resource_spec(instance)
    current_ips = max(int(current.get('ip_count') or 0), current_ip_count(instance))
    return {
        'cpu_cores': max(0, int(target.get('cpu_cores') or 0) - int(current.get('cpu_cores') or 0)),
        'ram_mb': max(0, int(target.get('ram_mb') or 0) - int(current.get('ram_mb') or 0)),
        'disk_gb': max(0, int(target.get('disk_gb') or 0) - int(current.get('disk_gb') or 0)),
        'bandwidth_mbps': max(
            0,
            int(target.get('bandwidth_mbps') or 0) - int(current.get('bandwidth_mbps') or 0),
        ),
        'ip_count': max(0, int(target.get('ip_count') or 0) - current_ips),
    }


def change_capacity_spec(order: ServiceOrder) -> dict:
    instance = order.related_instance
    change = (
        ServiceChangeRecord.objects.filter(related_order=order)
        .order_by('-id')
        .first()
    )
    if instance is None or change is None:
        from .provisioning import build_spec
        return build_spec(order)
    return change_delta_spec(instance, target_spec_from_change(change))


def extra_ip_needed(instance: ServiceInstance, target: dict) -> int:
    return change_delta_spec(instance, target)['ip_count']


def refresh_instance_spec_snapshot(instance: ServiceInstance) -> dict:
    """按当前资源重写实例规格快照；边缘不含 CPU/内存。"""
    from product_manager.services import spec_from_edge, spec_from_vps

    spec = {}
    if instance.edge_id and instance.edge:
        spec = spec_from_edge(instance.edge)
    elif instance.vps_id and instance.vps:
        spec = spec_from_vps(instance.vps)
    if spec:
        instance.spec_snapshot = spec
        instance.save(update_fields=['spec_snapshot', 'updated_at'])
    return spec


def _business_ip_queryset(instance: ServiceInstance):
    return instance_business_ip_queryset(instance)


def _release_ip_record(instance: ServiceInstance, ip: IPAddress, *, push_to_node=True, operator=None):
    from resource_manager.ip_ops import pop_has_api_config, release_ip_local_and_node
    from resource_manager.resource_audit import log_ip_release, snapshot_ip

    pop = ip.ip_block.pop_point if ip.ip_block_id else None
    if push_to_node and pop is not None and pop_has_api_config(pop):
        release_ip_local_and_node(
            ip, push_to_node=True, operator=operator, source='服务变更-释放',
        )
    else:
        old_value = snapshot_ip(ip)
        old_vps, old_edge = ip.assigned_vps, ip.assigned_edge
        ip.status = IPAddress.IPStatus.AVAILABLE
        ip.assigned_at = None
        ip.assigned_vps = None
        ip.assigned_edge = None
        ip.interface_code = ''
        ip.node_route_ref = ''
        ip.is_primary = False
        ip.save(update_fields=[
            'status', 'assigned_at', 'assigned_vps', 'assigned_edge',
            'interface_code', 'node_route_ref', 'is_primary',
        ])
        log_ip_release(
            ip,
            old_value=old_value,
            operator=operator,
            source='服务变更-本地释放',
            vps=old_vps,
            edge=old_edge,
        )
    instance.ip_addresses.remove(ip)
    return ip


def release_selected_ips(instance: ServiceInstance, ip_ids, *, push_to_node=True, operator=None) -> dict:
    """释放指定业务 IP：本地档案 + 节点路由删除 / IP 管理释放。"""
    wanted = []
    for item in ip_ids or []:
        try:
            wanted.append(int(item))
        except (TypeError, ValueError):
            continue
    allowed = {ip.id: ip for ip in _business_ip_queryset(instance)}
    released, errors = [], []
    for pk in wanted:
        ip = allowed.get(pk)
        if ip is None:
            errors.append(f'IP #{pk} 不属于当前服务')
            continue
        try:
            _release_ip_record(instance, ip, push_to_node=push_to_node, operator=operator)
            released.append(str(ip.address))
        except Exception as exc:  # noqa: BLE001
            errors.append(f'{ip.address}: {exc}')
    return {'released': released, 'errors': errors}


def release_surplus_ips(instance: ServiceInstance, keep_count: int, *, operator=None) -> list:
    """降配时释放多余业务 IP（隧道段不释放）；优先走节点释放。"""
    keep_count = max(int(keep_count or 0), 0)
    ips = list(_business_ip_queryset(instance).order_by('is_primary', 'id'))
    surplus = ips[keep_count:] if len(ips) > keep_count else []
    if not surplus:
        return []
    result = release_selected_ips(instance, [ip.id for ip in surplus], operator=operator)
    released_set = set(result.get('released') or [])
    return [ip for ip in surplus if str(ip.address) in released_set]


def sync_edge_after_change(instance: ServiceInstance, change: ServiceChangeRecord) -> dict:
    """变更落库后对齐带宽档案，已下发过的边缘服务再推一次节点。"""
    edge = instance.edge if instance.edge_id else None
    if edge is None:
        return {'skipped': True, 'reason': 'not_edge'}
    target = target_spec_from_change(change)
    bw = int(target.get('bandwidth_mbps') or edge.bandwidth_mbps or 0)
    try:
        from resource_manager.edge_provision import allocate_edge_bandwidth
        allocate_edge_bandwidth(
            edge.pop_point, edge, bw,
            product=getattr(edge, 'product', None),
        )
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'reason': 'bandwidth', 'error': str(exc)}
    edge.refresh_from_db()
    if not edge.node_pushed:
        return {'ok': True, 'pushed': False, 'reason': 'awaiting_protocol_config'}
    from resource_manager.edge_provision import edge_config_ready, finalize_edge_after_push
    ready, missing = edge_config_ready(edge)
    if not ready:
        return {'ok': True, 'pushed': False, 'reason': 'config_not_ready', 'missing': missing}
    try:
        from resource_manager.deploy_service import push_pop_to_node
        result = push_pop_to_node(
            edge.pop_point, sections=['customers', 'edge_tunnels'], dry_run=False,
        )
        finalized = finalize_edge_after_push(edge)
        return {'ok': True, 'pushed': True, 'result': result, 'finalize': finalized}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'pushed': False, 'error': str(exc)}


def resolve_change_pop(order: ServiceOrder, instance: ServiceInstance):
    return order.preferred_pop or getattr(instance.vps or instance.edge, 'pop_point', None)


def _mark_wait(order: ServiceOrder, change: ServiceChangeRecord, *, status: str, subject: str, message: str, operator=None):
    order.status = ServiceOrder.OrderStatus.APPROVED
    order.auto_provision_status = status
    order.auto_provision_message = message[:2000]
    order.save(update_fields=[
        'status', 'auto_provision_status', 'auto_provision_message', 'updated_at',
    ])
    _log_service(
        operator=operator,
        action_type=ServiceOperationLog.ActionType.CHANGE,
        description=f'工单 [{order.order_no}] {subject}：{message}'[:500],
        service_order=order,
        service_instance=order.related_instance,
    )
    notify_managers(order, subject, message)
    from .notify_helpers import notify_change_event
    notify_change_event(change, subject, detail=message)


def _mark_success(order: ServiceOrder, change: ServiceChangeRecord, *, message: str):
    order.status = ServiceOrder.OrderStatus.ACTIVE
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
    order.auto_provision_message = message[:2000]
    order.save(update_fields=[
        'status', 'auto_provision_status', 'auto_provision_message', 'updated_at',
    ])
    from .notify_helpers import notify_order_event
    notify_order_event(order, '变更开通成功', detail=message, event='service_provision')


def allocate_extra_ips(order: ServiceOrder, instance: ServiceInstance, count: int, *, exclude_ids=None, operator=None):
    if count <= 0:
        return {'ips': [], 'warnings': []}
    pop = resolve_change_pop(order, instance)
    if pop is None:
        raise ChangeProvisionError('变更工单缺少 POP，无法分配新增 IP')
    qs = (
        IPAddress.objects.select_for_update()
        .filter(
            ip_block__pop_point=pop,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        .exclude(ip_block__description__startswith=_TUNNEL_BLOCK_TAG)
        .order_by('address')
    )
    if exclude_ids:
        qs = qs.exclude(pk__in=list(exclude_ids))
    available = list(qs[:count])
    if len(available) < count:
        raise ChangeProvisionError(f'可用 IP 不足：需新增 {count}，实际可用 {len(available)}')
    now = timezone.now()
    vps = instance.vps if instance.vps_id else None
    edge = instance.edge if instance.edge_id else None
    from resource_manager.ip_ops import assign_ip_local_and_node, pop_has_api_config

    assigned = []
    warnings = []
    for ip in available:
        pushed = False
        node_error = ''
        try:
            if edge is not None and pop_has_api_config(pop):
                assign_ip_local_and_node(
                    ip,
                    target_type='edge',
                    edge=edge,
                    push_to_node=bool(edge.node_ifname),
                    create_route=bool(edge.node_ifname),
                    operator=operator,
                    source=f'服务变更-分配 {order.order_no}',
                )
                pushed = True
            elif vps is not None and pop_has_api_config(pop):
                assign_ip_local_and_node(
                    ip,
                    target_type='vps',
                    vps=vps,
                    push_to_node=True,
                    create_route=False,
                    operator=operator,
                    source=f'服务变更-分配 {order.order_no}',
                )
                pushed = True
        except Exception as exc:  # noqa: BLE001
            pushed = False
            node_error = str(exc)
        if not pushed:
            from resource_manager.resource_audit import log_ip_assign, snapshot_ip
            old_value = snapshot_ip(ip)
            ip.status = IPAddress.IPStatus.ASSIGNED
            ip.assigned_at = now
            ip.assigned_vps = vps
            ip.assigned_edge = edge
            ip.product = order.product
            ip.description = ((ip.description or '') + f' | 变更工单 {order.order_no}')[:256]
            ip.save(update_fields=[
                'status', 'assigned_at', 'assigned_vps', 'assigned_edge',
                'product', 'description',
            ])
            log_ip_assign(
                ip,
                old_value=old_value,
                operator=operator,
                source=f'服务变更-本地分配 {order.order_no}',
                extra={'node_warning': node_error or '未下发节点'},
            )
            if node_error:
                warnings.append(f'{ip.address} 已写入本地档案，节点路由未下发：{node_error}')
            elif edge is not None and pop_has_api_config(pop) and not edge.node_ifname:
                warnings.append(f'{ip.address} 已分配，隧道接口未就绪暂未下发路由')
        elif order.product_id:
            ip.product = order.product
            ip.description = ((ip.description or '') + f' | 变更工单 {order.order_no}')[:256]
            ip.save(update_fields=['product', 'description'])
        assigned.append(ip)
    instance.ip_addresses.add(*assigned)
    return {'ips': assigned, 'warnings': warnings}


def attach_selected_ips(order: ServiceOrder, instance: ServiceInstance, ip_ids: list[int], *, operator=None):
    if not ip_ids:
        return []
    pop = resolve_change_pop(order, instance)
    ips = list(
        IPAddress.objects.select_for_update()
        .filter(pk__in=ip_ids)
        .select_related('ip_block__pop_point')
        .order_by('address')
    )
    if len(ips) != len(set(ip_ids)):
        raise ChangeProvisionError('包含不存在的 IP 地址')
    now = timezone.now()
    vps = instance.vps if instance.vps_id else None
    edge = instance.edge if instance.edge_id else None
    from resource_manager.resource_audit import log_ip_assign, snapshot_ip

    for ip in ips:
        if ip.status != IPAddress.IPStatus.AVAILABLE:
            raise ChangeProvisionError(f'IP {ip.address} 当前不是可分配状态')
        if pop and ip.ip_block.pop_point_id != pop.id:
            raise ChangeProvisionError(f'IP {ip.address} 不属于工单期望 POP')
        old_value = snapshot_ip(ip)
        ip.status = IPAddress.IPStatus.ASSIGNED
        ip.assigned_at = now
        ip.assigned_vps = vps
        ip.assigned_edge = edge
        ip.product = order.product
        ip.save(update_fields=[
            'status', 'assigned_at', 'assigned_vps', 'assigned_edge', 'product',
        ])
        log_ip_assign(
            ip,
            old_value=old_value,
            operator=operator or getattr(order, 'created_by', None),
            source=f'人工交付 {order.order_no}',
        )
    instance.ip_addresses.add(*ips)
    return ips


@transaction.atomic
def apply_paid_change(
    order: ServiceOrder,
    change: ServiceChangeRecord,
    *,
    operator=None,
    create_followup_ticket=None,
) -> dict:
    """付款后尝试自动交付变更：资源够则开通，不够则 IM 通知并等待人工交付。"""
    instance = order.related_instance
    if instance is None:
        return {'triggered': False, 'reason': 'no_related_instance'}
    if change.status == ServiceChangeRecord.ChangeStatus.COMPLETED:
        return {'triggered': False, 'reason': 'already_completed'}

    target = target_spec_from_change(change)
    after = change.after_spec or {}
    release_ids = []
    for item in after.get('release_ip_ids') or []:
        try:
            release_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    add_count = int(after.get('add_ip_count') or 0)
    delta = change_delta_spec(instance, target)
    extra_ips = extra_ip_needed(instance, target)
    if release_ids or add_count:
        extra_ips = max(add_count, 0)
    elif change.change_type == ServiceChangeRecord.ChangeType.DOWNGRADE:
        # 降配只收缩档案规格，不因当前未挂 IP 而误判需要新增地址
        extra_ips = 0
        delta = {key: 0 for key in delta}
    if extra_ips > int(delta.get('ip_count') or 0):
        delta = dict(delta)
        delta['ip_count'] = extra_ips
    pop = resolve_change_pop(order, instance)
    needs_pool = extra_ips > 0 or any(
        delta.get(key, 0) > 0 for key in ('cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps')
    )
    steps = [{
        'key': 'billing',
        'label': '计费统计',
        'status': 'done',
        'detail': '变更单与费用已入账',
    }]

    if needs_pool and pop is None:
        _mark_wait(
            order, change,
            status=ServiceOrder.AutoProvisionStatus.PAID_PENDING,
            subject='变更已付款待人工',
            message='支付已确认，但变更工单缺少 POP，请管理员手动交付新增资源',
            operator=operator,
        )
        steps.append({
            'key': 'allocate', 'label': '分配新 IP', 'status': 'error',
            'detail': '缺少 POP，无法交付新增资源',
        })
        return {'triggered': False, 'reason': 'no_pop', 'progress_steps': steps}

    check = {'ok': True, 'shortages': []}
    if extra_ips > 0 or needs_pool:
        check = _check_resources(pop, delta, exclude_order=order)
        if not capacity_allows(pop, check):
            shortages = '；'.join(check.get('shortages') or []) or '资源不足'
            _mark_wait(
                order, change,
                status=ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
                subject='变更资源不足待人工',
                message=f'支付已确认，但当前节点无法交付新增规格：{shortages}',
                operator=operator,
            )
            steps.append({
                'key': 'allocate', 'label': '分配新 IP', 'status': 'error',
                'detail': shortages,
            })
            return {
                'triggered': False,
                'reason': 'insufficient',
                'shortages': check.get('shortages') or [],
                'progress_steps': steps,
            }

    added = []
    if extra_ips > 0:
        try:
            alloc = allocate_extra_ips(
                order, instance, extra_ips, exclude_ids=release_ids, operator=operator,
            )
            added = alloc.get('ips') or []
            warnings = alloc.get('warnings') or []
            detail = (
                '已分配 ' + '、'.join(str(ip.address) for ip in added)
                if added else f'已分配 {extra_ips} 个地址'
            )
            if warnings:
                detail = f"{detail}；{'；'.join(warnings)}"[:240]
            steps.append({
                'key': 'allocate',
                'label': '分配新 IP 并下发路由',
                'status': 'warn' if warnings else 'done',
                'detail': detail,
            })
        except ChangeProvisionError as exc:
            _mark_wait(
                order, change,
                status=ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
                subject='变更资源不足待人工',
                message=f'支付已确认，但新增 IP 不足：{exc}',
                operator=operator,
            )
            steps.append({
                'key': 'allocate', 'label': '分配新 IP 并下发路由',
                'status': 'error', 'detail': str(exc),
            })
            return {
                'triggered': False, 'reason': 'insufficient',
                'error': str(exc), 'progress_steps': steps,
            }
    elif add_count == 0:
        steps.append({
            'key': 'allocate', 'label': '分配新 IP 并下发路由',
            'status': 'done', 'detail': '无需新增地址',
        })

    released = []
    if release_ids:
        rel = release_selected_ips(instance, release_ids, operator=operator)
        released = rel.get('released') or []
        if rel.get('errors'):
            steps.append({
                'key': 'release', 'label': '释放 IP 并删除路由',
                'status': 'error',
                'detail': '；'.join(rel['errors'])[:200],
            })
        else:
            steps.append({
                'key': 'release', 'label': '释放 IP 并删除路由',
                'status': 'done',
                'detail': '已释放 ' + '、'.join(released) if released else '已按选择释放',
            })
    elif change.change_type == ServiceChangeRecord.ChangeType.DOWNGRADE:
        released_objs = release_surplus_ips(
            instance, int(target.get('ip_count') or 0), operator=operator,
        )
        released = [str(ip.address) for ip in released_objs]
        steps.append({
            'key': 'release', 'label': '释放 IP 并删除路由',
            'status': 'done',
            'detail': '已释放 ' + '、'.join(released) if released else '无需释放地址',
        })
    else:
        steps.append({
            'key': 'release', 'label': '释放 IP 并删除路由',
            'status': 'done', 'detail': '无需释放地址',
        })

    try:
        execute_change(change, operator=operator)
        steps.append({
            'key': 'spec', 'label': '规格与带宽落库',
            'status': 'done',
            'detail': f"带宽 {target.get('bandwidth_mbps') or 0} Mbps",
        })
    except ChangeExecutionError as exc:
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.FAILED
        order.auto_provision_message = str(exc)[:2000]
        order.save(update_fields=['auto_provision_status', 'auto_provision_message', 'updated_at'])
        from .capacity_reservation import release_reservation
        release_reservation(order, reason='升配执行失败，回滚预占')
        notify_managers(order, '自动开通失败', str(exc))
        steps.append({
            'key': 'spec', 'label': '规格与带宽落库',
            'status': 'error', 'detail': str(exc)[:200],
        })
        return {'triggered': False, 'reason': 'execute_failed', 'error': str(exc), 'progress_steps': steps}

    from .capacity_reservation import consume_reservation
    consume_reservation(order)
    instance.refresh_from_db()
    refresh_instance_spec_snapshot(instance)
    node_sync = sync_vps_change_to_node(instance, added=added, released=released)
    if node_sync.get('pushed'):
        steps.append({
            'key': 'vps_node', 'label': '节点规格与网络同步',
            'status': 'warn' if node_sync.get('warnings') else 'done',
            'detail': '；'.join(node_sync.get('warnings') or [])[:200] or '已下发 CPU/内存/磁盘/IP',
        })
    elif node_sync.get('error'):
        steps.append({
            'key': 'vps_node', 'label': '节点规格与网络同步',
            'status': 'warn',
            'detail': str(node_sync.get('error'))[:200],
        })
    edge_sync = {}
    if instance.edge_id:
        edge_sync = sync_edge_after_change(instance, change)
        if edge_sync.get('pushed'):
            steps.append({
                'key': 'push', 'label': '节点带宽与路由同步',
                'status': 'done', 'detail': '边缘节点配置已同步',
            })
        elif edge_sync.get('error'):
            steps.append({
                'key': 'push', 'label': '节点带宽与路由同步',
                'status': 'warn', 'detail': str(edge_sync.get('error'))[:200],
            })
        else:
            steps.append({
                'key': 'push', 'label': '节点带宽与路由同步',
                'status': 'done',
                'detail': edge_sync.get('reason') or '已核对节点配置',
            })
    note = f'变更 {change.change_no} 已自动开通并交付完成'
    extra = oversell_note(pop, check)
    if extra:
        note = f'{note}；{extra}'
    if edge_sync.get('pushed'):
        note = f'{note}；边缘节点配置已同步'
    elif instance.edge_id and edge_sync.get('error'):
        note = f'{note}；节点同步未完成：{edge_sync.get("error")}'
    _mark_success(order, change, message=note)
    steps.append({
        'key': 'activate', 'label': '变更生效',
        'status': 'done', 'detail': note,
    })
    from .change_scheduler import ensure_change_followup_ticket
    ticket = ensure_change_followup_ticket(
        change, operator=operator, enabled=create_followup_ticket,
    )
    return {
        'triggered': True,
        'change_no': change.change_no,
        'auto': True,
        'followup_ticket_no': getattr(ticket, 'ticket_no', None),
        'released_ips': released,
        'added_ips': [str(ip.address) for ip in added],
        'progress_steps': steps,
    }


@transaction.atomic
def deliver_change_order(order: ServiceOrder, *, ip_ids=None, operator=None, remark: str = '') -> dict:
    """后台手动交付已付款变更：把补齐的 IP 挂到原实例并执行变更。"""
    instance = order.related_instance
    if instance is None:
        raise ChangeProvisionError('变更工单未关联原服务实例')
    change = (
        ServiceChangeRecord.objects.select_for_update()
        .filter(related_order=order)
        .order_by('-id')
        .first()
    )
    if change is None:
        raise ChangeProvisionError('未找到关联变更单')
    if change.status == ServiceChangeRecord.ChangeStatus.COMPLETED:
        raise ChangeProvisionError('该变更已执行完成')
    if change.status == ServiceChangeRecord.ChangeStatus.PENDING:
        change.status = ServiceChangeRecord.ChangeStatus.APPROVED
        change.approved_at = timezone.now()
        change.approved_by = operator
        change.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])

    attached = attach_selected_ips(order, instance, list(ip_ids or []), operator=operator)
    execute_change(change, operator=operator)
    from .capacity_reservation import consume_reservation
    consume_reservation(order)
    instance.refresh_from_db()
    sync_vps_change_to_node(instance, added=attached)
    note = remark or f'变更 {change.change_no} 已手动交付'
    if attached:
        note = f'{note}，新增 IP {len(attached)} 个'
    _mark_success(order, change, message=note)
    from .change_scheduler import ensure_change_followup_ticket
    ticket = ensure_change_followup_ticket(change, operator=operator)
    return {
        'change_no': change.change_no,
        'instance_no': instance.instance_no,
        'added_ips': [ip.address for ip in attached],
        'followup_ticket_no': getattr(ticket, 'ticket_no', None),
    }
