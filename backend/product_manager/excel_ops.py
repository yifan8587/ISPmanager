"""产品 / 模版 Excel 导入导出。"""
from __future__ import annotations

from excel_io import (
    as_bool,
    as_decimal,
    as_int,
    cell_str,
    import_code,
    import_result,
    map_choice,
    sheet_rows,
    split_list,
    write_workbook,
)

from resource_manager.excel_location import location_dict, resolve_pop
from resource_manager.models import PopPoint
from system_manager.models import SystemOsTemplate

from .accel import join_accel_types, parse_accel_types
from .models import Product, ProductTemplate, ProductUpgradeRule, TemplatePricing

CATEGORY = {
    'vps': 'vps', 'vps主机': 'vps', 'vps 主机': 'vps', '主机': 'vps',
    'edge': 'edge', '边缘': 'edge', '边缘加速': 'edge', '公网对接': 'edge',
}
CATEGORY_EXPORT = {'vps': 'VPS', 'edge': '边缘加速'}
STATUS = {
    'draft': 'draft', '草稿': 'draft',
    'online': 'online', '已上线': 'online', '上线': 'online',
    'offline': 'offline', '已下线': 'offline', '下线': 'offline',
    'archived': 'archived', '已归档': 'archived', '归档': 'archived',
}
BILLING = {
    'flat': 'flat', '包月': 'flat',
    '95th': '95th', '95': '95th', '95计费': '95th', '95 计费': '95th',
    'avg': 'avg', '平均带宽': 'avg',
    'transfer_quota': 'transfer_quota', '流量包': 'transfer_quota',
}

PRODUCT_HEADERS = [
    '产品编号', '产品名称', '类别', '状态',
    'POP编号', 'POP名称', '大区', '国家', '城市', '可用区',
    '模版编号', '供应商名称',
    'CPU核心', '内存GB', '硬盘GB', '网卡数', '带宽Mbps', 'IP数量', '加速类型',
    '月租价', '信誉值', '安装费', '币种', '计费模式', '流量包GB', '超额单价',
    '允许信誉值支付', '显示示例IP', '示例IP', '预估开通小时', '允许缺货售卖',
    '系统版本', '热卖', '描述',
]

TEMPLATE_HEADERS = [
    '模版编号', '模版名称', '类别', 'CPU核心', '内存GB', '硬盘GB', '网卡数',
    '带宽Mbps', 'IP数量', '加速类型', '启用', '描述',
]

PRICING_HEADERS = ['模版编号', 'POP编号', 'POP名称', '大区', '国家', '城市', '可用区', '月租价', '信誉值', '安装费', '币种']
UPGRADE_HEADERS = ['产品编号', '产品名称', '资源类型', '起始数量', '结束数量', '每单位月租', '每单位信誉值', '启用']

RESOURCE_KIND = {
    'cpu': 'cpu', 'cpu核': 'cpu', 'cpu 核': 'cpu',
    'ram': 'ram', '内存': 'ram', '内存gb': 'ram', '内存(gb)': 'ram',
    'disk': 'disk', '硬盘': 'disk', '硬盘gb': 'disk', '硬盘(gb)': 'disk',
    'bandwidth': 'bandwidth', '带宽': 'bandwidth', '带宽mbps': 'bandwidth', '带宽(mbps)': 'bandwidth',
    'ip': 'ip', 'ip数量': 'ip', 'ip 数量': 'ip',
    'nic': 'nic', '网卡': 'nic', '网卡数量': 'nic',
}


def _pop(code: str) -> PopPoint | None:
    code = cell_str(code)
    if not code:
        return None
    return PopPoint.objects.filter(code=code).first() or PopPoint.objects.filter(name=code).first()


def _template(code: str) -> ProductTemplate | None:
    code = cell_str(code)
    if not code:
        return None
    return (
        ProductTemplate.objects.filter(template_no=code).first()
        or ProductTemplate.objects.filter(name=code).first()
    )


def _supplier(name: str):
    name = cell_str(name)
    if not name:
        return None
    from supplier_manager.models import Supplier
    return Supplier.objects.filter(name=name).first() or Supplier.objects.filter(supplier_no=name).first()


