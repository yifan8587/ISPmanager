from django.urls import path
from .views import (
    UserManageListView,
    UserManageDetailView,
    UserResetPasswordView,
    UserToggleActiveView,
    APITokenListView,
    APITokenDetailView,
    APITokenRevokeAllView,
    APITokenVerifyView,
    AdminAllTokensView,
    RolePermissionCatalogView,
    RolePermissionUpdateView,
    PermissionResetDefaultsView,
    PermissionGroupUpdateView,
    PermissionPageManageView,
    PermissionDefinitionManageView,
    CurrentUserPermissionsView,
)

urlpatterns = [
    # ── 用户管理（管理员）─────────────────────────────────────
    path('users/',                          UserManageListView.as_view(),       name='account-user-list'),
    path('users/<int:pk>/',                 UserManageDetailView.as_view(),     name='account-user-detail'),
    path('users/<int:pk>/reset_password/',  UserResetPasswordView.as_view(),    name='account-reset-password'),
    path('users/<int:pk>/toggle_active/',   UserToggleActiveView.as_view(),     name='account-toggle-active'),

    # ── 角色 / 权限目录 ───────────────────────────────────────
    path('roles/',                          RolePermissionCatalogView.as_view(), name='account-role-catalog'),
    path('roles/<str:role>/permissions/',   RolePermissionUpdateView.as_view(), name='account-role-permissions'),
    path('permissions/reset-defaults/',   PermissionResetDefaultsView.as_view(), name='account-permissions-reset'),
    path('permission-groups/<str:key>/',  PermissionGroupUpdateView.as_view(), name='account-permission-group'),
    path('permission-pages/',             PermissionPageManageView.as_view(), name='account-permission-page-create'),
    path('permission-pages/<str:group_key>/<str:page_key>/',
         PermissionPageManageView.as_view(), name='account-permission-page'),
    path('permissions/',                    PermissionDefinitionManageView.as_view(), name='account-permission-create'),
    path('permissions/<path:code>/',        PermissionDefinitionManageView.as_view(), name='account-permission-detail'),
    path('me/permissions/',                 CurrentUserPermissionsView.as_view(), name='account-me-permissions'),

    # ── API Token 管理（当前用户）────────────────────────────
    path('tokens/',                         APITokenListView.as_view(),         name='account-token-list'),
    path('tokens/revoke_all/',              APITokenRevokeAllView.as_view(),    name='account-token-revoke-all'),
    path('tokens/verify/',                  APITokenVerifyView.as_view(),       name='account-token-verify'),
    path('tokens/<int:pk>/',                APITokenDetailView.as_view(),       name='account-token-detail'),

    # ── 管理员总览令牌────────────────────────────────────────
    path('admin/tokens/',                   AdminAllTokensView.as_view(),       name='account-admin-all-tokens'),
]
