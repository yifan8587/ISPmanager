"""门户热卖：手动勾选，或已采购满阈值个在服服务时自动提示。"""
from __future__ import annotations

from django.db.models import Count, Q

# 「采购 2-3 个服务」按满 2 个在服实例起算
HOT_SALE_SOLD_THRESHOLD = 2
HOT_SALE_COUNTED_STATUSES = ('provisioning', 'active', 'suspended', 'error')


def annotate_sold_service_count(queryset):
    return queryset.annotate(
        sold_service_count=Count(
            'service_orders__instances',
            filter=Q(service_orders__instances__status__in=HOT_SALE_COUNTED_STATUSES),
            distinct=True,
        )
    )


def product_sold_service_count(product) -> int:
    counted = getattr(product, 'sold_service_count', None)
    if counted is not None:
        return int(counted or 0)
    from service_manager.models import ServiceInstance

    return ServiceInstance.objects.filter(
        service_order__product_id=product.pk,
        status__in=HOT_SALE_COUNTED_STATUSES,
    ).count()


def product_is_hot_sale(product, sold_count=None) -> bool:
    if getattr(product, 'is_hot_sale', False):
        return True
    count = product_sold_service_count(product) if sold_count is None else int(sold_count or 0)
    return count >= HOT_SALE_SOLD_THRESHOLD


def public_supplier_info(product) -> dict | None:
    supplier = getattr(product, 'supplier', None)
    if supplier is None:
        return None
    return {
        'id': supplier.pk,
        'name': supplier.name,
        'short_name': supplier.short_name or '',
        'type': supplier.supplier_type,
        'type_label': supplier.get_supplier_type_display(),
        'website': (supplier.website or '').strip(),
        'country': supplier.country or '',
    }
