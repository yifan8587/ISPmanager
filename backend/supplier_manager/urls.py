from rest_framework.routers import DefaultRouter

from .views import (
    SupplierAPIViewSet,
    SupplierAccountViewSet,
    SupplierBillViewSet,
    SupplierPopBindingViewSet,
    SupplierResourceViewSet,
    SupplierSettlementViewSet,
    SupplierSyncLogViewSet,
    SupplierViewSet,
)

router = DefaultRouter()
router.register(r'suppliers', SupplierViewSet, basename='supplier')
router.register(r'accounts', SupplierAccountViewSet, basename='supplieraccount')
router.register(r'apis', SupplierAPIViewSet, basename='supplierapi')
router.register(r'pop-bindings', SupplierPopBindingViewSet, basename='supplierpopbinding')
router.register(r'resources', SupplierResourceViewSet, basename='supplierresource')
router.register(r'sync-logs', SupplierSyncLogViewSet, basename='suppliersynclog')
router.register(r'bills', SupplierBillViewSet, basename='supplierbill')
router.register(r'settlements', SupplierSettlementViewSet, basename='suppliersettlement')

urlpatterns = router.urls
