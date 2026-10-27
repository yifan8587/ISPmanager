"""POP / IP 块 / IP 地址 / 隧道地址段 Excel 导入导出。"""
from __future__ import annotations

import ipaddress

from excel_io import (
    as_bool,
    as_int,
    cell_str,
    import_code,
    import_result,
    map_choice,
    sheet_rows,
    split_list,
    write_workbook,
)

from customer_manager.models import Customer
from supplier_manager.models import Supplier

from .excel_location import location_dict, resolve_city, resolve_pop, resolve_zone
from .models import (
    Carrier,
    IPAddress,
    IPAttribute,
    IPBlock,
    PopPoint,
    TunnelIPPool,
)

POP_TYPE = {'pop': 'pop', 'pe': 'pe', 'vdc': 'vdc', 'other': 'other', '其他': 'other'}
POP_STATUS = {
    'online': 'online', '在线': 'online',
    'offline': 'offline', '离线': 'offline',
    'maintenance': 'maintenance', '维护中': 'maintenance', '维护': 'maintenance',
    'decommissioned': 'decommissioned', '已下线': 'decommissioned',
}
NET_TYPE = {'idc': 'idc', '家宽': 'home_broadband', 'home_broadband': 'home_broadband', 'home': 'home_broadband'}
ASN = {
    'isp': 'isp', '单isp': 'isp', '单ISP': 'isp',
    'dual_isp': 'dual_isp', '双isp': 'dual_isp', '双ISP': 'dual_isp',
    'non_isp': 'non_isp', '非isp': 'non_isp', '非ISP': 'non_isp',
}
IP_ATTR = {'native': 'native', '原生': 'native', 'broadcast': 'broadcast', '广播': 'broadcast'}
IP_VER = {'v4': 'v4', 'ipv4': 'v4', '4': 'v4', 'v6': 'v6', 'ipv6': 'v6', '6': 'v6'}
IP_STATUS = {
    'available': 'available', '可用': 'available',
    'assigned': 'assigned', '已分配': 'assigned',
    'reserved': 'reserved', '预留': 'reserved',
    'blocked': 'blocked', '封禁': 'blocked',
    'system': 'system', '系统保留': 'system',
}
CFG_MODE = {'auto': 'auto', '自动': 'auto', '自动配置': 'auto', 'manual': 'manual', '手动': 'manual', '手动配置': 'manual'}
DELIVERY = {'public': 'public', '公网直连': 'public', '公网': 'public', 'nat': 'nat', 'nat 交付': 'nat', 'nat交付': 'nat'}
OS_TYPE = {
    'debian': 'debian', 'ubuntu': 'ubuntu', 'centos': 'centos',
    'rhel': 'rhel', 'windows': 'windows', 'windows server': 'windows', 'other': 'other', '其他': 'other',
}

POP_HEADERS = [
    '节点编号', '节点名称', '节点类型', '状态', '大区', '国家', '城市', '可用区',
    '网络类型', 'ASN性质', 'IP属性', '运营商', '供应商名称', '供应商侧POP标识',
    '配置模式', 'IP交付', '管理IP', 'SSH端口', '管理用户名', '管理密码',
    '管理URL', 'API基址', 'API用户名', 'API密码', '总带宽Mbps',
    'CPU核心', '内存GB', '硬盘GB', 'CPU型号', '硬盘描述', '操作系统', '系统版本', '备注',
]
BLOCK_HEADERS = [
    'CIDR', 'POP编号', 'POP名称', '大区', '国家', '城市', '可用区',
    'IP版本', '网关', 'IP属性', '网络类型', 'ASN性质',
    '运营商', '描述', '启用', '解析IP',
]
ADDR_HEADERS = [
    'IP地址', '所属CIDR', 'POP编号', 'POP名称', '大区', '国家', '城市', '可用区',
    '前缀长度', '网关', '状态', '描述',
]
TUNNEL_HEADERS = [
    '名称', '地址段CIDR', 'POP编号', 'POP名称', '大区', '国家', '城市', '可用区',
    'p2p前缀', '客户编号', '启用', '备注',
]


