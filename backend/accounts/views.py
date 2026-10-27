"""
accounts/views.py
用户管理 & API Token 管理视图
"""
from django.contrib.auth.models import User
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse, inline_serializer
from drf_spectacular.openapi import AutoSchema
import rest_framework.serializers as s

from .models import APIToken, UserProfile, PermissionGroup, PermissionPage, PermissionDefinition
from .serializers import (
    UserListSerializer, UserCreateSerializer, UserUpdateSerializer,
    AdminResetPasswordSerializer, APITokenListSerializer,
    APITokenCreateSerializer, APITokenCreateResponseSerializer,
    serialize_auth_user,
)
from backend.throttles import PublicTokenVerifyRateThrottle
from .permissions import IsAdminUser, IsSystemAccount, check_permission
from .role_permissions import (
    serialize_role_catalog, serialize_permission_groups, serialize_action_meta, ROLE_LABELS,
)
from .permission_service import (
    ensure_permission_defaults,
    get_role_permission_codes,
    set_role_permissions,
    update_permission_group,
    update_permission_page,
    create_permission_page,
    update_permission_definition,
    create_custom_permission,
    delete_custom_permission,
)
# ─────────────────────────────────────────────────────────────
# 用户管理（仅管理员）
# ─────────────────────────────────────────────────────────────

