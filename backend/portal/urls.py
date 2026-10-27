"""portal/urls.py — 客户门户路由（挂载于 /api/portal/）"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    DashboardView, LocationsView, MyBillViewSet, MyEdgeViewSet, MyOrderViewSet,
    MyInquiryViewSet, MyTicketViewSet, MyServiceViewSet, MyVPSViewSet, PortalProductViewSet, ProfileView, RegisterView,
    SupportConfigView, SupportInquiryView, PortalOsImageView, PortalOsTemplateView,
)

router = DefaultRouter()
router.register('products', PortalProductViewSet, basename='portal-product')
router.register('services', MyServiceViewSet, basename='portal-service')
router.register('orders', MyOrderViewSet, basename='portal-order')
router.register('inquiries', MyInquiryViewSet, basename='portal-inquiry')
router.register('tickets', MyTicketViewSet, basename='portal-ticket')
router.register('bills', MyBillViewSet, basename='portal-bill')
router.register('vps', MyVPSViewSet, basename='portal-vps')
router.register('edge', MyEdgeViewSet, basename='portal-edge')

urlpatterns = [
    path('register/', RegisterView.as_view(), name='portal-register'),
    path('locations/', LocationsView.as_view(), name='portal-locations'),
    path('support-config/', SupportConfigView.as_view(), name='portal-support-config'),
    path('os-images/', PortalOsImageView.as_view(), name='portal-os-images'),
    path('os-templates/', PortalOsTemplateView.as_view(), name='portal-os-templates'),
    path('support-inquiries/', SupportInquiryView.as_view(), name='portal-support-inquiries'),
    path('dashboard/', DashboardView.as_view(), name='portal-dashboard'),
    path('profile/', ProfileView.as_view(), name='portal-profile'),
    path('', include(router.urls)),
]
