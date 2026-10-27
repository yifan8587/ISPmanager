import logging
import os

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum, OuterRef, Subquery, IntegerField, Prefetch, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

from accounts.drf_mixins import (
    ViewOnlyPermissionMixin,
    ResourceModulePermissionMixin,
    ResourceSystemPermissionMixin,
    CustomerScopedQuerysetMixin,
    customer_pop_ids,
)
from accounts.permissions import (
    apply_supplier_scope,
    deny_supplier_role,
    deny_supplier_write,
    ensure_supplier_pop_binding,
    is_supplier_role,
    require_bound_supplier,
    supplier_owns_pop,
    supplier_scope_pop_q,
    supplier_scope_via_pop_q,
)
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied


def _ensure_supplier_pop(request, pop):
    if not is_supplier_role(request.user):
        return
    supplier = require_bound_supplier(request.user)
    if pop is None or not supplier_owns_pop(supplier, pop):
        raise DRFPermissionDenied('只能管理本供应商的 POP / IP 资源')
from excel_io import ExcelIOMixin, load_upload
from .excel_base import export_base_data, import_base_data
from .excel_ops import (
    export_ip_addresses,
    export_ip_blocks,
    export_pops,
    export_tunnels,
    import_ip_addresses,
    import_ip_blocks,
    import_pops,
    import_tunnels,
)
from accounts.role_permissions import Perm

from .models import (
    Region, Country, City, AvailabilityZone,
    Carrier, NetworkTypeOption,
    PopPoint,
    NetworkInterface, RouteTable, StaticRoute, PolicyRoute,
    IPBlock, IPAddress, TunnelIPPool,
    VPS, VPSNetworkInterface,
    EdgeAcceleration, GREConfig, VXLANConfig, WireguardConfig, Socks5Config,
    BandwidthAllocation,
    ResourceOperationLog,
    record_resource_log, resource_model_snapshot,
)
from .serializers import (
    RegionSerializer, CountrySerializer, CitySerializer, AvailabilityZoneSerializer,
    CarrierSerializer, NetworkTypeOptionSerializer,
    PopPointListSerializer, PopPointDetailSerializer, PopPointWriteSerializer,
    VPSBandwidthItemSerializer, EdgeBandwidthItemSerializer,
    NetworkInterfaceSerializer,
    RouteTableSerializer, StaticRouteSerializer, PolicyRouteSerializer,
    IPBlockSerializer, IPBlockDetailSerializer,
    TunnelIPPoolSerializer,
    IPAddressSerializer, IPAddressAssignSerializer, IPBulkCreateSerializer,
    VPSListSerializer, VPSDetailSerializer, VPSWriteSerializer,
    VPSNetworkInterfaceSerializer,
    EdgeAccelerationListSerializer, EdgeAccelerationDetailSerializer, EdgeAccelerationWriteSerializer,
    GREConfigSerializer, VXLANConfigSerializer, WireguardConfigSerializer, Socks5ConfigSerializer,
    BandwidthAllocationSerializer,
    ResourceOperationLogSerializer,
    StatusChangeSerializer,
)
from .sync_service import sync_pop_from_node, sync_single_vps_from_node
from .deploy_service import push_pop_to_node
from . import ip_ops
from . import ip_scope
from . import pve_ops
from .pemanager_client import PemanagerError, pop_has_api_config

logger = logging.getLogger(__name__)


def _sync_service_instance_ip(
    ip, *, vps=None, edge=None, operator=None, request=None,
    released=False, before_state=None,
):
    """资源管理侧的 IP 分配/释放同步到关联服务实例与工单审计链路。"""
    target = vps or edge
    if target is None:
        return None
    try:
        service_instance = target.service_instance
    except Exception:  # 资源尚未关联服务实例是正常情况
        return None

    try:
        from service_manager.models import (
            ServiceAllocation, ServiceOperationLog, _log_service,
            service_snapshot, sync_service_order_delivery_state,
        )
        from service_manager.notify_helpers import notify_instance_event

        before = before_state if before_state is not None else service_snapshot(service_instance)
        if released:
            service_instance.ip_addresses.remove(ip)
            action_type = ServiceAllocation.ActionType.IP_REMOVE
            operation_type = ServiceOperationLog.ActionType.RELEASE_IP
            description = f'资源管理侧释放 IP {ip.address}'
            event_title = '资源管理侧 IP 已释放'
        else:
            service_instance.ip_addresses.add(ip)
            action_type = ServiceAllocation.ActionType.IP_ADD
            operation_type = ServiceOperationLog.ActionType.ASSIGN_IP
            description = f'资源管理侧分配 IP {ip.address}'
            event_title = '资源管理侧 IP 已分配'
        after = service_snapshot(service_instance)
        ServiceAllocation.objects.create(
            service_instance=service_instance,
            action_type=action_type,
            description=description,
            before_state=before,
            after_state=after,
            resource_type='IPAddress',
            resource_id=ip.pk,
            resource_repr=str(ip.address),
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=operation_type,
            description=f'实例 [{service_instance.instance_no}] {description}',
            service_order=service_instance.service_order,
            service_instance=service_instance,
            old_value=before,
            new_value=after,
            request=request,
        )
        sync_service_order_delivery_state(
            service_instance.service_order,
            operator=operator,
            reason=description,
        )
        notify_instance_event(service_instance, event_title, detail=str(ip.address))
        return service_instance
    except Exception:  # noqa: BLE001
        logger.exception('IP %s 同步服务实例失败', ip.address)
        return None


def _linked_service_snapshot(vps=None, edge=None):
    """在资源状态变更前保存关联服务实例快照；未关联时返回 None。"""
    target = vps or edge
    if target is None:
        return None
    try:
        from service_manager.models import service_snapshot
        return service_snapshot(target.service_instance)
    except Exception:
        return None


def _sync_service_instance_bandwidth(allocation, *, operator=None, request=None, detail=''):
    """带宽资源变更后，绑定关联服务实例并写入服务工单审计记录。"""
    try:
        service_instance = None
        if hasattr(allocation, 'service_instance'):
            service_instance = allocation.service_instance
        if service_instance is None:
            target = allocation.vps or allocation.edge
            if target is not None and hasattr(target, 'service_instance'):
                service_instance = target.service_instance
        if not service_instance:
            return None

        from service_manager.models import (
            ServiceAllocation, ServiceOperationLog, _log_service,
            service_snapshot, sync_service_order_delivery_state,
        )
        from service_manager.notify_helpers import notify_instance_event

        before = service_snapshot(service_instance)
        if service_instance.bandwidth_alloc_id != allocation.id:
            service_instance.bandwidth_alloc = allocation
            service_instance.save(update_fields=['bandwidth_alloc', 'updated_at'])
        after = service_snapshot(service_instance)
        description = detail or f'带宽分配同步为 {allocation.allocated_mbps}Mbps'
        ServiceAllocation.objects.create(
            service_instance=service_instance,
            action_type=ServiceAllocation.ActionType.BW_CHANGE,
            description=description,
            before_state=before,
            after_state=after,
            resource_type='BandwidthAllocation',
            resource_id=allocation.pk,
            resource_repr=str(allocation),
            operator=operator,
        )
        _log_service(
            operator=operator,
            action_type=ServiceOperationLog.ActionType.BW_ADJUST,
            description=f'实例 [{service_instance.instance_no}] {description}',
            service_order=service_instance.service_order,
            service_instance=service_instance,
            old_value=before,
            new_value=after,
            request=request,
        )
        sync_service_order_delivery_state(
            service_instance.service_order,
            operator=operator,
            reason=description,
        )
        notify_instance_event(service_instance, '资源管理侧带宽已同步', detail=description)
        return service_instance
    except Exception:  # noqa: BLE001
        logger.exception('带宽分配 %s 同步服务实例失败', allocation.pk)
        return None


# ─────────────────────────────────────────────────────────────
# 通用 Mixin
# ─────────────────────────────────────────────────────────────

def mark_pop_dirty(pop):
    """标记某 POP 点存在「未下发到节点」的配置变更（前端 CRUD 后调用）。"""
    if pop is None:
        return
    from django.utils import timezone
    pk = pop.pk if hasattr(pop, 'pk') else pop
    PopPoint.objects.filter(pk=pk).update(
        config_dirty=True, config_dirty_at=timezone.now())


def request_wants_node_push(request, default=True):
    """query 或 body 的 push_to_node；缺省按 default。"""
    raw = None
    if request is not None:
        raw = request.query_params.get('push_to_node')
        if raw is None and isinstance(getattr(request, 'data', None), dict):
            raw = request.data.get('push_to_node')
    if raw is None:
        return default
    return str(raw).lower() in ('1', 'true', 'yes')


def _raise_if_node_push_failed(result, fallback='下发节点失败'):
    if result.get('skipped'):
        return
    if not result.get('ok'):
        raise DRFValidationError({'detail': result.get('detail') or fallback})


def retry_paid_orders_after_capacity_change(pop, *, operator=None, reason=''):
    """资源扩容或释放后，异步于当前写入事务恢复可自动交付的已付款订单。"""
    if pop is None:
        return
    try:
        from service_manager.capacity_recovery import schedule_capacity_recovery

        schedule_capacity_recovery(pop, operator=operator, reason=reason)
    except Exception:  # noqa: BLE001
        logger.warning('POP %s 容量恢复后重试工单调度失败', getattr(pop, 'pk', None), exc_info=True)


class ResourceLogMixin:
    """为 ViewSet 提供统一日志记录"""
    log_target_model = ''

    def _log(self, request, action_type, description,
             target_id=None, target_repr='',
             pop_point=None, vps=None, edge=None,
             old_value=None, new_value=None):
        record_resource_log(
            operator=request.user,
            action_type=action_type,
            description=description,
            target_model=self.log_target_model,
            target_id=target_id,
            target_repr=target_repr,
            pop_point=pop_point,
            vps=vps,
            edge=edge,
            old_value=old_value,
            new_value=new_value,
            request=request,
        )


class ResourceLocationFilterMixin:
    """为资源列表统一提供 region/country/city/availability_zone/pop_point 地理筛选。

    各资源的数据模型并不完全相同：IP 地址需经 IPBlock 回溯 POP，POP 本身
    则直接以自身主键为节点。子类通过 lookup 属性声明对应关系，前端不必再
    了解这些模型差异。
    """

    location_region_lookup = 'pop_point__city__country__region'
    location_country_lookup = 'pop_point__city__country'
    location_city_lookup = 'pop_point__city'
    location_zone_lookup = 'pop_point__availability_zone'
    location_pop_lookup = 'pop_point'

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        params = self.request.query_params
        filters_by_location = (
            ('region', self.location_region_lookup),
            ('country', self.location_country_lookup),
            ('city', self.location_city_lookup),
            ('availability_zone', self.location_zone_lookup),
            ('pop_point', self.location_pop_lookup),
        )
        for parameter, lookup in filters_by_location:
            value = params.get(parameter)
            if value not in (None, ''):
                queryset = queryset.filter(**{lookup: value})
        return queryset


# ═══════════════════════════════════════════════════════════════
# § 1  地理位置（基础数据库设置：大区 / 国家 / 城市，完整 CRUD）
# ═══════════════════════════════════════════════════════════════

class BaseGeoCRUDMixin(ResourceLogMixin):
    """
    地理位置基础数据通用 CRUD：统一操作日志 + 保护性删除。
    子类需提供 log_target_model 与 _describe(instance)。
    """

    def _describe(self, instance):
        return str(instance)

    def perform_create(self, serializer):
        deny_supplier_write(self, self.request, '供应商账号不可修改地理基础数据')
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建{instance._meta.verbose_name} {self._describe(instance)}',
            target_id=instance.pk, target_repr=str(instance),
            new_value=resource_model_snapshot(instance),
        )

    def perform_update(self, serializer):
        deny_supplier_write(self, self.request, '供应商账号不可修改地理基础数据')
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.UPDATE,
            f'更新{instance._meta.verbose_name} {self._describe(instance)}',
            target_id=instance.pk, target_repr=str(instance),
            old_value=old, new_value=resource_model_snapshot(instance),
        )

    def perform_destroy(self, instance):
        deny_supplier_write(self, self.request, '供应商账号不可修改地理基础数据')
        from django.db.models import ProtectedError
        snap = resource_model_snapshot(instance)
        repr_str = str(instance)
        vname = instance._meta.verbose_name
        try:
            instance.delete()
        except ProtectedError:
            raise DRFValidationError(
                f'该{vname}已被下级数据引用（如国家/城市/POP点等），请先移除引用后再删除。'
            )
        self._log(
            self.request, ResourceOperationLog.ActionType.DELETE,
            f'删除{vname} {repr_str}',
            target_id=instance.pk, target_repr=repr_str, old_value=snap,
        )


class RegionViewSet(ResourceSystemPermissionMixin, BaseGeoCRUDMixin, viewsets.ModelViewSet):
    queryset = Region.objects.all()
    serializer_class = RegionSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'name_en', 'code']
    ordering_fields = ['code', 'name']
    log_target_model = ResourceOperationLog.TargetModel.REGION

    def _describe(self, instance):
        return f'[{instance.code}] {instance.name}'


class CountryViewSet(ResourceSystemPermissionMixin, BaseGeoCRUDMixin, viewsets.ModelViewSet):
    queryset = Country.objects.select_related('region').all()
    serializer_class = CountrySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['region']
    search_fields = ['name', 'name_en']
    ordering_fields = ['name', 'region']
    log_target_model = ResourceOperationLog.TargetModel.COUNTRY

    def _describe(self, instance):
        return instance.name


class CityViewSet(ResourceSystemPermissionMixin, BaseGeoCRUDMixin, viewsets.ModelViewSet):
    queryset = City.objects.select_related('country__region').all()
    serializer_class = CitySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['country', 'country__region']
    search_fields = ['name', 'name_en', 'country__name']
    ordering_fields = ['name', 'country']
    log_target_model = ResourceOperationLog.TargetModel.CITY

    def _describe(self, instance):
        return f'{instance.name} / {instance.country.name}'


