from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Customer, CustomerIMAccount, ContactRecord, Contract, CustomerBilling, BillingItem,
    CustomerOperationLog, PaymentTransaction, PaymentWebhookEvent,
    CustomerBalanceLedger,
    ChannelProfile, CustomerReferral, ChannelCommission, ChannelSettlement,
    ChannelPayoutProfile, ChannelFinanceAccount, ChannelFinanceLedger,
    record_operation_log, model_to_dict_simple,
)


# ─────────────────────────────────────────────────────────────
# 通用 Badge 工具
# ─────────────────────────────────────────────────────────────

def make_badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;'
        'border-radius:4px;font-size:12px;white-space:nowrap;">{}</span>',
        color, text,
    )


# ─────────────────────────────────────────────────────────────
# Inline 定义
# ─────────────────────────────────────────────────────────────

class CustomerIMAccountInline(admin.TabularInline):
    model = CustomerIMAccount
    extra = 0
    fields = ('im_type', 'account', 'group', 'label', 'is_primary')
    show_change_link = True


class ContactRecordInline(admin.TabularInline):
    model = ContactRecord
    extra = 0
    fields = ('contact_type', 'contact_time', 'summary', 'handler', 'next_follow_time')
    readonly_fields = ('created_at',)
    show_change_link = True
    ordering = ('-contact_time',)


class ContractInline(admin.TabularInline):
    model = Contract
    extra = 0
    fields = ('contract_no', 'contract_name', 'amount', 'start_date', 'end_date', 'status')
    readonly_fields = ('contract_no',)
    show_change_link = True
    ordering = ('-created_at',)


class BillingInline(admin.TabularInline):
    model = CustomerBilling
    extra = 0
    fields = ('bill_no', 'bill_month', 'amount', 'paid_amount', 'status', 'due_date')
    readonly_fields = ('bill_no',)
    show_change_link = True
    ordering = ('-bill_month',)


class ChannelProfileInline(admin.StackedInline):
    model = ChannelProfile
    extra = 0
    max_num = 1
    fields = (
        'promotion_code', 'promotion_discount_rate', 'commission_rate',
        'reputation_grant_mode', 'reputation_grant_amount',
        'is_active', 'remark',
    )


class BalanceLedgerInline(admin.TabularInline):
    model = CustomerBalanceLedger
    extra = 0
    fields = ('entry_type', 'delta', 'balance_after', 'currency', 'related_billing', 'remark', 'created_at')
    readonly_fields = fields
    ordering = ('-created_at',)
    can_delete = False
    show_change_link = True
    verbose_name_plural = '余额流水'

    def has_add_permission(self, request, obj=None):
        return False


class OperationLogInline(admin.TabularInline):
    model = CustomerOperationLog
    extra = 0
    fields = ('action_type', 'target_model', 'description', 'operator', 'ip_address', 'created_at')
    readonly_fields = fields
    ordering = ('-created_at',)
    max_num = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────
# Customer Admin
# ─────────────────────────────────────────────────────────────

