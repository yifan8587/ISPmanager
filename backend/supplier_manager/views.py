"""
supplier_manager/views.py
供应商管理模块 DRF ViewSets
"""
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import SupplierModulePermissionMixin, ViewOnlyPermissionMixin
from excel_io import ExcelIOMixin
from accounts.models import UserProfile
from accounts.permissions import (
    check_permission,
    get_user_supplier,
    is_supplier_role,
)
from accounts.role_permissions import page_permission

from . import services
from .excel_ops import export_suppliers, import_suppliers
from .models import (
    Supplier, SupplierAPI, SupplierBill, SupplierPopBinding, SupplierResource,
    SupplierSettlement, SupplierStatus, SupplierSyncLog,
)
from .serializers import (
    MapResourceSerializer, PullResourcesSerializer,
    SupplierAccountBindSerializer, SupplierAccountSerializer,
    SupplierAccountUnbindSerializer, SupplierAccountUpdateSerializer,
    SupplierAccountWriteSerializer, SupplierAPISerializer, SupplierBriefSerializer,
    SupplierBillSerializer, SupplierPopBindingSerializer, SupplierResourceSerializer,
    SupplierSerializer, SupplierSettlementSerializer, SupplierSyncLogSerializer,
)
from .settlement import (
    cancel_settlement, finance_summary, mark_settlement_paid, request_settlement,
)


