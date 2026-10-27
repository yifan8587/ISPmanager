"""产品计费模式（C2 骨架）。

定义模式与展示/快照。月结仍按 ``monthly_price`` 出基础月租；
95 / 平均 / 流量包的超额明细由 C3 流量归档出账。
"""
from __future__ import annotations

from decimal import Decimal

from .models import Product

OVERAGE_UNIT_LABELS = {
    Product.BillingMode.FLAT: '',
    Product.BillingMode.P95: 'Mbps',
    Product.BillingMode.AVG: 'Mbps',
    Product.BillingMode.TRANSFER_QUOTA: 'GB',
}


def _as_decimal(value) -> str:
    return str(Decimal(str(value or 0)))


def billing_profile(product: Product | None) -> dict:
    if product is None:
        return {
            'billing_mode': Product.BillingMode.FLAT,
            'billing_mode_label': '包月',
            'transfer_quota_gb': None,
            'overage_unit_price': '0',
            'overage_unit_label': '',
            'commit_mbps': 0,
        }
    mode = product.billing_mode or Product.BillingMode.FLAT
    return {
        'billing_mode': mode,
        'billing_mode_label': product.get_billing_mode_display(),
        'transfer_quota_gb': product.transfer_quota_gb,
        'overage_unit_price': _as_decimal(product.overage_unit_price),
        'overage_unit_label': OVERAGE_UNIT_LABELS.get(mode, ''),
        'commit_mbps': int(product.bandwidth_mbps or 0),
    }


def validate_billing_mode_fields(attrs: dict, instance: Product | None = None) -> dict:
    mode = attrs.get('billing_mode', getattr(instance, 'billing_mode', Product.BillingMode.FLAT))
    quota = attrs.get(
        'transfer_quota_gb',
        getattr(instance, 'transfer_quota_gb', None) if instance is not None else None,
    )
    bandwidth = attrs.get(
        'bandwidth_mbps',
        getattr(instance, 'bandwidth_mbps', 0) if instance is not None else 0,
    )
    errors = {}
    if mode == Product.BillingMode.TRANSFER_QUOTA and not quota:
        errors['transfer_quota_gb'] = '流量包产品必须填写每月包含流量（GB）'
    if mode in (Product.BillingMode.P95, Product.BillingMode.AVG) and int(bandwidth or 0) <= 0:
        errors['bandwidth_mbps'] = '95 / 平均计费必须填写承诺带宽'
    return errors


def billing_profile_from_order(order) -> dict:
    """优先用工单下单快照，没有则回退到当前产品。"""
    snapshot = getattr(order, 'request_snapshot', None) or {}
    quoted = (snapshot.get('quote') or {}).get('billing')
    if isinstance(quoted, dict) and quoted.get('billing_mode'):
        return quoted
    product_snap = snapshot.get('product') or {}
    if product_snap.get('billing_mode'):
        return {
            'billing_mode': product_snap.get('billing_mode'),
            'billing_mode_label': product_snap.get('billing_mode_label') or '',
            'transfer_quota_gb': product_snap.get('transfer_quota_gb'),
            'overage_unit_price': str(product_snap.get('overage_unit_price') or '0'),
            'overage_unit_label': product_snap.get('overage_unit_label') or '',
            'commit_mbps': int(product_snap.get('commit_mbps') or 0),
        }
    return billing_profile(getattr(order, 'product', None))