CUSTOMER_TYPE_COLORS = {
    'personal': '#17a2b8',
    'enterprise': '#007bff',
    'channel': '#6f42c1',
    'provider': '#fd7e14',
    'other': '#6c757d',
}

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        'customer_no', 'name', 'customer_type_badge', 'contact_name',
        'contact_phone', 'balance_display',
        'sales_manager', 'created_at',
    )
    list_filter = ('customer_type', 'province', 'city', 'created_at')
    search_fields = (
        'customer_no', 'name', 'contact_name', 'contact_phone',
        'contact_email', 'company_name', 'unified_credit_code',
    )
    readonly_fields = ('customer_no', 'balance', 'balance_display', 'reputation_period', 'created_at', 'updated_at')
    list_per_page = 20
    date_hierarchy = 'created_at'
    save_on_top = True
    inlines = [
        CustomerIMAccountInline, ChannelProfileInline, ContactRecordInline,
        ContractInline, BillingInline, BalanceLedgerInline, OperationLogInline,
    ]

    fieldsets = (
        ('基本信息', {
            'fields': (
                'customer_no', 'name', 'customer_type',
                'account', 'sales_manager', 'remark',
            ),
        }),
        ('联系信息', {
            'fields': ('contact_name', 'contact_phone', 'contact_email'),
        }),
        ('企业信息', {
            'classes': ('collapse',),
            'fields': (
                'company_name', 'company_name_short',
                'unified_credit_code', 'legal_representative',
            ),
        }),
        ('地址信息', {
            'classes': ('collapse',),
            'fields': ('province', 'city', 'address'),
        }),
        ('账务信息', {
            'fields': ('balance', 'balance_display', 'credit_limit'),
        }),
        ('信誉值', {
            'fields': (
                'reputation_mode', 'reputation_quota',
                'reputation_balance', 'reputation_period',
            ),
        }),
        ('时间信息', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    # ── 展示字段 ──────────────────────────────────

    def customer_type_badge(self, obj):
        color = CUSTOMER_TYPE_COLORS.get(obj.customer_type, '#6c757d')
        return make_badge(obj.get_customer_type_display(), color)
    customer_type_badge.short_description = '客户类型'
    customer_type_badge.admin_order_field = 'customer_type'

    def balance_display(self, obj):
        color = '#dc3545' if obj.balance < 0 else '#28a745'
        return format_html(
            '<span style="color:{};font-weight:bold;">¥{}</span>',
            color, f'{obj.balance:,.2f}',
        )
    balance_display.short_description = '账户余额（只读）'
    balance_display.admin_order_field = 'balance'

    # ── 操作日志钩子 ───────────────────────────────

    def save_model(self, request, obj, form, change):
        if change:
            old = Customer.objects.get(pk=obj.pk)
            old_data = model_to_dict_simple(old)
            super().save_model(request, obj, form, change)
            new_data = model_to_dict_simple(obj)
            changed = {
                k: {'old': old_data[k], 'new': new_data[k]}
                for k in old_data if old_data[k] != new_data[k]
            }
            record_operation_log(
                operator=request.user,
                action_type=CustomerOperationLog.ActionType.UPDATE,
                description=f'修改客户 [{obj.customer_no}] {obj.name}，变更字段：{", ".join(changed.keys())}',
                customer=obj,
                target_model=CustomerOperationLog.TargetModel.CUSTOMER,
                target_id=obj.pk,
                old_value=old_data,
                new_value=new_data,
                request=request,
            )
        else:
            super().save_model(request, obj, form, change)
            record_operation_log(
                operator=request.user,
                action_type=CustomerOperationLog.ActionType.CREATE,
                description=f'新建客户 [{obj.customer_no}] {obj.name}',
                customer=obj,
                target_model=CustomerOperationLog.TargetModel.CUSTOMER,
                target_id=obj.pk,
                request=request,
            )

    def delete_model(self, request, obj):
        snapshot = model_to_dict_simple(obj)
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.DELETE,
            description=f'删除客户 [{obj.customer_no}] {obj.name}',
            customer=None,
            target_model=CustomerOperationLog.TargetModel.CUSTOMER,
            target_id=obj.pk,
            old_value=snapshot,
            request=request,
        )
        super().delete_model(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('account', 'sales_manager')


# ─────────────────────────────────────────────────────────────
# ContactRecord Admin
# ─────────────────────────────────────────────────────────────

CONTACT_TYPE_ICONS = {
    'phone': '📞', 'email': '📧', 'visit': '🚗',
    'online': '💬', 'other': '📝',
}


@admin.register(ContactRecord)
class ContactRecordAdmin(admin.ModelAdmin):
    list_display = (
        'customer', 'contact_type_display', 'contact_time',
        'summary_short', 'handler', 'next_follow_time',
    )
    list_filter = ('contact_type', 'contact_time', 'handler')
    search_fields = (
        'customer__name', 'customer__customer_no',
        'summary', 'handler__username',
    )
    readonly_fields = ('created_at',)
    date_hierarchy = 'contact_time'
    list_per_page = 30
    autocomplete_fields = ['customer']

    fieldsets = (
        ('联系信息', {
            'fields': ('customer', 'contact_type', 'contact_time', 'handler'),
        }),
        ('沟通内容', {
            'fields': ('summary', 'next_follow_time'),
        }),
        ('其他', {
            'classes': ('collapse',),
            'fields': ('created_at',),
        }),
    )

    def contact_type_display(self, obj):
        icon = CONTACT_TYPE_ICONS.get(obj.contact_type, '')
        return f'{icon} {obj.get_contact_type_display()}'
    contact_type_display.short_description = '联系方式'

    def summary_short(self, obj):
        return obj.summary[:50] + '…' if len(obj.summary) > 50 else obj.summary
    summary_short.short_description = '沟通摘要'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.CONTACT,
            description=(
                f'{"修改" if change else "新增"}联系记录：'
                f'{obj.customer.name} - {obj.get_contact_type_display()}'
            ),
            customer=obj.customer,
            target_model=CustomerOperationLog.TargetModel.CONTACT_RECORD,
            target_id=obj.pk,
            request=request,
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('customer', 'handler')


# ─────────────────────────────────────────────────────────────
# Contract Admin
# ─────────────────────────────────────────────────────────────

CONTRACT_STATUS_COLORS = {
    'draft': '#6c757d',
    'reviewing': '#ffc107',
    'active': '#28a745',
    'expired': '#dc3545',
    'terminated': '#343a40',
}


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        'contract_no', 'contract_name', 'customer',
        'amount_display', 'status_badge', 'start_date', 'end_date', 'sign_date',
    )
    list_filter = ('status', 'start_date', 'end_date', 'created_by')
    search_fields = (
        'contract_no', 'contract_name',
        'customer__name', 'customer__customer_no',
    )
    readonly_fields = ('contract_no', 'created_at', 'updated_at')
    date_hierarchy = 'start_date'
    list_per_page = 20
    autocomplete_fields = ['customer']
    save_on_top = True

    fieldsets = (
        ('合同基本信息', {
            'fields': ('contract_no', 'contract_name', 'customer', 'status', 'created_by'),
        }),
        ('金额与日期', {
            'fields': ('amount', 'sign_date', 'start_date', 'end_date'),
        }),
        ('附件与备注', {
            'classes': ('collapse',),
            'fields': ('file_url', 'remark'),
        }),
        ('时间信息', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def amount_display(self, obj):
        return format_html(
            '<span style="color:#007bff;font-weight:bold;">¥{}</span>',
            f'{obj.amount:,.2f}',
        )
    amount_display.short_description = '合同金额'
    amount_display.admin_order_field = 'amount'

    def status_badge(self, obj):
        color = CONTRACT_STATUS_COLORS.get(obj.status, '#6c757d')
        return make_badge(obj.get_status_display(), color)
    status_badge.short_description = '合同状态'
    status_badge.admin_order_field = 'status'

    def save_model(self, request, obj, form, change):
        if change:
            old = Contract.objects.get(pk=obj.pk)
            old_data = model_to_dict_simple(old)
            super().save_model(request, obj, form, change)
            new_data = model_to_dict_simple(obj)
            action = (
                CustomerOperationLog.ActionType.CONTRACT_SIGN
                if obj.status == Contract.ContractStatus.ACTIVE and old.status != Contract.ContractStatus.ACTIVE
                else CustomerOperationLog.ActionType.UPDATE
            )
            record_operation_log(
                operator=request.user,
                action_type=action,
                description=f'{"合同签订" if action == CustomerOperationLog.ActionType.CONTRACT_SIGN else "修改合同"} [{obj.contract_no}] {obj.contract_name}',
                customer=obj.customer,
                target_model=CustomerOperationLog.TargetModel.CONTRACT,
                target_id=obj.pk,
                old_value=old_data,
                new_value=new_data,
                request=request,
            )
        else:
            super().save_model(request, obj, form, change)
            record_operation_log(
                operator=request.user,
                action_type=CustomerOperationLog.ActionType.CREATE,
                description=f'新建合同 [{obj.contract_no}] {obj.contract_name}',
                customer=obj.customer,
                target_model=CustomerOperationLog.TargetModel.CONTRACT,
                target_id=obj.pk,
                request=request,
            )

    def delete_model(self, request, obj):
        customer = obj.customer
        snapshot = model_to_dict_simple(obj)
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.DELETE,
            description=f'删除合同 [{obj.contract_no}]',
            customer=customer,
            target_model=CustomerOperationLog.TargetModel.CONTRACT,
            target_id=obj.pk,
            old_value=snapshot,
            request=request,
        )
        super().delete_model(request, obj)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('customer', 'created_by')


# ─────────────────────────────────────────────────────────────
# CustomerBilling Admin
# ─────────────────────────────────────────────────────────────

BILLING_STATUS_COLORS = {
    'unpaid': '#ffc107',
    'paid': '#28a745',
    'overdue': '#dc3545',
    'cancelled': '#6c757d',
}


class BillingItemInline(admin.TabularInline):
    model = BillingItem
    extra = 0
    fields = ('instance_no', 'description', 'period_start', 'period_end',
              'days_used', 'days_in_month', 'is_prorated', 'is_trial',
              'unit_monthly_fee', 'source_currency', 'amount_original', 'currency', 'amount',
              'traffic_report')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CustomerBilling)
class CustomerBillingAdmin(admin.ModelAdmin):
    inlines = [BillingItemInline]
    list_display = (
        'bill_no', 'customer', 'bill_month', 'billing_kind',
        'amount_display', 'paid_amount_display', 'outstanding_display',
        'status_badge', 'due_date', 'payment_method',
    )
    list_filter = ('status', 'billing_kind', 'payment_method', 'bill_month', 'due_date')
    search_fields = ('bill_no', 'customer__name', 'customer__customer_no')
    readonly_fields = (
        'bill_no', 'gross_amount', 'promotion_discount_amount', 'promotion_code',
        'channel_referral', 'outstanding_display',
        'refunded_amount', 'refund_reason', 'refunded_at',
        'created_at', 'updated_at',
    )
    date_hierarchy = 'due_date'
    list_per_page = 20
    autocomplete_fields = ['customer']
    save_on_top = True

    fieldsets = (
        ('账单信息', {
            'fields': ('bill_no', 'customer', 'bill_month', 'billing_kind', 'due_date'),
        }),
        ('金额信息', {
            'fields': (
                'amount', 'gross_amount', 'promotion_discount_amount', 'promotion_code',
                'channel_referral', 'currency', 'auto_generated',
                'paid_amount', 'refunded_amount', 'outstanding_display',
            ),
        }),
        ('支付信息', {
            'fields': ('status', 'payment_method', 'paid_at', 'refund_reason', 'refunded_at'),
        }),
        ('备注', {
            'classes': ('collapse',),
            'fields': ('remark',),
        }),
        ('时间信息', {
            'classes': ('collapse',),
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def amount_display(self, obj):
        return format_html('<span>¥{}</span>', f'{obj.amount:,.2f}')
    amount_display.short_description = '账单金额'
    amount_display.admin_order_field = 'amount'

    def paid_amount_display(self, obj):
        color = '#28a745' if obj.paid_amount >= obj.amount else '#6c757d'
        return format_html(
            '<span style="color:{};">¥{}</span>',
            color, f'{obj.paid_amount:,.2f}',
        )
    paid_amount_display.short_description = '已付金额'

    def outstanding_display(self, obj):
        val = obj.outstanding_amount
        color = '#dc3545' if val > 0 else '#28a745'
        return format_html(
            '<span style="color:{};font-weight:bold;">¥{}</span>',
            color, f'{val:,.2f}',
        )
    outstanding_display.short_description = '未付金额'

    def status_badge(self, obj):
        color = BILLING_STATUS_COLORS.get(obj.status, '#6c757d')
        return make_badge(obj.get_status_display(), color)
    status_badge.short_description = '状态'
    status_badge.admin_order_field = 'status'

    def save_model(self, request, obj, form, change):
        from .channel_services import apply_billing_promotion, ensure_paid_billing_commission

        if change:
            old = CustomerBilling.objects.get(pk=obj.pk)
            old_data = model_to_dict_simple(old)
            if 'amount' in form.changed_data and old.paid_amount == 0:
                # 管理后台的 amount 输入为优惠前金额，与 API 保持一致。
                apply_billing_promotion(obj, obj.amount)
            super().save_model(request, obj, form, change)
            if (
                old.status != CustomerBilling.BillingStatus.PAID
                and obj.status == CustomerBilling.BillingStatus.PAID
            ):
                ensure_paid_billing_commission(obj, operator=request.user, request=request)
            new_data = model_to_dict_simple(obj)
            is_new_payment = obj.paid_amount > old.paid_amount
            action = (
                CustomerOperationLog.ActionType.PAYMENT
                if is_new_payment
                else CustomerOperationLog.ActionType.UPDATE
            )
            desc = (
                f'收款 ¥{obj.paid_amount - old.paid_amount:,.2f}，账单 [{obj.bill_no}]'
                if is_new_payment
                else f'修改账单 [{obj.bill_no}]'
            )
            record_operation_log(
                operator=request.user,
                action_type=action,
                description=desc,
                customer=obj.customer,
                target_model=CustomerOperationLog.TargetModel.BILLING,
                target_id=obj.pk,
                old_value=old_data,
                new_value=new_data,
                request=request,
            )
        else:
            # 管理后台创建账单时同样应用已固化的推荐优惠。
            apply_billing_promotion(obj, obj.amount)
            super().save_model(request, obj, form, change)
            if obj.status == CustomerBilling.BillingStatus.PAID:
                ensure_paid_billing_commission(obj, operator=request.user, request=request)
            record_operation_log(
                operator=request.user,
                action_type=CustomerOperationLog.ActionType.CREATE,
                description=f'新建账单 [{obj.bill_no}] {obj.bill_month} ¥{obj.amount:,.2f}',
                customer=obj.customer,
                target_model=CustomerOperationLog.TargetModel.BILLING,
                target_id=obj.pk,
                request=request,
            )

    def delete_model(self, request, obj):
        customer = obj.customer
        snapshot = model_to_dict_simple(obj)
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.DELETE,
            description=f'删除账单 [{obj.bill_no}] {obj.bill_month}',
            customer=customer,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=obj.pk,
            old_value=snapshot,
            request=request,
        )
        super().delete_model(request, obj)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        qs = self.get_queryset(request)
        totals = qs.aggregate(
            total_amount=Sum('amount'),
            total_paid=Sum('paid_amount'),
        )
        extra_context['total_amount'] = totals['total_amount'] or 0
        extra_context['total_paid'] = totals['total_paid'] or 0
        return super().changelist_view(request, extra_context=extra_context)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('customer')


# ─────────────────────────────────────────────────────────────
# CustomerOperationLog Admin（只读）
# ─────────────────────────────────────────────────────────────

ACTION_TYPE_COLORS = {
    'create': '#28a745',
    'update': '#007bff',
    'delete': '#dc3545',
    'status_change': '#fd7e14',
    'payment': '#20c997',
    'contract_sign': '#6f42c1',
    'contact': '#17a2b8',
    'import': '#6c757d',
    'export': '#6c757d',
    'other': '#adb5bd',
}


@admin.register(CustomerOperationLog)
class CustomerOperationLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'action_type_badge', 'target_model',
        'description', 'customer', 'operator', 'ip_address',
    )
    list_filter = ('action_type', 'target_model', 'created_at', 'operator')
    search_fields = (
        'description', 'customer__name', 'customer__customer_no',
        'operator__username', 'operator__first_name',
    )
    readonly_fields = (
        'customer', 'operator', 'action_type', 'target_model',
        'target_id', 'description', 'old_value', 'new_value',
        'ip_address', 'created_at',
    )
    date_hierarchy = 'created_at'
    list_per_page = 30

    fieldsets = (
        ('操作信息', {
            'fields': ('created_at', 'action_type', 'operator', 'ip_address'),
        }),
        ('关联对象', {
            'fields': ('customer', 'target_model', 'target_id', 'description'),
        }),
        ('变更详情', {
            'classes': ('collapse',),
            'fields': ('old_value', 'new_value'),
        }),
    )

    def action_type_badge(self, obj):
        color = ACTION_TYPE_COLORS.get(obj.action_type, '#adb5bd')
        return make_badge(obj.get_action_type_display(), color)
    action_type_badge.short_description = '操作类型'
    action_type_badge.admin_order_field = 'action_type'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('customer', 'operator')


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'transaction_no', 'billing', 'provider', 'amount', 'currency',
        'status', 'last_reconcile_status', 'provider_order_id', 'provider_payment_id',
        'created_at',
    )
    list_filter = ('provider', 'status', 'currency', 'created_at')
    search_fields = (
        'transaction_no', 'billing__bill_no', 'billing__customer__name',
        'provider_order_id', 'provider_payment_id',
    )
    readonly_fields = tuple(field.name for field in PaymentTransaction._meta.fields)
    list_select_related = ('billing', 'billing__customer', 'channel', 'initiated_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        'event_id', 'provider', 'event_type', 'status',
        'payment', 'verification_status', 'created_at',
    )
    list_filter = ('provider', 'event_type', 'status', 'verification_status', 'created_at')
    search_fields = (
        'event_id', 'resource_id', 'transmission_id',
        'payment__transaction_no',
    )
    readonly_fields = tuple(field.name for field in PaymentWebhookEvent._meta.fields)
    list_select_related = ('channel', 'payment')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomerReferral)