def _can_manage_supplier_accounts(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return check_permission(user, page_permission('supplier', 'account', 'manage')) or check_permission(
        user, page_permission('supplier', 'list', 'manage'),
    )


def _can_manage_supplier_finance(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return check_permission(user, page_permission('supplier', 'list', 'manage')) or check_permission(
        user, page_permission('supplier', 'billing', 'manage'),
    )


def _bind_user_to_supplier(user, supplier):
    if user.is_superuser or user.is_staff:
        raise ValidationError({'username': '不能将管理员绑定为供应商账号'})
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.account_type == UserProfile.AccountType.CUSTOMER:
        raise ValidationError({'username': '客户账号不能绑定为供应商账号'})
    if profile.supplier_id and profile.supplier_id != supplier.pk:
        raise ValidationError({'username': '该账号已绑定其他供应商'})
    profile.account_type = UserProfile.AccountType.SYSTEM
    profile.role = UserProfile.Role.SUPPLIER
    profile.supplier = supplier
    profile.save()
    user.is_staff = False
    user.is_superuser = False
    user.save(update_fields=['is_staff', 'is_superuser'])
    return user


def _unbind_user_from_supplier(user, supplier):
    profile = getattr(user, 'profile', None)
    if not profile or profile.supplier_id != supplier.pk:
        raise ValidationError({'user_id': '该账号未绑定此供应商'})
    profile.supplier = None
    if profile.role == UserProfile.Role.SUPPLIER:
        profile.role = UserProfile.Role.OPERATOR
    profile.save(update_fields=['supplier', 'role'])
    return user


def _scope_supplier_qs(request, qs, field='supplier'):
    supplier = get_user_supplier(request.user)
    if is_supplier_role(request.user):
        if supplier is None:
            return qs.none()
        if field == 'self':
            return qs.filter(pk=supplier.pk)
        return qs.filter(**{field: supplier})
    return qs


class SupplierViewSet(ExcelIOMixin, SupplierModulePermissionMixin, viewsets.ModelViewSet):
    """供应商 CRUD + 关联资源/POP/同步日志/账号/财务摘要。"""
    excel_export_fn = export_suppliers
    excel_import_fn = import_suppliers
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['supplier_type', 'status']
    search_fields = ['name', 'short_name', 'supplier_no', 'company_name', 'contact_name']
    ordering_fields = ['created_at', 'rating', 'name']
    ordering = ['-created_at']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset(), field='self')

    def perform_create(self, serializer):
        if is_supplier_role(self.request.user):
            raise PermissionDenied('供应商账号不能新建供应商')
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        if is_supplier_role(self.request.user):
            raise PermissionDenied('供应商账号不能修改供应商档案')
        serializer.save()

    def perform_destroy(self, instance):
        if is_supplier_role(self.request.user):
            raise PermissionDenied('供应商账号不能删除供应商')
        instance.delete()

    @action(detail=False, methods=['get'], url_path='brief')
    def brief_list(self, request):
        qs = self.get_queryset().exclude(status=SupplierStatus.DISABLED)
        return Response(SupplierBriefSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'])
    def resources(self, request, pk=None):
        supplier = self.get_object()
        qs = supplier.resources.all()
        rtype = request.query_params.get('resource_type')
        if rtype:
            qs = qs.filter(resource_type=rtype)
        page = self.paginate_queryset(qs)
        ser = SupplierResourceSerializer(page if page is not None else qs, many=True)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)

    @action(detail=True, methods=['get'], url_path='sync-logs')
    def sync_logs(self, request, pk=None):
        supplier = self.get_object()
        qs = supplier.sync_logs.all()[:200]
        return Response(SupplierSyncLogSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        qs = self.get_queryset()
        by_status = {}
        for s in SupplierStatus.values:
            by_status[s] = qs.filter(status=s).count()
        return Response({
            'total': qs.count(),
            'by_status': by_status,
            'api_total': SupplierAPI.objects.filter(supplier__in=qs).count(),
            'resource_total': SupplierResource.objects.filter(supplier__in=qs).count(),
        })

    @action(detail=True, methods=['get'], url_path='finance-summary')
    def finance_summary(self, request, pk=None):
        supplier = self.get_object()
        return Response(finance_summary(supplier))

    @action(detail=True, methods=['get', 'post'], url_path='accounts')
    def accounts(self, request, pk=None):
        supplier = self.get_object()
        if request.method == 'GET':
            users = User.objects.filter(
                profile__supplier=supplier, profile__role=UserProfile.Role.SUPPLIER,
            ).select_related('profile').order_by('-date_joined')
            return Response(SupplierAccountSerializer(users, many=True).data)

        if not _can_manage_supplier_accounts(request.user):
            raise PermissionDenied('无权创建供应商账号')
        ser = SupplierAccountWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if User.objects.filter(username=data['username']).exists():
            raise ValidationError({'username': '用户名已存在'})
        try:
            validate_password(data['password'])
        except DjangoValidationError as exc:
            raise ValidationError({'password': list(exc.messages)})
        user = User(
            username=data['username'],
            email=data.get('email') or '',
            first_name=data.get('first_name') or '',
            is_active=data.get('is_active', True),
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(data['password'])
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.SYSTEM
        profile.role = UserProfile.Role.SUPPLIER
        profile.supplier = supplier
        profile.phone = data.get('phone') or ''
        profile.remark = data.get('remark') or ''
        profile.save()
        return Response(SupplierAccountSerializer(user).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='accounts/bind')
    def bind_account(self, request, pk=None):
        supplier = self.get_object()
        if not _can_manage_supplier_accounts(request.user):
            raise PermissionDenied('无权关联供应商账号')
        if is_supplier_role(request.user):
            raise PermissionDenied('供应商账号不能关联其他账号')
        ser = SupplierAccountBindSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            user = User.objects.select_related('profile').get(username=ser.validated_data['username'])
        except User.DoesNotExist:
            raise ValidationError({'username': '用户不存在'})
        user = _bind_user_to_supplier(user, supplier)
        return Response(SupplierAccountSerializer(user).data)

    @action(detail=True, methods=['post'], url_path='accounts/unbind')
    def unbind_account(self, request, pk=None):
        supplier = self.get_object()
        if not _can_manage_supplier_accounts(request.user):
            raise PermissionDenied('无权解除供应商账号关联')
        if is_supplier_role(request.user):
            raise PermissionDenied('供应商账号不能解除账号关联')
        ser = SupplierAccountUnbindSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            user = User.objects.select_related('profile').get(pk=ser.validated_data['user_id'])
        except User.DoesNotExist:
            raise ValidationError({'user_id': '用户不存在'})
        _unbind_user_from_supplier(user, supplier)
        return Response(SupplierAccountSerializer(user).data)


class SupplierAccountViewSet(SupplierModulePermissionMixin, viewsets.GenericViewSet):
    """供应商账号更新 / 重置密码 / 停用。"""
    queryset = User.objects.filter(profile__role=UserProfile.Role.SUPPLIER).select_related('profile')
    serializer_class = SupplierAccountSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        supplier = get_user_supplier(self.request.user)
        if is_supplier_role(self.request.user):
            if supplier is None:
                return qs.none()
            return qs.filter(profile__supplier=supplier)
        return qs

    def _require_manage(self):
        if not _can_manage_supplier_accounts(self.request.user):
            raise PermissionDenied('无权管理供应商账号')

    @action(detail=True, methods=['patch'], url_path='update-account')
    def update_account(self, request, pk=None):
        self._require_manage()
        user = self.get_object()
        ser = SupplierAccountUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        for field in ('email', 'first_name', 'is_active'):
            if field in data:
                setattr(user, field, data[field])
        if data.get('password'):
            try:
                validate_password(data['password'])
            except DjangoValidationError as exc:
                raise ValidationError({'password': list(exc.messages)})
            user.set_password(data['password'])
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if 'phone' in data:
            profile.phone = data['phone'] or ''
        if 'remark' in data:
            profile.remark = data['remark'] or ''
        profile.role = UserProfile.Role.SUPPLIER
        profile.save()
        return Response(SupplierAccountSerializer(user).data)

    @action(detail=True, methods=['post'], url_path='reset-password')
    def reset_password(self, request, pk=None):
        self._require_manage()
        user = self.get_object()
        password = request.data.get('password') or ''
        if not password:
            raise ValidationError({'password': '请输入新密码'})
        try:
            validate_password(password)
        except DjangoValidationError as exc:
            raise ValidationError({'password': list(exc.messages)})
        user.set_password(password)
        user.save(update_fields=['password'])
        return Response({'success': True})

    @action(detail=True, methods=['post'])
    def disable(self, request, pk=None):
        self._require_manage()
        user = self.get_object()
        user.is_active = False
        user.save(update_fields=['is_active'])
        return Response(SupplierAccountSerializer(user).data)


class SupplierAPIViewSet(SupplierModulePermissionMixin, viewsets.ModelViewSet):
    """供应商 API 配置 CRUD + 连接测试 + 拉取资源。"""
    queryset = SupplierAPI.objects.select_related('supplier').all()
    serializer_class = SupplierAPISerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['supplier', 'is_active', 'sync_enabled']
    search_fields = ['name', 'base_url', 'supplier__name']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())

    def perform_create(self, serializer):
        if is_supplier_role(self.request.user):
            raise PermissionDenied('供应商账号不能管理对接 API')
        serializer.save()

    @action(detail=True, methods=['post'], url_path='test')
    def test_connection(self, request, pk=None):
        api = self.get_object()
        try:
            result = services.SupplierClient(api).test_connection()
            return Response(result, status=status.HTTP_200_OK if result.get('success') else status.HTTP_400_BAD_REQUEST)
        except services.SupplierAPIError as e:
            return Response({'success': False, 'message': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='pull-resources')
    def pull_resources(self, request, pk=None):
        api = self.get_object()
        rtype = request.data.get('resource_type', SupplierResource.ResType.VPS)
        try:
            result = services.pull_resources(api, operator=request.user, resource_type=rtype)
            return Response(result)
        except services.SupplierAPIError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SupplierPopBindingViewSet(SupplierModulePermissionMixin, viewsets.ModelViewSet):
    queryset = SupplierPopBinding.objects.select_related('supplier', 'pop_point').all()
    serializer_class = SupplierPopBindingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['supplier', 'pop_point', 'is_active']
    search_fields = ['external_pop_id', 'external_pop_name', 'supplier__name']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())


class SupplierResourceViewSet(SupplierModulePermissionMixin, viewsets.ModelViewSet):
    """供应商资源（多由 API 同步生成）；支持映射 POP / 同步为产品。"""
    queryset = SupplierResource.objects.select_related('supplier', 'pop_point').all()
    serializer_class = SupplierResourceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['supplier', 'resource_type', 'sync_status', 'pop_point']
    search_fields = ['external_id', 'name', 'region_text']
    ordering_fields = ['updated_at', 'created_at']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())

    def perform_create(self, serializer):
        if is_supplier_role(self.request.user):
            raise PermissionDenied('供应商账号不能管理同步资源')
        serializer.save()

    @action(detail=True, methods=['post'])
    def map(self, request, pk=None):
        """映射资源到 POP 点，并可选同步为本地产品。"""
        resource = self.get_object()
        ser = MapResourceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        if data.get('pop_point_id'):
            from resource_manager.models import PopPoint
            try:
                resource.pop_point = PopPoint.objects.get(pk=data['pop_point_id'])
            except PopPoint.DoesNotExist:
                return Response({'detail': 'POP 点不存在'}, status=status.HTTP_404_NOT_FOUND)
            resource.save(update_fields=['pop_point', 'updated_at'])

        if data.get('to_product'):
            try:
                product, created = services.map_resource_to_product(resource, operator=request.user)
            except services.SupplierAPIError as e:
                return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            return Response({
                'success': True,
                'product_id': product.pk,
                'product_no': product.product_no,
                'created': created,
            })
        return Response(SupplierResourceSerializer(resource).data)


class SupplierSyncLogViewSet(SupplierModulePermissionMixin, viewsets.ReadOnlyModelViewSet):
    queryset = SupplierSyncLog.objects.select_related('supplier', 'api', 'operator').all()
    serializer_class = SupplierSyncLogSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['supplier', 'api', 'action', 'result']
    ordering = ['-created_at']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())


