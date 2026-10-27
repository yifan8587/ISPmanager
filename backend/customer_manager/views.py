import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.db import transaction
from django.db.models import Sum, ExpressionWrapper, F, DecimalField, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import (
    CustomerModulePermissionMixin,
    BillingModulePermissionMixin,
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
)
from accounts.permissions import (
    deny_channel_finance_write,
    get_user_channel,
    is_channel_role,
    require_bound_channel,
)
from rest_framework.permissions import AllowAny
from accounts.role_permissions import Perm
from backend.throttles import PublicPaymentWebhookRateThrottle

from .models import (
    Customer, ContactRecord, Contract, CustomerBalanceLedger,
    CustomerBilling, CustomerOperationLog, PaymentTransaction,
    ChannelCommission, ChannelSettlement, ChannelPayoutProfile,
    ChannelFinanceAccount, ChannelFinanceLedger,
    record_operation_log, model_to_dict_simple,
)
from .serializers import (
    CustomerListSerializer, CustomerDetailSerializer, CustomerWriteSerializer,
    ContactRecordSerializer, ContractSerializer,
    CustomerBillingSerializer, CustomerOperationLogSerializer,
    CustomerBalanceLedgerSerializer, CustomerBalanceAdjustSerializer,
    PaymentTransactionSerializer, StartOnlinePaymentSerializer,
    ChannelCommissionSerializer, ChannelSettlementSerializer,
    ChannelSettlementDetailSerializer, ChannelPayoutProfileSerializer,
    ChannelFinanceLedgerSerializer, ChannelFinanceAdjustmentSerializer,
    ChannelSettlementCreateSerializer, ChannelSettlementPaymentSerializer,
    ChannelSettlementCancelSerializer, ChannelCommissionVoidSerializer,
)
from . import billing_engine
from . import payment_service
from system_manager.models import PaymentChannel
from system_manager.serializers import PaymentChannelPublicSerializer
from accounts.permissions import IsSystemAccount

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 工具 Mixin
# ─────────────────────────────────────────────────────────────