def _os_templates(raw: str):
    items = []
    for token in split_list(raw):
        obj = (
            SystemOsTemplate.objects.filter(code=token).first()
            or SystemOsTemplate.objects.filter(name=token).first()
        )
        if obj:
            items.append(obj)
    return items


def _yn(value, default=False) -> bool:
    result = as_bool(value, default=default)
    return bool(result)


def category_export(value: str) -> str:
    return CATEGORY_EXPORT.get(value or '', value or '')


def resolve_category(raw, existing: str | None = None) -> str:
    """识别 VPS / 边缘加速。更新时类别留空沿用原值，不再默认改成 VPS。"""
    text = cell_str(raw)
    compact = ''.join(text.lower().replace('\u3000', ' ').split())
    if not compact:
        if existing in ('vps', 'edge'):
            return existing
        raise ValueError('类别不能为空，请填写 VPS 或 边缘加速')
    if '边缘' in text or '公网' in text or compact in ('edge', 'edgeaccel'):
        return 'edge'
    if 'vps' in compact or compact == '主机':
        return 'vps'
    mapped = map_choice(text, CATEGORY, '')
    if mapped in ('vps', 'edge'):
        return mapped
    raise ValueError(f'无法识别类别「{text}」，请填写 VPS 或 边缘加速')


def ram_gb_cell(mb) -> int | float:
    value = int(mb or 0)
    if value % 1024 == 0:
        return value // 1024
    return round(value / 1024, 2)


def ram_mb_from_row(row, default_mb: int = 1024) -> int:
    """管理端以 GB 填写；仍兼容旧表的内存MB。存储和节点调用继续使用 MB。"""
    gb_text = cell_str(row.get('内存GB')) or cell_str(row.get('内存(GB)'))
    if gb_text:
        gb = float(gb_text)
        return max(0, int(round(gb * 1024)))
    mb_text = cell_str(row.get('内存MB')) or cell_str(row.get('内存(MB)'))
    if mb_text:
        return as_int(mb_text, default_mb) or default_mb
    return default_mb


def export_products(queryset):
    rows = []
    rule_rows = []
    qs = queryset.select_related(
        'pop_point__city__country__region', 'pop_point__availability_zone',
        'template', 'supplier',
    ).prefetch_related('os_templates', 'upgrade_rules')
    for p in qs:
        os_codes = ','.join(t.code for t in p.os_templates.all() if t.code)
        loc = location_dict(p.pop_point)
        rows.append({
            '产品编号': p.product_no or '',
            '产品名称': p.name,
            '类别': category_export(p.category),
            '状态': p.get_status_display(),
            'POP编号': getattr(p.pop_point, 'code', '') or '',
            'POP名称': loc['POP名称'],
            '大区': loc['大区'],
            '国家': loc['国家'],
            '城市': loc['城市'],
            '可用区': loc['可用区'],
            '模版编号': getattr(p.template, 'template_no', '') or '',
            '供应商名称': getattr(p.supplier, 'name', '') or '',
            'CPU核心': p.cpu_cores,
            '内存GB': ram_gb_cell(p.ram_mb),
            '硬盘GB': p.disk_gb,
            '网卡数': p.nic_count,
            '带宽Mbps': p.bandwidth_mbps,
            'IP数量': p.ip_count,
            '加速类型': p.accel_type or '',
            '月租价': p.monthly_price,
            '信誉值': p.reputation_price,
            '安装费': p.setup_price,
            '币种': p.currency,
            '计费模式': p.get_billing_mode_display(),
            '流量包GB': p.transfer_quota_gb or '',
            '超额单价': p.overage_unit_price,
            '允许信誉值支付': '是' if p.allow_reputation_pay else '否',
            '显示示例IP': '是' if p.sample_ip_visible else '否',
            '示例IP': p.sample_ip or '',
            '预估开通小时': p.estimated_provision_hours or '',
            '允许缺货售卖': '是' if p.allow_backorder else '否',
            '系统版本': os_codes,
            '热卖': '是' if p.is_hot_sale else '否',
            '描述': p.description or '',
        })
        for rule in p.upgrade_rules.all():
            rule_rows.append({
                '产品编号': p.product_no or '',
                '产品名称': p.name,
                '资源类型': rule.get_resource_kind_display(),
                '起始数量': rule.min_qty,
                '结束数量': rule.max_qty if rule.max_qty is not None else '',
                '每单位月租': rule.unit_price,
                '每单位信誉值': rule.unit_reputation,
                '启用': '是' if rule.is_active else '否',
            })
    return write_workbook(
        filename='产品列表.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '1. 「产品」表必填：产品名称、类别（VPS 或 边缘加速），以及 POP编号（或 大区/国家/城市 + POP名称）。'},
                {'说明': '1a. 更新已有产品时，类别留空则保留原类别，不会把边缘加速改成 VPS。内存按 GB 填写。'},
                {'说明': '2. 「产品编号」以表格为准：已有该编号则覆盖更新，没有则按该编号新建，系统不再另生成编号。编号留空时才会自动生成。模版填编号，供应商填名称。'},
                {'说明': '3. 「阶梯定价」表按 产品编号 + 资源类型 + 起始数量 写入超出基础规格的升级档。'},
                {'说明': '4. 资源类型：CPU 核 / 内存(GB) / 硬盘(GB) / 带宽(Mbps) / IP 数量 / 网卡数量。结束数量留空表示不封顶。'},
            ],
            'hints': ['下载模板后保留表头，删除绿色示例行再导入。'],
        }, {
            'name': '产品',
            'headers': PRODUCT_HEADERS,
            'example': [
                '', '示例VPS', 'VPS', '草稿',
                'POP编号', '', '亚洲', '日本', '东京', '',
                '', '',
                1, 1, 40, 1, 5, 1, '',
                10, 0, 0, 'USD', '包月', '', 0,
                '否', '否', '', '', '否', '', '否', '示例行，导入前请删除',
            ],
            'rows': rows,
        }, {
            'name': '阶梯定价',
            'headers': UPGRADE_HEADERS,
            'example': ['产品编号', '示例VPS', 'CPU 核', 1, 4, 2, 0, '是'],
            'rows': rule_rows,
        }],
    )


