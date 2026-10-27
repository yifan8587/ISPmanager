"""
supplier_manager/serializers.py
供应商管理模块 DRF 序列化器
"""
from django.contrib.auth.models import User
from rest_framework import serializers

from .models import (
    Supplier, SupplierAPI, SupplierBill, SupplierPopBinding, SupplierResource,
    SupplierSettlement, SupplierSyncLog,
)


# ═══════════════════════════════════════════════════════════════
# § 供应商
# ═══════════════════════════════════════════════════════════════

class SupplierSerializer(serializers.ModelSerializer):
    supplier_type_label = serializers.CharField(source='get_supplier_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    api_count = serializers.SerializerMethodField()
    resource_count = serializers.SerializerMethodField()
    product_count = serializers.SerializerMethodField()
    pop_count = serializers.SerializerMethodField()
    account_count = serializers.SerializerMethodField()
    pending_settle_amount = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = [
            'id', 'supplier_no', 'name', 'short_name',
            'supplier_type', 'supplier_type_label', 'status', 'status_label',
            'contact_name', 'contact_phone', 'contact_email', 'contact_im',
            'company_name', 'unified_credit_code', 'country', 'address', 'website',
            'settlement_currency', 'settlement_rate', 'settlement_min_amount',
            'balance', 'credit_limit', 'rating',
            'remark', 'created_at', 'updated_at',
            'api_count', 'resource_count', 'product_count', 'pop_count',
            'account_count', 'pending_settle_amount',
        ]
        read_only_fields = ['supplier_no', 'created_at', 'updated_at']

    def validate_settlement_rate(self, value):
        if value is None:
            return value
        if value < 0 or value > 100:
            raise serializers.ValidationError('结算比例须在 0 到 100 之间')
        return value

    def validate_settlement_min_amount(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('满额结算金额不能为负数')
        return value

    def get_api_count(self, obj):
        return obj.apis.count()

    def get_resource_count(self, obj):
        return obj.resources.count()

    def get_product_count(self, obj):
        return obj.products.count()

    def get_pop_count(self, obj):
        return obj.pop_bindings.count()

    def get_account_count(self, obj):
        return obj.portal_accounts.count()

    def get_pending_settle_amount(self, obj):
        from django.db.models import Sum
        from .models import SupplierBill
        total = obj.bills.filter(
            status=SupplierBill.Status.PENDING, settlement__isnull=True,
        ).aggregate(t=Sum('settle_amount'))['t']
        return total or 0


class SupplierBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'supplier_no', 'name', 'supplier_type', 'status']


# ═══════════════════════════════════════════════════════════════
# § 供应商 API
# ═══════════════════════════════════════════════════════════════

class SupplierAPISerializer(serializers.ModelSerializer):
    auth_type_label = serializers.CharField(source='get_auth_type_display', read_only=True)
    last_sync_status_label = serializers.CharField(source='get_last_sync_status_display', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    # 写入用敏感字段；读取时不回显明文
    api_key = serializers.CharField(required=False, allow_blank=True, write_only=True)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)
    has_secret = serializers.SerializerMethodField()

    class Meta:
        model = SupplierAPI
        fields = [
            'id', 'supplier', 'supplier_name', 'name', 'base_url',
            'auth_type', 'auth_type_label', 'api_key', 'api_header_name',
            'username', 'password', 'extra_headers',
            'resources_path', 'products_path', 'pops_path',
            'verify_ssl', 'timeout_seconds',
            'sync_enabled', 'sync_interval_minutes',
            'sync_resources', 'sync_products', 'sync_pops', 'is_active',
            'last_sync_at', 'last_sync_status', 'last_sync_status_label', 'last_sync_message',
            'has_secret', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'last_sync_at', 'last_sync_status', 'last_sync_message',
            'created_at', 'updated_at',
        ]

    def get_has_secret(self, obj):
        return bool(obj.api_key or obj.password)

    def update(self, instance, validated_data):
        # 空字符串不覆盖已有密钥
        if validated_data.get('api_key', None) == '':
            validated_data.pop('api_key')
        if validated_data.get('password', None) == '':
            validated_data.pop('password')
        return super().update(instance, validated_data)


# ═══════════════════════════════════════════════════════════════
# § 供应商 POP 关联
# ═══════════════════════════════════════════════════════════════

class SupplierPopBindingSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    pop_code = serializers.CharField(source='pop_point.code', read_only=True)

    class Meta:
        model = SupplierPopBinding
        fields = [
            'id', 'supplier', 'supplier_name', 'pop_point', 'pop_name', 'pop_code',
            'external_pop_id', 'external_pop_name', 'is_active', 'remark',
            'synced_at', 'created_at',
        ]
        read_only_fields = ['synced_at', 'created_at']


# ═══════════════════════════════════════════════════════════════
# § 供应商资源
# ═══════════════════════════════════════════════════════════════

class SupplierResourceSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    resource_type_label = serializers.CharField(source='get_resource_type_display', read_only=True)
    sync_status_label = serializers.CharField(source='get_sync_status_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True, allow_null=True)

    class Meta:
        model = SupplierResource
        fields = [
            'id', 'supplier', 'supplier_name', 'api', 'resource_type', 'resource_type_label',
            'external_id', 'name',
            'cpu_cores', 'ram_mb', 'disk_gb', 'bandwidth_mbps', 'ip_count',
            'region_text', 'pop_external_id', 'pop_point', 'pop_name', 'mapped_product_id',
            'cost_price', 'currency', 'raw_payload',
            'sync_status', 'sync_status_label', 'synced_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ['synced_at', 'created_at', 'updated_at', 'mapped_product_id']


# ═══════════════════════════════════════════════════════════════
# § 同步日志
# ═══════════════════════════════════════════════════════════════

class SupplierSyncLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source='get_action_display', read_only=True)
    result_label = serializers.CharField(source='get_result_display', read_only=True)
    operator_name = serializers.CharField(source='operator.username', read_only=True, allow_null=True)

    class Meta:
        model = SupplierSyncLog
        fields = [
            'id', 'supplier', 'api', 'action', 'action_label', 'result', 'result_label',
            'received_count', 'created_count', 'updated_count', 'message',
            'operator', 'operator_name', 'created_at',
        ]


# ── Action 序列化器 ───────────────────────────────────────────

class PullResourcesSerializer(serializers.Serializer):
    api_id = serializers.IntegerField()
    resource_type = serializers.ChoiceField(
        choices=SupplierResource.ResType.choices, default=SupplierResource.ResType.VPS,
    )


class MapResourceSerializer(serializers.Serializer):
    pop_point_id = serializers.IntegerField(required=False)
    to_product = serializers.BooleanField(default=False)


class SupplierAccountSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source='profile.role', read_only=True)
    phone = serializers.CharField(source='profile.phone', read_only=True)
    remark = serializers.CharField(source='profile.remark', read_only=True)
    supplier_id = serializers.IntegerField(source='profile.supplier_id', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'is_active', 'last_login', 'date_joined',
            'role', 'phone', 'remark', 'supplier_id',
        ]


class SupplierAccountBindSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)


class SupplierAccountUnbindSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class SupplierAccountWriteSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    first_name = serializers.CharField(required=False, allow_blank=True, default='')
    phone = serializers.CharField(required=False, allow_blank=True, default='')
    remark = serializers.CharField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)


class SupplierAccountUpdateSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    first_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    remark = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    password = serializers.CharField(required=False, write_only=True, min_length=8)


class SupplierBillSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    settlement_no = serializers.CharField(source='settlement.settlement_no', read_only=True, default='')

    class Meta:
        model = SupplierBill
        fields = [
            'id', 'bill_no', 'supplier', 'supplier_name',
            'customer_billing', 'customer_bill_no', 'customer_name',
            'product', 'product_name', 'service_order', 'bill_month',
            'source_amount', 'settlement_rate', 'settle_amount', 'currency',
            'status', 'status_label', 'settlement', 'settlement_no',
            'paid_at', 'remark', 'created_at',
        ]


class SupplierSettlementSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True, default='')
    settled_by_name = serializers.CharField(source='settled_by.username', read_only=True, default='')

    class Meta:
        model = SupplierSettlement
        fields = [
            'id', 'settlement_no', 'supplier', 'supplier_name',
            'amount', 'currency', 'bill_count', 'status', 'status_label',
            'auto_created', 'remark',
            'created_by', 'created_by_name', 'settled_by', 'settled_by_name',
            'settled_at', 'created_at',
        ]
