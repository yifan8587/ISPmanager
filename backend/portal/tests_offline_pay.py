"""S-H7：门户禁止现金/转账自助入账。"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer, CustomerBilling


class PortalOfflinePayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('pay-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='支付客户', contact_name='联系人', account=self.user, status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.billing = CustomerBilling.objects.create(
            customer=self.customer,
            bill_month='2026-08',
            amount=Decimal('88.00'),
            due_date=date(2026, 8, 31),
            status=CustomerBilling.BillingStatus.UNPAID,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_cash_confirm_rejected(self):
        resp = self.client.post(
            f'/api/portal/bills/{self.billing.pk}/pay/',
            {'payment_method': 'cash', 'step': 'confirm', 'amount': '88.00'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.billing.refresh_from_db()
        self.assertEqual(self.billing.status, CustomerBilling.BillingStatus.UNPAID)
        self.assertEqual(self.billing.paid_amount, Decimal('0.00'))

    def test_payment_methods_exclude_offline(self):
        resp = self.client.get('/api/portal/bills/payment-methods/')
        self.assertEqual(resp.status_code, 200, resp.data)
        values = {item['value'] for item in resp.data['data']}
        self.assertNotIn('cash', values)
        self.assertNotIn('bank_transfer', values)
