from django.contrib.auth.models import User
from django.db import transaction
from decimal import Decimal
from rest_framework import serializers

from .models import (
    Customer, CustomerIMAccount, ContactRecord, Contract,
    CustomerBilling, BillingItem, CustomerOperationLog, PaymentTransaction,
    CustomerBalanceLedger,
    ChannelProfile, CustomerReferral, ChannelCommission, ChannelSettlement,
    ChannelPayoutProfile, ChannelFinanceAccount, ChannelFinanceLedger,
)


# ─────────────────────────────────────────────────────────────
# 嵌套用简化 Serializer
# ─────────────────────────────────────────────────────────────

class UserBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'full_name')

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class ChannelProfileSerializer(serializers.ModelSerializer):
    channel_id = serializers.IntegerField(source='customer_id', read_only=True)
    channel_name = serializers.CharField(source='customer.name', read_only=True)
    referred_customer_count = serializers.SerializerMethodField()

    class Meta:
        model = ChannelProfile
        fields = (
            'id', 'channel_id', 'channel_name',
            'promotion_code', 'promotion_discount_rate', 'commission_rate',
            'reputation_grant_mode', 'reputation_grant_amount',
            'is_active', 'remark', 'referred_customer_count',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_referred_customer_count(self, obj):
        return obj.customer.referred_customer_referrals.count()


class ChannelPayoutProfileSerializer(serializers.ModelSerializer):
    channel_name = serializers.CharField(source='channel.name', read_only=True)
    account = serializers.CharField(write_only=True, required=False, allow_blank=False)
    masked_account = serializers.CharField(read_only=True)

    class Meta:
        model = ChannelPayoutProfile
        fields = (
            'id', 'channel', 'channel_name', 'entity_name', 'account_type',
            'bank_name', 'bank_branch', 'recipient_name', 'tax_id',
            'account', 'masked_account', 'is_active', 'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'channel_name', 'masked_account', 'created_at', 'updated_at')

    def validate(self, attrs):
        channel = attrs.get('channel', getattr(self.instance, 'channel', None))
        if channel and channel.customer_type != 'channel':
            raise serializers.ValidationError({'channel': '只有渠道客户可以配置收款资料'})
        if self.instance and 'channel' in attrs and attrs['channel'].pk != self.instance.channel_id:
            raise serializers.ValidationError({'channel': '不能变更收款资料所属渠道'})
        if self.instance is None and not attrs.get('account'):
            raise serializers.ValidationError({'account': '请填写收款账号'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        account = validated_data.pop('account')
        profile = ChannelPayoutProfile(**validated_data)
        profile.set_account(account)
        profile.full_clean()
        profile.save()
        return profile

    @transaction.atomic
    def update(self, instance, validated_data):
        account = validated_data.pop('account', None)
        instance = ChannelPayoutProfile.objects.select_for_update().get(pk=instance.pk)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if account is not None:
            instance.set_account(account)
        instance.full_clean()
        instance.save()
        return instance


class CustomerReferralSerializer(serializers.ModelSerializer):
    channel_id = serializers.IntegerField(read_only=True)
    channel_no = serializers.CharField(source='channel.customer_no', read_only=True)
    channel_name = serializers.CharField(source='channel.name', read_only=True)

    class Meta:
        model = CustomerReferral
        fields = (
            'id', 'channel_id', 'channel_no', 'channel_name',
            'promotion_code', 'promotion_discount_rate', 'commission_rate',
            'created_at',
        )
        read_only_fields = fields


# ─────────────────────────────────────────────────────────────
# CustomerIMAccount
# ─────────────────────────────────────────────────────────────

class CustomerIMAccountSerializer(serializers.ModelSerializer):
    im_type_label = serializers.CharField(source='get_im_type_display', read_only=True)

    class Meta:
        model = CustomerIMAccount
        fields = (
            'id', 'im_type', 'im_type_label',
            'account', 'group', 'label', 'is_primary',
        )

    def validate(self, attrs):
        account = attrs.get('account', getattr(self.instance, 'account', ''))
        group = attrs.get('group', getattr(self.instance, 'group', ''))
        if not account and not group:
            raise serializers.ValidationError('IM 账号或群组至少填写一项')
        return attrs


# ─────────────────────────────────────────────────────────────
# ContactRecord
# ─────────────────────────────────────────────────────────────

class ContactRecordSerializer(serializers.ModelSerializer):
    handler_info = UserBriefSerializer(source='handler', read_only=True)
    contact_type_label = serializers.CharField(source='get_contact_type_display', read_only=True)

    class Meta:
        model = ContactRecord
        fields = (
            'id', 'customer', 'contact_type', 'contact_type_label',
            'contact_time', 'summary', 'handler', 'handler_info',
            'next_follow_time', 'created_at',
        )
        read_only_fields = ('id', 'created_at')


# ─────────────────────────────────────────────────────────────
# Contract
# ─────────────────────────────────────────────────────────────

class ContractSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    created_by_info = UserBriefSerializer(source='created_by', read_only=True)

    class Meta:
        model = Contract
        fields = (
            'id', 'customer', 'contract_no', 'contract_name',
            'status', 'status_label', 'amount',
            'start_date', 'end_date', 'sign_date',
            'file_url', 'remark',
            'created_by', 'created_by_info',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'contract_no', 'created_at', 'updated_at')

    def validate(self, attrs):
        start = attrs.get('start_date')
        end = attrs.get('end_date')
        if start and end and start >= end:
            raise serializers.ValidationError('合同开始日期必须早于结束日期')
        return attrs


# ─────────────────────────────────────────────────────────────
# CustomerBilling
# ─────────────────────────────────────────────────────────────

class BillingItemSerializer(serializers.ModelSerializer):
    service_type_label = serializers.SerializerMethodField()
    traffic_report_no = serializers.SerializerMethodField()

    class Meta:
        model = BillingItem
        fields = (
            'id', 'service_instance', 'instance_no', 'description',
            'service_type', 'service_type_label',
            'product', 'product_name',
            'period_start', 'period_end', 'days_used', 'days_in_month',
            'is_prorated', 'is_trial',
            'unit_monthly_fee', 'base_monthly_fee', 'overage_monthly_fee',
            'source_currency', 'exchange_rate',
            'amount_original', 'currency', 'amount', 'detail',
            'traffic_report', 'traffic_report_no', 'created_at',
        )
        read_only_fields = fields

    def get_service_type_label(self, obj):
        mapping = {'vps': 'VPS', 'edge': '边缘加速', 'ip': 'IP 地址', 'bandwidth': '带宽'}
        return mapping.get(obj.service_type, obj.service_type)

    def get_traffic_report_no(self, obj):
        report = obj.traffic_report
        return report.report_no if report else ''


class CustomerBillingSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    billing_kind_label = serializers.CharField(source='get_billing_kind_display', read_only=True)
    payment_method_label = serializers.CharField(source='get_payment_method_display', read_only=True)
    outstanding_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True,
    )
    items = BillingItemSerializer(many=True, read_only=True)
    trial_count = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    service_order_info = serializers.SerializerMethodField()

    class Meta:
        model = CustomerBilling
        fields = (
            'id', 'customer', 'customer_name', 'bill_no', 'bill_month',
            'service_order', 'service_order_info',
            'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'channel_referral',
            'amount', 'currency', 'auto_generated',
            'billing_kind', 'billing_kind_label',
            'paid_amount', 'refunded_amount', 'outstanding_amount',
            'status', 'status_label',
            'payment_method', 'payment_method_label',
            'due_date', 'paid_at', 'refund_reason', 'refunded_at', 'remark',
            'items', 'trial_count',
            'created_at', 'updated_at',
        )
        read_only_fields = (
            'id', 'bill_no', 'outstanding_amount', 'auto_generated',
            'billing_kind', 'billing_kind_label',
            'service_order', 'service_order_info',
            'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'channel_referral',
            'paid_amount', 'refunded_amount', 'status', 'payment_method', 'paid_at',
            'refund_reason', 'refunded_at',
            'items', 'trial_count', 'created_at', 'updated_at',
        )

    def get_trial_count(self, obj):
        return obj.items.filter(is_trial=True).count()

    def get_service_order_info(self, obj):
        order = obj.service_order
        if not order:
            return None
        from .refund_engine import is_eligible_for_auto_refund
        return {
            'id': order.pk,
            'order_no': order.order_no,
            'status': order.status,
            'auto_provision_status': order.auto_provision_status,
            'auto_provision_status_label': order.get_auto_provision_status_display(),
            'auto_provision_message': order.auto_provision_message,
            'can_request_refund': is_eligible_for_auto_refund(obj),
        }

    def validate_paid_amount(self, value):
        if value < 0:
            raise serializers.ValidationError('已付金额不能为负数')
        return value

    def validate(self, attrs):
        amount = attrs.get('amount', getattr(self.instance, 'amount', None))
        paid = attrs.get('paid_amount', getattr(self.instance, 'paid_amount', 0))
        if amount is not None and paid > amount:
            raise serializers.ValidationError('已付金额不能超过账单金额')
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        """手工账单也按推荐关系计算优惠，amount 输入值视为优惠前金额。"""
        from .channel_services import apply_billing_promotion

        gross_amount = validated_data.pop('amount')
        billing = CustomerBilling(**validated_data, amount=gross_amount)
        apply_billing_promotion(billing, gross_amount)
        billing.save()
        return billing

    @transaction.atomic
    def update(self, instance, validated_data):
        """未产生收款时，修改手工金额后重新按绑定时的优惠规则报价。"""
        from .channel_services import apply_billing_promotion

        gross_amount = validated_data.pop('amount', None)
        if gross_amount is not None:
            if instance.paid_amount > 0:
                raise serializers.ValidationError('已产生收款的账单不能修改金额')
            for field, value in validated_data.items():
                setattr(instance, field, value)
            apply_billing_promotion(instance, gross_amount)
            instance.save()
            return instance
        return super().update(instance, validated_data)


class PaymentTransactionSerializer(serializers.ModelSerializer):
    """支付交易安全视图，不暴露网关密钥、签名或 checkout token。"""

    status_label = serializers.CharField(source='get_status_display', read_only=True)
    billing_no = serializers.CharField(source='billing.bill_no', read_only=True)
    channel_name = serializers.CharField(source='channel.name', read_only=True, allow_null=True)
    provider_label = serializers.SerializerMethodField()

    class Meta:
        model = PaymentTransaction
        fields = (
            'id', 'transaction_no', 'billing', 'billing_no',
            'channel', 'channel_name', 'provider', 'provider_label',
            'amount', 'currency', 'status', 'status_label',
            'billing_amount', 'billing_currency', 'exchange_rate',
            'provider_order_id', 'provider_payment_id',
            'failure_reason', 'expires_at', 'paid_at',
            'last_reconciled_at', 'last_reconcile_status', 'last_reconcile_error',
            'refund_reason', 'refunded_amount', 'provider_refund_id',
            'refund_requested_at', 'refunded_at',
            'created_at', 'updated_at',
        )
        read_only_fields = fields

    def get_provider_label(self, obj):
        if obj.channel_id:
            return obj.channel.get_provider_display()
        return obj.provider


class StartOnlinePaymentSerializer(serializers.Serializer):
    channel_id = serializers.IntegerField(min_value=1)
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )


# ─────────────────────────────────────────────────────────────
# CustomerOperationLog
# ─────────────────────────────────────────────────────────────

class CustomerOperationLogSerializer(serializers.ModelSerializer):
    operator_info = UserBriefSerializer(source='operator', read_only=True)
    action_type_label = serializers.CharField(source='get_action_type_display', read_only=True)
    target_model_label = serializers.CharField(source='get_target_model_display', read_only=True)

    class Meta:
        model = CustomerOperationLog
        fields = (
            'id', 'customer', 'operator', 'operator_info',
            'action_type', 'action_type_label',
            'target_model', 'target_model_label', 'target_id',
            'description', 'old_value', 'new_value',
            'ip_address', 'created_at',
        )
        read_only_fields = fields


# ─────────────────────────────────────────────────────────────
# Customer（列表 / 详情）
# ─────────────────────────────────────────────────────────────

class CustomerListSerializer(serializers.ModelSerializer):
    """列表轻量版，不展开嵌套数据"""
    customer_type_label = serializers.CharField(source='get_customer_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    im_accounts = CustomerIMAccountSerializer(many=True, read_only=True)
    sales_manager_info = UserBriefSerializer(source='sales_manager', read_only=True)
    outstanding_total = serializers.SerializerMethodField()
    channel_profile = ChannelProfileSerializer(read_only=True)
    channel_referral = CustomerReferralSerializer(read_only=True)
    account_username = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = (
            'id', 'customer_no', 'name',
            'customer_type', 'customer_type_label',
            'status', 'status_label',
            'contact_name', 'contact_phone', 'contact_email',
            'im_accounts',
            'company_name', 'company_name_short',
            'province', 'city',
            'balance', 'credit_limit', 'outstanding_total',
            'reputation_mode', 'reputation_quota', 'reputation_balance', 'reputation_period',
            'sales_manager', 'sales_manager_info',
            'account', 'account_username',
            'channel_profile', 'channel_referral',
            'created_at', 'updated_at',
        )
        read_only_fields = ('id', 'customer_no', 'created_at', 'updated_at')

    def get_outstanding_total(self, obj):
        from django.db.models import Sum, F, ExpressionWrapper, DecimalField
        result = obj.billings.filter(
            status__in=['unpaid', 'overdue']
        ).aggregate(
            total=Sum(
                ExpressionWrapper(F('amount') - F('paid_amount'), output_field=DecimalField())
            )
        )
        return result['total'] or 0

    def get_account_username(self, obj):
        account = getattr(obj, 'account', None)
        return account.username if account else None


class CustomerDetailSerializer(CustomerListSerializer):
    """详情版，包含联系记录、合同、账单、操作日志"""
    contact_records = ContactRecordSerializer(many=True, read_only=True)
    contracts = ContractSerializer(many=True, read_only=True)
    billings = CustomerBillingSerializer(many=True, read_only=True)
    operation_logs = CustomerOperationLogSerializer(many=True, read_only=True)

    class Meta(CustomerListSerializer.Meta):
        fields = CustomerListSerializer.Meta.fields + (
            'unified_credit_code', 'legal_representative',
            'address', 'remark',
            'contact_records', 'contracts', 'billings', 'operation_logs',
        )


class CustomerWriteSerializer(serializers.ModelSerializer):
    """创建/更新专用，支持嵌套写入多个 IM 账号"""

    im_accounts = CustomerIMAccountSerializer(many=True, required=False)
    promotion_code = serializers.CharField(max_length=32, required=False, allow_blank=True, write_only=True)
    promotion_discount_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100,
        required=False, write_only=True,
    )
    commission_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100,
        required=False, write_only=True,
    )
    channel_promotion_enabled = serializers.BooleanField(required=False, write_only=True)
    channel_promotion_remark = serializers.CharField(required=False, allow_blank=True, write_only=True)
    reputation_grant_mode = serializers.ChoiceField(
        choices=Customer.ReputationMode.choices, required=False, write_only=True,
    )
    reputation_grant_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, write_only=True,
    )

    class Meta:
        model = Customer
        fields = (
            'name', 'customer_type',
            'contact_name', 'contact_phone', 'contact_email',
            'im_accounts',
            'company_name', 'company_name_short',
            'unified_credit_code', 'legal_representative',
            'province', 'city', 'address',
            'balance', 'credit_limit',
            'reputation_mode', 'reputation_quota', 'reputation_balance',
            'account', 'sales_manager', 'remark',
            'promotion_code', 'promotion_discount_rate', 'commission_rate',
            'channel_promotion_enabled', 'channel_promotion_remark',
            'reputation_grant_mode', 'reputation_grant_amount',
        )
        read_only_fields = ('balance',)

    def validate_contact_phone(self, value):
        if value and not value.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            raise serializers.ValidationError('请输入有效的联系电话')
        return value

    def validate(self, attrs):
        from .channel_services import ChannelServiceError, normalize_promotion_code
        from accounts.permissions import is_channel_role, require_bound_channel

        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if is_channel_role(user):
            channel = require_bound_channel(user)
            attrs['_bound_channel'] = channel
            for key in (
                'promotion_code', 'promotion_discount_rate', 'commission_rate',
                'channel_promotion_enabled', 'channel_promotion_remark',
                'reputation_grant_mode', 'reputation_grant_amount',
                'credit_limit', 'account', 'sales_manager',
                'reputation_mode', 'reputation_quota', 'reputation_balance',
            ):
                attrs.pop(key, None)
            incoming_type = attrs.get('customer_type')
            if self.instance and self.instance.pk == channel.pk:
                if incoming_type and incoming_type != 'channel':
                    raise serializers.ValidationError({'customer_type': '不能变更本渠道客户类型'})
                attrs['customer_type'] = 'channel'
            elif incoming_type == 'channel':
                raise serializers.ValidationError({'customer_type': '渠道账号不能创建或将客户变更为渠道类型'})

        customer_type = attrs.get(
            'customer_type',
            self.instance.customer_type if self.instance else None,
        )
        rule_fields = {
            'promotion_code', 'promotion_discount_rate', 'commission_rate',
            'channel_promotion_enabled', 'channel_promotion_remark',
            'reputation_grant_mode', 'reputation_grant_amount',
        }
        if customer_type != 'channel' and any(field in attrs for field in rule_fields):
            raise serializers.ValidationError('只有渠道客户可以维护推广规则')
        if 'promotion_code' in attrs and attrs['promotion_code']:
            try:
                code = normalize_promotion_code(attrs['promotion_code'])
            except ChannelServiceError as exc:
                raise serializers.ValidationError({'promotion_code': str(exc)}) from exc
            existing = ChannelProfile.objects.filter(promotion_code=code)
            if self.instance:
                existing = existing.exclude(customer=self.instance)
            if existing.exists():
                raise serializers.ValidationError({'promotion_code': '该推广优惠码已被其他渠道使用'})
        if (
            self.instance
            and customer_type != 'channel'
            and CustomerReferral.objects.filter(channel=self.instance).exists()
        ):
            raise serializers.ValidationError(
                {'customer_type': '已有推荐客户归属或佣金记录的渠道客户不能变更为非渠道类型'},
            )
        return attrs

    def _sync_im_accounts(self, customer, im_accounts):
        """全量替换该客户的 IM 账号列表，并保证至多一个主账号。"""
        customer.im_accounts.all().delete()
        has_primary = False
        for item in im_accounts:
            is_primary = item.get('is_primary', False)
            if is_primary and has_primary:
                is_primary = False
            if is_primary:
                has_primary = True
            CustomerIMAccount.objects.create(
                customer=customer,
                im_type=item.get('im_type', ''),
                account=item.get('account', ''),
                group=item.get('group', ''),
                label=item.get('label', ''),
                is_primary=is_primary,
            )
        # 没有显式主账号时，将第一个设为主账号
        if im_accounts and not has_primary:
            first = customer.im_accounts.first()
            if first:
                first.is_primary = True
                first.save(update_fields=['is_primary'])

    def _pop_channel_rule_data(self, validated_data):
        keys = (
            'promotion_code', 'promotion_discount_rate', 'commission_rate',
            'channel_promotion_enabled', 'channel_promotion_remark',
            'reputation_grant_mode', 'reputation_grant_amount',
        )
        data = {key: validated_data.pop(key) for key in keys if key in validated_data}
        return {
            'promotion_code': data.get('promotion_code'),
            'promotion_discount_rate': data.get('promotion_discount_rate'),
            'commission_rate': data.get('commission_rate'),
            'is_active': data.get('channel_promotion_enabled'),
            'remark': data.get('channel_promotion_remark'),
            'reputation_grant_mode': data.get('reputation_grant_mode'),
            'reputation_grant_amount': data.get('reputation_grant_amount'),
        }, bool(data)

    @transaction.atomic
    def create(self, validated_data):
        from .channel_services import (
            ChannelServiceError,
            bind_customer_referral,
            configure_channel_profile,
        )

        bound_channel = validated_data.pop('_bound_channel', None)
        im_accounts = validated_data.pop('im_accounts', None)
        channel_rule, _ = self._pop_channel_rule_data(validated_data)
        from .reputation import apply_reputation_settings
        reputation_payload = {
            key: validated_data.pop(key)
            for key in ('reputation_mode', 'reputation_quota', 'reputation_balance')
            if key in validated_data
        }
        customer = super().create(validated_data)
        if reputation_payload:
            apply_reputation_settings(
                customer,
                mode=reputation_payload.get('reputation_mode'),
                quota=reputation_payload.get('reputation_quota'),
                balance=reputation_payload.get('reputation_balance'),
            )
        if customer.customer_type == 'channel':
            configure_channel_profile(customer, **channel_rule)
        if im_accounts is not None:
            self._sync_im_accounts(customer, im_accounts)
        if bound_channel and customer.pk != bound_channel.pk:
            profile = getattr(bound_channel, 'channel_profile', None)
            if profile is None or not profile.promotion_code:
                profile = configure_channel_profile(bound_channel)
            request = self.context.get('request')
            try:
                bind_customer_referral(
                    customer,
                    profile.promotion_code,
                    bound_by=getattr(request, 'user', None),
                )
            except ChannelServiceError as exc:
                raise serializers.ValidationError({'detail': str(exc)}) from exc
        return customer

    @transaction.atomic
    def update(self, instance, validated_data):
        from .channel_services import configure_channel_profile

        validated_data.pop('_bound_channel', None)
        im_accounts = validated_data.pop('im_accounts', None)
        channel_rule, has_channel_rule = self._pop_channel_rule_data(validated_data)
        from .reputation import apply_reputation_settings
        reputation_payload = {
            key: validated_data.pop(key)
            for key in ('reputation_mode', 'reputation_quota', 'reputation_balance')
            if key in validated_data
        }
        customer = super().update(instance, validated_data)
        if reputation_payload:
            apply_reputation_settings(
                customer,
                mode=reputation_payload.get('reputation_mode'),
                quota=reputation_payload.get('reputation_quota'),
                balance=reputation_payload.get('reputation_balance'),
            )
        if customer.customer_type == 'channel':
            if has_channel_rule or not hasattr(customer, 'channel_profile'):
                configure_channel_profile(customer, **channel_rule)
        else:
            profile = ChannelProfile.objects.filter(customer=customer).first()
            if profile:
                profile.delete()
        if im_accounts is not None:
            self._sync_im_accounts(customer, im_accounts)
        return customer

    def to_representation(self, instance):
        return CustomerDetailSerializer(instance, context=self.context).data


