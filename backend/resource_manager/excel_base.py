"""系统基础字典：大区 / 国家 / 城市 / 可用区 / 运营商 Excel 导入导出。"""
from __future__ import annotations

from excel_io import as_decimal, cell_str, import_result, sheet_rows, split_list, write_workbook

from .excel_location import REGION_ALIASES, find_country, find_region, resolve_city
from .models import AvailabilityZone, Carrier, City, Country, Region

REGION_HEADERS = ['区域代码', '名称', '英文名称']
COUNTRY_HEADERS = ['国家名称', '英文名称', '所属大区']
CITY_HEADERS = ['城市名称', '英文名称', '国家', '大区', '时区', '纬度', '经度']
ZONE_HEADERS = ['可用区名称', '代码', '英文名称', '城市', '国家', '大区', '备注']
CARRIER_HEADERS = ['运营商代码', '名称', '覆盖大区', '备注']


def _region_code(raw: str) -> str:
    text = cell_str(raw)
    if not text:
        return ''
    return REGION_ALIASES.get(text.lower()) or REGION_ALIASES.get(text) or text.lower()


def export_base_data(empty: bool = False):
    region_rows = [] if empty else [
        {'区域代码': r.code, '名称': r.name, '英文名称': r.name_en or ''}
        for r in Region.objects.all().order_by('code')
    ]
    country_rows = [] if empty else [
        {'国家名称': c.name, '英文名称': c.name_en or '', '所属大区': c.region.name if c.region_id else ''}
        for c in Country.objects.select_related('region').order_by('region__name', 'name')
    ]
    city_rows = [] if empty else [
        {
            '城市名称': c.name,
            '英文名称': c.name_en or '',
            '国家': c.country.name if c.country_id else '',
            '大区': c.country.region.name if c.country_id else '',
            '时区': c.timezone or '',
            '纬度': c.latitude if c.latitude is not None else '',
            '经度': c.longitude if c.longitude is not None else '',
        }
        for c in City.objects.select_related('country__region').order_by('country__name', 'name')
    ]
    zone_rows = [] if empty else [
        {
            '可用区名称': z.name,
            '代码': z.code or '',
            '英文名称': z.name_en or '',
            '城市': z.city.name if z.city_id else '',
            '国家': z.city.country.name if z.city_id else '',
            '大区': z.city.country.region.name if z.city_id else '',
            '备注': z.remark or '',
        }
        for z in AvailabilityZone.objects.select_related('city__country__region').order_by('city__name', 'name')
    ]
    carrier_rows = [] if empty else [
        {
            '运营商代码': c.code or '',
            '名称': c.name,
            '覆盖大区': ','.join(r.name for r in c.regions.all()),
            '备注': c.remark or '',
        }
        for c in Carrier.objects.prefetch_related('regions').order_by('code', 'name')
    ]
    return write_workbook(
        filename='基础数据.xlsx',
        sheets=[{
            'name': '填写说明',
            'headers': ['说明'],
            'rows': [
                {'说明': '工作表顺序：大区 → 国家 → 城市 → 可用区 → 运营商。导入时按此顺序关联写入。'},
                {'说明': '大区代码：apac / europe / africa / americas / south_america / oceania，也可填中文名称（亚洲、欧洲、非洲、北美洲、南美洲、大洋洲）。'},
                {'说明': '国家必须填写所属大区；城市必须填写国家（建议同时填大区）；可用区必须填写城市。'},
                {'说明': '已存在则按代码/名称更新，否则新建。删除绿色示例行后再导入。'},
            ],
            'hints': ['先下载模板或导出当前数据，按表头填写后整本导入。'],
        }, {
            'name': '大区',
            'headers': REGION_HEADERS,
            'example': ['apac', '亚洲', 'Asia'],
            'rows': region_rows,
        }, {
            'name': '国家',
            'headers': COUNTRY_HEADERS,
            'example': ['日本', 'Japan', '亚洲'],
            'rows': country_rows,
        }, {
            'name': '城市',
            'headers': CITY_HEADERS,
            'example': ['东京', 'Tokyo', '日本', '亚洲', 'Asia/Tokyo', 35.6895, 139.6917],
            'rows': city_rows,
        }, {
            'name': '可用区',
            'headers': ZONE_HEADERS,
            'example': ['东京A区', 'tokyo-a', 'Tokyo AZ-A', '东京', '日本', '亚洲', '示例行请删除'],
            'rows': zone_rows,
        }, {
            'name': '运营商',
            'headers': CARRIER_HEADERS,
            'example': ['NTT', 'NTT Communications', '亚洲', '示例行请删除'],
            'rows': carrier_rows,
        }],
    )


