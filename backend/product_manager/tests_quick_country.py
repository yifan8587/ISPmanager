"""门户快速查找国家：管理端可配置，公开位置接口只返回启用项。"""
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from django.test import TestCase

from product_manager.models import PortalCountryShortcut
from resource_manager.models import Country, Region


class PortalCountryShortcutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('quick-country', 'qc@example.test', 'password')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        region = Region.objects.create(code='qc', name='快速区')
        self.brazil = Country.objects.create(region=region, name='巴西')
        self.japan = Country.objects.create(region=region, name='日本')

    def test_blank_display_name_uses_country_and_portal_hides_inactive(self):
        shown = PortalCountryShortcut.objects.create(country=self.brazil, sort_order=1)
        hidden = PortalCountryShortcut.objects.create(
            country=self.japan, display_name='自定义日本', sort_order=0, is_active=False,
        )
        self.assertEqual(shown.label, '巴西')
        self.assertEqual(hidden.label, '自定义日本')

        res = self.client.get('/api/portal/locations/')
        self.assertEqual(res.status_code, 200)
        names = [item['name'] for item in res.data['data']['quick_countries']]
        self.assertEqual(names, ['巴西'])
        self.assertEqual(res.data['data']['quick_countries'][0]['country'], self.brazil.id)

        created = self.client.post('/api/product/country-shortcuts/', {
            'country': self.japan.id,
            'display_name': '',
            'sort_order': 2,
            'is_active': True,
        }, format='json')
        self.assertEqual(created.status_code, 400)

        hidden.is_active = True
        hidden.display_name = ''
        hidden.save()
        res = self.client.get('/api/portal/locations/')
        names = [item['name'] for item in res.data['data']['quick_countries']]
        self.assertEqual(names, ['日本', '巴西'])
