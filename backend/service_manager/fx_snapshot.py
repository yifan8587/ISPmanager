"""工单汇率快照：下单锁定，支付与月结优先使用，避免出账日漂移。"""
from __future__ import annotations

from django.utils import timezone

from system_manager import currency_services as cs


def resolve_quote_currency(order, *, quote_currency: str | None = None) -> str:
    if quote_currency:
        return str(quote_currency).upper()
    product = getattr(order, 'product', None)
    if product is not None and getattr(product, 'currency', ''):
        return str(product.currency).upper()
    snap = (getattr(order, 'request_snapshot', None) or {}).get('quote') or {}
    if snap.get('currency'):
        return str(snap['currency']).upper()
    return cs.default_currency()


def build_fx_fields(*, quote_currency: str | None = None) -> dict:
    snapshot = cs.snapshot_rates(quote_currency=quote_currency or cs.default_currency())
    return {
        'fx_quote_currency': snapshot['quote_currency'],
        'fx_snapshot': snapshot,
        'fx_locked_at': timezone.now(),
    }


def apply_fx_fields(payload: dict, *, quote_currency: str | None = None) -> dict:
    """给 ``ServiceOrder.objects.create`` / serializer 写入汇率快照字段。"""
    payload.update(build_fx_fields(quote_currency=quote_currency))
    return payload


def lock_order_fx(order, *, quote_currency: str | None = None, force: bool = False):
    """锁定或补锁工单汇率。已锁定且非 force 时原样返回。"""
    if order.fx_locked_at and order.fx_snapshot and not force:
        return order
    currency = resolve_quote_currency(order, quote_currency=quote_currency)
    fields = build_fx_fields(quote_currency=currency)
    order.fx_quote_currency = fields['fx_quote_currency']
    order.fx_snapshot = fields['fx_snapshot']
    order.fx_locked_at = fields['fx_locked_at']
    if order.pk:
        order.save(update_fields=[
            'fx_quote_currency', 'fx_snapshot', 'fx_locked_at', 'updated_at',
        ])
    return order


def order_fx_rates(order) -> dict | None:
    if order is None:
        return None
    return cs.rates_from_snapshot(getattr(order, 'fx_snapshot', None))


def billing_fx_rates(billing) -> dict | None:
    if billing is None:
        return None
    return order_fx_rates(getattr(billing, 'service_order', None))
