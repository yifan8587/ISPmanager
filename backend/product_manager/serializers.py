"""
product_manager/serializers.py
产品管理模块 DRF 序列化器
"""
from rest_framework import serializers

from resource_manager.ip_block_attrs import collect_product_network_attrs

from . import services
from .accel import accel_types_label, join_accel_types, parse_accel_types
from system_manager.os_templates import assigned_os_templates
from system_manager.models import SystemOsTemplate
from system_manager.serializers import SystemOsTemplateBriefSerializer

from .models import (
    PortalCountryShortcut, Product, ProductCategory, ProductPublishLog, ProductSpec,
    ProductTemplate, ProductUpgradeRule, TemplatePricing,
)


# 规格按两类（VPS / 边缘加速）归一与校验
class SpecValidateMixin:
    """根据 category 归一规格字段：
      - VPS    ：CPU / 内存 / 硬盘 / 网卡 / 带宽 / IP，清空加速类型
      - 边缘加速：仅 带宽 / IP（+ 加速类型），CPU/内存/硬盘/网卡 置 0
    """

    def _resolve_category(self, attrs):
        if attrs.get('category'):
            return attrs['category']
        if self.instance is not None:
            return self.instance.category
        return ProductCategory.VPS

    def validate(self, attrs):
        attrs = super().validate(attrs)
        category = self._resolve_category(attrs)
        if category == ProductCategory.EDGE:
            incoming = attrs.pop('accel_types', None)
            if incoming is None and hasattr(self, 'initial_data'):
                incoming = self.initial_data.get('accel_types')
            if incoming is None:
                incoming = attrs.get('accel_type', None)
                if incoming is None and self.instance is not None:
                    incoming = self.instance.accel_type
            types = parse_accel_types(incoming)
            if not types:
                raise serializers.ValidationError({'accel_type': '边缘加速请至少选择 GRE 或 WireGuard'})
            attrs['accel_type'] = join_accel_types(types)
            for f in ('cpu_cores', 'ram_mb', 'disk_gb', 'nic_count'):
                attrs[f] = 0
        else:
            attrs['accel_type'] = ''
            attrs.pop('accel_types', None)
        return attrs


# ═══════════════════════════════════════════════════════════════
# § 产品规格
# ═══════════════════════════════════════════════════════════════

