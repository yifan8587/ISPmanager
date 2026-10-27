from rest_framework.routers import DefaultRouter

from .views import (
    PortalCountryShortcutViewSet,
    ProductPublishLogViewSet,
    ProductSpecViewSet,
    ProductTemplateViewSet,
    ProductUpgradeRuleViewSet,
    ProductViewSet,
    TemplatePricingViewSet,
)

router = DefaultRouter()
router.register(r'specs', ProductSpecViewSet, basename='productspec')
router.register(r'templates', ProductTemplateViewSet, basename='producttemplate')
router.register(r'template-pricings', TemplatePricingViewSet, basename='templatepricing')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'country-shortcuts', PortalCountryShortcutViewSet, basename='portal-country-shortcut')
router.register(r'upgrade-rules', ProductUpgradeRuleViewSet, basename='productupgraderule')
router.register(r'publish-logs', ProductPublishLogViewSet, basename='productpublishlog')

urlpatterns = router.urls