class CustomerReferralAdmin(admin.ModelAdmin):
    list_display = (
        'customer', 'channel', 'promotion_code',
        'promotion_discount_rate', 'commission_rate', 'created_at',
    )
    list_filter = ('channel', 'created_at')
    search_fields = (
        'customer__customer_no', 'customer__name',
        'channel__customer_no', 'channel__name', 'promotion_code',
    )
    readonly_fields = tuple(field.name for field in CustomerReferral._meta.fields)
    list_select_related = ('customer', 'channel', 'bound_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChannelCommission)
class ChannelCommissionAdmin(admin.ModelAdmin):
    list_display = (
        'bill_no', 'channel', 'customer', 'promotion_code',
        'commission_base_amount', 'commission_rate', 'commission_amount',
        'currency', 'status', 'earned_at',
    )
    list_filter = ('status', 'currency', 'channel', 'earned_at')
    search_fields = (
        'bill_no', 'channel__customer_no', 'channel__name',
        'customer__customer_no', 'customer__name', 'promotion_code',
    )
    readonly_fields = tuple(field.name for field in ChannelCommission._meta.fields)
    list_select_related = ('channel', 'customer', 'billing', 'settlement')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChannelSettlement)
class ChannelSettlementAdmin(admin.ModelAdmin):
    list_display = (
        'settlement_no', 'channel', 'currency', 'total_amount',
        'commission_count', 'status', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('settlement_no', 'channel__customer_no', 'channel__name', 'payment_reference')
    readonly_fields = tuple(field.name for field in ChannelSettlement._meta.fields)
    list_select_related = ('channel', 'created_by', 'confirmed_by', 'paid_by')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChannelPayoutProfile)