def import_products(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['产品']), start=2):
        name = cell_str(row.get('产品名称'))
        if not name or name.startswith('示例'):
            skipped += 1
            continue
        try:
            pop = resolve_pop(row)
            product_no = import_code(row.get('产品编号'), label='产品编号')
            obj = Product.objects.filter(product_no=product_no).first() if product_no else None
            category = resolve_category(row.get('类别'), getattr(obj, 'category', None))
            payload = {
                'name': name,
                'category': category,
                'pop_point': pop,
                'template': _template(row.get('模版编号')),
                'supplier': _supplier(row.get('供应商名称')),
                'cpu_cores': as_int(row.get('CPU核心'), 1) or 1,
                'ram_mb': ram_mb_from_row(row),
                'disk_gb': as_int(row.get('硬盘GB'), 40) or 40,
                'nic_count': as_int(row.get('网卡数'), 1) or 1,
                'bandwidth_mbps': as_int(row.get('带宽Mbps'), 0) or 0,
                'ip_count': as_int(row.get('IP数量'), 0) or 0,
                'monthly_price': as_decimal(row.get('月租价'), 0) or 0,
                'reputation_price': as_decimal(row.get('信誉值'), 0) or 0,
                'setup_price': as_decimal(row.get('安装费'), 0) or 0,
                'currency': cell_str(row.get('币种')) or 'USD',
                'billing_mode': map_choice(row.get('计费模式'), BILLING, 'flat'),
                'transfer_quota_gb': as_int(row.get('流量包GB')),
                'overage_unit_price': as_decimal(row.get('超额单价'), 0) or 0,
                'allow_reputation_pay': _yn(row.get('允许信誉值支付')),
                'sample_ip_visible': _yn(row.get('显示示例IP')),
                'sample_ip': cell_str(row.get('示例IP')),
                'estimated_provision_hours': as_int(row.get('预估开通小时')),
                'allow_backorder': _yn(row.get('允许缺货售卖')),
                'is_hot_sale': _yn(row.get('热卖')),
                'description': cell_str(row.get('描述')),
            }
            status = map_choice(row.get('状态'), STATUS, '')
            if status:
                payload['status'] = status
            if category == 'edge':
                types = parse_accel_types(row.get('加速类型') or 'gre,wireguard')
                payload['accel_type'] = join_accel_types(types)
                payload['cpu_cores'] = payload['ram_mb'] = payload['disk_gb'] = payload['nic_count'] = 0
            else:
                payload['accel_type'] = ''
            os_list = _os_templates(row.get('系统版本'))
            from accounts.permissions import get_user_supplier, is_supplier_role, supplier_owns_pop
            if is_supplier_role(user):
                bound = get_user_supplier(user)
                if bound is None:
                    raise ValueError('供应商账号未绑定供应商')
                if not supplier_owns_pop(bound, pop):
                    raise ValueError('只能导入本供应商 POP 上的产品')
                payload['supplier'] = bound
                if obj and obj.supplier_id and obj.supplier_id != bound.pk:
                    raise ValueError('不能导入或修改其他供应商的产品')
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                if user and not obj.created_by_id:
                    obj.created_by = user
                obj.save()
                updated += 1
            else:
                if product_no:
                    payload['product_no'] = product_no
                if user:
                    payload['created_by'] = user
                obj = Product.objects.create(**payload)
                created += 1
            if os_list:
                obj.os_templates.set(os_list)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'第{idx}行：{exc}')

    rule_created = rule_updated = 0
    for idx, row in enumerate(sheet_rows(wb, ['阶梯定价', '阶梯', '升级定价']), start=2):
        product = _product_row(row)
        kind = map_choice(row.get('资源类型'), RESOURCE_KIND, '')
        if not product or not kind:
            if cell_str(row.get('产品编号')) or cell_str(row.get('产品名称')) or cell_str(row.get('资源类型')):
                if cell_str(row.get('产品名称')).startswith('示例'):
                    continue
                errors.append(f'阶梯第{idx}行：找不到产品或资源类型无效')
            continue
        try:
            from accounts.permissions import get_user_supplier, is_supplier_role, supplier_owns_pop
            if is_supplier_role(user):
                bound = get_user_supplier(user)
                if bound is None:
                    raise ValueError('供应商账号未绑定供应商')
                if product.supplier_id and product.supplier_id != bound.pk:
                    raise ValueError('不能修改其他供应商产品的阶梯定价')
                if not product.supplier_id and not supplier_owns_pop(bound, product.pop_point):
                    raise ValueError('只能维护本供应商 POP 上的产品阶梯定价')
            min_qty = as_int(row.get('起始数量'), 0) or 0
            max_qty = as_int(row.get('结束数量'))
            defaults = {
                'max_qty': max_qty,
                'unit_price': as_decimal(row.get('每单位月租'), 0) or 0,
                'unit_reputation': as_decimal(row.get('每单位信誉值'), 0) or 0,
                'is_active': _yn(row.get('启用'), True),
            }
            _, is_new = ProductUpgradeRule.objects.update_or_create(
                product=product, resource_kind=kind, min_qty=min_qty, defaults=defaults,
            )
            if is_new:
                rule_created += 1
            else:
                rule_updated += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'阶梯第{idx}行：{exc}')
    return import_result(
        created, updated, skipped, errors,
        extra={'rules_created': rule_created, 'rules_updated': rule_updated},
    )