class UserManageListView(APIView):
    """
    GET  /api/accounts/users/        管理员获取用户列表
    POST /api/accounts/users/        管理员创建新用户
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=['用户管理'],
        summary='获取系统用户列表',
        description='管理员获取所有系统用户，含扩展信息和 Token 数量。',
        responses={200: UserListSerializer(many=True)},
    )
    def get(self, request):
        users = User.objects.select_related('profile__supplier', 'profile__channel').prefetch_related('api_tokens').order_by('id')

        # 支持简单过滤
        search = request.query_params.get('search', '')
        is_active = request.query_params.get('is_active', '')
        account_type = request.query_params.get('account_type', '')
        role = request.query_params.get('role', '')
        supplier_id = request.query_params.get('supplier_id', '')
        channel_id = request.query_params.get('channel_id', '')

        if search:
            users = users.filter(Q(username__icontains=search) | Q(email__icontains=search))
        if is_active in ('true', '1'):
            users = users.filter(is_active=True)
        elif is_active in ('false', '0'):
            users = users.filter(is_active=False)
        if account_type in dict(UserProfile.AccountType.choices):
            users = users.filter(profile__account_type=account_type)
        if role in dict(UserProfile.Role.choices):
            users = users.filter(profile__role=role)
        if supplier_id:
            users = users.filter(profile__supplier_id=supplier_id)
        if channel_id:
            users = users.filter(profile__channel_id=channel_id)

        serializer = UserListSerializer(users, many=True)
        return Response({
            'code': 200, 'message': 'ok',
            'data': {'count': users.count(), 'results': serializer.data},
        })

    @extend_schema(
        tags=['用户管理'],
        summary='创建系统用户',
        description='管理员创建新系统用户（含密码和扩展档案）。',
        request=UserCreateSerializer,
        responses={
            201: UserListSerializer,
            400: OpenApiResponse(description='参数校验失败'),
        },
    )
    def post(self, request):
        serializer = UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        user = User.objects.select_related('profile__supplier', 'profile__channel').prefetch_related('api_tokens').get(pk=user.pk)
        return Response(
            {'code': 201, 'message': '用户创建成功', 'data': UserListSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class UserManageDetailView(APIView):
    """
    GET    /api/accounts/users/<id>/           获取用户详情
    PATCH  /api/accounts/users/<id>/           更新用户信息
    DELETE /api/accounts/users/<id>/           删除用户
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    def _get_user(self, pk):
        try:
            return User.objects.select_related('profile__supplier', 'profile__channel').get(pk=pk)
        except User.DoesNotExist:
            return None

    @extend_schema(tags=['用户管理'], summary='获取用户详情', responses={200: UserListSerializer})
    def get(self, request, pk):
        user = self._get_user(pk)
        if not user:
            return Response({'code': 404, 'message': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'code': 200, 'message': 'ok', 'data': UserListSerializer(user).data})

    @extend_schema(
        tags=['用户管理'],
        summary='更新用户信息',
        request=UserUpdateSerializer,
        responses={200: UserListSerializer},
    )
    def patch(self, request, pk):
        user = self._get_user(pk)
        if not user:
            return Response({'code': 404, 'message': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)

        # 防止降级自己的超级管理员权限
        if user == request.user and 'is_superuser' in request.data:
            if not request.data.get('is_superuser', True):
                return Response({'code': 400, 'message': '不能撤销自己的超级管理员权限'},
                                status=status.HTTP_400_BAD_REQUEST)

        serializer = UserUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated_user = serializer.save()
        return Response({'code': 200, 'message': '更新成功', 'data': UserListSerializer(updated_user).data})

    @extend_schema(
        tags=['用户管理'],
        summary='删除用户',
        responses={204: OpenApiResponse(description='删除成功')},
    )
    def delete(self, request, pk):
        user = self._get_user(pk)
        if not user:
            return Response({'code': 404, 'message': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)
        if user == request.user:
            return Response({'code': 400, 'message': '不能删除自己'}, status=status.HTTP_400_BAD_REQUEST)
        username = user.username
        user.delete()
        return Response({'code': 200, 'message': f'用户 {username} 已删除'})


class UserResetPasswordView(APIView):
    """POST /api/accounts/users/<id>/reset_password/  管理员重置用户密码"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=['用户管理'],
        summary='管理员重置用户密码',
        request=AdminResetPasswordSerializer,
        responses={
            200: inline_serializer(name='ResetPwdResp',
                                   fields={'code': s.IntegerField(), 'message': s.CharField()}),
            403: OpenApiResponse(description='权限不足'),
        },
    )
    def post(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'code': 404, 'message': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)

        # 非超级管理员不能重置超级管理员密码
        if user.is_superuser and not request.user.is_superuser:
            return Response({'code': 403, 'message': '权限不足，不能重置超级管理员密码'},
                            status=status.HTTP_403_FORBIDDEN)

        serializer = AdminResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])
        return Response({'code': 200, 'message': f'用户 {user.username} 密码已重置'})


class UserToggleActiveView(APIView):
    """POST /api/accounts/users/<id>/toggle_active/  启用/禁用用户"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=['用户管理'],
        summary='启用/禁用用户',
        request=inline_serializer(name='ToggleActiveReq',
                                  fields={'is_active': s.BooleanField()}),
        responses={200: UserListSerializer},
    )
    def post(self, request, pk):
        try:
            user = User.objects.select_related('profile').get(pk=pk)
        except User.DoesNotExist:
            return Response({'code': 404, 'message': '用户不存在'}, status=status.HTTP_404_NOT_FOUND)
        if user == request.user:
            return Response({'code': 400, 'message': '不能禁用自己'}, status=status.HTTP_400_BAD_REQUEST)

        is_active = request.data.get('is_active', not user.is_active)
        user.is_active = is_active
        user.save(update_fields=['is_active'])
        return Response({'code': 200,
                         'message': f'用户已{"启用" if is_active else "禁用"}',
                         'data': UserListSerializer(user).data})


# ─────────────────────────────────────────────────────────────
# API Token 管理
# ─────────────────────────────────────────────────────────────

class APITokenListView(APIView):
    """
    GET  /api/accounts/tokens/   列出当前用户的令牌
    POST /api/accounts/tokens/   生成新令牌（仅系统账号）
    """
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsSystemAccount()]
        return [IsAuthenticated()]

    @extend_schema(
        tags=['API 令牌'],
        summary='列出 API 令牌',
        description='返回当前登录用户的所有 API 令牌（管理员可通过 ?user_id= 查看指定用户的令牌）。',
        responses={200: APITokenListSerializer(many=True)},
    )
    def get(self, request):
        if (request.user.is_staff or request.user.is_superuser) and 'user_id' in request.query_params:
            tokens = APIToken.objects.filter(user_id=request.query_params['user_id'])
        else:
            tokens = APIToken.objects.filter(user=request.user)

        return Response({
            'code': 200, 'message': 'ok',
            'data': APITokenListSerializer(tokens, many=True).data,
        })

    @extend_schema(
        tags=['API 令牌'],
        summary='生成 API 令牌',
        description=(
            '为当前用户生成一个新的 API 令牌（仅系统账号；客户账号不可自助签发）。\n\n'
            '**⚠️ 原始令牌仅在此响应中出现一次，请立即保存！**\n\n'
            '后续 API 调用使用方式：\n'
            '```\nAuthorization: Token ispm_xxxxx...\n```'
        ),
        request=APITokenCreateSerializer,
        responses={
            201: APITokenCreateResponseSerializer,
            400: OpenApiResponse(description='参数错误'),
        },
    )
    def post(self, request):
        serializer = APITokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 管理员可以为其他用户生成令牌
        target_user = request.user
        if (request.user.is_staff or request.user.is_superuser) and 'user_id' in request.data:
            try:
                target_user = User.objects.get(pk=request.data['user_id'])
            except User.DoesNotExist:
                return Response({'code': 404, 'message': '目标用户不存在'}, status=status.HTTP_404_NOT_FOUND)

        token_obj, raw_token = APIToken.generate(
            user=target_user,
            name=serializer.validated_data['name'],
            expires_at=serializer.validated_data.get('expires_at'),
        )

        return Response({
            'code': 201,
            'message': '令牌生成成功，请立即保存原始令牌，此后将无法再次查看',
            'data': {
                'id':          token_obj.id,
                'name':        token_obj.name,
                'token':       raw_token,
                'token_prefix': token_obj.token_prefix,
                'expires_at':  token_obj.expires_at,
                'created_at':  token_obj.created_at,
            },
        }, status=status.HTTP_201_CREATED)