def _skip_example(*values) -> bool:
    return any(cell_str(v).startswith('示例') or '请删除' in cell_str(v) for v in values)


def import_base_data(wb) -> dict:
    created = updated = skipped = 0
    errors = []

    for idx, row in enumerate(sheet_rows(wb, ['大区']), start=2):
        name = cell_str(row.get('名称'))
        code = _region_code(row.get('区域代码') or row.get('代码'))
        if _skip_example(name, code) or (not name and not code):
            skipped += 1
            continue
        try:
            if not name:
                raise ValueError('请填写大区名称')
            if not code:
                raise ValueError('请填写区域代码（如 apac）')
            obj = Region.objects.filter(code=code).first()
            payload = {'code': code, 'name': name, 'name_en': cell_str(row.get('英文名称'))}
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                Region.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'大区第{idx}行：{exc}')

    for idx, row in enumerate(sheet_rows(wb, ['国家']), start=2):
        name = cell_str(row.get('国家名称') or row.get('名称'))
        if _skip_example(name) or not name:
            skipped += 1
            continue
        try:
            region = find_region(row.get('所属大区') or row.get('大区'))
            if not region:
                raise ValueError('找不到所属大区')
            obj = Country.objects.filter(region=region, name=name).first()
            payload = {'region': region, 'name': name, 'name_en': cell_str(row.get('英文名称'))}
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                Country.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'国家第{idx}行：{exc}')

    for idx, row in enumerate(sheet_rows(wb, ['城市']), start=2):
        name = cell_str(row.get('城市名称') or row.get('名称'))
        if _skip_example(name) or not name:
            skipped += 1
            continue
        try:
            region = find_region(row.get('大区'))
            country = find_country(row.get('国家'), region)
            if not country:
                raise ValueError('找不到所属国家，请先导入国家并填写「国家」列')
            obj = City.objects.filter(country=country, name=name).first()
            payload = {
                'country': country,
                'name': name,
                'name_en': cell_str(row.get('英文名称')),
                'timezone': cell_str(row.get('时区')),
                'latitude': as_decimal(row.get('纬度')),
                'longitude': as_decimal(row.get('经度')),
            }
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                City.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'城市第{idx}行：{exc}')

    for idx, row in enumerate(sheet_rows(wb, ['可用区']), start=2):
        name = cell_str(row.get('可用区名称') or row.get('名称'))
        if _skip_example(name, row.get('备注')) or not name:
            skipped += 1
            continue
        try:
            city = resolve_city({
                '大区': row.get('大区'),
                '国家': row.get('国家'),
                '城市': row.get('城市'),
            })
            if not city:
                raise ValueError('找不到所属城市，请先导入城市并填写城市/国家/大区')
            code = cell_str(row.get('代码'))
            obj = (
                (city.availability_zones.filter(code=code).first() if code else None)
                or city.availability_zones.filter(name=name).first()
            )
            payload = {
                'city': city,
                'name': name,
                'code': code,
                'name_en': cell_str(row.get('英文名称')),
                'remark': cell_str(row.get('备注')),
            }
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                AvailabilityZone.objects.create(**payload)
                created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(f'可用区第{idx}行：{exc}')

    for idx, row in enumerate(sheet_rows(wb, ['运营商']), start=2):
        name = cell_str(row.get('名称'))
        code = cell_str(row.get('运营商代码') or row.get('代码'))
        if _skip_example(name, code, row.get('备注')) or (not name and not code):
            skipped += 1
            continue
        try:
            if not name:
                raise ValueError('请填写运营商名称')
            obj = (
                (Carrier.objects.filter(code=code).first() if code else None)
                or Carrier.objects.filter(name=name).first()
            )
            payload = {'name': name, 'code': code or None, 'remark': cell_str(row.get('备注'))}
            if obj:
                for key, value in payload.items():
                    setattr(obj, key, value)
                obj.save()
                updated += 1
            else:
                obj = Carrier.objects.create(**payload)
                created += 1
            regions = []
            for token in split_list(row.get('覆盖大区')):
                found = find_region(token)
                if found:
                    regions.append(found)
            if regions or cell_str(row.get('覆盖大区')):
                obj.regions.set(regions)
        except Exception as exc:  # noqa: BLE001
            errors.append(f'运营商第{idx}行：{exc}')

    return import_result(created, updated, skipped, errors)