class ChannelPayoutProfileAdmin(admin.ModelAdmin):
    list_display = (
        'channel', 'entity_name', 'account_type', 'recipient_name',
        'masked_account', 'is_active', 'updated_at',
    )
    list_filter = ('account_type', 'is_active')
    search_fields = ('channel__customer_no', 'channel__name', 'entity_name', 'recipient_name', 'tax_id')
    readonly_fields = tuple(field.name for field in ChannelPayoutProfile._meta.fields) + ('masked_account',)
    exclude = ('account_encrypted',)
    list_select_related = ('channel',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChannelFinanceAccount)
class ChannelFinanceAccountAdmin(admin.ModelAdmin):
    list_display = ('channel', 'currency', 'balance', 'updated_at')
    list_filter = ('currency',)
    search_fields = ('channel__customer_no', 'channel__name')
    readonly_fields = tuple(field.name for field in ChannelFinanceAccount._meta.fields)
    list_select_related = ('channel',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChannelFinanceLedger)
class ChannelFinanceLedgerAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'account', 'entry_type', 'delta', 'balance_after',
        'source_type', 'source_id', 'operator',
    )
    list_filter = ('entry_type', 'account__currency', 'created_at')
    search_fields = (
        'account__channel__customer_no', 'account__channel__name',
        'source_type', 'source_id', 'idempotency_key',
    )
    readonly_fields = tuple(field.name for field in ChannelFinanceLedger._meta.fields)
    list_select_related = ('account', 'account__channel', 'operator')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
