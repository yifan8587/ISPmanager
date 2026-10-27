"""portal/serializers.py — 客户门户序列化器（仅暴露客户可见的安全字段）"""
import re
from decimal import Decimal
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers

from customer_manager.models import Customer, CustomerBilling, BillingItem, CustomerReferral
from customer_manager import payment_service as billing_payment_service
from product_manager import services as product_services
from product_manager.serializers import ProductDetailSerializer
from resource_manager.models import IPAttribute, VPS, EdgeAcceleration
from service_manager.models import (
    ServiceInquiry, ServiceInquiryReply, ServiceInstance, ServiceOrder,
    SupportTicket, SupportTicketMessage,
    order_delivery_summary,
)

from . import payment_service


# ═══════════════════════════════════════════════════════════════
# § 注册
# ═══════════════════════════════════════════════════════════════

class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField()
    contact_name = serializers.CharField(max_length=64)
    contact_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    company_name = serializers.CharField(max_length=256, required=False, allow_blank=True)
    promotion_code = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        write_only=True,
    )
    customer_type = serializers.ChoiceField(
        choices=['personal', 'enterprise'], default='personal', required=False,
    )

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('该用户名已被注册')
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_promotion_code(self, value):
        from customer_manager.channel_services import (
            ChannelServiceError,
            normalize_promotion_code,
            resolve_active_promotion,
        )

        if not value:
            return ''
        try:
            code = normalize_promotion_code(value)
            resolve_active_promotion(code)
        except ChannelServiceError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return code

    @transaction.atomic
    def create(self, validated_data):
        from accounts.models import UserProfile
        from customer_manager.channel_services import bind_customer_referral

        promotion_code = validated_data.pop('promotion_code', '')
        user = User.objects.create_user(
            username=validated_data['username'],
            password=validated_data['password'],
            email=validated_data.get('email', ''),
            first_name=validated_data.get('contact_name', ''),
        )
        customer = Customer.objects.create(
            name=validated_data.get('company_name') or validated_data['contact_name'],
            customer_type=validated_data.get('customer_type', 'personal'),
            status='active',
            contact_name=validated_data['contact_name'],
            contact_phone=validated_data.get('contact_phone', ''),
            contact_email=validated_data.get('email', ''),
            company_name=validated_data.get('company_name', ''),
            account=user,
        )
        if promotion_code:
            bind_customer_referral(customer, promotion_code)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role', 'updated_at'])
        return {'user': user, 'customer': customer}


# ═══════════════════════════════════════════════════════════════
# § 客户资料
# ═══════════════════════════════════════════════════════════════