class CustomerBalanceLedgerSerializer(serializers.ModelSerializer):
    entry_type_label = serializers.CharField(source='get_entry_type_display', read_only=True)
    operator_info = UserBriefSerializer(source='operator', read_only=True)
    bill_no = serializers.CharField(source='related_billing.bill_no', read_only=True, default='')

    class Meta:
        model = CustomerBalanceLedger
        fields = (
            'id', 'entry_type', 'entry_type_label',
            'delta', 'balance_after', 'currency',
            'related_billing', 'bill_no',
            'remark', 'operator', 'operator_info', 'created_at',
        )
        read_only_fields = fields


class CustomerBalanceAdjustSerializer(serializers.Serializer):
    delta = serializers.DecimalField(max_digits=12, decimal_places=2)
    remark = serializers.CharField(max_length=256)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError('调整金额不能为零')
        return value


# ─────────────────────────────────────────────────────────────
# 渠道佣金与结算
# ─────────────────────────────────────────────────────────────

class ChannelCommissionSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    channel_name = serializers.CharField(source='channel.name', read_only=True)
    channel_no = serializers.CharField(source='channel.customer_no', read_only=True)
    settlement_no = serializers.CharField(source='settlement.settlement_no', read_only=True, allow_null=True)

    class Meta:
        model = ChannelCommission
        fields = (
            'id', 'channel', 'channel_name', 'channel_no',
            'customer', 'customer_name', 'customer_no',
            'billing', 'bill_no', 'promotion_code',
            'gross_amount', 'promotion_discount_amount',
            'billing_amount', 'billing_period', 'billing_paid_at',
            'service_items_snapshot', 'payment_summary_snapshot',
            'commission_base_amount', 'commission_rate', 'commission_amount', 'currency',
            'status', 'status_label', 'void_reason',
            'settlement', 'settlement_no',
            'earned_at', 'settled_at', 'created_at', 'updated_at',
        )
        read_only_fields = fields


