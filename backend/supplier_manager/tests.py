"""供应商账号隔离与账单结算。"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer, CustomerBilling
from product_manager.models import Product
from resource_manager.models import City, Country, PopPoint, Region
from service_manager.models import ServiceOrder
from supplier_manager.models import Supplier, SupplierBill, SupplierSettlement
from supplier_manager.settlement import ensure_paid_supplier_bills, finance_summary, request_settlement


class SupplierFeatureTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('sup-admin', 'sup-admin@example.test', 'password')
        self.supplier = Supplier.objects.create(
            name='张三线路', short_name='张三', supplier_type='idc',
            settlement_rate=Decimal('90.00'), settlement_min_amount=Decimal('80.00'),
        )
        other = Supplier.objects.create(name='其他供应商', short_name='其他', supplier_type='idc')
        region = Region.objects.create(code='sup', name='供应区')
        country = Country.objects.create(region=region, name='供应国')
        city = City.objects.create(country=country, name='供应市')
        self.pop = PopPoint.objects.create(name='供应POP', city=city, supplier=self.supplier)
        self.other_pop = PopPoint.objects.create(name='他人POP', city=city, supplier=other)
        self.product = Product.objects.create(
            name='供应商产品', status=Product.Status.ONLINE, pop_point=self.pop,
            monthly_price=Decimal('100.00'), supplier=self.supplier,
        )
        self.other_product = Product.objects.create(
            name='他人产品', status=Product.Status.ONLINE, pop_point=self.other_pop,
            monthly_price=Decimal('50.00'), supplier=other,
        )
        self.customer = Customer.objects.create(name='采购客户', contact_name='联系人', status='active')

        self.supplier_user = User.objects.create_user('vendor', 'vendor@example.test', 'password')
        UserProfile.objects.filter(user=self.supplier_user).update(
            account_type=UserProfile.AccountType.SYSTEM,
            role=UserProfile.Role.SUPPLIER,
            supplier=self.supplier,
        )
        self.supplier_user = User.objects.select_related('profile__supplier').get(pk=self.supplier_user.pk)
        self.assertEqual(self.supplier_user.profile.role, UserProfile.Role.SUPPLIER)
        self.assertEqual(self.supplier_user.profile.supplier_id, self.supplier.pk)

        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user)

    def test_supplier_only_sees_own_products(self):
        self._auth(self.supplier_user)
        res = self.client.get('/api/product/products/')
        self.assertEqual(res.status_code, 200)
        names = {item['name'] for item in res.data.get('results', res.data)}
        self.assertIn('供应商产品', names)
        self.assertNotIn('他人产品', names)

    def test_supplier_product_create_binds_supplier(self):
        self._auth(self.supplier_user)
        res = self.client.post('/api/product/products/', {
            'name': '新上传产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'monthly_price': '12.00',
            'currency': 'USD',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(Product.objects.get(pk=res.data['id']).supplier_id, self.supplier.pk)

    def test_supplier_cannot_see_vps(self):
        self._auth(self.supplier_user)
        res = self.client.get('/api/resource/vps/')
        self.assertEqual(res.status_code, 403)

    def test_supplier_only_sees_own_pops(self):
        self._auth(self.supplier_user)
        res = self.client.get('/api/resource/pop-points/')
        self.assertEqual(res.status_code, 200)
        names = {item['name'] for item in res.data.get('results', res.data)}
        self.assertIn('供应POP', names)
        self.assertNotIn('他人POP', names)

    def test_paid_billing_creates_supplier_bill_and_auto_settlement(self):
        order = ServiceOrder.objects.create(
            customer=self.customer, product=self.product, preferred_pop=self.pop,
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            created_by=self.admin,
        )
        billing = CustomerBilling.objects.create(
            customer=self.customer,
            service_order=order,
            bill_month='2026-09',
            amount=Decimal('100.00'),
            currency='CNY',
            due_date=date(2026, 9, 30),
            status=CustomerBilling.BillingStatus.PAID,
        )
        ensure_paid_supplier_bills(billing)
        bills = list(SupplierBill.objects.filter(customer_billing=billing))
        self.assertEqual(len(bills), 1)
        bill = bills[0]
        self.assertEqual(bill.supplier_id, self.supplier.pk)
        self.assertEqual(bill.source_amount, Decimal('100.00'))
        self.assertEqual(bill.settle_amount, Decimal('90.00'))
        self.assertEqual(bill.settlement_rate, Decimal('90.00'))
        # 满额 80，自动生成结算单
        settlement = SupplierSettlement.objects.get(supplier=self.supplier)
        self.assertEqual(settlement.amount, Decimal('90.00'))
        self.assertEqual(settlement.status, SupplierSettlement.Status.PENDING)
        bill.refresh_from_db()
        self.assertEqual(bill.status, SupplierBill.Status.IN_SETTLEMENT)

        again = ensure_paid_supplier_bills(billing)
        self.assertEqual(again, [])
        self.assertEqual(SupplierBill.objects.filter(supplier=self.supplier).count(), 1)

    def test_self_operated_product_has_no_supplier_bill(self):
        product = Product.objects.create(
            name='自营产品', status=Product.Status.ONLINE, pop_point=self.pop,
            monthly_price=Decimal('20.00'),
        )
        order = ServiceOrder.objects.create(
            customer=self.customer, product=product, preferred_pop=self.pop,
            created_by=self.admin,
        )
        billing = CustomerBilling.objects.create(
            customer=self.customer, service_order=order,
            bill_month='2026-09', amount=Decimal('20.00'),
            currency='CNY', due_date=date(2026, 9, 30),
            status=CustomerBilling.BillingStatus.PAID,
        )
        self.assertEqual(ensure_paid_supplier_bills(billing), [])

    def test_supplier_bill_api_scoped(self):
        order = ServiceOrder.objects.create(
            customer=self.customer, product=self.product, preferred_pop=self.pop,
            created_by=self.admin,
        )
        billing = CustomerBilling.objects.create(
            customer=self.customer, service_order=order,
            bill_month='2026-09', amount=Decimal('10.00'),
            currency='CNY', due_date=date(2026, 9, 30),
            status=CustomerBilling.BillingStatus.PAID,
        )
        self.supplier.settlement_min_amount = Decimal('0')
        self.supplier.save(update_fields=['settlement_min_amount'])
        ensure_paid_supplier_bills(billing)

        self._auth(self.supplier_user)
        res = self.client.get('/api/supplier/bills/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data.get('count', len(res.data.get('results', res.data))), 1)
        summary = self.client.get('/api/supplier/bills/summary/')
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(Decimal(str(summary.data['pending_settle_amount'])), Decimal('9.00'))

    def test_admin_creates_supplier_account(self):
        self._auth(self.admin)
        res = self.client.post(f'/api/supplier/suppliers/{self.supplier.pk}/accounts/', {
            'username': 'vendor2',
            'password': 'Vendor@12345',
            'email': 'vendor2@example.test',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        user = User.objects.get(username='vendor2')
        self.assertEqual(user.profile.role, UserProfile.Role.SUPPLIER)
        self.assertEqual(user.profile.supplier_id, self.supplier.pk)
        self.assertFalse(user.is_staff)

    def test_supplier_cannot_create_account(self):
        self._auth(self.supplier_user)
        res = self.client.post(f'/api/supplier/suppliers/{self.supplier.pk}/accounts/', {
            'username': 'hacker',
            'password': 'Vendor@12345',
        }, format='json')
        self.assertEqual(res.status_code, 403)

    def test_request_settlement_respects_threshold(self):
        self.supplier.settlement_min_amount = Decimal('1000.00')
        self.supplier.save(update_fields=['settlement_min_amount'])
        order = ServiceOrder.objects.create(
            customer=self.customer, product=self.product, preferred_pop=self.pop,
            created_by=self.admin,
        )
        billing = CustomerBilling.objects.create(
            customer=self.customer, service_order=order,
            bill_month='2026-09', amount=Decimal('10.00'),
            currency='CNY', due_date=date(2026, 9, 30),
            status=CustomerBilling.BillingStatus.PAID,
        )
        ensure_paid_supplier_bills(billing)
        summary = finance_summary(self.supplier)
        self.assertFalse(summary['can_settle'])
        with self.assertRaises(Exception):
            request_settlement(self.supplier, enforce_threshold=True)

    def test_user_manage_supplier_role_requires_supplier_id(self):
        self._auth(self.admin)
        res = self.client.post('/api/accounts/users/', {
            'username': 'vendor-unbound',
            'password': 'Vendor@12345',
            'role': 'supplier',
            'account_type': 'system',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_user_manage_create_supplier_allows_blank_profile_fields(self):
        self._auth(self.admin)
        res = self.client.post('/api/accounts/users/', {
            'username': 'vendor-blank',
            'password': 'Vendor@12345',
            'email': '',
            'first_name': '',
            'last_name': '',
            'phone': '',
            'department': '',
            'position': '',
            'remark': '',
            'role': 'supplier',
            'account_type': 'system',
            'supplier_id': self.supplier.pk,
            'customer_id': None,
            'is_staff': False,
            'is_superuser': False,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        user = User.objects.get(username='vendor-blank')
        self.assertEqual(user.profile.role, UserProfile.Role.SUPPLIER)
        self.assertEqual(user.profile.supplier_id, self.supplier.pk)

    def test_user_manage_creates_bound_supplier_account(self):
        self._auth(self.admin)
        res = self.client.post('/api/accounts/users/', {
            'username': 'vendor-bound',
            'password': 'Vendor@12345',
            'role': 'supplier',
            'account_type': 'system',
            'supplier_id': self.supplier.pk,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        data = res.data.get('data', res.data)
        self.assertEqual(data.get('supplier_id'), self.supplier.pk)
        self.assertEqual(data.get('role'), UserProfile.Role.SUPPLIER)
        user = User.objects.select_related('profile').get(username='vendor-bound')
        self.assertEqual(user.profile.supplier_id, self.supplier.pk)
        self.assertFalse(user.is_staff)

        listed = self.client.get('/api/accounts/users/', {'supplier_id': self.supplier.pk})
        self.assertEqual(listed.status_code, 200)
        names = {item['username'] for item in listed.data['data']['results']}
        self.assertIn('vendor-bound', names)
        self.assertIn('vendor', names)

    def test_orphan_product_on_owned_pop_is_visible(self):
        Product.objects.create(
            name='未标供应商产品', status=Product.Status.ONLINE, pop_point=self.pop,
            monthly_price=Decimal('8.00'),
        )
        self._auth(self.supplier_user)
        res = self.client.get('/api/product/products/')
        self.assertEqual(res.status_code, 200)
        names = {item['name'] for item in res.data.get('results', res.data)}
        self.assertIn('未标供应商产品', names)
        self.assertNotIn('他人产品', names)

    def test_supplier_cannot_create_product_on_foreign_pop(self):
        self._auth(self.supplier_user)
        res = self.client.post('/api/product/products/', {
            'name': '越权产品',
            'category': 'vps',
            'pop_point': self.other_pop.pk,
            'monthly_price': '12.00',
            'currency': 'USD',
        }, format='json')
        self.assertEqual(res.status_code, 400)

    def test_unbound_supplier_account_sees_nothing(self):
        unbound = User.objects.create_user('vendor-empty', 'empty@example.test', 'password')
        UserProfile.objects.filter(user=unbound).update(
            account_type=UserProfile.AccountType.SYSTEM,
            role=UserProfile.Role.SUPPLIER,
            supplier=None,
        )
        unbound = User.objects.select_related('profile').get(pk=unbound.pk)
        self._auth(unbound)
        res = self.client.get('/api/product/products/')
        self.assertEqual(res.status_code, 200)
        results = res.data.get('results', res.data)
        self.assertEqual(len(results), 0)
        create = self.client.post('/api/product/products/', {
            'name': '空账号产品',
            'category': 'vps',
            'pop_point': self.pop.pk,
            'monthly_price': '1.00',
            'currency': 'USD',
        }, format='json')
        self.assertIn(create.status_code, (400, 403))

    def test_bind_existing_account_and_scope(self):
        plain = User.objects.create_user('plain-op', 'plain@example.test', 'password')
        self._auth(self.admin)
        res = self.client.post(f'/api/supplier/suppliers/{self.supplier.pk}/accounts/bind/', {
            'username': 'plain-op',
        }, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        plain = User.objects.select_related('profile').get(pk=plain.pk)
        self.assertEqual(plain.profile.role, UserProfile.Role.SUPPLIER)
        self.assertEqual(plain.profile.supplier_id, self.supplier.pk)

        self._auth(plain)
        pops = self.client.get('/api/resource/pop-points/')
        self.assertEqual(pops.status_code, 200)
        names = {item['name'] for item in pops.data.get('results', pops.data)}
        self.assertIn('供应POP', names)
        self.assertNotIn('他人POP', names)

        unbind = self.client.post(
            f'/api/supplier/suppliers/{self.supplier.pk}/accounts/unbind/',
            {'user_id': plain.pk}, format='json',
        )
        self.assertEqual(unbind.status_code, 403)

        self._auth(self.admin)
        unbind = self.client.post(
            f'/api/supplier/suppliers/{self.supplier.pk}/accounts/unbind/',
            {'user_id': plain.pk}, format='json',
        )
        self.assertEqual(unbind.status_code, 200, unbind.data)
        plain.refresh_from_db()
        plain.profile.refresh_from_db()
        self.assertIsNone(plain.profile.supplier_id)
        self.assertEqual(plain.profile.role, UserProfile.Role.OPERATOR)

    def test_pop_create_binds_supplier_and_pop_binding(self):
        from supplier_manager.models import SupplierPopBinding
        self._auth(self.supplier_user)
        res = self.client.post('/api/resource/pop-points/', {
            'name': '供应商新建POP',
            'city': self.pop.city_id,
            'status': 'online',
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        created = PopPoint.objects.get(name='供应商新建POP')
        self.assertEqual(created.supplier_id, self.supplier.pk)
        self.assertTrue(
            SupplierPopBinding.objects.filter(supplier=self.supplier, pop_point=created).exists()
        )
