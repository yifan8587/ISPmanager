"""
服务工单自动开通编排。

当工单提交（门户下单 / 后台提交）时，若其期望 POP 节点的配置模式为「自动配置」
（PopPoint.config_mode == 'auto'），则：

  1. 依据工单需求组装规格，调用 product_manager 的 check_spec_against_pop 判断该 POP
     资源是否足够；
  2. 资源足够：按业务类型（VPS / 边缘加速 / IP / 带宽）自动从资源管理下发配置并创建
     ServiceInstance 绑定资源。边缘加速 GRE/WG 只预分配带宽、业务 IP、隧道地址段与
     POP 管理 IP，实例保持开通中/待配置，由客户在门户补全后下发；VPS 等仍直接开通。
  3. 资源不足或下发失败：工单保持待处理，写回 auto_provision_status/message，并通过 IM
     通知管理员到后台手工处理。

设计要点：
  - 手动配置的 POP（config_mode == 'manual'）不触发，走原有人工审批/开通流程；
  - 全过程对异常做兜底，任何失败都转化为「通知人工」，绝不因自动开通异常阻断下单主流程；
  - Socks5 边缘加速节点侧无自动下发通道，仅建档并通知人工。
"""

import logging
import secrets

from django.db import transaction
from django.utils import timezone

