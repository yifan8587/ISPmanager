"""portal/views.py — 客户自助门户接口

公共接口（无需登录）：产品列表/详情、地区树、注册。
客户接口（需客户账号）：总览、服务、订单、账单、资源、资料、变更申请。
"""
import logging
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from accounts.idle_session import IdleJWTAuthentication as JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import check_permission
from accounts.serializers import serialize_auth_user
from customer_manager.models import CustomerBalanceLedger, CustomerBilling, BillingItem
from customer_manager import payment_service as billing_payment_service
from product_manager import services as product_services
from product_manager.models import Product
from product_manager.serializers import CustomSpecSerializer, ProductListSerializer
from product_manager.views import ProductFilter
from resource_manager.models import City, Country, EdgeAcceleration, NetworkTypeOption, Region, VPS
from service_manager.models import (
    InstanceSnapshot, OsImage, SelfServiceJob, ServiceInquiry, ServiceInstance, ServiceOrder, SupportTicket,
)
from service_manager.serializers import (
    InstanceSnapshotSerializer, OsImageSerializer, SelfServiceJobSerializer,
)
from service_manager.serializers import PublicServiceInquirySerializer

from .permissions import IsCustomer, PortalActionPermissionMixin, request_customer
from backend.throttles import PublicRegisterRateThrottle
from .throttles import CustomerMonitoringRateThrottle, PublicInquiryRateThrottle
from customer_manager.serializers import CustomerBalanceLedgerSerializer
from .serializers import (
    ChangeQuoteSerializer, ChangeRequestSerializer, CustomerProfileSerializer, PortalBillPaySerializer,
    PortalBillingSerializer, PortalEdgeDetailSerializer, PortalEdgeSerializer,
    PortalDirectCheckoutSerializer, PortalOrderCreateSerializer, PortalOrderSerializer,
    PortalWalletRechargeSerializer,
    RenewQuoteSerializer, RenewRequestSerializer,
    PortalProductDetailSerializer,
    PortalInquirySerializer,
    PortalTicketSerializer, PortalTicketWriteSerializer, PortalTicketReplySerializer,
    PortalServiceItemSerializer, PortalServiceSerializer, PortalSetRootPasswordSerializer,
    PortalVPSDetailSerializer, PortalVPSSerializer, RegisterSerializer,
)
from . import monitoring, payment_service, service_list

logger = logging.getLogger(__name__)


def _portal_payment_return_url(request, path: str = '/console/orders') -> str:
    """生成支付回跳的门户地址，不信任任意 Origin 头。"""
    from backend.portal_channel import portal_return_origin

    pinned = portal_return_origin(request)
    if pinned:
        return f'{pinned}{path}'
    origin = str(request.headers.get('Origin') or '').strip().rstrip('/')
    configured_origins = set(getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or [])
    allow_all = bool(getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False))
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme in {'http', 'https'}
            and parsed.netloc
            and (allow_all or origin in configured_origins)
        ):
            return f'{origin}{path}'
    return request.build_absolute_uri(path)


def start_portal_billing_checkout(request, billing, *, return_path='/console/bills'):
    """为门户账单发起网关结账，失败时返回可读原因。"""
    from django.urls import reverse
    from system_manager.models import PaymentChannel

    channel_id = request.data.get('channel_id')
    payment_method = str(request.data.get('payment_method') or '').strip()
    if not channel_id and not payment_method:
        return None, None, ''

    channel = None
    if channel_id:
        channel = PaymentChannel.objects.filter(pk=channel_id, enabled=True).first()
    if channel is None and payment_method:
        channel = PaymentChannel.objects.filter(
            provider=payment_method, enabled=True,
        ).order_by('sort_order', 'id').first()
    if channel is None:
        return None, None, '请选择有效支付渠道后继续支付'

    available_ids = {
        item.id for item in billing_payment_service.available_channels(billing.currency, billing=billing)
    }
    if channel.id not in available_ids:
        return None, None, '所选支付渠道不支持账单币种'

    frontend_return_url = _portal_payment_return_url(request, return_path)
    from system_manager.payment_urls import browser_absolute_url, payment_callback_url
    callback_url = payment_callback_url(channel.id, request)
    payment = billing_payment_service.create_payment_transaction(
        billing=billing,
        channel=channel,
        operator=request.user,
        return_url=frontend_return_url,
    )
    gateway_return_url = browser_absolute_url(
        request,
        reverse('payment-return', kwargs={'transaction_no': payment.transaction_no}),
    )
    payment, checkout = billing_payment_service.issue_gateway_checkout(
        payment=payment,
        callback_url=callback_url,
        gateway_return_url=gateway_return_url,
    )
    transaction_payload = {
        'transaction_no': payment.transaction_no,
        'status': payment.status,
        'amount': str(payment.amount),
        'currency': payment.currency,
    }
    return transaction_payload, checkout, ''


class SupportConfigView(APIView):
    """公开客服入口：仅返回对外联系方式，不读取 IM 对内告警目标。"""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from system_manager.support_public import get_public_support_config

        response = Response(get_public_support_config())
        response['Cache-Control'] = 'no-store'
        return response


class SupportInquiryView(APIView):
    """官网访客提交需要帮助或测试申请；登录客户会自动绑定其客户档案。"""

    permission_classes = [AllowAny]
    authentication_classes = [JWTAuthentication]
    throttle_classes = [PublicInquiryRateThrottle]

    def post(self, request):
        serializer = PublicServiceInquirySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = request_customer(request)
        inquiry = serializer.save(
            customer=customer,
            submitted_by=request.user if customer else None,
        )
        from service_manager.notify_helpers import notify_inquiry_event
        notify_inquiry_event(inquiry)
        return Response(
            {'inquiry_no': inquiry.inquiry_no, 'detail': '需求已提交，我们将尽快与您联系。'},
            status=status.HTTP_201_CREATED,
        )


class MyInquiryViewSet(viewsets.ReadOnlyModelViewSet):
    """客户门户中的“我的咨询”，仅展示当前客户自己的咨询和正式回复。"""

    permission_classes = [IsCustomer]
    serializer_class = PortalInquirySerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['inquiry_no', 'requirements']
    ordering_fields = ['created_at', 'updated_at', 'replied_at']
    ordering = ['-created_at']

    def get_queryset(self):
        customer = request_customer(self.request)
        if customer is None:
            return ServiceInquiry.objects.none()
        return (
            ServiceInquiry.objects.filter(customer=customer)
            .select_related('related_order', 'related_ticket')
            .prefetch_related('reply_records__operator')
        )


def _issue_tokens(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def _ip_only(cidr):
    """从 'a.b.c.d/30' 中取出 IP 部分。"""
    if not cidr:
        return ''
    return str(cidr).split('/')[0].strip()


def check_edge_ip_conflicts(edge, proto, data, config):
    """校验隧道 / 接口 IP 是否与同 POP 下其它边缘加速冲突，或存在完全相同的配置。

    参数：
      edge   ：当前边缘加速（EdgeAcceleration）
      proto  ：协议（gre / wireguard）
      data   ：本次提交的字段（validated_data，可能为部分字段）
      config ：当前已存在的协议配置对象（可能为 None）

    返回：错误消息列表（为空表示校验通过）。
    """
    from resource_manager.models import GREConfig, VXLANConfig, WireguardConfig

    pop = edge.pop_point
    errors = []

    def eff(field, default=None):
        """取本次提交值，缺省时回退到已存配置值。"""
        if data and field in data and data.get(field) not in (None, ''):
            return data.get(field)
        return getattr(config, field, default) if config else default

    def used_tunnel_ips():
        """同 POP 下其它边缘占用的隧道 / 接口 IP → 归属边缘。"""
        used = {}
        for cfg in (GREConfig.objects.filter(edge__pop_point=pop)
                    .exclude(edge_id=edge.id).select_related('edge')):
            if cfg.tunnel_ip:
                used.setdefault(_ip_only(cfg.tunnel_ip), cfg.edge)
        for cfg in (VXLANConfig.objects.filter(edge__pop_point=pop)
                    .exclude(edge_id=edge.id).select_related('edge')):
            if cfg.tunnel_ip:
                used.setdefault(_ip_only(cfg.tunnel_ip), cfg.edge)
        for cfg in (WireguardConfig.objects.filter(edge__pop_point=pop)
                    .exclude(edge_id=edge.id).select_related('edge')):
            if cfg.interface_ip:
                used.setdefault(_ip_only(cfg.interface_ip), cfg.edge)
        return used

    if proto == 'gre':
        local_ip = eff('local_ip')
        remote_ip = eff('remote_ip')
        tunnel_ip = eff('tunnel_ip')

        if local_ip and remote_ip:
            dup = (GREConfig.objects.filter(
                edge__pop_point=pop, local_ip=local_ip, remote_ip=remote_ip)
                .exclude(edge_id=edge.id).select_related('edge').first())
            if dup:
                errors.append(
                    f'系统已存在相同 GRE 配置（本端 {local_ip} ↔ 远端 {remote_ip}），归属 {dup.edge.edge_no}')
        if remote_ip:
            r = (GREConfig.objects.filter(edge__pop_point=pop, remote_ip=remote_ip)
                 .exclude(edge_id=edge.id).select_related('edge').first())
            if r:
                errors.append(f'远端(客户端) IP {remote_ip} 已被 {r.edge.edge_no} 占用')

        if tunnel_ip:
            owner = used_tunnel_ips().get(_ip_only(tunnel_ip))
            if owner:
                errors.append(f'隧道接口 IP {tunnel_ip} 与 {owner.edge_no} 冲突')

    elif proto == 'wireguard':
        interface_ip = eff('interface_ip')
        listen_port = eff('listen_port')

        if interface_ip:
            owner = used_tunnel_ips().get(_ip_only(interface_ip))
            if owner:
                errors.append(f'接口 IP {interface_ip} 与 {owner.edge_no} 冲突')
        if listen_port:
            p = (WireguardConfig.objects.filter(edge__pop_point=pop, listen_port=listen_port)
                 .exclude(edge_id=edge.id).select_related('edge').first())
            if p:
                errors.append(f'监听端口 {listen_port} 已被 {p.edge.edge_no} 占用')

    return errors


# ═══════════════════════════════════════════════════════════════
# § 公共接口
# ═══════════════════════════════════════════════════════════════

class RegisterView(APIView):
    """POST /api/portal/register/ — 客户注册（创建账号 + 客户档案，返回 Token）。"""
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PublicRegisterRateThrottle]

    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        result = s.save()
        user = result['user']
        customer = result['customer']
        tokens = _issue_tokens(user)
        referral = getattr(customer, 'channel_referral', None)
        promotion = None
        if referral:
            promotion = {
                'promotion_code': referral.promotion_code,
                'promotion_discount_rate': str(referral.promotion_discount_rate),
            }
        return Response({
            'code': 200, 'message': '注册成功',
            'data': {**tokens, 'user': serialize_auth_user(user), 'promotion': promotion},
        }, status=status.HTTP_201_CREATED)


class PortalProductViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/portal/products/ — 已上线产品（公开），支持多维筛选；登录后可报价测算库存。"""
    permission_classes = [AllowAny]
    authentication_classes = [JWTAuthentication]
    queryset = Product.objects.filter(status='online').select_related(
        'pop_point', 'pop_point__city', 'pop_point__city__country',
        'pop_point__city__country__region', 'supplier', 'template',
    ).prefetch_related('upgrade_rules', 'pop_point__ip_blocks', 'os_templates')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = ProductFilter
    search_fields = ['name', 'product_no']
    ordering_fields = ['sort_order', 'monthly_price', 'created_at']
    ordering = ['sort_order', 'monthly_price']

    def get_queryset(self):
        from product_manager.hot_sale import annotate_sold_service_count

        return annotate_sold_service_count(super().get_queryset())

    def get_serializer_class(self):
        return PortalProductDetailSerializer if self.action == 'retrieve' else ProductListSerializer

    def _base_spec(self, product):
        return {
            'cpu_cores': product.cpu_cores, 'ram_mb': product.ram_mb, 'disk_gb': product.disk_gb,
            'bandwidth_mbps': product.bandwidth_mbps, 'ip_count': product.ip_count,
            'nic_count': product.nic_count,
        }

    @action(detail=True, methods=['post'], url_path='quote')
    def quote(self, request, pk=None):
        """定制规格报价：公开返回价格；登录用户额外返回 POP 可开通数量（stock）。"""
        product = self.get_object()
        ser = CustomSpecSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        custom = product_services.normalize_spec_for_category(
            product,
            {**self._base_spec(product), **{k: v for k, v in ser.validated_data.items()}},
        )
        if 'nic_count' in request.data and product.category != 'edge':
            custom['nic_count'] = request.data.get('nic_count', product.nic_count)
        price = product_services.compute_price(product, custom)
        from accounts.permissions import get_user_customer
        from customer_manager.reputation import portal_can_see_reputation
        customer = get_user_customer(request.user) if request.user and request.user.is_authenticated else None
        if not portal_can_see_reputation(customer, product):
            price = dict(price)
            price['reputation_price'] = None
            price['base_reputation_price'] = None
        payload = {
            'price': price,
            'custom_spec': custom,
            'base_spec': product_services.product_base_spec(product),
        }
        if request.user and request.user.is_authenticated:
            payload['stock'] = product_services.estimate_stock(product.pop_point, custom)
            payload['held'] = product_services.pop_held(product.pop_point)
        return Response(payload)

    @action(detail=True, methods=['get'], url_path='delivery-options')
    def delivery_options(self, request, pk=None):
        """门户下单前可见：可交付节点、库存与预计开通时长。"""
        product = self.get_object()
        pop = product.pop_point
        spec = self._base_spec(product)
        if pop is None:
            return Response({
                'product_id': product.id,
                'pop': None,
                'can_deliver': False,
                'stock': None,
                'available': {},
                'held': {},
                'estimated_provision_minutes': None,
                'allow_oversell': False,
                'capacity': {'ok': False, 'shortages': ['产品未绑定 POP']},
            })
        stock = product_services.estimate_stock(pop, spec)
        capacity = product_services.check_spec_against_pop(pop, spec)
        estimate = product_services.resolve_provision_estimate(product, capacity=capacity, pop=pop)
        can_deliver = True
        return Response({
            'product_id': product.id,
            'pop': {
                'id': pop.id,
                'name': pop.name,
                'code': pop.code,
                'city_name': pop.city.name if pop.city_id else '',
                'config_mode': pop.config_mode,
            },
            'can_deliver': can_deliver,
            'stock': stock.get('stock'),
            'stock_detail': stock,
            'available': product_services.pop_available(pop),
            'held': product_services.pop_held(pop),
            'estimated_provision_minutes': estimate['estimated_provision_minutes'],
            'estimated_provision_hours': estimate['estimated_provision_hours'],
            'expected_delivery_at': estimate['expected_delivery_at'],
            'is_backorder': estimate['is_backorder'],
            'allow_oversell': bool(pop.allow_oversell),
            'allow_backorder': bool(product.allow_backorder),
            'capacity': capacity,
        })


class LocationsView(APIView):
    """GET /api/portal/locations/ — 区域/国家/城市三级数据（公开，供产品筛选）。"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        regions = list(Region.objects.values('id', 'name'))
        countries = list(Country.objects.values('id', 'name', 'region'))
        cities = list(City.objects.values('id', 'name', 'country'))
        network_types = list(
            NetworkTypeOption.objects.filter(is_active=True)
            .order_by('sort_order', 'id')
            .values('code', 'name', 'sort_order')
        )
        from product_manager.models import PortalCountryShortcut
        quick_countries = [
            {
                'id': item.pk,
                'country': item.country_id,
                'region': item.country.region_id,
                'name': item.label,
            }
            for item in PortalCountryShortcut.objects.filter(is_active=True).select_related('country')
        ]
        return Response({'code': 200, 'message': 'ok', 'data': {
            'regions': regions, 'countries': countries, 'cities': cities,
            'network_types': network_types,
            'quick_countries': quick_countries,
        }})


def _portal_notices(customer) -> dict:
    """开通中工单与最近开通完成的服务，供门户提示客户进入管理。"""
    since = timezone.now() - timedelta(hours=72)
    ready = []
    instances = (
        ServiceInstance.objects.filter(
            service_order__customer=customer,
            status=ServiceInstance.InstanceStatus.ACTIVE,
        )
        .filter(Q(activated_at__gte=since) | Q(provisioned_at__gte=since))
        .select_related('service_order__product')
        .order_by('-activated_at', '-id')[:8]
    )
    for ins in instances:
        product = getattr(ins.service_order, 'product', None)
        ready.append({
            'id': ins.pk,
            'instance_no': ins.instance_no,
            'product_name': getattr(product, 'name', '') or '',
            'service_type': ins.service_type,
            'resource_id': ins.vps_id or ins.edge_id,
        })
    from service_manager.capacity_reservation import order_awaits_manual_stock

    provisioning = []
    orders = (
        ServiceOrder.objects.filter(
            customer=customer,
            auto_provision_status__in=[
                ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING,
                ServiceOrder.AutoProvisionStatus.PROVISIONING,
                ServiceOrder.AutoProvisionStatus.PAID_PENDING,
                ServiceOrder.AutoProvisionStatus.INSUFFICIENT,
            ],
        )
        .exclude(status__in=[
            ServiceOrder.OrderStatus.REJECTED,
            ServiceOrder.OrderStatus.TERMINATED,
        ])
        .select_related('product')
        .order_by('-updated_at')[:8]
    )
    for order in orders:
        waiting_admin = (
            order.auto_provision_status == ServiceOrder.AutoProvisionStatus.INSUFFICIENT
            or order_awaits_manual_stock(order)
        )
        provisioning.append({
            'id': order.pk,
            'order_no': order.order_no,
            'product_name': getattr(order.product, 'name', '') or '',
            'status': order.auto_provision_status,
            'message': order.auto_provision_message or '',
            'waiting_admin': waiting_admin,
        })
    return {'provisioning': provisioning, 'ready': ready}


# ═══════════════════════════════════════════════════════════════
# § 客户 · 总览 / 资料
# ═══════════════════════════════════════════════════════════════

