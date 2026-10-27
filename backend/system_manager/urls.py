from rest_framework.routers import DefaultRouter

from .views import (
    IMChannelViewSet,
    IMMessageLogViewSet,
    NotificationRuleViewSet,
    PaymentChannelViewSet,
    SystemBackupViewSet,
    SystemOsTemplateViewSet,
    SystemOverviewViewSet,
    SystemSettingViewSet,
    SystemUpgradeViewSet,
    SystemVersionViewSet,
)

router = DefaultRouter()
router.register(r'settings', SystemSettingViewSet, basename='system-setting')
router.register(r'overview', SystemOverviewViewSet, basename='system-overview')
router.register(r'version', SystemVersionViewSet, basename='system-version')
router.register(r'backups', SystemBackupViewSet, basename='system-backup')
router.register(r'upgrades', SystemUpgradeViewSet, basename='system-upgrade')
router.register(r'os-templates', SystemOsTemplateViewSet, basename='system-os-template')
router.register(r'payment-channels', PaymentChannelViewSet, basename='payment-channel')
router.register(r'im-channels', IMChannelViewSet, basename='im-channel')
router.register(r'notification-rules', NotificationRuleViewSet, basename='notification-rule')
router.register(r'im-logs', IMMessageLogViewSet, basename='im-log')

urlpatterns = router.urls
