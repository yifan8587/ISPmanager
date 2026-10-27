"""
supplier_manager/services.py
供应商 API 对接客户端与同步逻辑

实现一个通用的 JSON HTTP 客户端，可：
  - test_connection(api)        测试连通性
  - pull_resources(api, ...)    拉取供应商资源并落库到 SupplierResource
  - map_resource_to_product()   将供应商资源映射/同步为本地产品

供应商 API 千差万别，这里采用「容忍式」字段映射：
  - 列表数据支持顶层数组、或 {results:[...]} / {data:[...]} / {list:[...]}
  - 字段按常见别名归一（id/name/cpu/memory/disk/bandwidth/ip/region/price）
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from .models import (
    Supplier, SupplierAPI, SupplierResource, SupplierSyncLog, record_sync_log,
)


class SupplierAPIError(Exception):
    pass


# ── 字段别名 ───────────────────────────────────────────────────
_ALIASES = {
    'external_id': ['id', 'uuid', 'resource_id', 'instance_id', 'vmid', 'sid'],
    'name': ['name', 'hostname', 'title', 'label'],
    'cpu_cores': ['cpu', 'cpu_cores', 'cores', 'vcpu', 'vcpus'],
    'ram_mb': ['ram', 'ram_mb', 'memory', 'memory_mb', 'mem'],
    'disk_gb': ['disk', 'disk_gb', 'storage', 'storage_gb', 'ssd'],
    'bandwidth_mbps': ['bandwidth', 'bandwidth_mbps', 'bw', 'speed'],
    'ip_count': ['ip_count', 'ips', 'ip', 'ipv4'],
    'region_text': ['region', 'location', 'datacenter', 'dc', 'pop', 'node'],
    'pop_external_id': ['pop_id', 'node_id', 'region_id', 'dc_id'],
    'cost_price': ['price', 'cost', 'monthly', 'month_price', 'amount'],
    'currency': ['currency', 'cur'],
}


def _first(d: dict, keys: list[str]):
    for k in keys:
        if k in d and d[k] not in (None, ''):
            return d[k]
    return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_decimal(v):
    try:
        return Decimal(str(v))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _normalize_ram_mb(v):
    """内存归一为 MB：<=1024 的值视为 GB。"""
    n = _to_int(v)
    if n is None:
        return None
    return n * 1024 if 0 < n <= 1024 else n


class SupplierClient:
    """供应商 API 通用 HTTP 客户端。"""

    def __init__(self, api: SupplierAPI):
        self.api = api
        self.base = (api.base_url or '').rstrip('/')

    def _headers(self) -> dict:
        headers = {'Accept': 'application/json'}
        headers.update(self.api.extra_headers or {})
        at = self.api.auth_type
        if at == SupplierAPI.AuthType.TOKEN and self.api.api_key:
            headers['Authorization'] = f'Token {self.api.api_key}'
        elif at == SupplierAPI.AuthType.BEARER and self.api.api_key:
            headers['Authorization'] = f'Bearer {self.api.api_key}'
        elif at == SupplierAPI.AuthType.APIKEY and self.api.api_key:
            headers[self.api.api_header_name or 'X-Api-Key'] = self.api.api_key
        return headers

    def _auth(self):
        if self.api.auth_type == SupplierAPI.AuthType.BASIC:
            return (self.api.username, self.api.password)
        return None

    def get(self, path: str) -> requests.Response:
        if not self.base:
            raise SupplierAPIError('未配置 API 基址')
        url = path if path.startswith('http') else f'{self.base}/{path.lstrip("/")}'
        try:
            return requests.get(
                url, headers=self._headers(), auth=self._auth(),
                timeout=self.api.timeout_seconds or 20,
                verify=self.api.verify_ssl,
            )
        except requests.RequestException as e:
            raise SupplierAPIError(f'请求失败: {e}') from e

    def test_connection(self) -> dict:
        path = self.api.resources_path or '/'
        resp = self.get(path)
        ok = resp.status_code < 400
        return {
            'success': ok,
            'status_code': resp.status_code,
            'message': '连接成功' if ok else f'HTTP {resp.status_code}',
        }


def _extract_list(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ('results', 'data', 'list', 'items', 'resources'):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _map_item(item: dict) -> dict:
    """把供应商返回的单条资源归一为 SupplierResource 字段字典。"""
    mapped = {}
    for field, keys in _ALIASES.items():
        mapped[field] = _first(item, keys)
    return {
        'external_id': str(mapped['external_id']) if mapped['external_id'] is not None else '',
        'name': str(mapped['name'] or ''),
        'cpu_cores': _to_int(mapped['cpu_cores']),
        'ram_mb': _normalize_ram_mb(mapped['ram_mb']),
        'disk_gb': _to_int(mapped['disk_gb']),
        'bandwidth_mbps': _to_int(mapped['bandwidth_mbps']),
        'ip_count': _to_int(mapped['ip_count']),
        'region_text': str(mapped['region_text'] or ''),
        'pop_external_id': str(mapped['pop_external_id'] or ''),
        'cost_price': _to_decimal(mapped['cost_price']),
        'currency': str(mapped['currency'] or 'CNY'),
    }


def pull_resources(api: SupplierAPI, *, operator=None,
                   resource_type=SupplierResource.ResType.VPS) -> dict:
    """从供应商 API 拉取资源并 upsert 到 SupplierResource。"""
    supplier = api.supplier
    client = SupplierClient(api)
    resp = client.get(api.resources_path or '/api/resources/')
    if resp.status_code >= 400:
        record_sync_log(
            supplier=supplier, api=api,
            action=SupplierSyncLog.Action.PULL_RESOURCES,
            result=SupplierSyncLog.Result.FAILED,
            message=f'HTTP {resp.status_code}', operator=operator,
        )
        _mark_api(api, SupplierAPI.SyncStatus.FAILED, f'HTTP {resp.status_code}')
        raise SupplierAPIError(f'拉取失败 HTTP {resp.status_code}')

    try:
        payload = resp.json()
    except ValueError as e:
        record_sync_log(
            supplier=supplier, api=api,
            action=SupplierSyncLog.Action.PULL_RESOURCES,
            result=SupplierSyncLog.Result.FAILED,
            message=f'响应非 JSON: {e}', operator=operator,
        )
        _mark_api(api, SupplierAPI.SyncStatus.FAILED, '响应非 JSON')
        raise SupplierAPIError('响应非 JSON') from e

    items = _extract_list(payload)
    created = updated = 0
    errors = 0
    now = timezone.now()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        data = _map_item(raw)
        if not data['external_id']:
            errors += 1
            continue
        defaults = {
            'api': api,
            'resource_type': resource_type,
            'name': data['name'],
            'cpu_cores': data['cpu_cores'],
            'ram_mb': data['ram_mb'],
            'disk_gb': data['disk_gb'],
            'bandwidth_mbps': data['bandwidth_mbps'],
            'ip_count': data['ip_count'],
            'region_text': data['region_text'],
            'pop_external_id': data['pop_external_id'],
            'cost_price': data['cost_price'],
            'currency': data['currency'],
            'raw_payload': raw,
            'sync_status': SupplierResource.SyncStatus.SYNCED,
            'synced_at': now,
        }
        _, is_created = SupplierResource.objects.update_or_create(
            supplier=supplier, external_id=data['external_id'], defaults=defaults,
        )
        if is_created:
            created += 1
        else:
            updated += 1

    result = SupplierSyncLog.Result.PARTIAL if errors else SupplierSyncLog.Result.SUCCESS
    msg = f'接收 {len(items)} 条，新增 {created}，更新 {updated}' + (f'，跳过 {errors}' if errors else '')
    record_sync_log(
        supplier=supplier, api=api,
        action=SupplierSyncLog.Action.PULL_RESOURCES,
        result=result, received=len(items), created=created, updated=updated,
        message=msg, operator=operator,
    )
    _mark_api(
        api,
        SupplierAPI.SyncStatus.PARTIAL if errors else SupplierAPI.SyncStatus.SUCCESS,
        msg,
    )
    return {'received': len(items), 'created': created, 'updated': updated, 'errors': errors, 'message': msg}


def _mark_api(api: SupplierAPI, status: str, message: str):
    api.last_sync_at = timezone.now()
    api.last_sync_status = status
    api.last_sync_message = str(message)[:512]
    api.save(update_fields=['last_sync_at', 'last_sync_status', 'last_sync_message', 'updated_at'])


def map_resource_to_product(resource: SupplierResource, *, operator=None):
    """将一条供应商资源映射/同步为本地产品（需已映射 POP 点）。

    返回 (product, created)。若资源未映射 POP，抛出异常。
    """
    from product_manager.models import Product, ProductCategory

    if not resource.pop_point_id:
        raise SupplierAPIError('该资源尚未映射本地 POP 点，无法同步为产品')

    category = ProductCategory.EDGE if resource.resource_type == SupplierResource.ResType.EDGE else ProductCategory.VPS
    defaults = {
        'name': resource.name or f'{resource.supplier.name}-{resource.external_id}',
        'category': category,
        'pop_point': resource.pop_point,
        'supplier': resource.supplier,
        'cpu_cores': resource.cpu_cores or 1,
        'ram_mb': resource.ram_mb or 1024,
        'disk_gb': resource.disk_gb or 20,
        'bandwidth_mbps': resource.bandwidth_mbps or 0,
        'ip_count': resource.ip_count if resource.ip_count is not None else 1,
        'monthly_price': resource.cost_price or 0,
        'currency': resource.currency or 'CNY',
        'created_by': operator,
    }

    created = False
    if resource.mapped_product_id and Product.objects.filter(pk=resource.mapped_product_id).exists():
        product = Product.objects.get(pk=resource.mapped_product_id)
        for k, v in defaults.items():
            if k == 'created_by':
                continue
            setattr(product, k, v)
        product.save()
    else:
        product = Product.objects.create(**defaults)
        created = True
        resource.mapped_product_id = product.pk

    resource.sync_status = SupplierResource.SyncStatus.MAPPED
    resource.save(update_fields=['mapped_product_id', 'sync_status', 'updated_at'])
    record_sync_log(
        supplier=resource.supplier, api=resource.api,
        action=SupplierSyncLog.Action.MAP,
        result=SupplierSyncLog.Result.SUCCESS,
        message=f'资源 {resource.external_id} → 产品 {product.product_no}',
        operator=operator,
    )
    return product, created
