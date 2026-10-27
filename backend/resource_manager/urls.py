from rest_framework.routers import DefaultRouter
from .views import (
    RegionViewSet,
    CountryViewSet,
    CityViewSet,
    AvailabilityZoneViewSet,
    BaseDataExcelViewSet,
    CarrierViewSet,
    NetworkTypeOptionViewSet,
    PopPointViewSet,
    NetworkInterfaceViewSet,
    RouteTableViewSet,
    StaticRouteViewSet,
    PolicyRouteViewSet,
    IPBlockViewSet,
    IPAddressViewSet,
    TunnelIPPoolViewSet,
    VPSViewSet,
    VPSNetworkInterfaceViewSet,
    EdgeAccelerationViewSet,
    BandwidthAllocationViewSet,
    ResourceOperationLogViewSet,
)

router = DefaultRouter()

# 基础字典整本导入导出
router.register(r'base-data', BaseDataExcelViewSet, basename='base-data')

# 地理位置（只读）
router.register(r'regions', RegionViewSet, basename='region')
router.register(r'countries', CountryViewSet, basename='country')
router.register(r'cities', CityViewSet, basename='city')
router.register(r'availability-zones', AvailabilityZoneViewSet, basename='availability-zone')

# 运营商 / 网络类型目录
router.register(r'carriers', CarrierViewSet, basename='carrier')
router.register(r'network-types', NetworkTypeOptionViewSet, basename='network-type')

# POP 点
router.register(r'pop-points', PopPointViewSet, basename='pop-point')

# 网络基础设施
router.register(r'interfaces', NetworkInterfaceViewSet, basename='interface')
router.register(r'route-tables', RouteTableViewSet, basename='route-table')
router.register(r'static-routes', StaticRouteViewSet, basename='static-route')
router.register(r'policy-routes', PolicyRouteViewSet, basename='policy-route')

# IP 管理
router.register(r'ip-blocks', IPBlockViewSet, basename='ip-block')
router.register(r'ip-addresses', IPAddressViewSet, basename='ip-address')
router.register(r'tunnel-ip-pools', TunnelIPPoolViewSet, basename='tunnel-ip-pool')

# VPS
router.register(r'vps', VPSViewSet, basename='vps')
router.register(r'vps-interfaces', VPSNetworkInterfaceViewSet, basename='vps-interface')

# 边缘加速
router.register(r'edge-accelerations', EdgeAccelerationViewSet, basename='edge-acceleration')

# 带宽分配
router.register(r'bandwidth-allocations', BandwidthAllocationViewSet, basename='bandwidth-allocation')

# 操作日志（只读）
router.register(r'operation-logs', ResourceOperationLogViewSet, basename='resource-log')

urlpatterns = router.urls
