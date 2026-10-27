from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from accounts.models import UserProfile
from customer_manager.models import Customer, CustomerOperationLog
from log_manager.ingest import ingest_existing_logs
from log_manager.models import AuditLog
from log_manager.services import record_audit_log, sanitize_payload
from resource_manager.models import ResourceOperationLog, record_resource_log


class AuditLogServiceTests(APITestCase):
    def test_record_and_idempotent_source_ref(self):
        user = User.objects.create_user('auditor', password='SafePass_2026!')
        first = record_audit_log(
            module='system',
            action_type='update',
            description='修改参数',
            operator=user,
            source='manual',
            source_ref='cfg-1',
        )
        second = record_audit_log(
            module='system',
            action_type='update',
            description='重复写入应忽略',
            operator=user,
            source='manual',
            source_ref='cfg-1',
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AuditLog.objects.filter(source='manual', source_ref='cfg-1').count(), 1)
        self.assertEqual(first.operator_name, 'auditor')

    def test_sanitize_redacts_password(self):
        cleaned = sanitize_payload({
            'username': 'alice',
            'password': 'secret',
            'nested': {'api_key': 'k', 'ok': 1},
        })
        self.assertEqual(cleaned['password'], '***')
        self.assertEqual(cleaned['nested']['api_key'], '***')
        self.assertEqual(cleaned['nested']['ok'], 1)


class AuditLogIngestTests(APITestCase):
    def test_resource_log_signal_creates_audit(self):
        user = User.objects.create_user('ops', password='SafePass_2026!')
        record_resource_log(
            operator=user,
            action_type=ResourceOperationLog.ActionType.CREATE,
            description='新建测试运营商',
            target_model=ResourceOperationLog.TargetModel.CARRIER,
            target_id=9,
            target_repr='测试运营商',
        )
        log = AuditLog.objects.get(source='resource_operation')
        self.assertEqual(log.module, 'resource')
        self.assertEqual(log.action_type, 'create')
        self.assertEqual(log.description, '新建测试运营商')
        self.assertEqual(log.operator, user)

    def test_customer_billing_maps_to_billing_module(self):
        user = User.objects.create_user('bill', password='SafePass_2026!')
        customer = Customer.objects.create(
            name='日志客户', contact_name='甲', status='active',
        )
        CustomerOperationLog.objects.create(
            customer=customer,
            operator=user,
            action_type=CustomerOperationLog.ActionType.PAYMENT,
            target_model=CustomerOperationLog.TargetModel.BILLING,
            target_id=1,
            description='登记收款 100',
        )
        log = AuditLog.objects.get(source='customer_operation')
        self.assertEqual(log.module, 'billing')
        self.assertEqual(log.action_type, 'payment')

    def test_backfill_skips_existing(self):
        source = ResourceOperationLog.objects.create(
            action_type='other',
            description='历史记录',
        )
        AuditLog.objects.filter(source='resource_operation', source_ref=str(source.pk)).delete()
        first = ingest_existing_logs()
        second = ingest_existing_logs()
        self.assertGreaterEqual(first.get('ResourceOperationLog', 0), 1)
        self.assertEqual(second.get('ResourceOperationLog', 0), 0)


class AuditLogAPITests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser('log-admin', 'log@example.test', 'SafePass_2026!')
        self.client.force_authenticate(self.admin)

    def test_login_success_and_failure_are_logged(self):
        User.objects.create_user('login-user', password='SafePass_2026!')
        anon = APIClient()
        ok = anon.post('/api/auth/token/', {
            'username': 'login-user', 'password': 'SafePass_2026!',
        }, format='json')
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertTrue(
            AuditLog.objects.filter(action_type='login', operator_name='login-user').exists()
        )

        bad = anon.post('/api/auth/token/', {
            'username': 'login-user', 'password': 'wrong-password',
        }, format='json')
        self.assertEqual(bad.status_code, 401)
        failed = AuditLog.objects.get(action_type='login_failed')
        self.assertIn('login-user', failed.description)
        self.assertNotIn('wrong-password', str(failed.detail))

    def test_middleware_logs_account_create_without_password(self):
        resp = self.client.post('/api/accounts/users/', {
            'username': 'new-staff',
            'password': 'SafePass_2026!',
            'role': UserProfile.Role.OPERATOR,
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        log = AuditLog.objects.get(source='middleware', request_path='/api/accounts/users/')
        self.assertEqual(log.module, 'accounts')
        self.assertEqual(log.action_type, 'create')
        self.assertEqual(log.operator, self.admin)
        self.assertEqual(log.detail.get('request', {}).get('password'), '***')
        self.assertEqual(log.detail.get('request', {}).get('username'), 'new-staff')

    def test_resource_write_is_not_double_logged(self):
        resp = self.client.post('/api/resource/carriers/', {
            'name': '审计运营商',
            'code': 'AUDIT-CARRIER',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        resource_logs = AuditLog.objects.filter(module='resource', description__contains='审计运营商')
        self.assertEqual(resource_logs.count(), 1)
        self.assertEqual(resource_logs.get().source, 'resource_operation')
        self.assertFalse(
            AuditLog.objects.filter(source='middleware', request_path__contains='/carriers/').exists()
        )

    def test_get_list_is_not_logged_and_supports_module_filter(self):
        record_audit_log(module='resource', action_type='create', description='A')
        record_audit_log(module='system', action_type='update', description='B')
        listed = self.client.get('/api/log/audit-logs/', {'module': 'resource'})
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data['count'], 1)
        self.assertEqual(listed.data['results'][0]['module'], 'resource')
        self.assertFalse(AuditLog.objects.filter(request_path='/api/log/audit-logs/').exists())

        meta = self.client.get('/api/log/audit-logs/meta/')
        self.assertEqual(meta.status_code, 200)
        modules = {item['value'] for item in meta.data['modules']}
        self.assertIn('resource', modules)
        self.assertIn('system', modules)

    def test_customer_account_cannot_view_audit_logs(self):
        customer_user = User.objects.create_user('portal-user', password='SafePass_2026!')
        Customer.objects.create(
            name='门户客户', contact_name='乙', account=customer_user, status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=customer_user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])

        portal = APIClient()
        portal.force_authenticate(customer_user)
        denied = portal.get('/api/log/audit-logs/')
        self.assertEqual(denied.status_code, 403)

    def test_change_password_is_logged(self):
        self.admin.set_password('SafePass_2026!')
        self.admin.save(update_fields=['password'])
        resp = self.client.post('/api/auth/me/change_password/', {
            'old_password': 'SafePass_2026!',
            'new_password': 'SafePass_2027!',
            'confirm_password': 'SafePass_2027!',
        }, format='json')
        self.assertEqual(resp.status_code, 200, resp.data)
        log = AuditLog.objects.get(action_type='change_password')
        self.assertEqual(log.module, 'accounts')
        self.assertEqual(log.operator, self.admin)
        self.assertNotIn('SafePass', str(log.detail))
