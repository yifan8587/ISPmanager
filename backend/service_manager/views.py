"""
service_manager/views.py
服务管理模块 DRF ViewSets
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.db import models, transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import (
    ServiceModulePermissionMixin,
    ServiceSystemPermissionMixin,
    CustomerScopedQuerysetMixin,
    ModulePermissionMixin,
    ViewOnlyPermissionMixin,
)
from accounts.role_permissions import Perm, page_permission
from backend.throttles import PublicDeliveryCallbackRateThrottle

from .models import (
    ServiceOrder, ServiceInquiry, ServiceInstance,
    ServiceAllocation, ServiceChangeRecord, ServiceOperationLog,
    ServiceLifecycleRecord, ServiceProvisionHook,
    ServiceInquiryReply, SupportTicket, _log_service, service_snapshot,
    sync_service_order_delivery_state,
)
from .serializers import (
    ServiceOrderListSerializer, ServiceOrderDetailSerializer, ServiceOrderWriteSerializer,
    ServiceInstanceListSerializer, ServiceInstanceDetailSerializer, ServiceInstanceWriteSerializer,
    ServiceAllocationSerializer,
    ServiceChangeRecordListSerializer, ServiceChangeRecordDetailSerializer, ServiceChangeRecordWriteSerializer,
    ServiceOperationLogSerializer,
    ServiceLifecycleRecordSerializer, ServiceProvisionHookSerializer,
    StatusChangeSerializer, ApproveOrderSerializer, ChangeStatusSerializer, IPManageSerializer,
    AssignHandlerSerializer, AdvanceStageSerializer,
    ServiceInquirySerializer, InquiryReplySerializer, InquiryConvertSerializer,
    InquiryConvertTicketSerializer,
    SupportTicketListSerializer, SupportTicketSerializer, SupportTicketWriteSerializer,
    SupportTicketReplySerializer, SupportTicketAssignSerializer, SupportTicketCloseSerializer,
    OrderDeliverySerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 通用 Mixin
# ─────────────────────────────────────────────────────────────

class ServiceLogMixin:
    """统一日志写入辅助"""
    def _log(self, request, action_type, description,
             service_order=None, service_instance=None,
             old_value=None, new_value=None):
        _log_service(
            operator=request.user,
            action_type=action_type,
            description=description,
            service_order=service_order,
            service_instance=service_instance,
            old_value=old_value,
            new_value=new_value,
            request=request,
        )


# ═══════════════════════════════════════════════════════════════
# § 1  服务请求
# ═══════════════════════════════════════════════════════════════

class ServiceOrderViewSet(
    CustomerScopedQuerysetMixin,
    ServiceModulePermissionMixin,
    ServiceLogMixin,
    viewsets.ModelViewSet,
):
    """
    服务工单完整 CRUD + 审批/驳回/统计
    custom actions:
      approve  POST /orders/{id}/approve/    审批或驳回工单
      logs     GET  /orders/{id}/logs/       工单操作日志
      statistics GET /orders/statistics/    工单统计汇总
    """
    extra_read_actions = frozenset({
        'logs', 'statistics', 'lifecycle', 'overview', 'changes', 'snapshot',
        'manual_delivery', 'catalog',
    })

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'status', 'order_type', 'customer', 'billing_cycle',
        'business_type', 'current_stage', 'source', 'handler', 'product',
    ]
    search_fields = ['order_no', 'customer__name', 'customer__customer_no', 'requirements']
    ordering_fields = ['created_at', 'service_start', 'quoted_price']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ServiceOrder.objects.select_related(
            'customer', 'contract', 'product',
            'created_by', 'approved_by', 'handler', 'preferred_pop', 'related_instance',
            'payment_billing', 'manual_delivery', 'requested_os_template',
        ).prefetch_related(
            models.Prefetch(
                'instances',
                queryset=ServiceInstance.objects.select_related(
                    'vps', 'edge', 'bandwidth_alloc',
                ).prefetch_related('ip_addresses'),
            ),
            'lifecycle_records',
            'capacity_reservations',
        )
        start = self.request.query_params.get('start') or self.request.query_params.get('date_from')
        end = self.request.query_params.get('end') or self.request.query_params.get('date_to')
        if start or end:
            from .stats import apply_time_filters
            qs = apply_time_filters(qs, start=start, end=end)
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return ServiceOrderListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ServiceOrderWriteSerializer
        return ServiceOrderDetailSerializer

    def perform_create(self, serializer):
        obj = serializer.save(
            created_by=self.request.user,
            source=serializer.validated_data.get('source') or ServiceOrder.OrderSource.ADMIN,
        )
        self._log(
            self.request,
            ServiceOperationLog.ActionType.CREATE_ORDER,
            f'创建服务工单 [{obj.order_no}] 客户: {obj.customer.name}',
            service_order=obj,
        )
        try:
            from .lifecycle import bootstrap_order_lifecycle
            bootstrap_order_lifecycle(obj, submitted_by=self.request.user)
        except Exception:  # noqa: BLE001
            logger.warning('工单 [%s] 生命周期初始化失败', obj.order_no, exc_info=True)
        from .notify_helpers import notify_order_event
        notify_order_event(obj, '新建工单', event='service_order')

    def _try_auto_provision(self, order):
        """尝试自动开通；异常不影响主流程。"""
        try:
            from .provisioning import auto_provision
            auto_provision(order, operator=self.request.user)
            order.refresh_from_db()
        except Exception:  # noqa: BLE001
            logger.warning('工单 [%s] 自动开通触发异常', order.order_no, exc_info=True)

    def perform_update(self, serializer):
        old = {
            field: getattr(serializer.instance, field)
            for field in serializer.validated_data.keys()
        }
        obj = serializer.save()
        self._log(
            self.request,
            ServiceOperationLog.ActionType.OTHER,
            f'更新工单 [{obj.order_no}]',
            service_order=obj,
            old_value=old,
            new_value={
                field: getattr(obj, field)
                for field in serializer.validated_data.keys()
            },
        )
        from .notify_helpers import notify_order_event
        notify_order_event(
            obj,
            '工单信息已更新',
            detail=f'变更字段：{", ".join(sorted(serializer.validated_data.keys())) or "—"}',
        )

    def destroy(self, request, *args, **kwargs):
        """删除工单；目标已不存在时视为成功，避免列表未刷新时误报 404。"""
        try:
            instance = self.get_object()
        except (Http404, NotFound):
            return Response(status=status.HTTP_204_NO_CONTENT)
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        from .order_pop import delete_service_order

        delete_service_order(
            instance,
            operator=self.request.user,
            request=self.request,
        )

    @action(detail=False, methods=['get'], url_path='catalog')
    def catalog(self, request):
        """下单可选目录：上线产品、POP 与系统模版，不开放产品/资源管理。"""
        from product_manager.models import Product
        from resource_manager.models import PopPoint
        from system_manager.models import SystemOsTemplate
        from system_manager.serializers import SystemOsTemplateBriefSerializer

        products = Product.objects.filter(
            status=Product.Status.ONLINE,
        ).select_related('pop_point').order_by('sort_order', 'name', 'id')[:500]
        pops = PopPoint.objects.order_by('name', 'id').only('id', 'name', 'code', 'status')[:500]
        os_templates = SystemOsTemplate.objects.filter(enabled=True).order_by('sort_order', 'id')
        return Response({
            'products': [
                {
                    'id': item.id,
                    'product_no': item.product_no,
                    'name': item.name,
                    'category': item.category,
                    'pop_point': item.pop_point_id,
                    'status': item.status,
                    'currency': item.currency,
                    'monthly_price': item.monthly_price,
                    'cpu_cores': item.cpu_cores,
                    'ram_mb': item.ram_mb,
                    'disk_gb': item.disk_gb,
                    'bandwidth_mbps': item.bandwidth_mbps,
                    'ip_count': item.ip_count,
                    'accel_type': item.accel_type,
                }
                for item in products
            ],
            'pops': [
                {
                    'id': item.id,
                    'name': item.name,
                    'code': item.code,
                    'status': item.status,
                }
                for item in pops
            ],
            'os_templates': SystemOsTemplateBriefSerializer(os_templates, many=True).data,
        })

    @action(detail=True, methods=['post'], url_path='approve')
    @transaction.atomic
    def approve(self, request, pk=None):
        """审批或驳回工单"""
        order = self.get_object()
        if order.status != 'pending':
            return Response({'detail': '只有待审批状态的工单才能审批'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = ApproveOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        from .notify_helpers import notify_order_event
        from .lifecycle import mark_requirement_stage
        from .hooks import dispatch_provision_hooks
        from .payment_billing import (
            OrderBillingError,
            ensure_order_payment_billing,
            order_quote,
        )

        if data['approved']:
            from .order_pop import apply_product_fulfillment
            apply_product_fulfillment(order, save=True)
            try:
                quoted_amount, _currency = order_quote(order)
            except OrderBillingError as exc:
                raise ValidationError({'quoted_price': str(exc)}) from exc
            from service_manager.fx_snapshot import lock_order_fx
            lock_order_fx(order)
            order.status = 'approved'
            order.approved_by = request.user
            order.approved_at = timezone.now()
            order.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
            self._log(request, ServiceOperationLog.ActionType.APPROVE_ORDER,
                      f'审批通过工单 [{order.order_no}]', service_order=order)
            mark_requirement_stage(order, handled_by=order.handler, approved_by=request.user)
            notify_order_event(order, '审批通过', event='service_order')
            if quoted_amount == 0:
                from service_manager.capacity_reservation import pin_reservation
                pin_reservation(order)
                dispatch_provision_hooks('order_approved', order=order)
                self._try_auto_provision(order)
            else:
                try:
                    billing = ensure_order_payment_billing(order)
                except OrderBillingError as exc:
                    raise ValidationError({'quoted_price': str(exc)}) from exc
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
            order.refresh_from_db()
        else:
            order.status = 'rejected'
            order.reject_reason = data['reject_reason']
            order.save(update_fields=['status', 'reject_reason', 'updated_at'])
            from service_manager.capacity_reservation import release_reservation
            release_reservation(order, reason=f'审批驳回：{data["reject_reason"]}')
            self._log(request, ServiceOperationLog.ActionType.REJECT_ORDER,
                      f'驳回工单 [{order.order_no}] 原因: {data["reject_reason"]}',
                      service_order=order)
            notify_order_event(order, '审批驳回', detail=data['reject_reason'], event='service_order')

        return Response(ServiceOrderDetailSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='retry-auto-provision')
    def retry_auto_provision(self, request, pk=None):
        """管理员重试已付款（或零金额）工单的自动开通。"""
        order = self.get_object()
        from .order_pop import apply_product_fulfillment
        apply_product_fulfillment(order, save=True)
        if order.status not in (
            ServiceOrder.OrderStatus.APPROVED,
            ServiceOrder.OrderStatus.PROVISIONING,
        ):
            return Response(
                {'detail': '只有已审批或开通中的未交付工单可以重试'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.instances.exists():
            return Response(
                {'detail': '工单已存在服务实例，无需重试'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        billing = getattr(order, 'payment_billing', None)
        if billing is not None:
            if billing.status != billing.BillingStatus.PAID:
                return Response(
                    {'detail': '专属账单尚未付清，不能开通'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from .payment_fulfillment import fulfill_paid_billing
            fulfill_paid_billing(billing.pk, force=True)
        else:
            from .payment_billing import OrderBillingError, order_quote
            try:
                amount, _currency = order_quote(order)
            except OrderBillingError as exc:
                raise ValidationError({'quoted_price': str(exc)}) from exc
            if amount != 0:
                return Response(
                    {'detail': '工单缺少专属预付账单'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            self._try_auto_provision(order)
        order.refresh_from_db()
        return Response(ServiceOrderDetailSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='manual-delivery')
    def manual_delivery(self, request, pk=None):
        from .manual_delivery import public_summary
        order = self.get_object()
        data = public_summary(order, include_items=True)
        if data is None:
            return Response({'exists': False})
        return Response(data)

    @action(detail=True, methods=['post'], url_path='manual-delivery/rotate-token')
    def rotate_manual_delivery_token(self, request, pk=None):
        from .manual_delivery import delivery_for, rotate_token
        order = self.get_object()
        delivery = delivery_for(order)
        if delivery is None:
            return Response({'detail': '该工单还没有人工交付清单'}, status=status.HTTP_400_BAD_REQUEST)
        token = rotate_token(delivery)
        self._log(
            request,
            ServiceOperationLog.ActionType.OTHER,
            f'轮换工单 [{order.order_no}] 人工交付回传令牌',
            service_order=order,
        )
        return Response({
            'token': token,
            'token_prefix': delivery.token_prefix,
            'rotated_at': delivery.token_rotated_at,
        })

    @action(detail=True, methods=['post'], url_path='manual-delivery/callback')
    def manual_delivery_callback(self, request, pk=None):
        from .manual_delivery import (
            ManualDeliveryError, apply_callback, delivery_for, parse_callback_payload,
        )
        order = self.get_object()
        delivery = delivery_for(order)
        if delivery is None:
            return Response({'detail': '该工单还没有人工交付清单'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            result = apply_callback(
                delivery,
                parse_callback_payload(request.data),
                source='admin_callback',
                operator=request.user,
            )
        except ManualDeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        result['order'] = ServiceOrderDetailSerializer(order, context={'request': request}).data
        return Response(result)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """提交草稿工单"""
        order = self.get_object()
        if order.status != 'draft':
            return Response({'detail': '只有草稿状态的工单才能提交'}, status=status.HTTP_400_BAD_REQUEST)
        order.status = 'pending'
        order.save(update_fields=['status', 'updated_at'])
        self._log(request, ServiceOperationLog.ActionType.SUBMIT_ORDER,
                  f'提交工单 [{order.order_no}] 待审批', service_order=order)
        try:
            from .lifecycle import bootstrap_order_lifecycle
            bootstrap_order_lifecycle(order, submitted_by=request.user)
        except Exception:  # noqa: BLE001
            logger.warning('工单 [%s] 提交生命周期写入失败', order.order_no, exc_info=True)
        from .notify_helpers import notify_order_event
        notify_order_event(order, '提交待审批', event='service_order')
        return Response(ServiceOrderDetailSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='assign_handler')
    def assign_handler(self, request, pk=None):
        """指派当前处理人"""
        from django.contrib.auth.models import User
        order = self.get_object()
        serializer = AssignHandlerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        handler = User.objects.filter(pk=serializer.validated_data['handler_id']).first()
        if not handler:
            return Response({'detail': '处理人不存在'}, status=status.HTTP_400_BAD_REQUEST)
        old = {'handler_id': order.handler_id}
        order.handler = handler
        order.assigned_at = timezone.now()
        order.save(update_fields=['handler', 'assigned_at', 'updated_at'])
        self._log(
            request, ServiceOperationLog.ActionType.ASSIGN_HANDLER,
            f'工单 [{order.order_no}] 指派处理人 {handler.username}',
            service_order=order, old_value=old,
            new_value={'handler_id': handler.id, 'remark': serializer.validated_data.get('remark', '')},
        )
        from .notify_helpers import notify_order_event
        notify_order_event(order, '指派处理人', detail=f'处理人：{handler.username}', event='service_order')
        return Response(ServiceOrderDetailSerializer(order, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='advance_stage')
    def advance_stage(self, request, pk=None):
        """手动推进生命周期阶段（需求确认/测试/使用/变更/终止）"""
        from .lifecycle import ensure_stage, mark_test_stage, mark_usage_stage, mark_termination_stage
        order = self.get_object()
        serializer = AdvanceStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        stage = data['stage']
        instance = None
        if data.get('service_instance_id'):
            instance = order.instances.filter(pk=data['service_instance_id']).first()
        elif order.instances.exists():
            instance = order.instances.order_by('-id').first()

        if stage == ServiceOrder.LifecycleStage.TEST and instance:
            if data.get('is_trial') and not instance.is_trial:
                instance.is_trial = True
                instance.trial_start = instance.trial_start or timezone.now().date()
                instance.save(update_fields=['is_trial', 'trial_start', 'updated_at'])
            record = mark_test_stage(order, instance, handled_by=request.user)
        elif stage == ServiceOrder.LifecycleStage.USAGE and instance:
            record = mark_usage_stage(order, instance, handled_by=request.user)
        elif stage == ServiceOrder.LifecycleStage.TERMINATION and instance:
            record = mark_termination_stage(
                order, instance, handled_by=request.user, detail=data.get('detail', ''),
            )
        else:
            record = ensure_stage(
                order, stage,
                submitted_by=order.created_by,
                handled_by=request.user,
                service_instance=instance,
                detail=data.get('detail', ''),
                status=ServiceLifecycleRecord.StageStatus.COMPLETED,
            )
        return Response(ServiceLifecycleRecordSerializer(record).data)

    def _delivery_options(self, order):
        """构造与工单客户、POP、已绑定状态严格匹配的可交付资源选项。"""
        from resource_manager.models import (
            BandwidthAllocation, EdgeAcceleration, IPAddress, VPS,
        )

        pop_filter = Q(pop_point=order.preferred_pop) if order.preferred_pop_id else Q()
        customer_filter = Q(customer__isnull=True) | Q(customer=order.customer)

        vps_qs = (
            VPS.objects.filter(pop_filter, customer_filter)
            .exclude(service_instance__isnull=False)
            .select_related('pop_point')
            .order_by('name')[:500]
        )
        edge_qs = (
            EdgeAcceleration.objects.filter(pop_filter, customer_filter)
            .exclude(service_instance__isnull=False)
            .select_related('pop_point')
            .order_by('name')[:500]
        )
        ip_qs = IPAddress.objects.filter(status=IPAddress.IPStatus.AVAILABLE)
        if order.preferred_pop_id:
            ip_qs = ip_qs.filter(ip_block__pop_point=order.preferred_pop)
        ip_qs = ip_qs.select_related('ip_block').order_by('address')[:500]

        bw_qs = (
            BandwidthAllocation.objects.filter(
                pop_filter,
                is_active=True,
            )
            .filter(
                Q(vps__isnull=False, vps__customer__isnull=True)
                | Q(vps__customer=order.customer)
                | Q(edge__isnull=False, edge__customer__isnull=True)
                | Q(edge__customer=order.customer)
            )
            .exclude(service_instance__isnull=False)
            .select_related('vps', 'edge', 'pop_point')
            .order_by('-updated_at')[:500]
        )
        return {
            'vps': [{
                'id': obj.id,
                'label': f'{obj.vps_no} · {obj.name}',
                'pop_name': obj.pop_point.name if obj.pop_point_id else '',
                'status': obj.status,
                'customer_id': obj.customer_id,
                'bandwidth_mbps': obj.bandwidth_mbps,
            } for obj in vps_qs],
            'edge': [{
                'id': obj.id,
                'label': f'{obj.edge_no} · {obj.name}',
                'pop_name': obj.pop_point.name if obj.pop_point_id else '',
                'status': obj.status,
                'customer_id': obj.customer_id,
                'protocol': obj.protocol,
                'bandwidth_mbps': obj.bandwidth_mbps,
            } for obj in edge_qs],
            'ip_addresses': [{
                'id': obj.id,
                'address': str(obj.address),
                'prefix_len': obj.prefix_len,
                'label': f'{obj.address}/{obj.prefix_len}',
            } for obj in ip_qs],
            'bandwidth_allocations': [{
                'id': obj.id,
                'allocated_mbps': obj.allocated_mbps,
                'target_type': obj.target_type,
                'target_label': (
                    f'VPS · {obj.vps.vps_no} {obj.vps.name}'
                    if obj.vps_id else f'边缘 · {obj.edge.edge_no} {obj.edge.name}'
                    if obj.edge_id else '未绑定目标'
                ),
                'vps_id': obj.vps_id,
                'edge_id': obj.edge_id,
            } for obj in bw_qs],
        }

    @action(detail=True, methods=['get'], url_path='delivery-options')
    def delivery_options(self, request, pk=None):
        """返回当前工单可以安全交付的 VPS、边缘、IP 与带宽资源。"""
        order = self.get_object()
        from .order_pop import apply_product_fulfillment, can_quick_provision
        apply_product_fulfillment(order, save=True)
        order.refresh_from_db()
        if order.status not in (
            ServiceOrder.OrderStatus.APPROVED,
            ServiceOrder.OrderStatus.PROVISIONING,
            ServiceOrder.OrderStatus.ACTIVE,
        ):
            return Response(
                {'detail': '工单需先完成审批，才可选择资源进行交付'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = self._delivery_options(order)
        product = order.product
        payload.update({
            'order_id': order.id,
            'order_no': order.order_no,
            'business_type': order.business_type,
            'requested_quantity': order.requested_quantity,
            'requested_ip_count': order.requested_ip_count,
            'requested_bandwidth_mbps': order.requested_bandwidth_mbps,
            'preferred_pop_id': order.preferred_pop_id,
            'preferred_pop_name': order.preferred_pop.name if order.preferred_pop_id else '',
            'product_pop_id': product.pop_point_id if product else None,
            'product_pop_name': (
                product.pop_point.name
                if product and product.pop_point_id else ''
            ),
            'can_quick_provision': can_quick_provision(order),
        })
        return Response(payload)

    @action(detail=True, methods=['post'], url_path='deliver')
    @transaction.atomic
    def deliver(self, request, pk=None):
        """人工交付资源并原子回写实例、分配记录、生命周期和工单状态。"""
        from resource_manager.models import (
            BandwidthAllocation, EdgeAcceleration, IPAddress, VPS,
        )
        from .lifecycle import mark_requirement_stage, mark_test_stage, mark_usage_stage
        from .notify_helpers import notify_instance_event, notify_order_event
        from .provisioning import build_spec

        order = ServiceOrder.objects.select_for_update().get(pk=pk)
        from .order_pop import apply_product_fulfillment, bootstrap_order_resource
        apply_product_fulfillment(order, save=True)
        order.refresh_from_db()
        if order.status not in (
            ServiceOrder.OrderStatus.APPROVED,
            ServiceOrder.OrderStatus.PROVISIONING,
            ServiceOrder.OrderStatus.ACTIVE,
        ):
            return Response(
                {'detail': '只有已审批或开通中的工单可以交付资源'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment_billing = getattr(order, 'payment_billing', None)
        if payment_billing and payment_billing.status != payment_billing.BillingStatus.PAID:
            return Response(
                {'detail': '该工单的预付账单尚未付清，不能交付资源'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.quoted_price and order.quoted_price > 0 and payment_billing is None:
            return Response(
                {'detail': '该工单缺少预付账单，请通过审批流程生成账单后再交付资源'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = OrderDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get('quick_bootstrap'):
            created = bootstrap_order_resource(order, operator=request.user)
            if created.get('vps'):
                data['vps_id'] = created['vps'].pk
            if created.get('edge'):
                data['edge_id'] = created['edge'].pk
            if created.get('edge') and not created['edge'].node_pushed:
                data['activate'] = False

        if (
            order.order_type in (
                ServiceOrder.OrderType.UPGRADE,
                ServiceOrder.OrderType.DOWNGRADE,
                ServiceOrder.OrderType.CHANGE,
            )
            and order.related_instance_id
        ):
            from .change_executor import ChangeExecutionError
            from .change_provisioning import ChangeProvisionError, deliver_change_order
            try:
                result = deliver_change_order(
                    order,
                    ip_ids=data.get('ip_ids') or [],
                    operator=request.user,
                    remark=data.get('remark', ''),
                )
            except (ChangeProvisionError, ChangeExecutionError) as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            order.refresh_from_db()
            instance = order.related_instance
            notify_instance_event(
                instance,
                '人工资源交付完成',
                detail=f'变更工单 {order.order_no} 已交付到实例 {instance.instance_no}',
            )
            notify_order_event(
                order,
                '工单资源交付同步',
                detail=f'变更 {result.get("change_no")} 已手动交付完成',
            )
            from .manual_delivery import mark_admin_deliver
            mark_admin_deliver(order)
            return Response({
                'detail': '变更资源已交付并同步更新服务工单',
                'order': ServiceOrderDetailSerializer(order, context={'request': request}).data,
                'instance': ServiceInstanceDetailSerializer(instance, context={'request': request}).data,
            }, status=status.HTTP_200_OK)

        target_type = {
            ServiceOrder.BusinessType.VPS: ServiceInstance.ServiceType.VPS,
            ServiceOrder.BusinessType.EDGE_GRE: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.EDGE_WG: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.EDGE_SOCKS5: ServiceInstance.ServiceType.EDGE_ACCELERATION,
            ServiceOrder.BusinessType.IP: ServiceInstance.ServiceType.IP_ADDRESS,
            ServiceOrder.BusinessType.BANDWIDTH: ServiceInstance.ServiceType.BANDWIDTH,
        }[order.business_type]
        delivered_count = order.instances.exclude(
            status__in=[
                ServiceInstance.InstanceStatus.TERMINATED,
                ServiceInstance.InstanceStatus.ERROR,
                ServiceInstance.InstanceStatus.EXPIRED,
            ],
        ).filter(service_type=target_type).count()
        if delivered_count >= order.requested_quantity:
            return Response(
                {'detail': f'工单已完成 {delivered_count}/{order.requested_quantity} 个服务实例交付'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vps = edge = bandwidth_alloc = None
        if data.get('vps_id'):
            vps = VPS.objects.select_for_update().filter(pk=data['vps_id']).first()
            if not vps:
                raise ValidationError({'vps_id': 'VPS 不存在'})
        if data.get('edge_id'):
            edge = EdgeAcceleration.objects.select_for_update().filter(pk=data['edge_id']).first()
            if not edge:
                raise ValidationError({'edge_id': '边缘加速资源不存在'})
        if data.get('bandwidth_allocation_id'):
            bandwidth_alloc = (
                BandwidthAllocation.objects.select_for_update()
                .select_related('vps', 'edge')
                .filter(pk=data['bandwidth_allocation_id'])
                .first()
            )
            if not bandwidth_alloc:
                raise ValidationError({'bandwidth_allocation_id': '带宽分配不存在'})

        expected = {
            ServiceInstance.ServiceType.VPS: ('vps', vps),
            ServiceInstance.ServiceType.EDGE_ACCELERATION: ('edge', edge),
            ServiceInstance.ServiceType.BANDWIDTH: ('bandwidth_allocation', bandwidth_alloc),
        }.get(target_type)
        if expected and not expected[1]:
            raise ValidationError({f'{expected[0]}_id': '请先选择与工单类型匹配的资源'})
        if target_type == ServiceInstance.ServiceType.IP_ADDRESS and not data.get('ip_ids'):
            raise ValidationError({'ip_ids': 'IP 资源工单至少要选择一个可用 IP'})
        if vps and target_type != ServiceInstance.ServiceType.VPS:
            raise ValidationError({'vps_id': '当前工单类型不能绑定 VPS'})
        if edge and target_type != ServiceInstance.ServiceType.EDGE_ACCELERATION:
            raise ValidationError({'edge_id': '当前工单类型不能绑定边缘加速资源'})

        def validate_resource(resource, field):
            if not resource:
                return
            if resource.customer_id not in (None, order.customer_id):
                raise ValidationError({field: '资源已属于其他客户'})
            if order.preferred_pop_id and resource.pop_point_id != order.preferred_pop_id:
                raise ValidationError({field: '资源所属 POP 与工单期望 POP 不一致'})

        validate_resource(vps, 'vps_id')
        validate_resource(edge, 'edge_id')
        if edge and data.get('activate', True) and not edge.node_pushed:
            raise ValidationError({
                'edge_id': '边缘加速配置尚未成功下发到节点，请先完成节点下发，或选择暂不激活',
            })
        if vps and ServiceInstance.objects.select_for_update().filter(vps=vps).exists():
            raise ValidationError({'vps_id': '该 VPS 已绑定到其他服务实例'})
        if edge and ServiceInstance.objects.select_for_update().filter(edge=edge).exists():
            raise ValidationError({'edge_id': '该边缘加速资源已绑定到其他服务实例'})
        if bandwidth_alloc:
            if order.preferred_pop_id and bandwidth_alloc.pop_point_id != order.preferred_pop_id:
                raise ValidationError({'bandwidth_allocation_id': '带宽分配所属 POP 与工单期望 POP 不一致'})
            bandwidth_target = bandwidth_alloc.vps or bandwidth_alloc.edge
            if not bandwidth_target:
                raise ValidationError({'bandwidth_allocation_id': '带宽分配必须已绑定 VPS 或边缘加速目标'})
            validate_resource(bandwidth_target, 'bandwidth_allocation_id')
            if ServiceInstance.objects.select_for_update().filter(
                bandwidth_alloc=bandwidth_alloc,
            ).exists():
                raise ValidationError({'bandwidth_allocation_id': '该带宽分配已绑定到其他服务实例'})
            if vps and bandwidth_alloc.vps_id != vps.id:
                raise ValidationError({'bandwidth_allocation_id': '所选带宽分配不属于当前 VPS'})
            if edge and bandwidth_alloc.edge_id != edge.id:
                raise ValidationError({'bandwidth_allocation_id': '所选带宽分配不属于当前边缘资源'})
            if target_type == ServiceInstance.ServiceType.BANDWIDTH and not bandwidth_target.customer_id:
                bandwidth_target.customer = order.customer
                bandwidth_target.save(update_fields=['customer', 'updated_at'])

        ip_ids = list(dict.fromkeys(data.get('ip_ids') or []))
        ips = list(
            IPAddress.objects.select_for_update()
            .filter(pk__in=ip_ids)
            .select_related('ip_block__pop_point')
            .order_by('address')
        )
        if len(ips) != len(ip_ids):
            raise ValidationError({'ip_ids': '包含不存在的 IP 地址'})
        for ip in ips:
            if ip.status != IPAddress.IPStatus.AVAILABLE:
                raise ValidationError({'ip_ids': f'IP {ip.address} 当前不是可分配状态'})
            if order.preferred_pop_id and ip.ip_block.pop_point_id != order.preferred_pop_id:
                raise ValidationError({'ip_ids': f'IP {ip.address} 不属于工单期望 POP'})

        before_state = {
            'order_status': order.status,
            'resource_ids': {
                'vps': vps.id if vps else None,
                'edge': edge.id if edge else None,
                'bandwidth_allocation': bandwidth_alloc.id if bandwidth_alloc else None,
                'ip_ids': ip_ids,
            },
        }
        instance = ServiceInstance.objects.create(
            service_order=order,
            service_type=target_type,
            status=ServiceInstance.InstanceStatus.PROVISIONING,
            vps=vps,
            edge=edge,
            bandwidth_alloc=bandwidth_alloc,
            spec_snapshot=build_spec(order),
            is_trial=data.get('is_trial', False),
            trial_start=timezone.now().date() if data.get('is_trial', False) else None,
            provisioned_at=timezone.now(),
            provisioned_by=request.user,
            remark=data.get('remark', ''),
        )
        if ips:
            instance.ip_addresses.set(ips)
            if not data.get('activate', True):
                for ip in ips:
                    ip.status = IPAddress.IPStatus.RESERVED
                    ip.assigned_at = timezone.now()
                    ip.save(update_fields=['status', 'assigned_at'])

        if not order.handler_id:
            order.handler = request.user
            order.assigned_at = timezone.now()
            order.save(update_fields=['handler', 'assigned_at', 'updated_at'])

        if data.get('activate', True):
            instance.activate(operator=request.user)
        else:
            sync_service_order_delivery_state(
                order,
                operator=request.user,
                reason=f'实例 {instance.instance_no} 已创建，等待激活',
            )

        after_state = service_snapshot(instance)
        ServiceAllocation.objects.create(
            service_instance=instance,
            action_type=ServiceAllocation.ActionType.ALLOCATE,
            description=f'人工交付工单 {order.order_no} 的 {instance.get_service_type_display()} 资源',
            before_state=before_state,
            after_state=after_state,
            resource_type=(
                'VPS' if vps else 'EdgeAcceleration' if edge
                else 'BandwidthAllocation' if bandwidth_alloc else 'IPAddress'
            ),
            resource_id=(vps.id if vps else edge.id if edge else bandwidth_alloc.id if bandwidth_alloc else None),
            resource_repr=', '.join(str(ip.address) for ip in ips) or instance.instance_no,
            operator=request.user,
            remark=data.get('remark', ''),
        )
        self._log(
            request,
            ServiceOperationLog.ActionType.PROVISION,
            f'人工交付工单 [{order.order_no}]，创建实例 [{instance.instance_no}]',
            service_order=order,
            service_instance=instance,
            old_value=before_state,
            new_value=after_state,
        )

        order = sync_service_order_delivery_state(
            order,
            operator=request.user,
            reason=f'人工交付实例 {instance.instance_no}',
        )
        mark_requirement_stage(order, handled_by=request.user, approved_by=order.approved_by)
        if data.get('activate', True):
            if instance.is_trial:
                mark_test_stage(order, instance, handled_by=request.user)
            else:
                mark_usage_stage(order, instance, handled_by=request.user)
        notify_instance_event(
            instance,
            '人工资源交付完成' if data.get('activate', True) else '人工资源已预留',
            detail=ServiceAllocation.objects.filter(service_instance=instance)
            .order_by('-created_at').first().description,
        )
        notify_order_event(
            order,
            '工单资源交付同步',
            detail=f'已交付实例 {instance.instance_no}，当前状态：{order.get_status_display()}',
        )
        from service_manager.capacity_reservation import consume_reservation
        from .manual_delivery import mark_admin_deliver
        consume_reservation(order, spec=build_spec(order))
        mark_admin_deliver(order)
        return Response({
            'detail': '资源已交付并同步更新服务工单',
            'order': ServiceOrderDetailSerializer(order, context={'request': request}).data,
            'instance': ServiceInstanceDetailSerializer(instance, context={'request': request}).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        """获取工单操作日志"""
        order = self.get_object()
        logs = order.operation_logs.order_by('-created_at')
        serializer = ServiceOperationLogSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='lifecycle')
    def lifecycle(self, request, pk=None):
        """获取工单全生命周期阶段时间线"""
        order = self.get_object()
        records = order.lifecycle_records.select_related(
            'submitted_by', 'handled_by', 'approved_by',
            'service_instance', 'change_record',
        ).order_by('created_at', 'id')
        return Response(ServiceLifecycleRecordSerializer(records, many=True).data)

    @action(detail=True, methods=['get'], url_path='changes')
    def changes(self, request, pk=None):
        """服务单关联的全部变更：直接关联工单或其服务实例。"""
        order = self.get_object()
        records = (
            ServiceChangeRecord.objects.filter(
                models.Q(related_order=order) | models.Q(service_instance__service_order=order)
            )
            .select_related('service_instance', 'requested_by', 'approved_by', 'executed_by')
            .distinct()
            .order_by('-created_at')
        )
        return Response(ServiceChangeRecordListSerializer(records, many=True).data)

    @action(detail=True, methods=['get'], url_path='snapshot')
    def snapshot(self, request, pk=None):
        """服务单当前资源快照，供实例 Dashboard 与变更前审计使用。"""
        from .snapshot import build_order_snapshot
        return Response(build_order_snapshot(self.get_object()))

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """工单统计汇总（支持 customer / start / end）"""
        from django.db.models import Count, Sum
        qs = self.get_queryset()
        by_status = dict(qs.values_list('status').annotate(cnt=Count('id')).order_by())
        by_type = dict(qs.values_list('order_type').annotate(cnt=Count('id')).order_by())
        by_stage = dict(qs.values_list('current_stage').annotate(cnt=Count('id')).order_by())
        total_quoted = qs.aggregate(t=Sum('quoted_price'))['t'] or 0
        return Response({
            'total': qs.count(),
            'by_status': by_status,
            'by_order_type': by_type,
            'by_stage': by_stage,
            'total_quoted_price': float(total_quoted),
            'filters': {
                'customer': request.query_params.get('customer'),
                'start': request.query_params.get('start') or request.query_params.get('date_from'),
                'end': request.query_params.get('end') or request.query_params.get('date_to'),
            },
        })

    @action(detail=False, methods=['get'], url_path='overview')
    def overview(self, request):
        """服务全景统计：客户筛选 + 时间段 + 资费汇总"""
        from .stats import service_overview
        data = service_overview(
            customer_id=request.query_params.get('customer') or request.query_params.get('customer_id'),
            start=request.query_params.get('start') or request.query_params.get('date_from'),
            end=request.query_params.get('end') or request.query_params.get('date_to'),
        )
        return Response(data)


# ═══════════════════════════════════════════════════════════════
# § 官网咨询与测试申请
# ═══════════════════════════════════════════════════════════════

class ServiceInquiryViewSet(ServiceModulePermissionMixin, viewsets.ReadOnlyModelViewSet):
    """官网咨询与测试申请的服务侧处理队列。"""

    queryset = ServiceInquiry.objects.select_related(
        'customer', 'submitted_by', 'related_order', 'related_ticket',
        'handler', 'replied_by',
    ).prefetch_related(
        'reply_records__operator',
    ).all()
    serializer_class = ServiceInquirySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['inquiry_type', 'status', 'handler']
    search_fields = ['inquiry_no', 'contact_name', 'company_name', 'phone', 'email', 'requirements']
    ordering_fields = ['created_at', 'updated_at', 'replied_at']
    ordering = ['-created_at']
    extra_read_actions = frozenset()

    @action(detail=True, methods=['post'], url_path='assign')
    @transaction.atomic
    def assign(self, request, pk=None):
        inquiry = self.get_object()
        handler_id = request.data.get('handler')
        if not handler_id:
            return Response({'detail': '请选择处理人'}, status=status.HTTP_400_BAD_REQUEST)
        from django.contrib.auth.models import User
        handler = User.objects.filter(pk=handler_id, is_active=True).first()
        if not handler:
            return Response({'detail': '处理人不存在或已停用'}, status=status.HTTP_400_BAD_REQUEST)
        inquiry.handler = handler
        if inquiry.status == ServiceInquiry.InquiryStatus.NEW:
            inquiry.status = ServiceInquiry.InquiryStatus.PROCESSING
        inquiry.save(update_fields=['handler', 'status', 'updated_at'])
        ServiceInquiryReply.objects.create(
            inquiry=inquiry,
            entry_type=ServiceInquiryReply.EntryType.ASSIGN,
            content=f'咨询已指派给：{handler.get_full_name() or handler.username}',
            status_after=inquiry.status,
            operator=request.user,
        )
        from .notify_helpers import notify_inquiry_update_event
        notify_inquiry_update_event(
            inquiry,
            '指派处理人',
            detail=f'已指派给：{handler.get_full_name() or handler.username}',
        )
        inquiry.refresh_from_db()
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='reply')
    @transaction.atomic
    def reply(self, request, pk=None):
        inquiry = self.get_object()
        serializer = InquiryReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply_content = serializer.validated_data['reply_content']
        entry_type = serializer.validated_data['entry_type']
        inquiry.reply_content = reply_content
        inquiry.status = serializer.validated_data['status']
        inquiry.replied_by = request.user
        inquiry.replied_at = timezone.now()
        if not inquiry.handler:
            inquiry.handler = request.user
        inquiry.save(update_fields=[
            'reply_content', 'status', 'replied_by', 'replied_at', 'handler', 'updated_at',
        ])
        ServiceInquiryReply.objects.create(
            inquiry=inquiry,
            entry_type=entry_type,
            content=reply_content,
            status_after=inquiry.status,
            operator=request.user,
        )
        from .notify_helpers import notify_inquiry_update_event
        notify_inquiry_update_event(
            inquiry,
            (
                '关闭需求' if inquiry.status == ServiceInquiry.InquiryStatus.CLOSED
                else '记录内部跟进' if entry_type == ServiceInquiryReply.EntryType.NOTE
                else '回复需求'
            ),
            detail=reply_content,
        )
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='convert-to-order')
    @transaction.atomic
    def convert_to_order(self, request, pk=None):
        """将已确认的官网需求转为可审批、可交付的正式服务工单。"""
        inquiry = ServiceInquiry.objects.select_for_update().select_related(
            'customer', 'handler', 'related_order',
        ).get(pk=pk)
        if inquiry.related_order_id:
            return Response(
                {
                    'detail': '该咨询已转为服务工单',
                    'order_id': inquiry.related_order_id,
                    'order_no': inquiry.related_order.order_no,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = InquiryConvertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        customer = data.get('customer') or inquiry.customer
        if customer is None:
            raise ValidationError({'customer': '该咨询尚未关联客户，请先选择对应客户'})

        product = data['product']
        from product_manager.billing_mode import billing_profile
        from product_manager.models import ProductCategory
        from service_manager.fx_snapshot import build_fx_fields

        if product.category == ProductCategory.VPS:
            business_type = ServiceOrder.BusinessType.VPS
        else:
            from product_manager.accel import default_edge_business_type
            mapped = default_edge_business_type(product.accel_type)
            business_type = (
                ServiceOrder.BusinessType.EDGE_WG
                if mapped == 'edge_wg'
                else ServiceOrder.BusinessType.EDGE_GRE
            )

        preferred_pop = data.get('preferred_pop') or product.pop_point
        quoted_price = data.get('quoted_price')
        if quoted_price is None:
            quoted_price = product.monthly_price
        handler = data.get('handler') or inquiry.handler or request.user
        request_snapshot = {
            'source': 'website_inquiry',
            'inquiry': {
                'id': inquiry.pk,
                'inquiry_no': inquiry.inquiry_no,
                'inquiry_type': inquiry.inquiry_type,
                'contact_name': inquiry.contact_name,
                'company_name': inquiry.company_name,
                'phone': inquiry.phone,
                'email': inquiry.email,
                'preferred_contact': inquiry.preferred_contact,
                'requirements': inquiry.requirements,
                'source_url': inquiry.source_url,
                'submitted_at': inquiry.created_at.isoformat() if inquiry.created_at else '',
            },
            'product': {
                'id': product.pk,
                'product_no': product.product_no,
                'name': product.name,
                'category': product.category,
                'accel_type': product.accel_type,
                'monthly_price': str(product.monthly_price),
                'currency': product.currency,
                **billing_profile(product),
            },
        }
        order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=business_type,
            status=ServiceOrder.OrderStatus.PENDING,
            source=ServiceOrder.OrderSource.INQUIRY,
            customer=customer,
            product=product,
            preferred_pop=preferred_pop,
            handler=handler,
            requested_vcpu=product.cpu_cores,
            requested_ram_mb=product.ram_mb,
            requested_disk_gb=product.disk_gb,
            requested_bandwidth_mbps=product.bandwidth_mbps,
            requested_ip_count=product.ip_count,
            requested_quantity=data.get('requested_quantity', 1),
            requirements=(
                f'来源官网咨询 {inquiry.inquiry_no}\n'
                f'{inquiry.requirements}'
            ),
            request_snapshot=request_snapshot,
            quoted_price=quoted_price,
            billing_cycle=ServiceOrder.BillingCycle.MONTHLY,
            created_by=request.user,
            **build_fx_fields(quote_currency=product.currency),
        )
        from service_manager.capacity_reservation import (
            CapacityReservationError, reserve_for_order,
        )
        try:
            reserve_for_order(order)
        except CapacityReservationError as exc:
            raise ValidationError({
                'preferred_pop': str(exc),
                'capacity_shortages': exc.shortages,
            }) from exc
        inquiry.customer = customer
        inquiry.related_order = order
        inquiry.converted_at = timezone.now()
        inquiry.status = ServiceInquiry.InquiryStatus.PROCESSING
        if not inquiry.handler:
            inquiry.handler = handler
        inquiry.save(update_fields=[
            'customer', 'related_order', 'converted_at', 'status', 'handler', 'updated_at',
        ])
        ServiceInquiryReply.objects.create(
            inquiry=inquiry,
            entry_type=ServiceInquiryReply.EntryType.CONVERT,
            content=f'已转为服务工单：{order.order_no}',
            status_after=inquiry.status,
            operator=request.user,
        )
        _log_service(
            operator=request.user,
            action_type=ServiceOperationLog.ActionType.CREATE_ORDER,
            description=f'官网咨询 [{inquiry.inquiry_no}] 转为服务工单 [{order.order_no}]',
            service_order=order,
            request=request,
            new_value={
                'source_inquiry_no': inquiry.inquiry_no,
                'customer_id': customer.pk,
                'product_id': product.pk,
            },
        )
        try:
            from .lifecycle import bootstrap_order_lifecycle
            bootstrap_order_lifecycle(order, submitted_by=request.user)
        except Exception:  # noqa: BLE001
            logger.warning('咨询 %s 转工单后初始化生命周期失败', inquiry.inquiry_no, exc_info=True)
        from .notify_helpers import notify_inquiry_update_event, notify_order_event
        notify_inquiry_update_event(
            inquiry,
            '转服务工单',
            detail=f'已生成待审批服务工单：{order.order_no}',
        )
        notify_order_event(
            order,
            '官网咨询转服务工单',
            detail=f'来源咨询：{inquiry.inquiry_no}',
            event='service_order',
        )
        return Response(
            {
                'detail': '已转为待审批服务工单',
                'order_id': order.pk,
                'order_no': order.order_no,
                'inquiry': self.get_serializer(inquiry).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='convert-to-ticket')
    @transaction.atomic
    def convert_to_ticket(self, request, pk=None):
        """将已绑定客户的官网咨询转为技术工单（与转服务工单独立）。"""
        inquiry = ServiceInquiry.objects.select_for_update().select_related(
            'customer', 'handler', 'related_ticket',
        ).get(pk=pk)
        if SupportTicket.objects.filter(related_inquiry=inquiry).exists():
            ticket = inquiry.related_ticket
            return Response(
                {
                    'detail': '该咨询已转为技术工单',
                    'ticket_id': ticket.pk,
                    'ticket_no': ticket.ticket_no,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if inquiry.customer_id is None:
            raise ValidationError({'customer': '该咨询尚未关联客户，请先绑定客户后再转技术工单'})

        serializer = InquiryConvertTicketSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        handler = data.get('handler') or inquiry.handler or request.user
        from .support_ticket import create_support_ticket
        try:
            ticket = create_support_ticket(
                customer=inquiry.customer,
                subject=f'咨询转入：{inquiry.inquiry_no}',
                description=inquiry.requirements,
                category=data['category'],
                priority=data['priority'],
                source=SupportTicket.Source.INQUIRY,
                created_by=request.user,
                handler=handler,
                related_inquiry=inquiry,
            )
        except DjangoValidationError as exc:
            raise ValidationError(getattr(exc, 'message_dict', None) or exc.messages)

        if inquiry.status == ServiceInquiry.InquiryStatus.NEW:
            inquiry.status = ServiceInquiry.InquiryStatus.PROCESSING
        if not inquiry.handler:
            inquiry.handler = handler
        inquiry.save(update_fields=['status', 'handler', 'updated_at'])
        ServiceInquiryReply.objects.create(
            inquiry=inquiry,
            entry_type=ServiceInquiryReply.EntryType.CONVERT_TICKET,
            content=f'已转为技术工单：{ticket.ticket_no}',
            status_after=inquiry.status,
            operator=request.user,
        )
        from .notify_helpers import notify_inquiry_update_event
        notify_inquiry_update_event(
            inquiry,
            '转技术工单',
            detail=f'已生成技术工单：{ticket.ticket_no}',
        )
        return Response(
            {
                'detail': '已转为技术工单',
                'ticket_id': ticket.pk,
                'ticket_no': ticket.ticket_no,
                'inquiry': self.get_serializer(self.get_object()).data,
            },
            status=status.HTTP_201_CREATED,
        )


def _ticket_validation(exc: DjangoValidationError):
    if getattr(exc, 'message_dict', None):
        raise ValidationError(exc.message_dict)
    raise ValidationError(list(exc.messages) if getattr(exc, 'messages', None) else str(exc))


class SupportTicketViewSet(
    CustomerScopedQuerysetMixin,
    ModulePermissionMixin,
    viewsets.ModelViewSet,
):
    """客户技术工单（与服务开通工单分离），含 SLA 与跟进。"""

    http_method_names = ['get', 'post', 'head', 'options']
    view_permission = page_permission('service', 'ticket', 'view')
    manage_permission = page_permission('service', 'ticket', 'manage')
    extra_read_actions = frozenset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['category', 'priority', 'status', 'handler', 'customer', 'source']
    search_fields = ['ticket_no', 'subject', 'description']
    ordering_fields = [
        'created_at', 'updated_at', 'first_response_due', 'resolve_due', 'priority',
    ]
    ordering = ['-created_at']

    def get_queryset(self):
        qs = SupportTicket.objects.select_related(
            'customer', 'handler', 'created_by', 'related_instance', 'related_inquiry',
        ).annotate(message_count=Count('messages'))
        if self.action == 'retrieve':
            qs = qs.prefetch_related('messages__author')
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return SupportTicketListSerializer
        if self.action == 'create':
            return SupportTicketWriteSerializer
        return SupportTicketSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from .support_ticket import create_support_ticket
        try:
            ticket = create_support_ticket(
                customer=data['customer'],
                subject=data['subject'],
                description=data['description'],
                category=data.get('category', SupportTicket.Category.TECHNICAL),
                priority=data.get('priority', SupportTicket.Priority.P3),
                source=SupportTicket.Source.ADMIN,
                created_by=request.user,
                handler=data.get('handler'),
                related_instance=data.get('related_instance'),
            )
        except DjangoValidationError as exc:
            _ticket_validation(exc)
        ticket = self.get_queryset().get(pk=ticket.pk)
        return Response(
            SupportTicketSerializer(ticket).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='assign')
    def assign(self, request, pk=None):
        ticket = self.get_object()
        serializer = SupportTicketAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .support_ticket import assign_ticket
        assign_ticket(ticket, serializer.validated_data['handler'])
        return Response(SupportTicketSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='reply')
    def reply(self, request, pk=None):
        ticket = self.get_object()
        serializer = SupportTicketReplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from .support_ticket import add_staff_message
        try:
            add_staff_message(
                ticket,
                data['content'],
                author=request.user,
                is_internal=data.get('is_internal', False),
                status=data.get('status'),
            )
        except DjangoValidationError as exc:
            _ticket_validation(exc)
        return Response(SupportTicketSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='resolve')
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        serializer = SupportTicketCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .support_ticket import resolve_ticket
        try:
            resolve_ticket(
                ticket,
                author=request.user,
                content=serializer.validated_data.get('content') or '',
            )
        except DjangoValidationError as exc:
            _ticket_validation(exc)
        return Response(SupportTicketSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        ticket = self.get_object()
        serializer = SupportTicketCloseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .support_ticket import close_ticket
        try:
            close_ticket(
                ticket,
                author=request.user,
                content=serializer.validated_data.get('content') or '',
            )
        except DjangoValidationError as exc:
            _ticket_validation(exc)
        return Response(SupportTicketSerializer(self.get_object()).data)

    @action(detail=True, methods=['post'], url_path='escalate')
    def escalate(self, request, pk=None):
        ticket = self.get_object()
        from .support_ticket import escalate_ticket
        try:
            escalate_ticket(ticket)
        except DjangoValidationError as exc:
            _ticket_validation(exc)
        return Response(SupportTicketSerializer(self.get_object()).data)


# ═══════════════════════════════════════════════════════════════
# § 3  服务实例
# ═══════════════════════════════════════════════════════════════

class ServiceInstanceViewSet(
    CustomerScopedQuerysetMixin,
    ServiceModulePermissionMixin,
    ServiceLogMixin,
    viewsets.ModelViewSet,
):
    """
    服务实例完整 CRUD + 生命周期操作
    custom actions:
      activate   POST /instances/{id}/activate/
      suspend    POST /instances/{id}/suspend/
      resume     POST /instances/{id}/resume/
      terminate  POST /instances/{id}/terminate/
      assign_ips POST /instances/{id}/assign_ips/    绑定 IP
      release_ips POST /instances/{id}/release_ips/  移除 IP
      logs       GET  /instances/{id}/logs/
      allocations GET /instances/{id}/allocations/   分配记录
      statistics GET /instances/statistics/
    """
    customer_scope_field = 'service_order__customer'
    extra_read_actions = frozenset({'logs', 'statistics', 'allocations', 'fee_summary'})

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'service_type', 'service_order', 'service_order__customer', 'is_trial']
    search_fields = [
        'instance_no',
        'service_order__order_no',
        'service_order__customer__name',
        'vps__vps_no', 'edge__edge_no',
    ]
    ordering_fields = ['created_at', 'activated_at', 'expire_time']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ServiceInstance.objects.select_related(
            'service_order__customer',
            'vps', 'edge', 'bandwidth_alloc',
            'provisioned_by', 'terminated_by',
        ).prefetch_related('ip_addresses')
        start = self.request.query_params.get('start') or self.request.query_params.get('date_from')
        end = self.request.query_params.get('end') or self.request.query_params.get('date_to')
        if start or end:
            from .stats import apply_time_filters
            qs = apply_time_filters(qs, start=start, end=end)
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return ServiceInstanceListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ServiceInstanceWriteSerializer
        if self.action in ['assign_ips', 'release_ips']:
            return IPManageSerializer
        if self.action in ['activate', 'suspend', 'resume', 'terminate']:
            return StatusChangeSerializer
        return ServiceInstanceDetailSerializer

    def perform_create(self, serializer):
        order = serializer.validated_data['service_order']
        payment_billing = getattr(order, 'payment_billing', None)
        if payment_billing and payment_billing.status != payment_billing.BillingStatus.PAID:
            raise ValidationError({'service_order': '该工单的预付账单尚未付清，不能创建服务实例'})
        if order.quoted_price and order.quoted_price > 0 and payment_billing is None:
            raise ValidationError({'service_order': '该工单缺少预付账单，不能创建服务实例'})
        obj = serializer.save(
            provisioned_by=self.request.user,
            status=ServiceInstance.InstanceStatus.PROVISIONING,
        )
        sync_service_order_delivery_state(
            obj.service_order,
            operator=self.request.user,
            reason=f'实例 {obj.instance_no} 已创建，等待激活',
        )
        self._log(
            self.request,
            ServiceOperationLog.ActionType.PROVISION,
            f'创建服务实例 [{obj.instance_no}] 类型: {obj.get_service_type_display()}',
            service_order=obj.service_order,
            service_instance=obj,
        )
        from .notify_helpers import notify_instance_event
        notify_instance_event(obj, '服务实例已创建', event='service_provision')

    def perform_update(self, serializer):
        old = service_snapshot(serializer.instance)
        obj = serializer.save()
        sync_service_order_delivery_state(
            obj.service_order,
            operator=self.request.user,
            reason=f'实例 {obj.instance_no} 信息已更新',
        )
        self._log(
            self.request,
            ServiceOperationLog.ActionType.OTHER,
            f'更新服务实例 [{obj.instance_no}]',
            service_order=obj.service_order,
            service_instance=obj,
            old_value=old,
            new_value=service_snapshot(obj),
        )
        from .notify_helpers import notify_instance_event
        notify_instance_event(obj, '服务实例信息已更新')

    def perform_destroy(self, instance):
        if instance.status not in ['terminated', 'error']:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('只能删除已终止或故障状态的实例，请先终止服务')
        no = instance.instance_no
        order = instance.service_order
        instance.delete()
        sync_service_order_delivery_state(
            order,
            operator=self.request.user,
            reason=f'实例 {no} 记录已删除',
        )
        self._log(self.request, ServiceOperationLog.ActionType.OTHER, f'删除实例记录 [{no}]')
        from .notify_helpers import notify_instance_event
        notify_instance_event(instance, '服务实例已删除')

    # ── 生命周期动作 ──────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        """激活服务实例（同步更新资源）"""
        instance = self.get_object()
        if instance.status not in ['provisioning', 'suspended']:
            return Response({'detail': f'当前状态 [{instance.get_status_display()}] 不允许激活'},
                            status=status.HTTP_400_BAD_REQUEST)
        payment_billing = getattr(instance.service_order, 'payment_billing', None)
        if payment_billing and payment_billing.status != payment_billing.BillingStatus.PAID:
            return Response(
                {'detail': '该工单的预付账单尚未付清，不能激活服务实例'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            instance.service_order.quoted_price
            and instance.service_order.quoted_price > 0
            and payment_billing is None
        ):
            return Response(
                {'detail': '该工单缺少预付账单，不能激活服务实例'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.edge and not instance.edge.node_pushed:
            return Response(
                {'detail': '边缘加速配置尚未成功下发到节点，不能激活服务实例'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.activate(operator=request.user)
        try:
            from .lifecycle import mark_test_stage, mark_usage_stage
            from .notify_helpers import notify_instance_event
            from .hooks import dispatch_provision_hooks
            if instance.is_trial:
                mark_test_stage(instance.service_order, instance, handled_by=request.user)
            else:
                mark_usage_stage(instance.service_order, instance, handled_by=request.user)
            notify_instance_event(instance, '服务已激活')
            dispatch_provision_hooks('provision', order=instance.service_order, instance=instance)
        except Exception:  # noqa: BLE001
            logger.warning('实例 [%s] 激活后置处理失败', instance.instance_no, exc_info=True)
        return Response(ServiceInstanceDetailSerializer(instance, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='suspend')
    def suspend(self, request, pk=None):
        """暂停服务实例"""
        instance = self.get_object()
        if instance.status != 'active':
            return Response({'detail': '只有运行中的实例才能暂停'},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance.suspend(operator=request.user, reason=serializer.validated_data.get('reason', ''))
        from .notify_helpers import notify_instance_event
        from .hooks import dispatch_provision_hooks
        notify_instance_event(instance, '服务已暂停', detail=serializer.validated_data.get('reason', ''))
        dispatch_provision_hooks('suspend', order=instance.service_order, instance=instance)
        return Response(ServiceInstanceDetailSerializer(instance, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='resume')
    def resume(self, request, pk=None):
        """恢复已暂停的实例"""
        instance = self.get_object()
        if instance.status != 'suspended':
            return Response({'detail': '只有已暂停的实例才能恢复'},
                            status=status.HTTP_400_BAD_REQUEST)
        instance.resume(operator=request.user)
        from .notify_helpers import notify_instance_event
        from .hooks import dispatch_provision_hooks
        notify_instance_event(instance, '服务已恢复')
        dispatch_provision_hooks('resume', order=instance.service_order, instance=instance)
        return Response(ServiceInstanceDetailSerializer(instance, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='terminate')
    def terminate(self, request, pk=None):
        """终止服务实例（释放全部资源）"""
        instance = self.get_object()
        if instance.status == 'terminated':
            return Response({'detail': '实例已终止'}, status=status.HTTP_400_BAD_REQUEST)
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('reason', '')
        instance.terminate(operator=request.user, reason=reason)
        try:
            from .lifecycle import mark_termination_stage
            from .notify_helpers import notify_instance_event
            from .hooks import dispatch_provision_hooks
            mark_termination_stage(instance.service_order, instance, handled_by=request.user, detail=reason)
            order = sync_service_order_delivery_state(
                instance.service_order,
                operator=request.user,
                reason=f'实例 {instance.instance_no} 已终止',
            )
            notify_instance_event(instance, '服务已终止', detail=reason)
            dispatch_provision_hooks('terminate', order=order, instance=instance)
        except Exception:  # noqa: BLE001
            logger.warning('实例 [%s] 终止后置处理失败', instance.instance_no, exc_info=True)
        return Response(ServiceInstanceDetailSerializer(instance, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='convert_to_billing')
    def convert_to_billing(self, request, pk=None):
        """测试服务转入正式计费周期"""
        instance = self.get_object()
        if not instance.is_trial:
            return Response({'detail': '该实例不是测试服务，无需转入'},
                            status=status.HTTP_400_BAD_REQUEST)
        billing_start = request.data.get('billing_start') or None
        if billing_start:
            from datetime import date
            try:
                billing_start = date.fromisoformat(billing_start)
            except (TypeError, ValueError):
                return Response({'detail': '计费起始日格式应为 YYYY-MM-DD'},
                                status=status.HTTP_400_BAD_REQUEST)
        instance.convert_to_billing(operator=request.user, billing_start=billing_start)
        from .lifecycle import mark_usage_stage
        from .notify_helpers import notify_instance_event
        mark_usage_stage(instance.service_order, instance, handled_by=request.user)
        notify_instance_event(instance, '测试转正计费', detail=f'计费起始 {instance.billing_start_date}')
        return Response(ServiceInstanceDetailSerializer(instance, context={'request': request}).data)

    # ── IP 管理动作 ───────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='assign_ips')
    @transaction.atomic
    def assign_ips(self, request, pk=None):
        """给实例分配 IP 地址"""
        from resource_manager.models import IPAddress
        instance = self.get_object()
        if instance.status not in ['provisioning', 'active']:
            return Response({'detail': '只有开通中或运行中的实例才能分配 IP'},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = IPManageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ip_ids = list(dict.fromkeys(serializer.validated_data['ip_ids']))
        ips = IPAddress.objects.select_for_update().filter(id__in=ip_ids).order_by('address')
        if ips.count() != len(ip_ids):
            return Response({'detail': '请求中包含不存在的 IP'},
                            status=status.HTTP_400_BAD_REQUEST)
        unavailable = list(ips.exclude(status=IPAddress.IPStatus.AVAILABLE).values_list('address', flat=True))
        if unavailable:
            return Response(
                {'detail': f'以下 IP 当前不可分配：{", ".join(unavailable)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.service_order.preferred_pop_id:
            out_of_pop = list(
                ips.exclude(ip_block__pop_point_id=instance.service_order.preferred_pop_id)
                .values_list('address', flat=True)
            )
            if out_of_pop:
                return Response(
                    {'detail': f'以下 IP 不属于工单期望 POP：{", ".join(out_of_pop)}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        now = timezone.now()
        for ip in ips:
            ip.status = 'assigned'
            ip.assigned_at = now
            if instance.vps:
                ip.assigned_vps = instance.vps
            if instance.edge:
                ip.assigned_edge = instance.edge
            ip.save(update_fields=['status', 'assigned_at', 'assigned_vps', 'assigned_edge'])
            instance.ip_addresses.add(ip)

        ServiceAllocation.objects.create(
            service_instance=instance,
            action_type=ServiceAllocation.ActionType.IP_ADD,
            description=f'为实例 {instance.instance_no} 分配 {ips.count()} 个 IP',
            resource_type='IPAddress',
            resource_repr=', '.join(ips.values_list('address', flat=True)),
            operator=request.user,
        )
        self._log(request, ServiceOperationLog.ActionType.ASSIGN_IP,
                  f'为实例 [{instance.instance_no}] 分配 IP: {", ".join(ips.values_list("address", flat=True))}',
                  service_order=instance.service_order, service_instance=instance)
        from .notify_helpers import notify_instance_event
        notify_instance_event(
            instance,
            '服务 IP 已分配',
            detail=f'已分配：{", ".join(ips.values_list("address", flat=True))}',
        )
        return Response({'detail': f'成功分配 {ips.count()} 个 IP',
                         'ip_count': instance.ip_addresses.count()})

    @action(detail=True, methods=['post'], url_path='release_ips')
    @transaction.atomic
    def release_ips(self, request, pk=None):
        """从实例移除（释放）IP 地址"""
        from resource_manager.models import IPAddress
        instance = self.get_object()
        serializer = IPManageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ip_ids = list(dict.fromkeys(serializer.validated_data['ip_ids']))
        ips = instance.ip_addresses.select_for_update().filter(id__in=ip_ids)
        if ips.count() != len(ip_ids):
            return Response({'detail': '未找到关联的 IP'}, status=status.HTTP_400_BAD_REQUEST)
        addrs = list(ips.values_list('address', flat=True))
        for ip in ips:
            ip.status = 'available'
            ip.assigned_at = None
            ip.assigned_vps = None
            ip.assigned_edge = None
            ip.save(update_fields=['status', 'assigned_at', 'assigned_vps', 'assigned_edge'])
        instance.ip_addresses.remove(*ips)

        ServiceAllocation.objects.create(
            service_instance=instance,
            action_type=ServiceAllocation.ActionType.IP_REMOVE,
            description=f'从实例 {instance.instance_no} 移除 {len(addrs)} 个 IP',
            resource_type='IPAddress',
            resource_repr=', '.join(addrs),
            operator=request.user,
        )
        self._log(request, ServiceOperationLog.ActionType.RELEASE_IP,
                  f'从实例 [{instance.instance_no}] 释放 IP: {", ".join(addrs)}',
                  service_order=instance.service_order, service_instance=instance)
        from .notify_helpers import notify_instance_event
        notify_instance_event(
            instance,
            '服务 IP 已释放',
            detail=f'已释放：{", ".join(addrs)}',
        )
        return Response({'detail': f'已释放 {len(addrs)} 个 IP',
                         'ip_count': instance.ip_addresses.count()})

    # ── 查询动作 ─────────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        """获取实例操作日志"""
        instance = self.get_object()
        logs = instance.operation_logs.order_by('-created_at')
        return Response(ServiceOperationLogSerializer(logs, many=True).data)

    @action(detail=True, methods=['get'], url_path='allocations')
    def allocations(self, request, pk=None):
        """获取实例资源分配/回收记录"""
        instance = self.get_object()
        allocs = instance.allocations.order_by('-created_at')
        return Response(ServiceAllocationSerializer(allocs, many=True).data)

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """服务实例统计"""
        from django.db.models import Count
        qs = self.get_queryset()
        by_status = dict(qs.values_list('status').annotate(cnt=Count('id')).order_by())
        by_type = dict(qs.values_list('service_type').annotate(cnt=Count('id')).order_by())
        return Response({
            'total': qs.count(),
            'by_status': by_status,
            'by_service_type': by_type,
            'active_count': qs.filter(status='active').count(),
            'trial_count': qs.filter(is_trial=True).exclude(status='terminated').count(),
            'expiring_soon': qs.filter(
                status='active',
                expire_time__isnull=False,
                expire_time__lte=timezone.now() + timezone.timedelta(days=30),
            ).count(),
        })

    @action(detail=False, methods=['get'], url_path='fee_summary')
    def fee_summary(self, request):
        """实例资费汇总（可按客户/时间筛选，供后续计费）"""
        from decimal import Decimal
        qs = self.get_queryset()
        customer = request.query_params.get('service_order__customer') or request.query_params.get('customer')
        if customer:
            qs = qs.filter(service_order__customer_id=customer)
        by_currency = {}
        trial_fee = Decimal('0')
        billing_fee = Decimal('0')
        items = []
        for inst in qs.select_related('service_order__customer')[:500]:
            fee = inst.effective_monthly_fee()
            cur = inst.effective_fee_currency()
            by_currency[cur] = float(Decimal(str(by_currency.get(cur, 0))) + fee)
            if inst.is_trial:
                trial_fee += fee
            else:
                billing_fee += fee
            items.append({
                'instance_no': inst.instance_no,
                'customer': inst.service_order.customer.name,
                'status': inst.status,
                'is_trial': inst.is_trial,
                'monthly_fee': float(fee),
                'currency': cur,
            })
        return Response({
            'count': qs.count(),
            'billing_monthly_fee_total': float(billing_fee),
            'trial_monthly_fee_total': float(trial_fee),
            'by_currency': by_currency,
            'items': items,
        })


# ═══════════════════════════════════════════════════════════════
# § 4  资源分配记录（只读）
# ═══════════════════════════════════════════════════════════════

class ServiceAllocationViewSet(
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    分配/回收记录只读接口
    list    GET /allocations/
    retrieve GET /allocations/{id}/
    """
    queryset = ServiceAllocation.objects.select_related(
        'service_instance__service_order__customer', 'operator'
    )
    view_permission = Perm.SERVICE_VIEW
    customer_scope_field = 'service_instance__service_order__customer'
    serializer_class = ServiceAllocationSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['action_type', 'resource_type', 'service_instance']
    search_fields = ['description', 'resource_repr', 'service_instance__instance_no']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ServiceAllocation.objects.select_related(
            'service_instance__service_order__customer', 'operator'
        )
        return self.apply_customer_scope(qs)