class CustomerProfileSerializer(serializers.ModelSerializer):
    customer_type_label = serializers.CharField(source='get_customer_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    username = serializers.CharField(source='account.username', read_only=True)
    referral_channel_name = serializers.SerializerMethodField()
    referral_promotion_code = serializers.SerializerMethodField()
    promotion_discount_rate = serializers.SerializerMethodField()

    class Meta:
        model = Customer
        fields = [
            'id', 'customer_no', 'name', 'username',
            'customer_type', 'customer_type_label', 'status', 'status_label',
            'contact_name', 'contact_phone', 'contact_email',
            'company_name', 'company_name_short', 'unified_credit_code',
            'province', 'city', 'address',
            'balance', 'credit_limit', 'created_at',
            'reputation_mode', 'reputation_quota', 'reputation_balance',
            'referral_channel_name', 'referral_promotion_code', 'promotion_discount_rate',
        ]
        read_only_fields = [
            'id', 'customer_no', 'customer_type', 'status',
            'balance', 'credit_limit', 'created_at',
            'reputation_mode', 'reputation_quota', 'reputation_balance',
        ]

    def to_representation(self, instance):
        from customer_manager.reputation import serialize_reputation
        data = super().to_representation(instance)
        data.update(serialize_reputation(instance))
        if not data.get('reputation_visible'):
            data['reputation_mode'] = 'none'
            data['reputation_quota'] = '0.00'
            data['reputation_balance'] = '0.00'
        return data

    def _referral(self, obj):
        try:
            return obj.channel_referral
        except CustomerReferral.DoesNotExist:
            return None

    def get_referral_channel_name(self, obj):
        referral = self._referral(obj)
        return referral.channel.name if referral else ''

    def get_referral_promotion_code(self, obj):
        referral = self._referral(obj)
        return referral.promotion_code if referral else ''

    def get_promotion_discount_rate(self, obj):
        referral = self._referral(obj)
        return str(referral.promotion_discount_rate) if referral else '0.00'


# ═══════════════════════════════════════════════════════════════
# § 官网咨询（客户可见）
# ═══════════════════════════════════════════════════════════════

class PortalInquiryReplySerializer(serializers.ModelSerializer):
    """仅返回已实际发送给客户的回复，不泄露内部备注或指派记录。"""

    operator_name = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInquiryReply
        fields = ['id', 'content', 'operator_name', 'created_at']
        read_only_fields = fields

    def get_operator_name(self, obj):
        if obj.operator:
            return obj.operator.get_full_name() or obj.operator.username
        return '客服团队'


class PortalInquirySerializer(serializers.ModelSerializer):
    inquiry_type_label = serializers.CharField(source='get_inquiry_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    related_order_no = serializers.CharField(source='related_order.order_no', read_only=True)
    related_ticket_no = serializers.SerializerMethodField()
    replies = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInquiry
        fields = [
            'id', 'inquiry_no', 'inquiry_type', 'inquiry_type_label',
            'status', 'status_label', 'contact_name', 'company_name',
            'requirements', 'related_order_no', 'related_ticket_no', 'replies',
            'created_at', 'updated_at', 'replied_at',
        ]
        read_only_fields = fields

    def get_replies(self, obj):
        replies = [
            reply for reply in obj.reply_records.all()
            if reply.entry_type == ServiceInquiryReply.EntryType.REPLY
        ]
        return PortalInquiryReplySerializer(replies, many=True).data

    def get_related_ticket_no(self, obj):
        try:
            return obj.related_ticket.ticket_no
        except ObjectDoesNotExist:
            return ''


class PortalTicketMessageSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicketMessage
        fields = ['id', 'author_type', 'content', 'author_name', 'created_at']
        read_only_fields = fields

    def get_author_name(self, obj):
        if obj.author:
            return obj.author.get_full_name() or obj.author.username
        return '客服团队' if obj.author_type == SupportTicketMessage.AuthorType.STAFF else '我'


class PortalTicketSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    priority_label = serializers.CharField(source='get_priority_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    related_instance_no = serializers.CharField(
        source='related_instance.instance_no', read_only=True, default='',
    )
    messages = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            'id', 'ticket_no', 'category', 'category_label',
            'priority', 'priority_label', 'status', 'status_label',
            'subject', 'description',
            'related_instance', 'related_instance_no',
            'first_response_due', 'resolve_due',
            'first_responded_at', 'resolved_at', 'closed_at',
            'messages', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_messages(self, obj):
        items = [
            msg for msg in obj.messages.all()
            if not msg.is_internal
        ]
        return PortalTicketMessageSerializer(items, many=True).data


class PortalTicketWriteSerializer(serializers.Serializer):
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


class PortalTicketReplySerializer(serializers.Serializer):
    content = serializers.CharField(max_length=4000)


# ═══════════════════════════════════════════════════════════════
# § 服务实例
# ═══════════════════════════════════════════════════════════════

class PortalServiceItemSerializer(serializers.Serializer):
    """门户服务列表项（实例 + 直连资源）"""
    id = serializers.CharField()
    entry_type = serializers.CharField()
    instance_id = serializers.IntegerField(allow_null=True)
    instance_no = serializers.CharField()
    service_type = serializers.CharField()
    service_type_label = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    pop_name = serializers.CharField(allow_blank=True)
    product_name = serializers.CharField(allow_blank=True)
    spec_snapshot = serializers.JSONField()
    monthly_fee_effective = serializers.CharField()
    fee_currency = serializers.CharField()
    is_trial = serializers.BooleanField()
    activated_at = serializers.DateTimeField(allow_null=True)
    expire_time = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    resource = serializers.DictField(allow_null=True)
    can_change_request = serializers.BooleanField()
    needs_protocol_config = serializers.BooleanField(required=False)


class PortalServiceSerializer(serializers.ModelSerializer):
    service_type_label = serializers.CharField(source='get_service_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    pop_name = serializers.SerializerMethodField()
    monthly_fee_effective = serializers.SerializerMethodField()
    fee_currency = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    resource = serializers.SerializerMethodField()

    class Meta:
        model = ServiceInstance
        fields = [
            'id', 'instance_no', 'service_type', 'service_type_label',
            'status', 'status_label', 'pop_name', 'spec_snapshot',
            'monthly_fee_effective', 'fee_currency', 'product_name',
            'is_trial', 'activated_at', 'expire_time', 'created_at', 'resource',
        ]

    def _resource(self, obj):
        return obj.vps or obj.edge or obj.bandwidth_alloc

    def get_pop_name(self, obj):
        r = self._resource(obj)
        return getattr(getattr(r, 'pop_point', None), 'name', '') if r else ''

    def get_monthly_fee_effective(self, obj):
        try:
            return str(obj.effective_monthly_fee())
        except Exception:
            return str(obj.monthly_fee)

    def get_fee_currency(self, obj):
        try:
            return obj.effective_fee_currency()
        except Exception:
            return obj.fee_currency

    def get_product_name(self, obj):
        r = self._resource(obj)
        p = getattr(r, 'product', None) if r else None
        return p.name if p else ''

    def get_resource(self, obj):
        if obj.vps_id and obj.vps:
            return {'kind': 'vps', 'id': obj.vps.id, 'no': obj.vps.vps_no, 'name': obj.vps.name}
        if obj.edge_id and obj.edge:
            return {'kind': 'edge', 'id': obj.edge.id, 'no': obj.edge.edge_no, 'name': obj.edge.name}
        return None


# ═══════════════════════════════════════════════════════════════
# § 资源：VPS / 边缘加速
# ═══════════════════════════════════════════════════════════════

class PortalVPSSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    os_label = serializers.CharField(source='get_os_type_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    ip_delivery_mode = serializers.CharField(source='pop_point.ip_delivery_mode', read_only=True)
    ip_delivery_mode_label = serializers.CharField(
        source='pop_point.get_ip_delivery_mode_display', read_only=True,
    )
    region_name = serializers.CharField(source='pop_point.city.country.region.name', read_only=True, allow_null=True)
    country_name = serializers.CharField(source='pop_point.city.country.name', read_only=True, allow_null=True)
    city_name = serializers.CharField(source='pop_point.city.name', read_only=True, allow_null=True)
    zone_name = serializers.CharField(source='pop_point.availability_zone.name', read_only=True, allow_null=True)
    product_name = serializers.CharField(source='product.name', read_only=True, allow_null=True)
    has_console = serializers.SerializerMethodField()

    class Meta:
        model = VPS
        fields = [
            'id', 'vps_no', 'name', 'status', 'status_label',
            'pop_name', 'ip_delivery_mode', 'ip_delivery_mode_label',
            'region_name', 'country_name', 'city_name', 'zone_name',
            'vcpu', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps',
            'mgmt_ip', 'mgmt_port', 'mgmt_username',
            'os_type', 'os_label', 'os_version', 'kernel_version',
            'product_name', 'is_synced', 'has_console',
            'start_time', 'expire_time', 'last_synced_at', 'created_at',
        ]

    def get_has_console(self, obj):
        return bool(obj.source_ref)


class PortalVPSDetailSerializer(PortalVPSSerializer):
    ip_list = serializers.SerializerMethodField()
    interfaces = serializers.SerializerMethodField()

    class Meta(PortalVPSSerializer.Meta):
        fields = PortalVPSSerializer.Meta.fields + ['ip_list', 'interfaces', 'remark']

    def get_ip_list(self, obj):
        rows = []
        for ip in obj.ip_addresses.select_related('ip_block').all():
            rows.append({
                'id': ip.id,
                'address': str(ip.address) if ip.address else None,
                'prefix_len': ip.effective_prefix_len,
                'gateway': ip.effective_gateway,
                'netmask': ip.netmask,
                'is_primary': ip.is_primary,
                'status': ip.status,
            })
        return rows

    def get_interfaces(self, obj):
        return list(
            obj.vps_interfaces.values('id', 'ifname', 'mac_address', 'mtu', 'is_primary', 'bandwidth_mbps')
        )


class PortalEdgeSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    protocol_label = serializers.CharField(source='get_protocol_display', read_only=True)
    pop_name = serializers.CharField(source='pop_point.name', read_only=True)
    ip_delivery_mode = serializers.CharField(source='pop_point.ip_delivery_mode', read_only=True)
    ip_delivery_mode_label = serializers.CharField(
        source='pop_point.get_ip_delivery_mode_display', read_only=True,
    )
    region_name = serializers.CharField(source='pop_point.city.country.region.name', read_only=True, allow_null=True)
    country_name = serializers.CharField(source='pop_point.city.country.name', read_only=True, allow_null=True)
    city_name = serializers.CharField(source='pop_point.city.name', read_only=True, allow_null=True)
    zone_name = serializers.CharField(source='pop_point.availability_zone.name', read_only=True, allow_null=True)
    product_name = serializers.CharField(source='product.name', read_only=True, allow_null=True)
    allowed_protocols = serializers.SerializerMethodField()
    pop_mgmt_ip = serializers.SerializerMethodField()
    config_ready = serializers.SerializerMethodField()
    needs_protocol_config = serializers.SerializerMethodField()

    class Meta:
        model = EdgeAcceleration
        fields = [
            'id', 'edge_no', 'name', 'protocol', 'protocol_label',
            'status', 'status_label', 'pop_name',
            'ip_delivery_mode', 'ip_delivery_mode_label',
            'region_name', 'country_name', 'city_name', 'zone_name',
            'bandwidth_mbps', 'product_name',
            'is_synced', 'node_ifname', 'node_pushed',
            'allowed_protocols',
            'pop_mgmt_ip', 'config_ready', 'needs_protocol_config',
            'expire_time', 'created_at',
        ]

    def get_pop_mgmt_ip(self, obj):
        return getattr(obj.pop_point, 'mgmt_ip', None) or ''

    def get_config_ready(self, obj):
        from resource_manager.edge_provision import edge_config_ready
        ready, _missing = edge_config_ready(obj)
        return ready

    def get_needs_protocol_config(self, obj):
        from resource_manager.edge_provision import needs_protocol_config
        return needs_protocol_config(obj)

    def get_allowed_protocols(self, obj):
        from product_manager.accel import parse_accel_types
        types = [
            item for item in parse_accel_types(
                getattr(obj.product, 'accel_type', '') if obj.product_id else '',
            )
            if item in ('gre', 'wireguard')
        ]
        current = (obj.protocol or '').lower()
        if current in ('gre', 'wireguard') and current not in types:
            types.append(current)
        return types or ['gre', 'wireguard']


class PortalEdgeDetailSerializer(PortalEdgeSerializer):
    ip_list = serializers.SerializerMethodField()
    gre_config = serializers.SerializerMethodField()
    wg_config = serializers.SerializerMethodField()
    socks5_config = serializers.SerializerMethodField()
    config_checklist = serializers.SerializerMethodField()
    client_tunnel_guide = serializers.SerializerMethodField()
    tunnel_peer_ip = serializers.SerializerMethodField()

    class Meta(PortalEdgeSerializer.Meta):
        fields = PortalEdgeSerializer.Meta.fields + [
            'ip_list', 'gre_config', 'wg_config', 'socks5_config', 'remark', 'start_time',
            'config_checklist', 'client_tunnel_guide', 'tunnel_peer_ip',
        ]

    def get_ip_list(self, obj):
        from resource_manager.edge_provision import edge_business_ip_rows
        return edge_business_ip_rows(obj)

    def get_config_checklist(self, obj):
        from resource_manager.edge_provision import edge_config_checklist
        return edge_config_checklist(obj)

    def get_client_tunnel_guide(self, obj):
        from resource_manager.edge_provision import client_tunnel_guide
        return client_tunnel_guide(obj)

    def get_tunnel_peer_ip(self, obj):
        from resource_manager.edge_provision import client_tunnel_guide
        return (client_tunnel_guide(obj) or {}).get('your_tunnel_ip') or ''

    def _safe_config(self, obj, rel_name, fields):
        cfg = getattr(obj, rel_name, None)
        if not cfg:
            return None
        return {f: getattr(cfg, f) for f in fields if hasattr(cfg, f)}

    def get_gre_config(self, obj):
        return self._safe_config(obj, 'gre_config', (
            'id', 'local_ip', 'remote_ip', 'tunnel_ip', 'key', 'ttl', 'mtu',
            'pmtud', 'checksum',
        ))

    def get_wg_config(self, obj):
        cfg = getattr(obj, 'wg_config', None)
        if not cfg:
            return None
        return {
            'id': cfg.id,
            'listen_port': cfg.listen_port,
            'interface_ip': cfg.interface_ip,
            'public_key': cfg.public_key,
            'peer_public_key': cfg.peer_public_key,
            'peer_endpoint': cfg.peer_endpoint,
            'peer_allowed_ips': cfg.peer_allowed_ips,
            'persistent_keepalive': cfg.persistent_keepalive,
            'mtu': cfg.mtu,
            'dns': cfg.dns,
            'has_private_key': bool(cfg.private_key),
        }

    def get_socks5_config(self, obj):
        cfg = getattr(obj, 'socks5_config', None)
        if not cfg:
            return None
        return {
            'id': cfg.id,
            'listen_ip': cfg.listen_ip,
            'listen_port': cfg.listen_port,
            'auth_required': cfg.auth_required,
            'username': cfg.username,
            'udp_enabled': cfg.udp_enabled,
            'max_connections': cfg.max_connections,
        }


class PortalSetRootPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    username = serializers.CharField(required=False, allow_blank=True, max_length=32)

    def validate_username(self, value):
        value = (value or '').strip()
        if value and not re.fullmatch(r'[A-Za-z0-9._-]+', value):
            raise serializers.ValidationError('用户名仅允许字母、数字、点、下划线和短横线')
        return value


# ═══════════════════════════════════════════════════════════════
# § 账单
# ═══════════════════════════════════════════════════════════════

class PortalBillingItemSerializer(serializers.ModelSerializer):
    service_type_label = serializers.SerializerMethodField()

    class Meta:
        model = BillingItem
        fields = [
            'id', 'instance_no', 'description', 'service_type', 'service_type_label',
            'product', 'product_name', 'period_start', 'period_end',
            'days_used', 'days_in_month', 'is_prorated', 'is_trial',
            'unit_monthly_fee', 'base_monthly_fee', 'overage_monthly_fee',
            'source_currency', 'exchange_rate', 'amount_original', 'currency', 'amount',
            'detail',
        ]

    def get_service_type_label(self, obj):
        mapping = {'vps': 'VPS', 'edge': '边缘加速', 'ip': 'IP 地址', 'bandwidth': '带宽'}
        return mapping.get(obj.service_type, obj.service_type or '—')


class PortalBillingSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    billing_kind_label = serializers.CharField(source='get_billing_kind_display', read_only=True)
    payment_method_label = serializers.CharField(source='get_payment_method_display', read_only=True)
    outstanding_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    items = PortalBillingItemSerializer(many=True, read_only=True)

    class Meta:
        model = CustomerBilling
        fields = [
            'id', 'bill_no', 'bill_month',
            'gross_amount', 'promotion_discount_amount', 'promotion_code',
            'amount', 'currency', 'auto_generated',
            'paid_amount', 'refunded_amount', 'outstanding_amount',
            'status', 'status_label',
            'payment_method', 'payment_method_label', 'due_date', 'paid_at',
            'billing_kind', 'billing_kind_label',
            'refund_reason', 'refunded_at',
            'remark', 'created_at', 'items',
        ]


class PortalWalletRechargeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        min_value=Decimal('1.00'), max_value=Decimal('100000.00'),
    )


class PortalBillPaySerializer(serializers.Serializer):
    """客户门户账单支付。

    线上渠道以 ``channel_id`` 为准；兼容旧客户端时可仅传 payment_method，
    服务端会选择该服务商的首个已启用渠道。
    """
    payment_method = serializers.CharField()
    channel_id = serializers.IntegerField(required=False, min_value=1)

    def validate_payment_method(self, value):
        method = str(value or '').strip().lower()
        if method == 'reputation':
            return method
        if method not in payment_service.ONLINE_METHODS:
            raise serializers.ValidationError('不支持的支付方式')
        return method
    step = serializers.ChoiceField(choices=['init', 'confirm'], default='init', required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    session_id = serializers.CharField(required=False, allow_blank=True, max_length=64,
                                       help_text='兼容字段：线上支付交易号')


# ═══════════════════════════════════════════════════════════════
# § 服务工单（下单 / 变更）
# ═══════════════════════════════════════════════════════════════

class PortalOrderSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    order_type_label = serializers.CharField(source='get_order_type_display', read_only=True)
    business_type_label = serializers.CharField(source='get_business_type_display', read_only=True)
    auto_provision_status_label = serializers.CharField(
        source='get_auto_provision_status_display',
        read_only=True,
    )
    pop_name = serializers.CharField(source='preferred_pop.name', read_only=True, allow_null=True)
    product_name = serializers.CharField(source='product.name', read_only=True, allow_null=True)
    requested_os_template_label = serializers.CharField(
        source='requested_os_template.display_label', read_only=True, allow_null=True,
    )
    delivery_summary = serializers.SerializerMethodField()
    manual_delivery = serializers.SerializerMethodField()
    is_backorder = serializers.SerializerMethodField()

    class Meta:
        model = ServiceOrder
        fields = [
            'id', 'order_no', 'order_type', 'order_type_label',
            'business_type', 'business_type_label', 'status', 'status_label',
            'pop_name', 'product_name',
            'requested_vcpu', 'requested_ram_mb', 'requested_disk_gb', 'requested_os',
            'requested_os_template', 'requested_os_template_label',
            'requested_bandwidth_mbps', 'requested_ip_count', 'requested_quantity',
            'quoted_price', 'fx_quote_currency', 'fx_locked_at',
            'expected_delivery_at', 'is_backorder',
            'requirements', 'delivery_summary',
            'auto_provision_status', 'auto_provision_status_label', 'auto_provision_message',
            'manual_delivery',
            'created_at',
        ]

    def get_delivery_summary(self, obj):
        summary = order_delivery_summary(obj)
        return {
            key: summary[key]
            for key in (
                'status', 'expected_quantity', 'instance_count',
                'allocated_count', 'active_count', 'provisioning_count',
                'ip_count', 'bandwidth_mbps',
            )
        }

    def get_is_backorder(self, obj):
        delivery = (obj.request_snapshot or {}).get('delivery') or {}
        return bool(delivery.get('is_backorder'))

    def get_manual_delivery(self, obj):
        from service_manager.manual_delivery import portal_summary
        return portal_summary(obj)


class PortalOrderCreateSerializer(serializers.Serializer):
    """客户下单：根据产品 + 可选定制规格生成新开服务工单（待审批）。"""
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(default=1, min_value=1, max_value=20, required=False)
    requested_os = serializers.CharField(required=False, allow_blank=True, max_length=64)
    os_template_id = serializers.IntegerField(required=False, allow_null=True)
    username = serializers.CharField(required=False, allow_blank=True, max_length=32)
    password = serializers.CharField(required=False, allow_blank=True, min_length=8, max_length=128, write_only=True)
    sshkeys = serializers.CharField(required=False, allow_blank=True, max_length=8192)
    nameserver = serializers.CharField(required=False, allow_blank=True, max_length=128)
    requirements = serializers.CharField(required=False, allow_blank=True)
    source_url = serializers.CharField(required=False, allow_blank=True, max_length=512)
    cpu_cores = serializers.IntegerField(required=False, min_value=0)
    ram_mb = serializers.IntegerField(required=False, min_value=0)
    disk_gb = serializers.IntegerField(required=False, min_value=0)
    nic_count = serializers.IntegerField(required=False, min_value=0)
    bandwidth_mbps = serializers.IntegerField(required=False, min_value=0)
    ip_count = serializers.IntegerField(required=False, min_value=0)
    accel_type = serializers.CharField(required=False, allow_blank=True, max_length=32)

    def validate_product_id(self, value):
        from product_manager.models import Product
        try:
            self._product = Product.objects.select_related('pop_point').get(pk=value)
        except Product.DoesNotExist:
            raise serializers.ValidationError('产品不存在')
        if self._product.status != 'online':
            raise serializers.ValidationError('该产品当前不可下单')
        return value

    def validate(self, attrs):
        product = getattr(self, '_product', None)
        quantity = attrs.get('quantity', 1)
        if product and product.category == 'edge' and quantity > 1:
            raise serializers.ValidationError({
                'quantity': '边缘加速需要按每个对端配置分别开通，请一次提交一个服务请求'
            })
        from system_manager.models import SystemOsTemplate
        from system_manager.os_templates import match_os_template, product_allows_os_template

        self._os_template = None
        os_template_id = attrs.get('os_template_id')
        requested_os = (attrs.get('requested_os') or '').strip()
        if product and product.category == 'vps' and (os_template_id or requested_os):
            if os_template_id:
                template = SystemOsTemplate.objects.filter(pk=os_template_id, enabled=True).first()
                if template is None:
                    raise serializers.ValidationError({'os_template_id': '系统版本不存在或已停用'})
            else:
                template = match_os_template(requested_os, queryset=SystemOsTemplate.objects.filter(enabled=True))
            if template and not product_allows_os_template(product, template):
                raise serializers.ValidationError({'os_template_id': '该产品不支持所选系统版本'})
            self._os_template = template
            if template:
                attrs['requested_os'] = template.display_label
        username = (attrs.get('username') or '').strip()
        if username and not re.fullmatch(r'[A-Za-z0-9._-]+', username):
            raise serializers.ValidationError({'username': '用户名仅允许字母、数字、点、下划线和短横线'})
        attrs['username'] = username
        attrs['sshkeys'] = (attrs.get('sshkeys') or '').strip()
        attrs['nameserver'] = (attrs.get('nameserver') or '').strip()
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        product = self._product
        customer = self.context['customer']
        user = self.context['request'].user
        # 公网对接产品不预绑定协议；以前台客户选择的 GRE/WG 建单
        selected_accel = product.accel_type
        if product.category == 'edge':
            from product_manager.accel import default_edge_business_type, parse_accel_types
            requested = (validated_data.get('accel_type') or '').strip().lower()
            if requested == 'wg':
                requested = 'wireguard'
            allowed = parse_accel_types(product.accel_type)
            if requested in ('gre', 'wireguard') and (not allowed or requested in allowed):
                mapped = 'edge_wg' if requested == 'wireguard' else 'edge_gre'
                selected_accel = requested
            else:
                mapped = default_edge_business_type(product.accel_type)
            business_type = (
                ServiceOrder.BusinessType.EDGE_WG
                if mapped == 'edge_wg'
                else ServiceOrder.BusinessType.EDGE_GRE
            )
        else:
            business_type = ServiceOrder.BusinessType.VPS
        spec = product_services.normalize_spec_for_category(product, {
            'cpu_cores': validated_data.get('cpu_cores', product.cpu_cores),
            'ram_mb': validated_data.get('ram_mb', product.ram_mb),
            'disk_gb': validated_data.get('disk_gb', product.disk_gb),
            'nic_count': validated_data.get('nic_count', product.nic_count),
            'bandwidth_mbps': validated_data.get('bandwidth_mbps', product.bandwidth_mbps),
            'ip_count': validated_data.get('ip_count', product.ip_count),
        })
        price = product_services.compute_price(product, spec)
        capacity = product_services.check_spec_against_pop(product.pop_point, spec)
        estimate = product_services.resolve_provision_estimate(product, capacity=capacity)
        quantity = validated_data.get('quantity', 1)
        money_quantum = Decimal('0.01')
        unit_monthly_price = Decimal(str(price['monthly_price'])).quantize(money_quantum)
        unit_setup_price = Decimal(str(price.get('setup_price') or 0)).quantize(money_quantum)
        monthly_total = (unit_monthly_price * quantity).quantize(money_quantum)
        setup_total = (unit_setup_price * quantity).quantize(money_quantum)
        first_payment_total = (monthly_total + setup_total).quantize(money_quantum)
        request = self.context['request']
        from service_manager.fx_snapshot import build_fx_fields
        request_snapshot = {
            'source': 'portal',
            'submitted_at': None,
            'source_url': (
                validated_data.get('source_url', '').strip()
                or request.build_absolute_uri(request.get_full_path())
            ),
            'product': {
                'id': product.id,
                'product_no': product.product_no,
                'name': product.name,
                'category': product.category,
                'accel_type': selected_accel,
                'billing_mode': price.get('billing_mode'),
                'billing_mode_label': price.get('billing_mode_label'),
                'transfer_quota_gb': price.get('transfer_quota_gb'),
                'overage_unit_price': price.get('overage_unit_price'),
                'overage_unit_label': price.get('overage_unit_label'),
                'commit_mbps': price.get('commit_mbps'),
            },
            'quantity': quantity,
            'requested_spec': spec,
            'requested_os': validated_data.get('requested_os', ''),
            'os_template_id': getattr(self, '_os_template', None).pk if getattr(self, '_os_template', None) else None,
            'cloud_init': {
                'username': validated_data.get('username', ''),
                'password': validated_data.get('password', ''),
                'sshkeys': validated_data.get('sshkeys', ''),
                'nameserver': validated_data.get('nameserver', ''),
            },
            'requirements': validated_data.get('requirements', ''),
            'quote': {
                'unit_monthly_price': str(unit_monthly_price),
                'monthly_total': str(monthly_total),
                'unit_setup_price': str(unit_setup_price),
                'setup_total': str(setup_total),
                'first_payment_total': str(first_payment_total),
                'currency': price.get('currency') or product.currency or 'USD',
                'breakdown': price.get('breakdown') or [],
                'billing': {
                    'billing_mode': price.get('billing_mode'),
                    'billing_mode_label': price.get('billing_mode_label'),
                    'transfer_quota_gb': price.get('transfer_quota_gb'),
                    'overage_unit_price': price.get('overage_unit_price'),
                    'overage_unit_label': price.get('overage_unit_label'),
                    'commit_mbps': price.get('commit_mbps'),
                },
            },
            'delivery': {
                'estimated_provision_minutes': estimate.get('estimated_provision_minutes'),
                'is_backorder': estimate.get('is_backorder'),
                'expected_delivery_at': (
                    estimate['expected_delivery_at'].isoformat()
                    if estimate.get('expected_delivery_at') else None
                ),
            },
        }
        order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=business_type,
            status=ServiceOrder.OrderStatus.PENDING,
            current_stage=ServiceOrder.LifecycleStage.ORDER,
            source=ServiceOrder.OrderSource.PORTAL,
            customer=customer,
            product=product,
            preferred_pop=product.pop_point,
            requested_vcpu=spec['cpu_cores'] or None,
            requested_ram_mb=spec['ram_mb'] or None,
            requested_disk_gb=spec['disk_gb'] or None,
            requested_os=validated_data.get('requested_os', ''),
            requested_os_template=getattr(self, '_os_template', None),
            requested_bandwidth_mbps=spec['bandwidth_mbps'] or None,
            requested_ip_count=spec['ip_count'] or None,
            requested_quantity=quantity,
            quoted_price=first_payment_total,
            requirements=validated_data.get('requirements', ''),
            request_snapshot=request_snapshot,
            expected_delivery_at=estimate.get('expected_delivery_at'),
            remark=f'门户下单 · 产品[{product.product_no}] {product.name} ×{quantity}',
            created_by=user,
            **build_fx_fields(quote_currency=price.get('currency') or product.currency or 'USD'),
        )
        try:
            from service_manager.capacity_reservation import (
                CapacityReservationError, reserve_for_order,
            )
            reserve_for_order(order)
        except CapacityReservationError as exc:
            raise serializers.ValidationError({
                'product_id': str(exc),
                'capacity_shortages': exc.shortages,
            }) from exc
        try:
            from service_manager.lifecycle import bootstrap_order_lifecycle
            from service_manager.notify_helpers import notify_order_event
            bootstrap_order_lifecycle(order, submitted_by=user)
            notify_order_event(order, '门户下单', event='service_order')
        except Exception:  # noqa: BLE001
            pass
        return order


class PortalDirectCheckoutSerializer(PortalOrderCreateSerializer):
    """产品直付下单：复用门户订单校验，并额外锁定一个有效线上支付渠道。"""

    channel_id = serializers.IntegerField(min_value=1, required=False)
    payment_method = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from system_manager.models import PaymentChannel
        from customer_manager.reputation import can_pay_with_reputation, required_reputation

        product = getattr(self, '_product', None)
        if product is None:
            raise serializers.ValidationError({'product_id': '产品不存在'})
        method = str(attrs.get('payment_method') or '').strip().lower()
        self._use_reputation = method == 'reputation'
        self._reputation_needed = None
        self._payment_channel = None
        if self._use_reputation:
            customer = self.context.get('customer')
            spec = product_services.normalize_spec_for_category(product, {
                'cpu_cores': attrs.get('cpu_cores', product.cpu_cores),
                'ram_mb': attrs.get('ram_mb', product.ram_mb),
                'disk_gb': attrs.get('disk_gb', product.disk_gb),
                'nic_count': attrs.get('nic_count', product.nic_count),
                'bandwidth_mbps': attrs.get('bandwidth_mbps', product.bandwidth_mbps),
                'ip_count': attrs.get('ip_count', product.ip_count),
            })
            if not can_pay_with_reputation(
                customer, product, quantity=attrs.get('quantity', 1), custom_spec=spec,
            ):
                raise serializers.ValidationError({
                    'payment_method': '当前账号无法使用信誉值支付该产品',
                })
            self._reputation_needed = required_reputation(
                product, attrs.get('quantity', 1), spec,
            )
            return attrs

        channel_id = attrs.get('channel_id')
        channel = PaymentChannel.objects.filter(pk=channel_id, enabled=True).first() if channel_id else None
        if channel is None:
            raise serializers.ValidationError({'channel_id': '支付渠道不存在或未启用'})
        available_channel_ids = {
            item.id for item in billing_payment_service.available_channels(product.currency)
        }
        if channel.id not in available_channel_ids:
            raise serializers.ValidationError({
                'channel_id': '该支付渠道不支持此产品币种或支付配置未完成',
            })
        self._payment_channel = channel
        return attrs

    @property
    def payment_channel(self):
        return self._payment_channel

    @property
    def use_reputation(self):
        return bool(getattr(self, '_use_reputation', False))

    @property
    def reputation_needed(self):
        return getattr(self, '_reputation_needed', None)


class PortalProductDetailSerializer(ProductDetailSerializer):
    """门户产品详情：IP 属性取自所属 POP 点。"""
    ip_attribute_list = serializers.SerializerMethodField()

    class Meta(ProductDetailSerializer.Meta):
        fields = ProductDetailSerializer.Meta.fields + ['ip_attribute_list']

    def get_ip_attribute_list(self, obj):
        pop = obj.pop_point if obj.pop_point_id else None
        value = (getattr(pop, 'ip_attribute', '') or '').strip()
        if not value:
            return []
        labels = dict(IPAttribute.choices)
        return [{
            'value': value,
            'label': labels.get(value, value),
            'count': 1,
        }]


class ChangeRequestSerializer(serializers.Serializer):
    """对已有服务实例发起变更（规格变更走报价；续期走出账支付；终止仍为工单申请）。"""
    change_type = serializers.ChoiceField(
        choices=['upgrade', 'downgrade', 'renew', 'terminate', 'change'],
        required=False,
    )
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'quarterly', 'semi_annual', 'annual'],
        required=False,
    )
    requested_vcpu = serializers.IntegerField(required=False, min_value=1)
    requested_ram_mb = serializers.IntegerField(required=False, min_value=256)
    requested_disk_gb = serializers.IntegerField(required=False, min_value=1)
    requested_bandwidth_mbps = serializers.IntegerField(required=False, min_value=0)
    requested_ip_count = serializers.IntegerField(required=False, min_value=0)
    release_ip_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    add_ip_count = serializers.IntegerField(required=False, min_value=0)
    replace_ips = serializers.BooleanField(required=False, default=False)
    requirements = serializers.CharField(required=False, allow_blank=True)
    channel_id = serializers.IntegerField(required=False, min_value=1)
    payment_method = serializers.CharField(required=False, allow_blank=True)
    source_url = serializers.CharField(required=False, allow_blank=True, max_length=512)


class RenewQuoteSerializer(serializers.Serializer):
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'quarterly', 'semi_annual', 'annual'],
        required=False,
    )


class RenewRequestSerializer(serializers.Serializer):
    billing_cycle = serializers.ChoiceField(
        choices=['monthly', 'quarterly', 'semi_annual', 'annual'],
        required=False,
    )
    requirements = serializers.CharField(required=False, allow_blank=True)
    channel_id = serializers.IntegerField(required=False, min_value=1)
    payment_method = serializers.CharField(required=False, allow_blank=True)


class ChangeQuoteSerializer(serializers.Serializer):
    """服务变更报价请求。"""
    requested_vcpu = serializers.IntegerField(required=False, min_value=1)
    requested_ram_mb = serializers.IntegerField(required=False, min_value=256)
    requested_disk_gb = serializers.IntegerField(required=False, min_value=1)
    requested_bandwidth_mbps = serializers.IntegerField(required=False, min_value=0)
    requested_ip_count = serializers.IntegerField(required=False, min_value=0)
    release_ip_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    add_ip_count = serializers.IntegerField(required=False, min_value=0)
    replace_ips = serializers.BooleanField(required=False, default=False)
