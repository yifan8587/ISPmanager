"""
operation_manager/views.py — 运维监控 API
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from django.db import IntegrityError
from django.utils.crypto import constant_time_compare
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from accounts.drf_mixins import OperationSystemPermissionMixin

from .models import (
    ZabbixServer,
    MonitoringHostTemplate,
    MonitoringHost,
    MonitoringItem,
    MonitoringGraph,
    MonitoringGraphProfile,
    MonitoringGraphSeries,
    MonitoringGraphBinding,
    MonitoringTrafficReport,
    MonitoringSyncLog,
    PopQualityMonitor,
    PopQualityProbe,
    PopTelemetryCredential,
    EdgeInterfaceQualitySample,
    WebSSHCommandTemplate,
    record_monitoring_log,
)
from resource_manager.models import VPS, EdgeAcceleration
from .serializers import (
    ZabbixServerListSerializer,
    ZabbixServerWriteSerializer,
    MonitoringHostTemplateSerializer,
    MonitoringHostListSerializer,
    MonitoringHostDetailSerializer,
    MonitoringHostWriteSerializer,
    MonitoringItemSerializer,
    MonitoringGraphSerializer,
    MonitoringGraphWriteSerializer,
    MonitoringGraphProfileSerializer,
    MonitoringGraphSeriesSerializer,
    MonitoringGraphBindingSerializer,
    MonitoringTrafficReportSerializer,
    MonitoringTrafficReportListSerializer,
    MonitoringSyncLogSerializer,
    WebSSHCommandTemplateSerializer,
    TrafficQuerySerializer,
    GraphDataQuerySerializer,
    ResourceMonitoringPanelSerializer,
    PopQualityMonitorListSerializer,
    PopQualityMonitorDetailSerializer,
    PopQualityMonitorWriteSerializer,
    PopQualityProbeSerializer,
    PopQualityHistoryQuerySerializer,
    PopTelemetryCredentialSerializer,
    PopTelemetryCredentialWriteSerializer,
    EdgeQualityIngestRequestSerializer,
    EdgeQualitySampleInputSerializer,
)
from .traffic_analytics import (
    query_traffic, get_graph_chart_data, get_item_chart_data, unit_convert_bps,
)
from .zabbix_client import ZabbixClient, ZabbixError, get_default_zabbix_server
from .zabbix_provision import (
    fetch_host_groups, fetch_templates, sync_host_full, sync_server_inventory_light,
)
from .zabbix_sync_worker import enqueue_host_sync, enqueue_server_inventory_sync, is_server_sync_running
from .resource_monitoring import (
    get_traffic_resource_context,
    resolve_traffic_from_binding,
    validate_binding_for_resource,
    validate_monitoring_host_for_resource,
)
from .pop_quality import (
    ProbeInProgressError,
    discover_pop_quality_monitors,
    run_monitor_probe,
)
from .throttles import EdgeTelemetryIngestRateThrottle

logger = logging.getLogger(__name__)


def _telemetry_credential_from_request(request):
    """仅通过专用请求头认证 POP 遥测节点，绝不走用户/JWT 认证。"""
    token = (request.headers.get('X-POP-Telemetry-Token') or '').strip()
    if not token or len(token) > 512:
        return None
    digest = PopTelemetryCredential.hash_token(token)
    credential = (
        PopTelemetryCredential.objects.select_related('pop_point')
        .filter(token_hash=digest, is_active=True)
        .first()
    )
    if credential is None or not constant_time_compare(credential.token_hash, digest):
        return None
    return credential


def _safe_ingest_raw(sample: dict) -> dict:
    """只保留协议白名单字段，避免将令牌或节点扩展字段意外持久化。"""
    return {
        'schema': 'edge_quality_v1',
        'sample': {
            'source_sample_id': sample['source_sample_id'],
            'interface_name': sample['interface_name'],
            'target_address': sample['target_address'],
            'sampled_at': sample['sampled_at'].isoformat(),
            'latency_ms': sample.get('latency_ms'),
            'packet_loss_pct': sample.get('packet_loss_pct'),
            'jitter_ms': sample.get('jitter_ms'),
            'packets_sent': sample.get('packets_sent', 0),
            'packets_recv': sample.get('packets_recv', 0),
            'ok': sample['ok'],
        },
    }


class EdgeQualityIngestView(APIView):
    """
    POP 节点向中心上报边缘接口品质样本。

    该接口是唯一允许匿名访问的 operation 路径：认证完全由每 POP 的专用令牌
    完成，不接受 JWT、客户身份、central_edge_id 或客户字段作为资源授权依据。
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [EdgeTelemetryIngestRateThrottle]

    def post(self, request):
        credential = _telemetry_credential_from_request(request)
        if credential is None:
            return Response(
                {'detail': '遥测令牌无效或已停用'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        request_serializer = EdgeQualityIngestRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        raw_samples = request_serializer.validated_data['samples']
        now = timezone.now()
        oldest_allowed = now - timedelta(days=7)
        latest_allowed = now + timedelta(minutes=5)

        # 批量查出本 POP 的接口映射，并在 Python 中再次做大小写敏感的精确匹配。
        interfaces = {
            str(item.get('interface_name') or '').strip()
            for item in raw_samples
            if isinstance(item, dict) and str(item.get('interface_name') or '').strip()
        }
        edges_by_interface: dict[str, list] = defaultdict(list)
        if interfaces:
            candidates = EdgeAcceleration.objects.filter(
                pop_point=credential.pop_point,
                node_ifname__in=interfaces,
            ).select_related('customer')
            for edge in candidates:
                if edge.node_ifname in interfaces:
                    edges_by_interface[edge.node_ifname].append(edge)

        result = {
            'accepted': 0,
            'duplicates': 0,
            'rejected': 0,
            # 计数供管理端快速汇总；样本 ID 供节点上传器在部分成功时精确推进游标。
            'accepted_ids': [],
            'duplicate_ids': [],
            'rejections': [],
        }

        def reject(index: int, reason: str, source_sample_id: str = ''):
            result['rejected'] += 1
            result['rejections'].append({
                'index': index,
                'source_sample_id': source_sample_id,
                'reason': reason,
            })

        for index, raw_sample in enumerate(raw_samples):
            sample_serializer = EdgeQualitySampleInputSerializer(data=raw_sample)
            if not sample_serializer.is_valid():
                reject(
                    index,
                    'invalid_sample',
                    str(raw_sample.get('source_sample_id') or '')[:128],
                )
                continue
            sample = sample_serializer.validated_data
            sampled_at = sample['sampled_at']
            if sampled_at < oldest_allowed or sampled_at > latest_allowed:
                reject(index, 'sampled_at_out_of_range', sample['source_sample_id'])
                continue

            # central_edge_id 完全不参与匹配；同 POP、同 node_ifname 且唯一才可写入。
            matching_edges = edges_by_interface.get(sample['interface_name'], [])
            if len(matching_edges) != 1:
                reject(index, 'unknown_interface', sample['source_sample_id'])
                continue
            edge = matching_edges[0]

            defaults = {
                'edge': edge,
                'customer': edge.customer,
                'interface_name': edge.node_ifname,
                'target_address': sample['target_address'],
                'sampled_at': sampled_at,
                'received_at': now,
                'latency_ms': sample.get('latency_ms'),
                'packet_loss_pct': sample.get('packet_loss_pct'),
                'jitter_ms': sample.get('jitter_ms'),
                'packets_sent': sample.get('packets_sent', 0),
                'packets_recv': sample.get('packets_recv', 0),
                'ok': sample['ok'],
                'raw': _safe_ingest_raw(sample),
            }
            try:
                _record, created = EdgeInterfaceQualitySample.objects.get_or_create(
                    pop=credential.pop_point,
                    source_sample_id=sample['source_sample_id'],
                    defaults=defaults,
                )
            except IntegrityError:
                # 并发重传下唯一约束获胜时仍视为幂等重复，而非失败。
                created = False
            if created:
                result['accepted'] += 1
                result['accepted_ids'].append(sample['source_sample_id'])
            else:
                result['duplicates'] += 1
                result['duplicate_ids'].append(sample['source_sample_id'])

        credential.last_used_at = now
        credential.save(update_fields=['last_used_at', 'updated_at'])
        return Response(result, status=status.HTTP_200_OK)


def _resolve_period(period: str, time_from=None, time_till=None):
    now = timezone.now()
    if time_from and time_till:
        return time_from, time_till
    mapping = {
        '1h': timedelta(hours=1),
        '6h': timedelta(hours=6),
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
        '30d': timedelta(days=30),
    }
    delta = mapping.get(period or '24h', timedelta(hours=24))
    return now - delta, now


def _quality_bucket_seconds(time_from, time_till) -> int:
    """按时间范围适度降采样，避免 30 天分钟级图表返回过多点。"""
    seconds = max((time_till - time_from).total_seconds(), 1)
    if seconds <= 6 * 3600:
        return 60
    if seconds <= 24 * 3600:
        return 300
    if seconds <= 7 * 24 * 3600:
        return 900
    return 3600


def _mean(values):
    return round(sum(values) / len(values), 3) if values else None


def _quality_history_payload(monitor, *, time_from, time_till) -> dict:
    """将本地品质采样聚合成现有 MonitoringChart 可消费的 generic 数据。"""
    bucket_seconds = _quality_bucket_seconds(time_from, time_till)
    buckets = defaultdict(lambda: {
        'loss': [], 'availability': [], 'icmp_latency': [], 'http_latency': [],
    })
    raw_sample_count = 0

    probes = monitor.probes.filter(
        started_at__gte=time_from,
        started_at__lte=time_till,
    ).order_by('started_at')
    for probe in probes.iterator():
        raw_sample_count += 1
        bucket = int(probe.started_at.timestamp() // bucket_seconds * bucket_seconds)
        data = buckets[bucket]
        if probe.icmp_packet_loss_pct is not None:
            data['loss'].append(float(probe.icmp_packet_loss_pct))
        if probe.icmp_success is not None:
            data['availability'].append(100.0 if probe.icmp_success else 0.0)
        if probe.icmp_avg_ms is not None:
            data['icmp_latency'].append(float(probe.icmp_avg_ms))
        if probe.http_response_ms is not None:
            data['http_latency'].append(float(probe.http_response_ms))

    def build_series(key: str, name: str, unit: str, color: str) -> dict:
        points = []
        for bucket in sorted(buckets):
            value = _mean(buckets[bucket][key])
            if value is None:
                continue
            points.append({
                'clock': bucket,
                'time': datetime.fromtimestamp(
                    bucket,
                    tz=timezone.get_current_timezone(),
                ).isoformat(),
                'value': value,
            })
        return {'name': name, 'unit': unit, 'color': color, 'points': points}

    quality_series = [
        build_series('loss', '丢包率', '%', '#F56C6C'),
        build_series('availability', 'ICMP 可用性', '%', '#67C23A'),
    ]
    latency_series = [
        build_series('icmp_latency', 'ICMP 平均响应', 'ms', '#409EFF'),
        build_series('http_latency', '管理 URL 响应', 'ms', '#E6A23C'),
    ]
    latest = probes.order_by('-started_at').first()
    return {
        'monitor_id': monitor.pk,
        'time_from': time_from,
        'time_till': time_till,
        'bucket_seconds': bucket_seconds,
        'summary': {
            'sample_count': raw_sample_count,
            'avg_packet_loss_pct': _mean([
                value
                for bucket in buckets.values()
                for value in bucket['loss']
            ]),
            'avg_icmp_latency_ms': _mean([
                value
                for bucket in buckets.values()
                for value in bucket['icmp_latency']
            ]),
            'avg_http_response_ms': _mean([
                value
                for bucket in buckets.values()
                for value in bucket['http_latency']
            ]),
            'latest_probe': PopQualityProbeSerializer(latest).data if latest else None,
        },
        'quality_chart': {
            'chart_type': 'generic',
            'meta': {
                'title': f'{monitor.pop_label} · 品质',
                'time_from': time_from,
                'time_till': time_till,
            },
            'series': quality_series,
        },
        'latency_chart': {
            'chart_type': 'generic',
            'meta': {
                'title': f'{monitor.pop_label} · 响应时间',
                'time_from': time_from,
                'time_till': time_till,
            },
            'series': latency_series,
        },
    }


def _resolve_resource_host(pop_point=None, vps=None, edge=None):
    """根据资源解析所属监控主机：POP 直接关联，VPS/Edge 通过图形绑定推断。"""
    if pop_point:
        host = MonitoringHost.objects.filter(pop_point=pop_point).first()
        if host:
            return host
    fk = {'pop_point': pop_point, 'vps': vps, 'edge': edge}
    binding = MonitoringGraphBinding.objects.filter(
        **{k: v for k, v in fk.items() if v},
    ).select_related('monitoring_host', 'graph__monitoring_host',
                     'monitoring_item__monitoring_host').first()
    if binding:
        return binding.effective_host
    return None


class ZabbixServerViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = ZabbixServer.objects.all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['is_active', 'is_default', 'auth_type']
    search_fields = ['name', 'api_url']
    ordering = ['-is_default', 'name']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return ZabbixServerWriteSerializer
        return ZabbixServerListSerializer

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        server = self.get_object()
        try:
            result = ZabbixClient(server).test_connection()
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.TEST_CONNECTION,
                status=MonitoringSyncLog.Status.SUCCESS,
                message=f'连接成功 Zabbix {result.get("version")}',
                zabbix_server=server,
                operator=request.user,
                detail=result,
            )
            return Response(result)
        except ZabbixError as exc:
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.TEST_CONNECTION,
                status=MonitoringSyncLog.Status.FAILED,
                message=str(exc),
                zabbix_server=server,
                operator=request.user,
            )
            return Response({'detail': str(exc)}, status=400)

    @action(detail=True, methods=['get'], url_path='host-groups')
    def host_groups(self, request, pk=None):
        server = self.get_object()
        try:
            return Response(fetch_host_groups(server, request.query_params.get('search')))
        except ZabbixError as exc:
            return Response({'detail': str(exc)}, status=400)

    @action(detail=True, methods=['get'], url_path='templates')
    def templates(self, request, pk=None):
        server = self.get_object()
        try:
            return Response(fetch_templates(server, request.query_params.get('search')))
        except ZabbixError as exc:
            return Response({'detail': str(exc)}, status=400)

    @action(detail=True, methods=['post'], url_path='sync')
    def sync(self, request, pk=None):
        """后台同步：主机清单 + 已登记监控主机的项/图（明细在后台缓慢拉取）。"""
        server = self.get_object()
        if is_server_sync_running(server.pk):
            return Response(
                {'detail': '该服务器正在后台同步中，请稍后再试', 'status': 'running'},
                status=409,
            )
        server.last_sync_status = 'running'
        server.last_sync_message = '后台同步任务已启动…'
        server.save(update_fields=['last_sync_status', 'last_sync_message'])
        enqueue_server_inventory_sync(server.pk, operator_id=getattr(request.user, 'pk', None))
        return Response({
            'status': 'queued',
            'message': '已在后台开始同步：先更新主机清单与统计，监控项/图形将缓慢拉取',
        }, status=202)

    @action(detail=True, methods=['get'], url_path='sync-status')
    def sync_status(self, request, pk=None):
        server = self.get_object()
        return Response({
            'running': is_server_sync_running(server.pk),
            'last_sync_at': server.last_sync_at,
            'last_sync_status': server.last_sync_status,
            'last_sync_message': server.last_sync_message,
        })


class MonitoringGraphProfileViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringGraphProfile.objects.all()
    serializer_class = MonitoringGraphProfileSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['profile_type', 'is_active', 'is_system']
    search_fields = ['code', 'name']
    ordering = ['profile_type', 'name']


class MonitoringHostTemplateViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringHostTemplate.objects.select_related('zabbix_server')
    serializer_class = MonitoringHostTemplateSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['target_kind', 'is_active', 'zabbix_server']
    search_fields = ['name']


class MonitoringHostViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringHost.objects.select_related(
        'zabbix_server', 'pop_point', 'service_pop_point', 'template',
    )
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['host_kind', 'sync_status', 'zabbix_server', 'is_enabled']
    search_fields = ['hostname', 'visible_name', 'zabbix_hostid', 'monitored_ip']

    def get_queryset(self):
        qs = super().get_queryset()
        pop_point = self.request.query_params.get('pop_point')
        if pop_point:
            from django.db.models import Q
            qs = qs.filter(
                Q(pop_point_id=pop_point) | Q(service_pop_point_id=pop_point),
            )
        return qs

    def perform_create(self, serializer):
        host = serializer.save()
        if host.is_enabled:
            enqueue_host_sync(host.pk, push=host.host_kind == MonitoringHost.HostKind.POP)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return MonitoringHostDetailSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return MonitoringHostWriteSerializer
        return MonitoringHostListSerializer

    @action(detail=True, methods=['post'], url_path='sync')
    def sync(self, request, pk=None):
        """将监控主机下发到 Zabbix，并同步 items / graphs（默认后台执行）。"""
        host = self.get_object()
        push = request.data.get('push', True)
        async_mode = request.data.get('async', True)
        if async_mode:
            if not enqueue_host_sync(host.pk, push=bool(push), operator_id=getattr(request.user, 'pk', None)):
                return Response({'detail': '该监控主机正在同步中', 'status': 'running'}, status=409)
            host.mark_syncing()
            return Response({
                'status': 'queued',
                'message': '已在后台排队同步监控项与图形',
                'sync_status': host.sync_status,
                'zabbix_hostid': host.zabbix_hostid,
            }, status=202)
        try:
            result = sync_host_full(host, push=bool(push))
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
                status=MonitoringSyncLog.Status.SUCCESS,
                message=(
                    f'同步完成：hostid={result.get("host", {}).get("hostid", host.zabbix_hostid)} '
                    f'items={result.get("items", {}).get("total", 0)} '
                    f'graphs={result.get("graphs", {}).get("total", 0)}'
                ),
                zabbix_server=host.zabbix_server,
                monitoring_host=host,
                operator=request.user,
                detail=result,
            )
            host.refresh_from_db()
            return Response({
                **result,
                'sync_status': host.sync_status,
                'zabbix_hostid': host.zabbix_hostid,
            })
        except (ValueError, ZabbixError) as exc:
            host.mark_error(str(exc))
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
                status=MonitoringSyncLog.Status.FAILED,
                message=str(exc),
                zabbix_server=host.zabbix_server,
                monitoring_host=host,
                operator=request.user,
            )
            return Response({'detail': str(exc)}, status=400)

    @action(detail=True, methods=['get'], url_path='monitoring-panel')
    def monitoring_panel(self, request, pk=None):
        host = self.get_object()
        bindings = MonitoringGraphBinding.objects.filter(
            is_visible=True,
            graph__monitoring_host=host,
            graph__is_enabled=True,
        ).select_related('graph', 'graph__profile').order_by('display_order')

        panels: dict[str, list] = {}
        for b in bindings:
            g = b.graph
            slot = b.panel_slot
            panels.setdefault(slot, []).append({
                'binding_id': b.pk,
                'graph_id': g.pk,
                'source_type': g.source_type,
                'zabbix_graphid': g.zabbix_graphid or '',
                'title': b.panel_title or g.effective_title,
                'graph_kind': g.graph_kind,
                'graph_kind_label': g.get_graph_kind_display(),
                'width': g.width,
                'height': g.height,
                'billing_enabled': g.billing_enabled,
                'interface_name': g.interface_name,
                'profile_code': g.profile.code if g.profile_id else None,
                'chart_url': request.build_absolute_uri(
                    f'/api/operation/monitoring-graphs/{g.pk}/chart-data/'
                ),
            })

        data = {
            'enabled': host.is_enabled,
            'target_type': host.host_kind,
            'resource_id': host.pop_point_id or 0,
            'monitoring_host_id': host.pk,
            'sync_status': host.sync_status,
            'last_sync_at': host.last_sync_at,
            'monitored_ip': host.monitored_ip,
            'panels': panels,
            'primary_items': MonitoringItemSerializer(
                host.items.filter(is_primary_traffic=True), many=True,
            ).data,
        }
        return Response(ResourceMonitoringPanelSerializer(data).data)


class MonitoringItemViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringItem.objects.select_related('monitoring_host')
    serializer_class = MonitoringItemSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ['monitoring_host', 'traffic_direction', 'interface_name', 'is_primary_traffic']
    search_fields = ['name', 'item_key', 'interface_name']

    @action(detail=True, methods=['get', 'post'], url_path='chart-data')
    def chart_data(self, request, pk=None):
        item = self.get_object()
        src = request.data if request.method == 'POST' else request.query_params
        ser = GraphDataQuerySerializer(data=src)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        time_from, time_till = _resolve_period(
            d.get('period'), d.get('time_from'), d.get('time_till'),
        )
        try:
            return Response(get_item_chart_data(item, time_from=time_from, time_till=time_till))
        except (ValueError, ZabbixError) as exc:
            return Response({'detail': str(exc)}, status=400)


class MonitoringGraphViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringGraph.objects.select_related(
        'monitoring_host', 'profile',
    ).prefetch_related('items', 'series_list')
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['monitoring_host', 'graph_kind', 'source_type', 'billing_enabled', 'is_enabled']
    search_fields = ['name', 'display_title', 'interface_name']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return MonitoringGraphWriteSerializer
        return MonitoringGraphSerializer

    @action(detail=True, methods=['get', 'post'], url_path='chart-data')
    def chart_data(self, request, pk=None):
        graph = self.get_object()
        if request.method == 'POST':
            ser = GraphDataQuerySerializer(data=request.data)
        else:
            ser = GraphDataQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        time_from, time_till = _resolve_period(
            d.get('period'), d.get('time_from'), d.get('time_till'),
        )
        try:
            result = get_graph_chart_data(graph, time_from=time_from, time_till=time_till)
            profile = graph.profile
            unit = profile.unit_display if profile else 'Mbps'
            if result.get('billing'):
                b = result['billing']
                b['percentile_display'] = unit_convert_bps(
                    b.get('percentile_bps') or 0, unit,
                )
                b['unit'] = unit
            return Response(result)
        except (ValueError, ZabbixError) as exc:
            return Response({'detail': str(exc)}, status=400)


class MonitoringGraphSeriesViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringGraphSeries.objects.select_related('graph', 'monitoring_item')
    serializer_class = MonitoringGraphSeriesSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['graph', 'series_role']


class MonitoringGraphBindingViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringGraphBinding.objects.select_related(
        'graph', 'graph__monitoring_host', 'graph__profile',
        'monitoring_item', 'monitoring_item__monitoring_host', 'monitoring_host',
    )
    serializer_class = MonitoringGraphBindingSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['target_type', 'panel_slot', 'pop_point', 'vps', 'edge', 'monitoring_host']

    @action(detail=False, methods=['get'], url_path='resource-panel')
    def resource_panel(self, request):
        """
        资源详情页监控面板：按 (target_type, resource_id) 返回绑定的图形/监控项卡片。
        VPS / 边缘加速 / POP 均通过本接口读取所关联的监控主机图形或监控项。
        """
        target_type = request.query_params.get('target_type')
        resource_id = request.query_params.get('resource_id')
        if target_type not in ('pop', 'vps', 'edge') or not resource_id:
            return Response({'detail': '请提供 target_type 与 resource_id'}, status=400)

        fk = {'pop': 'pop_point', 'vps': 'vps', 'edge': 'edge'}[target_type]
        qs = self.get_queryset().filter(
            target_type=target_type, is_visible=True, **{fk: resource_id},
        ).order_by('panel_slot', 'display_order')

        slot_labels = dict(MonitoringGraphBinding.PanelSlot.choices)
        panels: dict[str, list] = {}
        host_ids = set()
        for b in qs:
            host = b.effective_host
            if host:
                host_ids.add(host.pk)
            card = {
                'binding_id': b.pk,
                'bind_kind': b.bind_kind,
                'panel_slot': b.panel_slot,
                'panel_slot_label': slot_labels.get(b.panel_slot, b.panel_slot),
                'title': b.panel_title,
                'monitoring_host_id': host.pk if host else None,
                'monitoring_host_name': (host.visible_name or host.hostname) if host else '',
            }
            if b.graph_id:
                g = b.graph
                card.update({
                    'graph_id': g.pk,
                    'item_id': None,
                    'title': b.panel_title or g.effective_title,
                    'graph_kind': g.graph_kind,
                    'billing_enabled': g.billing_enabled,
                    'chart_url': request.build_absolute_uri(
                        f'/api/operation/monitoring-graphs/{g.pk}/chart-data/'
                    ),
                })
            else:
                it = b.monitoring_item
                card.update({
                    'graph_id': None,
                    'item_id': it.pk,
                    'title': b.panel_title or it.name,
                    'graph_kind': 'traffic' if it.traffic_direction in ('in', 'out') else 'custom',
                    'billing_enabled': False,
                    'interface_name': it.interface_name,
                    'chart_url': request.build_absolute_uri(
                        f'/api/operation/monitoring-items/{it.pk}/chart-data/'
                    ),
                })
            panels.setdefault(b.panel_slot, []).append(card)

        return Response({
            'target_type': target_type,
            'resource_id': int(resource_id),
            'monitoring_host_ids': sorted(host_ids),
            'panels': panels,
        })


class PopTelemetryCredentialViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    """
    POP 遥测令牌的系统管理入口。

    常规读取只返回令牌元数据；原始令牌仅由 ``create`` 与 ``rotate`` 的响应返回，
    且不会写入数据库、日志或后续查询接口。
    """

    queryset = PopTelemetryCredential.objects.select_related('pop_point').all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['pop_point', 'is_active']
    search_fields = ['pop_point__code', 'pop_point__name', 'token_prefix']
    ordering = ['pop_point__code', 'id']
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return PopTelemetryCredentialWriteSerializer
        return PopTelemetryCredentialSerializer

    def create(self, request, *args, **kwargs):
        serializer = PopTelemetryCredentialWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        credential = PopTelemetryCredential(
            pop_point=serializer.validated_data['pop_point'],
            is_active=serializer.validated_data.get('is_active', True),
        )
        token = credential.rotate_token()
        credential.is_active = serializer.validated_data.get('is_active', True)
        credential.save()
        return Response(
            {
                **PopTelemetryCredentialSerializer(
                    credential,
                    context=self.get_serializer_context(),
                ).data,
                'token': token,
            },
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        credential = self.get_object()
        serializer = PopTelemetryCredentialWriteSerializer(
            credential,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            PopTelemetryCredentialSerializer(
                credential,
                context=self.get_serializer_context(),
            ).data,
        )

    @action(detail=True, methods=['post'], url_path='rotate')
    def rotate(self, request, pk=None):
        credential = self.get_object()
        token = credential.rotate_token()
        credential.save(update_fields=[
            'token_hash', 'token_prefix', 'is_active', 'rotated_at', 'updated_at',
        ])
        return Response({
            **PopTelemetryCredentialSerializer(
                credential,
                context=self.get_serializer_context(),
            ).data,
            'token': token,
        })


class PopQualityMonitorViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    """
    POP 管理入口的独立品质探测配置与历史数据。

    该 ViewSet 不替代 Zabbix 的 Host/Graph 管理；它只针对 PopPoint.mgmt_ip 和
    PopPoint.mgmt_url 产生快速的管理面品质采样。
    """

    queryset = PopQualityMonitor.objects.select_related('pop_point').all()
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['pop_point', 'is_enabled', 'status', 'alert_active']
    search_fields = ['pop_point__code', 'pop_point__name', 'pop_point__mgmt_ip', 'pop_point__mgmt_url']
    ordering_fields = ['last_probe_at', 'status', 'created_at', 'updated_at']
    ordering = ['pop_point__code', 'id']
    extra_read_actions = frozenset({'history'})

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return PopQualityMonitorWriteSerializer
        if self.action == 'retrieve':
            return PopQualityMonitorDetailSerializer
        return PopQualityMonitorListSerializer

    @staticmethod
    def _sync_enabled_status(monitor):
        if not monitor.is_enabled:
            monitor.status = PopQualityMonitor.Status.DISABLED
            monitor.next_probe_at = None
            monitor.save(update_fields=['status', 'next_probe_at', 'updated_at'])
        else:
            update_fields = ['next_probe_at', 'updated_at']
            if monitor.status == PopQualityMonitor.Status.DISABLED:
                monitor.status = PopQualityMonitor.Status.UNKNOWN
                update_fields.insert(0, 'status')
            # 配置新增或编辑后立即交给守护进程领取，避免等待上一次的调度周期。
            monitor.next_probe_at = timezone.now()
            monitor.save(update_fields=update_fields)
        return monitor

    def perform_create(self, serializer):
        self._sync_enabled_status(serializer.save())

    def perform_update(self, serializer):
        self._sync_enabled_status(serializer.save())

    @action(detail=False, methods=['post'], url_path='discover')
    def discover(self, request):
        """根据资源管理中的管理 IP / URL 自动创建尚未配置的 POP 监控对象。"""
        result = discover_pop_quality_monitors()
        return Response({
            **result,
            'message': (
                f'品质监控对象同步完成：新增 {result["created"]} 个，'
                f'已有 {result["existing"]} 个。'
            ),
        })

    @action(detail=True, methods=['post'], url_path='probe-now')
    def probe_now(self, request, pk=None):
        """手工立即执行一次品质探测。"""
        monitor = self.get_object()
        if not monitor.is_enabled:
            return Response({'detail': '该 POP 品质监控已停用，请先启用后再探测。'}, status=400)
        try:
            probe = run_monitor_probe(monitor.pk)
        except ProbeInProgressError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except Exception as exc:  # noqa: BLE001
            logger.exception('manual POP quality probe failed monitor_id=%s', monitor.pk)
            return Response(
                {'detail': f'品质探测执行失败：{exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        monitor = self.get_queryset().get(pk=monitor.pk)
        return Response({
            'monitor': PopQualityMonitorDetailSerializer(monitor).data,
            'probe': PopQualityProbeSerializer(probe).data,
        })

    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """返回品质/响应时间两张 generic 图表所需的本地时序数据。"""
        monitor = self.get_object()
        query = PopQualityHistoryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        time_from, time_till = _resolve_period(
            data.get('period'),
            data.get('time_from'),
            data.get('time_till'),
        )
        return Response(_quality_history_payload(
            monitor,
            time_from=time_from,
            time_till=time_till,
        ))


class MonitoringTrafficReportViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    queryset = MonitoringTrafficReport.objects.select_related(
        'monitoring_host', 'profile', 'in_item', 'out_item', 'graph',
        'pop_point', 'vps', 'edge',
        'billing_item', 'billing_item__billing',
    )
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['target_type', 'status', 'monitoring_host', 'interface_name']
    search_fields = ['report_no', 'title', 'interface_name']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action == 'list':
            show = self.request.query_params.get('show_archived', '').lower()
            if show not in ('1', 'true', 'yes'):
                qs = qs.exclude(status=MonitoringTrafficReport.ReportStatus.ARCHIVED)
        return qs

    def get_serializer_class(self):
        if self.action == 'list':
            return MonitoringTrafficReportListSerializer
        return MonitoringTrafficReportSerializer

    @action(detail=False, methods=['get'], url_path='host-graphs')
    def host_graphs(self, request):
        """按监控主机返回可用于流量截取的图形列表（含 interface / item 解析）。"""
        host_id = request.query_params.get('monitoring_host')
        if not host_id:
            return Response({'detail': '请提供 monitoring_host'}, status=400)
        host = MonitoringHost.objects.filter(pk=host_id).first()
        if not host:
            return Response({'detail': '监控主机不存在'}, status=404)
        from .resource_monitoring import build_host_traffic_graph_options
        graphs = build_host_traffic_graph_options(host)
        return Response({
            'monitoring_host_id': host.pk,
            'monitoring_host_name': host.visible_name or host.hostname,
            'graphs': graphs,
        })

    @action(detail=True, methods=['post'], url_path='archive')
    def archive(self, request, pk=None):
        report = self.get_object()
        report.status = MonitoringTrafficReport.ReportStatus.ARCHIVED
        report.save(update_fields=['status'])
        from customer_manager.traffic_billing import generate_traffic_billing_item
        try:
            traffic_billing = generate_traffic_billing_item(report, user=request.user)
        except Exception:  # noqa: BLE001
            logger.exception('流量报告 %s 归档后出账失败', report.report_no)
            traffic_billing = {
                'ok': False,
                'status': 'error',
                'reason': 'error',
                'reason_label': '出账异常，报告已归档，可用命令补扫',
            }
        payload = MonitoringTrafficReportListSerializer(report).data
        payload['traffic_billing'] = traffic_billing
        return Response(payload)

    @action(detail=False, methods=['get'], url_path='resource-context')
    def resource_context(self, request):
        """VPS/边缘加速流量截取页：接口列表 + POP 过滤后的监控主机。"""
        target_type = request.query_params.get('target_type')
        resource_id = request.query_params.get('resource_id')
        if target_type not in ('vps', 'edge') or not resource_id:
            return Response({'detail': '请提供 target_type=vps|edge 与 resource_id'}, status=400)
        try:
            data = get_traffic_resource_context(
                target_type=target_type, resource_id=int(resource_id),
            )
        except (VPS.DoesNotExist, EdgeAcceleration.DoesNotExist):
            return Response({'detail': '资源不存在'}, status=404)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=400)
        return Response(data)

    @action(detail=False, methods=['post'], url_path='query')
    def query_traffic(self, request):
        """按时间窗口截取接口流量并计算 95 峰值"""
        ser = TrafficQuerySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        binding = d.get('binding')
        resolved = None
        if binding:
            target_type = None
            resource_id = None
            if d.get('vps'):
                target_type, resource_id = 'vps', d['vps'].pk
            elif d.get('edge'):
                target_type, resource_id = 'edge', d['edge'].pk
            elif d.get('pop_point'):
                target_type, resource_id = 'pop', d['pop_point'].pk
            else:
                target_type = binding.target_type
                resource_id = binding.vps_id or binding.edge_id or binding.pop_point_id
            if target_type and resource_id:
                try:
                    validate_binding_for_resource(
                        binding, target_type=target_type, resource_id=resource_id,
                    )
                except ValueError as exc:
                    return Response({'detail': str(exc)}, status=400)
            try:
                resolved = resolve_traffic_from_binding(binding)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=400)

        if d.get('graph') and not resolved:
            try:
                from .resource_monitoring import resolve_traffic_from_graph
                resolved = resolve_traffic_from_graph(d['graph'])
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=400)

        host = (resolved or {}).get('monitoring_host') or d.get('monitoring_host') or _resolve_resource_host(
            d.get('pop_point'), d.get('vps'), d.get('edge'),
        )
        if not host:
            return Response(
                {'detail': '未找到监控数据，请先在监控 Tab 关联监控图形/监控项'},
                status=400,
            )

        pop_id = None
        if d.get('vps'):
            pop_id = d['vps'].pop_point_id
        elif d.get('edge'):
            pop_id = d['edge'].pop_point_id
        if pop_id and not binding:
            try:
                validate_monitoring_host_for_resource(monitoring_host=host, pop_point_id=pop_id)
            except ValueError as exc:
                return Response({'detail': str(exc)}, status=400)

        time_from, time_till = d['time_from'], d['time_till']
        try:
            result = query_traffic(
                monitoring_host=host,
                time_from=time_from,
                time_till=time_till,
                profile=d.get('profile'),
                graph=(resolved or {}).get('graph') or d.get('graph'),
                interface_name=(
                    (resolved or {}).get('interface_name')
                    or d.get('interface_name')
                    or ''
                ),
                in_item=(resolved or {}).get('in_item') or d.get('in_item'),
                out_item=(resolved or {}).get('out_item') or d.get('out_item'),
                persist=d.get('persist', False),
                title=d.get('title') or (resolved or {}).get('title') or '',
                operator=request.user,
                pop_point=d.get('pop_point'),
                vps=d.get('vps'),
                edge=d.get('edge'),
            )
            record_monitoring_log(
                action_type=MonitoringSyncLog.ActionType.TRAFFIC_QUERY,
                status=MonitoringSyncLog.Status.SUCCESS,
                message=f'流量查询 {time_from.date()} ~ {time_till.date()}',
                zabbix_server=host.zabbix_server,
                monitoring_host=host,
                operator=request.user,
                detail={'sample_count': result.get('billing', {}).get('sample_count')},
            )
            if d.get('persist'):
                record_monitoring_log(
                    action_type=MonitoringSyncLog.ActionType.BILLING_COMPUTE,
                    status=MonitoringSyncLog.Status.SUCCESS,
                    message=f'95计费 {result.get("billing", {}).get("percentile_mbps")} Mbps',
                    monitoring_host=host,
                    operator=request.user,
                )
            return Response(result)
        except (ValueError, ZabbixError) as exc:
            return Response({'detail': str(exc)}, status=400)

    @action(detail=False, methods=['post'], url_path='percentile')
    def percentile(self, request):
        """快捷 95 计费接口"""
        data = dict(request.data)
        data.setdefault('persist', True)
        if 'profile' not in data:
            profile = MonitoringGraphProfile.objects.filter(code='traffic_p95_default').first()
            if profile:
                data['profile'] = profile.pk
        ser = TrafficQuerySerializer(data=data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        host = d.get('monitoring_host') or _resolve_resource_host(
            d.get('pop_point'), d.get('vps'), d.get('edge'),
        )
        if not host:
            return Response({'detail': '未找到监控主机'}, status=400)

        try:
            result = query_traffic(
                monitoring_host=host,
                time_from=d['time_from'],
                time_till=d['time_till'],
                profile=d.get('profile'),
                graph=d.get('graph'),
                interface_name=d.get('interface_name') or '',
                in_item=d.get('in_item'),
                out_item=d.get('out_item'),
                persist=True,
                title=d.get('title') or '',
                operator=request.user,
            )
            return Response(result)
        except (ValueError, ZabbixError) as exc:
            return Response({'detail': str(exc)}, status=400)


class MonitoringSyncLogViewSet(OperationSystemPermissionMixin, viewsets.ReadOnlyModelViewSet):
    queryset = MonitoringSyncLog.objects.select_related(
        'zabbix_server', 'monitoring_host', 'operator',
    )
    serializer_class = MonitoringSyncLogSerializer
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['action_type', 'status', 'zabbix_server', 'monitoring_host']
    ordering = ['-created_at']


class WebSSHCommandTemplateViewSet(OperationSystemPermissionMixin, viewsets.ModelViewSet):
    """WebSSH 命令模板 CRUD。"""

    queryset = WebSSHCommandTemplate.objects.all()
    serializer_class = WebSSHCommandTemplateSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['category', 'is_active', 'is_system']
    search_fields = ['code', 'name', 'description', 'command']
    ordering_fields = ['sort_order', 'id', 'updated_at']
    ordering = ['sort_order', 'id']

    def get_queryset(self):
        qs = super().get_queryset()
        if self.action in ('list', 'retrieve') and self.request.query_params.get('active_only') == '1':
            qs = qs.filter(is_active=True)
        return qs

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError('系统内置模板不可删除')
        instance.delete()