class ProductSpecSerializer(SpecValidateMixin, serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    accel_type_label = serializers.SerializerMethodField()
    accel_types = serializers.SerializerMethodField()
    pop_name = serializers.CharField(source='pop_point.name', read_only=True, allow_null=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)

    class Meta:
        model = ProductSpec
        fields = [
            'id', 'spec_no', 'name', 'category', 'category_label',
            'pop_point', 'pop_name', 'supplier', 'supplier_name',
            'cpu_cores', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps', 'ip_count',
            'accel_type', 'accel_types', 'accel_type_label',
            'is_active', 'remark', 'created_at', 'updated_at',
        ]
        read_only_fields = ['spec_no', 'created_at', 'updated_at']

    def get_accel_type_label(self, obj):
        return accel_types_label(obj.accel_type)

    def get_accel_types(self, obj):
        return parse_accel_types(obj.accel_type)


# ═══════════════════════════════════════════════════════════════
# § 产品模版 + 定价
# ═══════════════════════════════════════════════════════════════

class TemplatePricingSerializer(serializers.ModelSerializer):
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_code = serializers.CharField(source='pop_point.code', read_only=True)
    ip_delivery_mode = serializers.CharField(
        source='pop_point.ip_delivery_mode', read_only=True, allow_null=True,
    )
    ip_delivery_mode_label = serializers.CharField(
        source='pop_point.get_ip_delivery_mode_display', read_only=True, allow_null=True,
    )

    class Meta:
        model = TemplatePricing
        fields = [
            'id', 'template', 'pop_point', 'pop_name', 'pop_code',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'monthly_price', 'reputation_price', 'setup_price', 'currency',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ProductTemplateSerializer(SpecValidateMixin, serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    accel_type_label = serializers.SerializerMethodField()
    accel_types = serializers.ListField(child=serializers.CharField(), required=False, write_only=True)
    pricings = TemplatePricingSerializer(many=True, read_only=True)

    class Meta:
        model = ProductTemplate
        fields = [
            'id', 'template_no', 'name', 'category', 'category_label', 'spec',
            'cpu_cores', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps', 'ip_count',
            'accel_type', 'accel_types', 'accel_type_label',
            'is_active', 'description', 'pricings', 'created_at', 'updated_at',
        ]
        read_only_fields = ['template_no', 'created_at', 'updated_at']

    def get_accel_type_label(self, obj):
        return accel_types_label(obj.accel_type)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['accel_types'] = parse_accel_types(instance.accel_type)
        return data


# ═══════════════════════════════════════════════════════════════
# § 阶梯升级规则
# ═══════════════════════════════════════════════════════════════

class ProductUpgradeRuleSerializer(serializers.ModelSerializer):
    resource_kind_label = serializers.CharField(source='get_resource_kind_display', read_only=True)

    class Meta:
        model = ProductUpgradeRule
        fields = [
            'id', 'product', 'resource_kind', 'resource_kind_label',
            'min_qty', 'max_qty', 'unit_price', 'unit_reputation',
            'is_active', 'created_at',
        ]
        read_only_fields = ['created_at']


# ═══════════════════════════════════════════════════════════════
# § 产品
# ═══════════════════════════════════════════════════════════════

class ProductListSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_code = serializers.CharField(source='pop_point.code', read_only=True)
    ip_delivery_mode = serializers.CharField(
        source='pop_point.ip_delivery_mode', read_only=True, allow_null=True,
    )
    ip_delivery_mode_label = serializers.CharField(
        source='pop_point.get_ip_delivery_mode_display', read_only=True, allow_null=True,
    )
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, allow_null=True)
    template_name = serializers.CharField(source='template.name', read_only=True, allow_null=True)
    # ── 网络属性（读取所属 POP 点）──
    network_type = serializers.SerializerMethodField()
    network_type_label = serializers.SerializerMethodField()
    asn_nature = serializers.SerializerMethodField()
    asn_nature_label = serializers.SerializerMethodField()
    network_types = serializers.SerializerMethodField()
    asn_natures = serializers.SerializerMethodField()
    # ── 区域 / 国家 / 城市 ──────────────────────────────────────
    city_name = serializers.CharField(source='pop_point.city.name', read_only=True, allow_null=True)
    country_name = serializers.CharField(source='pop_point.city.country.name', read_only=True, allow_null=True)
    region_name = serializers.CharField(source='pop_point.city.country.region.name', read_only=True, allow_null=True)
    region_id = serializers.IntegerField(source='pop_point.city.country.region_id', read_only=True, allow_null=True)
    country_id = serializers.IntegerField(source='pop_point.city.country_id', read_only=True, allow_null=True)
    city_id = serializers.IntegerField(source='pop_point.city_id', read_only=True, allow_null=True)
    zone_id = serializers.IntegerField(source='pop_point.availability_zone_id', read_only=True, allow_null=True)
    zone_name = serializers.CharField(source='pop_point.availability_zone.name', read_only=True, allow_null=True)
    # ── IP 属性（所属 POP 点）────────────────────────────────
    ip_attributes = serializers.SerializerMethodField()
    billing_mode_label = serializers.CharField(source='get_billing_mode_display', read_only=True)
    accel_type_label = serializers.SerializerMethodField()
    accel_types = serializers.SerializerMethodField()
    os_templates = serializers.SerializerMethodField()
    os_template_ids = serializers.SerializerMethodField()
    sold_service_count = serializers.SerializerMethodField()
    hot_sale = serializers.SerializerMethodField()
    supplier_info = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'product_no', 'name', 'category', 'category_label',
            'status', 'status_label', 'pop_point', 'pop_name', 'pop_code',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'network_type', 'network_type_label', 'asn_nature', 'asn_nature_label',
            'network_types', 'asn_natures',
            'region_name', 'country_name', 'city_name', 'zone_name',
            'region_id', 'country_id', 'city_id', 'zone_id',
            'ip_attributes',
            'template', 'template_name', 'supplier', 'supplier_name', 'supplier_info',
            'cpu_cores', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps', 'ip_count',
            'accel_type', 'accel_types', 'accel_type_label',
            'monthly_price', 'reputation_price', 'setup_price', 'currency',
            'billing_mode', 'billing_mode_label', 'transfer_quota_gb', 'overage_unit_price',
            'sample_ip', 'sample_ip_visible',
            'estimated_provision_hours', 'allow_backorder', 'allow_reputation_pay',
            'os_templates', 'os_template_ids',
            'sort_order', 'is_featured', 'is_hot_sale', 'sold_service_count', 'hot_sale',
            'published_at', 'created_at', 'updated_at',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        path = getattr(request, 'path', '') or ''
        is_portal = '/api/portal/' in path
        if is_portal and not instance.sample_ip_visible:
            data['sample_ip'] = ''
        if is_portal:
            from accounts.permissions import get_user_customer
            from customer_manager.reputation import portal_can_see_reputation
            customer = get_user_customer(request.user) if request and getattr(request, 'user', None) else None
            visible = portal_can_see_reputation(customer, instance)
            data['reputation_visible'] = visible
            if not visible:
                data['reputation_price'] = None
        return data

    def _ip_network_attrs(self, obj):
        return collect_product_network_attrs(obj, self.context)

    def get_network_type(self, obj):
        return self._ip_network_attrs(obj)['network_type']

    def get_network_type_label(self, obj):
        return self._ip_network_attrs(obj)['network_type_label']

    def get_asn_nature(self, obj):
        return self._ip_network_attrs(obj)['asn_nature']

    def get_asn_nature_label(self, obj):
        return self._ip_network_attrs(obj)['asn_nature_label']

    def get_network_types(self, obj):
        return self._ip_network_attrs(obj)['network_types']

    def get_asn_natures(self, obj):
        return self._ip_network_attrs(obj)['asn_natures']

    def get_ip_attributes(self, obj):
        return list(self._ip_network_attrs(obj).get('ip_attributes') or [])

    def get_accel_type_label(self, obj):
        return accel_types_label(obj.accel_type)

    def get_accel_types(self, obj):
        return parse_accel_types(obj.accel_type)

    def get_os_templates(self, obj):
        from system_manager.os_templates import product_os_templates

        request = self.context.get('request')
        path = getattr(request, 'path', '') or ''
        portal = '/api/portal/' in path
        if portal:
            items = product_os_templates(obj, deployable_only=True)
            return SystemOsTemplateBriefSerializer(items, many=True).data
        assigned = [item for item in assigned_os_templates(obj) if item.enabled]
        if assigned:
            assigned.sort(key=lambda item: (item.sort_order, item.id))
            return SystemOsTemplateBriefSerializer(assigned, many=True).data
        cache = self.context.get('_enabled_os_templates')
        if cache is None:
            from system_manager.models import SystemOsTemplate
            cache = list(SystemOsTemplate.objects.filter(enabled=True).order_by('sort_order', 'id'))
            self.context['_enabled_os_templates'] = cache
        return SystemOsTemplateBriefSerializer(cache, many=True).data

    def get_os_template_ids(self, obj):
        return [item.pk for item in assigned_os_templates(obj)]

    def get_sold_service_count(self, obj):
        from .hot_sale import product_sold_service_count

        return product_sold_service_count(obj)

    def get_hot_sale(self, obj):
        from .hot_sale import product_is_hot_sale

        return product_is_hot_sale(obj, sold_count=self.get_sold_service_count(obj))

    def get_supplier_info(self, obj):
        from .hot_sale import public_supplier_info

        return public_supplier_info(obj)


class ProductUpgradeRuleBriefSerializer(serializers.ModelSerializer):
    resource_kind_label = serializers.CharField(source='get_resource_kind_display', read_only=True)

    class Meta:
        model = ProductUpgradeRule
        fields = [
            'id', 'resource_kind', 'resource_kind_label',
            'min_qty', 'max_qty', 'unit_price', 'unit_reputation', 'is_active',
        ]


class ProductDetailSerializer(ProductListSerializer):
    upgrade_rules = ProductUpgradeRuleBriefSerializer(many=True, read_only=True)
    capacity = serializers.SerializerMethodField()
    stock = serializers.SerializerMethodField()
    template_pricing = serializers.SerializerMethodField()
    template_spec = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            'description', 'remark', 'offline_at', 'created_by',
            'upgrade_rules', 'capacity', 'stock', 'template_pricing', 'template_spec',
        ]

    def _spec(self, obj):
        return {
            'cpu_cores': obj.cpu_cores, 'ram_mb': obj.ram_mb, 'disk_gb': obj.disk_gb,
            'bandwidth_mbps': obj.bandwidth_mbps, 'ip_count': obj.ip_count,
        }

    def get_capacity(self, obj):
        try:
            return services.check_spec_against_pop(obj.pop_point, self._spec(obj))
        except Exception:  # noqa: BLE001
            return None

    def get_stock(self, obj):
        try:
            return services.estimate_stock(obj.pop_point, self._spec(obj))
        except Exception:  # noqa: BLE001
            return None

    def get_template_pricing(self, obj):
        """该产品所选模版在其 POP 点的定价（模版定价）。"""
        if not obj.template_id:
            return None
        tp = TemplatePricing.objects.filter(
            template_id=obj.template_id, pop_point_id=obj.pop_point_id,
        ).first()
        if not tp:
            return None
        return {
            'monthly_price': str(tp.monthly_price),
            'reputation_price': str(tp.reputation_price),
            'setup_price': str(tp.setup_price),
            'currency': tp.currency,
        }

    def get_template_spec(self, obj):
        """模版基准规格（阶梯升级以此为基准）。"""
        if not obj.template_id:
            return None
        t = obj.template
        return {
            'cpu_cores': t.cpu_cores, 'ram_mb': t.ram_mb, 'disk_gb': t.disk_gb,
            'nic_count': t.nic_count, 'bandwidth_mbps': t.bandwidth_mbps, 'ip_count': t.ip_count,
        }


class ProductWriteSerializer(SpecValidateMixin, serializers.ModelSerializer):
    accel_types = serializers.ListField(child=serializers.CharField(), required=False, write_only=True)
    os_templates = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=SystemOsTemplate.objects.all(),
        required=False,
    )

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'category', 'status', 'pop_point', 'template', 'supplier',
            'cpu_cores', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps', 'ip_count',
            'accel_type', 'accel_types', 'monthly_price', 'reputation_price', 'setup_price', 'currency',
            'billing_mode', 'transfer_quota_gb', 'overage_unit_price',
            'sample_ip', 'sample_ip_visible',
            'estimated_provision_hours', 'allow_backorder', 'allow_reputation_pay',
            'os_templates',
            'sort_order', 'is_featured', 'is_hot_sale', 'description', 'remark',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from .billing_mode import validate_billing_mode_fields
        errors = validate_billing_mode_fields(attrs, instance=self.instance)
        visible = attrs.get('sample_ip_visible', getattr(self.instance, 'sample_ip_visible', False))
        sample_ip = (attrs.get('sample_ip', getattr(self.instance, 'sample_ip', '') or '') or '').strip()
        if visible and not sample_ip:
            errors['sample_ip'] = '勾选显示示例 IP 时必须填写示例地址'
        if 'sample_ip' in attrs:
            attrs['sample_ip'] = sample_ip
        if 'currency' in attrs and attrs['currency']:
            attrs['currency'] = str(attrs['currency']).upper()
        else:
            attrs.setdefault('currency', 'USD')
        request = self.context.get('request')
        user = getattr(request, 'user', None) if request else None
        from accounts.permissions import (
            get_user_supplier,
            is_supplier_role,
            supplier_owns_pop,
        )
        if is_supplier_role(user):
            supplier = get_user_supplier(user)
            if supplier is None:
                errors['supplier'] = '供应商账号未绑定供应商'
            else:
                attrs['supplier'] = supplier
                pop = attrs.get('pop_point') or getattr(self.instance, 'pop_point', None)
                if pop is not None and not supplier_owns_pop(supplier, pop):
                    errors['pop_point'] = '只能使用本供应商的 POP 点'
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ProductPublishLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source='get_action_display', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True, allow_null=True)

    class Meta:
        model = ProductPublishLog
        fields = [
            'id', 'product', 'action', 'action_label',
            'from_status', 'to_status', 'message',
            'operator', 'operator_name', 'created_at',
        ]