class AvailabilityZoneViewSet(ResourceSystemPermissionMixin, BaseGeoCRUDMixin, viewsets.ModelViewSet):
    queryset = AvailabilityZone.objects.select_related('city__country__region').all()
    serializer_class = AvailabilityZoneSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['city', 'city__country', 'city__country__region']
    search_fields = ['name', 'name_en', 'code', 'city__name']
    ordering_fields = ['name', 'city']
    log_target_model = ResourceOperationLog.TargetModel.AVAILABILITY_ZONE

    def _describe(self, instance):
        return f'{instance.name} / {instance.city.name}'


class NetworkTypeOptionViewSet(ResourceSystemPermissionMixin, ResourceLogMixin, viewsets.ModelViewSet):
    """网络类型目录 CRUD，供 IP 管理与产品筛选动态取数。"""
    queryset = NetworkTypeOption.objects.all()
    serializer_class = NetworkTypeOptionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active']
    search_fields = ['code', 'name']
    ordering_fields = ['sort_order', 'id', 'name']
    ordering = ['sort_order', 'id']
    log_target_model = ResourceOperationLog.TargetModel.OTHER

    def perform_create(self, serializer):
        deny_supplier_write(self, self.request, '供应商账号不可修改网络类型目录')
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建网络类型 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
        )

    def perform_update(self, serializer):
        deny_supplier_write(self, self.request, '供应商账号不可修改网络类型目录')
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.UPDATE,
            f'更新网络类型 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            old_value=old, new_value=resource_model_snapshot(instance),
        )

    def perform_destroy(self, instance):
        deny_supplier_write(self, self.request, '供应商账号不可修改网络类型目录')
        if IPBlock.objects.filter(network_type=instance.code).exists():
            raise DRFValidationError({'detail': '仍有 IP 地址块使用该网络类型，请先调整后再删除'})
        self._log(
            self.request, ResourceOperationLog.ActionType.DELETE,
            f'删除网络类型 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
        )
        instance.delete()


# ═══════════════════════════════════════════════════════════════
# § 2  运营商
# ═══════════════════════════════════════════════════════════════

class BaseDataExcelViewSet(ResourceSystemPermissionMixin, viewsets.ViewSet):
    """系统基础字典（大区/国家/城市/可用区/运营商）整本导入导出。"""

    extra_read_actions = frozenset({'export', 'import_template'})

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        return export_base_data()

    @action(detail=False, methods=['get'], url_path='import-template')
    def import_template(self, request):
        return export_base_data(empty=True)

    @action(detail=False, methods=['post'], url_path='import', parser_classes=[MultiPartParser, FormParser])
    def import_excel(self, request):
        deny_supplier_role(request.user, '供应商账号不可导入系统基础数据')
        return Response(import_base_data(load_upload(request)))


class CarrierViewSet(ResourceSystemPermissionMixin, ResourceLogMixin, viewsets.ModelViewSet):
    """
    运营商 CRUD
    GET  /api/resource/carriers/statistics/  统计各运营商 POP 数
    """
    queryset = Carrier.objects.prefetch_related('regions').all()
    serializer_class = CarrierSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'code']
    log_target_model = ResourceOperationLog.TargetModel.CARRIER

    def perform_create(self, serializer):
        deny_supplier_write(self, self.request, '供应商账号不可修改运营商')
        instance = serializer.save()
        self._log(
            self.request,
            ResourceOperationLog.ActionType.CREATE,
            f'新建运营商 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
        )

    def perform_update(self, serializer):
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        self._log(
            self.request,
            ResourceOperationLog.ActionType.UPDATE,
            f'更新运营商 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            old_value=old, new_value=resource_model_snapshot(instance),
        )

    def perform_destroy(self, instance):
        snap = resource_model_snapshot(instance)
        self._log(
            self.request,
            ResourceOperationLog.ActionType.DELETE,
            f'删除运营商 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            old_value=snap,
        )
        instance.delete()

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        data = list(
            Carrier.objects.annotate(pop_count=Count('pop_points')).values(
                'id', 'code', 'name', 'pop_count'
            )
        )
        return Response(data)


# ═══════════════════════════════════════════════════════════════
# § 3  POP 点
# ═══════════════════════════════════════════════════════════════