def _product_row(row) -> Product | None:
    no = cell_str(row.get('产品编号'))
    if no:
        return Product.objects.filter(product_no=no).first() or Product.objects.filter(name=no).first()
    name = cell_str(row.get('产品名称'))
    if not name or name.startswith('示例'):
        return None
    return Product.objects.filter(name=name).first()


def export_templates(queryset):
    templates = list(queryset.prefetch_related(
        'pricings__pop_point__city__country__region',
        'pricings__pop_point__availability_zone',
    ))
    tpl_rows = []
    price_rows = []
    for t in templates:
        tpl_rows.append({
            '模版编号': t.template_no or '',
            '模版名称': t.name,
            '类别': category_export(t.category),
            'CPU核心': t.cpu_cores,
            '内存GB': ram_gb_cell(t.ram_mb),
            '硬盘GB': t.disk_gb,
            '网卡数': t.nic_count,
            '带宽Mbps': t.bandwidth_mbps,
            'IP数量': t.ip_count,
            '加速类型': t.accel_type or '',
            '启用': '是' if t.is_active else '否',
            '描述': t.description or '',
        })
        for pr in t.pricings.all():
            loc = location_dict(pr.pop_point)
            price_rows.append({
                '模版编号': t.template_no or '',
                'POP编号': getattr(pr.pop_point, 'code', '') or '',
                'POP名称': loc['POP名称'],
                '大区': loc['大区'],
                '国家': loc['国家'],
                '城市': loc['城市'],
                '可用区': loc['可用区'],
                '月租价': pr.monthly_price,
                '信誉值': pr.reputation_price,
                '安装费': pr.setup_price,
                '币种': pr.currency,
            })
    return write_workbook(
        filename='产品模版.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '「模版」表填写规格；「定价」表按 模版编号 + POP编号 写入各节点价格。'},
                {'说明': '「模版编号」以表格为准：已有该编号则覆盖更新，没有则按该编号新建，系统不再另生成编号。编号留空时才按名称匹配或自动生成。类别填 VPS 或 边缘加速；更新时留空则保留原类别。内存按 GB 填写。'},
            ],
            'hints': ['保留表头，删除示例行后导入。'],
        }, {
            'name': '模版',
            'headers': TEMPLATE_HEADERS,
            'example': ['', '示例1C1G', 'VPS', 1, 1, 40, 1, 5, 1, '', '是', '示例行请删除'],
            'rows': tpl_rows,
        }, {
            'name': '定价',
            'headers': PRICING_HEADERS,
            'example': ['模版编号', 'POP编号', '', '亚洲', '日本', '东京', '', 10, 0, 0, 'USD'],
            'rows': price_rows,
        }],
    )