class DashboardView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        customer = request_customer(request)
        if customer is None:
            return Response({'code': 200, 'message': 'ok', 'data': {}})

        instances = ServiceInstance.objects.filter(service_order__customer=customer)
        active = instances.filter(status='active')
        vps_qs = VPS.objects.filter(customer=customer)
        edge_qs = EdgeAcceleration.objects.filter(customer=customer)
        bills = CustomerBilling.objects.filter(customer=customer)
        unpaid = bills.filter(status__in=['unpaid', 'overdue'])

        monthly_cost = 0.0
        for ins in active.select_related('service_order'):
            try:
                monthly_cost += float(ins.effective_monthly_fee())
            except Exception:
                pass

        outstanding = 0.0
        for b in unpaid:
            outstanding += float(b.amount) - float(b.paid_amount)

        from customer_manager.reputation import serialize_reputation
        reputation = serialize_reputation(customer)
        return Response({'code': 200, 'message': 'ok', 'data': {
            'customer_name': customer.name,
            'customer_no': customer.customer_no,
            'status': customer.status,
            'balance': str(customer.balance),
            'credit_limit': str(customer.credit_limit),
            **reputation,
            'active_services': active.count(),
            'total_services': instances.exclude(status='terminated').count(),
            'vps_count': vps_qs.count(),
            'edge_count': edge_qs.count(),
            'pending_orders': ServiceOrder.objects.filter(
                customer=customer,
                status__in=['pending', 'approved', 'provisioning'],
            ).count(),
            'unpaid_bills': unpaid.count(),
            'outstanding_amount': round(outstanding, 2),
            'monthly_cost': round(monthly_cost, 2),
            'capabilities': {
                'service_change': check_permission(request.user, 'service.change.manage'),
                'order_create': check_permission(request.user, 'service.order.manage'),
                'billing_pay': check_permission(request.user, 'billing.list.manage'),
                'vps_manage': check_permission(request.user, 'resource.vps.manage'),
                'edge_manage': check_permission(request.user, 'resource.edge.manage'),
                'profile_update': check_permission(request.user, 'customer.detail.manage'),
                'ticket_manage': check_permission(request.user, 'service.ticket.manage'),
            },
            'notices': _portal_notices(customer),
        }})


class ProfileView(APIView):
    permission_classes = [IsCustomer]

    def get(self, request):
        customer = request_customer(request)
        if customer is None:
            return Response({'code': 404, 'message': '未关联客户档案'}, status=404)
        return Response({'code': 200, 'message': 'ok', 'data': CustomerProfileSerializer(customer).data})

    def patch(self, request):
        customer = request_customer(request)
        if customer is None:
            return Response({'code': 404, 'message': '未关联客户档案'}, status=404)
        if not check_permission(request.user, 'customer.detail.manage'):
            return Response({'code': 403, 'message': '当前角色无权修改客户资料'}, status=403)
        s = CustomerProfileSerializer(customer, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response({'code': 200, 'message': '资料已更新', 'data': s.data})


# ═══════════════════════════════════════════════════════════════
# § 客户 · 服务 / 订单 / 账单 / 资源
# ═══════════════════════════════════════════════════════════════

class CustomerScopedMixin(PortalActionPermissionMixin):
    permission_classes = [IsCustomer]
    action_permissions = {}

    def _customer(self):
        return request_customer(self.request)


class MyTicketViewSet(CustomerScopedMixin, viewsets.ModelViewSet):
    """客户门户技术工单：本客户可见，不含内部备注。"""

    http_method_names = ['get', 'post', 'head', 'options']
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'priority', 'status']
    search_fields = ['ticket_no', 'subject', 'description']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    action_permissions = {
        'create': 'service.ticket.manage',
        'reply': 'service.ticket.manage',
    }

    def get_serializer_class(self):
        if self.action == 'create':
            return PortalTicketWriteSerializer
        return PortalTicketSerializer

    def get_queryset(self):
        customer = self._customer()
        if customer is None:
            return SupportTicket.objects.none()
        return (
            SupportTicket.objects.filter(customer=customer)
            .select_related('related_instance')
            .prefetch_related('messages__author')
        )

    def create(self, request, *args, **kwargs):
        customer = self._customer()
        if customer is None:
            return Response({'detail': '未关联客户档案，无法提交工单'}, status=403)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from service_manager.support_ticket import create_support_ticket
        try:
            ticket = create_support_ticket(
                customer=customer,
                subject=data['subject'],
                description=data['description'],
                category=data.get('category', SupportTicket.Category.TECHNICAL),
                priority=data.get('priority', SupportTicket.Priority.P3),
                source=SupportTicket.Source.PORTAL,
                created_by=request.user,
                related_instance=data.get('related_instance'),
            )
        except DjangoValidationError as exc:
            payload = getattr(exc, 'message_dict', None) or exc.messages
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            PortalTicketSerializer(self.get_queryset().get(pk=ticket.pk)).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='reply')
    def reply(self, request, pk=None):
        ticket = self.get_object()
        serializer = PortalTicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from service_manager.support_ticket import add_customer_message
        try:
            add_customer_message(
                ticket,
                serializer.validated_data['content'],
                author=request.user,
            )
        except DjangoValidationError as exc:
            return Response(
                {'detail': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(PortalTicketSerializer(self.get_queryset().get(pk=ticket.pk)).data)


class MyServiceViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    """客户「我的服务」：聚合 ServiceInstance + 直连 VPS/Edge 资源。"""
    serializer_class = PortalServiceItemSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'service_type']
    ordering = ['-created_at']
    action_permissions = {
        'change_quote': 'service.change.view',
        'change_request': 'service.change.manage',
        'renew_quote': 'service.change.view',
        'renew': 'service.change.manage',
        'convert_quote': 'service.change.view',
        'convert': 'service.change.manage',
    }

    def get_queryset(self):
        """保留 detail/change_request 用的 ServiceInstance 查询集。"""
        customer = self._customer()
        if customer is None:
            return ServiceInstance.objects.none()
        return ServiceInstance.objects.filter(
            Q(service_order__customer=customer)
            | Q(vps__customer=customer)
            | Q(edge__customer=customer)
        ).select_related(
            'service_order', 'vps__pop_point', 'edge__pop_point',
        ).distinct()

    def list(self, request, *args, **kwargs):
        customer = self._customer()
        if customer is None:
            return Response({'count': 0, 'next': None, 'previous': None, 'results': []})
        status_filter = request.query_params.get('status') or None
        service_type = request.query_params.get('service_type') or None
        items = service_list.build_customer_service_items(customer, status_filter=status_filter)
        if service_type:
            items = [it for it in items if it['service_type'] == service_type]
        can_change = check_permission(request.user, 'service.change.manage')
        for it in items:
            it['can_change_request'] = bool(it.get('can_change_request') and can_change)
        page = self.paginate_queryset(items)
        serializer = PortalServiceItemSerializer(page if page is not None else items, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='change_quote')
    def change_quote(self, request, pk=None):
        """对已有服务实例做变更报价：展示原月费、变更后月费、本次应付与生效策略。"""
        from service_manager.change_billing import ChangeBillingError, quote_service_change

        instance = self.get_object()
        serializer = ChangeQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            quote = quote_service_change(instance, serializer.validated_data)
        except ChangeBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'code': 200, 'message': 'ok', 'data': quote})

    def _renew_response(self, request, instance, *, billing_cycle='', requirements='', kind='renew'):
        from service_manager.renew_billing import (
            RenewBillingError,
            submit_instance_renewal,
            submit_trial_conversion,
        )

        submit = submit_trial_conversion if kind == 'convert' else submit_instance_renewal
        try:
            result = submit(
                instance,
                billing_cycle=billing_cycle or None,
                requirements=requirements or '',
                user=request.user,
            )
        except RenewBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        order = result['order']
        billing = result['billing']
        quote = result['quote']
        payload = {
            'order': PortalOrderSerializer(order).data,
            'quote': quote,
            'billing': PortalBillingSerializer(billing).data if billing else None,
            'payment_required': result['payment_required'],
            'transaction': None,
            'checkout': None,
        }
        if result['payment_required'] and billing is not None:
            try:
                transaction_payload, checkout, checkout_error = start_portal_billing_checkout(
                    request, billing, return_path='/console/bills',
                )
            except (billing_payment_service.PaymentServiceError, ValueError) as exc:
                logger.warning('续费 %s 发起支付失败：%s', order.order_no, exc)
                return Response({
                    'code': 200,
                    'message': f'{result["message"]}；支付发起失败：{exc}，请稍后在账单中继续支付',
                    'data': payload,
                })
            if checkout_error:
                return Response({
                    'code': 200,
                    'message': f'{result["message"]}；{checkout_error}',
                    'data': payload,
                })
            payload['transaction'] = transaction_payload
            payload['checkout'] = checkout
        return Response({
            'code': 200,
            'message': result['message'],
            'data': payload,
        }, status=status.HTTP_201_CREATED if result['payment_required'] else status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='renew_quote')
    def renew_quote(self, request, pk=None):
        from service_manager.renew_billing import RenewBillingError, quote_instance_renewal

        instance = self.get_object()
        serializer = RenewQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            quote = quote_instance_renewal(instance, serializer.validated_data.get('billing_cycle'))
        except RenewBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'code': 200, 'message': 'ok', 'data': quote})

    @action(detail=True, methods=['post'], url_path='renew')
    def renew(self, request, pk=None):
        instance = self.get_object()
        serializer = RenewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._renew_response(
            request,
            instance,
            billing_cycle=serializer.validated_data.get('billing_cycle') or '',
            requirements=serializer.validated_data.get('requirements') or '',
        )

    @action(detail=True, methods=['post'], url_path='convert_quote')
    def convert_quote(self, request, pk=None):
        from service_manager.renew_billing import RenewBillingError, quote_trial_conversion

        instance = self.get_object()
        serializer = RenewQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            quote = quote_trial_conversion(instance, serializer.validated_data.get('billing_cycle'))
        except RenewBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'code': 200, 'message': 'ok', 'data': quote})

    @action(detail=True, methods=['post'], url_path='convert')
    def convert(self, request, pk=None):
        instance = self.get_object()
        serializer = RenewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._renew_response(
            request,
            instance,
            billing_cycle=serializer.validated_data.get('billing_cycle') or '',
            requirements=serializer.validated_data.get('requirements') or '',
            kind='convert',
        )

    @action(detail=True, methods=['post'], url_path='change_request')
    def change_request(self, request, pk=None):
        """发起服务变更。

        - 规格升/降/调整：走报价与补差价支付（升配）或下周期生效（降配）
        - 续期：按账期出账支付，不再创建人工续期工单
        - 终止：保留工单式人工审批
        """
        from service_manager.models import (
            ServiceChangeRecord, ServiceLifecycleRecord, service_snapshot,
        )
        from service_manager.lifecycle import mark_change_stage
        from service_manager.notify_helpers import notify_change_event, notify_order_event
        from service_manager.change_billing import (
            ChangeBillingError,
            submit_service_change,
        )

        instance = self.get_object()
        s = ChangeRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        change_type = d.get('change_type') or 'change'

        if change_type == 'renew':
            return self._renew_response(
                request,
                instance,
                billing_cycle=d.get('billing_cycle') or '',
                requirements=d.get('requirements') or '',
            )

        # 终止：保持原工单流程
        if change_type == 'terminate':
            resource = instance.vps or instance.edge
            if instance.service_type == 'vps':
                business_type = ServiceOrder.BusinessType.VPS
            elif instance.edge_id and instance.edge:
                proto = (instance.edge.protocol or '').lower()
                if 'wireguard' in proto or proto == 'wg':
                    business_type = ServiceOrder.BusinessType.EDGE_WG
                elif 'socks' in proto:
                    business_type = ServiceOrder.BusinessType.EDGE_SOCKS5
                else:
                    business_type = ServiceOrder.BusinessType.EDGE_GRE
            else:
                business_type = instance.service_order.business_type

            before = service_snapshot(instance)
            after_spec = dict(before)
            after_spec['terminate'] = True

            order = ServiceOrder.objects.create(
                order_type=ServiceOrder.OrderType.TERMINATE,
                business_type=business_type,
                status=ServiceOrder.OrderStatus.PENDING,
                current_stage=ServiceOrder.LifecycleStage.CHANGE,
                source=ServiceOrder.OrderSource.PORTAL,
                customer=instance.service_order.customer,
                preferred_pop=getattr(resource, 'pop_point', None),
                related_instance=instance,
                requirements=d.get('requirements', ''),
                remark=f'门户变更申请 · 实例[{instance.instance_no}] · {change_type}',
                created_by=request.user,
            )
            change = ServiceChangeRecord.objects.create(
                service_instance=instance,
                related_order=order,
                change_type=ServiceChangeRecord.ChangeType.OTHER,
                status=ServiceChangeRecord.ChangeStatus.PENDING,
                change_reason=d.get('requirements') or f'门户发起{change_type}申请',
                change_detail=d.get('requirements', ''),
                before_spec=before,
                after_spec=after_spec,
                requested_by=request.user,
            )
            try:
                mark_change_stage(
                    order, instance, change,
                    submitted_by=request.user,
                    status=ServiceLifecycleRecord.StageStatus.PENDING,
                )
                notify_order_event(order, '门户变更申请', event='service_order')
                notify_change_event(change, '门户变更申请已提交')
            except Exception:  # noqa: BLE001
                logger.warning('门户变更后置处理失败 order=%s', order.order_no, exc_info=True)

            return Response({
                'code': 200,
                'message': '变更申请已提交，等待审批',
                'data': {
                    'order': PortalOrderSerializer(order).data,
                    'change_no': change.change_no,
                    'change_id': change.id,
                    'payment_required': False,
                },
            })

        # 规格变更：报价 + 支付 / 下周期生效
        try:
            result = submit_service_change(
                instance,
                requested=d,
                requirements=d.get('requirements', ''),
                user=request.user,
            )
        except ChangeBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        order = result['order']
        change = result['change']
        billing = result['billing']
        quote = result['quote']

        # 升配且无需补差价：按资源情况自动开通或转人工
        if quote['direction'] == 'upgrade' and not result['payment_required']:
            from service_manager.change_provisioning import apply_paid_change
            applied = apply_paid_change(order, change, operator=request.user)
            result['applied'] = applied
            order.refresh_from_db()
            change.refresh_from_db()
            if applied.get('reason') == 'execute_failed':
                return Response({
                    'code': 200,
                    'message': f'变更已创建但自动执行失败：{applied.get("error")}，请等待运维处理',
                    'data': {
                        'order': PortalOrderSerializer(order).data,
                        'change_no': change.change_no,
                        'change_id': change.id,
                        'quote': quote,
                        'payment_required': False,
                    },
                })

        applied = result.get('applied') or {}
        payload = {
            'order': PortalOrderSerializer(order).data,
            'change_no': change.change_no,
            'change_id': change.id,
            'quote': quote,
            'billing': PortalBillingSerializer(billing).data if billing else None,
            'payment_required': result['payment_required'],
            'transaction': None,
            'checkout': None,
            'applied': applied,
            'progress_steps': applied.get('progress_steps') or [],
        }

        if result['payment_required'] and billing is not None:
            try:
                transaction_payload, checkout, checkout_error = start_portal_billing_checkout(
                    request, billing, return_path='/console/bills',
                )
            except (billing_payment_service.PaymentServiceError, ValueError) as exc:
                logger.warning('服务变更 %s 发起支付失败：%s', change.change_no, exc)
                return Response({
                    'code': 200,
                    'message': f'{result["message"]}；支付发起失败：{exc}，请稍后在账单中继续支付',
                    'data': payload,
                })
            if checkout_error:
                return Response({
                    'code': 200,
                    'message': f'{result["message"]}；{checkout_error}',
                    'data': payload,
                })
            payload['transaction'] = transaction_payload
            payload['checkout'] = checkout

        return Response({
            'code': 200,
            'message': result['message'],
            'data': payload,
        }, status=status.HTTP_201_CREATED if result['payment_required'] else status.HTTP_200_OK)

