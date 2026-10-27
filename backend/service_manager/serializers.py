"""
service_manager/serializers.py
服务管理模块 DRF 序列化器
"""
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from rest_framework import serializers

from customer_manager.models import Customer
from product_manager.models import Product
from resource_manager.models import PopPoint

from .models import (
    ServiceOrder, ServiceInquiry, ServiceInstance,
    ServiceAllocation, ServiceChangeRecord, ServiceOperationLog,
    ServiceLifecycleRecord, ServiceProvisionHook, ServiceInquiryReply,
    SupportTicket, SupportTicketMessage,
    OsImage, InstanceSnapshot, SelfServiceJob,
    order_delivery_summary,
)


# ─────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────

class UserBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


# ═══════════════════════════════════════════════════════════════
# § 1  服务请求
# ═══════════════════════════════════════════════════════════════

class ServiceOrderListSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    order_type_label = serializers.CharField(source='get_order_type_display', read_only=True)
    business_type_label = serializers.CharField(source='get_business_type_display', read_only=True)
    billing_cycle_label = serializers.CharField(source='get_billing_cycle_display', read_only=True)
    current_stage_label = serializers.CharField(source='get_current_stage_display', read_only=True)
    source_label = serializers.CharField(source='get_source_display', read_only=True)
    auto_provision_status_label = serializers.CharField(
        source='get_auto_provision_status_display', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    customer_no = serializers.CharField(source='customer.customer_no', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    preferred_pop_name = serializers.CharField(source='preferred_pop.name', read_only=True)
    payment_billing = serializers.SerializerMethodField()
    instance_count = serializers.SerializerMethodField()
    delivery_summary = serializers.SerializerMethodField()
    requested_os_template_label = serializers.CharField(
        source='requested_os_template.display_label', read_only=True, allow_null=True,
    )
    created_by_name = serializers.SerializerMethodField()
    handler_name = serializers.SerializerMethodField()
    capacity_reservation = serializers.SerializerMethodField()
    manual_delivery = serializers.SerializerMethodField()

    class Meta:
        model = ServiceOrder
        fields = [
            'id', 'order_no', 'order_type', 'order_type_label',
            'business_type', 'business_type_label',
            'status', 'status_label',
            'current_stage', 'current_stage_label',
            'source', 'source_label',
            'customer', 'customer_name', 'customer_no',
            'product', 'product_name',
            'preferred_pop', 'preferred_pop_name',
            'related_instance',
            'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb', 'requested_os',
            'requested_os_template', 'requested_os_template_label',
            'requested_bandwidth_mbps', 'requested_ip_count', 'requested_quantity',
            'billing_cycle', 'billing_cycle_label',
            'quoted_price',
            'fx_quote_currency', 'fx_locked_at',
            'expected_delivery_at',
            'capacity_reservation',
            'manual_delivery',
            'service_start', 'service_end',
            'auto_provision_status', 'auto_provision_status_label', 'auto_provision_message',
            'payment_billing',
            'instance_count', 'delivery_summary',
            'created_by', 'created_by_name',
            'handler', 'handler_name', 'assigned_at',
            'created_at', 'updated_at',
        ]

    def get_instance_count(self, obj):
        return obj.instances.count()

    def get_delivery_summary(self, obj):
        return order_delivery_summary(obj)

    def get_created_by_name(self, obj):
        return obj.created_by.get_full_name() or obj.created_by.username if obj.created_by else None

    def get_handler_name(self, obj):
        return obj.handler.get_full_name() or obj.handler.username if obj.handler else None

    def get_capacity_reservation(self, obj):
        from service_manager.capacity_reservation import latest_reservation, reservation_payload
        return reservation_payload(latest_reservation(obj))

    def get_manual_delivery(self, obj):
        from service_manager.manual_delivery import public_summary
        return public_summary(obj)

    def get_payment_billing(self, obj):
        try:
            billing = obj.payment_billing
        except ObjectDoesNotExist:
            return None
        return {
            'id': billing.pk,
            'bill_no': billing.bill_no,
            'amount': str(billing.amount),
            'paid_amount': str(billing.paid_amount),
            'currency': billing.currency,
            'status': billing.status,
            'status_label': billing.get_status_display(),
            'due_date': billing.due_date,
            'paid_at': billing.paid_at,
        }


class ServiceOrderDetailSerializer(ServiceOrderListSerializer):
    instances = serializers.SerializerMethodField()
    approved_by_info = serializers.SerializerMethodField()
    recent_logs = serializers.SerializerMethodField()
    lifecycle = serializers.SerializerMethodField()
    edge_socks5_pass = serializers.SerializerMethodField()

    class Meta(ServiceOrderListSerializer.Meta):
        fields = ServiceOrderListSerializer.Meta.fields + [
            'contract', 'requirements',
            'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb',
            'requested_os',
            'requested_bandwidth_mbps', 'requested_ip_count',
            'edge_local_ip', 'edge_remote_ip',
            'edge_wg_port', 'edge_wg_keepalive', 'edge_wg_allowed_ips', 'edge_wg_client_pubkey',
            'edge_socks5_port', 'edge_socks5_user', 'edge_socks5_pass',
            'approved_by', 'approved_by_info', 'approved_at', 'reject_reason',
            'request_snapshot', 'fx_snapshot',
            'remark', 'instances', 'recent_logs', 'lifecycle',
        ]

    def get_instances(self, obj):
        return ServiceInstanceBriefSerializer(
            obj.instances.all(), many=True, context=self.context
        ).data

    def get_approved_by_info(self, obj):
        if obj.approved_by:
            return {'id': obj.approved_by.id, 'username': obj.approved_by.username}
        return None

    def get_recent_logs(self, obj):
        logs = obj.operation_logs.order_by('-created_at')[:10]
        return ServiceOperationLogSerializer(logs, many=True).data

    def get_lifecycle(self, obj):
        records = obj.lifecycle_records.order_by('created_at', 'id')
        return ServiceLifecycleRecordSerializer(records, many=True).data

    def get_edge_socks5_pass(self, obj):
        return '已设置' if obj.edge_socks5_pass else ''


class ServiceOrderWriteSerializer(serializers.ModelSerializer):
    edge_socks5_pass = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
    )

    class Meta:
        model = ServiceOrder
        fields = [
            'order_type', 'business_type', 'status', 'current_stage', 'source',
            'customer', 'contract', 'product', 'preferred_pop',
            'related_instance', 'handler',
            'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb',
            'requested_os', 'requested_os_template',
            'requested_bandwidth_mbps', 'requested_ip_count', 'requested_quantity',
            'edge_local_ip', 'edge_remote_ip',
            'edge_wg_port', 'edge_wg_keepalive', 'edge_wg_allowed_ips', 'edge_wg_client_pubkey',
            'edge_socks5_port', 'edge_socks5_user', 'edge_socks5_pass',
            'requirements',
            'service_start', 'service_end', 'billing_cycle',
            'quoted_price', 'reject_reason', 'remark',
        ]
        read_only_fields = ['status', 'current_stage', 'source', 'reject_reason']

    def validate(self, data):
        if self.instance and self.instance.status in (
            ServiceOrder.OrderStatus.PROVISIONING,
            ServiceOrder.OrderStatus.ACTIVE,
            ServiceOrder.OrderStatus.SUSPENDED,
            ServiceOrder.OrderStatus.TERMINATED,
        ):
            delivery_fields = {
                'order_type', 'business_type', 'customer', 'contract', 'product',
                'preferred_pop', 'related_instance',
                'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb',
                'requested_os', 'requested_os_template', 'requested_bandwidth_mbps', 'requested_ip_count',
                'requested_quantity',
                'edge_local_ip', 'edge_remote_ip',
                'edge_wg_port', 'edge_wg_keepalive', 'edge_wg_allowed_ips', 'edge_wg_client_pubkey',
                'edge_socks5_port', 'edge_socks5_user', 'edge_socks5_pass',
                'requirements', 'quoted_price',
            }
            changed = delivery_fields.intersection(data.keys())
            if changed:
                raise serializers.ValidationError({
                    field: '工单已进入交付流程，请通过服务变更单调整资源或需求'
                    for field in changed
                })
        if self.instance and self.instance.status == ServiceOrder.OrderStatus.APPROVED:
            from django.core.exceptions import ObjectDoesNotExist

            has_billing = False
            try:
                has_billing = self.instance.payment_billing is not None
            except ObjectDoesNotExist:
                has_billing = False
            if has_billing or self.instance.auto_provision_status in (
                ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING,
                ServiceOrder.AutoProvisionStatus.PROVISIONING,
                ServiceOrder.AutoProvisionStatus.SUCCESS,
            ):
                locked_fields = {
                    'order_type', 'business_type', 'customer', 'contract', 'product',
                    'related_instance', 'quoted_price',
                    'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb',
                    'requested_os', 'requested_os_template', 'requested_bandwidth_mbps', 'requested_ip_count',
                    'requested_quantity',
                }
                changed = locked_fields.intersection(data.keys())
                if changed:
                    raise serializers.ValidationError({
                        field: '工单已审批，客户、产品和报价请通过变更单或重新提交调整'
                        for field in changed
                    })
        order_type = data.get('order_type', getattr(self.instance, 'order_type', None))
        product = data.get('product', getattr(self.instance, 'product', None))
        if order_type == ServiceOrder.OrderType.NEW and not product:
            raise serializers.ValidationError({'product': '新开服务必须选择已上线的服务产品'})
        if product and product.status != 'online':
            raise serializers.ValidationError({'product': '只能选择已上线的服务产品'})
        service_start = data.get('service_start')
        service_end = data.get('service_end')
        if service_start and service_end and service_end <= service_start:
            raise serializers.ValidationError({'service_end': '服务结束日期必须晚于开始日期'})
        quantity = data.get('requested_quantity', getattr(self.instance, 'requested_quantity', 1))
        if quantity is not None and int(quantity) < 1:
            raise serializers.ValidationError({'requested_quantity': '需求数量至少为 1'})
        from .order_pop import apply_product_defaults_to_validated
        apply_product_defaults_to_validated(
            data, instance=self.instance, fill_spec=self.instance is None,
        )
        template = data.get('requested_os_template')
        if template is None and self.instance is not None and 'requested_os_template' not in data:
            template = self.instance.requested_os_template
        requested_os = (data.get('requested_os') or '').strip()
        if template:
            data['requested_os'] = template.display_label
        elif requested_os:
            from system_manager.os_templates import match_os_template
            matched = match_os_template(requested_os)
            if matched:
                data['requested_os_template'] = matched
                data['requested_os'] = matched.display_label
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        from accounts.permissions import (
            channel_owns_customer,
            is_channel_role,
            require_bound_channel,
        )
        if is_channel_role(user):
            channel = require_bound_channel(user)
            customer = data.get('customer', getattr(self.instance, 'customer', None))
            if customer is None:
                data['customer'] = channel
            elif not channel_owns_customer(channel, customer):
                raise serializers.ValidationError({'customer': '只能为本渠道或名下客户下单'})
        return data

    def create(self, validated_data):
        from django.db import transaction
        from rest_framework.exceptions import ValidationError
        from service_manager.capacity_reservation import (
            CapacityReservationError, reserve_for_order,
        )
        from service_manager.fx_snapshot import apply_fx_fields

        product = validated_data.get('product')
        apply_fx_fields(
            validated_data,
            quote_currency=getattr(product, 'currency', None),
        )
        with transaction.atomic():
            order = super().create(validated_data)
            try:
                reserve_for_order(order)
            except CapacityReservationError as exc:
                raise ValidationError({
                    'preferred_pop': str(exc),
                    'capacity_shortages': exc.shortages,
                }) from exc
        return order

    def update(self, instance, validated_data):
        from django.db import transaction
        from rest_framework.exceptions import ValidationError
        from service_manager.capacity_reservation import (
            CapacityReservationError, reserve_for_order,
        )

        # 详情接口不会回传明文密码；编辑时留空即表示保持旧值。
        if validated_data.get('edge_socks5_pass') in ('', '已设置'):
            validated_data.pop('edge_socks5_pass')
        spec_fields = {
            'preferred_pop', 'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb',
            'requested_bandwidth_mbps', 'requested_ip_count', 'requested_quantity',
            'order_type',
        }
        with transaction.atomic():
            order = super().update(instance, validated_data)
            if order.status in (
                ServiceOrder.OrderStatus.DRAFT,
                ServiceOrder.OrderStatus.PENDING,
            ) and spec_fields.intersection(validated_data):
                try:
                    reserve_for_order(order)
                except CapacityReservationError as exc:
                    raise ValidationError({
                        'preferred_pop': str(exc),
                        'capacity_shortages': exc.shortages,
                    }) from exc
        return order


class ServiceInquiryReplySerializer(serializers.ModelSerializer):
    entry_type_label = serializers.CharField(source='get_entry_type_display', read_only=True)
    status_after_label = serializers.CharField(source='get_status_after_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInquiryReply
        fields = [
            'id', 'entry_type', 'entry_type_label',
            'content', 'status_after', 'status_after_label',
            'operator', 'operator_name', 'created_at',
        ]
        read_only_fields = fields

    def get_operator_name(self, obj):
        if obj.operator:
            return obj.operator.get_full_name() or obj.operator.username
        return '系统'


class ServiceInquirySerializer(serializers.ModelSerializer):
    inquiry_type_label = serializers.CharField(source='get_inquiry_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    handler_name = serializers.SerializerMethodField()
    replied_by_name = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    submitted_by_name = serializers.SerializerMethodField()
    related_order_no = serializers.CharField(source='related_order.order_no', read_only=True)
    related_ticket = serializers.SerializerMethodField()
    related_ticket_no = serializers.SerializerMethodField()
    reply_records = ServiceInquiryReplySerializer(many=True, read_only=True)
    reply_count = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInquiry
        fields = [
            'id', 'inquiry_no', 'inquiry_type', 'inquiry_type_label',
            'status', 'status_label',
            'contact_name', 'company_name', 'phone', 'email', 'preferred_contact',
            'requirements', 'source_url',
            'customer', 'customer_name', 'submitted_by', 'submitted_by_name',
            'related_order', 'related_order_no', 'converted_at',
            'related_ticket', 'related_ticket_no',
            'handler', 'handler_name',
            'reply_content', 'replied_by', 'replied_by_name', 'replied_at',
            'reply_count', 'reply_records',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'inquiry_no', 'status', 'handler', 'reply_content',
            'replied_by', 'replied_at', 'created_at', 'updated_at',
            'customer', 'submitted_by', 'related_order', 'converted_at',
        ]

    def get_handler_name(self, obj):
        return obj.handler.get_full_name() or obj.handler.username if obj.handler else ''

    def get_replied_by_name(self, obj):
        return obj.replied_by.get_full_name() or obj.replied_by.username if obj.replied_by else ''

    def get_submitted_by_name(self, obj):
        return obj.submitted_by.get_full_name() or obj.submitted_by.username if obj.submitted_by else ''

    def get_reply_count(self, obj):
        return obj.reply_records.count()

    def _related_ticket(self, obj):
        try:
            return obj.related_ticket
        except ObjectDoesNotExist:
            return None

    def get_related_ticket(self, obj):
        ticket = self._related_ticket(obj)
        return ticket.pk if ticket else None

    def get_related_ticket_no(self, obj):
        ticket = self._related_ticket(obj)
        return ticket.ticket_no if ticket else ''


class PublicServiceInquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceInquiry
        fields = [
            'inquiry_type', 'contact_name', 'company_name', 'phone', 'email',
            'preferred_contact', 'requirements', 'source_url',
        ]

    def validate(self, attrs):
        if not (attrs.get('phone', '').strip() or attrs.get('email', '').strip()):
            raise serializers.ValidationError({'phone': '请至少填写联系电话或联系邮箱'})
        return attrs


class InquiryReplySerializer(serializers.Serializer):
    reply_content = serializers.CharField(max_length=4000)
    entry_type = serializers.ChoiceField(
        choices=[
            ServiceInquiryReply.EntryType.REPLY,
            ServiceInquiryReply.EntryType.NOTE,
        ],
        default=ServiceInquiryReply.EntryType.REPLY,
    )
    status = serializers.ChoiceField(
        choices=[
            ServiceInquiry.InquiryStatus.PROCESSING,
            ServiceInquiry.InquiryStatus.REPLIED,
            ServiceInquiry.InquiryStatus.CLOSED,
        ],
        default=ServiceInquiry.InquiryStatus.REPLIED,
    )


class InquiryConvertSerializer(serializers.Serializer):
    """把官网咨询转成可审批、可交付的服务工单。"""

    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all(),
        required=False,
    )
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.filter(status='online'),
    )
    preferred_pop = serializers.PrimaryKeyRelatedField(
        queryset=PopPoint.objects.all(),
        required=False,
        allow_null=True,
    )
    handler = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    quoted_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
    )
    requested_quantity = serializers.IntegerField(min_value=1, required=False, default=1)

    def validate(self, attrs):
        product = attrs['product']
        preferred_pop = attrs.get('preferred_pop')
        if (
            preferred_pop
            and product.pop_point_id
            and preferred_pop.pk != product.pop_point_id
        ):
            raise serializers.ValidationError({
                'preferred_pop': '所选 POP 必须与产品所属 POP 一致',
            })
        return attrs