class APITokenDetailView(APIView):
    """
    GET    /api/accounts/tokens/<id>/         获取令牌详情
    DELETE /api/accounts/tokens/<id>/         撤销/删除令牌
    PATCH  /api/accounts/tokens/<id>/         更新令牌名称或启用状态
    """
    permission_classes = [IsAuthenticated]

    def _get_token(self, request, pk):
        """获取令牌，普通用户只能操作自己的令牌"""
        try:
            token = APIToken.objects.select_related('user').get(pk=pk)
        except APIToken.DoesNotExist:
            return None
        if token.user != request.user and not (request.user.is_staff or request.user.is_superuser):
            return None
        return token

    @extend_schema(tags=['API 令牌'], summary='获取令牌详情', responses={200: APITokenListSerializer})
    def get(self, request, pk):
        token = self._get_token(request, pk)
        if not token:
            return Response({'code': 404, 'message': '令牌不存在'}, status=status.HTTP_404_NOT_FOUND)
        return Response({'code': 200, 'message': 'ok', 'data': APITokenListSerializer(token).data})

    @extend_schema(
        tags=['API 令牌'],
        summary='更新令牌名称/状态',
        request=inline_serializer(name='TokenUpdateReq', fields={
            'name':      s.CharField(required=False),
            'is_active': s.BooleanField(required=False),
        }),
        responses={200: APITokenListSerializer},
    )
    def patch(self, request, pk):
        token = self._get_token(request, pk)
        if not token:
            return Response({'code': 404, 'message': '令牌不存在'}, status=status.HTTP_404_NOT_FOUND)
        if 'name' in request.data:
            token.name = request.data['name']
        if 'is_active' in request.data:
            token.is_active = bool(request.data['is_active'])
        token.save(update_fields=['name', 'is_active'])
        return Response({'code': 200, 'message': '已更新', 'data': APITokenListSerializer(token).data})

    @extend_schema(
        tags=['API 令牌'],
        summary='撤销（删除）令牌',
        responses={200: OpenApiResponse(description='删除成功')},
    )
    def delete(self, request, pk):
        token = self._get_token(request, pk)
        if not token:
            return Response({'code': 404, 'message': '令牌不存在'}, status=status.HTTP_404_NOT_FOUND)
        name = token.name
        token.delete()
        return Response({'code': 200, 'message': f'令牌 "{name}" 已撤销'})