def _yn(value, default=False) -> bool:
    return bool(as_bool(value, default=default))


def find_supplier(name: str) -> Supplier | None:
    name = cell_str(name)
    if not name:
        return None
    return Supplier.objects.filter(name=name).first() or Supplier.objects.filter(supplier_no=name).first()


def find_carriers(raw: str):
    found = []
    for token in split_list(raw):
        obj = Carrier.objects.filter(code=token).first() or Carrier.objects.filter(name=token).first()
        if obj:
            found.append(obj)
    return found


def find_customer(raw: str) -> Customer | None:
    text = cell_str(raw)
    if not text:
        return None
    return Customer.objects.filter(customer_no=text).first() or Customer.objects.filter(name=text).first()


def find_block(cidr: str, pop: PopPoint | None = None) -> IPBlock | None:
    cidr = cell_str(cidr)
    if not cidr:
        return None
    qs = IPBlock.objects.filter(cidr=cidr)
    if pop:
        qs = qs.filter(pop_point=pop)
    return qs.first()


def generate_block_ips(block: IPBlock, limit: int = 65536) -> int:
    try:
        network = ipaddress.ip_network(block.cidr, strict=False)
    except ValueError:
        return 0
    is_v4 = isinstance(network, ipaddress.IPv4Network)
    prefix = network.prefixlen
    total = network.num_addresses
    if is_v4 and prefix >= 31:
        usable = total
    elif is_v4:
        usable = max(0, total - 2)
    else:
        usable = max(0, total - 1)
    if usable > limit:
        raise ValueError(f'{block.cidr} 可用地址超过 {limit}，请拆段后再解析')
    if is_v4 and prefix >= 31:
        ip_list = list(network)
    elif is_v4:
        ip_list = list(network)[1:-1]
    else:
        ip_list = list(network)[1:]
    from .ip_scope import sibling_business_block_ids
    existing = set(
        IPAddress.objects.filter(ip_block_id__in=sibling_business_block_ids(block))
        .values_list('address', flat=True)
    )
    to_create = [
        IPAddress(
            ip_block=block, address=str(ip), prefix_len=prefix,
            gateway=block.gateway, status=IPAddress.IPStatus.AVAILABLE,
        )
        for ip in ip_list if str(ip) not in existing
    ]
    if to_create:
        IPAddress.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)
    return len(to_create)


