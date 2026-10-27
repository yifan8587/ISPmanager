from rest_framework.throttling import SimpleRateThrottle


class PublicIpRateThrottle(SimpleRateThrottle):
    """按客户端 IP 限流的公开接口基类。速率每次从 Django settings 读取，便于测试覆盖。"""

    def get_rate(self):
        from django.conf import settings
        rates = (getattr(settings, 'REST_FRAMEWORK', None) or {}).get('DEFAULT_THROTTLE_RATES') or {}
        if self.scope in rates:
            return rates[self.scope]
        return super().get_rate()

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class PublicRegisterRateThrottle(PublicIpRateThrottle):
    scope = 'public_register'


class PublicDeliveryCallbackRateThrottle(PublicIpRateThrottle):
    scope = 'public_delivery_callback'


class PublicPaymentWebhookRateThrottle(PublicIpRateThrottle):
    scope = 'public_payment_webhook'


class PublicTokenVerifyRateThrottle(PublicIpRateThrottle):
    scope = 'public_token_verify'