class InquiryConvertTicketSerializer(serializers.Serializer):
    """把已绑定客户的官网咨询转为技术工单（与转服务工单独立）。"""

    category = serializers.ChoiceField(
        choices=SupportTicket.Category.choices,
        default=SupportTicket.Category.TECHNICAL,
    )
    priority = serializers.ChoiceField(
        choices=SupportTicket.Priority.choices,
        default=SupportTicket.Priority.P3,
    )
    handler = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )


class SupportTicketMessageSerializer(serializers.ModelSerializer):
    author_type_label = serializers.CharField(source='get_author_type_display', read_only=True)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketMessage
        fields = [
            'id', 'author_type', 'author_type_label', 'is_internal',
            'content', 'author', 'author_name', 'created_at',
        ]
        read_only_fields = fields

    def get_author_name(self, obj):
        if obj.author:
            return obj.author.get_full_name() or obj.author.username
        return '系统' if obj.author_type == SupportTicketMessage.AuthorType.STAFF else '客户'


class SupportTicketListSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    priority_label = serializers.CharField(source='get_priority_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    source_label = serializers.CharField(source='get_source_display', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    handler_name = serializers.SerializerMethodField()
    related_instance_no = serializers.CharField(
        source='related_instance.instance_no', read_only=True, default='',
    )
    related_inquiry_no = serializers.CharField(
        source='related_inquiry.inquiry_no', read_only=True, default='',
    )
    message_count = serializers.IntegerField(read_only=True, default=0)
    sla_response_overdue = serializers.SerializerMethodField()
    sla_resolve_overdue = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            'id', 'ticket_no', 'customer', 'customer_name',
            'category', 'category_label', 'priority', 'priority_label',
            'status', 'status_label', 'source', 'source_label',
            'subject', 'handler', 'handler_name',
            'related_instance', 'related_instance_no',
            'related_inquiry', 'related_inquiry_no',
            'first_response_due', 'resolve_due',
            'first_responded_at', 'resolved_at', 'closed_at',
            'escalation_level', 'escalated_at',
            'sla_response_breached', 'sla_resolve_breached',
            'sla_response_overdue', 'sla_resolve_overdue',
            'message_count', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_handler_name(self, obj):
        return obj.handler.get_full_name() or obj.handler.username if obj.handler else ''

    def get_sla_response_overdue(self, obj):
        if obj.first_responded_at or not obj.first_response_due:
            return False
        from django.utils import timezone
        return timezone.now() > obj.first_response_due

    def get_sla_resolve_overdue(self, obj):
        if not obj.is_open or not obj.resolve_due:
            return False
        from django.utils import timezone
        return timezone.now() > obj.resolve_due


class SupportTicketSerializer(SupportTicketListSerializer):
    messages = SupportTicketMessageSerializer(many=True, read_only=True)
    description = serializers.CharField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_by_name = serializers.SerializerMethodField()

    class Meta(SupportTicketListSerializer.Meta):
        fields = SupportTicketListSerializer.Meta.fields + [
            'description', 'created_by', 'created_by_name', 'messages',
        ]

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return ''


class SupportTicketWriteSerializer(serializers.Serializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.all())
    category = serializers.ChoiceField(
        choices=SupportTicket.Category.choices,
        default=SupportTicket.Category.TECHNICAL,
    )
    priority = serializers.ChoiceField(
        choices=SupportTicket.Priority.choices,
        default=SupportTicket.Priority.P3,
    )
    subject = serializers.CharField(max_length=200)
    description = serializers.CharField()
    related_instance = serializers.PrimaryKeyRelatedField(
        queryset=ServiceInstance.objects.all(),
        required=False,
        allow_null=True,
    )
    handler = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )


class SupportTicketReplySerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000)
    is_internal = serializers.BooleanField(required=False, default=False)
    status = serializers.ChoiceField(
        choices=SupportTicket.Status.choices,
        required=False,
    )


class SupportTicketAssignSerializer(serializers.Serializer):
    handler = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
    )


class SupportTicketCloseSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000, required=False, allow_blank=True, default='')


# ═══════════════════════════════════════════════════════════════
# § 3  服务实例
# ═══════════════════════════════════════════════════════════════

class ServiceInstanceBriefSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    service_type_label = serializers.CharField(source='get_service_type_display', read_only=True)
    resource_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInstance
        fields = [
            'id', 'instance_no', 'service_type', 'service_type_label',
            'status', 'status_label', 'resource_name',
            'is_trial', 'monthly_fee', 'fee_currency',
            'expire_time', 'grace_days', 'suspend_at', 'recycle_at',
            'provisioned_at',
        ]

    def get_resource_name(self, obj):
        if obj.vps_id:
            return f'[VPS] {obj.vps.name}' if obj.vps else None
        if obj.edge_id:
            return f'[EDGE] {obj.edge.name}' if obj.edge else None
        if obj.bandwidth_alloc_id:
            bw = obj.bandwidth_alloc
            return f'[BW] {bw.allocated_mbps}Mbps' if bw else None
        return None


