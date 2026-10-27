"""S-M2：人工交付公开回传限流。"""
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


class DeliveryCallbackThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @override_settings(REST_FRAMEWORK=_rest_with_rates(public_delivery_callback='1/min'))
    def test_second_callback_from_same_ip_is_throttled(self):
        first = self.client.post(
            '/api/service/manual-delivery/callback/',
            {'token': 'mdt_invalid', 'complete': True},
            format='json',
        )
        self.assertEqual(first.status_code, 401)

        second = self.client.post(
            '/api/service/manual-delivery/callback/',
            {'token': 'mdt_invalid', 'complete': True},
            format='json',
        )
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.data['code'], 429)