class ChannelFinanceLedgerSerializer(serializers.ModelSerializer):
    channel = serializers.IntegerField(source='account.channel_id', read_only=True)
    channel_name = serializers.CharField(source='account.channel.name', read_only=True)
    currency = serializers.CharField(source='account.currency', read_only=True)
    operator_info = UserBriefSerializer(source='operator', read_only=True)

    class Meta:
        model = ChannelFinanceLedger
        fields = (
            'id', 'account', 'channel', 'channel_name', 'currency',
            'entry_type', 'delta', 'balance_after',
            'source_type', 'source_id', 'snapshot',
            'operator', 'operator_info', 'idempotency_key', 'created_at',
        )
        read_only_fields = fields


class ChannelSettlementSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    created_by_info = UserBriefSerializer(source='created_by', read_only=True)
    confirmed_by_info = UserBriefSerializer(source='confirmed_by', read_only=True)
    paid_by_info = UserBriefSerializer(source='paid_by', read_only=True)

    class Meta:
        model = ChannelSettlement
        fields = (
            'id', 'settlement_no', 'channel', 'channel_name', 'channel_no',
            'currency', 'total_amount', 'commission_count',
            'period_start', 'period_end',
            'status', 'status_label',
            'payout_profile_snapshot', 'payment_method', 'payment_reference', 'remark',
            'created_by', 'created_by_info',
            'confirmed_by', 'confirmed_by_info',
            'paid_by', 'paid_by_info',
            'confirmed_at', 'paid_at', 'created_at', 'updated_at',
        )
        read_only_fields = fields


