"""登录空闲超时：30 分钟无操作后，访问令牌与刷新令牌都失效。"""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

IDLE_MESSAGE = '登录已超时，请重新登录'
ACTIVITY_HEADER = 'X-User-Activity'
# 连续操作时最多每分钟写一次最近操作时间
TOUCH_INTERVAL_SECONDS = 60


def idle_timeout_seconds() -> int:
    return int(getattr(settings, 'AUTH_IDLE_TIMEOUT_SECONDS', 30 * 60))


def _profile(user):
    from accounts.models import UserProfile
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def login_is_idle(user) -> bool:
    if user is None or not getattr(user, 'is_authenticated', False):
        return True
    profile = _profile(user)
    seen = getattr(profile, 'last_seen_at', None) if profile else None
    if seen is None:
        return True
    return (timezone.now() - seen).total_seconds() > idle_timeout_seconds()


def touch_login_activity(user, *, force: bool = False) -> None:
    """记录用户仍在操作。force 用于登录成功，忽略节流。"""
    if user is None or not getattr(user, 'pk', None):
        return
    from accounts.models import UserProfile
    profile = _profile(user)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    now = timezone.now()
    if not force and profile.last_seen_at is not None:
        elapsed = (now - profile.last_seen_at).total_seconds()
        if elapsed < TOUCH_INTERVAL_SECONDS:
            return
    UserProfile.objects.filter(pk=profile.pk).update(last_seen_at=now)
    profile.last_seen_at = now


def request_reports_activity(request) -> bool:
    if request is None:
        return False
    return (request.headers.get(ACTIVITY_HEADER) or '') == '1'


class IdleJWTAuthentication(JWTAuthentication):
    """校验 JWT 后执行空闲超时；仅在用户有操作时顺延。"""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        if login_is_idle(user):
            raise AuthenticationFailed(IDLE_MESSAGE, code='idle_timeout')
        if request_reports_activity(request):
            touch_login_activity(user)
        return user, validated_token


class IdleTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        refresh = self.token_class(attrs['refresh'])
        user_id = refresh.payload.get('user_id')
        user = User.objects.filter(pk=user_id).first()
        if user is None or login_is_idle(user):
            raise InvalidToken(IDLE_MESSAGE, code='idle_timeout')
        return super().validate(attrs)