from .models import (
    ServiceOrder, ServiceInstance, ServiceAllocation,
    ServiceOperationLog, _log_service,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 规格 / 资源判断
# ═══════════════════════════════════════════════════════════════

def build_spec(order: ServiceOrder) -> dict:
    """由工单需求字段组装资源规格（供 check_spec_against_pop 使用）。"""
    spec = {
        'cpu_cores': int(order.requested_vcpu or 0),
        'ram_mb': int(order.requested_ram_mb or 0),
        'disk_gb': int(order.requested_disk_gb or 0),
        'bandwidth_mbps': int(order.requested_bandwidth_mbps or 0),
        'ip_count': int(order.requested_ip_count or 0),
        'nic_count': 0,
    }
    product = getattr(order, 'product', None)
    if product is not None:
        from product_manager.services import normalize_spec_for_category
        spec = normalize_spec_for_category(product, spec)
    elif getattr(order, 'business_type', '') in {
        ServiceOrder.BusinessType.EDGE_GRE,
        ServiceOrder.BusinessType.EDGE_WG,
        ServiceOrder.BusinessType.EDGE_SOCKS5,
    }:
        spec['cpu_cores'] = 0
        spec['ram_mb'] = 0
        spec['disk_gb'] = 0
        spec['nic_count'] = 0
    return spec


def _check_resources(pop, spec: dict, exclude_order=None) -> dict:
    from product_manager import services as product_services
    return product_services.check_spec_against_pop(
        pop, spec, exclude_order=exclude_order,
    )


def capacity_allows(pop, check: dict | None) -> bool:
    """开通与预占共用：容量够，或 POP 打开 ``allow_oversell``。

    容量校验异常（``error=True``）不允许靠超卖硬开，避免服务不可用时误开通。
    """
    check = check or {}
    if check.get('ok'):
        return True
    if check.get('error'):
        return False
    return bool(pop is not None and getattr(pop, 'allow_oversell', False))


def oversell_note(pop, check: dict | None) -> str:
    check = check or {}
    if check.get('ok') or not capacity_allows(pop, check):
        return ''
    shortages = '；'.join(check.get('shortages') or []) or '剩余容量不足'
    return f'已按超卖策略继续（{shortages}）'


# ═══════════════════════════════════════════════════════════════
# IM 通知
# ═══════════════════════════════════════════════════════════════

def notify_managers(order: ServiceOrder, subject: str, detail: str = '') -> None:
    """资源不足 / 自动开通失败时，通知管理员到后台处理。"""
    try:
        from .notify_helpers import notify_order_event
    except Exception:  # noqa: BLE001
        return
        notify_order_event(
        order,
        subject,
        detail=(
            f'{detail}\n请到资源管理补齐对应 POP 的容量、IP 或带宽，'
            '然后在服务工单中使用「资源交付」手动交给客户。'
            '库存不足成交的订单不会自动开通。'
        ).strip(),
        event='service_provision',
    )


# ═══════════════════════════════════════════════════════════════
# 各业务类型下发（资源足够时）
# ═══════════════════════════════════════════════════════════════

def _instance_type_for(order: ServiceOrder) -> str:
    bt = order.business_type
    if bt == ServiceOrder.BusinessType.VPS:
        return ServiceInstance.ServiceType.VPS
    if bt in (ServiceOrder.BusinessType.EDGE_GRE,
              ServiceOrder.BusinessType.EDGE_WG,
              ServiceOrder.BusinessType.EDGE_SOCKS5):
        return ServiceInstance.ServiceType.EDGE_ACCELERATION
    if bt == ServiceOrder.BusinessType.IP:
        return ServiceInstance.ServiceType.IP_ADDRESS
    if bt == ServiceOrder.BusinessType.BANDWIDTH:
        return ServiceInstance.ServiceType.BANDWIDTH
    return ServiceInstance.ServiceType.VPS


def _order_ip_count(order: ServiceOrder) -> int:
    count = int(order.requested_ip_count or 0)
    if count <= 0:
        product = getattr(order, 'product', None)
        count = int(getattr(product, 'ip_count', 0) or 0)
    return max(count, 0)


def _peek_order_ips(order: ServiceOrder, pop, count: int):
    """开通前预览将分配的 IP；地址块未展开时先按需生成，再写入 cloud-init。"""
    from resource_manager.ip_ops import ensure_available_ips_for_pop

    if count <= 0:
        return []
    ips = ensure_available_ips_for_pop(pop, count)
    if len(ips) < count:
        raise RuntimeError(
            f'POP「{getattr(pop, "name", pop)}」可用 IP 不足：需 {count}，实际可用 {len(ips)}。'
            f'请在资源管理生成地址或释放空闲 IP'
        )
    return ips


def _drop_empty_network_fields(data: dict) -> dict:
    """节点 serializer 拒绝 null，没分到 IP 时不要把 ip/gateway/prefix 传出去。"""
    payload = dict(data or {})
    for key in ('ip', 'gateway', 'prefix', 'ip6', 'gw6'):
        if payload.get(key) in (None, ''):
            payload.pop(key, None)
    cloud_init = payload.get('cloud_init')
    if isinstance(cloud_init, dict):
        payload['cloud_init'] = {
            key: value for key, value in cloud_init.items()
            if value not in (None, '')
        }
    return payload


def _generate_vps_password() -> str:
    return secrets.token_urlsafe(12)


def _order_cloud_init_request(order: ServiceOrder) -> dict:
    snapshot = order.request_snapshot if isinstance(order.request_snapshot, dict) else {}
    raw = snapshot.get('cloud_init') if isinstance(snapshot.get('cloud_init'), dict) else {}
    return {
        'username': str(raw.get('username') or '').strip(),
        'password': str(raw.get('password') or '').strip(),
        'sshkeys': str(raw.get('sshkeys') or '').strip(),
        'nameserver': str(raw.get('nameserver') or '').strip(),
    }


def _build_vps_cloud_init(catalog, pop, ips, password: str, *, username: str = '', nameserver: str = '', sshkeys: str = '') -> dict:
    from system_manager.os_templates import infer_cloud_ciuser

    hint = ''
    if catalog is not None:
        hint = f'{getattr(catalog, "name", "")} {getattr(catalog, "backup_filename", "")}'
    user = (username or '').strip() or infer_cloud_ciuser(hint)
    cloud_init = {
        'user': user,
        'password': password,
        'nameserver': nameserver or '223.5.5.5 1.1.1.1',
    }
    if sshkeys:
        cloud_init['sshkeys'] = sshkeys
    first = ips[0] if ips else None
    if first is not None:
        cloud_init['ip'] = str(first.address)
        cloud_init['prefix'] = first.effective_prefix_len
        gateway = first.effective_gateway
        if gateway:
            cloud_init['gateway'] = gateway
    else:
        cloud_init['net_mode'] = 'dhcp'
    return cloud_init


def _allocate_order_ips(
    order: ServiceOrder, pop, instance: ServiceInstance, *,
    vps=None, edge=None, push_to_node=True,
):
    """为已创建的服务实例分配工单所需 IP，并同步资源归属字段。"""
    from resource_manager.models import IPAddress
    from resource_manager.ip_scope import TUNNEL_BLOCK_TAG as _TUNNEL_BLOCK_TAG

    count = _order_ip_count(order)
    if count <= 0:
        return []

    from resource_manager.ip_ops import ensure_available_ips_for_pop
    ensure_available_ips_for_pop(pop, count)

    available = list(
        IPAddress.objects.select_for_update()
        .filter(
            ip_block__pop_point=pop,
            status=IPAddress.IPStatus.AVAILABLE,
        )
        .exclude(ip_block__description__startswith=_TUNNEL_BLOCK_TAG)
        .order_by('address')[:count]
    )
    if len(available) < count:
        raise ValueError(f'可用 IP 不足：需 {count}，实际可用 {len(available)}')

    now = timezone.now()
    for ip in available:
        ip.status = IPAddress.IPStatus.ASSIGNED
        ip.assigned_at = now
        ip.assigned_vps = vps
        ip.assigned_edge = edge
        ip.product = order.product
        ip.description = (ip.description or '')[:180] + f' | 工单 {order.order_no}'
        ip.save(update_fields=[
            'status', 'assigned_at', 'assigned_vps', 'assigned_edge',
            'product', 'description',
        ])
    instance.ip_addresses.set(available)

    from resource_manager.pemanager_client import PemanagerError, pop_has_api_config
    if available and push_to_node and pop_has_api_config(pop):
        from resource_manager.ip_ops import push_order_ips_to_node
        try:
            push_order_ips_to_node(
                pop, available,
                customer=order.customer,
                vps=vps,
                edge=edge,
            )
        except PemanagerError as exc:
            raise RuntimeError(f'工单 IP 下发节点失败：{exc}') from exc
    return available


def _instance_fee_from_order(order: ServiceOrder) -> tuple:
    from decimal import Decimal, InvalidOperation

    snap = order.request_snapshot or {}
    quote = snap.get('quote') or {}
    raw = quote.get('unit_monthly_price') or order.quoted_price or 0
    try:
        monthly = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        monthly = Decimal('0')
    currency = (
        quote.get('currency')
        or getattr(order, 'fx_quote_currency', '')
        or getattr(getattr(order, 'product', None), 'currency', '')
        or 'CNY'
    )
    return monthly, currency


def _edge_pending_customer_config(instance: ServiceInstance) -> bool:
    edge = getattr(instance, 'edge', None)
    if edge is None:
        return False
    return (
        edge.status == edge.EdgeStatus.PENDING
        and not edge.node_pushed
    )


def _provision_edge(order: ServiceOrder, pop, operator) -> tuple[ServiceInstance, str]:
    """GRE / WireGuard：预分配待配置资源，等客户补全后再下发；Socks5 必须人工交付。"""
    from resource_manager.edge_provision import (
        allocate_edge_bandwidth,
        ensure_pending_protocol_config,
        pop_peer_ip,
        reserve_tunnel_block,
    )
    from resource_manager.models import EdgeAcceleration

    proto_map = {
        ServiceOrder.BusinessType.EDGE_GRE: EdgeAcceleration.Protocol.GRE,
        ServiceOrder.BusinessType.EDGE_WG: EdgeAcceleration.Protocol.WIREGUARD,
        ServiceOrder.BusinessType.EDGE_SOCKS5: EdgeAcceleration.Protocol.SOCKS5,
    }
    protocol = proto_map[order.business_type]
    if protocol == EdgeAcceleration.Protocol.SOCKS5:
        raise ValueError('Socks5 服务不支持自动下发，请完成节点配置后人工交付')

    pop_peer_ip(pop, required=True)
    bw = int(order.requested_bandwidth_mbps or 0) or 100

    edge = EdgeAcceleration.objects.create(
        name=f'{order.customer.name}-{order.order_no}',
        protocol=protocol,
        status=EdgeAcceleration.EdgeStatus.PENDING,
        pop_point=pop,
        bandwidth_mbps=bw,
        customer=order.customer,
        contract=order.contract,
        product=order.product,
        start_time=timezone.now(),
        created_by=operator,
        remark=f'工单 {order.order_no} 自动开通，待客户补全协议配置',
    )

    tunnel = reserve_tunnel_block(pop, edge, customer=order.customer)
    bw_alloc = allocate_edge_bandwidth(
        pop, edge, bw, product=order.product, operator=operator,
    )
    ensure_pending_protocol_config(
        edge, reserve_tunnel=False, tunnel_cidr=tunnel.get('local_cidr') or '',
    )
    edge.refresh_from_db()

    gre = getattr(edge, 'gre_config', None)
    if gre is not None and order.edge_remote_ip and not gre.remote_ip:
        gre.remote_ip = order.edge_remote_ip
        gre.save(update_fields=['remote_ip'])
    wg = getattr(edge, 'wg_config', None)
    if wg is not None:
        fields = []
        if order.edge_wg_client_pubkey and not wg.peer_public_key:
            wg.peer_public_key = order.edge_wg_client_pubkey
            fields.append('peer_public_key')
        if order.edge_wg_allowed_ips and not wg.peer_allowed_ips:
            wg.peer_allowed_ips = order.edge_wg_allowed_ips
            fields.append('peer_allowed_ips')
        if fields:
            wg.save(update_fields=fields)

    monthly, currency = _instance_fee_from_order(order)
    instance = ServiceInstance.objects.create(
        service_order=order,
        service_type=ServiceInstance.ServiceType.EDGE_ACCELERATION,
        status=ServiceInstance.InstanceStatus.PROVISIONING,
        edge=edge,
        bandwidth_alloc=bw_alloc,
        spec_snapshot=build_spec(order),
        monthly_fee=monthly,
        fee_currency=currency,
        billing_start_date=timezone.now().date(),
        provisioned_at=timezone.now(),
        provisioned_by=operator,
    )
    ips = _allocate_order_ips(
        order, pop, instance, edge=edge, push_to_node=False,
    )
    ip_note = f'，已分配 IP：{", ".join(str(ip.address) for ip in ips)}' if ips else ''
    tunnel_note = f'，隧道 {tunnel.get("local_cidr")}' if tunnel.get('local_cidr') else ''
    return instance, (
        f'边缘加速 {edge.edge_no} 已预分配带宽 {bw}Mbps{ip_note}{tunnel_note}，'
        '待客户在门户补全协议配置后保存并下发'
    )


def _provision_ip(order: ServiceOrder, pop, operator) -> tuple[ServiceInstance, str]:
    """IP 资源自动分配：从该 POP 的可用 IP 中挑选 N 个标记为已分配并绑定实例。"""
    if _order_ip_count(order) <= 0:
        raise ValueError('IP 工单未指定需求 IP 数量')
    now = timezone.now()
    instance = ServiceInstance.objects.create(
        service_order=order,
        service_type=ServiceInstance.ServiceType.IP_ADDRESS,
        status=ServiceInstance.InstanceStatus.PROVISIONING,
        spec_snapshot=build_spec(order),
        provisioned_at=now,
        provisioned_by=operator,
    )
    ips = _allocate_order_ips(order, pop, instance)
    addrs = ', '.join(str(ip.address) for ip in ips)
    return instance, f'已分配 {len(ips)} 个 IP：{addrs}'


def _provision_bandwidth(order: ServiceOrder, pop, operator) -> tuple[ServiceInstance, str]:
    """独立带宽必须先明确绑定目标资源，避免写入无法下发的伪分配记录。"""
    raise ValueError(
        '独立带宽工单需先在资源管理中建立并绑定 VPS/边缘的带宽分配，'
        '再通过工单“资源交付”完成关联'
    )


def _resolve_order_os_template(order: ServiceOrder):
    from system_manager.os_templates import match_os_template

    if getattr(order, 'requested_os_template_id', None):
        return order.requested_os_template
    return match_os_template(order.requested_os)


def _provision_vps(order: ServiceOrder, pop, operator) -> tuple[ServiceInstance, str]:
    """VPS 自动创建：调用节点 PVE 创建虚机，成功后按名称绑定同步回来的 VPS。"""
    from resource_manager import pve_ops
    from resource_manager.models import VPS

    resources = pve_ops.list_pve_resources(pop)
    hosts = resources.get('hosts') or []
    if not hosts:
        raise RuntimeError('节点无可用 PVE 主机')
    host = hosts[0]
    nodes = host.get('nodes') or []
    if not nodes:
        raise RuntimeError('节点无可用 PVE 节点')
    node_name = nodes[0].get('node')
    storages = host.get('storages') or []
    storage = next((s.get('storage') for s in storages if isinstance(s, dict) and s.get('storage')), None)
    vmid = resources.get('next_vmid')
    vm_name = f'{order.customer.customer_no}-{order.order_no}'.lower().replace('_', '-')

    catalog = _resolve_order_os_template(order)
    template = None
    if catalog is not None:
        template = pve_ops.ensure_os_template_on_pop(pop, catalog)
    else:
        templates = resources.get('templates') or []
        want_os = (order.requested_os or '').lower()
        for item in templates:
            if isinstance(item, dict) and want_os and want_os in str(item.get('name', '')).lower():
                template = item
                break
        if template is None and templates:
            template = templates[0]

    cores = int(order.requested_vcpu or 1)
    memory = int(order.requested_ram_mb or 1024)
    disk = int(order.requested_disk_gb or 0)
    requested_ci = _order_cloud_init_request(order)
    password = requested_ci['password'] or _generate_vps_password()
    preview_ips = _peek_order_ips(order, pop, _order_ip_count(order))
    cloud_init = _build_vps_cloud_init(
        catalog, pop, preview_ips, password,
        username=requested_ci['username'],
        nameserver=requested_ci['nameserver'],
        sshkeys=requested_ci['sshkeys'],
    )

    payload = {
        'host_id': host.get('id'),
        'node': node_name,
        'vmid': vmid,
        'name': vm_name,
        'newname': vm_name,
        'cores': cores,
        'memory': memory,
        'memory_mb': memory,
        'username': cloud_init.get('user'),
        'password': password,
        'nameserver': cloud_init.get('nameserver'),
        'cloud_init': cloud_init,
        'start_after_create': True,
        'customer_code': getattr(order.customer, 'customer_no', '') or '',
        'auto_ip': False,
    }
    if cloud_init.get('ip'):
        payload['ip'] = cloud_init['ip']
    if cloud_init.get('gateway'):
        payload['gateway'] = cloud_init['gateway']
    if cloud_init.get('prefix') not in (None, ''):
        payload['prefix'] = cloud_init['prefix']
    payload = _drop_empty_network_fields(payload)
    if requested_ci['sshkeys']:
        payload['sshkeys'] = requested_ci['sshkeys']
    bandwidth_mbps = int(order.requested_bandwidth_mbps or 0)
    if bandwidth_mbps > 0:
        payload['nics'] = [{
            'model': 'virtio',
            'rate_mb': round(bandwidth_mbps / 8.0, 2),
        }]
    if storage:
        payload['storage'] = storage
    if disk:
        payload['disk_gb'] = disk
    if catalog is not None:
        payload['os_code'] = getattr(catalog, 'code', '') or ''
        if getattr(catalog, 'download_url', ''):
            payload['download_url'] = catalog.download_url
        if getattr(catalog, 'backup_filename', ''):
            payload['filename'] = catalog.backup_filename
    if template is not None:
        payload['template_id'] = template.get('id')
        payload['template_vmid'] = template.get('template_vmid') or template.get('vmid')

    if template is not None or catalog is not None:
        result = pve_ops.provision_vps(pop, payload, dry_run=False)
        if isinstance(result, dict) and result.get('job_id') and result.get('status') != 'succeeded':
            waited = pve_ops.wait_for_create_job(pop, result['job_id'])
            if isinstance(waited, dict):
                result = {**result, **waited}
        node_result = result.get('result') if isinstance(result, dict) else {}
        if not isinstance(node_result, dict):
            node_result = {}
        vm_pk = node_result.get('vm_pk') or node_result.get('vm_id') or (
            result.get('vm_pk') if isinstance(result, dict) else None
        )
        vmid = (
            node_result.get('vmid')
            or (result.get('vmid') if isinstance(result, dict) else None)
            or vmid
        )
    elif catalog is not None:
        raise RuntimeError(f'无法在节点准备系统模版 {catalog.display_label}')
    else:
        pve_ops.create_vm(pop, payload, dry_run=False)
        vm_pk = None

    # 节点创建后已触发同步，按名称 / 主键 / VMID 定位新建 VPS
    vps = (
        VPS.objects.filter(pop_point=pop, name=vm_name).order_by('-id').first()
        or (VPS.objects.filter(pop_point=pop, source_ref=str(vm_pk)).order_by('-id').first() if vm_pk else None)
        or VPS.objects.filter(pop_point=pop, source_ref=str(vmid)).order_by('-id').first()
    )
    if vps is None:
        raise RuntimeError('虚机已在节点创建，但未能同步定位到本地 VPS，请后台核对绑定')

    vps.customer = order.customer
    vps.product = order.product
    vps.mgmt_username = cloud_init.get('user') or 'ubuntu'
    vps.mgmt_password = password
    os_fields = ['customer', 'product', 'mgmt_username', 'mgmt_password', 'updated_at']
    if vm_pk and str(vps.source_ref or '') != str(vm_pk):
        vps.source_ref = str(vm_pk)
        os_fields.append('source_ref')
    if catalog is not None:
        if catalog.os_type:
            vps.os_type = catalog.os_type
            os_fields.append('os_type')
        if catalog.version:
            vps.os_version = catalog.version
            os_fields.append('os_version')
    vps.save(update_fields=os_fields)

    instance = ServiceInstance.objects.create(
        service_order=order,
        service_type=ServiceInstance.ServiceType.VPS,
        status=ServiceInstance.InstanceStatus.PROVISIONING,
        vps=vps,
        spec_snapshot=build_spec(order),
        provisioned_at=timezone.now(),
        provisioned_by=operator,
    )
    ips = _allocate_order_ips(order, pop, instance, vps=vps)
    ip_note = f'，已分配 IP：{", ".join(str(ip.address) for ip in ips)}' if ips else ''
    return instance, f'已在节点创建虚机 {vm_name}（VMID {vmid}）{ip_note}'


_PROVISIONERS = {
    ServiceOrder.BusinessType.VPS: _provision_vps,
    ServiceOrder.BusinessType.EDGE_GRE: _provision_edge,
    ServiceOrder.BusinessType.EDGE_WG: _provision_edge,
    ServiceOrder.BusinessType.EDGE_SOCKS5: _provision_edge,
    ServiceOrder.BusinessType.IP: _provision_ip,
    ServiceOrder.BusinessType.BANDWIDTH: _provision_bandwidth,
}


# ═══════════════════════════════════════════════════════════════
# 主编排
# ═══════════════════════════════════════════════════════════════

def _can_auto_provision(order: ServiceOrder) -> bool:
    """仅对审批通过或正在等待人工处理的新开服务尝试自动开通。"""
    if order.order_type != ServiceOrder.OrderType.NEW:
        return False
    if order.status not in (
        ServiceOrder.OrderStatus.APPROVED,
        ServiceOrder.OrderStatus.PROVISIONING,
    ):
        return False
    if order.instances.exists():
        return False
    return True


def auto_provision(order: ServiceOrder, operator=None) -> dict:
    """工单自动开通编排入口。

    返回 {'auto': bool, 'ok': bool, ...}：
      - auto=False：该工单不走自动开通（POP 手动配置 / 非新开 / 已开通）；
      - auto=True, ok=True：资源足够并已自动下发开通；
      - auto=True, ok=False：资源不足或下发失败，已更新工单并通知管理员。
    """
    from .manual_delivery import (
        ServiceManualDelivery,
        order_ready_for_delivery,
        park_order_for_manual_delivery,
    )

    from .order_pop import apply_product_fulfillment

    apply_product_fulfillment(order, save=True)
    pop = order.preferred_pop
    from .capacity_reservation import order_awaits_manual_stock
    if (
        _can_auto_provision(order)
        and order_ready_for_delivery(order)
        and order_awaits_manual_stock(order)
        and (pop is None or pop.config_mode != pop.ConfigMode.AUTO)
    ):
        shortages = ((order.request_snapshot or {}).get('delivery') or {}).get('stock_shortages') or []
        detail = '；'.join(shortages) or '下单时库存不足'
        return park_order_for_manual_delivery(
            order,
            ServiceManualDelivery.Reason.STOCK,
            f'{detail}。支付已完成，请补齐资源后在工单中手动交付，系统不会自动开通。',
            operator=operator,
            subject='资源不足待人工',
            provision_status=ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
            counted_as_auto=True,
        )
    if pop is None or pop.config_mode != pop.ConfigMode.AUTO:
        if _can_auto_provision(order) and order_ready_for_delivery(order):
            return park_order_for_manual_delivery(
                order,
                ServiceManualDelivery.Reason.POP_MANUAL,
                '当前节点为手动配置，请按交付清单准备资源后回传或后台交付',
                operator=operator,
                subject='节点待人工交付',
            )
        return {'auto': False, 'reason': 'pop_not_auto'}
    if not _can_auto_provision(order):
        return {'auto': False, 'reason': 'not_eligible'}

    # 多实例请求需要逐项选择、核验并记录资源。自动批量创建外部节点资源一旦中途失败
    # 难以保证回滚，因此交由统一的“资源交付”流程处理，避免部分开通却误标为完成。
    if int(order.requested_quantity or 1) > 1:
        if not order_ready_for_delivery(order):
            return {'auto': False, 'reason': 'multi_instance_manual_delivery'}
        reason = (
            f'该工单需要交付 {order.requested_quantity} 个服务实例，'
            '请按交付清单逐项分配并激活资源'
        )
        return park_order_for_manual_delivery(
            order,
            ServiceManualDelivery.Reason.MULTI_INSTANCE,
            reason,
            operator=operator,
            subject='多实例待人工交付',
        )

    if order.business_type == ServiceOrder.BusinessType.BANDWIDTH:
        if not order_ready_for_delivery(order):
            return {'auto': False, 'reason': 'bandwidth_manual_delivery'}
        reason = (
            '独立带宽需先绑定具体 VPS 或边缘加速资源，'
            '请按交付清单建立带宽分配后完成工单'
        )
        return park_order_for_manual_delivery(
            order,
            ServiceManualDelivery.Reason.BANDWIDTH,
            reason,
            operator=operator,
            subject='带宽待人工交付',
        )

    if order.business_type == ServiceOrder.BusinessType.EDGE_SOCKS5:
        if not order_ready_for_delivery(order):
            return {'auto': False, 'reason': 'socks5_manual_delivery'}
        reason = (
            'Socks5 服务需要先完成节点侧配置和下发确认，'
            '请按交付清单回传进度，或在资源管理完成后台交付'
        )
        return park_order_for_manual_delivery(
            order,
            ServiceManualDelivery.Reason.SOCKS5,
            reason,
            operator=operator,
            subject='Socks5 待人工交付',
        )

    spec = build_spec(order)
    try:
        check = _check_resources(pop, spec, exclude_order=order)
    except Exception as e:  # noqa: BLE001
        logger.warning('工单 %s 资源判断失败：%s', order.order_no, e, exc_info=True)
        check = {
            'ok': False,
            'error': True,
            'shortages': [f'资源容量校验异常：{e}'],
        }

    # ── 库存不足成交，或当前仍不够：只告警并等待人工交付，不自动开通 ──
    from .manual_delivery import ensure_manual_delivery

    awaits_stock = order_awaits_manual_stock(order)
    if awaits_stock or not capacity_allows(pop, check):
        shortages = '；'.join(check.get('shortages') or []) or '资源不足'
        if awaits_stock and capacity_allows(pop, check):
            message = (
                '下单时库存不足，支付已完成。请补齐资源后在工单中手动交付，系统不会自动开通。'
            )
        else:
            message = (
                f'{shortages}。支付已完成，系统不会自动开通。'
                '请补齐资源后在工单中手动交付给客户。'
            )
        order.auto_provision_status = ServiceOrder.AutoProvisionStatus.INSUFFICIENT
        order.auto_provision_message = message
        order.save(update_fields=['auto_provision_status', 'auto_provision_message', 'updated_at'])
        ensure_manual_delivery(order, ServiceManualDelivery.Reason.STOCK, operator=operator)
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.OTHER,
            description=f'工单 [{order.order_no}] 库存不足，改为人工交付（{shortages}）',
            service_order=order,
        )
        notify_managers(order, '资源不足待人工', message)
        try:
            from .hooks import dispatch_provision_hooks
            dispatch_provision_hooks(
                'provision', order=order,
                extra={'reason': 'insufficient', 'shortages': check.get('shortages') or []},
            )
        except Exception:  # noqa: BLE001
            logger.warning('工单 %s 资源不足钩子分发失败', order.order_no, exc_info=True)
        return {
            'auto': True,
            'ok': False,
            'reason': 'stock_manual',
            'shortages': check.get('shortages') or [],
        }

    # ── 资源足够：按业务类型自动下发开通 ──────────────────────
    provisioner = _PROVISIONERS.get(order.business_type)
    if provisioner is None:
        return {'auto': False, 'reason': 'unsupported_business_type'}

    # 在调用外部节点前持久化“开通中”状态。若节点调用或资源激活失败，工单仍保留
    # 可人工介入/重试的状态，而不会继续显示为已审批但没有任何履约痕迹。
    order.status = ServiceOrder.OrderStatus.PROVISIONING
    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PROVISIONING
    note_oversell = oversell_note(pop, check)
    order.auto_provision_message = (
        f'正在自动分配并下发资源{("；" + note_oversell) if note_oversell else ""}'
    )
    order.save(update_fields=[
        'status', 'auto_provision_status', 'auto_provision_message', 'updated_at',
    ])

    try:
        with transaction.atomic():
            instance, note = provisioner(order, pop, operator)
            # 边缘加速预分配后保持「待配置」：等客户补全协议并下发生效后再激活。
            if not _edge_pending_customer_config(instance):
                try:
                    instance.activate(operator=operator)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f'服务实例激活联动失败：{exc}') from exc

            ServiceAllocation.objects.create(
                service_instance=instance,
                action_type=ServiceAllocation.ActionType.ALLOCATE,
                description=f'自动开通：{note}',
                operator=operator,
            )
            order.status = ServiceOrder.OrderStatus.ACTIVE
            order.auto_provision_status = ServiceOrder.AutoProvisionStatus.SUCCESS
            order.auto_provision_message = note
            if not order.service_start:
                order.service_start = timezone.now().date()
            order.save(update_fields=[
                'status', 'auto_provision_status', 'auto_provision_message',
                'service_start', 'updated_at',
            ])
            from .capacity_reservation import consume_reservation
            consume_reservation(order, spec=spec)
            _log_service(
                operator=operator,
                action_type=ServiceOperationLog.ActionType.PROVISION,
                description=f'工单 [{order.order_no}] 自动开通成功：{note}',
                service_order=order,
                service_instance=instance,
            )
            # 生命周期：需求确认 → 测试/正式使用
            try:
                from .lifecycle import (
                    bootstrap_order_lifecycle, mark_requirement_stage,
                    mark_test_stage, mark_usage_stage,
                )
                bootstrap_order_lifecycle(order, submitted_by=order.created_by or operator)
                mark_requirement_stage(order, handled_by=operator, approved_by=operator)
                if instance.is_trial:
                    mark_test_stage(order, instance, handled_by=operator)
                else:
                    mark_usage_stage(order, instance, handled_by=operator)
            except Exception:  # noqa: BLE001
                logger.warning('工单 %s 生命周期写入失败', order.order_no, exc_info=True)
            try:
                from .hooks import dispatch_provision_hooks
                from .notify_helpers import notify_order_event, notify_instance_event
                dispatch_provision_hooks('provision', order=order, instance=instance)
                notify_order_event(order, '自动开通成功', detail=note, event='service_provision')
                notify_instance_event(instance, '自动开通激活', detail=note, event='service_provision')
            except Exception:  # noqa: BLE001
                logger.warning('工单 %s 开通通知/钩子失败', order.order_no, exc_info=True)
        return {'auto': True, 'ok': True, 'instance_no': instance.instance_no, 'note': note}
    except Exception as e:  # noqa: BLE001
        logger.exception('工单 %s 自动开通失败', order.order_no)
        msg = str(e)
        ServiceOrder.objects.filter(pk=order.pk).update(
            auto_provision_status=ServiceOrder.AutoProvisionStatus.FAILED,
            auto_provision_message=msg[:2000],
            updated_at=timezone.now(),
        )
        try:
            from .capacity_reservation import release_reservation
            release_reservation(order, reason='自动开通失败，回滚预占')
        except Exception:  # noqa: BLE001
            logger.warning('工单 %s 开通失败后释放预占失败', order.order_no, exc_info=True)
        try:
            _log_service(
                operator=operator,
                action_type=ServiceOperationLog.ActionType.OTHER,
                description=f'工单 [{order.order_no}] 自动开通失败：{msg}',
                service_order=order,
            )
        except Exception:  # noqa: BLE001
            pass
        notify_managers(order, '自动开通失败', msg)
        return {'auto': True, 'ok': False, 'error': msg}