class ServiceInstanceListSerializer(ServiceInstanceBriefSerializer):
    customer_name = serializers.CharField(source='service_order.customer.name', read_only=True)
    customer_no = serializers.CharField(source='service_order.customer.customer_no', read_only=True)
    order_no = serializers.CharField(source='service_order.order_no', read_only=True)
    ip_count = serializers.SerializerMethodField()
    vps_info = serializers.SerializerMethodField()
    edge_info = serializers.SerializerMethodField()

    class Meta(ServiceInstanceBriefSerializer.Meta):
        fields = ServiceInstanceBriefSerializer.Meta.fields + [
            'service_order', 'order_no',
            'customer_name', 'customer_no',
            'ip_count', 'vps_info', 'edge_info',
            'trial_start', 'trial_end', 'billing_start_date',
            'activated_at', 'suspended_at', 'terminated_at',
            'created_at',
        ]

    def get_ip_count(self, obj):
        return obj.ip_addresses.count()

    def get_vps_info(self, obj):
        if obj.vps:
            return {
                'id': obj.vps.pk, 'vps_no': obj.vps.vps_no,
                'name': obj.vps.name, 'status': obj.vps.status,
            }
        return None

    def get_edge_info(self, obj):
        if obj.edge:
            return {
                'id': obj.edge.pk, 'edge_no': obj.edge.edge_no,
                'name': obj.edge.name, 'protocol': obj.edge.protocol,
                'status': obj.edge.status,
            }
        return None