def export_pops(queryset):
    rows = []
    for p in queryset.select_related('city__country__region', 'availability_zone', 'supplier').prefetch_related('carriers'):
        loc = location_dict(p)
        rows.append({
            '节点编号': p.code or '',
            '节点名称': p.name,
            '节点类型': p.get_pop_type_display(),
            '状态': p.get_status_display(),
            '大区': loc['大区'],
            '国家': loc['国家'],
            '城市': loc['城市'],
            '可用区': loc['可用区'],
            '网络类型': p.get_network_type_display() if hasattr(p, 'get_network_type_display') else (p.network_type or ''),
            'ASN性质': p.get_asn_nature_display() if hasattr(p, 'get_asn_nature_display') else (p.asn_nature or ''),
            'IP属性': p.get_ip_attribute_display() if p.ip_attribute else '原生',
            '运营商': ','.join(c.code or c.name for c in p.carriers.all()),
            '供应商名称': getattr(p.supplier, 'name', '') or '',
            '供应商侧POP标识': p.supplier_external_id or '',
            '配置模式': p.get_config_mode_display(),
            'IP交付': p.get_ip_delivery_mode_display(),
            '管理IP': p.mgmt_ip or '',
            'SSH端口': p.ssh_port or 22,
            '管理用户名': p.mgmt_username or '',
            '管理密码': '',
            '管理URL': p.mgmt_url or '',
            'API基址': p.api_base_url or '',
            'API用户名': p.api_username or '',
            'API密码': '',
            '总带宽Mbps': p.total_bandwidth_mbps or 0,
            'CPU核心': p.cpu_cores or '',
            '内存GB': p.ram_gb or '',
            '硬盘GB': p.disk_gb or '',
            'CPU型号': p.cpu_model or '',
            '硬盘描述': p.disk_info or '',
            '操作系统': p.os_type or '',
            '系统版本': p.os_version or '',
            '备注': p.remark or '',
        })
    return write_workbook(
        filename='POP节点.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '必填：节点名称，以及大区/国家/城市（或已有节点编号）。城市须先在「基础数据库设置」中存在。'},
                {'说明': '「节点编号」以表格为准：已有该编号则覆盖更新，没有则按该编号新建，系统不再另生成编号。编号留空时才按名称和城市匹配或自动生成。密码列仅用于导入，导出留空。运营商填代码，逗号分隔。'},
                {'说明': '兼容旧表：城市列仍可写「国家/城市」或「大区/国家/城市」。'},
            ],
            'hints': ['删除绿色示例行后再导入。'],
        }, {
            'name': 'POP节点',
            'headers': POP_HEADERS,
            'example': [
                '', '示例节点', 'POP', '在线', '亚洲', '日本', '东京', '',
                'IDC', '单ISP', '原生', '', '', '',
                '手动配置', '公网直连', '', 22, '', '',
                '', '', '', '', 1000,
                16, 64, 1000, '', '', '', '', '示例行请删除',
            ],
            'rows': rows,
        }],
    )


def _bound_supplier(user):
    from accounts.permissions import get_user_supplier, is_supplier_role
    if not is_supplier_role(user):
        return None
    bound = get_user_supplier(user)
    if bound is None:
        raise ValueError('供应商账号未绑定供应商')
    return bound


def _assert_pop_owned(user, pop):
    from accounts.permissions import supplier_owns_pop
    bound = _bound_supplier(user)
    if bound is None:
        return None
    if not supplier_owns_pop(bound, pop):
        raise ValueError('只能导入本供应商 POP 上的资源')
    return bound


