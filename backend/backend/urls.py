from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

from .views import (
    CustomTokenObtainPairView,
    UserProfileView,
    ChangePasswordView,
    UserListView,
)

urlpatterns = [
    # ── Django Admin ─────────────────────────────────────────
    path('admin/', admin.site.urls),

    # ── 认证接口（无需 Token） ─────────────────────────────────
    path('api/auth/token/',
         CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/',
         TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/token/verify/',
         TokenVerifyView.as_view(), name='token_verify'),

    # ── 当前用户信息（需 Token） ──────────────────────────────
    path('api/auth/me/', UserProfileView.as_view(), name='user_profile'),
    path('api/auth/me/change_password/',
         ChangePasswordView.as_view(), name='change_password'),
    path('api/auth/users/', UserListView.as_view(), name='user_list'),

    # ── 用户账户管理（需 Token）──────────────────────────────
    path('api/accounts/', include('accounts.urls')),

    # ── 业务模块（需 Token） ──────────────────────────────────
    path('api/customer/', include('customer_manager.urls')),
    path('api/resource/', include('resource_manager.urls')),
    path('api/service/', include('service_manager.urls')),
    path('api/product/', include('product_manager.urls')),
    path('api/supplier/', include('supplier_manager.urls')),
    path('api/operation/', include('operation_manager.urls')),
    path('api/system/', include('system_manager.urls')),
    path('api/log/', include('log_manager.urls')),

    # ── 客户自助门户（部分公开，部分需客户 Token）────────────
    path('api/portal/', include('portal.urls')),

    # ── API 文档（无需 Token，可按需加权限）──────────────────
    # OpenAPI schema (JSON/YAML)
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    # Swagger UI：http://<host>/api/docs/
    path('api/docs/',
         SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # ReDoc UI：http://<host>/api/redoc/
    path('api/redoc/',
         SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