def import_templates(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['模版']), start=2):
        name = cell_str(row.get('模版名称'))
        if not name or name.startswith('示例'):
            skipped += 1
            continue
        try:
            no = import_code(row.get('模版编号'), label='模版编号')
            obj = ProductTemplate.objects.filter(template_no=no).first() if no else None
            category = resolve_category(row.get('类别'), getattr(obj, 'category', None))
            if obj is None and not no:
                obj = ProductTemplate.objects.filter(name=name, category=category).first()
            payload = {
                'name': name,
                'category': category,
                'cpu_cores': as_int(row.get('CPU核心'), 1) or 1,
                'ram_mb': ram_mb_from_row(row),
                'disk_gb': as_int(row.get('硬盘GB'), 40) or 40,
                'nic_count': as_int(row.get('网卡数'), 1) or 1,
                'bandwidth_mbps': as_int(row.get('带宽Mbps'), 0) or 0,
                'ip_count': as_int(row.get('IP数量'), 0) or 0,
                'is_active': _yn(row.get('启用'), True),
                'description': cell_str(row.get('描述')),
            }
            if category == 'edge':
                payload['accel_type'] = join_accel_types(parse_accel_types(row.get('加速类型') or 'gre,wireguard'))
                payload['cpu_cores'] = payload['ram_mb'] = payload['disk_gb'] = payload['nic_count'] = 0
            else:
                payload['accel_type'] = ''
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                if no:
                    payload['template_no'] = no
                if user:
                    payload['created_by'] = user
                obj = ProductTemplate.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'模版第{idx}行：{exc}')

    price_created = price_updated = 0
    for idx, row in enumerate(sheet_rows(wb, ['定价']), start=2):
        tpl = _template(row.get('模版编号'))
        pop = _pop(row.get('POP编号'))
        if not pop:
            try:
                pop = resolve_pop(row, require=False)
            except ValueError as exc:
                errors.append(f'定价第{idx}行：{exc}')
                continue
        if not tpl or not pop:
            if cell_str(row.get('模版编号')) or cell_str(row.get('POP编号')) or cell_str(row.get('POP名称')):
                errors.append(f'定价第{idx}行：模版或 POP 不存在')
            continue
        defaults = {
            'monthly_price': as_decimal(row.get('月租价'), 0) or 0,
            'reputation_price': as_decimal(row.get('信誉值'), 0) or 0,
            'setup_price': as_decimal(row.get('安装费'), 0) or 0,
            'currency': cell_str(row.get('币种')) or 'USD',
        }
        _, is_new = TemplatePricing.objects.update_or_create(
            template=tpl, pop_point=pop, defaults=defaults,
        )
        if is_new:
            price_created += 1
        else:
            price_updated += 1
    return import_result(
        created, updated, skipped, errors,
        extra={'pricing_created': price_created, 'pricing_updated': price_updated},
    )