def import_pops(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['POP节点', '节点']), start=2):
        name = cell_str(row.get('节点名称'))
        if not name or name.startswith('示例'):
            skipped += 1
            continue
        try:
            code = import_code(row.get('节点编号'), label='节点编号')
            city = resolve_city(row)
            obj = PopPoint.objects.filter(code=code).first() if code else None
            if not obj and not code and city:
                obj = PopPoint.objects.filter(name=name, city=city).first()
            if obj and not city:
                city = obj.city
            if not city:
                raise ValueError('找不到城市，请填写大区、国家、城市（或「国家/城市」）')
            payload = {
                'name': name,
                'pop_type': map_choice(row.get('节点类型'), POP_TYPE, 'pop'),
                'status': map_choice(row.get('状态'), POP_STATUS, 'online'),
                'city': city,
                'availability_zone': resolve_zone(city, row.get('可用区')),
                'network_type': map_choice(row.get('网络类型'), NET_TYPE, 'idc'),
                'asn_nature': map_choice(row.get('ASN性质'), ASN, 'isp'),
                'ip_attribute': map_choice(row.get('IP属性'), IP_ATTR, 'native') or IPAttribute.NATIVE,
                'supplier': find_supplier(row.get('供应商名称')),
                'supplier_external_id': cell_str(row.get('供应商侧POP标识')),
                'config_mode': map_choice(row.get('配置模式'), CFG_MODE, 'manual'),
                'ip_delivery_mode': map_choice(row.get('IP交付'), DELIVERY, 'public'),
                'mgmt_ip': cell_str(row.get('管理IP')) or None,
                'ssh_port': as_int(row.get('SSH端口'), 22) or 22,
                'mgmt_username': cell_str(row.get('管理用户名')),
                'mgmt_url': cell_str(row.get('管理URL')),
                'api_base_url': cell_str(row.get('API基址')),
                'api_username': cell_str(row.get('API用户名')),
                'total_bandwidth_mbps': (
                    as_int(row.get('总带宽Mbps'), 1000)
                    if as_int(row.get('总带宽Mbps'), 1000) is not None
                    else 1000
                ),
                'cpu_cores': as_int(row.get('CPU核心')),
                'ram_gb': as_int(row.get('内存GB')),
                'disk_gb': as_int(row.get('硬盘GB')),
                'cpu_model': cell_str(row.get('CPU型号')),
                'disk_info': cell_str(row.get('硬盘描述')),
                'os_type': (
                    map_choice(row.get('操作系统'), OS_TYPE, '')
                    if map_choice(row.get('操作系统'), OS_TYPE, '') in PopPoint.OSType.values
                    else ''
                ),
                'os_version': cell_str(row.get('系统版本')),
                'remark': cell_str(row.get('备注')),
            }
            bound = _bound_supplier(user)
            if bound:
                from accounts.permissions import supplier_owns_pop
                if obj and obj.supplier_id and obj.supplier_id != bound.pk and not supplier_owns_pop(bound, obj):
                    raise ValueError('不能导入或修改其他供应商的 POP')
                payload['supplier'] = bound
            mgmt_pwd = cell_str(row.get('管理密码'))
            api_pwd = cell_str(row.get('API密码'))
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                if mgmt_pwd:
                    obj.mgmt_password = mgmt_pwd
                if api_pwd:
                    obj.api_password = api_pwd
                obj.save()
                updated += 1
            else:
                if code:
                    payload['code'] = code
                if mgmt_pwd:
                    payload['mgmt_password'] = mgmt_pwd
                if api_pwd:
                    payload['api_password'] = api_pwd
                obj = PopPoint.objects.create(**payload)
                created += 1
            carriers = find_carriers(row.get('运营商'))
            if carriers:
                obj.carriers.set(carriers)
            if bound:
                from accounts.permissions import ensure_supplier_pop_binding
                ensure_supplier_pop_binding(obj, bound)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第{idx}行：{exc}')
    return import_result(created, updated, skipped, errors)


def export_ip_blocks(queryset):
    rows = []
    for b in queryset.select_related(
        'pop_point__city__country__region', 'pop_point__availability_zone', 'carrier',
    ):
        loc = location_dict(b.pop_point)
        rows.append({
            'CIDR': b.cidr,
            'POP编号': getattr(b.pop_point, 'code', '') or '',
            'POP名称': loc['POP名称'],
            '大区': loc['大区'],
            '国家': loc['国家'],
            '城市': loc['城市'],
            '可用区': loc['可用区'],
            'IP版本': b.ip_version,
            '网关': b.gateway or '',
            'IP属性': b.get_ip_attribute_display() if b.ip_attribute else '',
            '网络类型': b.network_type or '',
            'ASN性质': b.get_asn_nature_display(),
            '运营商': getattr(b.carrier, 'code', '') or getattr(b.carrier, 'name', '') or '',
            '描述': b.description or '',
            '启用': '是' if b.is_active else '否',
            '解析IP': '',
        })
    return write_workbook(
        filename='IP地址块.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '必填：CIDR，以及 POP编号（或 大区/国家/城市 + POP名称）。同一 POP 下相同 CIDR 则更新。'},
                {'说明': '「解析IP」填「是」时，导入后按 CIDR 生成可用地址（超大段会拒绝）。'},
            ],
            'hints': ['删除示例行后再导入。'],
        }, {
            'name': 'IP地址块',
            'headers': BLOCK_HEADERS,
            'example': [
                '203.0.113.0/24', 'POP编号', '', '亚洲', '日本', '东京', '',
                'v4', '203.0.113.1', '原生', 'idc', '单ISP', '', '示例行请删除', '是', '否',
            ],
            'rows': rows,
        }],
    )


