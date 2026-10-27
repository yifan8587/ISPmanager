from rest_framework.throttling import SimpleRateThrottle


class PublicInquiryRateThrottle(SimpleRateThrottle):
    """限制匿名访客提交频率，避免公开咨询接口被滥用。"""

    scope = 'public_inquiry'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class CustomerMonitoringRateThrottle(SimpleRateThrottle):
    """按已认证客户账户限流高成本的实时监控查询。"""

    scope = 'portal_monitoring'

    def get_cache_key(self, request, view):
        user = getattr(request, 'user', None)
        ident = f'user:{user.pk}' if getattr(user, 'is_authenticated', False) else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}
