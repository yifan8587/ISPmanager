from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .webssh_views import WebSSHResolveView
from .views import (
    ZabbixServerViewSet,
    MonitoringGraphProfileViewSet,
    MonitoringHostTemplateViewSet,
    MonitoringHostViewSet,
    MonitoringItemViewSet,
    MonitoringGraphViewSet,
    MonitoringGraphSeriesViewSet,
    MonitoringGraphBindingViewSet,
    MonitoringTrafficReportViewSet,
    MonitoringSyncLogViewSet,
    PopQualityMonitorViewSet,
    PopTelemetryCredentialViewSet,
    EdgeQualityIngestView,
    WebSSHCommandTemplateViewSet,
)

router = DefaultRouter()
router.register('zabbix-servers', ZabbixServerViewSet, basename='zabbix-server')
router.register('graph-profiles', MonitoringGraphProfileViewSet, basename='graph-profile')
router.register('host-templates', MonitoringHostTemplateViewSet, basename='host-template')
router.register('monitoring-hosts', MonitoringHostViewSet, basename='monitoring-host')
router.register('monitoring-items', MonitoringItemViewSet, basename='monitoring-item')
router.register('monitoring-graphs', MonitoringGraphViewSet, basename='monitoring-graph')
router.register('graph-series', MonitoringGraphSeriesViewSet, basename='graph-series')
router.register('graph-bindings', MonitoringGraphBindingViewSet, basename='graph-binding')
router.register('traffic-reports', MonitoringTrafficReportViewSet, basename='traffic-report')
router.register('sync-logs', MonitoringSyncLogViewSet, basename='monitoring-sync-log')
router.register('pop-quality-monitors', PopQualityMonitorViewSet, basename='pop-quality-monitor')
router.register(
    'pop-telemetry-credentials',
    PopTelemetryCredentialViewSet,
    basename='pop-telemetry-credential',
)
router.register('webssh-templates', WebSSHCommandTemplateViewSet, basename='webssh-template')

urlpatterns = [
    path('edge-quality/ingest/', EdgeQualityIngestView.as_view(), name='edge-quality-ingest'),
    path('webssh/resolve/', WebSSHResolveView.as_view(), name='webssh-resolve'),
    path('', include(router.urls)),
]
