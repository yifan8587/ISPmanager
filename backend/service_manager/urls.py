from django.urls import path
from rest_framework.routers import DefaultRouter
from .self_service_views import InstanceSnapshotViewSet, OsImageViewSet, SelfServiceJobViewSet
from .views import (
    ServiceOrderViewSet,
    ServiceInquiryViewSet,
    SupportTicketViewSet,
    ServiceInstanceViewSet,
    ServiceAllocationViewSet,
    ServiceChangeRecordViewSet,
    ServiceOperationLogViewSet,
    ServiceLifecycleRecordViewSet,
    ServiceProvisionHookViewSet,
    ServiceStatsViewSet,
    ServiceBillingViewSet,
    ManualDeliveryCallbackView,
)

router = DefaultRouter()
router.register(r'orders', ServiceOrderViewSet, basename='serviceorder')
router.register(r'inquiries', ServiceInquiryViewSet, basename='serviceinquiry')
router.register(r'tickets', SupportTicketViewSet, basename='supportticket')
router.register(r'os-images', OsImageViewSet, basename='osimage')
router.register(r'snapshots', InstanceSnapshotViewSet, basename='instancesnapshot')
router.register(r'self-service-jobs', SelfServiceJobViewSet, basename='selfservicejob')
router.register(r'instances', ServiceInstanceViewSet, basename='serviceinstance')
router.register(r'allocations', ServiceAllocationViewSet, basename='serviceallocation')
router.register(r'changes', ServiceChangeRecordViewSet, basename='servicechange')
router.register(r'logs', ServiceOperationLogViewSet, basename='servicelog')
router.register(r'lifecycle', ServiceLifecycleRecordViewSet, basename='servicelifecycle')
router.register(r'hooks', ServiceProvisionHookViewSet, basename='servicehook')
router.register(r'stats', ServiceStatsViewSet, basename='servicestats')
router.register(r'billing', ServiceBillingViewSet, basename='servicebilling')

urlpatterns = [
    path(
        'manual-delivery/callback/',
        ManualDeliveryCallbackView.as_view(),
        name='manual-delivery-callback',
    ),
] + router.urls
