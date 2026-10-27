"""
accounts/authentication.py
API Token 认证后端（Authorization: Token <raw_token>）
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import APIToken


class APITokenAuthentication(BaseAuthentication):
    """
    自定义 API Token 认证。
    客户端请求头格式：
        Authorization: Token ispm_xxxxx...
    或：
        X-API-Token: ispm_xxxxx...

    与 JWT 认证并列——先由 JWTAuthentication 处理 Bearer 头，
    若 Authorization 头以 "Token " 开头则由本类处理。
    """

    keyword = 'Token'

    def authenticate(self, request):
        # 优先从 Authorization: Token <raw> 中取
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        raw_token = None

        if auth_header.startswith(self.keyword + ' '):
            raw_token = auth_header[len(self.keyword) + 1:].strip()
        else:
            # 备用：X-API-Token 头
            raw_token = request.META.get('HTTP_X_API_TOKEN', '').strip() or None

        if not raw_token:
            return None  # 不是本认证方式，交给下一个认证类

        user, token_obj = APIToken.authenticate(raw_token)

        if token_obj is None:
            raise AuthenticationFailed('API Token 无效')

        if token_obj.is_expired:
            raise AuthenticationFailed('API Token 已过期')

        if not token_obj.is_active:
            raise AuthenticationFailed('API Token 已被禁用')

        if user is None or not user.is_active:
            raise AuthenticationFailed('关联用户不存在或已禁用')

        # 异步记录使用情况（不影响响应性能）
        ip = self._get_client_ip(request)
        token_obj.update_last_used(ip=ip)

        return user, token_obj  # (user, auth) 元组

    def authenticate_header(self, request):
        return self.keyword

    @staticmethod
    def _get_client_ip(request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
