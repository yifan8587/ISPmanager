"""管理端自助重装 / 快照 / 镜像目录。"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from accounts.drf_mixins import CustomerScopedQuerysetMixin, ModulePermissionMixin
from accounts.role_permissions import page_permission

from .models import InstanceSnapshot, OsImage, SelfServiceJob
from .serializers import (
    InstanceSnapshotSerializer, OsImageSerializer,
    SelfServiceJobMessageSerializer, SelfServiceJobSerializer,
)


def _raise(exc: DjangoValidationError):
    if getattr(exc, 'message_dict', None):
        raise ValidationError(exc.message_dict)
    raise ValidationError(list(exc.messages) if getattr(exc, 'messages', None) else str(exc))


class OsImageViewSet(ModulePermissionMixin, viewsets.ModelViewSet):
    queryset = OsImage.objects.all()
    serializer_class = OsImageSerializer
    view_permission = page_permission('service', 'selfservice', 'view')
    manage_permission = page_permission('service', 'selfservice', 'manage')
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['os_type', 'enabled']
    search_fields = ['code', 'name', 'version']
    ordering = ['sort_order', 'id']


class InstanceSnapshotViewSet(
    CustomerScopedQuerysetMixin,
    ModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    serializer_class = InstanceSnapshotSerializer
    view_permission = page_permission('service', 'selfservice', 'view')
    manage_permission = page_permission('service', 'selfservice', 'manage')
    extra_read_actions = frozenset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'vps']
    search_fields = ['snapshot_no', 'name', 'vps__vps_no']
    ordering = ['-created_at']
    customer_scope_field = 'vps__customer'

    def get_queryset(self):
        return self.apply_customer_scope(
            InstanceSnapshot.objects.select_related('vps__customer', 'service_instance')
        )

    @action(detail=True, methods=['post'], url_path='delete')
    def delete_snapshot(self, request, pk=None):
        from .self_service import delete_snapshot
        snap = self.get_object()
        try:
            delete_snapshot(snap, requested_by=request.user, request=request)
        except DjangoValidationError as exc:
            _raise(exc)
        return Response(self.get_serializer(snap).data)


class SelfServiceJobViewSet(
    CustomerScopedQuerysetMixin,
    ModulePermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    serializer_class = SelfServiceJobSerializer
    view_permission = page_permission('service', 'selfservice', 'view')
    manage_permission = page_permission('service', 'selfservice', 'manage')
    extra_read_actions = frozenset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['job_type', 'status', 'vps']
    search_fields = ['job_no', 'vps__vps_no', 'result_message']
    ordering = ['-created_at']
    customer_scope_field = 'vps__customer'

    def get_queryset(self):
        return self.apply_customer_scope(
            SelfServiceJob.objects.select_related(
                'vps__customer', 'service_instance', 'os_image', 'snapshot', 'requested_by',
            )
        )

    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        serializer = SelfServiceJobMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .self_service import complete_job
        try:
            job = complete_job(self.get_object(), message=serializer.validated_data.get('message') or '')
        except DjangoValidationError as exc:
            _raise(exc)
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=['post'], url_path='fail')
    def fail(self, request, pk=None):
        serializer = SelfServiceJobMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .self_service import fail_job
        try:
            job = fail_job(self.get_object(), message=serializer.validated_data.get('message') or '')
        except DjangoValidationError as exc:
            _raise(exc)
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        serializer = SelfServiceJobMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from .self_service import cancel_job
        try:
            job = cancel_job(self.get_object(), message=serializer.validated_data.get('message') or '')
        except DjangoValidationError as exc:
            _raise(exc)
        return Response(self.get_serializer(job).data)