class APITokenRevokeAllView(APIView):
    """POST /api/accounts/tokens/revoke_all/  撤销当前用户所有令牌"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['API 令牌'],
        summary='撤销所有令牌',
        description='撤销当前用户的全部 API 令牌（管理员可通过 user_id 字段撤销他人令牌）。',
        request=inline_serializer(name='RevokeAllReq',
                                  fields={'user_id': s.IntegerField(required=False)}),
        responses={200: OpenApiResponse(description='操作成功')},
    )
    def post(self, request):
        target_user = request.user
        if (request.user.is_staff or request.user.is_superuser) and 'user_id' in request.data:
            try:
                target_user = User.objects.get(pk=request.data['user_id'])
            except User.DoesNotExist:
                return Response({'code': 404, 'message': '目标用户不存在'}, status=status.HTTP_404_NOT_FOUND)
        count, _ = APIToken.objects.filter(user=target_user).delete()
        return Response({'code': 200, 'message': f'已撤销 {count} 个令牌'})


class APITokenVerifyView(APIView):
    """POST /api/accounts/tokens/verify/  验证 API Token 是否有效（无需登录）"""
    permission_classes = []  # 公开接口
    authentication_classes = []
    throttle_classes = [PublicTokenVerifyRateThrottle]

    @extend_schema(
        tags=['API 令牌'],
        summary='验证 API Token',
        description='校验原始令牌是否有效，可用于第三方系统接入前的预检。',
        request=inline_serializer(name='TokenVerifyReq',
                                  fields={'token': s.CharField()}),
        responses={
            200: inline_serializer(name='TokenVerifyResp', fields={
                'valid':    s.BooleanField(),
                'username': s.CharField(allow_null=True),
                'expires_at': s.DateTimeField(allow_null=True),
            }),
        },
        auth=[],
    )
    def post(self, request):
        raw = request.data.get('token', '').strip()
        if not raw:
            return Response({'code': 400, 'message': '请提供 token 字段'}, status=400)
        user, token_obj = APIToken.authenticate(raw)
        if token_obj is None:
            return Response({'code': 200, 'message': 'ok',
                             'data': {'valid': False, 'username': None, 'expires_at': None}})
        return Response({'code': 200, 'message': 'ok', 'data': {
            'valid':      token_obj.is_valid,
            'username':   user.username if user else None,
            'expires_at': token_obj.expires_at,
        }})


class RoleCatalogAccess(IsAdminUser):
    """管理员，或拥有权限管理查看能力的账号，可读取角色目录。"""
    message = '无权查看角色权限目录'

    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        return check_permission(request.user, 'accounts.role.view') or check_permission(
            request.user, 'accounts.view'
        )


class RolePermissionCatalogView(APIView):
    """
    GET /api/accounts/roles/
    返回系统角色及其权限组映射（供管理界面配置参考）。
    """
    permission_classes = [IsAuthenticated, RoleCatalogAccess]

    @extend_schema(
        tags=['用户管理'],
        summary='获取角色与权限组目录',
        description='返回各系统角色对应的权限码及权限组明细。',
    )
    def get(self, request):
        ensure_permission_defaults()
        return Response({
            'code': 200,
            'message': 'ok',
            'data': {
                'roles': serialize_role_catalog(),
                'permission_groups': serialize_permission_groups(),
                'action_meta': serialize_action_meta(),
                'role_labels': ROLE_LABELS,
                'editable': True,
                'account_types': [
                    {'value': k, 'label': v}
                    for k, v in UserProfile.AccountType.choices
                ],
            },
        })


class RolePermissionUpdateView(APIView):
    """GET/PUT /api/accounts/roles/<role>/permissions/"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def _check_role(self, role: str):
        valid = dict(UserProfile.Role.choices)
        if role not in valid:
            return Response({'code': 404, 'message': f'角色 {role} 不存在'}, status=404)
        return None

    @extend_schema(tags=['用户管理'], summary='获取角色已授予的权限码')
    def get(self, request, role):
        err = self._check_role(role)
        if err:
            return err
        ensure_permission_defaults()
        return Response({
            'code': 200,
            'message': 'ok',
            'data': {
                'role': role,
                'role_label': ROLE_LABELS.get(role, role),
                'permissions': get_role_permission_codes(role),
            },
        })

    @extend_schema(tags=['用户管理'], summary='更新角色权限')
    def put(self, request, role):
        err = self._check_role(role)
        if err:
            return err
        if role == UserProfile.Role.SUPERADMIN and not request.user.is_superuser:
            return Response({'code': 403, 'message': '仅超级管理员可修改超级管理员角色权限'}, status=403)
        permissions = request.data.get('permissions')
        if not isinstance(permissions, list):
            return Response({'code': 400, 'message': '请提供 permissions 数组'}, status=400)
        try:
            saved = set_role_permissions(role, permissions)
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=400)
        return Response({
            'code': 200,
            'message': '角色权限已更新',
            'data': {
                'role': role,
                'permissions': sorted(saved),
            },
        })


class PermissionResetDefaultsView(APIView):
    """POST /api/accounts/permissions/reset-defaults/  恢复内置权限目录与角色授权"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['用户管理'], summary='恢复默认权限配置')
    def post(self, request):
        if not request.user.is_superuser:
            return Response({'code': 403, 'message': '仅超级管理员可恢复默认权限'}, status=403)
        result = ensure_permission_defaults(force=True)
        return Response({
            'code': 200,
            'message': '已恢复默认权限配置',
            'data': result,
        })


class PermissionGroupUpdateView(APIView):
    """PATCH /api/accounts/permission-groups/<key>/"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['用户管理'], summary='更新权限模块信息')
    def patch(self, request, key):
        ensure_permission_defaults()
        try:
            group = update_permission_group(key, request.data)
        except PermissionGroup.DoesNotExist:
            return Response({'code': 404, 'message': '模块不存在'}, status=404)
        return Response({
            'code': 200,
            'message': '已更新',
            'data': {
                'key': group.key,
                'label': group.label,
                'description': group.description,
                'sort_order': group.sort_order,
            },
        })