class ChannelSettlementDetailSerializer(ChannelSettlementSerializer):
    commissions = ChannelCommissionSerializer(many=True, read_only=True)
    finance_ledgers = serializers.SerializerMethodField()
    customer_subtotals = serializers.SerializerMethodField()
    service_type_subtotals = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta(ChannelSettlementSerializer.Meta):
        fields = ChannelSettlementSerializer.Meta.fields + (
            'commissions', 'finance_ledgers',
            'customer_subtotals', 'service_type_subtotals', 'timeline',
        )

    def get_finance_ledgers(self, obj):
        queryset = ChannelFinanceLedger.objects.filter(
            source_type='channel_settlement',
            source_id=str(obj.pk),
        ).select_related('account', 'account__channel', 'operator')
        return ChannelFinanceLedgerSerializer(queryset, many=True).data

    def get_customer_subtotals(self, obj):
        grouped = {}
        for commission in obj.commissions.all():
            key = (commission.customer_no, commission.customer_name)
            item = grouped.setdefault(key, {
                'customer_no': commission.customer_no,
                'customer_name': commission.customer_name,
                'commission_count': 0,
                'billing_amount': Decimal('0.00'),
                'commission_amount': Decimal('0.00'),
            })
            item['commission_count'] += 1
            item['billing_amount'] += commission.billing_amount
            item['commission_amount'] += commission.commission_amount
        return list(grouped.values())

    def get_service_type_subtotals(self, obj):
        grouped = {}
        for commission in obj.commissions.all():
            items = commission.service_items_snapshot or []
            item_total = sum((Decimal(str(item.get('amount') or 0)) for item in items), Decimal('0'))
            for source in items:
                service_type = str(source.get('service_type') or 'other')
                amount = Decimal(str(source.get('amount') or 0))
                item = grouped.setdefault(service_type, {
                    'service_type': service_type,
                    'item_count': 0,
                    'billing_amount': Decimal('0.00'),
                    'commission_amount': Decimal('0.00'),
                })
                item['item_count'] += 1
                item['billing_amount'] += amount
                if item_total:
                    item['commission_amount'] += (
                        commission.commission_amount * amount / item_total
                    ).quantize(Decimal('0.01'))
        return list(grouped.values())

    def get_timeline(self, obj):
        def actor(user):
            if not user:
                return None
            return {'id': user.pk, 'username': user.username}

        events = [{
            'event': 'created',
            'at': obj.created_at,
            'operator': actor(obj.created_by),
        }]
        if obj.confirmed_at:
            events.append({
                'event': 'confirmed',
                'at': obj.confirmed_at,
                'operator': actor(obj.confirmed_by),
            })
        if obj.paid_at:
            events.append({
                'event': 'paid',
                'at': obj.paid_at,
                'operator': actor(obj.paid_by),
            })
        if obj.status == ChannelSettlement.Status.CANCELLED:
            events.append({'event': 'cancelled', 'at': obj.updated_at, 'operator': None})
        return sorted(events, key=lambda item: item['at'])


class ChannelSettlementCreateSerializer(serializers.Serializer):
    commission_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=1,
    )
    remark = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ChannelSettlementPaymentSerializer(serializers.Serializer):
    payment_method = serializers.CharField(max_length=32)
    payment_reference = serializers.CharField(max_length=128)


class ChannelSettlementCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ChannelCommissionVoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class ChannelFinanceAdjustmentSerializer(serializers.Serializer):
    channel = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.filter(customer_type='channel'),
    )
    currency = serializers.CharField(max_length=8)
    delta = serializers.DecimalField(max_digits=14, decimal_places=2)
    reason = serializers.CharField(max_length=2000)
    reference = serializers.CharField(max_length=128)
    evidence = serializers.CharField(max_length=2000)
    idempotency_key = serializers.CharField(max_length=100)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError('调整金额不能为零')
        return value