class LogActionMixin:
    """为 ViewSet 提供自动日志记录"""

    log_target_model = ''

    def _log(self, request, action_type, description, customer=None,
             target_id=None, old_value=None, new_value=None):
        record_operation_log(
            operator=request.user,
            action_type=action_type,
            description=description,
            customer=customer,
            target_model=self.log_target_model,
            target_id=target_id,
            old_value=old_value,
            new_value=new_value,
            request=request,
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        self._log(
            self.request,
            action_type=CustomerOperationLog.ActionType.CREATE,
            description=f'新建 {self.log_target_model}: {instance}',
            customer=getattr(instance, 'customer', None),
            target_id=instance.pk,
            new_value=model_to_dict_simple(instance),
        )

    def perform_update(self, serializer):
        old = model_to_dict_simple(serializer.instance)
        instance = serializer.save()
        self._log(
            self.request,
            action_type=CustomerOperationLog.ActionType.UPDATE,
            description=f'更新 {self.log_target_model}: {instance}',
            customer=getattr(instance, 'customer', None),
            target_id=instance.pk,
            old_value=old,
            new_value=model_to_dict_simple(instance),
        )

    def perform_destroy(self, instance):
        old = model_to_dict_simple(instance)
        self._log(
            self.request,
            action_type=CustomerOperationLog.ActionType.DELETE,
            description=f'删除 {self.log_target_model}: {instance}',
            customer=getattr(instance, 'customer', None),
            target_id=instance.pk,
            old_value=old,
        )
        instance.delete()


class SystemMutationPermissionMixin:
    """读操作沿用模块权限，所有写操作额外要求系统内部账号。"""

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.action not in self.get_read_actions():
            permissions.append(IsSystemAccount())
        return permissions


# ─────────────────────────────────────────────────────────────
# Customer ViewSet
# ─────────────────────────────────────────────────────────────

class CustomerViewSet(
    CustomerScopedQuerysetMixin,
    CustomerModulePermissionMixin,
    LogActionMixin,
    viewsets.ModelViewSet,
):
    """
    客户 CRUD + 查询操作

    list:   GET  /api/customers/
    create: POST /api/customers/
    retrieve: GET /api/customers/{id}/
    update: PUT/PATCH /api/customers/{id}/
    destroy: DELETE /api/customers/{id}/

    自定义动作：
      GET  /api/customers/{id}/logs/            操作日志
      GET  /api/customers/statistics/           统计数据
    """
    customer_scope_field = 'self'
    extra_read_actions = frozenset({'logs', 'statistics', 'wallet_ledger'})

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'customer_type', 'province', 'city', 'sales_manager',
        'channel_referral__channel',
    ]
    search_fields = ['customer_no', 'name', 'contact_name', 'contact_phone',
                     'contact_email', 'company_name']
    ordering_fields = ['created_at', 'updated_at', 'name', 'balance']
    ordering = ['-created_at']
    log_target_model = CustomerOperationLog.TargetModel.CUSTOMER

    def get_queryset(self):
        qs = Customer.objects.select_related(
            'account', 'sales_manager',
            'channel_profile',
            'channel_referral', 'channel_referral__channel',
        ).all()
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CustomerDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return CustomerWriteSerializer
        return CustomerListSerializer

    def perform_create(self, serializer):
        instance = serializer.save()
        record_operation_log(
            operator=self.request.user,
            action_type=CustomerOperationLog.ActionType.CREATE,
            description=f'新建客户 [{instance.customer_no}] {instance.name}',
            customer=instance,
            target_model=CustomerOperationLog.TargetModel.CUSTOMER,
            target_id=instance.pk,
            new_value=model_to_dict_simple(instance),
            request=self.request,
        )

    def perform_update(self, serializer):
        old = model_to_dict_simple(serializer.instance)
        instance = serializer.save()
        new = model_to_dict_simple(instance)
        changed_keys = [k for k in old if old[k] != new.get(k)]
        record_operation_log(
            operator=self.request.user,
            action_type=CustomerOperationLog.ActionType.UPDATE,
            description=f'更新客户 [{instance.customer_no}] {instance.name}，变更：{", ".join(changed_keys)}',
            customer=instance,
            target_model=CustomerOperationLog.TargetModel.CUSTOMER,
            target_id=instance.pk,
            old_value=old,
            new_value=new,
            request=self.request,
        )

    def perform_destroy(self, instance):
        channel = get_user_channel(self.request.user)
        if is_channel_role(self.request.user) and channel and instance.pk == channel.pk:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('不能删除本渠道客户档案')
        old = model_to_dict_simple(instance)
        record_operation_log(
            operator=self.request.user,
            action_type=CustomerOperationLog.ActionType.DELETE,
            description=f'删除客户 [{instance.customer_no}] {instance.name}',
            customer=None,
            target_model=CustomerOperationLog.TargetModel.CUSTOMER,
            target_id=instance.pk,
            old_value=old,
            request=self.request,
        )
        instance.delete()

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        """获取该客户的操作日志"""
        customer = self.get_object()
        logs = CustomerOperationLog.objects.filter(
            customer=customer
        ).select_related('operator').order_by('-created_at')
        page = self.paginate_queryset(logs)
        if page is not None:
            return self.get_paginated_response(
                CustomerOperationLogSerializer(page, many=True).data
            )
        return Response(CustomerOperationLogSerializer(logs, many=True).data)

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """客户统计数据"""
        qs = self.apply_customer_scope(Customer.objects.all())
        billing_qs = CustomerBilling.objects.filter(customer__in=qs)
        type_dist = list(qs.values('customer_type').annotate(count=Count('id')))
        status_dist = list(qs.values('status').annotate(count=Count('id')))
        billing_summary = billing_qs.filter(
            status__in=['unpaid', 'overdue']
        ).aggregate(
            total_outstanding=Sum(
                ExpressionWrapper(F('amount') - F('paid_amount'), output_field=DecimalField())
            )
        )
        return Response({
            'total_customers': qs.count(),
            'active_customers': qs.filter(status='active').count(),
            'type_distribution': type_dist,
            'status_distribution': status_dist,
            'total_outstanding': billing_summary['total_outstanding'] or 0,
        })

    @action(detail=True, methods=['get'], url_path='wallet-ledger')
    def wallet_ledger(self, request, pk=None):
        """GET /api/customer/customers/{id}/wallet-ledger/ — 余额流水。"""
        customer = self.get_object()
        ledgers = CustomerBalanceLedger.objects.filter(
            customer=customer,
        ).select_related('operator', 'related_billing').order_by('-created_at', '-id')
        page = self.paginate_queryset(ledgers)
        if page is not None:
            return self.get_paginated_response(
                CustomerBalanceLedgerSerializer(page, many=True).data
            )
        return Response(CustomerBalanceLedgerSerializer(ledgers, many=True).data)

    @action(detail=True, methods=['post'], url_path='adjust-balance')
    def adjust_balance(self, request, pk=None):
        """POST /api/customer/customers/{id}/adjust-balance/ — 人工调账。"""
        deny_channel_finance_write(request.user, '渠道账号不能调整客户余额')
        from .wallet import WalletError, adjust_customer_balance

        customer = self.get_object()
        ser = CustomerBalanceAdjustSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            ledger = adjust_customer_balance(
                customer,
                delta=ser.validated_data['delta'],
                entry_type=CustomerBalanceLedger.EntryType.ADJUST,
                remark=ser.validated_data['remark'],
                operator=request.user,
            )
        except WalletError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        customer.refresh_from_db()
        return Response({
            'balance': customer.balance,
            'ledger': CustomerBalanceLedgerSerializer(ledger).data,
        })

    @action(detail=True, methods=['get', 'post'], url_path='portal-account')
    def portal_account(self, request, pk=None):
        """渠道/管理员为该客户开通门户登录账号。"""
        customer = self.get_object()
        if request.method == 'GET':
            account = customer.account
            if account is None:
                return Response({'account': None})
            return Response({
                'account': {
                    'id': account.id,
                    'username': account.username,
                    'email': account.email,
                    'first_name': account.first_name,
                    'is_active': account.is_active,
                    'last_login': account.last_login,
                },
            })

        if customer.account_id:
            return Response({'detail': '该客户已绑定登录账号'}, status=status.HTTP_400_BAD_REQUEST)
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        email = (request.data.get('email') or '').strip()
        first_name = (request.data.get('first_name') or customer.contact_name or '').strip()
        if is_channel_role(request.user):
            channel = require_bound_channel(request.user)
            from accounts.permissions import channel_owns_customer
            if not channel_owns_customer(channel, customer):
                return Response({'detail': '只能为本渠道名下客户开通登录账号'}, status=status.HTTP_403_FORBIDDEN)
        if not username:
            return Response({'username': '请输入用户名'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(username=username).exists():
            return Response({'username': '用户名已存在'}, status=status.HTTP_400_BAD_REQUEST)
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            return Response({'password': list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

        from accounts.models import UserProfile
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(password)
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save()
        customer.account = user
        customer.save(update_fields=['account', 'updated_at'])
        from accounts.signals import sync_profile_account_type
        sync_profile_account_type(user)
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
        }, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────
# ContactRecord ViewSet
# ─────────────────────────────────────────────────────────────

class ContactRecordViewSet(
    CustomerScopedQuerysetMixin,
    CustomerModulePermissionMixin,
    LogActionMixin,
    viewsets.ModelViewSet,
):
    """联系记录 CRUD，支持按 customer / contact_type 过滤"""
    serializer_class = ContactRecordSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['customer', 'contact_type', 'handler']
    search_fields = ['summary', 'customer__name']
    ordering = ['-contact_time']
    log_target_model = CustomerOperationLog.TargetModel.CONTACT_RECORD

    def get_queryset(self):
        qs = ContactRecord.objects.select_related('customer', 'handler').all()
        return self.apply_customer_scope(qs)

    def perform_create(self, serializer):
        from accounts.permissions import get_user_customer
        customer = get_user_customer(self.request.user)
        if customer:
            instance = serializer.save(customer=customer)
        else:
            instance = serializer.save()
        record_operation_log(
            operator=self.request.user,
            action_type=CustomerOperationLog.ActionType.CONTACT,
            description=f'新增联系记录：{instance.customer.name} - {instance.get_contact_type_display()}',
            customer=instance.customer,
            target_model=CustomerOperationLog.TargetModel.CONTACT_RECORD,
            target_id=instance.pk,
            request=self.request,
        )


# ─────────────────────────────────────────────────────────────
# Contract ViewSet
# ─────────────────────────────────────────────────────────────

class ContractViewSet(
    CustomerScopedQuerysetMixin,
    CustomerModulePermissionMixin,
    LogActionMixin,
    viewsets.ModelViewSet,
):
    """
    合同 CRUD

    自定义动作：
      POST /api/contracts/{id}/sign/   签订合同（状态改为 active）
    """
    serializer_class = ContractSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['customer', 'status', 'created_by']
    search_fields = ['contract_no', 'contract_name', 'customer__name']
    ordering = ['-created_at']
    log_target_model = CustomerOperationLog.TargetModel.CONTRACT

    def get_queryset(self):
        qs = Contract.objects.select_related('customer', 'created_by').all()
        return self.apply_customer_scope(qs)

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        record_operation_log(
            operator=self.request.user,
            action_type=CustomerOperationLog.ActionType.CREATE,
            description=f'新建合同 [{instance.contract_no}] {instance.contract_name}',
            customer=instance.customer,
            target_model=CustomerOperationLog.TargetModel.CONTRACT,
            target_id=instance.pk,
            request=self.request,
        )

    @action(detail=True, methods=['post'], url_path='sign')
    def sign(self, request, pk=None):
        """签订合同"""
        contract = self.get_object()
        if contract.status not in (
            Contract.ContractStatus.DRAFT,
            Contract.ContractStatus.REVIEWING,
        ):
            return Response(
                {'detail': '只有草稿或审核中的合同才能签订'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        import datetime
        old_status = contract.status
        contract.status = Contract.ContractStatus.ACTIVE
        contract.sign_date = contract.sign_date or datetime.date.today()
        contract.save(update_fields=['status', 'sign_date', 'updated_at'])

        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.CONTRACT_SIGN,
            description=f'合同签订 [{contract.contract_no}] {contract.contract_name}',
            customer=contract.customer,
            target_model=CustomerOperationLog.TargetModel.CONTRACT,
            target_id=contract.pk,
            old_value={'status': old_status},
            new_value={'status': contract.status, 'sign_date': str(contract.sign_date)},
            request=request,
        )
        return Response(ContractSerializer(contract).data)


# ─────────────────────────────────────────────────────────────
# CustomerBilling ViewSet
# ─────────────────────────────────────────────────────────────

class CustomerBillingViewSet(
    CustomerScopedQuerysetMixin,
    BillingModulePermissionMixin,
    LogActionMixin,
    viewsets.ModelViewSet,
):
    """
    账单 CRUD

    自定义动作：
      POST /api/billings/{id}/pay/   登记收款
    """
    serializer_class = CustomerBillingSerializer
    extra_read_actions = frozenset({
        'preview', 'online_payment_methods', 'payment_transactions',
    })
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'customer', 'status', 'bill_month', 'payment_method',
        'auto_generated', 'billing_kind', 'service_order',
    ]
    search_fields = ['bill_no', 'customer__name']
    ordering = ['-bill_month', '-created_at']
    log_target_model = CustomerOperationLog.TargetModel.BILLING
    _payment_controlled_fields = frozenset({
        'paid_amount', 'status', 'payment_method', 'paid_at',
    })

    def get_queryset(self):
        qs = CustomerBilling.objects.select_related(
            'customer', 'service_order', 'channel_referral',
        ).prefetch_related('items', 'items__traffic_report').all()
        return self.apply_customer_scope(qs)

    def update(self, request, *args, **kwargs):
        """支付金额和支付状态只能经收款/网关状态机写入。"""
        attempted = self._payment_controlled_fields & set(request.data)
        if attempted:
            return Response(
                {
                    'detail': (
                        f'字段 {", ".join(sorted(attempted))} 仅能由登记收款或'
                        '已验签的在线支付回调更新'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    def _resolve_due_days(self):
        from system_manager.services import get_setting
        try:
            return int(get_setting('billing_due_days', 30) or 30)
        except (TypeError, ValueError):
            return 30

    @action(detail=True, methods=['get'], url_path='online-payment-methods')
    def online_payment_methods(self, request, pk=None):
        """当前账单可选的、已启用的线上支付渠道。"""
        billing = self.get_object()
        channels = payment_service.available_channels(billing.currency, billing=billing)
        return Response(PaymentChannelPublicSerializer(
            channels,
            many=True,
            context={'request': request, 'billing': billing},
        ).data)

    @action(detail=True, methods=['get'], url_path='payment-transactions')
    def payment_transactions(self, request, pk=None):
        """账单的在线支付记录。"""
        billing = self.get_object()
        qs = billing.payment_transactions.select_related('channel', 'initiated_by').all()
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(PaymentTransactionSerializer(page, many=True).data)
        return Response(PaymentTransactionSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='start-online-payment')
    def start_online_payment(self, request, pk=None):
        """创建线上支付交易并返回服务商跳转/表单/二维码/SKD 指令。"""
        billing = self.get_object()
        serializer = StartOnlinePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            channel = PaymentChannel.objects.get(pk=data['channel_id'])
        except PaymentChannel.DoesNotExist:
            return Response({'detail': '支付渠道不存在'}, status=status.HTTP_404_NOT_FOUND)

        frontend_return_url = _frontend_billing_return_url(request)
        try:
            payment = payment_service.create_payment_transaction(
                billing=billing,
                channel=channel,
                operator=request.user,
                amount=data.get('amount'),
                return_url=frontend_return_url,
            )
            from system_manager.payment_urls import payment_callback_url
            callback_url = payment_callback_url(channel.id, request)
            gateway_return_url = request.build_absolute_uri(
                reverse('payment-return', kwargs={'transaction_no': payment.transaction_no}),
            )
            payment, checkout = payment_service.issue_gateway_checkout(
                payment=payment,
                callback_url=callback_url,
                gateway_return_url=gateway_return_url,
            )
        except payment_service.PaymentServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'transaction': PaymentTransactionSerializer(payment).data,
            'checkout': checkout,
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        """按月计费预览（不落库）。body: {customer, bill_month, currency?}"""
        customer_id = request.data.get('customer')
        bill_month = request.data.get('bill_month')
        currency = request.data.get('currency') or None
        if not customer_id or not bill_month:
            return Response({'detail': '请提供 customer 与 bill_month(YYYY-MM)'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            customer = self.apply_customer_scope(Customer.objects.all()).get(pk=customer_id)
        except Customer.DoesNotExist:
            return Response({'detail': '客户不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        try:
            result = billing_engine.compute_customer_month(customer, bill_month, currency)
        except (ValueError, IndexError):
            return Response({'detail': '账单月份格式应为 YYYY-MM'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """按月生成/更新账单。body: {customer, bill_month, currency?}"""
        customer_id = request.data.get('customer')
        bill_month = request.data.get('bill_month')
        currency = request.data.get('currency') or None
        if not customer_id or not bill_month:
            return Response({'detail': '请提供 customer 与 bill_month(YYYY-MM)'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            customer = self.apply_customer_scope(Customer.objects.all()).get(pk=customer_id)
        except Customer.DoesNotExist:
            return Response({'detail': '客户不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)
        try:
            billing, result, updated = billing_engine.generate_bill(
                customer, bill_month, user=request.user,
                due_days=self._resolve_due_days(), target_currency=currency,
            )
        except billing_engine.BillingGenerationBlocked as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except (ValueError, IndexError):
            return Response({'detail': '账单月份格式应为 YYYY-MM'}, status=status.HTTP_400_BAD_REQUEST)
        record_operation_log(
            operator=request.user,
            action_type=CustomerOperationLog.ActionType.OTHER,
            description=f'{"生成" if updated else "跳过(已支付)"}账单 [{billing.bill_no}] '
                        f'{bill_month} {billing.currency}{billing.amount}',
            customer=customer,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=billing.pk,
            request=request,
        )
        return Response({
            'billing': CustomerBillingSerializer(billing).data,
            'summary': result,
            'updated': updated,
        })

    @action(detail=True, methods=['post'], url_path='pay')
    def pay(self, request, pk=None):
        """登记收款"""
        billing = self.get_object()
        amount_str = request.data.get('amount')
        payment_method = request.data.get('payment_method', '')

        try:
            pay_amount = Decimal(str(amount_str))
            if pay_amount <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {'detail': '请输入有效的收款金额'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            billing = CustomerBilling.objects.select_for_update().select_related('customer').get(pk=billing.pk)
            if billing.status in {
                CustomerBilling.BillingStatus.CANCELLED,
                CustomerBilling.BillingStatus.REFUNDING,
                CustomerBilling.BillingStatus.REFUNDED,
            }:
                return Response(
                    {'detail': '账单已取消或已退款，无法登记收款'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            outstanding = billing.amount - billing.paid_amount
            if pay_amount > outstanding:
                return Response(
                    {'detail': f'收款金额不能超过未付金额 ¥{outstanding:.2f}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            old_paid = billing.paid_amount
            old_status = billing.status
            billing.paid_amount = billing.paid_amount + pay_amount
            if payment_method:
                billing.payment_method = payment_method
            billing.paid_at = timezone.now()
            if billing.paid_amount >= billing.amount:
                billing.status = CustomerBilling.BillingStatus.PAID
            billing.save(update_fields=['paid_amount', 'payment_method', 'paid_at', 'status', 'updated_at'])

            commission = None
            if (
                old_status != CustomerBilling.BillingStatus.PAID
                and billing.status == CustomerBilling.BillingStatus.PAID
            ):
                from .channel_services import ensure_paid_billing_commission

                from .wallet import credit_paid_recharge

                credit_paid_recharge(billing, operator=request.user)
                commission = ensure_paid_billing_commission(
                    billing,
                    operator=request.user,
                    request=request,
                )

            record_operation_log(
                operator=request.user,
                action_type=CustomerOperationLog.ActionType.PAYMENT,
                description=f'收款 ¥{pay_amount:,.2f}，账单 [{billing.bill_no}]',
                customer=billing.customer,
                target_model=CustomerOperationLog.TargetModel.BILLING,
                target_id=billing.pk,
                old_value={'paid_amount': str(old_paid), 'status': old_status},
                new_value={
                    'paid_amount': str(billing.paid_amount),
                    'status': billing.status,
                    'channel_commission_id': commission.pk if commission else None,
                },
                request=request,
            )
            if (
                old_status != CustomerBilling.BillingStatus.PAID
                and billing.status == CustomerBilling.BillingStatus.PAID
                and billing.service_order_id
            ):
                from service_manager.payment_fulfillment import fulfill_paid_billing_safely

                transaction.on_commit(
                    lambda billing_id=billing.pk: fulfill_paid_billing_safely(billing_id),
                )
        return Response(CustomerBillingSerializer(billing).data)

    @action(detail=True, methods=['post'], url_path='request-refund')
    def request_refund(self, request, pk=None):
        """对开通失败 / 升配未执行的已付账单发起退款；force 可强制人工退。"""
        billing = self.get_object()
        force = bool(request.data.get('force'))
        reason = str(request.data.get('reason') or '').strip() or None
        from .refund_engine import RefundError, request_refund_for_billing

        try:
            result = request_refund_for_billing(
                billing.pk,
                reason=reason,
                operator=request.user,
                force=force,
            )
        except RefundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        billing.refresh_from_db()
        return Response({
            'billing': CustomerBillingSerializer(billing).data,
            'refund': result,
        })

    @action(detail=True, methods=['post'], url_path='complete-refund')
    def complete_refund(self, request, pk=None):
        """确认人工退款已完成。"""
        billing = self.get_object()
        from .refund_engine import RefundError, mark_manual_refund_completed

        try:
            result = mark_manual_refund_completed(billing.pk, operator=request.user)
        except RefundError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        billing.refresh_from_db()
        return Response({
            'billing': CustomerBillingSerializer(billing).data,
            'refund': result,
        })


# ─────────────────────────────────────────────────────────────
# 渠道佣金与结算
# ─────────────────────────────────────────────────────────────

class ChannelCommissionViewSet(
    CustomerScopedQuerysetMixin,
    SystemMutationPermissionMixin,
    CustomerModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """渠道佣金明细、汇总、作废和创建结算单。"""

    customer_scope_field = 'channel'
    serializer_class = ChannelCommissionSerializer
    extra_read_actions = frozenset({'summary'})
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['channel', 'customer', 'billing', 'settlement', 'status', 'currency']
    search_fields = [
        'bill_no', 'promotion_code',
        'channel__customer_no', 'channel__name',
        'customer__customer_no', 'customer__name',
    ]
    ordering_fields = ['earned_at', 'commission_amount', 'commission_base_amount', 'settled_at']
    ordering = ['-earned_at']

    def get_queryset(self):
        qs = ChannelCommission.objects.select_related(
            'channel', 'customer', 'billing', 'settlement',
        ).all()
        return self.apply_customer_scope(qs)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        totals = qs.aggregate(
            total_count=Count('id'),
            pending_count=Count('id', filter=Q(status=ChannelCommission.Status.PENDING)),
            settling_count=Count('id', filter=Q(status=ChannelCommission.Status.SETTLING)),
            paid_count=Count('id', filter=Q(status=ChannelCommission.Status.PAID)),
            void_count=Count('id', filter=Q(status=ChannelCommission.Status.VOID)),
        )
        by_currency = list(
            qs.values('currency')
            .annotate(
                pending_amount=Sum(
                    'commission_amount',
                    filter=Q(status=ChannelCommission.Status.PENDING),
                ),
                settling_amount=Sum(
                    'commission_amount',
                    filter=Q(status=ChannelCommission.Status.SETTLING),
                ),
                paid_amount=Sum(
                    'commission_amount',
                    filter=Q(status=ChannelCommission.Status.PAID),
                ),
            )
            .order_by('currency')
        )
        by_channel = list(
            qs.values('channel_id', 'channel__customer_no', 'channel__name', 'currency')
            .annotate(
                commission_count=Count('id'),
                pending_amount=Sum(
                    'commission_amount',
                    filter=Q(status=ChannelCommission.Status.PENDING),
                ),
                paid_amount=Sum(
                    'commission_amount',
                    filter=Q(status=ChannelCommission.Status.PAID),
                ),
            )
            .order_by('channel__name', 'currency')
        )
        from system_manager.currency_services import sum_in_settlement
        return Response({
            **{key: value or 0 for key, value in totals.items()},
            'by_currency': by_currency,
            'by_channel': by_channel,
            'settlement_totals': sum_in_settlement(
                by_currency,
                ('pending_amount', 'settling_amount', 'paid_amount'),
            ),
        })

    @action(detail=False, methods=['post'], url_path='create-settlement')
    def create_settlement(self, request):
        deny_channel_finance_write(request.user, '渠道账号不能发起结算')
        serializer = ChannelSettlementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commission_ids = serializer.validated_data['commission_ids']
        visible_count = self.get_queryset().filter(id__in=commission_ids).count()
        if visible_count != len(set(commission_ids)):
            return Response({'detail': '部分佣金不存在或无权访问'}, status=status.HTTP_404_NOT_FOUND)

        from .channel_services import ChannelServiceError, create_channel_settlement

        try:
            settlement = create_channel_settlement(
                commission_ids,
                operator=request.user,
                remark=serializer.validated_data.get('remark', ''),
            )
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelSettlementSerializer(settlement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='void')
    def void(self, request, pk=None):
        deny_channel_finance_write(request.user, '渠道账号不能作废佣金')
        serializer = ChannelCommissionVoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        commission = self.get_object()

        from .channel_services import ChannelServiceError, void_channel_commission

        try:
            commission = void_channel_commission(
                commission,
                operator=request.user,
                reason=serializer.validated_data['reason'],
            )
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelCommissionSerializer(commission).data)


class ChannelPayoutProfileViewSet(
    CustomerScopedQuerysetMixin,
    SystemMutationPermissionMixin,
    CustomerModulePermissionMixin,
    viewsets.ModelViewSet,
):
    """维护渠道加密收款资料；响应只返回掩码账号。"""

    customer_scope_field = 'channel'
    serializer_class = ChannelPayoutProfileSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['channel', 'account_type']
    search_fields = ['channel__customer_no', 'channel__name', 'entity_name', 'recipient_name']
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_queryset(self):
        return self.apply_customer_scope(
            ChannelPayoutProfile.objects.select_related('channel').order_by('-updated_at'),
        )

    def perform_create(self, serializer):
        deny_channel_finance_write(self.request.user, '渠道账号不能修改收款资料')
        serializer.save()

    def perform_update(self, serializer):
        deny_channel_finance_write(self.request.user, '渠道账号不能修改收款资料')
        serializer.save()


class ChannelFinanceLedgerViewSet(
    CustomerScopedQuerysetMixin,
    SystemMutationPermissionMixin,
    CustomerModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """不可变渠道财务流水、账户汇总及人工调整。"""

    customer_scope_field = 'account__channel'
    serializer_class = ChannelFinanceLedgerSerializer
    extra_read_actions = frozenset({'summary'})
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account__channel', 'account__currency', 'entry_type', 'source_type', 'source_id']
    search_fields = [
        'account__channel__customer_no', 'account__channel__name',
        'source_id', 'idempotency_key',
    ]
    ordering_fields = ['created_at', 'delta', 'balance_after']
    ordering = ['-created_at', '-id']

    def get_queryset(self):
        queryset = self.apply_customer_scope(
            ChannelFinanceLedger.objects.select_related(
                'account', 'account__channel', 'operator',
            ).all(),
        )
        channel = self.request.query_params.get('channel')
        currency = self.request.query_params.get('currency')
        start_date = parse_date(self.request.query_params.get('start_date', ''))
        end_date = parse_date(self.request.query_params.get('end_date', ''))
        if channel:
            queryset = queryset.filter(account__channel_id=channel)
        if currency:
            queryset = queryset.filter(account__currency=str(currency).upper())
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)
        return queryset

    @action(detail=False, methods=['get'])
    def summary(self, request):
        from accounts.permissions import get_user_customer, is_channel_role, require_bound_channel
        accounts = ChannelFinanceAccount.objects.select_related('channel').all()
        scoped_customer = get_user_customer(request.user)
        if scoped_customer is not None:
            accounts = accounts.filter(channel=scoped_customer)
        elif is_channel_role(request.user):
            accounts = accounts.filter(channel=require_bound_channel(request.user))
        channel = request.query_params.get('channel')
        currency = request.query_params.get('currency')
        if channel:
            accounts = accounts.filter(channel_id=channel)
        if currency:
            accounts = accounts.filter(currency=str(currency).upper())
        ledgers = ChannelFinanceLedger.objects.filter(account__in=accounts)
        start_date = parse_date(request.query_params.get('start_date', ''))
        end_date = parse_date(request.query_params.get('end_date', ''))
        if start_date:
            ledgers = ledgers.filter(created_at__date__gte=start_date)
        if end_date:
            ledgers = ledgers.filter(created_at__date__lte=end_date)
        by_currency = list(
            ledgers.values('account__currency')
            .annotate(
                accrual=Sum('delta', filter=Q(entry_type=ChannelFinanceLedger.EntryType.ACCRUAL)),
                void=Sum('delta', filter=Q(entry_type=ChannelFinanceLedger.EntryType.VOID)),
                payout=Sum('delta', filter=Q(entry_type=ChannelFinanceLedger.EntryType.PAYOUT)),
                adjustment=Sum('delta', filter=Q(entry_type=ChannelFinanceLedger.EntryType.ADJUSTMENT)),
            )
            .order_by('account__currency')
        )
        for item in by_currency:
            item['currency'] = item.pop('account__currency')
            for field in ('accrual', 'void', 'payout', 'adjustment'):
                item[field] = item[field] or Decimal('0.00')
        from system_manager.currency_services import sum_in_settlement
        balance_totals = sum_in_settlement(
            [
                {'currency': item.currency, 'balance': item.balance}
                for item in accounts
            ],
            ('balance',),
        )
        flow_totals = sum_in_settlement(
            by_currency,
            ('accrual', 'void', 'payout', 'adjustment'),
        )
        settlement_totals = {**balance_totals, **flow_totals}
        return Response({
            'total_balance': accounts.aggregate(total=Sum('balance'))['total'] or Decimal('0.00'),
            'settlement_totals': settlement_totals,
            'accounts': [
                {
                    'id': item.id,
                    'channel': item.channel_id,
                    'channel_no': item.channel.customer_no,
                    'channel_name': item.channel.name,
                    'currency': item.currency,
                    'balance': item.balance,
                }
                for item in accounts.order_by('channel__name', 'currency')
            ],
            'by_currency': by_currency,
        })

    @action(detail=False, methods=['post'])
    def adjustment(self, request):
        deny_channel_finance_write(request.user, '渠道账号不能调整渠道账户')
        serializer = ChannelFinanceAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from accounts.permissions import get_user_customer
        scoped_customer = get_user_customer(request.user)
        if scoped_customer is not None and scoped_customer.pk != data['channel'].pk:
            return Response({'detail': '无权调整该渠道账户'}, status=status.HTTP_403_FORBIDDEN)
        from .channel_services import ChannelServiceError, adjust_channel_balance
        try:
            ledger = adjust_channel_balance(
                channel=data['channel'],
                currency=data['currency'],
                delta=data['delta'],
                reason=data['reason'],
                reference=data['reference'],
                evidence=data['evidence'],
                operator=request.user,
                idempotency_key=data['idempotency_key'],
            )
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelFinanceLedgerSerializer(ledger).data, status=status.HTTP_201_CREATED)


class ChannelSettlementViewSet(
    CustomerScopedQuerysetMixin,
    SystemMutationPermissionMixin,
    CustomerModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """渠道结算批次及确认、登记打款、取消操作。"""

    customer_scope_field = 'channel'
    serializer_class = ChannelSettlementSerializer
    extra_read_actions = frozenset({'commissions'})
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['channel', 'status', 'currency']
    search_fields = ['settlement_no', 'channel__customer_no', 'channel__name', 'payment_reference']
    ordering_fields = ['created_at', 'paid_at', 'total_amount']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = ChannelSettlement.objects.select_related(
            'channel', 'created_by', 'confirmed_by', 'paid_by',
        ).prefetch_related('commissions').all()
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ChannelSettlementDetailSerializer
        return ChannelSettlementSerializer

    @action(detail=True, methods=['get'], url_path='commissions')
    def commissions(self, request, pk=None):
        settlement = self.get_object()
        qs = settlement.commissions.select_related(
            'channel', 'customer', 'billing', 'settlement',
        ).order_by('-earned_at')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ChannelCommissionSerializer(page, many=True).data)
        return Response(ChannelCommissionSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='confirm')
    def confirm(self, request, pk=None):
        deny_channel_finance_write(request.user, '渠道账号不能确认结算')
        settlement = self.get_object()
        from .channel_services import ChannelServiceError, confirm_channel_settlement

        try:
            settlement = confirm_channel_settlement(settlement, operator=request.user)
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelSettlementSerializer(settlement).data)

    @action(detail=True, methods=['post'], url_path='mark-paid')
    def mark_paid(self, request, pk=None):
        deny_channel_finance_write(request.user, '渠道账号不能登记打款')
        serializer = ChannelSettlementPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settlement = self.get_object()
        from .channel_services import ChannelServiceError, pay_channel_settlement

        try:
            settlement = pay_channel_settlement(
                settlement,
                operator=request.user,
                payment_method=serializer.validated_data['payment_method'],
                payment_reference=serializer.validated_data.get('payment_reference', ''),
            )
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelSettlementSerializer(settlement).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        deny_channel_finance_write(request.user, '渠道账号不能取消结算')
        serializer = ChannelSettlementCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        settlement = self.get_object()
        from .channel_services import ChannelServiceError, cancel_channel_settlement

        try:
            settlement = cancel_channel_settlement(
                settlement,
                operator=request.user,
                reason=serializer.validated_data.get('reason', ''),
            )
        except ChannelServiceError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ChannelSettlementSerializer(settlement).data)


# ─────────────────────────────────────────────────────────────
# CustomerOperationLog ViewSet（只读）
# ─────────────────────────────────────────────────────────────

class CustomerOperationLogViewSet(
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """操作日志（只读）"""
    view_permission = Perm.CUSTOMER_VIEW
    serializer_class = CustomerOperationLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['customer', 'operator', 'action_type', 'target_model']
    search_fields = ['description', 'customer__name', 'operator__username']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = CustomerOperationLog.objects.select_related('customer', 'operator').all()
        return self.apply_customer_scope(qs)


# ─────────────────────────────────────────────────────────────
# 在线支付交易 / 回调
# ─────────────────────────────────────────────────────────────

class PaymentTransactionViewSet(
    CustomerScopedQuerysetMixin,
    BillingModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """在线支付交易状态查询；客户账号自动限制为自己的账单。"""

    customer_scope_field = 'billing__customer'
    serializer_class = PaymentTransactionSerializer
    lookup_field = 'transaction_no'
    lookup_url_kwarg = 'transaction_no'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['billing', 'provider', 'status']
    search_fields = ['transaction_no', 'billing__bill_no', 'provider_order_id', 'provider_payment_id']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = PaymentTransaction.objects.select_related(
            'billing', 'channel', 'initiated_by',
        ).all()
        return self.apply_customer_scope(qs)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in payment_service.OPEN_PAYMENT_STATUSES:
            payment_service.reconcile_payment_transaction(
                instance,
                force=False,
                expire_if_unpaid=True,
                request=request,
            )
            instance = self.get_object()
        from service_manager.payment_fulfillment import maybe_fulfill_succeeded_payment
        maybe_fulfill_succeeded_payment(instance)
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reconcile')
    def reconcile(self, request, transaction_no=None):
        if not IsSystemAccount().has_permission(request, self):
            return Response({'detail': '仅后台可强制对账'}, status=status.HTTP_403_FORBIDDEN)
        instance = self.get_object()
        payment_service.reconcile_payment_transaction(
            instance,
            force=True,
            expire_if_unpaid=True,
            request=request,
        )
        instance = self.get_object()
        return Response(self.get_serializer(instance).data)


def _frontend_billing_return_url(request) -> str:
    """生成允许支付完成后返回的前端账单页，避免保存任意外部跳转地址。"""
    configured = str(getattr(settings, 'PAYMENT_FRONTEND_URL', '') or '').strip().rstrip('/')
    admin_base = str(getattr(settings, 'ADMIN_BASE_PATH', '') or '').strip()
    if admin_base and not admin_base.startswith('/'):
        admin_base = f'/{admin_base}'
    admin_base = admin_base.rstrip('/')

    def _with_billing(base: str) -> str:
        base = base.rstrip('/')
        if not admin_base or base.endswith(admin_base):
            return f'{base}/customer/billings'
        return f'{base}{admin_base}/customer/billings'

    if configured:
        return _with_billing(configured)

    origin = str(request.headers.get('Origin') or '').strip().rstrip('/')
    if origin:
        parsed = urlsplit(origin)
        allowed_origins = set(getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or [])
        allow_all = bool(getattr(settings, 'CORS_ALLOW_ALL_ORIGINS', False))
        if parsed.scheme in {'http', 'https'} and parsed.netloc and (allow_all or origin in allowed_origins):
            return _with_billing(origin)

    suffix = f'{admin_base}/customer/billings' if admin_base else '/customer/billings'
    return request.build_absolute_uri(suffix)


def _payment_webhook_ack(provider: str):
    """按支付平台要求返回成功应答，避免对方把已入账的通知当成失败并反复重试。"""
    if provider == 'wechat':
        return JsonResponse({'code': 'SUCCESS', 'message': '成功'})
    if provider == 'unionpay':
        return HttpResponse('ok', status=status.HTTP_200_OK)
    return HttpResponse('success', status=status.HTTP_200_OK)


class PaymentWebhookView(APIView):
    """服务商异步通知入口。所有入账均先执行服务商签名验证。"""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PublicPaymentWebhookRateThrottle]

    def post(self, request, channel_id: int):
        try:
            channel = PaymentChannel.objects.get(pk=channel_id)
        except PaymentChannel.DoesNotExist:
            return HttpResponse('not found', status=status.HTTP_404_NOT_FOUND)

        try:
            if channel.provider in {'alipay', 'unionpay'}:
                payment_service.process_webhook(
                    channel=channel, form_data=request.data, headers=request.headers, request=request,
                )
            else:
                payment_service.process_webhook(
                    channel=channel, raw_body=request.body, headers=request.headers, request=request,
                )
        except (payment_service.PaymentServiceError, ValueError) as exc:
            return HttpResponse(str(exc), status=status.HTTP_400_BAD_REQUEST)
        return _payment_webhook_ack(channel.provider)


class PaymentReturnView(APIView):
    """支付服务商的浏览器同步回跳入口；处理后重定向回账单页面。"""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, transaction_no: str):
        return self._handle(request, transaction_no, request.query_params)

    def post(self, request, transaction_no: str):
        return self._handle(request, transaction_no, request.data)

    def _handle(self, request, transaction_no: str, data):
        try:
            payment = PaymentTransaction.objects.select_related('channel', 'billing').get(
                transaction_no=transaction_no,
            )
        except PaymentTransaction.DoesNotExist:
            return HttpResponse('payment transaction not found', status=status.HTTP_404_NOT_FOUND)

        error = ''
        try:
            payment = payment_service.process_payment_return(
                payment=payment, data=data, request=request,
            )
        except (payment_service.PaymentServiceError, ValueError) as exc:
            error = str(exc)
            payment.refresh_from_db()

        try:
            from service_manager.payment_fulfillment import maybe_fulfill_succeeded_payment
            maybe_fulfill_succeeded_payment(payment)
        except Exception:  # noqa: BLE001
            logger.warning('支付回跳后履约触发失败：%s', payment.transaction_no, exc_info=True)

        target = payment.return_url or _frontend_billing_return_url(request)
        separator = '&' if '?' in target else '?'
        params = {
            'payment_transaction': payment.transaction_no,
            'payment_status': payment.status,
        }
        if error:
            params['payment_error'] = error[:200]
        return HttpResponseRedirect(f'{target}{separator}{urlencode(params)}')
