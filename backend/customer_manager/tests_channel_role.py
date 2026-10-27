"""渠道系统账号：绑定渠道客户、名下客户/下单隔离、佣金结算只读。"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.channel_services import configure_channel_profile
from customer_manager.models import Customer, CustomerReferral
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.models import ServiceOrder


class ChannelRoleAccountTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('ch-admin', 'ch-admin@example.test', 'password')
        self.channel = Customer.objects.create(
            name='华东渠道',
            customer_type='channel',
            status='active',
            contact_name='渠道负责人',
            contact_phone='13800000001',
        )
        self.profile = configure_channel_profile(
            self.channel,
            promotion_code='EAST2026',
            promotion_discount_rate='8.00',
            commission_rate='12.00',
        )
        self.other_channel = Customer.objects.create(
            name='其他渠道',
            customer_type='channel',
            status='active',
            contact_name='他人',
            contact_phone='13800000002',
        )
        configure_channel_profile(
            self.other_channel,
            promotion_code='OTHER2026',
            promotion_discount_rate='5.00',
            commission_rate='10.00',
        )
        self.foreign = Customer.objects.create(
            name='无关客户',
            customer_type='enterprise',
            status='active',
            contact_name='外人',
            contact_phone='13800000003',
        )
        region = Region.objects.create(code='ch', name='渠道区')
        country = Country.objects.create(region=region, name='渠道国')
        city = City.objects.create(country=country, name='渠道市')
        self.pop = PopPoint.objects.create(name='渠道POP', city=city)
        self.product = Product.objects.create(
            name='渠道可售产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price=Decimal('99.00'),
        )

        self.channel_user = User.objects.create_user('channel-op', 'channel-op@example.test', 'password')
        UserProfile.objects.filter(user=self.channel_user).update(
            account_type=UserProfile.AccountType.SYSTEM,
            role=UserProfile.Role.CHANNEL,
            channel=self.channel,
        )
        self.channel_user = User.objects.select_related('profile__channel').get(pk=self.channel_user.pk)
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user)

    def test_user_manage_channel_role_requires_channel_id(self):
        self._auth(self.admin)
        res = self.client.post('/api/accounts/users/', {
            'username': 'channel-unbound',
            'password': 'Channel@12345',
            'role': 'channel',
            'account_type': 'system',
        }, format='json')
        self.assertEqual(res.status_code, 400)
        detail = res.data.get('detail', res.data)
        self.assertIn('channel_id', detail)

    def test_user_manage_creates_bound_channel_account(self):
        self._auth(self.admin)
        res = self.client.post('/api/accounts/users/', {
            'username': 'channel-bound',
            'password': 'Channel@12345',
            'email': '',
            'first_name': '',
            'last_name': '',
            'phone': '',
            'department': '',
            'position': '',
            'remark': '',
            'role': 'channel',
            'account_type': 'system',
            'channel_id': self.channel.pk,
            'customer_id': None,
            'is_staff': False,
            'is_superuser': False,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data.get('data', res.data)
        self.assertEqual(data.get('channel_id'), self.channel.pk)
        self.assertEqual(data.get('role'), UserProfile.Role.CHANNEL)
        user = User.objects.select_related('profile').get(username='channel-bound')
        self.assertEqual(user.profile.channel_id, self.channel.pk)
        self.assertFalse(user.is_staff)

        listed = self.client.get('/api/accounts/users/', {'channel_id': self.channel.pk})
        self.assertEqual(listed.status_code, 200)
        names = {item['username'] for item in listed.data['data']['results']}
        self.assertIn('channel-bound', names)
        self.assertIn('channel-op', names)

    def test_channel_user_only_sees_owned_customers(self):
        self._auth(self.channel_user)
        res = self.client.get('/api/customer/customers/')
        self.assertEqual(res.status_code, 200)
        names = {item['name'] for item in res.data.get('results', [])}
        self.assertIn('华东渠道', names)
        self.assertNotIn('无关客户', names)
        self.assertNotIn('其他渠道', names)

        forbidden = self.client.get(f'/api/customer/customers/{self.foreign.pk}/')
        self.assertEqual(forbidden.status_code, 404)

    def test_channel_user_creates_downline_customer_and_portal_account(self):
        self._auth(self.channel_user)
        res = self.client.post('/api/customer/customers/', {
            'name': '渠道下线客户',
            'customer_type': 'enterprise',
            'contact_name': '张三',
            'contact_phone': '13900001111',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        customer_id = res.data['id']
        referral = CustomerReferral.objects.get(customer_id=customer_id)
        self.assertEqual(referral.channel_id, self.channel.pk)
        self.assertEqual(referral.promotion_code, 'EAST2026')

        reject = self.client.post('/api/customer/customers/', {
            'name': '伪渠道',
            'customer_type': 'channel',
            'contact_name': '李四',
            'contact_phone': '13900002222',
        }, format='json')
        self.assertEqual(reject.status_code, 400)

        portal = self.client.post(f'/api/customer/customers/{customer_id}/portal-account/', {
            'username': 'downline-portal',
            'password': 'Channel@12345',
            'email': 'downline@example.test',
            'first_name': '张三',
        }, format='json')
        self.assertEqual(portal.status_code, 201, portal.data)
        downline = Customer.objects.get(pk=customer_id)
        self.assertEqual(downline.account.username, 'downline-portal')
        self.assertEqual(downline.account.profile.account_type, UserProfile.AccountType.CUSTOMER)

        foreign_portal = self.client.post(
            f'/api/customer/customers/{self.foreign.pk}/portal-account/',
            {'username': 'hack', 'password': 'Channel@12345'},
            format='json',
        )
        self.assertIn(foreign_portal.status_code, (403, 404))

    def test_channel_user_cannot_delete_own_channel_or_adjust_balance(self):
        self._auth(self.channel_user)
        destroy = self.client.delete(f'/api/customer/customers/{self.channel.pk}/')
        self.assertEqual(destroy.status_code, 403)
        adjust = self.client.post(
            f'/api/customer/customers/{self.channel.pk}/adjust-balance/',
            {'delta': '10.00', 'remark': '越权调账'},
            format='json',
        )
        self.assertEqual(adjust.status_code, 403)

    def test_channel_user_can_order_for_self_and_downline_only(self):
        downline = Customer.objects.create(
            name='已绑定下线',
            customer_type='personal',
            status='active',
            contact_name='下线',
            contact_phone='13900003333',
        )
        from customer_manager.channel_services import bind_customer_referral
        bind_customer_referral(downline, 'EAST2026', bound_by=self.admin)

        self._auth(self.channel_user)
        own = self.client.post('/api/service/orders/', {
            'order_type': 'new',
            'customer': self.channel.pk,
            'product': self.product.pk,
            'quoted_price': '99.00',
        }, format='json')
        self.assertEqual(own.status_code, 201, own.data)
        self.assertEqual(own.data['customer'], self.channel.pk)

        child = self.client.post('/api/service/orders/', {
            'order_type': 'new',
            'customer': downline.pk,
            'product': self.product.pk,
            'quoted_price': '99.00',
        }, format='json')
        self.assertEqual(child.status_code, 201, child.data)

        foreign = self.client.post('/api/service/orders/', {
            'order_type': 'new',
            'customer': self.foreign.pk,
            'product': self.product.pk,
            'quoted_price': '99.00',
        }, format='json')
        self.assertEqual(foreign.status_code, 400)

        listed = self.client.get('/api/service/orders/')
        self.assertEqual(listed.status_code, 200)
        customer_ids = {item['customer'] for item in listed.data.get('results', listed.data)}
        self.assertIn(self.channel.pk, customer_ids)
        self.assertIn(downline.pk, customer_ids)
        self.assertNotIn(self.foreign.pk, customer_ids)

        ServiceOrder.objects.create(
            customer=self.foreign, product=self.product, preferred_pop=self.pop,
            created_by=self.admin,
        )
        listed2 = self.client.get('/api/service/orders/')
        ids = {item['customer'] for item in listed2.data.get('results', listed2.data)}
        self.assertNotIn(self.foreign.pk, ids)

    def test_channel_user_has_no_product_or_resource_access(self):
        self._auth(self.channel_user)
        self.assertEqual(self.client.get('/api/product/products/').status_code, 403)
        self.assertEqual(self.client.get('/api/resource/pop-points/').status_code, 403)
        catalog = self.client.get('/api/service/orders/catalog/')
        self.assertEqual(catalog.status_code, 200, catalog.data)
        names = {item['name'] for item in catalog.data.get('products', [])}
        self.assertIn('渠道可售产品', names)

    def test_channel_user_can_view_commission_but_cannot_write_finance(self):
        self._auth(self.channel_user)
        self.assertEqual(self.client.get('/api/customer/channel-commissions/').status_code, 200)
        self.assertEqual(self.client.get('/api/customer/channel-settlements/').status_code, 200)
        self.assertEqual(self.client.get('/api/customer/channel-finance-ledgers/').status_code, 200)
        self.assertEqual(self.client.get('/api/customer/channel-finance-ledgers/summary/').status_code, 200)
        self.assertEqual(self.client.get('/api/customer/channel-payout-profiles/').status_code, 200)

        self.assertEqual(
            self.client.post('/api/customer/channel-commissions/create-settlement/', {
                'commission_ids': [1],
            }, format='json').status_code,
            403,
        )
        self.assertEqual(
            self.client.post('/api/customer/channel-finance-ledgers/adjustment/', {
                'channel': self.channel.pk,
                'currency': 'CNY',
                'delta': '1.00',
                'reason': '越权',
                'reference': 'BAD',
                'evidence': 'BAD',
                'idempotency_key': 'channel-bad',
            }, format='json').status_code,
            403,
        )
        self.assertEqual(
            self.client.post('/api/customer/channel-payout-profiles/', {
                'channel': self.channel.pk,
                'entity_name': '测试',
                'account_type': 'bank',
                'account': '622200001111',
                'recipient_name': '测试',
            }, format='json').status_code,
            403,
        )