def import_ip_blocks(wb, user=None) -> dict:
    created = updated = skipped = generated = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['IP地址块', '地址块']), start=2):
        cidr = cell_str(row.get('CIDR'))
        if not cidr or cidr.startswith('示例') or '请删除' in cell_str(row.get('描述')):
            skipped += 1
            continue
        try:
            pop = resolve_pop(row)
            _assert_pop_owned(user, pop)
            ipaddress.ip_network(cidr, strict=False)
            obj = find_block(cidr, pop)
            ver = map_choice(row.get('IP版本'), IP_VER, 'v4' if '.' in cidr else 'v6')
            payload = {
                'pop_point': pop,
                'cidr': cidr,
                'ip_version': ver,
                'gateway': cell_str(row.get('网关')) or None,
                'ip_attribute': map_choice(row.get('IP属性'), IP_ATTR, '') or IPAttribute.NATIVE,
                'network_type': map_choice(row.get('网络类型'), NET_TYPE, 'idc'),
                'asn_nature': map_choice(row.get('ASN性质'), ASN, 'isp'),
                'description': cell_str(row.get('描述')),
                'is_active': _yn(row.get('启用'), True),
            }
            carrier_name = cell_str(row.get('运营商'))
            if carrier_name:
                payload['carrier'] = Carrier.objects.filter(code=carrier_name).first() or Carrier.objects.filter(name=carrier_name).first()
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                obj = IPBlock.objects.create(**payload)
                created += 1
            if _yn(row.get('解析IP')):
                generated += generate_block_ips(obj)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第{idx}行：{exc}')
    return import_result(created, updated, skipped, errors, extra={'generated_ips': generated})


def export_ip_addresses(queryset, limit: int = 50000):
    qs = queryset.select_related(
        'ip_block__pop_point__city__country__region',
        'ip_block__pop_point__availability_zone',
    )[:limit]
    rows = []
    for a in qs:
        loc = location_dict(getattr(a.ip_block, 'pop_point', None) if a.ip_block_id else None)
        rows.append({
            'IP地址': a.address,
            '所属CIDR': a.ip_block.cidr if a.ip_block_id else '',
            'POP编号': getattr(getattr(a.ip_block, 'pop_point', None), 'code', '') or '',
            'POP名称': loc['POP名称'],
            '大区': loc['大区'],
            '国家': loc['国家'],
            '城市': loc['城市'],
            '可用区': loc['可用区'],
            '前缀长度': a.prefix_len or '',
            '网关': a.gateway or '',
            '状态': a.get_status_display(),
            '描述': a.description or '',
        })
    return write_workbook(
        filename='IP地址.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '必填：IP地址、所属CIDR；POP 用编号或 大区/国家/城市 + POP名称 定位。'},
                {'说明': '已存在的地址跳过或仅更新描述/网关；不会改已分配地址的归属。'},
            ],
            'hints': ['当前筛选条件下最多导出 5 万条。'],
        }, {
            'name': 'IP地址',
            'headers': ADDR_HEADERS,
            'example': [
                '203.0.113.10', '203.0.113.0/24', 'POP编号', '', '亚洲', '日本', '东京', '',
                24, '203.0.113.1', '可用', '示例行请删除',
            ],
            'rows': rows,
        }],
    )