class ServiceInstanceDetailSerializer(ServiceInstanceListSerializer):
    ip_addresses = serializers.SerializerMethodField()
    allocations = serializers.SerializerMethodField()
    change_records = serializers.SerializerMethodField()
    recent_logs = serializers.SerializerMethodField()
    provisioned_by_name = serializers.SerializerMethodField()
    terminated_by_name = serializers.SerializerMethodField()
    bandwidth_info = serializers.SerializerMethodField()

    class Meta(ServiceInstanceListSerializer.Meta):
        fields = ServiceInstanceListSerializer.Meta.fields + [
            'vps', 'edge', 'bandwidth_alloc',
            'bandwidth_info', 'ip_addresses',
            'spec_snapshot',
            'provisioned_by', 'provisioned_by_name',
            'terminated_by', 'terminated_by_name',
            'remark', 'updated_at',
            'allocations', 'change_records', 'recent_logs',
        ]

    def get_ip_addresses(self, obj):
        rows = []
        for ip in obj.ip_addresses.select_related('ip_block').all():
            rows.append({
                'id': ip.pk,
                'address': ip.address,
                'prefix_len': ip.prefix_len,
                'ip_version': getattr(ip.ip_block, 'ip_version', None),
                'status': ip.status,
            })
        return rows

    def get_allocations(self, obj):
        allocs = obj.allocations.order_by('-created_at')[:20]
        return ServiceAllocationSerializer(allocs, many=True).data

    def get_change_records(self, obj):
        records = obj.change_records.order_by('-created_at')[:10]
        return ServiceChangeRecordListSerializer(records, many=True).data

    def get_recent_logs(self, obj):
        logs = obj.operation_logs.order_by('-created_at')[:10]
        return ServiceOperationLogSerializer(logs, many=True).data

    def get_provisioned_by_name(self, obj):
        if obj.provisioned_by:
            return obj.provisioned_by.get_full_name() or obj.provisioned_by.username
        return None

    def get_terminated_by_name(self, obj):
        if obj.terminated_by:
            return obj.terminated_by.get_full_name() or obj.terminated_by.username
        return None

    def get_bandwidth_info(self, obj):
        if obj.bandwidth_alloc:
            bw = obj.bandwidth_alloc
            return {
                'id': bw.pk, 'allocated_mbps': bw.allocated_mbps,
                'used_mbps': getattr(bw, 'used_mbps', None),
                'is_active': bw.is_active,
            }
        return None


class ServiceInstanceWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceInstance
        fields = [
            'service_order', 'service_type', 'status',
            'vps', 'edge', 'bandwidth_alloc', 'ip_addresses',
            'spec_snapshot', 'expire_time', 'grace_days',
            'suspend_at', 'recycle_at', 'remark',
            'monthly_fee', 'fee_currency',
            'is_trial', 'trial_start', 'trial_end', 'billing_start_date',
        ]
        read_only_fields = ['status']

    def validate(self, data):
        instance = self.instance
        service_order = data.get('service_order', getattr(instance, 'service_order', None))
        service_type = data.get('service_type', getattr(instance, 'service_type', None))
        vps = data.get('vps', getattr(instance, 'vps', None))
        edge = data.get('edge', getattr(instance, 'edge', None))
        bw = data.get('bandwidth_alloc', getattr(instance, 'bandwidth_alloc', None))
        ips = data.get('ip_addresses', None)

        if not service_order:
            raise serializers.ValidationError({'service_order': '必须关联服务工单'})
        if instance and 'service_order' in data and service_order.pk != instance.service_order_id:
            raise serializers.ValidationError({'service_order': '已创建的服务实例不可变更所属工单'})
        if not instance and service_order.status not in (
            ServiceOrder.OrderStatus.APPROVED,
            ServiceOrder.OrderStatus.PROVISIONING,
            ServiceOrder.OrderStatus.ACTIVE,
        ):
            raise serializers.ValidationError({'service_order': '只有已审批或开通中的工单可以交付资源'})

        expected_type = {
            ServiceOrder.BusinessType.VPS: ServiceInstance.ServiceType.VPS,
            ServiceOrder.BusinessType.EDGE_GRE: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.EDGE_WG: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.EDGE_SOCKS5: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.IP: ServiceInstance.ServiceType.IP_ADDRESS,
            ServiceOrder.BusinessType.BANDWIDTH: ServiceInstance.ServiceType.BANDWIDTH,
        }.get(service_order.business_type)
        if expected_type and service_type != expected_type:
            raise serializers.ValidationError({
                'service_type': f'该工单仅允许交付 {dict(ServiceInstance.ServiceType.choices)[expected_type]} 类型资源'
            })

        if service_type == ServiceInstance.ServiceType.VPS and not vps:
            raise serializers.ValidationError({'vps': 'VPS 类型服务必须绑定 VPS 资源'})
        if service_type == ServiceInstance.ServiceType.EDGE_ACCELERATION and not edge:
            raise serializers.ValidationError({'edge': '边缘加速类型服务必须绑定 EdgeAcceleration 资源'})
        if service_type == ServiceInstance.ServiceType.BANDWIDTH and not bw:
            raise serializers.ValidationError({'bandwidth_alloc': '带宽类型服务必须绑定带宽分配'})
        if service_type == ServiceInstance.ServiceType.IP_ADDRESS:
            effective_ips = ips if ips is not None else (
                instance.ip_addresses.all() if instance else []
            )
            if not effective_ips:
                raise serializers.ValidationError({'ip_addresses': 'IP 地址类型服务至少需要绑定一个 IP'})

        for field, resource in (('vps', vps), ('edge', edge)):
            if not resource:
                continue
            if resource.customer_id not in (None, service_order.customer_id):
                raise serializers.ValidationError({field: '资源已属于其他客户，不能交付给当前工单'})
            if (
                service_order.preferred_pop_id
                and resource.pop_point_id != service_order.preferred_pop_id
            ):
                raise serializers.ValidationError({field: '资源所属 POP 与工单期望 POP 不一致'})

        if bw:
            target = bw.vps or bw.edge
            if target and target.customer_id not in (None, service_order.customer_id):
                raise serializers.ValidationError({'bandwidth_alloc': '带宽目标资源已属于其他客户'})
            if service_order.preferred_pop_id and bw.pop_point_id != service_order.preferred_pop_id:
                raise serializers.ValidationError({'bandwidth_alloc': '带宽分配所属 POP 与工单期望 POP 不一致'})
        return data

    def update(self, instance, validated_data):
        expire_changed = 'expire_time' in validated_data or 'grace_days' in validated_data
        obj = super().update(instance, validated_data)
        if expire_changed:
            from service_manager.expiry_engine import apply_schedule_to_instance
            if apply_schedule_to_instance(obj, force=True):
                obj.save(update_fields=['suspend_at', 'recycle_at', 'updated_at'])
        return obj


# ═══════════════════════════════════════════════════════════════
# § 4  资源分配记录
# ═══════════════════════════════════════════════════════════════

class ServiceAllocationSerializer(serializers.ModelSerializer):
    action_type_label = serializers.CharField(source='get_action_type_display', read_only=True)
    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceAllocation
        fields = [
            'id', 'action_type', 'action_type_label',
            'description', 'resource_type', 'resource_id', 'resource_repr',
            'before_state', 'after_state',
            'operator', 'operator_name', 'remark', 'created_at',
        ]
        read_only_fields = fields

    def get_operator_name(self, obj):
        if obj.operator:
            return obj.operator.get_full_name() or obj.operator.username
        return '系统'


