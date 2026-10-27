"""供应商列表 Excel 导入导出。"""
from __future__ import annotations

from excel_io import (
    as_decimal,
    as_int,
    cell_str,
    import_code,
    import_result,
    map_choice,
    sheet_rows,
    write_workbook,
)

from .models import Supplier

TYPE = {
    'idc': 'idc', 'idc机房': 'idc', 'idc 机房': 'idc',
    'cloud': 'cloud', '云厂商': 'cloud',
    'isp': 'isp', '运营商': 'isp', '运营商/isp': 'isp',
    'agent': 'agent', '代理商': 'agent',
    'other': 'other', '其他': 'other',
}
STATUS = {
    'active': 'active', '合作中': 'active',
    'suspended': 'suspended', '暂停合作': 'suspended',
    'disabled': 'disabled', '停用': 'disabled',
    'pending': 'pending', '待审核': 'pending',
}

HEADERS = [
    '供应商编号', '供应商名称', '简称', '类型', '状态',
    '联系人', '联系电话', '邮箱', 'IM',
    '公司全称', '统一社会信用代码', '国家/地区', '地址', '官网',
    '结算币种', '结算比例', '满额结算', '授信额度', '信誉评分', '备注',
]


def export_suppliers(queryset):
    rows = []
    for item in queryset.order_by('supplier_no', 'id'):
        rows.append({
            '供应商编号': item.supplier_no or '',
            '供应商名称': item.name,
            '简称': item.short_name or '',
            '类型': item.get_supplier_type_display(),
            '状态': item.get_status_display(),
            '联系人': item.contact_name or '',
            '联系电话': item.contact_phone or '',
            '邮箱': item.contact_email or '',
            'IM': item.contact_im or '',
            '公司全称': item.company_name or '',
            '统一社会信用代码': item.unified_credit_code or '',
            '国家/地区': item.country or '',
            '地址': item.address or '',
            '官网': item.website or '',
            '结算币种': item.settlement_currency or '',
            '结算比例': item.settlement_rate,
            '满额结算': item.settlement_min_amount,
            '授信额度': item.credit_limit,
            '信誉评分': item.rating,
            '备注': item.remark or '',
        })
    return write_workbook(
        filename='供应商列表.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '1. 「供应商」表必填供应商名称。「供应商编号」以表格为准：已有该编号则覆盖更新，没有则按该编号新建，系统不再另生成编号。编号留空时才按名称匹配或自动生成。'},
                {'说明': '2. 类型：IDC 机房 / 云厂商 / 运营商/ISP / 代理商 / 其他。状态：合作中 / 暂停合作 / 停用 / 待审核。'},
                {'说明': '3. 请删除绿色示例行后再导入。'},
            ],
            'hints': ['下载模板后保留表头，删除绿色示例行再导入。'],
        }, {
            'name': '供应商',
            'headers': HEADERS,
            'example': [
                '', '示例供应商', '示例', 'IDC 机房', '待审核',
                '张三', '', '', '',
                '示例科技有限公司', '', '中国', '', '',
                'CNY', 90, 100, 0, 0, '示例行，导入前请删除',
            ],
            'rows': rows,
        }],
    )


def import_suppliers(wb, user=None) -> dict:
    created = updated = skipped = 0
    errors = []
    for idx, row in enumerate(sheet_rows(wb, ['供应商']), start=2):
        name = cell_str(row.get('供应商名称'))
        if not name or name.startswith('示例'):
            skipped += 1
            continue
        try:
            rating = as_int(row.get('信誉评分'), 0) or 0
            payload = {
                'name': name,
                'short_name': cell_str(row.get('简称')),
                'supplier_type': map_choice(row.get('类型'), TYPE, 'idc'),
                'status': map_choice(row.get('状态'), STATUS, 'pending'),
                'contact_name': cell_str(row.get('联系人')),
                'contact_phone': cell_str(row.get('联系电话')),
                'contact_email': cell_str(row.get('邮箱')),
                'contact_im': cell_str(row.get('IM')),
                'company_name': cell_str(row.get('公司全称')),
                'unified_credit_code': cell_str(row.get('统一社会信用代码')),
                'country': cell_str(row.get('国家/地区')),
                'address': cell_str(row.get('地址')),
                'website': cell_str(row.get('官网')),
                'settlement_currency': cell_str(row.get('结算币种')) or 'CNY',
                'settlement_rate': as_decimal(row.get('结算比例'), 90) or 0,
                'settlement_min_amount': as_decimal(row.get('满额结算'), 100) or 0,
                'credit_limit': as_decimal(row.get('授信额度'), 0) or 0,
                'rating': max(0, min(100, rating)),
                'remark': cell_str(row.get('备注')),
            }
            supplier_no = import_code(row.get('供应商编号'), label='供应商编号')
            if supplier_no:
                obj = Supplier.objects.filter(supplier_no=supplier_no).first()
            else:
                obj = Supplier.objects.filter(name=name).first()
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                if supplier_no:
                    payload['supplier_no'] = supplier_no
                if user and getattr(user, 'is_authenticated', False):
                    payload['created_by'] = user
                Supplier.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'供应商第{idx}行：{exc}')
    return import_result(created, updated, skipped, errors)
