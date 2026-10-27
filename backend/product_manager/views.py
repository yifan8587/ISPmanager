"""
product_manager/views.py
产品管理模块 DRF ViewSets
"""
import django_filters
from django.db.models import Q
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import ProductModulePermissionMixin
from accounts.permissions import (
    apply_supplier_scope,
    deny_supplier_role,
    require_bound_supplier,
    supplier_owned_q,
    supplier_scope_product_q,
)
from excel_io import ExcelIOMixin
from .excel_ops import export_products, export_templates, import_products, import_templates

from . import services
from .models import (
    PortalCountryShortcut, Product, ProductPublishLog, ProductSpec, ProductTemplate,
    ProductUpgradeRule, TemplatePricing,
)
from .serializers import (
    CustomSpecSerializer, PortalCountryShortcutSerializer, ProductDetailSerializer, ProductListSerializer,
    ProductPublishLogSerializer, ProductSpecSerializer, ProductTemplateSerializer,
    ProductUpgradeRuleSerializer, ProductWriteSerializer, PublishActionSerializer,
    TemplateInstantiateSerializer, TemplatePricingSerializer,
)


def _brief_specs(qs):
    return [
        {'id': o.id, 'spec_no': o.spec_no, 'name': o.name, 'category': o.category}
        for o in qs
    ]


class ProductFilter(django_filters.FilterSet):
    """产品多维筛选：类别 / 状态 / POP / 供应商 / 区域·国家·城市 / 网络类型 / IP 属性 / ASN。"""
    network_type = django_filters.CharFilter(method='filter_network_type')
    asn_nature = django_filters.CharFilter(method='filter_asn_nature')
    region = django_filters.NumberFilter(field_name='pop_point__city__country__region')
    country = django_filters.NumberFilter(field_name='pop_point__city__country')
    city = django_filters.NumberFilter(field_name='pop_point__city')
    availability_zone = django_filters.NumberFilter(field_name='pop_point__availability_zone')
    ip_attribute = django_filters.CharFilter(method='filter_ip_attribute')
    hot_sale = django_filters.BooleanFilter(method='filter_hot_sale')

    class Meta:
        model = Product
        fields = [
            'category', 'status', 'pop_point', 'supplier', 'is_featured', 'is_hot_sale',
            'billing_mode', 'network_type', 'asn_nature', 'region', 'country', 'city',
            'availability_zone', 'ip_attribute', 'hot_sale',
        ]

    def filter_network_type(self, queryset, name, value):
        return queryset.filter(pop_point__network_type=value)

    def filter_asn_nature(self, queryset, name, value):
        return queryset.filter(pop_point__asn_nature=value)

    def filter_ip_attribute(self, queryset, name, value):
        return queryset.filter(pop_point__ip_attribute=value)

    def filter_hot_sale(self, queryset, name, value):
        from .hot_sale import HOT_SALE_SOLD_THRESHOLD, annotate_sold_service_count

        qs = annotate_sold_service_count(queryset)
        cond = Q(is_hot_sale=True) | Q(sold_service_count__gte=HOT_SALE_SOLD_THRESHOLD)
        return qs.filter(cond) if value else qs.exclude(cond)


# ═══════════════════════════════════════════════════════════════
# § 产品规格
# ═══════════════════════════════════════════════════════════════