# ── Action 序列化器 ───────────────────────────────────────────

class PublishActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=['publish', 'online', 'offline', 'archive'])
    message = serializers.CharField(required=False, allow_blank=True, max_length=512)
    estimated_provision_hours = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    allow_backorder = serializers.BooleanField(required=False, default=False)


class CustomSpecSerializer(serializers.Serializer):
    cpu_cores = serializers.IntegerField(required=False, min_value=0)
    ram_mb = serializers.IntegerField(required=False, min_value=0)
    disk_gb = serializers.IntegerField(required=False, min_value=0)
    nic_count = serializers.IntegerField(required=False, min_value=0)
    bandwidth_mbps = serializers.IntegerField(required=False, min_value=0)
    ip_count = serializers.IntegerField(required=False, min_value=0)


class PortalCountryShortcutSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source='country.name', read_only=True)
    region = serializers.IntegerField(source='country.region_id', read_only=True)
    region_name = serializers.CharField(source='country.region.name', read_only=True)
    label = serializers.CharField(read_only=True)

    class Meta:
        model = PortalCountryShortcut
        fields = (
            'id', 'country', 'country_name', 'region', 'region_name',
            'display_name', 'label', 'sort_order', 'is_active',
            'created_at', 'updated_at',
        )
        read_only_fields = ('created_at', 'updated_at')


class TemplateInstantiateSerializer(serializers.Serializer):
    """从模版生成产品。"""
    template_id = serializers.IntegerField()
    pop_point_id = serializers.IntegerField()
    name = serializers.CharField(required=False, allow_blank=True, max_length=128)
    supplier_id = serializers.IntegerField(required=False)
    publish = serializers.BooleanField(default=False)