def import_ip_addresses(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['IP地址']), start=2):
        address = cell_str(row.get('IP地址'))
        if not address or address.startswith('示例') or '请删除' in cell_str(row.get('描述')):
            skipped += 1
            continue
        try:
            ipaddress.ip_address(address)
            pop = resolve_pop(row, require=False)
            block = find_block(row.get('所属CIDR'), pop)
            if not block:
                raise ValueError('找不到所属 IP 地址块，请先导入地址块')
            _assert_pop_owned(user, block.pop_point)
            status = map_choice(row.get('状态'), IP_STATUS, 'available')
            try:
                block_prefix = ipaddress.ip_network(block.cidr, strict=False).prefixlen
            except ValueError:
                block_prefix = 32 if '.' in address else 128
            defaults = {
                'prefix_len': as_int(row.get('前缀长度'), block_prefix) or block_prefix,
                'gateway': cell_str(row.get('网关')) or block.gateway,
                'description': cell_str(row.get('描述')),
            }
            obj = IPAddress.objects.filter(ip_block=block, address=address).first()
            if not obj:
                obj = IPAddress.objects.filter(address=address, ip_block__pop_point=block.pop_point).first()
            if obj:
                if obj.status == IPAddress.IPStatus.ASSIGNED:
                    skipped += 1
                    continue
                obj.prefix_len = defaults['prefix_len'] or obj.prefix_len
                obj.gateway = defaults['gateway'] or obj.gateway
                obj.description = defaults['description'] or obj.description
                if status != 'assigned':
                    obj.status = status
                obj.save()
                updated += 1
            else:
                IPAddress.objects.create(
                    ip_block=block,
                    address=address,
                    status=status if status != 'assigned' else 'available',
                    **defaults,
                )
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第{idx}行：{exc}')
    return import_result(created, updated, skipped, errors)


def export_tunnels(queryset):
    rows = []
    for p in queryset.select_related(
        'pop_point__city__country__region', 'pop_point__availability_zone', 'customer',
    ):
        loc = location_dict(p.pop_point)
        rows.append({
            '名称': p.name,
            '地址段CIDR': p.cidr,
            'POP编号': getattr(p.pop_point, 'code', '') or '',
            'POP名称': loc['POP名称'],
            '大区': loc['大区'],
            '国家': loc['国家'],
            '城市': loc['城市'],
            '可用区': loc['可用区'],
            'p2p前缀': p.point_prefixlen,
            '客户编号': getattr(p.customer, 'customer_no', '') or '',
            '启用': '是' if p.is_active else '否',
            '备注': p.remark or '',
        })
    return write_workbook(
        filename='隧道地址段.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '必填：名称、地址段CIDR，以及 POP编号（或 大区/国家/城市 + POP名称）。'},
                {'说明': 'p2p 前缀填 30 或 31。客户编号留空表示公共池。导入默认不同步节点。'},
            ],
            'hints': ['删除示例行后再导入。'],
        }, {
            'name': '隧道地址段',
            'headers': TUNNEL_HEADERS,
            'example': [
                'edge-tunnel', '10.21.0.0/24', 'POP编号', '', '亚洲', '日本', '东京', '',
                30, '', '是', '示例行请删除',
            ],
            'rows': rows,
        }],
    )


def import_tunnels(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['隧道地址段', '隧道']), start=2):
        name = cell_str(row.get('名称'))
        cidr = cell_str(row.get('地址段CIDR'))
        if not name or name.startswith('示例') or not cidr or '请删除' in cell_str(row.get('备注')):
            skipped += 1
            continue
        try:
            pop = resolve_pop(row)
            _assert_pop_owned(user, pop)
            obj = (
                TunnelIPPool.objects.filter(pop_point=pop, cidr=cidr).first()
                or TunnelIPPool.objects.filter(pop_point=pop, name=name).first()
            )
            payload = {
                'pop_point': pop,
                'name': name,
                'cidr': cidr,
                'point_prefixlen': as_int(row.get('p2p前缀'), 30) or 30,
                'customer': find_customer(row.get('客户编号')),
                'is_active': _yn(row.get('启用'), True),
                'remark': cell_str(row.get('备注')),
            }
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.full_clean()
                obj.save()
                updated += 1
            else:
                obj = TunnelIPPool(**payload)
                obj.full_clean()
                obj.save()
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第{idx}行：{exc}')
    return import_result(created, updated, skipped, errors)