# ═══════════════════════════════════════════════════════════════
# § 5  服务变更记录
# ═══════════════════════════════════════════════════════════════

class ServiceChangeRecordListSerializer(serializers.ModelSerializer):
    change_type_label = serializers.CharField(source='get_change_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    customer_name = serializers.CharField(
        source='service_instance.service_order.customer.name', read_only=True,
    )
    # 添加实例编号，方便前端直接展示（不需要再查询关联对象）
    instance_no = serializers.CharField(source='service_instance.instance_no', read_only=True)
    related_order_no = serializers.CharField(source='related_order.order_no', read_only=True)
    related_order_status = serializers.CharField(source='related_order.status', read_only=True)
    payment_required = serializers.SerializerMethodField()
    payment_billing = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    handler_name = serializers.SerializerMethodField()
    executed_by_name = serializers.SerializerMethodField()
    rolled_back_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceChangeRecord
        fields = [
            'id', 'change_no', 'service_instance', 'instance_no',
            'related_order', 'related_order_no', 'related_order_status',
            'payment_required', 'payment_billing',
            'change_type', 'change_type_label',
            'status', 'status_label',
            'change_reason', 'customer_name',
            'price_delta', 'scheduled_at', 'executed_at',
            'requested_by', 'requested_by_name',
            'handler', 'handler_name',
            'executed_by', 'executed_by_name',
            'rolled_back_at', 'rolled_back_by', 'rolled_back_by_name',
            'created_at', 'updated_at',
        ]

    def get_requested_by_name(self, obj):
        if obj.requested_by:
            return obj.requested_by.get_full_name() or obj.requested_by.username
        return None

    def get_handler_name(self, obj):
        if obj.handler:
            return obj.handler.get_full_name() or obj.handler.username
        return None

    def get_executed_by_name(self, obj):
        if obj.executed_by:
            return obj.executed_by.get_full_name() or obj.executed_by.username
        return None

    def get_rolled_back_by_name(self, obj):
        if obj.rolled_back_by:
            return obj.rolled_back_by.get_full_name() or obj.rolled_back_by.username
        return None

    def get_payment_required(self, obj):
        from .workflow import change_requires_payment
        return change_requires_payment(obj)

    def get_payment_billing(self, obj):
        from .workflow import change_payment_snapshot
        return change_payment_snapshot(obj)


class ServiceChangeRecordDetailSerializer(ServiceChangeRecordListSerializer):
    approved_by_name = serializers.SerializerMethodField()

    class Meta(ServiceChangeRecordListSerializer.Meta):
        fields = ServiceChangeRecordListSerializer.Meta.fields + [
            'change_detail',
            'before_spec', 'after_spec',
            'approved_by', 'approved_by_name', 'approved_at',
            'reject_reason', 'remark',
        ]

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None


class ServiceChangeRecordWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceChangeRecord
        fields = [
            'service_instance', 'related_order', 'change_type', 'status',
            'change_reason', 'change_detail',
            'before_spec', 'after_spec',
            'price_delta', 'scheduled_at', 'handler',
            'reject_reason', 'remark',
        ]
        read_only_fields = ['status']


# ═══════════════════════════════════════════════════════════════
# § 6  服务操作日志
# ═══════════════════════════════════════════════════════════════

class ServiceOperationLogSerializer(serializers.ModelSerializer):
    action_type_label = serializers.CharField(source='get_action_type_display', read_only=True)
    operator_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='service_order.order_no', read_only=True)
    instance_no = serializers.CharField(source='service_instance.instance_no', read_only=True)

    class Meta:
        model = ServiceOperationLog
        fields = [
            'id', 'action_type', 'action_type_label',
            'description',
            'service_order', 'order_no',
            'service_instance', 'instance_no',
            'old_value', 'new_value',
            'operator', 'operator_name', 'ip_address',
            'created_at',
        ]
        read_only_fields = fields

    def get_operator_name(self, obj):
        if obj.operator:
            return obj.operator.get_full_name() or obj.operator.username
        return '系统'


# ═══════════════════════════════════════════════════════════════
# § 动作专用序列化器
# ═══════════════════════════════════════════════════════════════

class StatusChangeSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class ApproveOrderSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
    reject_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if not data['approved'] and not data.get('reject_reason'):
            raise serializers.ValidationError({'reject_reason': '驳回时必须填写驳回原因'})
        return data


class ChangeStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ServiceChangeRecord.ChangeStatus.choices)
    reject_reason = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, data):
        if data.get('status') == ServiceChangeRecord.ChangeStatus.REJECTED and not data.get('reject_reason'):
            raise serializers.ValidationError({'reject_reason': '驳回变更时必须填写原因'})
        return data


class IPManageSerializer(serializers.Serializer):
    ip_ids = serializers.ListField(
        child=serializers.IntegerField(), min_length=1,
        help_text='IPAddress 的 ID 列表',
    )


class AssignHandlerSerializer(serializers.Serializer):
    handler_id = serializers.IntegerField(help_text='处理人用户 ID')
    remark = serializers.CharField(required=False, allow_blank=True, default='')


class AdvanceStageSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=ServiceOrder.LifecycleStage.choices)
    detail = serializers.CharField(required=False, allow_blank=True, default='')
    is_trial = serializers.BooleanField(required=False, default=False)
    service_instance_id = serializers.IntegerField(required=False)


class OrderDeliverySerializer(serializers.Serializer):
    """工单人工交付：明确选择资源后统一绑定、激活与回写工单状态。"""

    vps_id = serializers.IntegerField(required=False, min_value=1)
    edge_id = serializers.IntegerField(required=False, min_value=1)
    bandwidth_allocation_id = serializers.IntegerField(required=False, min_value=1)
    ip_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )
    activate = serializers.BooleanField(default=True)
    is_trial = serializers.BooleanField(default=False)
    remark = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    quick_bootstrap = serializers.BooleanField(required=False, default=False)


class ServiceLifecycleRecordSerializer(serializers.ModelSerializer):
    stage_label = serializers.CharField(source='get_stage_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    submitted_by_name = serializers.SerializerMethodField()
    handled_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    order_no = serializers.CharField(source='service_order.order_no', read_only=True)
    instance_no = serializers.CharField(source='service_instance.instance_no', read_only=True)
    change_no = serializers.CharField(source='change_record.change_no', read_only=True)

    class Meta:
        model = ServiceLifecycleRecord
        fields = [
            'id', 'service_order', 'order_no',
            'service_instance', 'instance_no',
            'change_record', 'change_no',
            'stage', 'stage_label', 'status', 'status_label',
            'title', 'detail',
            'submitted_by', 'submitted_by_name', 'submitted_at',
            'handled_by', 'handled_by_name', 'started_at',
            'approved_by', 'approved_by_name', 'approved_at',
            'completed_at',
            'resource_before', 'resource_after', 'fee_snapshot',
            'reject_reason', 'remark', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def _name(self, user):
        if not user:
            return None
        return user.get_full_name() or user.username

    def get_submitted_by_name(self, obj):
        return self._name(obj.submitted_by)

    def get_handled_by_name(self, obj):
        return self._name(obj.handled_by)

    def get_approved_by_name(self, obj):
        return self._name(obj.approved_by)


class ServiceProvisionHookSerializer(serializers.ModelSerializer):
    event_label = serializers.CharField(source='get_event_display', read_only=True)
    hook_type_label = serializers.CharField(source='get_hook_type_display', read_only=True)

    class Meta:
        model = ServiceProvisionHook
        fields = [
            'id', 'name', 'event', 'event_label',
            'hook_type', 'hook_type_label',
            'business_types', 'endpoint_url', 'http_method',
            'headers', 'adapter_key', 'is_active',
            'timeout_seconds', 'remark',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def validate_endpoint_url(self, value):
        if not value:
            return value
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .hooks import validate_webhook_url
        try:
            return validate_webhook_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
            )


class OsImageSerializer(serializers.ModelSerializer):
    os_type_label = serializers.CharField(source='get_os_type_display', read_only=True)

    class Meta:
        model = OsImage
        fields = [
            'id', 'code', 'name', 'os_type', 'os_type_label', 'version',
            'description', 'enabled', 'sort_order', 'node_template_ref',
            'os_template',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class InstanceSnapshotSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    vps_no = serializers.CharField(source='vps.vps_no', read_only=True)
    instance_no = serializers.CharField(source='service_instance.instance_no', read_only=True, default='')
    customer_name = serializers.CharField(source='vps.customer.name', read_only=True, default='')

    class Meta:
        model = InstanceSnapshot
        fields = [
            'id', 'snapshot_no', 'vps', 'vps_no', 'service_instance', 'instance_no',
            'customer_name', 'name', 'status', 'status_label', 'note', 'size_mb',
            'created_at', 'expires_at', 'restored_at', 'updated_at',
        ]
        read_only_fields = fields


class SelfServiceJobSerializer(serializers.ModelSerializer):
    job_type_label = serializers.CharField(source='get_job_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    vps_no = serializers.CharField(source='vps.vps_no', read_only=True)
    instance_no = serializers.CharField(source='service_instance.instance_no', read_only=True, default='')
    customer_name = serializers.CharField(source='vps.customer.name', read_only=True, default='')
    os_image_name = serializers.CharField(source='os_image.name', read_only=True, default='')
    snapshot_no = serializers.CharField(source='snapshot.snapshot_no', read_only=True, default='')
    requested_by_name = serializers.SerializerMethodField()
    request_payload = serializers.SerializerMethodField()

    class Meta:
        model = SelfServiceJob
        fields = [
            'id', 'job_no', 'job_type', 'job_type_label', 'status', 'status_label',
            'vps', 'vps_no', 'service_instance', 'instance_no', 'customer_name',
            'os_image', 'os_image_name', 'snapshot', 'snapshot_no',
            'progress', 'request_payload', 'result_message', 'node_job_id',
            'requested_by', 'requested_by_name',
            'created_at', 'started_at', 'finished_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_requested_by_name(self, obj):
        if obj.requested_by:
            return obj.requested_by.get_full_name() or obj.requested_by.username
        return ''

    def get_request_payload(self, obj):
        payload = dict(obj.request_payload or {})
        payload.pop('password', None)
        return payload


class SelfServiceJobMessageSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