class PermissionPageManageView(APIView):
    """
    POST   /api/accounts/permission-pages/          新建页面
    PATCH  /api/accounts/permission-pages/<group_key>/<page_key>/  更新页面
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['用户管理'], summary='新建权限页面')
    def post(self, request):
        ensure_permission_defaults()
        group_key = request.data.get('group_key', '').strip()
        page_key = request.data.get('key', '').strip()
        label = request.data.get('label', '').strip()
        route = request.data.get('route', '').strip()
        if not group_key or not page_key or not label:
            return Response({'code': 400, 'message': '请提供 group_key、key、label'}, status=400)
        try:
            page = create_permission_page(group_key, page_key, label, route)
        except PermissionGroup.DoesNotExist:
            return Response({'code': 404, 'message': '模块不存在'}, status=404)
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=400)
        return Response({
            'code': 200,
            'message': '页面已创建',
            'data': {
                'group_key': group_key,
                'key': page.key,
                'label': page.label,
                'route': page.route,
            },
        }, status=201)

    @extend_schema(tags=['用户管理'], summary='更新权限页面')
    def patch(self, request, group_key, page_key):
        ensure_permission_defaults()
        try:
            page = update_permission_page(group_key, page_key, request.data)
        except PermissionPage.DoesNotExist:
            return Response({'code': 404, 'message': '页面不存在'}, status=404)
        return Response({
            'code': 200,
            'message': '已更新',
            'data': {
                'group_key': group_key,
                'key': page.key,
                'label': page.label,
                'route': page.route,
            },
        })


class PermissionDefinitionManageView(APIView):
    """
    POST   /api/accounts/permissions/           创建自定义权限
    PATCH  /api/accounts/permissions/<code>/  更新权限
    DELETE /api/accounts/permissions/<code>/  删除自定义权限
    """
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['用户管理'], summary='创建自定义权限')
    def post(self, request):
        ensure_permission_defaults()
        code = request.data.get('code', '').strip()
        label = request.data.get('label', '').strip()
        action = request.data.get('action', 'view').strip()
        group_key = request.data.get('group_key', '').strip()
        if not code or not label or not group_key:
            return Response({'code': 400, 'message': '请提供 code、label、group_key'}, status=400)
        try:
            perm = create_custom_permission(
                code, label, action, group_key,
                page_key=request.data.get('page_key', ''),
            )
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=400)
        return Response({
            'code': 200,
            'message': '权限已创建',
            'data': {'code': perm.code, 'label': perm.label, 'action': perm.action},
        }, status=201)

    @extend_schema(tags=['用户管理'], summary='更新权限定义')
    def patch(self, request, code):
        ensure_permission_defaults()
        try:
            perm = update_permission_definition(code, request.data)
        except PermissionDefinition.DoesNotExist:
            return Response({'code': 404, 'message': '权限不存在'}, status=404)
        return Response({
            'code': 200,
            'message': '已更新',
            'data': {
                'code': perm.code,
                'label': perm.label,
                'is_active': perm.is_active,
            },
        })

    @extend_schema(tags=['用户管理'], summary='删除自定义权限')
    def delete(self, request, code):
        ensure_permission_defaults()
        try:
            delete_custom_permission(code)
        except PermissionDefinition.DoesNotExist:
            return Response({'code': 404, 'message': '权限不存在'}, status=404)
        except ValueError as exc:
            return Response({'code': 400, 'message': str(exc)}, status=400)
        return Response({'code': 200, 'message': '已删除'})


class CurrentUserPermissionsView(APIView):
    """GET /api/accounts/me/permissions/  当前用户有效权限"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['用户管理'],
        summary='获取当前用户权限',
        description='返回当前登录用户的角色、账号类型及有效权限列表。',
    )
    def get(self, request):
        return Response({
            'code': 200,
            'message': 'ok',
            'data': serialize_auth_user(request.user),
        })


# ─────────────────────────────────────────────────────────────
# 管理员查看所有 Token（总览）
# ─────────────────────────────────────────────────────────────

class AdminAllTokensView(APIView):
    """GET /api/accounts/admin/tokens/  管理员查看所有用户的令牌"""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(
        tags=['用户管理'],
        summary='管理员查看所有 API 令牌',
        responses={200: APITokenListSerializer(many=True)},
    )
    def get(self, request):
        tokens = APIToken.objects.select_related('user').all()
        return Response({
            'code': 200, 'message': 'ok',
            'data': {
                'count': tokens.count(),
                'results': APITokenListSerializer(tokens, many=True).data,
            },
        })