class ProductSpecViewSet(ProductModulePermissionMixin, viewsets.ModelViewSet):
    queryset = ProductSpec.objects.select_related('pop_point', 'supplier').all()
    serializer_class = ProductSpecSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'pop_point', 'supplier', 'is_active', 'accel_type']
    search_fields = ['name', 'spec_no']
    ordering_fields = ['created_at', 'name']

    def get_queryset(self):
        qs = super().get_queryset()
        return apply_supplier_scope(
            self.request, qs, lambda s: supplier_owned_q(s, 'supplier'),
        )

    def perform_create(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品规格')
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def brief(self, request):
        qs = self.get_queryset().filter(is_active=True)
        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        return Response(_brief_specs(qs))


# ═══════════════════════════════════════════════════════════════
# § 产品模版 + 定价
# ═══════════════════════════════════════════════════════════════

class PortalCountryShortcutViewSet(ProductModulePermissionMixin, viewsets.ModelViewSet):
    """门户首页快速查找国家。显示名称留空时使用国家原名。"""
    queryset = PortalCountryShortcut.objects.select_related('country__region').all()
    serializer_class = PortalCountryShortcutSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['is_active', 'country']
    ordering_fields = ['sort_order', 'id']
    ordering = ['sort_order', 'id']

    def perform_create(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理门户快速查找')
        serializer.save()

    def perform_update(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理门户快速查找')
        serializer.save()

    def perform_destroy(self, instance):
        deny_supplier_role(self.request.user, '供应商账号不可管理门户快速查找')
        instance.delete()


class ProductTemplateViewSet(ExcelIOMixin, ProductModulePermissionMixin, viewsets.ModelViewSet):
    excel_export_fn = export_templates
    excel_import_fn = import_templates
    queryset = ProductTemplate.objects.prefetch_related('pricings__pop_point').all()
    serializer_class = ProductTemplateSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'is_active', 'accel_type']
    search_fields = ['name', 'template_no']
    ordering_fields = ['created_at', 'name']

    def perform_create(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        serializer.save()

    def perform_destroy(self, instance):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        instance.delete()

    @action(detail=False, methods=['get'])
    def brief(self, request):
        qs = self.get_queryset().filter(is_active=True)
        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        data = [
            {
                'id': o.id, 'template_no': o.template_no, 'name': o.name, 'category': o.category,
                'cpu_cores': o.cpu_cores, 'ram_mb': o.ram_mb, 'disk_gb': o.disk_gb,
                'nic_count': o.nic_count, 'bandwidth_mbps': o.bandwidth_mbps, 'ip_count': o.ip_count,
                'accel_type': o.accel_type,
            }
            for o in qs
        ]
        return Response(data)


class TemplatePricingViewSet(ProductModulePermissionMixin, viewsets.ModelViewSet):
    queryset = TemplatePricing.objects.select_related('template', 'pop_point').all()
    serializer_class = TemplatePricingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['template', 'pop_point', 'is_active']

    def perform_create(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        serializer.save()

    def perform_update(self, serializer):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        serializer.save()

    def perform_destroy(self, instance):
        deny_supplier_role(self.request.user, '供应商账号不可管理产品模版')
        instance.delete()


# ═══════════════════════════════════════════════════════════════
# § 阶梯升级规则
# ═══════════════════════════════════════════════════════════════

class ProductUpgradeRuleViewSet(ProductModulePermissionMixin, viewsets.ModelViewSet):
    queryset = ProductUpgradeRule.objects.select_related('product').all()
    serializer_class = ProductUpgradeRuleSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['product', 'resource_kind', 'is_active']

    def get_queryset(self):
        from django.db.models import Q

        return apply_supplier_scope(
            self.request, super().get_queryset(),
            lambda s: supplier_owned_q(s, 'product__supplier') | (
                Q(product__supplier__isnull=True) & (
                    Q(product__pop_point__supplier=s) | Q(product__pop_point__supplier_bindings__supplier=s)
                ) if s else Q(pk__in=[])
            ),
        )


# ═══════════════════════════════════════════════════════════════
# § 产品
# ═══════════════════════════════════════════════════════════════

class ProductViewSet(ExcelIOMixin, ProductModulePermissionMixin, viewsets.ModelViewSet):
    excel_export_fn = export_products
    excel_import_fn = import_products
    queryset = Product.objects.select_related(
        'pop_point', 'pop_point__city', 'pop_point__city__country',
        'pop_point__city__country__region', 'pop_point__availability_zone',
        'supplier', 'template',
    ).prefetch_related('upgrade_rules', 'pop_point__ip_blocks', 'os_templates').all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = ProductFilter
    search_fields = ['name', 'product_no']
    ordering_fields = ['sort_order', 'created_at', 'monthly_price']
    ordering = ['sort_order', '-created_at', 'id']

    def get_queryset(self):
        from .hot_sale import annotate_sold_service_count

        qs = annotate_sold_service_count(super().get_queryset())
        return apply_supplier_scope(self.request, qs, supplier_scope_product_q)

    def get_serializer_class(self):
        if self.action == 'list':
            return ProductListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return ProductWriteSerializer
        return ProductDetailSerializer

    def perform_create(self, serializer):
        extra = {'created_by': self.request.user}
        supplier = require_bound_supplier(self.request.user)
        if supplier is not None:
            extra['supplier'] = supplier
        product = serializer.save(**extra)
        ProductPublishLog.objects.create(
            product=product, action=ProductPublishLog.Action.UPDATE,
            to_status=product.status, message='创建产品', operator=self.request.user,
        )

    def _spec(self, product):
        return {
            'cpu_cores': product.cpu_cores, 'ram_mb': product.ram_mb, 'disk_gb': product.disk_gb,
            'bandwidth_mbps': product.bandwidth_mbps, 'ip_count': product.ip_count,
        }

    @action(detail=True, methods=['post'], url_path='refresh-network')
    def refresh_network(self, request, pk=None):
        """按该产品所属 POP 点，重新读取网络类型、ASN 和 IP 属性。"""
        from resource_manager.ip_block_attrs import collect_product_network_attrs

        product = self.get_object()
        if not product.pop_point_id:
            return Response({'detail': '该产品未关联 POP 点，无法读取网络属性'}, status=400)
        attrs = collect_product_network_attrs(product, {'request': request}, refresh=True)
        data = self.get_serializer(product).data
        data['network_refresh'] = {
            'network_types': attrs['network_types'],
            'asn_natures': attrs['asn_natures'],
            'ip_attributes': attrs['ip_attributes'],
        }
        return Response(data)

    # ── 资源容量校验 ──────────────────────────────────────────
    @action(detail=True, methods=['get'])
    def capacity(self, request, pk=None):
        """判断该产品规格在其 POP 点是否资源充足。"""
        product = self.get_object()
        return Response(services.check_spec_against_pop(product.pop_point, self._spec(product)))

    # ── 库存动态估算 ──────────────────────────────────────────
    @action(detail=True, methods=['get'])
    def stock(self, request, pk=None):
        """估算该 POP 点还能开通多少个该产品。"""
        product = self.get_object()
        result = services.estimate_stock(product.pop_point, self._spec(product))
        result['available'] = services.pop_available(product.pop_point)
        result['held'] = services.pop_held(product.pop_point)
        return Response(result)

    # ── 定制规格报价（阶梯定价） ──────────────────────────────
    @action(detail=True, methods=['post'])
    def quote(self, request, pk=None):
        """根据定制规格计算月租 / 信誉值价格，并校验 POP 容量。"""
        product = self.get_object()
        ser = CustomSpecSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        custom = {**self._spec(product), **{k: v for k, v in ser.validated_data.items()}}
        custom['nic_count'] = request.data.get('nic_count', product.nic_count)
        price = services.compute_price(product, custom)
        capacity = services.check_spec_against_pop(product.pop_point, custom)
        stock = services.estimate_stock(product.pop_point, custom)
        return Response({'price': price, 'capacity': capacity, 'stock': stock, 'custom_spec': custom})

    @action(detail=False, methods=['get'], url_path='os-templates')
    def os_templates(self, request):
        """产品管理可选的系统版本，与系统模版目录同一数据库。"""
        from system_manager.models import SystemOsTemplate
        from system_manager.serializers import SystemOsTemplateBriefSerializer

        qs = SystemOsTemplate.objects.filter(enabled=True).order_by('sort_order', 'id')
        return Response(SystemOsTemplateBriefSerializer(qs, many=True).data)

    # ── 发布 / 上线 / 下线 / 归档 ────────────────────────────
    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        product = self.get_object()
        ser = PublishActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        act = ser.validated_data['action']
        msg = ser.validated_data.get('message', '')

        target_status = {
            'publish': Product.Status.ONLINE,
            'online': Product.Status.ONLINE,
            'offline': Product.Status.OFFLINE,
            'archive': Product.Status.ARCHIVED,
        }[act]

        # 上线前做容量校验；库存不足时须填写预估开通时间后才允许上线
        extra_fields = []
        if target_status == Product.Status.ONLINE:
            cap = services.check_spec_against_pop(product.pop_point, self._spec(product))
            hours = ser.validated_data.get('estimated_provision_hours')
            allow_backorder = bool(ser.validated_data.get('allow_backorder'))
            if not cap['ok']:
                if not hours and not product.estimated_provision_hours:
                    return Response(
                        {
                            'detail': 'POP 点资源不足，无法上线。请填写预估开通时间后重试。\n'
                                      + '\n'.join(cap['shortages']),
                            'capacity': cap,
                            'requires_estimated_provision': True,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if hours:
                    product.estimated_provision_hours = hours
                    extra_fields.append('estimated_provision_hours')
                product.allow_backorder = True
                extra_fields.append('allow_backorder')
                if not msg:
                    msg = (
                        f'库存不足仍上线，预估开通 {product.estimated_provision_hours} 小时。'
                        + '；'.join(cap['shortages'])
                    )
            elif allow_backorder and hours:
                product.estimated_provision_hours = hours
                extra_fields.extend(['estimated_provision_hours'])

        from_status = product.status
        product.status = target_status
        now = timezone.now()
        if target_status == Product.Status.ONLINE:
            product.published_at = product.published_at or now
        elif target_status == Product.Status.OFFLINE:
            product.offline_at = now
        product.save(update_fields=['status', 'published_at', 'offline_at', 'updated_at', *extra_fields])

        ProductPublishLog.objects.create(
            product=product, action=act, from_status=from_status,
            to_status=target_status, message=msg, operator=request.user,
        )
        return Response(ProductDetailSerializer(product, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='publish-logs')
    def publish_logs(self, request, pk=None):
        product = self.get_object()
        qs = product.publish_logs.all()[:200]
        return Response(ProductPublishLogSerializer(qs, many=True).data)

    # ── 从模版生成产品 ────────────────────────────────────────
    @action(detail=False, methods=['post'], url_path='from-template')
    def from_template(self, request):
        ser = TemplateInstantiateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            tpl = ProductTemplate.objects.get(pk=data['template_id'])
        except ProductTemplate.DoesNotExist:
            return Response({'detail': '模版不存在'}, status=status.HTTP_404_NOT_FOUND)
        from resource_manager.models import PopPoint
        try:
            pop = PopPoint.objects.get(pk=data['pop_point_id'])
        except PopPoint.DoesNotExist:
            return Response({'detail': 'POP 点不存在'}, status=status.HTTP_404_NOT_FOUND)

        pricing = TemplatePricing.objects.filter(template=tpl, pop_point=pop, is_active=True).first()
        product = Product.objects.create(
            name=data.get('name') or tpl.name,
            category=tpl.category,
            pop_point=pop,
            template=tpl,
            supplier_id=data.get('supplier_id'),
            cpu_cores=tpl.cpu_cores, ram_mb=tpl.ram_mb, disk_gb=tpl.disk_gb,
            nic_count=tpl.nic_count, bandwidth_mbps=tpl.bandwidth_mbps, ip_count=tpl.ip_count,
            accel_type=tpl.accel_type,
            monthly_price=pricing.monthly_price if pricing else 0,
            reputation_price=pricing.reputation_price if pricing else 0,
            setup_price=pricing.setup_price if pricing else 0,
            currency=pricing.currency if pricing else 'USD',
            created_by=request.user,
        )
        if data.get('publish'):
            cap = services.check_spec_against_pop(pop, self._spec(product))
            if cap['ok']:
                product.status = Product.Status.ONLINE
                product.published_at = timezone.now()
                product.save(update_fields=['status', 'published_at', 'updated_at'])
            ProductPublishLog.objects.create(
                product=product, action=ProductPublishLog.Action.PUBLISH,
                to_status=product.status,
                message='从模版发布' + ('' if cap['ok'] else '（资源不足，保持草稿）'),
                operator=request.user,
            )
        return Response(ProductDetailSerializer(product, context={'request': request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def brief(self, request):
        """精简产品列表，用于资源关联产品下拉。

        支持过滤：category、pop_point、status（默认仅上线产品）。
        """
        qs = self.get_queryset()
        category = request.query_params.get('category')
        pop_point = request.query_params.get('pop_point')
        status_param = request.query_params.get('status', 'online')
        if category:
            qs = qs.filter(category=category)
        if pop_point:
            qs = qs.filter(pop_point_id=pop_point)
        if status_param and status_param != 'all':
            qs = qs.filter(status=status_param)
        qs = qs.prefetch_related('pop_point__ip_blocks')
        from resource_manager.ip_block_attrs import collect_product_network_attrs
        data = []
        for p in qs[:500]:
            attrs = collect_product_network_attrs(p)
            data.append({
                'id': p.id,
                'product_no': p.product_no,
                'name': p.name,
                'category': p.category,
                'pop_point': p.pop_point_id,
                'pop_point_name': p.pop_point.name if p.pop_point_id else '',
                'network_type': attrs['network_type'],
                'asn_nature': attrs['asn_nature'],
                'network_type_label': attrs['network_type_label'],
                'asn_nature_label': attrs['asn_nature_label'],
                'status': p.status,
                'currency': p.currency,
                'monthly_price': str(p.monthly_price),
                'billing_mode': p.billing_mode,
                'cpu_cores': p.cpu_cores,
                'ram_mb': p.ram_mb,
                'disk_gb': p.disk_gb,
                'bandwidth_mbps': p.bandwidth_mbps,
                'ip_count': p.ip_count,
            })
        return Response(data)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        qs = self.get_queryset()
        by_status = {s: qs.filter(status=s).count() for s in Product.Status.values}
        by_category = {c: qs.filter(category=c).count() for c in ('vps', 'edge')}
        return Response({
            'total': qs.count(),
            'by_status': by_status,
            'by_category': by_category,
        })


# ═══════════════════════════════════════════════════════════════
# § 产品发布日志（只读）
# ═══════════════════════════════════════════════════════════════

class ProductPublishLogViewSet(ProductModulePermissionMixin, viewsets.ReadOnlyModelViewSet):
    queryset = ProductPublishLog.objects.select_related('product', 'operator').all()
    serializer_class = ProductPublishLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['product', 'action']
    ordering = ['-created_at']