class PopPointViewSet(
    ExcelIOMixin,
    ResourceLocationFilterMixin,
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    POP 点 CRUD + 自定义操作

    POST /api/resource/pop-points/{id}/change_status/   变更状态
    GET  /api/resource/pop-points/{id}/logs/            操作日志
    GET  /api/resource/pop-points/{id}/bandwidth/       带宽分配明细
    PATCH /api/resource/pop-points/{id}/set_bandwidth/  设置总带宽
    GET  /api/resource/pop-points/statistics/           全局统计
    """
    excel_export_fn = export_pops
    excel_import_fn = import_pops
    extra_read_actions = frozenset({
        'logs', 'statistics', 'bandwidth', 'map_overview', 'pve_resources', 'pve_job',
    })

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'pop_type', 'status', 'network_type', 'asn_nature', 'city',
        'availability_zone', 'city__country', 'city__country__region',
        'supplier', 'config_mode',
    ]
    search_fields = ['code', 'name', 'mgmt_ip', 'city__name', 'availability_zone__name']
    ordering_fields = ['name', 'created_at', 'total_bandwidth_mbps']
    ordering = ['city__country__region__name', 'city__country__name', 'city__name', 'name']
    log_target_model = ResourceOperationLog.TargetModel.POP
    location_region_lookup = 'city__country__region'
    location_country_lookup = 'city__country'
    location_city_lookup = 'city'
    location_zone_lookup = 'availability_zone'
    location_pop_lookup = 'pk'

    def get_customer_scope_q(self, customer):
        pop_ids = customer_pop_ids(customer)
        return Q(pk__in=pop_ids) if pop_ids else Q(pk__in=[])

    # ── 子查询：统计每个 POP 下 VPS / 边缘加速的带宽汇总 ──────────
    @staticmethod
    def _bw_subqueries():
        """返回 VPS 带宽子查询、Edge 带宽子查询、VPS 数量子查询、Edge 数量子查询"""
        vps_bw_sq = (
            VPS.objects
            .filter(pop_point=OuterRef('pk'))
            .values('pop_point')
            .annotate(t=Sum('bandwidth_mbps'))
            .values('t')
        )
        edge_bw_sq = (
            EdgeAcceleration.objects
            .filter(pop_point=OuterRef('pk'))
            .values('pop_point')
            .annotate(t=Sum('bandwidth_mbps'))
            .values('t')
        )
        vps_cnt_sq = (
            VPS.objects
            .filter(pop_point=OuterRef('pk'))
            .values('pop_point')
            .annotate(c=Count('id'))
            .values('c')
        )
        edge_cnt_sq = (
            EdgeAcceleration.objects
            .filter(pop_point=OuterRef('pk'))
            .values('pop_point')
            .annotate(c=Count('id'))
            .values('c')
        )
        return vps_bw_sq, edge_bw_sq, vps_cnt_sq, edge_cnt_sq

    def get_queryset(self):
        vps_bw_sq, edge_bw_sq, vps_cnt_sq, edge_cnt_sq = self._bw_subqueries()
        qs = (
            PopPoint.objects
            .prefetch_related('carriers')
            .select_related('city__country__region', 'availability_zone', 'created_by')
            .annotate(
                ann_vps_bw    = Coalesce(Subquery(vps_bw_sq,    output_field=IntegerField()), Value(0)),
                ann_edge_bw   = Coalesce(Subquery(edge_bw_sq,   output_field=IntegerField()), Value(0)),
                ann_vps_count = Coalesce(Subquery(vps_cnt_sq,   output_field=IntegerField()), Value(0)),
                ann_edge_count= Coalesce(Subquery(edge_cnt_sq,  output_field=IntegerField()), Value(0)),
            )
        )
        qs = self.apply_customer_scope(qs)
        return apply_supplier_scope(self.request, qs, supplier_scope_pop_q)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PopPointDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return PopPointWriteSerializer
        return PopPointListSerializer

    def perform_create(self, serializer):
        extra = {'created_by': self.request.user}
        supplier = require_bound_supplier(self.request.user)
        if supplier is not None:
            extra['supplier'] = supplier
        instance = serializer.save(**extra)
        if supplier is not None:
            ensure_supplier_pop_binding(instance, supplier)
        from resource_manager.pve_ops import schedule_pop_os_image_downloads
        schedule_pop_os_image_downloads(instance)
        self._log(
            self.request,
            ResourceOperationLog.ActionType.CREATE,
            f'新建 POP 点 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance,
        )

    def perform_update(self, serializer):
        supplier = require_bound_supplier(self.request.user)
        if supplier is not None:
            serializer.validated_data['supplier'] = supplier
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        if supplier is not None:
            ensure_supplier_pop_binding(instance, supplier)
        action_type = (
            ResourceOperationLog.ActionType.STATUS_CHANGE
            if 'status' in serializer.validated_data
            else ResourceOperationLog.ActionType.UPDATE
        )
        self._log(
            self.request, action_type,
            f'更新 POP 点 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance,
            old_value=old, new_value=resource_model_snapshot(instance),
        )
        retry_paid_orders_after_capacity_change(
            instance,
            operator=self.request.user,
            reason='POP 点容量或配置已调整',
        )
        api_fields = {
            'pop_type', 'api_base_url', 'api_username', 'api_password',
            'mgmt_ip', 'mgmt_token', 'mgmt_username', 'mgmt_password',
        }
        if api_fields.intersection(serializer.validated_data):
            from resource_manager.pve_ops import schedule_pop_os_image_downloads
            schedule_pop_os_image_downloads(instance)

    def perform_destroy(self, instance):
        snap = resource_model_snapshot(instance)
        self._log(
            self.request,
            ResourceOperationLog.ActionType.DELETE,
            f'删除 POP 点 [{instance.code}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            old_value=snap,
        )
        instance.delete()

    @action(detail=True, methods=['post'], url_path='change_status')
    def change_status(self, request, pk=None):
        pop = self.get_object()
        ser = StatusChangeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data['status']
        valid = [c[0] for c in PopPoint.PopStatus.choices]
        if new_status not in valid:
            return Response({'detail': f'无效状态，可选：{valid}'}, status=400)
        old_status = pop.status
        pop.status = new_status
        pop.save(update_fields=['status', 'updated_at'])
        self._log(
            request,
            ResourceOperationLog.ActionType.STATUS_CHANGE,
            f'POP 点 [{pop.code}] 状态: {old_status} → {new_status}，原因: {ser.validated_data["reason"]}',
            target_id=pop.pk, target_repr=str(pop), pop_point=pop,
            old_value={'status': old_status}, new_value={'status': new_status},
        )
        return Response(PopPointListSerializer(pop).data)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        pop = self.get_object()
        qs = pop.operation_logs.select_related('operator').order_by('-created_at')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ResourceOperationLogSerializer(page, many=True).data)
        return Response(ResourceOperationLogSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='bandwidth')
    def bandwidth(self, request, pk=None):
        """
        GET /api/resource/pop-points/{id}/bandwidth/
        返回该 POP 点完整的带宽分配明细：
        总带宽 / VPS 占用 / 边缘加速占用 / 可用带宽 / 各资源清单
        """
        pop = self.get_object()

        vps_qs  = pop.vps_list.select_related('customer').order_by('name')
        edge_qs = pop.edge_accelerations.select_related('customer').order_by('name')

        vps_total  = vps_qs.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        edge_total = edge_qs.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        used       = vps_total + edge_total
        available  = max(0, pop.total_bandwidth_mbps - used)
        pct        = round(used / pop.total_bandwidth_mbps * 100, 1) if pop.total_bandwidth_mbps else 0

        return Response({
            'pop_id':   pop.id,
            'pop_code': pop.code,
            'pop_name': pop.name,
            'total_bandwidth_mbps':     pop.total_bandwidth_mbps,
            'vps_allocated_mbps':       vps_total,
            'edge_allocated_mbps':      edge_total,
            'used_bandwidth_mbps':      used,
            'available_bandwidth_mbps': available,
            'usage_pct':                pct,
            'vps_count':  vps_qs.count(),
            'edge_count': edge_qs.count(),
            'vps_list':   VPSBandwidthItemSerializer(vps_qs,  many=True).data,
            'edge_list':  EdgeBandwidthItemSerializer(edge_qs, many=True).data,
        })

    @action(detail=True, methods=['patch'], url_path='set_bandwidth')
    def set_bandwidth(self, request, pk=None):
        """
        PATCH /api/resource/pop-points/{id}/set_bandwidth/
        Body: { "total_bandwidth_mbps": 10000 }
        设置 POP 点总带宽容量，并校验是否低于已分配带宽。
        """
        pop   = self.get_object()
        total = request.data.get('total_bandwidth_mbps')

        # 参数校验
        if not isinstance(total, int) or total < 0:
            return Response({'detail': '请提供有效的带宽值（非负整数 Mbps）'}, status=status.HTTP_400_BAD_REQUEST)

        # 计算当前已分配带宽
        vps_bw  = pop.vps_list.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        edge_bw = pop.edge_accelerations.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        used = vps_bw + edge_bw

        if total < used:
            return Response(
                {
                    'detail': (
                        f'设置的总带宽 {total} Mbps 低于已分配带宽 {used} Mbps'
                        f'（VPS {vps_bw} + 边缘加速 {edge_bw}），请先调整下属资源带宽。'
                    ),
                    'current_used_mbps': used,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_total = pop.total_bandwidth_mbps
        pop.total_bandwidth_mbps = total
        pop.save(update_fields=['total_bandwidth_mbps', 'updated_at'])

        self._log(
            request,
            ResourceOperationLog.ActionType.UPDATE,
            f'POP 点 [{pop.code}] 总带宽: {old_total} → {total} Mbps',
            target_id=pop.pk, target_repr=str(pop), pop_point=pop,
            old_value={'total_bandwidth_mbps': old_total},
            new_value={'total_bandwidth_mbps': total},
        )
        if total > old_total:
            retry_paid_orders_after_capacity_change(
                pop,
                operator=request.user,
                reason=f'POP 总带宽已扩容至 {total} Mbps',
            )
        return Response({
            'id':                    pop.id,
            'total_bandwidth_mbps':  pop.total_bandwidth_mbps,
            'used_bandwidth_mbps':   used,
            'available_bandwidth_mbps': max(0, total - used),
        })

    @action(detail=True, methods=['post'], url_path='resume-paid-provisioning')
    def resume_paid_provisioning(self, request, pk=None):
        """资源调整完成后，立即重试该 POP 中因资源不足而暂停的已付款订单。"""
        pop = self.get_object()
        from service_manager.capacity_recovery import retry_insufficient_paid_orders_for_pop

        result = retry_insufficient_paid_orders_for_pop(
            pop,
            operator=request.user,
            reason='资源管理人员确认资源调整完成',
        )
        self._log(
            request,
            ResourceOperationLog.ActionType.OTHER,
            (
                f'POP 点 [{pop.code}] 重试资源不足的已付款工单：'
                f'检查 {result["checked"]} 个，已触发 {result["retried"]} 个'
            ),
            target_id=pop.pk,
            target_repr=str(pop),
            pop_point=pop,
            new_value=result,
        )
        return Response(result)

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """全局统计，带宽数据从 VPS + EdgeAcceleration 实时汇总"""
        qs = PopPoint.objects.all()

        # 带宽：通过子查询聚合所有 POP 下 VPS + Edge 实际分配
        vps_bw_total  = VPS.objects.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0
        edge_bw_total = EdgeAcceleration.objects.aggregate(t=Sum('bandwidth_mbps'))['t'] or 0

        return Response({
            'total':              qs.count(),
            'by_status':          list(qs.values('status').annotate(count=Count('id'))),
            'by_network_type':    list(qs.values('network_type').annotate(count=Count('id'))),
            'by_asn_nature':      list(qs.values('asn_nature').annotate(count=Count('id'))),
            'by_region':          list(
                qs.values('city__country__region__name').annotate(count=Count('id'))
            ),
            'total_bandwidth_mbps': qs.aggregate(total=Sum('total_bandwidth_mbps'))['total'] or 0,
            'vps_allocated_mbps':   vps_bw_total,
            'edge_allocated_mbps':  edge_bw_total,
            'used_bandwidth_mbps':  vps_bw_total + edge_bw_total,
        })

    @action(detail=False, methods=['get'], url_path='map-overview')
    def map_overview(self, request):
        """Dashboard 世界地图：按国家/城市信息返回 POP 节点及带宽摘要。"""
        qs = self.get_queryset().exclude(status=PopPoint.PopStatus.DECOMMISSIONED)
        points = []
        unmapped = []
        by_type = {}
        by_status = {}

        for pop in qs:
            by_type[pop.pop_type] = by_type.get(pop.pop_type, 0) + 1
            by_status[pop.status] = by_status.get(pop.status, 0) + 1

            city = pop.city
            country = city.country if city and city.country_id else None
            country_name = country.name if country else ''
            country_name_en = (country.name_en or '') if country else ''
            city_name = city.name if city else ''
            city_name_en = (city.name_en or '') if city else ''

            if not city or not country_name or not city_name:
                unmapped.append({
                    'id': pop.id,
                    'code': pop.code,
                    'name': pop.name,
                    'pop_type': pop.pop_type,
                    'pop_type_label': pop.get_pop_type_display(),
                    'city_name': city_name,
                    'country_name': country_name,
                })
                continue

            vps_bw = getattr(pop, 'ann_vps_bw', 0) or 0
            edge_bw = getattr(pop, 'ann_edge_bw', 0) or 0
            used = vps_bw + edge_bw
            total = pop.total_bandwidth_mbps or 0
            pct = round(used / total * 100, 1) if total else 0

            points.append({
                'id': pop.id,
                'code': pop.code,
                'name': pop.name,
                'pop_type': pop.pop_type,
                'pop_type_label': pop.get_pop_type_display(),
                'status': pop.status,
                'status_label': pop.get_status_display(),
                'city_name': city_name,
                'city_name_en': city_name_en,
                'country_name': country_name,
                'country_name_en': country_name_en,
                'region_name': country.region.name if country.region_id else '',
                'mgmt_ip': pop.mgmt_ip or '',
                'total_bandwidth_mbps': total,
                'vps_bandwidth_mbps': vps_bw,
                'edge_bandwidth_mbps': edge_bw,
                'used_bandwidth_mbps': used,
                'available_bandwidth_mbps': max(0, total - used),
                'bw_usage_pct': pct,
                'vps_count': getattr(pop, 'ann_vps_count', 0) or 0,
                'edge_count': getattr(pop, 'ann_edge_count', 0) or 0,
            })

        return Response({
            'map': {'width': 950, 'height': 620},
            'points': points,
            'unmapped': unmapped,
            'summary': {
                'total': qs.count(),
                'mapped': len(points),
                'unmapped': len(unmapped),
                'by_type': by_type,
                'by_status': by_status,
            },
        })

    # ── 部署 / 同步 ───────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='test_ssh')
    def test_ssh(self, request, pk=None):
        """
        POST /api/resource/pop-points/{id}/test_ssh/
        Body 可选覆盖：{ host, port, username, password }
        测试到节点的 SSH 连通性，并探测系统信息 / 是否已安装 pemanager。
        """
        import paramiko
        pop = self.get_object()
        host = request.data.get('host') or pop.mgmt_ip
        port = int(request.data.get('port') or pop.ssh_port or 22)
        username = request.data.get('username') or pop.mgmt_username or 'root'
        password = request.data.get('password') or pop.mgmt_password or ''
        if not host:
            return Response({'detail': '缺少节点地址（host / 管理 IP）'},
                            status=status.HTTP_400_BAD_REQUEST)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, port=port, username=username, password=password,
                           timeout=12, banner_timeout=12, auth_timeout=12,
                           look_for_keys=False, allow_agent=False)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'SSH 连接失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        info = {}
        try:
            for key, cmd in (
                ('uname', 'uname -a'),
                ('os', 'cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2'),
                ('pemanager', 'cat /opt/pemanager/VERSION 2>/dev/null || systemctl is-active pemanager-backend 2>/dev/null'),
            ):
                _in, out, _err = client.exec_command(cmd, timeout=8)
                info[key] = out.read().decode('utf-8', 'replace').strip().strip('"')
        finally:
            client.close()
        return Response({'ok': True, 'detail': 'SSH 连接成功', 'info': info})

    @action(detail=True, methods=['post'], url_path='upload_package',
            parser_classes=[MultiPartParser, FormParser])
    def upload_package(self, request, pk=None):
        """
        POST /api/resource/pop-points/{id}/upload_package/   (multipart)
        字段：file=<部署包 tar.gz>
        保存到服务端临时目录，返回文件名供 WebSSH 部署时引用。
        """
        pop = self.get_object()
        f = request.FILES.get('file')
        if not f:
            return Response({'detail': '未收到文件（字段名 file）'},
                            status=status.HTTP_400_BAD_REQUEST)
        if f.size > settings.POP_DEPLOY_MAX_UPLOAD:
            return Response({'detail': f'文件超过上限 {settings.POP_DEPLOY_MAX_UPLOAD // (1024*1024)} MB'},
                            status=status.HTTP_400_BAD_REQUEST)
        name = os.path.basename(f.name)
        if not (name.endswith('.tar.gz') or name.endswith('.tgz')):
            return Response({'detail': '仅支持 .tar.gz / .tgz 部署包'},
                            status=status.HTTP_400_BAD_REQUEST)
        os.makedirs(settings.POP_DEPLOY_UPLOAD_DIR, exist_ok=True)
        # 以 pop 前缀隔离不同节点，避免重名覆盖
        stored = f'pop{pop.pk}_{name}'
        dest = os.path.join(settings.POP_DEPLOY_UPLOAD_DIR, stored)
        with open(dest, 'wb') as fh:
            for chunk in f.chunks():
                fh.write(chunk)
        pop.deploy_status = PopPoint.DeployStatus.DEPLOYING
        pop.save(update_fields=['deploy_status', 'updated_at'])
        self._log(
            request, ResourceOperationLog.ActionType.DEPLOY,
            f'POP 点 [{pop.code}] 上传部署包 {name}（{f.size} 字节）',
            target_id=pop.pk, target_repr=str(pop), pop_point=pop,
        )
        return Response({'ok': True, 'package': stored, 'size': f.size,
                         'remote_dir': settings.POP_DEPLOY_REMOTE_DIR})

    @action(detail=True, methods=['post'], url_path='set_deploy_status')
    def set_deploy_status(self, request, pk=None):
        """POST {status: deployed|failed|deploying|undeployed, agent_version?}"""
        pop = self.get_object()
        new = request.data.get('status')
        valid = [c[0] for c in PopPoint.DeployStatus.choices]
        if new not in valid:
            return Response({'detail': f'无效部署状态，可选：{valid}'},
                            status=status.HTTP_400_BAD_REQUEST)
        pop.deploy_status = new
        fields = ['deploy_status', 'updated_at']
        if new == PopPoint.DeployStatus.DEPLOYED:
            pop.last_deploy_at = timezone.now()
            fields.append('last_deploy_at')
        if request.data.get('agent_version'):
            pop.agent_version = str(request.data['agent_version'])[:64]
            fields.append('agent_version')
        pop.save(update_fields=fields)
        self._log(
            request, ResourceOperationLog.ActionType.DEPLOY,
            f'POP 点 [{pop.code}] 部署状态 → {pop.get_deploy_status_display()}',
            target_id=pop.pk, target_repr=str(pop), pop_point=pop,
        )
        return Response(PopPointListSerializer(pop).data)

    @action(detail=True, methods=['post'], url_path='sync_from_node')
    def sync_from_node(self, request, pk=None):
        """
        POST /api/resource/pop-points/{id}/sync_from_node/
        从节点 pemanager API 拉取系统现场数据并同步入库（接口/路由/策略/IP/带宽）。
        """
        pop = self.get_object()
        try:
            result = sync_pop_from_node(pop)
        except PemanagerError as e:
            msg = str(e)
            if 'CERTIFICATE_VERIFY_FAILED' in msg:
                msg += '。节点按 IP 访问时请关闭「校验节点 HTTPS 证书」。'
            return Response({'ok': False, 'detail': msg},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'同步异常：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        sm = result.get('summary', {})
        brief = '、'.join(
            f'{k}:{v.get("total", v.get("fetched", 0))}'
            for k, v in sm.items() if isinstance(v, dict)
        )
        self._log(
            request, ResourceOperationLog.ActionType.IMPORT,
            f'POP 点 [{pop.code}] 从节点同步系统数据（{brief}）',
            target_id=pop.pk, target_repr=str(pop), pop_point=pop,
            new_value=result['summary'],
        )
        return Response(result)

    @action(detail=True, methods=['post'], url_path='push_to_node')
    def push_to_node(self, request, pk=None):
        """
        POST /api/resource/pop-points/{id}/push_to_node/
        Body 可选：{ sections: ["static_routes","policy_rules","edge_tunnels","bandwidth"],
                     dry_run: true }
        把 ISPmanager 维护的配置下发到节点。dry_run=true（默认）仅返回下发计划，
        不实际写节点；前端二次确认后带 dry_run=false 真正下发并即时生效。
        """
        pop = self.get_object()
        sections = request.data.get('sections') or None
        dry_run = request.data.get('dry_run', True)
        dry_run = bool(dry_run) if not isinstance(dry_run, str) else dry_run.lower() not in ('false', '0', 'no')
        try:
            result = push_pop_to_node(pop, sections=sections, dry_run=dry_run)
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'下发异常：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        # 仅在真正下发（非 dry-run）时记录操作日志
        if not dry_run:
            sm = result.get('summary', {})
            brief = '、'.join(
                f'{k}:下发{v.get("pushed", 0)}'
                for k, v in sm.items() if isinstance(v, dict)
            )
            self._log(
                request, ResourceOperationLog.ActionType.DEPLOY,
                f'POP 点 [{pop.code}] 下发配置到节点（{brief}）',
                target_id=pop.pk, target_repr=str(pop), pop_point=pop,
                new_value=result['summary'],
            )
        return Response(result)

    @action(detail=True, methods=['get'], url_path='pve_resources')
    def pve_resources(self, request, pk=None):
        """GET /api/resource/pop-points/{id}/pve_resources/  节点 PVE 主机/节点/模板下拉数据"""
        pop = self.get_object()
        try:
            return Response(pve_ops.list_pve_resources(pop))
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'查询节点资源失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='create_vm')
    def create_vm(self, request, pk=None):
        """
        POST /api/resource/pop-points/{id}/create_vm/
        Body: { from_template?: bool, dry_run?: bool, ...创建参数 }
        在节点上创建虚机（from_template=true 走模板/备份创建），dry_run 默认 true。
        备份恢复为节点异步任务时返回 job_id，前端应轮询 pve_job。
        """
        pop = self.get_object()
        payload = dict(request.data or {})
        dry_run = bool(payload.pop('dry_run', True))
        from_template = bool(payload.pop('from_template', False))
        payload.pop('source_type', None)
        try:
            if from_template:
                result = pve_ops.create_from_template(pop, payload, dry_run=dry_run)
            else:
                result = pve_ops.create_vm(pop, payload, dry_run=dry_run)
        except PemanagerError as e:
            logger.warning('POP[%s] 节点创建虚机失败: %s', pop.code, e)
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            logger.exception('POP[%s] 节点创建虚机异常', pop.code)
            return Response({'ok': False, 'detail': f'创建虚机失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not dry_run:
            self._log(
                request, ResourceOperationLog.ActionType.CREATE,
                f'POP 点 [{pop.code}] 在节点创建虚机',
                target_id=pop.pk, target_repr=str(pop), pop_point=pop,
                new_value=payload,
            )
        return Response(result)

    @action(detail=True, methods=['get'], url_path='pve_job/(?P<job_id>[^/.]+)')
    def pve_job(self, request, pk=None, job_id=None):
        """GET /api/resource/pop-points/{id}/pve_job/{job_id}/  查询节点异步创建任务"""
        pop = self.get_object()
        try:
            return Response(pve_ops.poll_pve_job(pop, job_id))
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'查询节点任务失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)


# ═══════════════════════════════════════════════════════════════
# § 4  网络基础设施
# ═══════════════════════════════════════════════════════════════

class NetworkInterfaceViewSet(ResourceSystemPermissionMixin, ResourceLogMixin, viewsets.ModelViewSet):
    queryset = NetworkInterface.objects.select_related('pop_point').all()
    serializer_class = NetworkInterfaceSerializer

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问网络基础设施')
        return super().get_queryset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['pop_point', 'interface_type', 'status', 'is_management']
    search_fields = ['ifname', 'mac_address', 'description']
    log_target_model = ResourceOperationLog.TargetModel.INTERFACE

    def perform_create(self, serializer):
        instance = serializer.save()
        mark_pop_dirty(instance.pop_point)
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建接口 {instance.ifname} @ {instance.pop_point}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point,
        )
        if str(self.request.query_params.get('push_to_node', '')).lower() in ('1', 'true', 'yes'):
            self._try_push_interface(instance)

    def perform_update(self, serializer):
        old = resource_model_snapshot(serializer.instance)
        previous_pop_id = serializer.instance.pop_point_id
        instance = serializer.save()
        if previous_pop_id and previous_pop_id != instance.pop_point_id:
            # 迁移后的旧 POP 也要标记为待同步，使下次下发停用遗留的品质探针。
            previous_pop = PopPoint.objects.filter(pk=previous_pop_id).first()
            if previous_pop is not None:
                mark_pop_dirty(previous_pop)
        mark_pop_dirty(instance.pop_point)
        self._log(
            self.request, ResourceOperationLog.ActionType.UPDATE,
            f'更新接口 {instance.ifname}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point,
            old_value=old, new_value=resource_model_snapshot(instance),
        )
        if str(self.request.query_params.get('push_to_node', '')).lower() in ('1', 'true', 'yes'):
            self._try_push_interface(instance)

    @staticmethod
    def _try_push_interface(instance):
        """供 perform_* 调用；失败时抛 ValidationError。"""
        from .deploy_service import push_interface_to_node
        pop = instance.pop_point
        if pop is None:
            raise DRFValidationError({'detail': '接口未关联 POP 点，无法下发'})
        try:
            res = push_interface_to_node(pop, instance)
            if not res.get('ok'):
                raise DRFValidationError({'detail': res.get('detail') or '节点下发失败'})
        except PemanagerError as e:
            raise DRFValidationError({'detail': f'节点下发失败：{e}'})

    @action(detail=True, methods=['post'], url_path='push_to_node')
    def push_to_node(self, request, pk=None):
        """POST /api/resource/interfaces/{id}/push_to_node/ 将 netplan_config 下发到节点。"""
        iface = self.get_object()
        if iface.pop_point is None:
            return Response({'detail': '接口未关联 POP 点'}, status=status.HTTP_400_BAD_REQUEST)
        from .deploy_service import push_interface_to_node
        try:
            result = push_interface_to_node(iface.pop_point, iface)
        except PemanagerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not result.get('ok'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        from django.utils import timezone
        PopPoint.objects.filter(pk=iface.pop_point_id).update(
            config_dirty=False, last_push_at=timezone.now())
        return Response(result)

    def perform_destroy(self, instance):
        # 删除接口默认同步传播到节点（delete_on_node=false 可仅删除本地记录）。
        # 节点删除失败时不删除本地记录并返回错误，避免下次同步又把接口拉回造成"删除无效"。
        delete_on_node = str(
            self.request.query_params.get('delete_on_node', 'true')
        ).lower() in ('1', 'true', 'yes')
        pop = instance.pop_point
        ifname = instance.ifname
        iftype = instance.interface_type
        node_detail = ''
        if delete_on_node and pop and ifname:
            from .deploy_service import delete_interface_on_node
            try:
                res = delete_interface_on_node(pop, ifname, iftype)
                node_detail = res.get('detail', '')
            except PemanagerError as e:
                raise DRFValidationError({'detail': f'节点删除接口失败：{e}'})
            except Exception as e:  # noqa: BLE001
                raise DRFValidationError({'detail': f'节点删除接口异常：{e}'})
        snap = resource_model_snapshot(instance)
        self._log(
            self.request, ResourceOperationLog.ActionType.DELETE,
            f'删除接口 {ifname}' + (f'（{node_detail}）' if node_detail else ''),
            target_id=instance.pk, target_repr=str(instance),
            pop_point=pop, old_value=snap,
        )
        instance.delete()


class RouteTableViewSet(ResourceSystemPermissionMixin, ResourceLogMixin, viewsets.ModelViewSet):
    queryset = RouteTable.objects.prefetch_related('static_routes').select_related('pop_point', 'vps').all()
    serializer_class = RouteTableSerializer

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问网络基础设施')
        return super().get_queryset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['owner_type', 'pop_point', 'vps']
    search_fields = ['name', 'description']
    log_target_model = ResourceOperationLog.TargetModel.ROUTE_TABLE

    def perform_create(self, serializer):
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建路由表 table {instance.table_id} ({instance.name})',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, vps=instance.vps,
        )


class StaticRouteViewSet(ResourceSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = StaticRoute.objects.select_related(
        'route_table__pop_point', 'interface',
    ).all()
    serializer_class = StaticRouteSerializer

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问网络基础设施')
        return super().get_queryset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['route_table', 'is_active']
    search_fields = ['destination', 'gateway', 'description']

    @staticmethod
    def _route_pop(instance):
        rt = getattr(instance, 'route_table', None)
        return getattr(rt, 'pop_point', None) if rt else None

    def _maybe_push_static_route(self, instance, *, deleting=False):
        from .deploy_service import (
            push_static_route_to_node,
            remove_static_route_from_node,
        )
        if not request_wants_node_push(self.request, default=True):
            return
        pop = self._route_pop(instance)
        if pop and not pop_has_api_config(pop):
            return
        if deleting:
            result = remove_static_route_from_node(instance)
        else:
            result = push_static_route_to_node(instance)
        _raise_if_node_push_failed(result, '静态路由下发节点失败')

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            instance = StaticRoute.objects.select_related(
                'route_table__pop_point', 'interface',
            ).get(pk=instance.pk)
            mark_pop_dirty(self._route_pop(instance))
            self._maybe_push_static_route(instance)

    def perform_update(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            instance = StaticRoute.objects.select_related(
                'route_table__pop_point', 'interface',
            ).get(pk=instance.pk)
            mark_pop_dirty(self._route_pop(instance))
            self._maybe_push_static_route(instance)

    def perform_destroy(self, instance):
        with transaction.atomic():
            self._maybe_push_static_route(instance, deleting=True)
            pop = self._route_pop(instance)
            instance.delete()
            mark_pop_dirty(pop)


class PolicyRouteViewSet(ResourceSystemPermissionMixin, ResourceLogMixin, viewsets.ModelViewSet):
    queryset = PolicyRoute.objects.select_related('pop_point', 'vps', 'lookup_table').all()
    serializer_class = PolicyRouteSerializer

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问网络基础设施')
        return super().get_queryset()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['pop_point', 'vps', 'action', 'is_active']
    search_fields = ['src_prefix', 'dst_prefix', 'description']
    ordering = ['priority']
    log_target_model = ResourceOperationLog.TargetModel.POLICY_ROUTE

    def _maybe_push_policy_route(self, instance, *, deleting=False):
        from .deploy_service import (
            push_policy_route_to_node,
            remove_policy_route_from_node,
        )
        if not request_wants_node_push(self.request, default=True):
            return
        if instance.pop_point_id and not pop_has_api_config(instance.pop_point):
            return
        if deleting:
            result = remove_policy_route_from_node(instance)
        else:
            result = push_policy_route_to_node(instance)
        _raise_if_node_push_failed(result, '策略路由下发节点失败')

    def perform_create(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            instance = PolicyRoute.objects.select_related(
                'pop_point', 'lookup_table',
            ).get(pk=instance.pk)
            mark_pop_dirty(instance.pop_point)
            self._log(
                self.request, ResourceOperationLog.ActionType.CREATE,
                f'新建策略路由 prio {instance.priority}',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=instance.pop_point, vps=instance.vps,
            )
            self._maybe_push_policy_route(instance)

    def perform_update(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            instance = PolicyRoute.objects.select_related(
                'pop_point', 'lookup_table',
            ).get(pk=instance.pk)
            mark_pop_dirty(instance.pop_point)
            self._log(
                self.request, ResourceOperationLog.ActionType.UPDATE,
                f'更新策略路由 prio {instance.priority}',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=instance.pop_point, vps=instance.vps,
            )
            self._maybe_push_policy_route(instance)

    def perform_destroy(self, instance):
        with transaction.atomic():
            self._maybe_push_policy_route(instance, deleting=True)
            pop = instance.pop_point
            self._log(
                self.request, ResourceOperationLog.ActionType.DELETE,
                f'删除策略路由 prio {instance.priority}',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=pop, vps=instance.vps,
            )
            instance.delete()
            mark_pop_dirty(pop)


# ═══════════════════════════════════════════════════════════════
# § 5  IP 管理
# ═══════════════════════════════════════════════════════════════

class IPBlockViewSet(
    ExcelIOMixin,
    ResourceLocationFilterMixin,
    ResourceSystemPermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    IP 地址块 CRUD + IP 解析

    GET  /api/resource/ip-blocks/{id}/              详情（含所有 IP 列表）
    POST /api/resource/ip-blocks/{id}/generate_ips/ 根据 CIDR 自动解析并写入所有 IP 地址
    GET  /api/resource/ip-blocks/{id}/preview_ips/  预览 CIDR 会生成多少 IP（不写库）
    """
    excel_export_fn = export_ip_blocks
    excel_import_fn = import_ip_blocks
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = [
        'pop_point', 'ip_version', 'ip_attribute', 'carrier', 'is_active',
        'network_type', 'asn_nature',
        'pop_point__city__country__region',
    ]
    search_fields = ['cidr', 'description']
    log_target_model = ResourceOperationLog.TargetModel.IP_BLOCK

    # 单次生成 IP 的绝对上限（超过则拒绝，防止 /8 等超大段写崩数据库）
    MAX_GENERATE_IPS = 65536  # /16 = 65534

    def get_queryset(self):
        qs = ip_scope.annotate_ip_block_usage(
            ip_scope.exclude_tunnel_blocks(
                IPBlock.objects.select_related(
                    'pop_point__city__country__region',
                    'pop_point__availability_zone',
                    'carrier',
                )
            )
        ).order_by(
            'pop_point__city__country__region__name',
            'pop_point__city__country__name',
            'pop_point__city__name',
            'pop_point__name',
            'cidr',
            'id',
        )
        return apply_supplier_scope(
            self.request, qs, lambda s: supplier_scope_via_pop_q(s, 'pop_point'),
        )

    def list(self, request, *args, **kwargs):
        queryset = ip_scope.group_ip_blocks(list(self.filter_queryset(self.get_queryset())))
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return IPBlockDetailSerializer
        return IPBlockSerializer

    def perform_create(self, serializer):
        _ensure_supplier_pop(self.request, serializer.validated_data.get('pop_point'))
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建 IP 地址块 {instance.cidr}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point,
        )

    def perform_update(self, serializer):
        pop = serializer.validated_data.get('pop_point', serializer.instance.pop_point)
        _ensure_supplier_pop(self.request, pop)
        instance = serializer.save()
        self._log(
            self.request, ResourceOperationLog.ActionType.UPDATE,
            f'更新 IP 地址块 {instance.cidr}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point,
        )

    # ── 自定义动作 ──────────────────────────────────────────────

    def _push_new_ips_to_node(self, block, created_objs, push=True):
        if not push or not created_objs:
            return None
        try:
            addrs = [str(o.address) for o in created_objs if o.address]
            return ip_ops.push_ips_bulk_create(
                block.pop_point, addrs,
                state='available',
                subnet_label=block.description or block.cidr,
            )
        except PemanagerError as e:
            return {'errors': [str(e)]}

    @action(detail=True, methods=['get'], url_path='preview_ips')
    def preview_ips(self, request, pk=None):
        """
        预览：根据 CIDR 计算将生成多少个 IP 地址（不写入数据库）。
        前端新增地址块后先调此接口显示数量，由用户决定是否正式生成。
        """
        import ipaddress
        block = self.get_object()
        try:
            network = ipaddress.ip_network(block.cidr, strict=False)
        except ValueError as e:
            return Response({'detail': f'无效的 CIDR: {e}'}, status=400)

        is_v4 = isinstance(network, ipaddress.IPv4Network)
        prefix = network.prefixlen
        total_addrs = network.num_addresses

        # 可用主机数（排除网络地址和广播地址，IPv4 /31 /32 特殊处理）
        if is_v4:
            if prefix >= 31:
                usable = total_addrs
            else:
                usable = total_addrs - 2  # 去掉网络地址和广播地址
        else:
            usable = total_addrs - 1  # IPv6 去掉网络地址（第一个）

        sibling_ids = ip_scope.sibling_business_block_ids(block)
        already_exists = IPAddress.objects.filter(ip_block_id__in=sibling_ids).count()
        would_create = max(0, usable - already_exists)

        return Response({
            'cidr': block.cidr,
            'ip_version': block.ip_version,
            'prefix_len': prefix,
            'total_addresses': total_addrs,
            'usable_hosts': usable,
            'already_in_db': already_exists,
            'would_create': would_create,
            'exceeds_limit': usable > self.MAX_GENERATE_IPS,
            'limit': self.MAX_GENERATE_IPS,
            'network_address': str(network.network_address),
            'broadcast_address': str(network.broadcast_address) if is_v4 else None,
        })

    @action(detail=True, methods=['post'], url_path='generate_ips')
    def generate_ips(self, request, pk=None):
        """
        根据 CIDR 自动解析并批量写入该地址块内所有可用 IP 地址。
        - 已存在的 IP 跳过（幂等）
        - IPv4 /31 /32 特殊处理（不去掉网络/广播）
        - 超过 MAX_GENERATE_IPS 拒绝执行（防止误操作超大段）
        - 使用 bulk_create(ignore_conflicts=True) 提升写入性能

        请求体（可选）:
          include_network   bool  是否包含网络地址（默认 False）
          include_broadcast bool  是否包含广播地址（默认 False）
          description       str   写入每条 IP 的描述（默认空）
        """
        import ipaddress
        block = self.get_object()

        include_network   = bool(request.data.get('include_network', False))
        include_broadcast = bool(request.data.get('include_broadcast', False))
        ip_description    = request.data.get('description', '')

        try:
            network = ipaddress.ip_network(block.cidr, strict=False)
        except ValueError as e:
            return Response({'detail': f'无效的 CIDR: {e}'}, status=400)

        is_v4  = isinstance(network, ipaddress.IPv4Network)
        prefix = network.prefixlen

        # 构建待写入的 IP 列表
        if is_v4 and prefix >= 31:
            # /31 点对点链路, /32 单主机：全部写入
            ip_list = list(network)
        elif is_v4:
            all_ips = list(network)
            # 默认排除第一个（网络地址）和最后一个（广播地址）
            start_idx = 0 if include_network   else 1
            end_idx   = None if include_broadcast else -1
            ip_list   = all_ips[start_idx:end_idx]
        else:
            # IPv6：只排除网络地址（第一个），无广播概念
            all_ips = list(network)
            ip_list = all_ips if include_network else all_ips[1:]

        usable_count = len(ip_list)
        if usable_count > self.MAX_GENERATE_IPS:
            return Response({
                'detail': (
                    f'该 CIDR ({block.cidr}) 包含 {usable_count} 个可用地址，'
                    f'超过单次生成上限 {self.MAX_GENERATE_IPS}。'
                    f'请拆分为更小的地址块后再操作。'
                )
            }, status=400)

        # 查出已存在的 IP（用于 skip 统计）
        sibling_ids = ip_scope.sibling_business_block_ids(block)
        existing_addrs = set(
            IPAddress.objects.filter(ip_block_id__in=sibling_ids).values_list('address', flat=True)
        )

        # 构建待插入对象（过滤已存在）
        to_create = [
            IPAddress(
                ip_block=block,
                address=str(ip),
                prefix_len=prefix,
                gateway=block.gateway,
                status=IPAddress.IPStatus.AVAILABLE,
                description=ip_description,
            )
            for ip in ip_list
            if str(ip) not in existing_addrs
        ]

        # 批量写入，ignore_conflicts 保证幂等
        created_objs = IPAddress.objects.bulk_create(
            to_create,
            batch_size=500,
            ignore_conflicts=True,
        )
        created_count = len(created_objs)
        skipped_count = usable_count - len(to_create)

        self._log(
            request,
            ResourceOperationLog.ActionType.IMPORT,
            f'自动解析 IP 块 {block.cidr}：新增 {created_count} 条，跳过 {skipped_count} 条（已存在）',
            target_id=block.pk,
            target_repr=str(block),
            pop_point=block.pop_point,
            new_value={
                'cidr': block.cidr,
                'created': created_count,
                'skipped': skipped_count,
                'total_in_db': block.ip_addresses.count(),
            },
        )

        node_push = self._push_new_ips_to_node(
            block, created_objs,
            push=bool(request.data.get('push_to_node', True)),
        )
        if created_count:
            retry_paid_orders_after_capacity_change(
                block.pop_point,
                operator=request.user,
                reason=f'新增 {created_count} 个可用 IP 地址',
            )

        return Response({
            'cidr': block.cidr,
            'created': created_count,
            'skipped': skipped_count,
            'total_in_db': block.ip_addresses.count(),
            'node_push': node_push,
            'message': f'成功解析 {block.cidr}，新增 {created_count} 个 IP 地址',
        })


