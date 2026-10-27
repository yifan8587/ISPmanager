from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import UserProfile


class IdleSessionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('idle-user', password='SafePass_2026!')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)

    def _login(self):
        res = self.client.post('/api/auth/token/', {
            'username': 'idle-user',
            'password': 'SafePass_2026!',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        data = res.data['data']
        return data['access'], data['refresh']

    def test_login_sets_last_seen_and_allows_me(self):
        access, _refresh = self._login()
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.last_seen_at)
        res = self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(res.status_code, 200, res.data)

    def test_missing_last_seen_requires_relogin(self):
        access = str(RefreshToken.for_user(self.user).access_token)
        UserProfile.objects.filter(pk=self.profile.pk).update(last_seen_at=None)
        res = self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(res.status_code, 401, res.data)
        self.assertEqual(res.data.get('code'), 'idle_timeout')

    def test_idle_blocks_access_and_refresh(self):
        access, refresh = self._login()
        UserProfile.objects.filter(pk=self.profile.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=31),
        )
        me = self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(me.status_code, 401, me.data)
        self.assertEqual(me.data.get('code'), 'idle_timeout')
        self.assertIn('登录已超时', me.data.get('message', ''))

        refreshed = self.client.post(
            '/api/auth/token/refresh/',
            {'refresh': refresh},
            format='json',
        )
        self.assertEqual(refreshed.status_code, 401, refreshed.data)
        self.assertEqual(refreshed.data.get('code'), 'idle_timeout')

    def test_activity_header_extends_only_while_session_is_alive(self):
        access, _refresh = self._login()
        old = timezone.now() - timedelta(minutes=2)
        UserProfile.objects.filter(pk=self.profile.pk).update(last_seen_at=old)

        quiet = self.client.get('/api/auth/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(quiet.status_code, 200, quiet.data)
        self.profile.refresh_from_db()
        self.assertLess(self.profile.last_seen_at, timezone.now() - timedelta(seconds=60))

        active = self.client.get(
            '/api/auth/me/',
            HTTP_AUTHORIZATION=f'Bearer {access}',
            HTTP_X_USER_ACTIVITY='1',
        )
        self.assertEqual(active.status_code, 200, active.data)
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.last_seen_at, timezone.now() - timedelta(seconds=15))

        UserProfile.objects.filter(pk=self.profile.pk).update(
            last_seen_at=timezone.now() - timedelta(minutes=31),
        )
        revived = self.client.get(
            '/api/auth/me/',
            HTTP_AUTHORIZATION=f'Bearer {access}',
            HTTP_X_USER_ACTIVITY='1',
        )
        self.assertEqual(revived.status_code, 401, revived.data)
        self.assertEqual(revived.data.get('code'), 'idle_timeout')
