"""产品展示用的网络类型 / ASN / IP 属性，取自所属 POP 点。"""
from __future__ import annotations

from .models import AsnNature, IPAttribute, NetworkType, NetworkTypeOption

ASN_DISPLAY_ORDER = (AsnNature.NON_ISP, AsnNature.ISP, AsnNature.DUAL_ISP)
IP_ATTR_DISPLAY_ORDER = (IPAttribute.BROADCAST, IPAttribute.NATIVE)


def sort_by_display_order(items, order, key='value'):
    rank = {code: index for index, code in enumerate(order)}
    return sorted(items, key=lambda item: rank.get(item.get(key) if isinstance(item, dict) else item, 50))


def asn_nature_label(code: str) -> str:
    if not code:
        return ''
    return dict(AsnNature.choices).get(code, code)


class NetworkAttrCatalog:
    """一次查出网络类型目录，供列表序列化复用。"""

    def __init__(self):
        self.labels = dict(NetworkType.choices)
        self.order = {}
        self._pop_cache = {}
        for opt in NetworkTypeOption.objects.all():
            self.labels[opt.code] = opt.name
            self.order[opt.code] = opt.sort_order

    def network_type_label(self, code: str) -> str:
        if not code:
            return ''
        return self.labels.get(code, code)

    def collect(self, pop):
        """读取 POP 点自身的网络类型、ASN 和 IP 属性。"""
        empty = {
            'network_types': [],
            'asn_natures': [],
            'ip_attributes': [],
            'network_type': '',
            'network_type_label': '',
            'asn_nature': '',
            'asn_nature_label': '',
        }
        if pop is None:
            return empty
        cached = self._pop_cache.get(pop.pk)
        if cached is not None:
            return cached

        nt = (getattr(pop, 'network_type', '') or '').strip()
        asn = (getattr(pop, 'asn_nature', '') or '').strip()
        attr = (getattr(pop, 'ip_attribute', '') or '').strip()
        network_types = [{
            'value': nt,
            'label': self.network_type_label(nt),
        }] if nt else []
        asn_natures = [{
            'value': asn,
            'label': asn_nature_label(asn),
        }] if asn else []
        ip_attributes = [attr] if attr else []
        result = {
            'network_types': network_types,
            'asn_natures': asn_natures,
            'ip_attributes': ip_attributes,
            'network_type': nt,
            'network_type_label': network_types[0]['label'] if network_types else '',
            'asn_nature': asn,
            'asn_nature_label': asn_natures[0]['label'] if asn_natures else '',
        }
        self._pop_cache[pop.pk] = result
        return result


def catalog_from_context(context: dict | None) -> NetworkAttrCatalog:
    ctx = context if context is not None else {}
    cat = ctx.get('_network_attr_catalog')
    if cat is None:
        cat = NetworkAttrCatalog()
        ctx['_network_attr_catalog'] = cat
    return cat


def collect_product_network_attrs(product, context: dict | None = None, *, refresh: bool = False) -> dict:
    cat = catalog_from_context(context)
    if refresh:
        cat._pop_cache.pop(getattr(product, 'pop_point_id', None), None)
        if hasattr(product, '_ip_block_network_attrs'):
            del product._ip_block_network_attrs
    cache = getattr(product, '_ip_block_network_attrs', None)
    if cache is None:
        pop = product.pop_point if getattr(product, 'pop_point_id', None) else None
        cache = cat.collect(pop)
        product._ip_block_network_attrs = cache
    return cache