class IPAddressViewSet(
    ExcelIOMixin,
    ResourceLocationFilterMixin,
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    IP 地址 CRUD + 分配/释放

    POST /api/resource/ip-addresses/{id}/assign/   分配给 VPS 或边缘加速
    POST /api/resource/ip-addresses/{id}/release/  从目标释放
    POST /api/resource/ip-addresses/batch_import/  批量导入（按 CIDR 段生成）
    """
    excel_export_fn = export_ip_addresses
    excel_import_fn = import_ip_addresses
    serializer_class = IPAddressSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'ip_block', 'status', 'is_primary', 'assigned_vps', 'assigned_edge',
        'ip_block__ip_version', 'ip_block__pop_point', 'ip_block__cidr',
        'ip_block__network_type', 'ip_block__asn_nature',
        'ip_block__pop_point__city__country__region',
    ]
    search_fields = ['address', 'description']
    ordering = [
        'ip_block__pop_point__city__country__region__name',
        'ip_block__pop_point__city__country__name',
        'ip_block__pop_point__city__name',
        'ip_block__pop_point__name',
        'ip_block__cidr',
        'address',
    ]
    log_target_model = ResourceOperationLog.TargetModel.IP_ADDRESS
    location_region_lookup = 'ip_block__pop_point__city__country__region'
    location_country_lookup = 'ip_block__pop_point__city__country'
    location_city_lookup = 'ip_block__pop_point__city'
    location_zone_lookup = 'ip_block__pop_point__availability_zone'
    location_pop_lookup = 'ip_block__pop_point'

    def get_customer_scope_q(self, customer):
        return Q(assigned_vps__customer=customer) | Q(assigned_edge__customer=customer)

    def get_queryset(self):
        qs = ip_scope.exclude_tunnel_addresses(
            IPAddress.objects.select_related(
                'ip_block__pop_point__city__country__region',
                'ip_block__pop_point__availability_zone',
                'assigned_vps__customer', 'assigned_edge__customer',
            )
        ).order_by(
            'ip_block__pop_point__city__country__region__name',
            'ip_block__pop_point__city__country__name',
            'ip_block__pop_point__city__name',
            'ip_block__pop_point__name',
            'ip_block__cidr',
            'address',
        )
        qs = self.apply_customer_scope(qs).distinct()
        return apply_supplier_scope(
            self.request, qs, lambda s: supplier_scope_via_pop_q(s, 'ip_block__pop_point'),
        )

    def perform_create(self, serializer):
        ip_block = serializer.validated_data.get('ip_block')
        if ip_block is not None:
            _ensure_supplier_pop(self.request, ip_block.pop_point)
        instance = serializer.save()
        pop = instance.ip_block.pop_point
        self._log(
            self.request,
            ResourceOperationLog.ActionType.CREATE,
            f'新建 IP 地址 {instance.address}',
            target_id=instance.pk,
            target_repr=str(instance),
            pop_point=pop,
            new_value=resource_model_snapshot(instance),
        )
        if instance.status == IPAddress.IPStatus.AVAILABLE:
            retry_paid_orders_after_capacity_change(
                pop,
                operator=self.request.user,
                reason=f'新增可用 IP {instance.address}',
            )

    def perform_update(self, serializer):
        ip_block = serializer.validated_data.get('ip_block', serializer.instance.ip_block)
        if ip_block is not None:
            _ensure_supplier_pop(self.request, ip_block.pop_point)
        old = resource_model_snapshot(serializer.instance)
        old_status = serializer.instance.status
        instance = serializer.save()
        pop = instance.ip_block.pop_point
        self._log(
            self.request,
            ResourceOperationLog.ActionType.STATUS_CHANGE
            if old_status != instance.status else ResourceOperationLog.ActionType.UPDATE,
            f'更新 IP 地址 {instance.address}',
            target_id=instance.pk,
            target_repr=str(instance),
            pop_point=pop,
            old_value=old,
            new_value=resource_model_snapshot(instance),
        )
        if instance.status == IPAddress.IPStatus.AVAILABLE and old_status != instance.status:
            retry_paid_orders_after_capacity_change(
                pop,
                operator=self.request.user,
                reason=f'IP {instance.address} 已调整为可用',
            )

    @action(detail=True, methods=['post'], url_path='assign')
    def assign(self, request, pk=None):
        ip = self.get_object()
        if ip.status == IPAddress.IPStatus.ASSIGNED:
            return Response({'detail': 'IP 已被分配，请先释放'}, status=400)
        ser = IPAddressAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        target_type = data['target_type']
        target_id = data['target_id']
        pop = ip.ip_block.pop_point

        vps = edge = None
        if target_type == 'vps':
            try:
                vps = VPS.objects.get(pk=target_id)
            except VPS.DoesNotExist:
                return Response({'detail': f'VPS #{target_id} 不存在'}, status=400)
            if vps.pop_point_id != pop.id:
                return Response({'detail': 'VPS 不属于该 IP 所在 POP 节点'}, status=400)
        else:
            try:
                edge = EdgeAcceleration.objects.select_related(
                    'gre_config', 'vxlan_config', 'wg_config',
                ).get(pk=target_id)
            except EdgeAcceleration.DoesNotExist:
                return Response({'detail': f'边缘加速 #{target_id} 不存在'}, status=400)
            if edge.pop_point_id != pop.id:
                return Response({'detail': '边缘加速不属于该 IP 所在 POP 节点'}, status=400)

        push_to_node = data.get('push_to_node', True)
        create_route = data.get('create_route', True)
        route_override = data.get('route') or None
        if data.get('interface_code'):
            route_override = dict(route_override or {})
            route_override['interface_name'] = data['interface_code']
        service_before = _linked_service_snapshot(vps=vps, edge=edge)

        try:
            node_payload = ip_ops.assign_ip_local_and_node(
                ip,
                target_type=target_type,
                vps=vps,
                edge=edge,
                is_primary=data['is_primary'],
                assigned_at=data.get('assigned_at'),
                push_to_node=push_to_node,
                create_route=create_route if target_type == 'edge' else False,
                route_override=route_override,
                operator=request.user,
                request=request,
                source='IP管理-分配',
            )
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        except PemanagerError as e:
            return Response({'detail': f'节点下发失败：{e}'}, status=400)

        ip.refresh_from_db()
        service_instance = _sync_service_instance_ip(
            ip,
            vps=vps,
            edge=edge,
            operator=request.user,
            request=request,
            before_state=service_before,
        )
        return Response({
            **IPAddressSerializer(ip).data,
            'node_push': node_payload,
            'service_instance_id': service_instance.pk if service_instance else None,
        })

    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        ip = self.get_object()
        if ip.status != IPAddress.IPStatus.ASSIGNED:
            return Response({'detail': 'IP 当前未被分配'}, status=400)
        old_vps = ip.assigned_vps
        old_edge = ip.assigned_edge
        service_before = _linked_service_snapshot(vps=old_vps, edge=old_edge)
        push_to_node = bool(request.data.get('push_to_node', True))

        try:
            node_payload = ip_ops.release_ip_local_and_node(
                ip,
                push_to_node=push_to_node,
                operator=request.user,
                request=request,
                source='IP管理-释放',
            )
        except PemanagerError as e:
            return Response({'detail': f'节点释放失败：{e}'}, status=400)

        ip.refresh_from_db()
        service_instance = _sync_service_instance_ip(
            ip,
            vps=old_vps,
            edge=old_edge,
            operator=request.user,
            request=request,
            released=True,
            before_state=service_before,
        )
        retry_paid_orders_after_capacity_change(
            ip.ip_block.pop_point,
            operator=request.user,
            reason=f'已释放 IP {ip.address}',
        )
        return Response({
            **IPAddressSerializer(ip).data,
            'node_push': node_payload,
            'service_instance_id': service_instance.pk if service_instance else None,
        })

    @action(detail=False, methods=['post'], url_path='bulk_create')
    def bulk_create_ips(self, request):
        """录入 IP：单条 / 不连续列表 / 起止 / CIDR，可带掩码与网关。"""
        import ipaddress
        from .ip_net import cidr_contains, expand_cidr_hosts, gateway_in_cidr, netmask_from_prefix, parse_network

        ser = IPBulkCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            block = IPBlock.objects.select_related('pop_point').get(pk=data['ip_block'])
        except IPBlock.DoesNotExist:
            return Response({'detail': 'IP 地址块不存在'}, status=400)

        items = []
        mode = data.get('mode', 'single')
        cidr = (data.get('cidr') or '').strip()
        prefix = data.get('prefix_len')
        if prefix == 0:
            prefix = None
        try:
            block_net = parse_network(block.cidr)
            block_prefix = block_net.prefixlen
        except ValueError as exc:
            return Response({'detail': f'地址块 CIDR 无效：{exc}'}, status=400)

        if mode == 'cidr' or cidr:
            cidr = cidr or block.cidr
            try:
                child_net = parse_network(cidr)
            except ValueError as exc:
                return Response({'detail': f'无效的 CIDR：{exc}'}, status=400)
            if not cidr_contains(block.cidr, cidr):
                return Response(
                    {'detail': f'CIDR {cidr} 必须属于地址块 {block.cidr}'},
                    status=400,
                )
            prefix = prefix or child_net.prefixlen
            items, usable = expand_cidr_hosts(
                cidr,
                include_network=bool(data.get('include_network')),
                include_broadcast=bool(data.get('include_broadcast')),
                limit=1024,
            )
            if usable > 1024:
                return Response({
                    'detail': f'该 CIDR 含 {usable} 个可用地址，单次最多录入 1024 条，请拆段或到地址块「解析 IP」',
                }, status=400)
        elif mode == 'single' and data.get('address'):
            items = [str(data['address'])]
        elif mode == 'range' and data.get('start') and data.get('end'):
            start = ipaddress.ip_address(str(data['start']))
            end = ipaddress.ip_address(str(data['end']))
            if int(end) < int(start):
                return Response({'detail': '结束 IP 必须 >= 起始 IP'}, status=400)
            cur = int(start)
            while cur <= int(end):
                items.append(str(ipaddress.ip_address(cur)))
                cur += 1
                if len(items) > 1024:
                    return Response({'detail': '单次最多录入 1024 条'}, status=400)
        elif data.get('addresses'):
            items = [str(a) for a in data['addresses']]
        else:
            return Response(
                {'detail': '请提供 address / start+end / addresses / cidr'},
                status=400,
            )

        if len(items) > 1024:
            return Response({'detail': '单次最多录入 1024 条'}, status=400)

        if mode != 'cidr' and not cidr:
            outside = []
            for addr in items:
                try:
                    if ipaddress.ip_address(addr) not in block_net:
                        outside.append(addr)
                except ValueError:
                    outside.append(addr)
            if outside:
                shown = ', '.join(outside[:8])
                extra = f' 等 {len(outside)} 条' if len(outside) > 8 else ''
                return Response(
                    {'detail': f'以下地址不属于 {block.cidr}：{shown}{extra}'},
                    status=400,
                )

        if prefix is None:
            prefix = block_prefix
        gateway = data.get('gateway')
        if gateway:
            scope = cidr or block.cidr
            if not gateway_in_cidr(str(gateway), scope):
                return Response({'detail': f'网关必须属于网段 {scope}'}, status=400)
            if data.get('update_block_gateway', True) and not block.gateway:
                block.gateway = str(gateway)
                block.save(update_fields=['gateway'])
            elif data.get('update_block_gateway', True) and cidr and cidr == block.cidr:
                block.gateway = str(gateway)
                block.save(update_fields=['gateway'])

        state = data.get('state', 'available')
        subnet_label = data.get('subnet_label', '') or block.description or ''
        isp_status = (
            IPAddress.IPStatus.RESERVED if state == 'reserved'
            else IPAddress.IPStatus.AVAILABLE
        )

        created, skipped, errors = [], [], []
        new_objs = []
        for addr in items:
            try:
                defaults = {
                    'prefix_len': prefix,
                    'status': isp_status,
                    'subnet_label': subnet_label,
                }
                if gateway:
                    defaults['gateway'] = str(gateway)
                obj, is_new = IPAddress.objects.get_or_create(
                    ip_block=block, address=addr,
                    defaults=defaults,
                )
                if is_new:
                    created.append(addr)
                    new_objs.append(obj)
                else:
                    skipped.append({'address': addr, 'reason': '已存在'})
            except Exception as e:  # noqa: BLE001
                errors.append({'address': addr, 'error': str(e)})

        node_push = None
        if data.get('push_to_node', True) and new_objs:
            node_push = ip_ops.push_ips_bulk_create(
                block.pop_point, created,
                state=state,
                subnet_label=subnet_label,
            )

        self._log(
            request, ResourceOperationLog.ActionType.IMPORT,
            f'录入 IP {block.cidr}：新增 {len(created)} 条',
            pop_point=block.pop_point,
        )
        if created and isp_status == IPAddress.IPStatus.AVAILABLE:
            retry_paid_orders_after_capacity_change(
                block.pop_point,
                operator=request.user,
                reason=f'批量录入 {len(created)} 个可用 IP 地址',
            )
        return Response({
            'ok': not errors,
            'created': created,
            'skipped': skipped,
            'errors': errors,
            'prefix_len': prefix,
            'gateway': str(gateway) if gateway else (str(block.gateway) if block.gateway else ''),
            'netmask': netmask_from_prefix(prefix, block.ip_version),
            'node_push': node_push,
        })

    @action(detail=False, methods=['post'], url_path='batch_import')
    def batch_import(self, request):
        """批量导入：根据 ip_block_id 和 IP 列表批量创建 IPAddress 记录"""
        ip_block_id = request.data.get('ip_block_id')
        addresses = request.data.get('addresses', [])
        prefix_len = request.data.get('prefix_len', 32)

        if not ip_block_id or not addresses:
            return Response({'detail': 'ip_block_id 和 addresses 不能为空'}, status=400)
        try:
            ip_block = IPBlock.objects.get(pk=ip_block_id)
        except IPBlock.DoesNotExist:
            return Response({'detail': 'IP 地址块不存在'}, status=400)

        created, skipped = [], []
        for addr in addresses:
            obj, is_new = IPAddress.objects.get_or_create(
                ip_block=ip_block, address=addr,
                defaults={'prefix_len': prefix_len, 'status': 'available'},
            )
            (created if is_new else skipped).append(addr)

        self._log(
            request, ResourceOperationLog.ActionType.IMPORT,
            f'批量导入 IP：{ip_block.cidr} 新增 {len(created)} 条',
            pop_point=ip_block.pop_point,
        )
        if created:
            retry_paid_orders_after_capacity_change(
                ip_block.pop_point,
                operator=request.user,
                reason=f'批量导入 {len(created)} 个可用 IP 地址',
            )
        return Response({'created': len(created), 'skipped': len(skipped),
                         'created_ips': created, 'skipped_ips': skipped})


class TunnelIPPoolViewSet(
    ExcelIOMixin,
    ResourceLocationFilterMixin,
    ResourceSystemPermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    隧道 IP 地址段（p2p /30 递增分配池）管理 —— 对齐 vpopmanager TunnelIPPool。

    GET  /api/resource/tunnel-ip-pools/                列表（含 total/used/free_blocks、next_free）
    GET  /api/resource/tunnel-ip-pools/{id}/usage/     用量详情（已占用 p2p 块 + 下一段可用）
    GET  /api/resource/tunnel-ip-pools/{id}/next-free/ 下一段可用 p2p 块
    POST /api/resource/tunnel-ip-pools/{id}/push_to_node/  下发到所属 POP 节点（Body: {dry_run?}）
    """
    excel_export_fn = export_tunnels
    excel_import_fn = import_tunnels
    serializer_class = TunnelIPPoolSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = [
        'pop_point', 'customer', 'is_active', 'is_synced',
        'pop_point__city__country__region',
    ]
    search_fields = ['name', 'cidr', 'remark']
    log_target_model = ResourceOperationLog.TargetModel.IP_BLOCK

    def get_queryset(self):
        taken_statuses = (
            IPAddress.IPStatus.ASSIGNED,
            IPAddress.IPStatus.RESERVED,
            IPAddress.IPStatus.BLOCKED,
            IPAddress.IPStatus.SYSTEM,
        )
        qs = (
            TunnelIPPool.objects
            .select_related(
                'pop_point__city__country__region',
                'pop_point__availability_zone',
                'customer',
            )
            .prefetch_related(
                Prefetch(
                    'pop_point__ip_blocks__ip_addresses',
                    queryset=IPAddress.objects.filter(status__in=taken_statuses).select_related(
                        'assigned_edge__customer',
                        'assigned_vps__customer',
                    ),
                ),
                Prefetch(
                    'pop_point__edge_accelerations',
                    queryset=EdgeAcceleration.objects.exclude(node_ifname='').select_related('customer'),
                ),
            )
            .order_by(
                'pop_point__city__country__region__name',
                'pop_point__city__country__name',
                'pop_point__city__name',
                'pop_point__name',
                'name',
            )
        )
        return apply_supplier_scope(
            self.request, qs, lambda s: supplier_scope_via_pop_q(s, 'pop_point'),
        )

    def _maybe_push_pool(self, instance, *, deleting=False):
        from .deploy_service import (
            push_tunnel_ip_pool_to_node,
            remove_tunnel_ip_pool_from_node,
        )
        if not request_wants_node_push(self.request, default=True):
            return
        if instance.pop_point_id and not pop_has_api_config(instance.pop_point):
            return
        if deleting:
            result = remove_tunnel_ip_pool_from_node(instance)
        else:
            result = push_tunnel_ip_pool_to_node(instance, dry_run=False)
        _raise_if_node_push_failed(result, '隧道地址段下发节点失败')

    def perform_create(self, serializer):
        _ensure_supplier_pop(self.request, serializer.validated_data.get('pop_point'))
        with transaction.atomic():
            instance = serializer.save()
            instance = TunnelIPPool.objects.select_related('pop_point', 'customer').get(pk=instance.pk)
            mark_pop_dirty(instance.pop_point)
            self._log(
                self.request, ResourceOperationLog.ActionType.CREATE,
                f'新建隧道 IP 地址段 {instance.name}（{instance.cidr}）',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=instance.pop_point,
            )
            self._maybe_push_pool(instance)

    def perform_update(self, serializer):
        pop = serializer.validated_data.get('pop_point', serializer.instance.pop_point)
        _ensure_supplier_pop(self.request, pop)
        with transaction.atomic():
            instance = serializer.save()
            instance = TunnelIPPool.objects.select_related('pop_point', 'customer').get(pk=instance.pk)
            mark_pop_dirty(instance.pop_point)
            self._log(
                self.request, ResourceOperationLog.ActionType.UPDATE,
                f'更新隧道 IP 地址段 {instance.name}（{instance.cidr}）',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=instance.pop_point,
            )
            self._maybe_push_pool(instance)

    def perform_destroy(self, instance):
        with transaction.atomic():
            self._maybe_push_pool(instance, deleting=True)
            self._log(
                self.request, ResourceOperationLog.ActionType.DELETE,
                f'删除隧道 IP 地址段 {instance.name}（{instance.cidr}）',
                target_id=instance.pk, target_repr=str(instance),
                pop_point=instance.pop_point,
            )
            instance.delete()
            mark_pop_dirty(instance.pop_point)

    @action(detail=True, methods=['get'], url_path='usage')
    def usage(self, request, pk=None):
        from . import tunnel_ip
        pool = self.get_object()
        return Response(tunnel_ip.usage(pool))

    @action(detail=True, methods=['get'], url_path='next-free')
    def next_free(self, request, pk=None):
        from . import tunnel_ip
        pool = self.get_object()
        block = tunnel_ip.find_next_free_block(pool)
        if block is None:
            return Response({'detail': '该地址段已无可用 p2p 块'}, status=status.HTTP_409_CONFLICT)
        return Response(block)

    @action(detail=True, methods=['post'], url_path='push_to_node')
    def push_to_node(self, request, pk=None):
        """把该隧道地址段下发到所属 POP 节点（在节点侧幂等创建/更新同名池）。"""
        pool = self.get_object()
        dry_run = bool(request.data.get('dry_run', True))
        from .deploy_service import push_tunnel_ip_pool_to_node
        try:
            result = push_tunnel_ip_pool_to_node(pool, dry_run=dry_run)
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'下发异常：{e}'}, status=status.HTTP_400_BAD_REQUEST)
        if not dry_run and result.get('ok'):
            pool.refresh_from_db()
            self._log(
                request, ResourceOperationLog.ActionType.DEPLOY,
                f'隧道 IP 地址段 [{pool.name}] 下发到节点',
                target_id=pool.pk, target_repr=str(pool),
                pop_point=pool.pop_point, new_value=result.get('detail'),
            )
        return Response(result)


