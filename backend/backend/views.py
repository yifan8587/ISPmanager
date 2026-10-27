"""
backend/views.py
认证视图、用户信息、统一异常处理
"""
import logging

from django.contrib.auth.models import User
from rest_framework import serializers as drf_serializers, status
from rest_framework.exceptions import (
    AuthenticationFailed, NotAuthenticated, PermissionDenied,
    ValidationError, NotFound, MethodNotAllowed, Throttled,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiResponse

logger = logging.getLogger(__name__)


def _record_auth_event(request, *, success, user=None, action_type=None, description=None):
    """记录登录 / 改密等认证审计，失败不影响主流程。"""
    try:
        from log_manager.services import record_audit_log
        username = ''
        try:
            username = str((getattr(request, 'data', None) or {}).get('username') or '')
        except Exception:
            username = ''
        if action_type is None:
            action_type = 'login' if success else 'login_failed'
        if description is None:
            description = '登录成功' if success else f'登录失败（用户 {username or "未知"}）'
        operator = user if success else None
        operator_name = ''
        if user is not None:
            operator_name = user.get_full_name() or user.username
        elif username:
            operator_name = username
        record_audit_log(
            module='accounts',
            action_type=action_type,
            description=description,
            operator=operator,
            operator_name=operator_name or '未知',
            target_model='User',
            target_id=getattr(user, 'pk', None),
            target_repr=getattr(user, 'username', username),
            request=request,
            source='auth',
        )
    except Exception:
        logger.exception('记录认证审计失败')


# ─────────────────────────────────────────────────────────────
# 统一异常处理
# ─────────────────────────────────────────────────────────────

def _first_error_message(data):
    """从 DRF 嵌套字段错误中提取可直接展示的第一条具体原因。"""
    if isinstance(data, dict):
        for value in data.values():
            message = _first_error_message(value)
            if message:
                return message
        return ''
    if isinstance(data, (list, tuple)):
        for value in data:
            message = _first_error_message(value)
            if message:
                return message
        return ''
    return str(data) if data not in (None, '') else ''


def _is_idle_timeout(exc, response) -> bool:
    if getattr(exc, 'get_codes', lambda: None)() == 'idle_timeout':
        return True
    data = getattr(response, 'data', None)
    if not isinstance(data, dict):
        return False
    if data.get('code') == 'idle_timeout':
        return True
    detail = data.get('detail')
    return isinstance(detail, dict) and detail.get('code') == 'idle_timeout'


def custom_exception_handler(exc, context):
    """
    统一错误响应格式:
    {"code": <int>, "message": <str>, "detail": <any>}
    """
    from rest_framework.views import exception_handler
    response = exception_handler(exc, context)
    if response is None:
        return None

    error_map = {
        NotAuthenticated:  (401, '请先登录，携带有效的 Bearer Token 后重试'),
        AuthenticationFailed: (401, '认证失败，Token 无效或已过期'),
        NotFound:          (404, '请求的资源不存在'),
        MethodNotAllowed:  (405, '不支持该请求方法'),
        Throttled:         (429, '请求过于频繁，请稍后重试'),
    }
    if isinstance(exc, AuthenticationFailed) and _is_idle_timeout(exc, response):
        return Response(
            {'code': 'idle_timeout', 'message': '登录已超时，请重新登录', 'detail': '登录已超时，请重新登录'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if isinstance(exc, PermissionDenied):
        detail_msg = _first_error_message(response.data)
        generic = {
            'You do not have permission to perform this action.',
            '权限不足，无法执行此操作',
        }
        message = (
            detail_msg
            if detail_msg and detail_msg not in generic
            else '权限不足，无法执行此操作'
        )
        return Response(
            {'code': 403, 'message': message, 'detail': response.data},
            status=response.status_code,
        )
    for exc_type, (code, msg) in error_map.items():
        if isinstance(exc, exc_type):
            return Response(
                {'code': code, 'message': msg, 'detail': response.data},
                status=response.status_code,
            )
    if isinstance(exc, ValidationError):
        message = _first_error_message(response.data) or '请求数据校验失败'
        return Response(
            {'code': 400, 'message': message, 'detail': response.data},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(
        {'code': response.status_code, 'message': '请求处理失败', 'detail': response.data},
        status=response.status_code,
    )


# ─────────────────────────────────────────────────────────────
# 自定义 JWT Token（登录响应包含用户信息）
# ─────────────────────────────────────────────────────────────

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        from accounts.idle_session import touch_login_activity
        from accounts.serializers import serialize_auth_user
        touch_login_activity(self.user, force=True)
        data['user'] = serialize_auth_user(self.user)
        return data


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    POST /api/auth/token/
    用户名+密码登录，返回 access/refresh token 及用户信息。
    access token 有效期 2 小时，refresh token 有效期 7 天。
    """
    serializer_class = CustomTokenObtainPairSerializer

    @extend_schema(
        tags=['认证'],
        summary='用户登录',
        description=(
            '使用用户名和密码登录，返回 JWT access/refresh token 及当前用户信息。\n\n'
            '**后续所有 API 请求须在 Header 中携带：**\n'
            '```\nAuthorization: Bearer <access_token>\n```'
        ),
        request=inline_serializer(
            name='LoginRequest',
            fields={
                'username': drf_serializers.CharField(),
                'password': drf_serializers.CharField(),
            },
        ),
        responses={
            200: inline_serializer(
                name='LoginResponse',
                fields={
                    'code': drf_serializers.IntegerField(),
                    'message': drf_serializers.CharField(),
                    'data': inline_serializer(
                        name='LoginData',
                        fields={
                            'access': drf_serializers.CharField(help_text='访问 Token（2小时有效）'),
                            'refresh': drf_serializers.CharField(help_text='刷新 Token（7天有效）'),
                            'user': inline_serializer(
                                name='LoginUser',
                                fields={
                                    'id': drf_serializers.IntegerField(),
                                    'username': drf_serializers.CharField(),
                                    'email': drf_serializers.CharField(),
                                    'is_staff': drf_serializers.BooleanField(),
                                    'is_superuser': drf_serializers.BooleanField(),
                                },
                            ),
                        },
                    ),
                },
            ),
            401: OpenApiResponse(description='用户名或密码错误'),
        },
        auth=[],
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            _record_auth_event(request, success=False)
            raise InvalidToken(e.args[0])
        except (AuthenticationFailed, ValidationError):
            _record_auth_event(request, success=False)
            raise
        _record_auth_event(request, success=True, user=serializer.user)
        return Response(
            {'code': 200, 'message': '登录成功', 'data': serializer.validated_data},
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────
# Serializers
# ─────────────────────────────────────────────────────────────

class UserProfileSerializer(drf_serializers.ModelSerializer):
    full_name = drf_serializers.SerializerMethodField()
    has_customer = drf_serializers.SerializerMethodField()
    role = drf_serializers.SerializerMethodField()
    role_label = drf_serializers.SerializerMethodField()
    account_type = drf_serializers.SerializerMethodField()
    account_type_label = drf_serializers.SerializerMethodField()
    permissions = drf_serializers.SerializerMethodField()
    customer_id = drf_serializers.SerializerMethodField()
    customer_name = drf_serializers.SerializerMethodField()
    department = drf_serializers.SerializerMethodField()
    phone = drf_serializers.SerializerMethodField()
    position = drf_serializers.SerializerMethodField()
    avatar = drf_serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'full_name',
            'is_staff', 'is_superuser', 'date_joined', 'last_login', 'has_customer',
            'role', 'role_label', 'account_type', 'account_type_label', 'permissions',
            'customer_id', 'customer_name', 'department', 'phone', 'position', 'avatar',
        ]

    def get_full_name(self, obj) -> str:
        return obj.get_full_name() or obj.username

    def get_has_customer(self, obj) -> bool:
        return hasattr(obj, 'customer')

    def _auth_ctx(self, obj):
        from accounts.permissions import build_user_auth_context
        return build_user_auth_context(obj)

    def get_role(self, obj):
        return self._auth_ctx(obj)['role']

    def get_role_label(self, obj):
        return self._auth_ctx(obj)['role_label']

    def get_account_type(self, obj):
        return self._auth_ctx(obj)['account_type']

    def get_account_type_label(self, obj):
        return self._auth_ctx(obj)['account_type_label']

    def get_permissions(self, obj):
        return self._auth_ctx(obj)['permissions']

    def get_customer_id(self, obj):
        return self._auth_ctx(obj)['customer_id']

    def get_customer_name(self, obj):
        return self._auth_ctx(obj)['customer_name']

    def get_department(self, obj):
        return self._auth_ctx(obj)['department']

    def get_phone(self, obj):
        return self._auth_ctx(obj)['phone']

    def get_position(self, obj):
        return self._auth_ctx(obj)['position']

    def get_avatar(self, obj):
        return self._auth_ctx(obj)['avatar']


class UserUpdateSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']


class ChangePasswordSerializer(drf_serializers.Serializer):
    old_password = drf_serializers.CharField(write_only=True, help_text='当前密码')
    new_password = drf_serializers.CharField(write_only=True, min_length=8, help_text='新密码（至少8位）')
    confirm_password = drf_serializers.CharField(write_only=True, help_text='确认新密码')

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise drf_serializers.ValidationError({'confirm_password': '两次密码输入不一致'})
        return data


# ─────────────────────────────────────────────────────────────
# 用户信息接口
# ─────────────────────────────────────────────────────────────

class UserProfileView(APIView):
    """
    GET   /api/auth/me/   获取当前用户信息
    PATCH /api/auth/me/   更新用户信息（姓名/邮箱）
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['认证'], summary='获取当前用户信息',
        responses={200: UserProfileSerializer},
    )
    def get(self, request):
        return Response({
            'code': 200, 'message': 'ok',
            'data': UserProfileSerializer(request.user).data,
        })

    @extend_schema(
        tags=['认证'], summary='更新用户信息',
        request=UserUpdateSerializer,
        responses={200: UserProfileSerializer},
    )
    def patch(self, request):
        s = UserUpdateSerializer(request.user, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response({
            'code': 200, 'message': '更新成功',
            'data': UserProfileSerializer(request.user).data,
        })


class ChangePasswordView(APIView):
    """POST /api/auth/me/change_password/  修改密码"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['认证'], summary='修改密码',
        request=ChangePasswordSerializer,
        responses={
            200: inline_serializer(
                name='ChangePasswordResp',
                fields={'code': drf_serializers.IntegerField(),
                        'message': drf_serializers.CharField()},
            ),
            400: OpenApiResponse(description='原密码错误或两次密码不一致'),
        },
    )
    def post(self, request):
        s = ChangePasswordSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not request.user.check_password(s.validated_data['old_password']):
            return Response(
                {'code': 400, 'message': '原密码错误'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        request.user.set_password(s.validated_data['new_password'])
        request.user.save(update_fields=['password'])
        _record_auth_event(
            request,
            success=True,
            user=request.user,
            action_type='change_password',
            description='修改登录密码',
        )
        return Response({'code': 200, 'message': '密码修改成功，请重新登录'})


class UserListView(APIView):
    """GET /api/auth/users/  获取系统用户列表（下拉选择用）"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['认证'], summary='获取用户列表',
        description='返回所有激活用户的简要信息，供前端下拉框使用。',
        responses={
            200: inline_serializer(
                name='UserListResp',
                fields={
                    'code': drf_serializers.IntegerField(),
                    'message': drf_serializers.CharField(),
                    'data': inline_serializer(
                        name='UserItem',
                        fields={
                            'id': drf_serializers.IntegerField(),
                            'username': drf_serializers.CharField(),
                            'full_name': drf_serializers.CharField(),
                            'email': drf_serializers.CharField(),
                        },
                        many=True,
                    ),
                },
            ),
        },
    )
    def get(self, request):
        users = User.objects.filter(is_active=True).order_by('username')
        data = [
            {'id': u.id, 'username': u.username,
             'full_name': u.get_full_name() or u.username,
             'email': u.email}
            for u in users
        ]
        return Response({'code': 200, 'message': 'ok', 'data': data})
