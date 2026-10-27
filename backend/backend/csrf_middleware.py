"""兼容反代映射端口的 CSRF 校验。

Django 默认要求 Origin 与「协议 + get_host()」完全一致。
生产经面板/Docker 把 80/443 映到 :30090 时：

- 浏览器 Origin = http://www.example.com:30090
- nginx $host / X-Forwarded-Host 常是 www.example.com（无端口）
- CSRF_TRUSTED_ORIGINS 若只按 80/443 生成，登录 POST 必 403

本中间件在标准校验失败后，允许「Origin/Referer 主机名 = 当前请求主机名」。
"""
from django.middleware.csrf import CsrfViewMiddleware, RejectRequest

from .csrf_origins import origin_matches_request_host


class CompatibleCsrfViewMiddleware(CsrfViewMiddleware):
    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True
        return origin_matches_request_host(request.META.get('HTTP_ORIGIN', ''), request)

    def _check_referer(self, request):
        try:
            return super()._check_referer(request)
        except RejectRequest:
            referer = request.META.get('HTTP_REFERER', '')
            if referer and origin_matches_request_host(referer, request):
                return None
            raise
