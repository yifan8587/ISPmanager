"""S2：Token 校验限流、客户不可自助签发、WS 子协议取令牌。"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import APIToken, UserProfile
from customer_manager.models import Customer
from resource_manager.ws_auth import chosen_ws_subprotocol, extract_ws_token


def _rest_with_rates(**overrides):
    from django.conf import settings
    framework = dict(settings.REST_FRAMEWORK)
    rates = dict(framework.get('DEFAULT_THROTTLE_RATES') or {})
    rates.update(overrides)
    framework['DEFAULT_THROTTLE_RATES'] = rates
    return framework


class TokenVerifyThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @override_settings(REST_FRAMEWORK=_rest_with_rates(public_token_verify='1/min'))
    def test_second_verify_from_same_ip_is_throttled(self):
        first = self.client.post('/api/accounts/tokens/verify/', {'token': 'ispm_invalid'}, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.data['data']['valid'])

        second = self.client.post('/api/accounts/tokens/verify/', {'token': 'ispm_another'}, format='json')
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.data['code'], 429)


class CustomerCannotIssueApiTokenTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.customer_user = User.objects.create_user('s2-cust', password='SafePass_2026!')
        Customer.objects.create(
            name='S2 Token 客户',
            contact_name='甲',
            account=self.customer_user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.customer_user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])

        self.admin = User.objects.create_superuser('s2-admin', 's2@example.test', 'password')

    def test_customer_can_list_but_not_create(self):
        self.client.force_authenticate(self.customer_user)
        listed = self.client.get('/api/accounts/tokens/')
        self.assertEqual(listed.status_code, 200)

        created = self.client.post('/api/accounts/tokens/', {'name': '自助令牌'}, format='json')
        self.assertEqual(created.status_code, 403)
        self.assertFalse(APIToken.objects.filter(user=self.customer_user).exists())

    def test_system_account_can_create(self):
        self.client.force_authenticate(self.admin)
        created = self.client.post('/api/accounts/tokens/', {'name': '管理令牌'}, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data['data']['token'])


class WsTokenExtractTests(TestCase):
    def test_query_token_takes_precedence(self):
        scope = {
            'query_string': b'token=from-query',
            'headers': [(b'sec-websocket-protocol', b'access_token, from-proto')],
        }
        self.assertEqual(extract_ws_token(scope), 'from-query')

    def test_protocol_access_token_pair(self):
        scope = {
            'query_string': b'',
            'headers': [(b'sec-websocket-protocol', b'access_token, header.jwt.token')],
        }
        self.assertEqual(extract_ws_token(scope), 'header.jwt.token')
        self.assertEqual(chosen_ws_subprotocol(scope), 'access_token')

    def test_jwt_prefixed_protocol(self):
        scope = {
            'query_string': b'',
            'headers': [(b'sec-websocket-protocol', b'jwt.abc.def.ghi')],
        }
        self.assertEqual(extract_ws_token(scope), 'abc.def.ghi')

    def test_vnc_prefers_binary(self):
        scope = {
            'query_string': b'token=keep',
            'headers': [(b'sec-websocket-protocol', b'binary')],
        }
        self.assertEqual(extract_ws_token(scope), 'keep')
        self.assertEqual(chosen_ws_subprotocol(scope, prefer='binary'), 'binary')