# ═══════════════════════════════════════════════════════════════
# § 6  VPS
# ═══════════════════════════════════════════════════════════════

class VPSViewSet(
    ResourceLocationFilterMixin,
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    VPS CRUD + 自定义操作

    POST /api/resource/vps/{id}/change_status/   变更状态
    GET  /api/resource/vps/{id}/logs/            操作日志
    GET  /api/resource/vps/{id}/ip_addresses/    IP 列表
    GET  /api/resource/vps/statistics/           统计
    """
    extra_read_actions = frozenset({'logs', 'statistics', 'ip_addresses', 'vnc_ticket', 'billing_preview'})

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'status', 'os_type', 'pop_point', 'customer', 'is_synced', 'product',
        'pop_point__city__country__region',
    ]
    search_fields = ['vps_no', 'name', 'mgmt_ip', 'customer__name', 'source_node']
    ordering_fields = ['created_at', 'name', 'vcpu', 'ram_mb']
    ordering = [
        'pop_point__city__country__region__name',
        'pop_point__city__country__name',
        'pop_point__city__name',
        'pop_point__name',
        'name',
    ]
    log_target_model = ResourceOperationLog.TargetModel.VPS

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问 VPS 管理')
        qs = VPS.objects.select_related(
            'pop_point__city__country__region',
            'pop_point__availability_zone',
            'customer',
            'created_by',
        ).prefetch_related('ip_addresses', 'vps_interfaces').all()
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return VPSDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return VPSWriteSerializer
        return VPSListSerializer

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建 VPS [{instance.vps_no}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, vps=instance,
        )

    def perform_update(self, serializer):
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        bw_changed = 'bandwidth_mbps' in serializer.validated_data
        if bw_changed:
            action_type = ResourceOperationLog.ActionType.BW_ALLOC
            desc = (
                f'VPS [{instance.vps_no}] 带宽 '
                f'{old.get("bandwidth_mbps")}→{instance.bandwidth_mbps}Mbps'
            )
        elif 'status' in serializer.validated_data:
            action_type = ResourceOperationLog.ActionType.STATUS_CHANGE
            desc = f'更新 VPS [{instance.vps_no}] {instance.name}'
        else:
            action_type = ResourceOperationLog.ActionType.UPDATE
            desc = f'更新 VPS [{instance.vps_no}] {instance.name}'
        self._log(
            self.request, action_type, desc,
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, vps=instance,
            old_value=old, new_value=resource_model_snapshot(instance),
        )
        retry_paid_orders_after_capacity_change(
            instance.pop_point,
            operator=self.request.user,
            reason=f'VPS {instance.vps_no} 规格或状态已调整',
        )
        if bw_changed and str(self.request.query_params.get('push_to_node', '')).lower() in ('1', 'true', 'yes'):
            self._try_push_vps_bandwidth(instance)

    @staticmethod
    def _try_push_vps_bandwidth(vps):
        from .deploy_service import push_vps_bandwidth_to_node
        try:
            res = push_vps_bandwidth_to_node(vps)
            if not res.get('ok'):
                raise DRFValidationError({'detail': res.get('detail') or '带宽下发失败'})
        except PemanagerError as e:
            raise DRFValidationError({'detail': f'带宽下发失败：{e}'})

    @action(detail=True, methods=['get'], url_path='billing_preview')
    def billing_preview(self, request, pk=None):
        """GET /api/resource/vps/{id}/billing_preview/?product=<id>

        预览该 VPS 按指定（或已关联）产品计费的明细（基础规格 + 超出阶梯）。
        """
        vps = self.get_object()
        from product_manager import services as product_services
        product_id = request.query_params.get('product') or vps.product_id
        if not product_id:
            return Response({'detail': '未关联产品', 'product_id': None})
        from product_manager.models import Product
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return Response({'detail': '产品不存在'}, status=status.HTTP_404_NOT_FOUND)
        spec = product_services.spec_from_vps(vps)
        price = product_services.compute_price(product, spec)
        base = float(price['base_monthly_price'])
        total = float(price['monthly_price'])
        return Response({
            'product_id': product.id, 'product_name': product.name,
            'currency': price['currency'], 'spec': spec,
            'base_monthly': price['base_monthly_price'],
            'overage_monthly': str(round(total - base, 2)),
            'monthly_price': price['monthly_price'],
            'breakdown': price['breakdown'],
        })

    @action(detail=True, methods=['post'], url_path='push_bandwidth')
    def push_bandwidth(self, request, pk=None):
        """POST /api/resource/vps/{id}/push_bandwidth/ 将带宽下发到节点 VM 网卡。"""
        vps = self.get_object()
        from .deploy_service import push_vps_bandwidth_to_node
        try:
            result = push_vps_bandwidth_to_node(vps)
        except PemanagerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        if not result.get('ok'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        if vps.pop_point_id:
            from django.utils import timezone
            PopPoint.objects.filter(pk=vps.pop_point_id).update(
                config_dirty=False, last_push_at=timezone.now())
        self._log(
            request, ResourceOperationLog.ActionType.DEPLOY,
            f'VPS [{vps.vps_no}] 带宽 {vps.bandwidth_mbps}Mbps 已下发到节点',
            target_id=vps.pk, target_repr=str(vps),
            pop_point=vps.pop_point, vps=vps,
            new_value={'bandwidth_mbps': vps.bandwidth_mbps, 'node_result': result},
        )
        return Response(result)

    def perform_destroy(self, instance):
        snap = resource_model_snapshot(instance)
        pop = instance.pop_point
        self._log(
            self.request, ResourceOperationLog.ActionType.DELETE,
            f'删除 VPS [{instance.vps_no}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=pop, old_value=snap,
        )
        instance.delete()
        retry_paid_orders_after_capacity_change(
            pop,
            operator=self.request.user,
            reason=f'VPS {snap.get("vps_no") or "资源"} 已删除并释放容量',
        )

    @action(detail=True, methods=['post'], url_path='change_status')
    def change_status(self, request, pk=None):
        vps = self.get_object()
        ser = StatusChangeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data['status']
        valid = [c[0] for c in VPS.VPSStatus.choices]
        if new_status not in valid:
            return Response({'detail': f'无效状态，可选：{valid}'}, status=400)
        old_status = vps.status
        vps.status = new_status
        vps.save(update_fields=['status', 'updated_at'])
        self._log(
            request, ResourceOperationLog.ActionType.STATUS_CHANGE,
            f'VPS [{vps.vps_no}] 状态: {old_status} → {new_status}',
            target_id=vps.pk, target_repr=str(vps),
            pop_point=vps.pop_point, vps=vps,
            old_value={'status': old_status}, new_value={'status': new_status},
        )
        return Response(VPSListSerializer(vps).data)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        vps = self.get_object()
        qs = vps.operation_logs.select_related('operator').order_by('-created_at')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ResourceOperationLogSerializer(page, many=True).data)
        return Response(ResourceOperationLogSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='ip_addresses')
    def ip_addresses(self, request, pk=None):
        vps = self.get_object()
        qs = vps.ip_addresses.select_related('ip_block').all()
        return Response(IPAddressSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        qs = VPS.objects.all()
        return Response({
            'total': qs.count(),
            'synced': qs.filter(is_synced=True).count(),
            'by_status': list(qs.values('status').annotate(count=Count('id'))),
            'by_os': list(qs.values('os_type').annotate(count=Count('id'))),
            'total_vcpu': qs.aggregate(total=Sum('vcpu'))['total'] or 0,
            'total_ram_mb': qs.aggregate(total=Sum('ram_mb'))['total'] or 0,
            'total_disk_gb': qs.aggregate(total=Sum('disk_gb'))['total'] or 0,
        })

    # ── PVE 生命周期（通过节点 pvemanager） ───────────────────
    @action(detail=True, methods=['post'], url_path='power')
    def power(self, request, pk=None):
        """
        POST /api/resource/vps/{id}/power/
        Body: { action: start|stop|restart|shutdown, dry_run?: bool }
        """
        vps = self.get_object()
        op = request.data.get('action')
        dry_run = bool(request.data.get('dry_run', True))
        try:
            result = pve_ops.vm_power(vps, op, dry_run=dry_run)
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'电源操作失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not dry_run:
            vps.refresh_from_db()
            self._log(
                request, ResourceOperationLog.ActionType.STATUS_CHANGE,
                f'VPS [{vps.vps_no}] 节点电源操作 {op}',
                target_id=vps.pk, target_repr=str(vps),
                pop_point=vps.pop_point, vps=vps, new_value={'action': op},
            )
        return Response(result)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone(self, request, pk=None):
        """POST /api/resource/vps/{id}/clone/  Body: { newname?, newid?, full?, storage?, dry_run? }"""
        vps = self.get_object()
        payload = dict(request.data or {})
        dry_run = bool(payload.pop('dry_run', True))
        try:
            result = pve_ops.clone_vm(vps, payload, dry_run=dry_run)
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'克隆失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not dry_run:
            self._log(
                request, ResourceOperationLog.ActionType.CREATE,
                f'VPS [{vps.vps_no}] 克隆',
                target_id=vps.pk, target_repr=str(vps),
                pop_point=vps.pop_point, vps=vps, new_value=payload,
            )
        return Response(result)

    @action(detail=True, methods=['get'], url_path='vnc_ticket')
    def vnc_ticket(self, request, pk=None):
        """
        GET /api/resource/vps/{id}/vnc_ticket/
        向所属 POP 节点申请 PVE noVNC 票据（触发节点 vncproxy，30 秒内须建立 WS）。
        返回 { ticket, password, port, ws_path }，前端立即用 ws_path 建立 noVNC 连接。
        """
        vps = self.get_object()
        if not vps.source_ref:
            return Response({'detail': '该 VPS 非节点同步虚机或缺少节点主键，无法使用 VNC 控制台'},
                            status=status.HTTP_400_BAD_REQUEST)
        pop = vps.pop_point
        from .pemanager_client import PemanagerClient
        try:
            client, _user = PemanagerClient.connect_pop(pop)
            data = client.vnc_ticket(vps.source_ref) or {}
        except PemanagerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'detail': f'获取 VNC 票据失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        ticket = data.get('ticket') or ''
        port = data.get('port')
        if not ticket or not port:
            return Response({'detail': '节点未返回 VNC 票据/端口'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            ws_url = client.console_websocket_url(vps.source_ref, ticket, port)
        except PemanagerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        response = Response({
            'ticket': ticket,
            'password': data.get('password') or ticket,
            'port': port,
            'ws_url': ws_url,
        })
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        return response

    @action(detail=True, methods=['post'], url_path='sync_now')
    def sync_now(self, request, pk=None):
        """POST /api/resource/vps/{id}/sync_now/  从节点刷新单台 VPS 状态与带宽"""
        vps = self.get_object()
        pop = vps.pop_point
        pop_label = (
            f'{pop.name}（{pop.api_base_url or pop.mgmt_ip}）'
            if pop else '未知 POP'
        )
        try:
            result = sync_single_vps_from_node(vps)
        except PemanagerError as e:
            msg = str(e)
            if '连接节点失败' in msg or 'timed out' in msg.lower():
                detail = (
                    f'无法连接 POP 节点 {pop_label}：{msg}。'
                    '请确认节点 API 在线，且 ISPmanager 服务器能访问该地址（防火墙/安全组）。'
                )
                code = status.HTTP_502_BAD_GATEWAY
            else:
                detail = f'{pop_label}：{msg}'
                code = status.HTTP_400_BAD_REQUEST
            return Response(
                {'ok': False, 'detail': detail, 'pop_point': pop.pk if pop else None},
                status=code,
            )
        except Exception as e:  # noqa: BLE001
            return Response(
                {'ok': False, 'detail': f'同步失败：{e}', 'pop_point': pop.pk if pop else None},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not result.get('ok'):
            errs = result.get('errors') or {}
            detail = '；'.join(str(v) for v in errs.values() if v) or '同步未完成'
            return Response({**result, 'detail': detail}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    def destroy(self, request, *args, **kwargs):
        """删除 VPS；?delete_on_node=true 时联动删除节点虚机。"""
        vps = self.get_object()
        flag = str(request.query_params.get('delete_on_node', '')).lower()
        if flag in ('true', '1', 'yes') and vps.source_ref:
            try:
                pve_ops.delete_vm(vps, dry_run=False)
            except PemanagerError as e:
                return Response({'ok': False, 'detail': f'节点删除失败：{e}'},
                                status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:  # noqa: BLE001
                return Response({'ok': False, 'detail': f'节点删除异常：{e}'},
                                status=status.HTTP_400_BAD_REQUEST)
            # 节点删除后本地记录可能已被刷新清理
            if not VPS.objects.filter(pk=vps.pk).exists():
                return Response(status=status.HTTP_204_NO_CONTENT)
        return super().destroy(request, *args, **kwargs)


# ═══════════════════════════════════════════════════════════════
# § 6b  VPS 网络接口
# ═══════════════════════════════════════════════════════════════

class VPSNetworkInterfaceViewSet(
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    viewsets.ModelViewSet,
):
    customer_scope_field = 'vps__customer'
    serializer_class = VPSNetworkInterfaceSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['vps', 'is_primary']

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问 VPS 管理')
        qs = VPSNetworkInterface.objects.select_related('vps').all()
        return self.apply_customer_scope(qs)


# ═══════════════════════════════════════════════════════════════
# § 7  边缘加速
# ═══════════════════════════════════════════════════════════════

class EdgeAccelerationViewSet(
    ResourceLocationFilterMixin,
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    """
    边缘加速 CRUD + 自定义操作

    POST /api/resource/edge-accelerations/{id}/change_status/  变更状态
    GET  /api/resource/edge-accelerations/{id}/logs/           操作日志
    GET  /api/resource/edge-accelerations/{id}/ip_addresses/   IP 列表
    PUT  /api/resource/edge-accelerations/{id}/protocol_config/ 更新协议配置
    GET  /api/resource/edge-accelerations/statistics/          统计
    """
    extra_read_actions = frozenset({'logs', 'statistics', 'ip_addresses', 'billing_preview'})

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        'protocol', 'status', 'pop_point', 'customer', 'is_synced', 'product',
        'pop_point__city__country__region',
    ]
    search_fields = ['edge_no', 'name', 'pop_point__name', 'customer__name']
    ordering = [
        'pop_point__city__country__region__name',
        'pop_point__city__country__name',
        'pop_point__city__name',
        'pop_point__name',
        'name',
    ]
    log_target_model = ResourceOperationLog.TargetModel.EDGE

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问边缘加速管理')
        qs = EdgeAcceleration.objects.select_related(
            'pop_point__city__country__region',
            'pop_point__availability_zone',
            'customer',
            'route_table',
            'created_by',
        ).all()
        return self.apply_customer_scope(qs)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return EdgeAccelerationDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return EdgeAccelerationWriteSerializer
        return EdgeAccelerationListSerializer

    @action(detail=True, methods=['get'], url_path='billing_preview')
    def billing_preview(self, request, pk=None):
        """预览该边缘加速按指定（或已关联）产品计费的明细。"""
        edge = self.get_object()
        from product_manager import services as product_services
        product_id = request.query_params.get('product') or edge.product_id
        if not product_id:
            return Response({'detail': '未关联产品', 'product_id': None})
        from product_manager.models import Product
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return Response({'detail': '产品不存在'}, status=status.HTTP_404_NOT_FOUND)
        spec = product_services.spec_from_edge(edge)
        price = product_services.compute_price(product, spec)
        base = float(price['base_monthly_price'])
        total = float(price['monthly_price'])
        return Response({
            'product_id': product.id, 'product_name': product.name,
            'currency': price['currency'], 'spec': spec,
            'base_monthly': price['base_monthly_price'],
            'overage_monthly': str(round(total - base, 2)),
            'monthly_price': price['monthly_price'],
            'breakdown': price['breakdown'],
        })

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        mark_pop_dirty(instance.pop_point)
        self._log(
            self.request, ResourceOperationLog.ActionType.CREATE,
            f'新建边缘加速 [{instance.edge_no}] {instance.name} ({instance.get_protocol_display()})',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, edge=instance,
        )

    def perform_update(self, serializer):
        old = resource_model_snapshot(serializer.instance)
        instance = serializer.save()
        mark_pop_dirty(instance.pop_point)
        if 'bandwidth_mbps' in serializer.validated_data:
            action_type = ResourceOperationLog.ActionType.BW_ALLOC
            desc = (
                f'边缘 [{instance.edge_no}] 带宽 '
                f'{old.get("bandwidth_mbps")}→{instance.bandwidth_mbps}Mbps'
            )
        elif 'status' in serializer.validated_data:
            action_type = ResourceOperationLog.ActionType.STATUS_CHANGE
            desc = f'更新边缘加速 [{instance.edge_no}] {instance.name}'
        elif 'protocol' in serializer.validated_data:
            action_type = ResourceOperationLog.ActionType.CONFIG_CHANGE
            desc = f'更新边缘加速 [{instance.edge_no}] {instance.name}'
        else:
            action_type = ResourceOperationLog.ActionType.UPDATE
            desc = f'更新边缘加速 [{instance.edge_no}] {instance.name}'
        self._log(
            self.request, action_type, desc,
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, edge=instance,
            old_value=old, new_value=resource_model_snapshot(instance),
        )
        retry_paid_orders_after_capacity_change(
            instance.pop_point,
            operator=self.request.user,
            reason=f'边缘加速 {instance.edge_no} 规格或状态已调整',
        )

    def perform_destroy(self, instance):
        snap = resource_model_snapshot(instance)
        pop = instance.pop_point
        self._log(
            self.request, ResourceOperationLog.ActionType.DELETE,
            f'删除边缘加速 [{instance.edge_no}] {instance.name}',
            target_id=instance.pk, target_repr=str(instance),
            pop_point=instance.pop_point, old_value=snap,
        )
        instance.delete()
        mark_pop_dirty(pop)
        retry_paid_orders_after_capacity_change(
            pop,
            operator=self.request.user,
            reason=f'边缘加速 {snap.get("edge_no") or "资源"} 已删除并释放容量',
        )

    @action(detail=True, methods=['post'], url_path='change_status')
    def change_status(self, request, pk=None):
        edge = self.get_object()
        ser = StatusChangeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_status = ser.validated_data['status']
        valid = [c[0] for c in EdgeAcceleration.EdgeStatus.choices]
        if new_status not in valid:
            return Response({'detail': f'无效状态，可选：{valid}'}, status=400)
        old_status = edge.status
        edge.status = new_status
        edge.save(update_fields=['status', 'updated_at'])
        self._log(
            request, ResourceOperationLog.ActionType.STATUS_CHANGE,
            f'边缘加速 [{edge.edge_no}] 状态: {old_status} → {new_status}',
            target_id=edge.pk, target_repr=str(edge),
            pop_point=edge.pop_point, edge=edge,
            old_value={'status': old_status}, new_value={'status': new_status},
        )
        return Response(EdgeAccelerationListSerializer(edge).data)

    @action(detail=True, methods=['get'], url_path='logs')
    def logs(self, request, pk=None):
        edge = self.get_object()
        qs = edge.operation_logs.select_related('operator').order_by('-created_at')
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(ResourceOperationLogSerializer(page, many=True).data)
        return Response(ResourceOperationLogSerializer(qs, many=True).data)

    @action(detail=True, methods=['get'], url_path='ip_addresses')
    def ip_addresses(self, request, pk=None):
        edge = self.get_object()
        qs = edge.ip_addresses.select_related('ip_block').all()
        return Response(IPAddressSerializer(qs, many=True).data)

    @action(detail=True, methods=['put', 'patch'], url_path='protocol_config')
    def protocol_config(self, request, pk=None):
        """更新协议专属配置（GRE / WireGuard / Socks5）"""
        edge = self.get_object()
        protocol_map = {
            'gre': (GREConfig, GREConfigSerializer, 'gre_config'),
            'vxlan': (VXLANConfig, VXLANConfigSerializer, 'vxlan_config'),
            'wireguard': (WireguardConfig, WireguardConfigSerializer, 'wg_config'),
            'socks5': (Socks5Config, Socks5ConfigSerializer, 'socks5_config'),
        }
        if edge.protocol not in protocol_map:
            return Response({'detail': '未知协议'}, status=400)

        model_cls, ser_cls, rel_name = protocol_map[edge.protocol]
        config = getattr(edge, rel_name, None)
        partial = request.method == 'PATCH'

        if config:
            ser = ser_cls(config, data=request.data, partial=partial)
        else:
            data = {**request.data, 'edge': edge.pk}
            ser = ser_cls(data=data)

        ser.is_valid(raise_exception=True)
        instance = ser.save()
        mark_pop_dirty(edge.pop_point)

        self._log(
            request, ResourceOperationLog.ActionType.CONFIG_CHANGE,
            f'更新 {edge.get_protocol_display()} 配置 [{edge.edge_no}]',
            target_id=edge.pk, target_repr=str(edge),
            pop_point=edge.pop_point, edge=edge,
        )
        return Response(ser.data)

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        qs = EdgeAcceleration.objects.all()
        return Response({
            'total': qs.count(),
            'synced': qs.filter(is_synced=True).count(),
            'by_status': list(qs.values('status').annotate(count=Count('id'))),
            'by_protocol': list(qs.values('protocol').annotate(count=Count('id'))),
        })

    @action(detail=True, methods=['post'], url_path='push_to_node')
    def push_to_node(self, request, pk=None):
        """
        POST /api/resource/edge-accelerations/{id}/push_to_node/
        Body: { dry_run?: bool }
        把该边缘加速的隧道配置（GRE/WireGuard）下发到所属 POP 节点。
        Socks5 无对应节点隧道通道，不支持下发。dry_run 默认 true。
        """
        edge = self.get_object()
        if edge.protocol == EdgeAcceleration.Protocol.SOCKS5:
            return Response({'ok': False, 'detail': 'Socks5 暂不支持节点下发'},
                            status=status.HTTP_400_BAD_REQUEST)
        dry_run = bool(request.data.get('dry_run', True))
        # 复用 deploy_service 的隧道下发，仅作用于本 POP（其下边缘节点）
        from .deploy_service import push_pop_to_node
        try:
            result = push_pop_to_node(edge.pop_point, sections=['customers', 'edge_tunnels'], dry_run=dry_run)
        except PemanagerError as e:
            return Response({'ok': False, 'detail': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'ok': False, 'detail': f'下发异常：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not dry_run:
            edge.refresh_from_db()
            if edge.node_pushed:
                from .edge_provision import finalize_edge_after_push
                result = dict(result or {})
                result['finalize'] = finalize_edge_after_push(
                    edge, operator=request.user,
                )
            self._log(
                request, ResourceOperationLog.ActionType.DEPLOY,
                f'边缘加速 [{edge.edge_no}] 隧道下发到节点',
                target_id=edge.pk, target_repr=str(edge),
                pop_point=edge.pop_point, edge=edge,
                new_value=result.get('summary'),
            )
        return Response(result)

    @action(detail=False, methods=['post'], url_path='wg_genkey')
    def wg_genkey(self, request):
        """
        POST /api/resource/edge-accelerations/wg_genkey/
        Body: { pop_point: <id> }
        通过所属 POP 节点的 WireGuard 工具生成密钥对，返回 { private, public }。
        前端 WireGuard 表单创建时用于一键生成本端密钥。
        """
        pop_id = request.data.get('pop_point')
        if not pop_id:
            return Response({'detail': '缺少 pop_point 参数'}, status=status.HTTP_400_BAD_REQUEST)
        pop = PopPoint.objects.filter(pk=pop_id).first()
        if pop is None:
            return Response({'detail': 'POP 点不存在'}, status=status.HTTP_400_BAD_REQUEST)
        from .pemanager_client import PemanagerClient
        try:
            client, _user = PemanagerClient.connect_pop(pop)
            data = client.wg_genkey() or {}
        except PemanagerError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:  # noqa: BLE001
            return Response({'detail': f'生成 WireGuard 密钥失败：{e}'},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({
            'private': data.get('private') or data.get('private_key'),
            'public': data.get('public') or data.get('public_key'),
            'source': data.get('source'),
        })


# ═══════════════════════════════════════════════════════════════
# § 8  带宽分配
# ═══════════════════════════════════════════════════════════════

class BandwidthAllocationViewSet(
    ResourceLocationFilterMixin,
    CustomerScopedQuerysetMixin,
    ResourceModulePermissionMixin,
    ResourceLogMixin,
    viewsets.ModelViewSet,
):
    serializer_class = BandwidthAllocationSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = [
        'pop_point', 'target_type', 'vps', 'edge', 'is_active', 'is_synced',
        'pop_point__city__country__region',
    ]
    ordering = [
        'pop_point__city__country__region__name',
        'pop_point__city__country__name',
        'pop_point__city__name',
        'pop_point__name',
        '-created_at',
    ]
    log_target_model = ResourceOperationLog.TargetModel.BW_ALLOC

    def get_customer_scope_q(self, customer):
        return Q(vps__customer=customer) | Q(edge__customer=customer)

    def get_queryset(self):
        deny_supplier_role(self.request.user, '供应商账号无权访问带宽管理')
        qs = BandwidthAllocation.objects.select_related(
            'pop_point__city__country__region',
            'pop_point__availability_zone',
            'vps',
            'edge',
        ).all()
        return self.apply_customer_scope(qs).distinct()

    @staticmethod
    def _request_wants_node_push(request, default=True):
        return request_wants_node_push(request, default=default)

    def _maybe_push_allocation(self, instance, *, deleting=False):
        from .deploy_service import (
            push_bandwidth_allocation_to_node,
            remove_bandwidth_allocation_from_node,
        )
        if not self._request_wants_node_push(self.request, default=True):
            return
        if instance.pop_point_id and not pop_has_api_config(instance.pop_point):
            return
        if deleting:
            result = remove_bandwidth_allocation_from_node(instance)
        else:
            result = push_bandwidth_allocation_to_node(instance)
        _raise_if_node_push_failed(result, '带宽下发节点失败')

    @staticmethod
    def _writeback_target_bandwidth(instance):
        """手工带宽分配回写到目标资源的 bandwidth_mbps，保证 POP「已用」口径统一、避免双计。"""
        if instance.is_synced:
            return  # 同步分配的权威值已由 sync_service 回写资源字段
        if instance.vps_id and instance.vps and instance.vps.bandwidth_mbps != instance.allocated_mbps:
            instance.vps.bandwidth_mbps = instance.allocated_mbps
            instance.vps.save(update_fields=['bandwidth_mbps'])
        elif instance.edge_id and instance.edge and instance.edge.bandwidth_mbps != instance.allocated_mbps:
            instance.edge.bandwidth_mbps = instance.allocated_mbps
            instance.edge.save(update_fields=['bandwidth_mbps'])

    def perform_create(self, serializer):
        # 手工分配按目标 upsert：同一 VPS/边缘已有手工分配时更新而非新建，
        # 防止重复行（MySQL 不支持条件唯一约束，需在应用层兜底）。
        data = serializer.validated_data
        with transaction.atomic():
            if not data.get('is_synced'):
                existing = None
                if data.get('vps'):
                    existing = BandwidthAllocation.objects.filter(
                        is_synced=False, vps=data['vps']).first()
                elif data.get('edge'):
                    existing = BandwidthAllocation.objects.filter(
                        is_synced=False, edge=data['edge']).first()
                if existing is not None:
                    from .resource_audit import log_bandwidth_change, snapshot_bandwidth
                    old_bw = snapshot_bandwidth(existing)
                    instance = serializer.update(existing, data)
                    self._writeback_target_bandwidth(instance)
                    mark_pop_dirty(instance.pop_point)
                    _sync_service_instance_bandwidth(
                        instance,
                        operator=self.request.user,
                        request=self.request,
                        detail=f'资源管理侧更新带宽分配为 {instance.allocated_mbps}Mbps',
                    )
                    log_bandwidth_change(
                        instance,
                        old_value=old_bw,
                        operator=self.request.user,
                        request=self.request,
                        source='带宽管理-调整',
                    )
                    retry_paid_orders_after_capacity_change(
                        instance.pop_point,
                        operator=self.request.user,
                        reason=f'带宽分配已调整为 {instance.allocated_mbps}Mbps',
                    )
                    self._maybe_push_allocation(instance)
                    return
            instance = serializer.save(created_by=self.request.user)
            self._writeback_target_bandwidth(instance)
            mark_pop_dirty(instance.pop_point)
            _sync_service_instance_bandwidth(
                instance,
                operator=self.request.user,
                request=self.request,
                detail=f'资源管理侧创建带宽分配 {instance.allocated_mbps}Mbps',
            )
            from .resource_audit import log_bandwidth_change
            log_bandwidth_change(
                instance,
                operator=self.request.user,
                request=self.request,
                source='带宽管理-分配',
            )
            self._maybe_push_allocation(instance)

    def perform_update(self, serializer):
        old = resource_model_snapshot(serializer.instance)
        with transaction.atomic():
            instance = serializer.save()
            self._writeback_target_bandwidth(instance)
            mark_pop_dirty(instance.pop_point)
            _sync_service_instance_bandwidth(
                instance,
                operator=self.request.user,
                request=self.request,
                detail=f'资源管理侧更新带宽分配为 {instance.allocated_mbps}Mbps',
            )
            from .resource_audit import log_bandwidth_change
            log_bandwidth_change(
                instance,
                old_value=old,
                operator=self.request.user,
                request=self.request,
                source='带宽管理-更新',
            )
            retry_paid_orders_after_capacity_change(
                instance.pop_point,
                operator=self.request.user,
                reason=f'带宽分配已调整为 {instance.allocated_mbps}Mbps',
            )
            self._maybe_push_allocation(instance)

    def perform_destroy(self, instance):
        from .resource_audit import log_bandwidth_change, snapshot_bandwidth
        old_bw = snapshot_bandwidth(instance)
        with transaction.atomic():
            self._maybe_push_allocation(instance, deleting=True)
            log_bandwidth_change(
                instance,
                action='release',
                old_value=old_bw,
                operator=self.request.user,
                request=self.request,
                source='带宽管理-回收',
            )
            instance.delete()

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        qs = self.filter_queryset(BandwidthAllocation.objects.all())
        active = qs.filter(is_active=True)
        # 口径说明：
        #  - synced_mbps：节点同步导入的分配（与 VPS/Edge.bandwidth_mbps 数值一致，POP「已用」即采用资源字段口径）
        #  - manual_mbps：手工分配（尚未并入资源字段，单独展示，避免与 synced 行重复求和误导）
        synced_mbps = active.filter(is_synced=True).aggregate(t=Sum('allocated_mbps'))['t'] or 0
        manual_mbps = active.filter(is_synced=False).aggregate(t=Sum('allocated_mbps'))['t'] or 0
        return Response({
            'total': qs.count(),
            'active': active.count(),
            'synced': qs.filter(is_synced=True).count(),
            'synced_allocated_mbps': synced_mbps,
            'manual_allocated_mbps': manual_mbps,
            # 仅统计有效分配；保留旧字段名以兼容前端，含义为有效分配合计
            'total_allocated_mbps': synced_mbps + manual_mbps,
            'by_target_type': list(active.values('target_type').annotate(
                count=Count('id'), mbps=Sum('allocated_mbps'))),
        })


# ═══════════════════════════════════════════════════════════════
# § 9  操作日志（只读）
# ═══════════════════════════════════════════════════════════════

class ResourceOperationLogViewSet(
    CustomerScopedQuerysetMixin,
    ViewOnlyPermissionMixin,
    viewsets.ReadOnlyModelViewSet,
):
    view_permission = Perm.RESOURCE_VIEW
    serializer_class = ResourceOperationLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['action_type', 'target_model', 'target_id', 'operator', 'pop_point', 'vps', 'edge']
    search_fields = ['description', 'target_repr', 'operator__username']
    ordering = ['-created_at']

    def get_customer_scope_q(self, customer):
        return Q(vps__customer=customer) | Q(edge__customer=customer)

    def get_queryset(self):
        qs = ResourceOperationLog.objects.select_related(
            'operator', 'pop_point', 'vps', 'edge'
        ).all()
        return self.apply_customer_scope(qs).distinct()
