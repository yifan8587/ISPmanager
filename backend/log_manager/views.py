from django.db.models import Count
from django_filters import rest_framework as django_filters
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, extend_schema_view

from accounts.drf_mixins import SystemAccountPermissionMixin, ViewOnlyPermissionMixin

from .models import AuditLog
from .serializers import AuditLogSerializer, serialize_meta


class AuditLogFilter(django_filters.FilterSet):
    created_at_after = django_filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_at_before = django_filters.IsoDateTimeFilter(field_name='created_at', lookup_expr='lte')
    date_from = django_filters.DateFilter(field_name='created_at', lookup_expr='date__gte')
    date_to = django_filters.DateFilter(field_name='created_at', lookup_expr='date__lte')
    operator_name = django_filters.CharFilter(field_name='operator_name', lookup_expr='icontains')

    class Meta:
        model = AuditLog
        fields = (
            'module', 'action_type', 'operator', 'source',
            'target_model', 'target_id',
        )


@extend_schema_view(
    list=extend_schema(tags=['操作日志'], summary='审计日志列表'),
    retrieve=extend_schema(tags=['操作日志'], summary='审计日志详情'),
)
class AuditLogViewSet(
    SystemAccountPermissionMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    view_permission = 'system.log.view'
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    filter_backends = [django_filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'description', 'target_repr', 'operator_name',
        'request_path', 'target_model', 'ip_address',
    ]
    ordering = ['-created_at', '-id']
    queryset = AuditLog.objects.select_related('operator').all()

    @extend_schema(tags=['操作日志'], summary='日志模块/类型元数据')
    @action(detail=False, methods=['get'])
    def meta(self, request):
        counts = {
            row['module']: row['count']
            for row in AuditLog.objects.values('module').annotate(count=Count('id'))
        }
        return Response(serialize_meta(module_counts=counts))