# ═══════════════════════════════════════════════════════════════
# § 5  服务变更记录
# ═══════════════════════════════════════════════════════════════

class ServiceChangeRecordViewSet(
    CustomerScopedQuerysetMixin,
    ServiceModulePermissionMixin,
    ServiceLogMixin,
    viewsets.ModelViewSet,
):
    """
    服务变更申请与记录
    custom actions:
      approve  POST /changes/{change_no}/approve/    审批变更
      execute  POST /changes/{change_no}/execute/    执行变更（落库资源+快照）
      rollback POST /changes/{change_no}/rollback/   基于快照回滚
      cancel   POST /changes/{change_no}/cancel/     取消变更
    """
    customer_scope_field = 'service_instance__service_order__customer'
    lookup_field = 'change_no'
    lookup_url_kwarg = 'change_no'

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'change_type', 'status', 'service_instance',
        'service_instance__service_order__customer',
    ]
    search_fields = [
        'change_no', 'change_reason',
        'service_instance__instance_no',
        'service_instance__service_order__customer__name',
    ]
    ordering_fields = ['created_at', 'scheduled_at']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ServiceChangeRecord.objects.select_related(
            'service_instance__service_order__customer',
            'requested_by', 'approved_by', 'handler', 'executed_by',
            'related_order', 'related_order__payment_billing',
        )
        start = self.request.query_params.get('start') or self.request.query_params.get('date_from')
        end = self.request.query_params.get('end') or self.request.query_params.get('date_to')
        if start or end:
            from .stats import apply_time_filters
            qs = apply_time_filters(qs, start=start, end=end)
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return ServiceChangeRecordListSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ServiceChangeRecordWriteSerializer
        return ServiceChangeRecordDetailSerializer

    def perform_create(self, serializer):
        instance = serializer.validated_data.get('service_instance')
        before = service_snapshot(instance) if instance else {}
        obj = serializer.save(
            requested_by=self.request.user,
            before_spec=serializer.validated_data.get('before_spec') or before,
        )
        self._log(
            self.request,
            ServiceOperationLog.ActionType.CHANGE,
            f'提交变更申请 [{obj.change_no}] 类型: {obj.get_change_type_display()}',
            service_instance=obj.service_instance,
            service_order=obj.service_instance.service_order,
            old_value=obj.before_spec,
            new_value=obj.after_spec,
        )
        try:
            from .lifecycle import mark_change_stage
            from .notify_helpers import notify_change_event
            mark_change_stage(
                obj.service_instance.service_order,
                obj.service_instance,
                obj,
                submitted_by=self.request.user,
                status=ServiceLifecycleRecord.StageStatus.PENDING,
            )
            notify_change_event(obj, '变更申请已提交')
        except Exception:  # noqa: BLE001
            logger.warning('变更 [%s] 创建后置处理失败', obj.change_no, exc_info=True)

    @action(detail=True, methods=['post'], url_path='approve')
    @transaction.atomic
    def approve(self, request, change_no=None):
        """审批变更申请"""
        record = self.get_object()
        if record.status != 'pending':
            return Response({'detail': '只有待审批的变更才能审批'}, status=status.HTTP_400_BAD_REQUEST)
        s = ChangeStatusSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        new_status = s.validated_data['status']
        if new_status not in ['approved', 'rejected']:
            return Response({'detail': '该接口只接受 approved 或 rejected'},
                            status=status.HTTP_400_BAD_REQUEST)
        from .workflow import close_change_related_order, ensure_change_related_order_approved

        if new_status == 'approved':
            ensure_change_related_order_approved(record, operator=request.user)
            record.status = new_status
            record.approved_by = request.user
            record.approved_at = timezone.now()
            record.handler = record.handler or request.user
        else:
            record.reject_reason = s.validated_data.get('reject_reason', '')
            close_change_related_order(
                record,
                reason=record.reject_reason or f'变更 [{record.change_no}] 已驳回',
            )
            record.status = new_status
        record.save()
        record.refresh_from_db()
        self._log(request, ServiceOperationLog.ActionType.CHANGE,
                  f'变更单 [{record.change_no}] {record.get_status_display()}',
                  service_instance=record.service_instance,
                  service_order=record.service_instance.service_order)
        from .lifecycle import mark_change_stage
        from .notify_helpers import notify_change_event
        mark_change_stage(
            record.service_instance.service_order,
            record.service_instance,
            record,
            handled_by=request.user,
            status=(
                ServiceLifecycleRecord.StageStatus.IN_PROGRESS
                if new_status == 'approved'
                else ServiceLifecycleRecord.StageStatus.REJECTED
            ),
        )
        notify_change_event(
            record,
            '变更审批通过' if new_status == 'approved' else '变更已驳回',
            detail=record.reject_reason,
        )
        return Response(ServiceChangeRecordDetailSerializer(record, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='execute')
    def execute(self, request, change_no=None):
        """执行变更：更新资源规格并记录前后快照"""
        from .change_executor import ChangeExecutionError, execute_change
        record = self.get_object()
        try:
            record = execute_change(record, operator=request.user)
        except ChangeExecutionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            logger.exception('变更执行失败 [%s]', record.change_no)
            return Response({'detail': f'变更执行失败: {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ServiceChangeRecordDetailSerializer(record, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, change_no=None):
        """标记变更已完成（兼容旧接口，内部走 execute）"""
        return self.execute(request, change_no=change_no)

    @action(detail=True, methods=['post'], url_path='rollback')
    def rollback(self, request, change_no=None):
        """回滚已完成的、具备变更前快照的非终止类变更。"""
        from .change_executor import ChangeExecutionError, rollback_change
        record = self.get_object()
        try:
            record = rollback_change(record, operator=request.user)
        except ChangeExecutionError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            logger.exception('变更回滚失败 [%s]', record.change_no)
            return Response({'detail': f'变更回滚失败: {exc}'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ServiceChangeRecordDetailSerializer(record, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    @transaction.atomic
    def cancel(self, request, change_no=None):
        """取消变更申请"""
        record = self.get_object()
        if record.status in ['completed', 'cancelled']:
            return Response({'detail': '已完成或已取消的变更不可再次取消'},
                            status=status.HTTP_400_BAD_REQUEST)
        from .workflow import close_change_related_order

        close_change_related_order(
            record,
            reason=f'变更 [{record.change_no}] 已取消',
        )
        record.status = 'cancelled'
        record.save(update_fields=['status', 'updated_at'])
        self._log(request, ServiceOperationLog.ActionType.CHANGE,
                  f'取消变更单 [{record.change_no}]',
                  service_instance=record.service_instance,
                  service_order=record.service_instance.service_order)
        from .notify_helpers import notify_change_event
        notify_change_event(record, '变更已取消')
        return Response(ServiceChangeRecordDetailSerializer(record, context={'request': request}).data)


# ═══════════════════════════════════════════════════════════════
# § 5b  生命周期记录（只读） / 开通钩子
# ═══════════════════════════════════════════════════════════════

class ServiceLifecycleRecordViewSet(
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """生命周期阶段记录只读接口"""
    view_permission = Perm.SERVICE_VIEW
    customer_scope_field = 'service_order__customer'
    serializer_class = ServiceLifecycleRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['stage', 'status', 'service_order', 'service_instance', 'service_order__customer']
    search_fields = ['title', 'detail', 'service_order__order_no', 'service_instance__instance_no']
    ordering = ['created_at', 'id']

    def get_queryset(self):
        qs = ServiceLifecycleRecord.objects.select_related(
            'service_order__customer', 'service_instance', 'change_record',
            'submitted_by', 'handled_by', 'approved_by',
        )
        start = self.request.query_params.get('start') or self.request.query_params.get('date_from')
        end = self.request.query_params.get('end') or self.request.query_params.get('date_to')
        if start or end:
            from .stats import apply_time_filters
            qs = apply_time_filters(qs, start=start, end=end)
        return self.apply_customer_scope(qs)


class ServiceProvisionHookViewSet(ServiceSystemPermissionMixin, viewsets.ModelViewSet):
    """外部开通/对接 API 钩子配置"""
    queryset = ServiceProvisionHook.objects.all()
    serializer_class = ServiceProvisionHookSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['event', 'hook_type', 'is_active']
    search_fields = ['name', 'endpoint_url', 'adapter_key']
    ordering = ['event', 'name']


# ═══════════════════════════════════════════════════════════════
# § 5c  服务统计 / 账单预览
# ═══════════════════════════════════════════════════════════════

class ServiceStatsViewSet(ServiceSystemPermissionMixin, viewsets.GenericViewSet):
    """标准服务统计接口：按客户、POP 与服务类型聚合。"""
    extra_read_actions = frozenset({'by_customer', 'by_pop', 'by_type'})

    def _filters(self, request):
        return {
            'start': request.query_params.get('start') or request.query_params.get('date_from'),
            'end': request.query_params.get('end') or request.query_params.get('date_to'),
        }

    @action(detail=False, methods=['get'], url_path='by-customer')
    def by_customer(self, request):
        from .stats import stats_by_customer
        return Response(stats_by_customer(**self._filters(request)))

    @action(detail=False, methods=['get'], url_path='by-pop')
    def by_pop(self, request):
        from .stats import stats_by_pop
        return Response(stats_by_pop(**self._filters(request)))

    @action(detail=False, methods=['get'], url_path='by-type')
    def by_type(self, request):
        from .stats import stats_by_type
        return Response(stats_by_type(**self._filters(request)))


class ServiceBillingViewSet(ServiceSystemPermissionMixin, viewsets.GenericViewSet):
    """服务管理侧的只读账单预览，复用客户计费引擎且不写入账单。"""
    extra_read_actions = frozenset({'preview'})

    @action(detail=False, methods=['get'], url_path='preview')
    def preview(self, request):
        from customer_manager.models import Customer
        from customer_manager.billing_engine import compute_customer_month

        customer_id = request.query_params.get('customer')
        bill_month = request.query_params.get('bill_month') or timezone.now().strftime('%Y-%m')
        currency = request.query_params.get('currency') or None
        if not customer_id:
            return Response({'detail': '参数 customer 为必填项'}, status=status.HTTP_400_BAD_REQUEST)
        customer = Customer.objects.filter(pk=customer_id).first()
        if not customer:
            return Response({'detail': '客户不存在'}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(compute_customer_month(customer, bill_month, currency))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ═══════════════════════════════════════════════════════════════
# § 6  服务操作日志（只读）
# ═══════════════════════════════════════════════════════════════

class ServiceOperationLogViewSet(
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """
    服务操作日志只读接口
    list    GET /logs/
    retrieve GET /logs/{id}/
    """
    queryset = ServiceOperationLog.objects.select_related(
        'operator', 'service_order__customer', 'service_instance'
    )
    view_permission = Perm.SERVICE_VIEW
    customer_scope_field = 'service_order__customer'
    serializer_class = ServiceOperationLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['action_type', 'operator', 'service_order', 'service_instance']
    search_fields = ['description', 'service_order__order_no', 'service_instance__instance_no']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ServiceOperationLog.objects.select_related(
            'operator', 'service_order__customer', 'service_instance'
        )
        return self.apply_customer_scope(qs)


class ManualDeliveryCallbackView(APIView):
    """节点/运维用回传令牌更新人工交付清单。不接受 JWT。"""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicDeliveryCallbackRateThrottle]

    def post(self, request):
        from .manual_delivery import (
            ManualDeliveryError, apply_callback, get_delivery_by_token,
            parse_callback_payload,
        )
        raw = (
            request.headers.get('X-Delivery-Token')
            or request.META.get('HTTP_X_DELIVERY_TOKEN')
            or (request.data or {}).get('token')
            or ''
        )
        delivery = get_delivery_by_token(raw)
        if delivery is None:
            return Response({'detail': '交付回传令牌无效或已停用'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            result = apply_callback(
                delivery,
                parse_callback_payload(request.data),
                source='token_callback',
            )
        except ManualDeliveryError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
