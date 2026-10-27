"""Excel 中大区 / 国家 / 城市 / 可用区 / POP 的解析与导出字段。"""
from __future__ import annotations

from excel_io import cell_str

from .models import AvailabilityZone, City, Country, PopPoint, Region

REGION_ALIASES = {
    'apac': 'apac', '亚洲': 'apac', '亚州': 'apac', '亚太': 'apac',
    'asia': 'apac', 'asia-pacific': 'apac', 'asia pacific': 'apac',
    'europe': 'europe', '欧洲': 'europe', 'eu': 'europe',
    'africa': 'africa', '非洲': 'africa',
    'americas': 'americas', '北美洲': 'americas', '北美州': 'americas', '美洲': 'americas',
    'america': 'americas', 'north america': 'americas', 'north-america': 'americas',
    'south_america': 'south_america', '南美洲': 'south_america',
    'south america': 'south_america', 'south-america': 'south_america',
    'oceania': 'oceania', '大洋洲': 'oceania',
}


def location_dict(pop: PopPoint | None) -> dict:
    if not pop:
        return {'大区': '', '国家': '', '城市': '', '可用区': '', 'POP名称': ''}
    city = getattr(pop, 'city', None)
    country = getattr(city, 'country', None) if city else None
    region = getattr(country, 'region', None) if country else None
    zone = getattr(pop, 'availability_zone', None)
    return {
        '大区': getattr(region, 'name', '') or '',
        '国家': getattr(country, 'name', '') or '',
        '城市': getattr(city, 'name', '') or '',
        '可用区': (getattr(zone, 'code', None) or getattr(zone, 'name', None) or '') if zone else '',
        'POP名称': pop.name or '',
    }


def find_region(raw) -> Region | None:
    text = cell_str(raw)
    if not text:
        return None
    code = REGION_ALIASES.get(text.lower()) or REGION_ALIASES.get(text)
    if code:
        obj = Region.objects.filter(code=code).first()
        if obj:
            return obj
    return (
        Region.objects.filter(code__iexact=text).first()
        or Region.objects.filter(name=text).first()
        or Region.objects.filter(name_en__iexact=text).first()
    )


def find_country(raw, region: Region | None = None) -> Country | None:
    text = cell_str(raw)
    if not text:
        return None
    qs = Country.objects.all()
    if region:
        qs = qs.filter(region=region)
    return (
        qs.filter(name=text).first()
        or qs.filter(name_en__iexact=text).first()
    )


def find_city_combined(raw) -> City | None:
    text = cell_str(raw)
    if not text:
        return None
    parts = [p.strip() for p in text.replace('／', '/').split('/') if p.strip()]
    name = parts[-1]
    qs = City.objects.filter(name=name)
    if len(parts) >= 2:
        qs = qs.filter(country__name=parts[-2])
    if len(parts) >= 3:
        qs = qs.filter(country__region__name=parts[-3])
    return qs.first() or City.objects.filter(name_en__iexact=name).first()


def resolve_city(row: dict) -> City | None:
    region = find_region(row.get('大区'))
    country = find_country(row.get('国家'), region)
    city_raw = cell_str(row.get('城市'))
    if city_raw and ('/' in city_raw or '／' in city_raw):
        return find_city_combined(city_raw)
    if city_raw:
        qs = City.objects.filter(name=city_raw)
        if country:
            qs = qs.filter(country=country)
        elif region:
            qs = qs.filter(country__region=region)
        return qs.first() or City.objects.filter(name_en__iexact=city_raw).first()
    return None


def resolve_zone(city: City | None, raw) -> AvailabilityZone | None:
    text = cell_str(raw)
    if not city or not text:
        return None
    return (
        city.availability_zones.filter(code=text).first()
        or city.availability_zones.filter(name=text).first()
    )


def _first(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = cell_str(row.get(key))
        if value:
            return value
    return ''


def resolve_pop(
    row: dict,
    *,
    code_keys: tuple[str, ...] = ('POP编号', '节点编号'),
    name_keys: tuple[str, ...] = ('POP名称', '节点名称'),
    require: bool = True,
) -> PopPoint | None:
    code = _first(row, code_keys)
    name = _first(row, name_keys)
    city = resolve_city(row)
    pop = None
    if code:
        pop = PopPoint.objects.filter(code=code).first() or PopPoint.objects.filter(name=code).first()
    if not pop and name:
        qs = PopPoint.objects.filter(name=name)
        if city:
            qs = qs.filter(city=city)
        pop = qs.first()
    if not pop:
        if require:
            raise ValueError('找不到 POP，请填写 POP编号，或填写大区/国家/城市 + POP名称')
        return None
    if city and pop.city_id and pop.city_id != city.id:
        raise ValueError(f'POP {pop.code or pop.name} 属于「{pop.city}」，与填写的城市不一致')
    region = find_region(row.get('大区'))
    country = find_country(row.get('国家'), region)
    if region and pop.city_id and pop.city.country.region_id != region.id:
        raise ValueError(f'POP {pop.code or pop.name} 不在大区「{region.name}」')
    if country and pop.city_id and pop.city.country_id != country.id:
        raise ValueError(f'POP {pop.code or pop.name} 不在国家「{country.name}」')
    zone_text = cell_str(row.get('可用区'))
    if zone_text and pop.availability_zone_id:
        zone = pop.availability_zone
        if zone_text not in (zone.code or '', zone.name or ''):
            raise ValueError(f'POP {pop.code or pop.name} 的可用区是「{zone.code or zone.name}」，与填写不一致')
    return pop
