"""S-M3：支付 Webhook 按 IP 限流。"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


def _rest_with_rates(**overrides):
    from django.conf import settings
    framework = dict(settings.REST_FRAMEWORK)
    rates = dict(framework.get('DEFAULT_THROTTLE_RATES') or {})
    rates.update(overrides)
    framework['DEFAULT_THROTTLE_RATES'] = rates
    return framework


class PaymentWebhookThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @override_settings(REST_FRAMEWORK=_rest_with_rates(public_payment_webhook='1/min'))
    def test_second_webhook_from_same_ip_is_throttled(self):
        first = self.client.post('/api/customer/payments/webhooks/99999/', {}, format='json')
        self.assertEqual(first.status_code, 404)

        second = self.client.post('/api/customer/payments/webhooks/99999/', {}, format='json')
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.data['code'], 429)