class SupplierBillViewSet(ViewOnlyPermissionMixin, viewsets.ReadOnlyModelViewSet):
    view_permission = page_permission('supplier', 'billing', 'view')
    queryset = SupplierBill.objects.select_related(
        'supplier', 'product', 'settlement', 'customer_billing',
    ).all()
    serializer_class = SupplierBillSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['supplier', 'status', 'currency', 'bill_month', 'product']
    search_fields = ['bill_no', 'customer_bill_no', 'customer_name', 'product_name']
    ordering = ['-created_at']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())

    @action(detail=False, methods=['get'])
    def summary(self, request):
        supplier = get_user_supplier(request.user)
        if supplier is None:
            sid = request.query_params.get('supplier')
            if not sid:
                return Response({'detail': '请指定供应商'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                supplier = Supplier.objects.get(pk=sid)
            except Supplier.DoesNotExist:
                return Response({'detail': '供应商不存在'}, status=status.HTTP_404_NOT_FOUND)
        return Response(finance_summary(supplier))


class SupplierSettlementViewSet(ViewOnlyPermissionMixin, viewsets.ReadOnlyModelViewSet):
    view_permission = page_permission('supplier', 'billing', 'view')
    extra_read_actions = frozenset()
    queryset = SupplierSettlement.objects.select_related(
        'supplier', 'created_by', 'settled_by',
    ).all()
    serializer_class = SupplierSettlementSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['supplier', 'status', 'currency', 'auto_created']
    search_fields = ['settlement_no', 'supplier__name']
    ordering = ['-created_at']

    def get_queryset(self):
        return _scope_supplier_qs(self.request, super().get_queryset())

    def _target_supplier(self, request):
        scoped = get_user_supplier(request.user)
        if scoped is not None:
            return scoped
        sid = request.data.get('supplier') or request.query_params.get('supplier')
        if not sid:
            raise ValidationError({'supplier': '请指定供应商'})
        try:
            return Supplier.objects.get(pk=sid)
        except Supplier.DoesNotExist:
            raise ValidationError({'supplier': '供应商不存在'})

    @action(detail=False, methods=['post'], url_path='request')
    def request_settle(self, request):
        supplier = self._target_supplier(request)
        if is_supplier_role(request.user) and get_user_supplier(request.user) != supplier:
            raise PermissionDenied('只能申请本供应商的结算')
        enforce = is_supplier_role(request.user) or not _can_manage_supplier_finance(request.user)
        try:
            settlement = request_settlement(
                supplier, operator=request.user, enforce_threshold=enforce,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(str(exc))
        return Response(SupplierSettlementSerializer(settlement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def pay(self, request, pk=None):
        if not _can_manage_supplier_finance(request.user):
            raise PermissionDenied('无权确认供应商结算打款')
        settlement = self.get_object()
        remark = request.data.get('remark') or ''
        try:
            settlement = mark_settlement_paid(settlement, operator=request.user, remark=remark)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(str(exc))
        return Response(SupplierSettlementSerializer(settlement).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        if not _can_manage_supplier_finance(request.user):
            raise PermissionDenied('无权取消结算单')
        settlement = self.get_object()
        try:
            settlement = cancel_settlement(settlement, operator=request.user)
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(str(exc))
        return Response(SupplierSettlementSerializer(settlement).data)
