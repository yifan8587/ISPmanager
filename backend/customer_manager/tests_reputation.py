from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region

from .channel_services import configure_channel_profile
from .models import Customer
from .reputation import apply_reputation_settings, refresh_reputation


class ReputationGrantAndPayTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='rep-apac', name='信誉值亚太')
        country = Country.objects.create(region=region, name='信誉值国')
        city = City.objects.create(country=country, name='信誉值市')
        self.pop = PopPoint.objects.create(
            code='REPPOP',
            name='信誉值 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
        )
        self.product = Product.objects.create(
            name='信誉值测试产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price='80.00',
            reputation_price='100.00',
            setup_price='0.00',
            currency='USD',
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=5,
            ip_count=1,
            allow_reputation_pay=True,
        )
        self.channel = Customer.objects.create(
            name='授信渠道',
            customer_type='channel',
            status='active',
            contact_name='渠道联系人',
        )
        self.channel_profile = configure_channel_profile(
            self.channel,
            promotion_code='CREDIT100',
            reputation_grant_mode=Customer.ReputationMode.ONE_TIME,
            reputation_grant_amount='100.00',
        )

    def _register(self, username, promotion_code=''):
        client = APIClient()
        payload = {
            'username': username,
            'password': 'SafePass_2026!',
            'email': f'{username}@example.com',
            'contact_name': username,
            'customer_type': 'personal',
        }
        if promotion_code:
            payload['promotion_code'] = promotion_code
        response = client.post('/api/portal/register/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        customer = Customer.objects.get(account__username=username)
        client.force_authenticate(customer.account)
        return client, customer

    def test_direct_register_has_no_reputation(self):
        client, customer = self._register('plain-user')
        self.assertEqual(customer.reputation_mode, Customer.ReputationMode.NONE)
        self.assertEqual(customer.reputation_quota, Decimal('0.00'))

        dashboard = client.get('/api/portal/dashboard/')
        self.assertEqual(dashboard.status_code, 200)
        self.assertFalse(dashboard.data['data']['reputation_visible'])

        product = client.get(f'/api/portal/products/{self.product.pk}/')
        self.assertEqual(product.status_code, 200)
        payload = product.data.get('data', product.data)
        self.assertFalse(payload.get('reputation_visible'))
        self.assertIsNone(payload.get('reputation_price'))

        methods = client.get('/api/portal/bills/payment-methods/', {
            'currency': 'USD',
            'product_id': self.product.pk,
        })
        self.assertEqual(methods.status_code, 200)
        self.assertFalse(any(item.get('reputation') for item in methods.data['data']))

    def test_channel_code_grants_one_time_reputation(self):
        client, customer = self._register('coded-user', 'credit100')
        customer.refresh_from_db()
        self.assertEqual(customer.reputation_mode, Customer.ReputationMode.ONE_TIME)
        self.assertEqual(customer.reputation_quota, Decimal('100.00'))
        self.assertEqual(customer.reputation_balance, Decimal('100.00'))

        dashboard = client.get('/api/portal/dashboard/')
        self.assertTrue(dashboard.data['data']['reputation_visible'])
        self.assertEqual(dashboard.data['data']['reputation_balance'], '100.00')

        product = client.get(f'/api/portal/products/{self.product.pk}/')
        payload = product.data.get('data', product.data)
        self.assertTrue(payload.get('reputation_visible'))
        self.assertEqual(str(payload.get('reputation_price')), '100.00')

        methods = client.get('/api/portal/bills/payment-methods/', {
            'currency': 'USD',
            'product_id': self.product.pk,
        })
        self.assertTrue(any(item.get('reputation') for item in methods.data['data']))
        item = next(x for x in methods.data['data'] if x.get('reputation'))
        self.assertTrue(item.get('available'))
        self.assertEqual(item.get('needed'), '100.00')

    def test_insufficient_balance_still_lists_reputation_method(self):
        client, customer = self._register('low-balance', 'credit100')
        customer.reputation_balance = Decimal('10.00')
        customer.save(update_fields=['reputation_balance', 'updated_at'])
        methods = client.get('/api/portal/bills/payment-methods/', {
            'currency': 'USD',
            'product_id': self.product.pk,
        })
        item = next(x for x in methods.data['data'] if x.get('reputation'))
        self.assertFalse(item.get('available'))
        self.assertIn('余额不足', item.get('disabled_reason') or '')

    def test_monthly_reputation_resets_in_new_period(self):
        customer = Customer.objects.create(
            name='按月客户',
            contact_name='按月联系人',
            status='active',
        )
        apply_reputation_settings(
            customer, mode=Customer.ReputationMode.MONTHLY, quota='80.00', balance='80.00',
        )
        customer.reputation_balance = Decimal('15.00')
        customer.reputation_period = '2020-01'
        customer.save(update_fields=['reputation_balance', 'reputation_period', 'updated_at'])
        refresh_reputation(customer)
        customer.refresh_from_db()
        self.assertEqual(customer.reputation_balance, Decimal('80.00'))
        self.assertNotEqual(customer.reputation_period, '2020-01')

    def test_product_must_allow_reputation_pay(self):
        client, customer = self._register('blocked-user', 'credit100')
        self.product.allow_reputation_pay = False
        self.product.save(update_fields=['allow_reputation_pay', 'updated_at'])

        product = client.get(f'/api/portal/products/{self.product.pk}/')
        payload = product.data.get('data', product.data)
        self.assertFalse(payload.get('reputation_visible'))
        self.assertIsNone(payload.get('reputation_price'))

        response = client.post('/api/portal/orders/direct-checkout/', {
            'product_id': self.product.pk,
            'quantity': 1,
            'payment_method': 'reputation',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        customer.refresh_from_db()
        self.assertEqual(customer.reputation_balance, Decimal('100.00'))

    @patch('service_manager.payment_fulfillment.fulfill_paid_billing_safely')
    def test_reputation_direct_checkout_consumes_balance(self, fulfill):
        client, customer = self._register('payer-user', 'credit100')

        response = client.post('/api/portal/orders/direct-checkout/', {
            'product_id': self.product.pk,
            'quantity': 1,
            'payment_method': 'reputation',
        }, format='json')
        self.assertIn(response.status_code, {200, 201}, response.data)
        self.assertTrue(response.data['data'].get('provision_queued'))
        customer.refresh_from_db()
        self.assertEqual(customer.reputation_balance, Decimal('0.00'))
        billing = customer.billings.get()
        self.assertEqual(billing.status, 'paid')
        self.assertEqual(billing.payment_method, 'reputation')


class ManagerReputationWriteTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('rep-admin', 'rep-admin@example.com', 'password')
        self.customer = Customer.objects.create(
            name='后台客户',
            contact_name='后台联系人',
            status='active',
        )
        self.user = User.objects.create_user('rep-account', 'rep-account@example.com', 'SafePass_2026!')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.customer.account = self.user
        self.customer.save(update_fields=['account', 'updated_at'])
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_admin_can_set_customer_one_time_reputation(self):
        response = self.client.patch(f'/api/customer/customers/{self.customer.pk}/', {
            'reputation_mode': 'one_time',
            'reputation_quota': '150.00',
            'reputation_balance': '150.00',
        }, format='json')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.reputation_mode, Customer.ReputationMode.ONE_TIME)
        self.assertEqual(self.customer.reputation_quota, Decimal('150.00'))
        self.assertEqual(self.customer.reputation_balance, Decimal('150.00'))

    def test_admin_enabling_quota_fills_zero_balance(self):
        response = self.client.patch(f'/api/customer/customers/{self.customer.pk}/', {
            'reputation_mode': 'one_time',
            'reputation_quota': '500.00',
            'reputation_balance': '0.00',
        }, format='json')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.reputation_mode, Customer.ReputationMode.ONE_TIME)
        self.assertEqual(self.customer.reputation_quota, Decimal('500.00'))
        self.assertEqual(self.customer.reputation_balance, Decimal('500.00'))

    def test_admin_can_set_account_monthly_reputation(self):
        response = self.client.patch(f'/api/accounts/users/{self.user.pk}/', {
            'reputation_mode': 'monthly',
            'reputation_quota': '60.00',
            'reputation_balance': '60.00',
        }, format='json')
        self.assertEqual(response.status_code, 200, getattr(response, 'data', response.content))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.reputation_mode, Customer.ReputationMode.MONTHLY)
        self.assertEqual(self.customer.reputation_quota, Decimal('60.00'))
        self.assertEqual(self.customer.reputation_balance, Decimal('60.00'))
