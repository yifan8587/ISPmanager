from django.contrib import admin

from .models import (
    PortalCountryShortcut, Product, ProductPublishLog, ProductSpec, ProductTemplate,
    ProductUpgradeRule, TemplatePricing,
)


@admin.register(PortalCountryShortcut)
class PortalCountryShortcutAdmin(admin.ModelAdmin):
    list_display = ['country', 'display_name', 'sort_order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['display_name', 'country__name']
    ordering = ['sort_order', 'id']


@admin.register(ProductSpec)
class ProductSpecAdmin(admin.ModelAdmin):
    list_display = ['spec_no', 'name', 'category', 'cpu_cores', 'ram_mb', 'disk_gb', 'pop_point', 'supplier', 'is_active']
    list_filter = ['category', 'is_active', 'accel_type']
    search_fields = ['spec_no', 'name']
    readonly_fields = ['spec_no', 'created_at', 'updated_at']


class TemplatePricingInline(admin.TabularInline):
    model = TemplatePricing
    extra = 0


@admin.register(ProductTemplate)
class ProductTemplateAdmin(admin.ModelAdmin):
    list_display = ['template_no', 'name', 'category', 'cpu_cores', 'ram_mb', 'disk_gb', 'is_active']
    list_filter = ['category', 'is_active', 'accel_type']
    search_fields = ['template_no', 'name']
    readonly_fields = ['template_no', 'created_at', 'updated_at']
    inlines = [TemplatePricingInline]


class ProductUpgradeRuleInline(admin.TabularInline):
    model = ProductUpgradeRule
    extra = 0


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['product_no', 'name', 'category', 'status', 'billing_mode', 'pop_point', 'supplier', 'monthly_price', 'currency', 'is_hot_sale', 'sample_ip_visible']
    list_filter = ['category', 'status', 'billing_mode', 'is_featured', 'is_hot_sale']
    search_fields = ['product_no', 'name']
    readonly_fields = ['product_no', 'created_at', 'updated_at', 'published_at', 'offline_at']
    inlines = [ProductUpgradeRuleInline]


@admin.register(TemplatePricing)
class TemplatePricingAdmin(admin.ModelAdmin):
    list_display = ['template', 'pop_point', 'monthly_price', 'reputation_price', 'currency', 'is_active']
    list_filter = ['is_active', 'currency']


@admin.register(ProductPublishLog)
class ProductPublishLogAdmin(admin.ModelAdmin):
    list_display = ['product', 'action', 'from_status', 'to_status', 'operator', 'created_at']
    list_filter = ['action']
    readonly_fields = ['created_at']