class MyOrderViewSet(CustomerScopedMixin, viewsets.ModelViewSet):
    http_method_names = ['get', 'post', 'head', 'options']
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'order_type', 'business_type']
    ordering = ['-created_at']
    action_permissions = {
        'create': 'service.order.manage',
        'direct_checkout': 'service.order.manage',
    }

    def get_serializer_class(self):
        return PortalOrderCreateSerializer if self.action == 'create' else PortalOrderSerializer

    def get_queryset(self):
        customer = self._customer()
        if customer is None:
            return ServiceOrder.objects.none()
        return ServiceOrder.objects.filter(customer=customer).select_related(
            'preferred_pop', 'product', 'manual_delivery', 'requested_os_template',
        ).prefetch_related(
            Prefetch(
                'instances',
                queryset=ServiceInstance.objects.select_related(
                    'vps', 'edge', 'bandwidth_alloc',
                ).prefetch_related('ip_addresses'),
            ),
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['customer'] = self._customer()
        return ctx

    def create(self, request, *args, **kwargs):
        customer = self._customer()
        if customer is None:
            return Response({'code': 403, 'message': '未关联客户档案，无法下单'}, status=403)
        s = self.get_serializer(data=request.data)
        s.is_valid(raise_exception=True)
        order = s.save()
        # 标准流程：门户下单只创建服务请求；审批通过后才允许自动交付。
        return Response({'code': 200, 'message': '下单成功，服务请求已提交审批',
                         'data': PortalOrderSerializer(order).data}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='direct-checkout')
    def direct_checkout(self, request):
        """创建产品订单、生成预付账单并立即发起线上支付。

        这是客户门户的自助直付通道：支付成功后由支付回调进入既有履约编排。若
        POP/IP/带宽不足，编排会将订单置为资源不足并向服务管理 IM 规则发送通知；
        资源管理完成扩容后会自动重试。
        """
        customer = self._customer()
        if customer is None:
            return Response({'code': 403, 'message': '未关联客户档案，无法下单'}, status=403)

        serializer = PortalDirectCheckoutSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        from service_manager.lifecycle import mark_requirement_stage
        from service_manager.notify_helpers import notify_order_event
        from service_manager.payment_billing import (
            OrderBillingError,
            ensure_order_payment_billing,
            order_quote,
        )

        try:
            with transaction.atomic():
                order = ServiceOrder.objects.select_for_update().select_related(
                    'product', 'customer',
                ).get(pk=order.pk)
                quoted_amount, _currency = order_quote(order)
                order.status = ServiceOrder.OrderStatus.APPROVED
                order.approved_at = timezone.now()
                order.approved_by = None
                order.remark = (
                    f'{order.remark}\n门户直付：客户已选择在线支付渠道，支付确认后自动开通。'
                )[:2000]
                order.save(update_fields=[
                    'status', 'approved_at', 'approved_by', 'remark', 'updated_at',
                ])

                billing = ensure_order_payment_billing(order)
                if billing is not None:
                    order.auto_provision_status = ServiceOrder.AutoProvisionStatus.PAYMENT_PENDING
                    order.auto_provision_message = (
                        f'等待支付账单 {billing.bill_no}'
                        f'（{billing.currency}{billing.amount:.2f}）'
                    )
                    order.save(update_fields=[
                        'auto_provision_status',
                        'auto_provision_message',
                        'updated_at',
                    ])
        except OrderBillingError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # 生命周期和 IM 写在订单状态落库之后，避免网关异常影响订单审计。
        try:
            mark_requirement_stage(order, handled_by=None, approved_by=request.user)
            notify_order_event(order, '门户直付订单已确认', event='service_order')
        except Exception:  # noqa: BLE001
            logger.warning('门户直付订单 %s 生命周期或通知写入失败', order.order_no, exc_info=True)

        # 零金额产品无需创建支付交易；开通放到后台，门户不必等待虚机创建。
        if billing is None:
            from service_manager.payment_fulfillment import schedule_auto_provision

            schedule_auto_provision(order.pk)
            order.refresh_from_db()
            return Response({
                'code': 200,
                'message': '订单已确认，系统正在后台开通',
                'data': {
                    'order': PortalOrderSerializer(order).data,
                    'billing': None,
                    'transaction': None,
                    'provision_queued': True,
                },
            }, status=status.HTTP_201_CREATED)

        if serializer.use_reputation:
            from customer_manager.reputation import ReputationError, pay_billing_with_reputation
            try:
                billing = pay_billing_with_reputation(
                    billing,
                    needed=serializer.reputation_needed,
                    operator=request.user,
                    request=request,
                )
            except ReputationError as exc:
                return Response({
                    'code': 400,
                    'message': str(exc),
                    'data': {
                        'order': PortalOrderSerializer(order).data,
                        'billing': PortalBillingSerializer(billing).data,
                    },
                }, status=status.HTTP_400_BAD_REQUEST)
            from service_manager.payment_fulfillment import schedule_fulfill_paid_billing
            schedule_fulfill_paid_billing(billing.pk)
            order.refresh_from_db()
            return Response({
                'code': 200,
                'message': '信誉值支付成功，系统正在后台开通',
                'data': {
                    'order': PortalOrderSerializer(order).data,
                    'billing': PortalBillingSerializer(billing).data,
                    'transaction': None,
                    'provision_queued': True,
                },
            }, status=status.HTTP_201_CREATED)

        channel = serializer.payment_channel
        frontend_return_url = _portal_payment_return_url(request)
        try:
            from django.urls import reverse

            from system_manager.payment_urls import browser_absolute_url, payment_callback_url
            callback_url = payment_callback_url(channel.id, request)
            payment = billing_payment_service.create_payment_transaction(
                billing=billing,
                channel=channel,
                operator=request.user,
                return_url=frontend_return_url,
            )
            gateway_return_url = browser_absolute_url(
                request,
                reverse('payment-return', kwargs={'transaction_no': payment.transaction_no}),
            )
            payment, checkout = billing_payment_service.issue_gateway_checkout(
                payment=payment,
                callback_url=callback_url,
                gateway_return_url=gateway_return_url,
            )
        except (billing_payment_service.PaymentServiceError, ValueError) as exc:
            logger.warning('门户直付订单 %s 创建支付交易失败：%s', order.order_no, exc)
            return Response({
                'code': 400,
                'message': f'订单已创建，但支付订单创建失败：{exc}',
                'data': {
                    'order': PortalOrderSerializer(order).data,
                    'billing': PortalBillingSerializer(billing).data,
                },
            }, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'code': 200,
            'message': '订单和支付交易已创建，请完成在线支付',
            'data': {
                'order': PortalOrderSerializer(order).data,
                'billing': PortalBillingSerializer(billing).data,
                'transaction': {
                    'id': payment.id,
                    'transaction_no': payment.transaction_no,
                    'status': payment.status,
                    'amount': str(payment.amount),
                    'currency': payment.currency,
                    'expires_at': payment.expires_at.isoformat() if payment.expires_at else None,
                },
                'checkout': checkout,
            },
        }, status=status.HTTP_201_CREATED)


class MyBillViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PortalBillingSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'bill_month']
    ordering = ['-bill_month']
    action_permissions = {
        'pay': 'billing.list.manage',
        'recharge': 'billing.list.manage',
        'pay_with_balance': 'billing.list.manage',
    }

    def get_queryset(self):
        customer = self._customer()
        if customer is None:
            return CustomerBilling.objects.none()
        items_qs = BillingItem.objects.select_related('product', 'service_instance')
        return CustomerBilling.objects.filter(customer=customer).prefetch_related(
            Prefetch('items', queryset=items_qs),
        )

    @action(detail=True, methods=['post'], url_path='pay')
    def pay(self, request, pk=None):
        """POST /api/portal/bills/{id}/pay/ — 客户支付账单。

        仅在线渠道：step=init 创建网关交易。线下现金/转账须后台财务确认，门户不再入账。
        ``confirm`` 对在线渠道返回 409，由验签回调入账。
        """
        billing = self.get_object()
        ser = PortalBillPaySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        method = data['payment_method']
        step = data.get('step') or 'init'
        if method == 'reputation':
            from customer_manager.reputation import (
                ReputationError,
                can_pay_with_reputation,
                pay_billing_with_reputation,
                required_reputation,
                resolve_billing_product,
            )
            product = resolve_billing_product(billing)
            quantity = max(1, billing.items.count() or 1)
            if product is None or not can_pay_with_reputation(
                billing.customer, product, quantity=quantity,
            ):
                return Response(
                    {'code': 400, 'message': '当前账单无法使用信誉值支付'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                billing = pay_billing_with_reputation(
                    billing,
                    needed=required_reputation(product, quantity),
                    operator=request.user,
                    request=request,
                )
            except ReputationError as exc:
                return Response(
                    {'code': 400, 'message': str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from service_manager.payment_fulfillment import schedule_fulfill_paid_billing
            schedule_fulfill_paid_billing(billing.pk)
            billing.refresh_from_db()
            return Response({
                'code': 200,
                'message': '信誉值支付成功，系统正在后台开通',
                'data': PortalBillingSerializer(billing).data,
            })
        if method in payment_service.OFFLINE_METHODS:
            return Response(
                {'code': 400, 'message': '线下入账仅能由后台财务确认，请使用在线支付'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if method in payment_service.ONLINE_METHODS:
                if step == 'init':
                    from django.urls import reverse
                    from system_manager.models import PaymentChannel

                    channel = None
                    channel_id = data.get('channel_id')
                    if channel_id:
                        channel = PaymentChannel.objects.filter(
                            pk=channel_id, provider=method,
                        ).first()
                    if channel is None:
                        channel = PaymentChannel.objects.filter(
                            provider=method, enabled=True,
                        ).order_by('sort_order', 'id').first()
                    if channel is None:
                        return Response(
                            {'code': 400, 'message': '该支付方式暂未配置或未启用'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    frontend_return_url = _portal_payment_return_url(
                        request,
                        '/console/bills',
                    )
                    payment = billing_payment_service.create_payment_transaction(
                        billing=billing,
                        channel=channel,
                        operator=request.user,
                        amount=data.get('amount'),
                        return_url=frontend_return_url,
                    )
                    from system_manager.payment_urls import browser_absolute_url, payment_callback_url
                    callback_url = payment_callback_url(channel.id, request)
                    gateway_return_url = browser_absolute_url(
                        request,
                        reverse('payment-return', kwargs={'transaction_no': payment.transaction_no}),
                    )
                    payment, checkout = billing_payment_service.issue_gateway_checkout(
                        payment=payment,
                        callback_url=callback_url,
                        gateway_return_url=gateway_return_url,
                    )
                    return Response({
                        'code': 200,
                        'message': '支付订单已创建，请完成扫码/跳转支付',
                        'data': {
                            'transaction_no': payment.transaction_no,
                            'transaction_id': payment.id,
                            'status': payment.status,
                            'checkout': checkout,
                        },
                    })
                return Response(
                    {
                        'code': 409,
                        'message': '在线支付状态由服务商验签回调确认，请查询支付交易状态。',
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            else:
                pay_amount = payment_service.parse_pay_amount(data.get('amount'), billing)
                billing = payment_service.apply_bill_payment(
                    billing, pay_amount, method, request.user, request=request,
                )
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=400)

        return Response({
            'code': 200,
            'message': '支付成功',
            'data': PortalBillingSerializer(billing).data,
        })

    @action(detail=False, methods=['post'], url_path='recharge')
    def recharge(self, request):
        """POST /api/portal/bills/recharge/ — 创建余额充值单，再走在线支付。"""
        from customer_manager.wallet import WalletError, create_recharge_billing

        customer = self._customer()
        if customer is None:
            return Response({'code': 400, 'message': '未绑定客户'}, status=status.HTTP_400_BAD_REQUEST)
        ser = PortalWalletRechargeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            billing = create_recharge_billing(
                customer,
                amount=ser.validated_data['amount'],
                operator=request.user,
            )
        except WalletError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'code': 201,
            'message': '充值账单已创建，请完成在线支付',
            'data': PortalBillingSerializer(billing).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='pay-with-balance')
    def pay_with_balance(self, request, pk=None):
        """POST /api/portal/bills/{id}/pay-with-balance/ — 用账户余额抵扣。"""
        from customer_manager.wallet import WalletError, apply_balance_to_billing

        billing = self.get_object()
        try:
            billing = apply_balance_to_billing(
                billing, operator=request.user, request=request,
            )
        except WalletError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'code': 200,
            'message': '余额支付成功',
            'data': PortalBillingSerializer(billing).data,
        })

    @action(detail=False, methods=['get'], url_path='wallet')
    def wallet(self, request):
        """GET /api/portal/bills/wallet/ — 余额与近期流水。"""
        from customer_manager.wallet import wallet_currency

        customer = self._customer()
        if customer is None:
            return Response({'code': 400, 'message': '未绑定客户'}, status=status.HTTP_400_BAD_REQUEST)
        ledgers = CustomerBalanceLedger.objects.filter(
            customer=customer,
        ).select_related('operator', 'related_billing').order_by('-created_at', '-id')[:50]
        return Response({
            'code': 200,
            'message': 'ok',
            'data': {
                'balance': customer.balance,
                'currency': wallet_currency(),
                'ledgers': CustomerBalanceLedgerSerializer(ledgers, many=True).data,
            },
        })

    @action(detail=False, methods=['get'], url_path='payment-methods')
    def payment_methods(self, request):
        """GET /api/portal/bills/payment-methods/?currency=USD — 可用支付方式列表。"""
        currency = str(request.query_params.get('currency') or 'USD').strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            return Response(
                {'code': 400, 'message': 'currency 必须为三位币种代码'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        methods = []
        product_id = request.query_params.get('product_id')
        billing_id = request.query_params.get('billing_id')
        try:
            quantity = max(1, int(request.query_params.get('quantity') or 1))
        except (TypeError, ValueError):
            quantity = 1
        product = None
        customer = self._customer()
        if product_id:
            from product_manager.models import Product
            product = Product.objects.filter(pk=product_id, status='online').first()
        if product is None and billing_id and customer is not None:
            from customer_manager.reputation import resolve_billing_product
            billing = CustomerBilling.objects.filter(pk=billing_id, customer=customer).first()
            product = resolve_billing_product(billing)
        if product is not None and customer is not None:
            from customer_manager.reputation import build_reputation_payment_method
            spec = {}
            for key in ('cpu_cores', 'ram_mb', 'disk_gb', 'nic_count', 'bandwidth_mbps', 'ip_count'):
                raw = request.query_params.get(key)
                if raw in (None, ''):
                    continue
                try:
                    spec[key] = int(raw)
                except (TypeError, ValueError):
                    continue
            method = build_reputation_payment_method(
                customer, product,
                quantity=quantity,
                custom_spec=spec or None,
                currency=currency,
            )
            if method:
                methods.append(method)
        channels = billing_payment_service.available_channels(currency)
        methods.extend([
            {
                'value': channel.provider,
                'channel_id': channel.id,
                'label': channel.name,
                'provider_label': channel.get_provider_display(),
                'online': True,
                'sandbox': channel.sandbox,
                'allowed_currencies': channel.allowed_currencies,
            }
            for channel in channels
        ])
        return Response({'code': 200, 'message': 'ok', 'data': methods})


class MyVPSViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PortalVPSSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status']
    ordering = ['-created_at']
    action_permissions = {
        'power': 'resource.vps.manage',
        'vnc_ticket': 'resource.vps.manage',
        'set_root_password': 'resource.vps.manage',
        'sync_now': 'resource.vps.manage',
        'reinstall': 'resource.vps.manage',
        'create_snapshot': 'resource.vps.manage',
        'restore_snapshot': 'resource.vps.manage',
        'delete_snapshot': 'resource.vps.manage',
    }

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PortalVPSDetailSerializer
        return PortalVPSSerializer

    def get_queryset(self):
        customer = self._customer()
        if customer is None:
            return VPS.objects.none()
        return VPS.objects.filter(customer=customer).select_related(
            'pop_point__city__country__region', 'product',
        ).prefetch_related('ip_addresses', 'vps_interfaces')

    @action(detail=True, methods=['post'], url_path='power')
    def power(self, request, pk=None):
        """VPS 电源：start / stop / restart / shutdown"""
        from resource_manager import pve_ops
        from resource_manager.pemanager_client import PemanagerError

        vps = self.get_object()
        action_name = request.data.get('action', '')
        try:
            result = pve_ops.vm_power(vps, action_name, dry_run=False)
        except PemanagerError as e:
            return Response({'code': 400, 'message': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return Response({'code': 400, 'message': f'电源操作失败：{e}'}, status=400)
        vps.refresh_from_db()
        from .audit import log_portal_vps_action
        from resource_manager.models import ResourceOperationLog
        log_portal_vps_action(
            request, vps, f'门户电源操作：{action_name}',
            action_type=(
                ResourceOperationLog.ActionType.RESTART
                if action_name == 'restart'
                else ResourceOperationLog.ActionType.STATUS_CHANGE
            ),
            extra={'action': action_name},
        )
        return Response({
            'code': 200, 'message': '操作成功', 'data': result,
            'vps': PortalVPSSerializer(vps).data,
        })

    @action(detail=True, methods=['get'], url_path='vnc_ticket')
    def vnc_ticket(self, request, pk=None):
        """获取 noVNC 控制台票据"""
        from resource_manager.pemanager_client import PemanagerClient, PemanagerError

        vps = self.get_object()
        if not vps.source_ref:
            return Response({'code': 400, 'message': '该 VPS 暂不支持远程控制台'}, status=400)
        pop = vps.pop_point
        from resource_manager.pemanager_client import pop_has_api_config
        if pop is None or not pop_has_api_config(pop):
            return Response({'code': 400, 'message': '该 VPS 所属节点未配置 API，无法打开控制台'}, status=400)
        try:
            client, _user = PemanagerClient.connect_pop(pop)
            data = client.vnc_ticket(vps.source_ref) or {}
        except PemanagerError as e:
            return Response({'code': 400, 'message': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return Response({'code': 400, 'message': f'获取 VNC 票据失败：{e}'}, status=400)
        from .audit import log_portal_vps_action
        from resource_manager.models import ResourceOperationLog
        log_portal_vps_action(
            request, vps, '获取 VNC 控制台票据',
            action_type=ResourceOperationLog.ActionType.OTHER,
            extra={'port': data.get('port')},
            notify=False,
        )
        ticket = data.get('ticket') or ''
        port = data.get('port')
        if not ticket or not port:
            return Response({'code': 400, 'message': '节点未返回 VNC 票据/端口'}, status=400)
        try:
            port_no = int(port)
        except (TypeError, ValueError):
            return Response({'code': 400, 'message': '节点未返回 VNC 端口'}, status=400)
        password = data.get('password') or ticket
        from resource_manager.models import VNCConsoleGrant
        grant = VNCConsoleGrant.objects.create(
            vps=vps,
            user=request.user,
            ticket=ticket,
            password=password,
            port=port_no,
        )
        response = Response({
            'code': 200, 'message': 'ok',
            'data': {
                'grant': str(grant.id),
                'password': password,
                'port': port_no,
            },
        })
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        return response

    @action(detail=True, methods=['post'], url_path='set_root_password')
    def set_root_password(self, request, pk=None):
        """设置 VPS 登录密码：写入档案，并在已同步节点时下发 cloud-init / Guest Agent。"""
        vps = self.get_object()
        ser = PortalSetRootPasswordSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        pwd = ser.validated_data['password']
        username = (ser.validated_data.get('username') or '').strip()
        if username:
            vps.mgmt_username = username
        if not vps.mgmt_username:
            vps.mgmt_username = 'ubuntu'
        from resource_manager.pemanager_client import PemanagerError, pop_has_api_config
        node_applied = False
        if vps.source_ref and vps.pop_point_id and pop_has_api_config(vps.pop_point):
            from resource_manager import pve_ops
            try:
                pve_ops.set_vps_credentials(vps, {
                    'username': vps.mgmt_username,
                    'password': pwd,
                    'apply_guest': True,
                }, dry_run=False)
                node_applied = True
            except PemanagerError as exc:
                return Response(
                    {'code': 400, 'message': f'节点改密失败：{exc}'},
                    status=400,
                )
        vps.mgmt_password = pwd
        vps.save(update_fields=['mgmt_password', 'mgmt_username', 'updated_at'])
        from .audit import log_portal_vps_action
        from resource_manager.models import ResourceOperationLog
        log_portal_vps_action(
            request, vps, '门户更新 Root 密码档案',
            action_type=ResourceOperationLog.ActionType.CONFIG_CHANGE,
            extra={'password_set': True, 'username': vps.mgmt_username},
        )
        return Response({
            'code': 200,
            'message': 'Root 密码已更新',
            'data': {
                'username': vps.mgmt_username,
                'gateway_reserved': True,
                'node_applied': node_applied,
            },
        })

    @action(detail=True, methods=['post'], url_path='sync_now')
    def sync_now(self, request, pk=None):
        """从节点同步 VPS 状态"""
        from resource_manager.sync_service import sync_single_vps_from_node
        from resource_manager.pemanager_client import PemanagerError

        vps = self.get_object()
        try:
            result = sync_single_vps_from_node(vps)
        except PemanagerError as e:
            return Response({'code': 400, 'message': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return Response({'code': 400, 'message': f'同步失败：{e}'}, status=400)
        if not result.get('ok'):
            detail = result.get('detail') or '同步未完成'
            return Response({'code': 400, 'message': detail, 'data': result}, status=400)
        vps.refresh_from_db()
        return Response({
            'code': 200, 'message': '同步成功',
            'data': {'result': result, 'vps': PortalVPSDetailSerializer(vps).data},
        })

    @action(
        detail=True,
        methods=['get'],
        url_path='traffic',
        throttle_classes=[CustomerMonitoringRateThrottle],
    )
    def traffic(self, request, pk=None):
        """VPS 流量监控（仅读取该客户资源已绑定的真实 Zabbix 数据）。"""
        vps = self.get_object()
        try:
            time_from, time_till, period = monitoring.resolve_traffic_query_window(
                hours=request.query_params.get('hours', 24),
                time_from=request.query_params.get('time_from'),
                time_till=request.query_params.get('time_till'),
            )
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = monitoring.traffic_series(
            'vps',
            vps,
            time_from=time_from,
            time_till=time_till,
            period=period,
        )
        return Response({'code': 200, 'message': 'ok', 'data': data})

    @action(detail=True, methods=['post'], url_path='reinstall')
    def reinstall(self, request, pk=None):
        from service_manager.self_service import request_reinstall

        vps = self.get_object()
        image_id = request.data.get('os_image')
        image = OsImage.objects.filter(pk=image_id, enabled=True).first()
        if image is None:
            return Response({'code': 400, 'message': '请选择已上架的系统镜像'}, status=400)
        password = str(request.data.get('password') or '')
        username = str(request.data.get('username') or '').strip()
        try:
            job = request_reinstall(
                vps, os_image=image, password=password, username=username,
                requested_by=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'code': 400, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)},
                status=400,
            )
        return Response({
            'code': 200, 'message': '重装任务已提交',
            'data': SelfServiceJobSerializer(job).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='snapshots')
    def snapshots(self, request, pk=None):
        vps = self.get_object()
        qs = InstanceSnapshot.objects.filter(vps=vps).exclude(
            status=InstanceSnapshot.Status.DELETED,
        )
        return Response({
            'code': 200, 'message': 'ok',
            'data': InstanceSnapshotSerializer(qs, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='snapshots/create')
    def create_snapshot(self, request, pk=None):
        from service_manager.self_service import request_snapshot

        vps = self.get_object()
        try:
            snap, job = request_snapshot(
                vps,
                name=str(request.data.get('name') or ''),
                note=str(request.data.get('note') or ''),
                requested_by=request.user,
                request=request,
            )
        except DjangoValidationError as exc:
            return Response(
                {'code': 400, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)},
                status=400,
            )
        return Response({
            'code': 200, 'message': '快照任务已提交',
            'data': {
                'snapshot': InstanceSnapshotSerializer(snap).data,
                'job': SelfServiceJobSerializer(job).data,
            },
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path=r'snapshots/(?P<snapshot_id>[0-9]+)/restore')
    def restore_snapshot(self, request, pk=None, snapshot_id=None):
        from service_manager.self_service import request_restore

        vps = self.get_object()
        snap = InstanceSnapshot.objects.filter(pk=snapshot_id, vps=vps).first()
        if snap is None:
            return Response({'code': 404, 'message': '快照不存在'}, status=404)
        try:
            job = request_restore(vps, snap, requested_by=request.user, request=request)
        except DjangoValidationError as exc:
            return Response(
                {'code': 400, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)},
                status=400,
            )
        return Response({
            'code': 200, 'message': '恢复任务已提交',
            'data': SelfServiceJobSerializer(job).data,
        })

    @action(detail=True, methods=['post'], url_path=r'snapshots/(?P<snapshot_id>[0-9]+)/delete')
    def delete_snapshot(self, request, pk=None, snapshot_id=None):
        from service_manager.self_service import delete_snapshot

        vps = self.get_object()
        snap = InstanceSnapshot.objects.filter(pk=snapshot_id, vps=vps).first()
        if snap is None:
            return Response({'code': 404, 'message': '快照不存在'}, status=404)
        try:
            delete_snapshot(snap, requested_by=request.user, request=request)
        except DjangoValidationError as exc:
            return Response(
                {'code': 400, 'message': exc.messages[0] if getattr(exc, 'messages', None) else str(exc)},
                status=400,
            )
        return Response({'code': 200, 'message': '快照已删除', 'data': InstanceSnapshotSerializer(snap).data})

    @action(detail=True, methods=['get'], url_path='self-service-jobs')
    def self_service_jobs(self, request, pk=None):
        vps = self.get_object()
        qs = SelfServiceJob.objects.filter(vps=vps).select_related('os_image', 'snapshot')[:50]
        return Response({
            'code': 200, 'message': 'ok',
            'data': SelfServiceJobSerializer(qs, many=True).data,
        })


class PortalOsImageView(APIView):
    """门户重装可选的已上架镜像。优先使用系统模版目录。"""

    permission_classes = [IsCustomer]

    def get(self, request):
        from service_manager.self_service import ensure_default_os_images
        from system_manager.models import SystemOsTemplate
        from system_manager.os_templates import sync_all_os_images

        if SystemOsTemplate.objects.filter(enabled=True).exists():
            sync_all_os_images()
            qs = OsImage.objects.filter(
                enabled=True,
                os_template__isnull=False,
                os_template__enabled=True,
            ).order_by('sort_order', 'id')
        else:
            ensure_default_os_images()
            qs = OsImage.objects.filter(enabled=True).order_by('sort_order', 'id')
        return Response({
            'code': 200, 'message': 'ok',
            'data': OsImageSerializer(qs, many=True).data,
        })


class PortalOsTemplateView(APIView):
    """门户下单可选系统版本，与管理端系统模版同一数据库。"""

    permission_classes = [AllowAny]
    authentication_classes = [JWTAuthentication]

    def get(self, request):
        from system_manager.models import SystemOsTemplate
        from system_manager.os_templates import (
            annotate_os_templates_for_portal,
            product_os_templates,
            sync_os_catalog_from_pop,
        )

        product_id = request.query_params.get('product_id')
        pop = None
        if product_id:
            product = Product.objects.filter(pk=product_id, status='online').select_related('pop_point').first()
            if product is None:
                return Response({'detail': '产品不存在或已下线'}, status=status.HTTP_404_NOT_FOUND)
            pop = product.pop_point
            catalog_items = sync_os_catalog_from_pop(pop)
            qs = product_os_templates(product, deployable_only=True)
        else:
            qs = list(SystemOsTemplate.objects.filter(enabled=True).order_by('sort_order', 'id'))
            catalog_items = []
        return Response({
            'code': 200, 'message': 'ok',
            'data': annotate_os_templates_for_portal(qs, catalog_items),
        })


class MyEdgeViewSet(CustomerScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PortalEdgeSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'protocol']
    ordering = ['-created_at']
    action_permissions = {
        'push_to_node': 'resource.edge.manage',
        'wg_genkey': 'resource.edge.manage',
    }

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PortalEdgeDetailSerializer
        return PortalEdgeSerializer

    def retrieve(self, request, *args, **kwargs):
        edge = self.get_object()
        if edge.protocol in ('gre', 'wireguard'):
            from resource_manager.edge_provision import ensure_pending_protocol_config
            ensure_pending_protocol_config(edge)
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        customer = self._customer()
        if customer is None:
            return EdgeAcceleration.objects.none()
        return EdgeAcceleration.objects.filter(customer=customer).select_related(
            'pop_point__city__country__region', 'product',
        ).prefetch_related(
            'ip_addresses', 'gre_config', 'wg_config', 'socks5_config',
        )

    @action(detail=True, methods=['get', 'put', 'patch'], url_path='protocol_config')
    def protocol_config(self, request, pk=None):
        """读取 / 更新 GRE / WireGuard / Socks5 配置"""
        from resource_manager.models import GREConfig, WireguardConfig, Socks5Config
        from resource_manager.serializers import (
            GREConfigSerializer, WireguardConfigSerializer, Socks5ConfigSerializer,
        )
        from resource_manager.views import mark_pop_dirty

        edge = self.get_object()
        if request.method != 'GET' and not check_permission(request.user, 'resource.edge.manage'):
            return Response({'code': 403, 'message': '当前角色无权修改边缘配置'}, status=403)
        protocol_map = {
            'gre': (GREConfig, GREConfigSerializer, 'gre_config'),
            'wireguard': (WireguardConfig, WireguardConfigSerializer, 'wg_config'),
            'socks5': (Socks5Config, Socks5ConfigSerializer, 'socks5_config'),
        }
        payload = request.data.copy() if request.method != 'GET' else {}
        requested = str((payload.get('protocol') if payload is not None else '') or '').strip().lower()
        if payload is not None and hasattr(payload, 'pop'):
            payload.pop('protocol', None)
        if request.method != 'GET' and requested and requested != edge.protocol:
            from product_manager.accel import parse_accel_types
            allowed = parse_accel_types(getattr(edge.product, 'accel_type', '') if edge.product_id else '')
            if requested not in ('gre', 'wireguard'):
                return Response({'code': 400, 'message': '仅支持切换 GRE 或 WireGuard'}, status=400)
            if allowed and requested not in allowed:
                return Response({'code': 400, 'message': '当前产品未开通该对接模式'}, status=400)
            edge.protocol = requested
            edge.node_ifname = ''
            edge.node_pushed = False
            edge.is_synced = False
            edge.save(update_fields=['protocol', 'node_ifname', 'node_pushed', 'is_synced'])
        if edge.protocol not in protocol_map:
            return Response({'code': 400, 'message': '当前协议不支持配置'}, status=400)

        from resource_manager.edge_provision import (
            apply_locked_protocol_fields,
            ensure_pending_protocol_config,
        )
        ensure_pending_protocol_config(edge)
        edge.refresh_from_db()
        for _rel in ('gre_config', 'wg_config', 'vxlan_config', 'socks5_config'):
            edge.__dict__.pop(_rel, None)

        model_cls, ser_cls, rel_name = protocol_map[edge.protocol]
        config = getattr(edge, rel_name, None)

        if request.method == 'GET':
            if not config:
                return Response({'code': 200, 'message': 'ok', 'data': None})
            return Response({'code': 200, 'message': 'ok', 'data': ser_cls(config).data})

        if not payload:
            return Response({
                'code': 200,
                'message': '对接模式已更新' if requested else 'ok',
                'data': PortalEdgeDetailSerializer(edge).data,
            })

        config_data = payload.dict() if hasattr(payload, 'dict') else dict(payload)
        config_data = apply_locked_protocol_fields(edge, edge.protocol, config_data, config)
        partial = request.method == 'PATCH'
        if config:
            ser = ser_cls(config, data=config_data, partial=partial)
        else:
            ser = ser_cls(data={**config_data, 'edge': edge.pk})
        ser.is_valid(raise_exception=True)

        conflicts = check_edge_ip_conflicts(edge, edge.protocol, ser.validated_data, config)
        if edge.protocol == 'wireguard':
            from resource_manager.edge_provision import wireguard_key_errors
            merged_public = ser.validated_data.get('public_key', getattr(config, 'public_key', ''))
            merged_private = ser.validated_data.get('private_key', getattr(config, 'private_key', ''))
            merged_peer = ser.validated_data.get('peer_public_key', getattr(config, 'peer_public_key', ''))
            conflicts = list(conflicts) + wireguard_key_errors(
                merged_public, merged_private, merged_peer,
            )
        if conflicts:
            return Response(
                {'code': 400, 'message': '配置校验未通过：\n' + '\n'.join(conflicts)},
                status=400,
            )

        ser.save()
        mark_pop_dirty(edge.pop_point)
        edge.refresh_from_db()
        for _rel in ('gre_config', 'wg_config', 'vxlan_config', 'socks5_config'):
            edge.__dict__.pop(_rel, None)
        from .audit import log_portal_edge_action
        from resource_manager.models import ResourceOperationLog
        log_portal_edge_action(
            request, edge, f'门户更新边缘协议配置（{edge.protocol}）',
            action_type=ResourceOperationLog.ActionType.CONFIG_CHANGE,
            extra={'protocol': edge.protocol},
        )
        return Response({
            'code': 200, 'message': '配置已保存',
            'data': PortalEdgeDetailSerializer(edge).data,
        })

    @action(detail=True, methods=['post'], url_path='push_to_node')
    def push_to_node(self, request, pk=None):
        """下发隧道配置到节点"""
        from resource_manager.deploy_service import push_pop_to_node
        from resource_manager.pemanager_client import PemanagerError

        edge = self.get_object()
        if edge.protocol == EdgeAcceleration.Protocol.SOCKS5:
            return Response({'code': 400, 'message': 'Socks5 暂不支持节点下发'}, status=400)

        from resource_manager.edge_provision import edge_config_ready
        ready, missing = edge_config_ready(edge)
        if not ready:
            return Response({
                'code': 400,
                'message': '请先补全协议配置：' + '、'.join(missing),
            }, status=400)

        rel_map = {'gre': 'gre_config', 'wireguard': 'wg_config'}
        cur_config = getattr(edge, rel_map.get(edge.protocol, ''), None)
        conflicts = check_edge_ip_conflicts(edge, edge.protocol, {}, cur_config)
        if edge.protocol == 'wireguard' and cur_config is not None:
            from resource_manager.edge_provision import wireguard_key_errors
            conflicts = list(conflicts) + wireguard_key_errors(
                cur_config.public_key,
                cur_config.private_key,
                cur_config.peer_public_key,
                require_peer=True,
            )
        if conflicts:
            return Response(
                {'code': 400, 'message': '下发前校验未通过：\n' + '\n'.join(conflicts)},
                status=400,
            )

        dry_run = bool(request.data.get('dry_run', False))
        try:
            result = push_pop_to_node(
                edge.pop_point, sections=['customers', 'edge_tunnels'], dry_run=dry_run,
            )
        except PemanagerError as e:
            return Response({'code': 400, 'message': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return Response({'code': 400, 'message': f'下发失败：{e}'}, status=400)
        edge.refresh_from_db()

        # 汇总下发结果并如实回报（区分成功 / 部分失败），供门户实时反馈是否已生效
        et = (result.get('summary') or {}).get('edge_tunnels') or {}
        errs = list(et.get('reconfigure_errors') or []) + list(et.get('bandwidth_errors') or [])
        for _sec, _msg in (result.get('errors') or {}).items():
            errs.append(f'{_sec}: {_msg}')
        from .audit import log_portal_edge_action
        from resource_manager.models import ResourceOperationLog
        log_portal_edge_action(
            request, edge,
            '门户下发隧道到节点（演练）' if dry_run else '门户下发隧道到节点',
            action_type=ResourceOperationLog.ActionType.DEPLOY,
            extra={'dry_run': dry_run, 'ok': bool(result.get('ok', True)) and not errs},
            notify=not dry_run,
        )
        if dry_run:
            plan = et.get('plan') or []
            lines = []
            for item in plan:
                if not isinstance(item, dict):
                    continue
                name = item.get('ifname') or item.get('edge_no') or item.get('name') or '接口'
                state = item.get('state') or ''
                lines.append(f'{name}：{state}' if state else str(name))
            preview_text = (
                '预览计划：' + '；'.join(lines)
                if lines else
                '将核对当前隧道配置（无新建接口）'
            )
            return Response({
                'code': 200,
                'message': '已生成下发计划',
                'data': result,
                'preview_text': preview_text,
            })
        apply_errs = result.get('apply_errors') or {}
        for _sec, _items in apply_errs.items():
            if isinstance(_items, (list, tuple)):
                errs.extend(str(x) for x in _items)
            elif _items:
                errs.append(str(_items))
        seen_errs = set()
        uniq_errs = []
        for item in errs:
            text = str(item)
            if text not in seen_errs:
                seen_errs.add(text)
                uniq_errs.append(item)
        errs = uniq_errs
        if errs or not result.get('ok', True) or result.get('apply_ok') is False:
            detail = '；'.join(str(e) for e in errs) or '节点未确认生效'
            return Response(
                {'code': 400, 'message': f'下发未完全生效：{detail}', 'data': result},
                status=400,
            )
        parts = []
        if et.get('reconfigured'):
            parts.append(f"重配置 {et['reconfigured']} 个接口")
        if et.get('created'):
            parts.append(f"新建 {et['created']} 个接口")
        if et.get('unchanged'):
            parts.append(f"{et['unchanged']} 个接口无变化")
        ifname = edge.node_ifname or '—'
        if not edge.node_pushed:
            return Response({
                'code': 400,
                'message': '当前隧道配置未写入节点，请确认协议已补全后再下发',
                'data': result,
            }, status=400)
        from resource_manager.edge_provision import finalize_edge_after_push
        finalized = finalize_edge_after_push(edge, operator=request.user)
        edge.refresh_from_db()
        ifname = edge.node_ifname or ifname
        msg = f'已实时下发到节点并生效（接口 {ifname}）'
        if parts:
            msg += '：' + '，'.join(parts)
        if finalized.get('route_mode') == 'wireguard_allowed_ips':
            msg += '，' + (finalized.get('route_detail') or '业务路由已写入 WireGuard AllowedIPs')
        elif finalized.get('route_pushed'):
            msg += f'，已下发 {finalized["route_pushed"]} 条 /32 主机路由'
        if finalized.get('route_errors'):
            msg += '；路由下发警告：' + '；'.join(finalized['route_errors'][:2])
        result = dict(result or {})
        result['finalize'] = finalized
        result['edge'] = PortalEdgeDetailSerializer(edge).data
        customers = (result.get('summary') or {}).get('customers') or {}
        result['progress_steps'] = [
            {
                'key': 'customers',
                'label': '客户档案写入节点',
                'status': 'done' if not (result.get('errors') or {}).get('customers') else 'error',
                'detail': '客户与带宽档案已同步' if customers else '已核对客户档案',
            },
            {
                'key': 'interface',
                'label': '边缘加速接口配置完成',
                'status': 'done' if (et.get('created') or et.get('reconfigured') or et.get('unchanged')) else 'error',
                'detail': '，'.join(parts) or f'接口 {ifname}',
            },
            {
                'key': 'route',
                'label': '业务路由配置完成',
                'status': 'warn' if finalized.get('route_errors') else 'done',
                'detail': (
                    finalized.get('route_detail')
                    if finalized.get('route_mode') == 'wireguard_allowed_ips'
                    else (
                        f'已下发 {finalized.get("route_pushed") or 0} 条 /32 主机路由'
                        if finalized.get('route_pushed') or not finalized.get('route_errors')
                        else '；'.join(finalized.get('route_errors') or [])[:160]
                    )
                ),
            },
            {
                'key': 'activate',
                'label': '服务已生效',
                'status': 'done' if edge.node_pushed else 'error',
                'detail': f'接口 {ifname}，状态 {edge.get_status_display()}',
            },
        ]
        return Response({'code': 200, 'message': msg, 'data': result})

    @action(detail=True, methods=['post'], url_path='wg_genkey')
    def wg_genkey(self, request, pk=None):
        """WireGuard 密钥对生成"""
        from resource_manager.pemanager_client import PemanagerClient, PemanagerError

        edge = self.get_object()
        pop = edge.pop_point
        try:
            client, _user = PemanagerClient.connect_pop(pop)
            data = client.wg_genkey() or {}
        except PemanagerError as e:
            return Response({'code': 400, 'message': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            return Response({'code': 400, 'message': f'生成密钥失败：{e}'}, status=400)
        return Response({
            'code': 200, 'message': 'ok',
            'data': {
                'private': data.get('private') or data.get('private_key'),
                'public': data.get('public') or data.get('public_key'),
            },
        })

    @action(
        detail=True,
        methods=['get'],
        url_path='traffic',
        throttle_classes=[CustomerMonitoringRateThrottle],
    )
    def traffic(self, request, pk=None):
        """边缘加速流量监控（仅读取该客户资源已绑定的真实 Zabbix 数据）。"""
        edge = self.get_object()
        try:
            time_from, time_till, period = monitoring.resolve_traffic_query_window(
                hours=request.query_params.get('hours', 24),
                time_from=request.query_params.get('time_from'),
                time_till=request.query_params.get('time_till'),
            )
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = monitoring.traffic_series(
            'edge',
            edge,
            time_from=time_from,
            time_till=time_till,
            period=period,
        )
        return Response({'code': 200, 'message': 'ok', 'data': data})

    @action(
        detail=True,
        methods=['get'],
        url_path='quality',
        throttle_classes=[CustomerMonitoringRateThrottle],
    )
    def quality(self, request, pk=None):
        """边缘加速品质监控（中心库中由 POP 节点上报的真实样本）。"""
        edge = self.get_object()
        try:
            hours = monitoring.validate_hours(request.query_params.get('hours', 24))
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = monitoring.quality_series(edge, hours=hours)
        return Response({'code': 200, 'message': 'ok', 'data': data})
