from django.contrib import admin

from .models import (
    Supplier, SupplierAPI, SupplierBill, SupplierPopBinding, SupplierResource,
    SupplierSettlement, SupplierSyncLog,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = [
        'supplier_no', 'name', 'supplier_type', 'status',
        'settlement_rate', 'settlement_min_amount', 'rating', 'contact_name', 'created_at',
    ]
    list_filter = ['supplier_type', 'status']
    search_fields = ['supplier_no', 'name', 'company_name', 'contact_name']
    readonly_fields = ['supplier_no', 'created_at', 'updated_at']


@admin.register(SupplierAPI)
class SupplierAPIAdmin(admin.ModelAdmin):
    list_display = ['name', 'supplier', 'base_url', 'auth_type', 'sync_enabled', 'last_sync_status', 'last_sync_at']
    list_filter = ['auth_type', 'sync_enabled', 'is_active']
    search_fields = ['name', 'base_url', 'supplier__name']


@admin.register(SupplierPopBinding)
class SupplierPopBindingAdmin(admin.ModelAdmin):
    list_display = ['supplier', 'pop_point', 'external_pop_id', 'is_active', 'synced_at']
    list_filter = ['is_active']
    search_fields = ['supplier__name', 'external_pop_id', 'external_pop_name']


@admin.register(SupplierResource)
class SupplierResourceAdmin(admin.ModelAdmin):
    list_display = ['supplier', 'resource_type', 'external_id', 'name', 'pop_point', 'sync_status', 'updated_at']
    list_filter = ['resource_type', 'sync_status']
    search_fields = ['external_id', 'name', 'supplier__name']
    readonly_fields = ['created_at', 'updated_at', 'synced_at']


@admin.register(SupplierBill)
class SupplierBillAdmin(admin.ModelAdmin):
    list_display = [
        'bill_no', 'supplier', 'customer_name', 'product_name',
        'source_amount', 'settlement_rate', 'settle_amount', 'status', 'created_at',
    ]
    list_filter = ['status', 'currency']
    search_fields = ['bill_no', 'customer_bill_no', 'customer_name', 'product_name']
    readonly_fields = ['bill_no', 'created_at', 'updated_at']


@admin.register(SupplierSettlement)
class SupplierSettlementAdmin(admin.ModelAdmin):
    list_display = [
        'settlement_no', 'supplier', 'amount', 'currency', 'bill_count',
        'status', 'auto_created', 'settled_at', 'created_at',
    ]
    list_filter = ['status', 'auto_created', 'currency']
    search_fields = ['settlement_no', 'supplier__name']
    readonly_fields = ['settlement_no', 'created_at', 'updated_at']


@admin.register(SupplierSyncLog)
class SupplierSyncLogAdmin(admin.ModelAdmin):
    list_display = ['supplier', 'action', 'result', 'received_count', 'created_count', 'updated_count', 'created_at']
    list_filter = ['action', 'result']
    search_fields = ['supplier__name', 'message']
    readonly_fields = ['created_at']
