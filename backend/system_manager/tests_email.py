"""S5：邮件通知开关、客户催缴投递、测试接口。"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from customer_manager.models import Customer
from unittest.mock import patch

from system_manager.email_services import dispatch_email, mail_connection, send_test_email, smtp_config
from system_manager.im_services import notify
from system_manager.models import SystemSetting
from system_manager.serializers import SystemSettingSerializer, SystemSettingWriteSerializer
from system_manager.services import set_setting


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            'mail-admin', 'ops@example.test', 'password',
        )
        self.customer = Customer.objects.create(
            name='邮件客户',
            contact_name='收件人',
            contact_email='user@example.test',
            status='active',
        )
        set_setting('notify_email_enabled', False)
        set_setting('notify_email_recipients', 'ops@example.test')
        set_setting('notify_email_from', 'noreply@example.test')
        mail.outbox.clear()

    def test_disabled_switch_sends_nothing(self):
        result = dispatch_email(
            'service_expiry',
            context={
                'action': 'remind',
                'customer_no': self.customer.customer_no,
                'customer_email': self.customer.contact_email,
                'target_name': 'INS1',
            },
        )
        self.assertTrue(result['disabled'])
        self.assertEqual(len(mail.outbox), 0)

    def test_expiry_emails_customer_and_ops(self):
        set_setting('notify_email_enabled', True)
        dispatch_email(
            'service_expiry',
            context={
                'action': 'remind',
                'action_label': '到期前 3 天催缴',
                'customer_no': self.customer.customer_no,
                'customer_email': self.customer.contact_email,
                'target_name': 'INS1',
                'detail': '请尽快续费',
            },
        )
        recipients = {addr for message in mail.outbox for addr in message.to}
        self.assertIn('user@example.test', recipients)
        self.assertIn('ops@example.test', recipients)
        self.assertGreaterEqual(len(mail.outbox), 1)

    def test_ticket_assign_does_not_email_customer(self):
        set_setting('notify_email_enabled', True)
        dispatch_email(
            'service_ticket',
            context={
                'action': 'assign',
                'customer_email': self.customer.contact_email,
                'target_name': 'ST1',
            },
        )
        customer_hits = [
            message for message in mail.outbox if 'user@example.test' in message.to
        ]
        self.assertEqual(customer_hits, [])
        self.assertTrue(any('ops@example.test' in message.to for message in mail.outbox))

    def test_notify_wrapper_sends_when_enabled(self):
        set_setting('notify_email_enabled', True)
        notify(
            'billing_overdue',
            context={
                'action': 'overdue',
                'customer_email': self.customer.contact_email,
                'customer_no': self.customer.customer_no,
                'bill_no': 'BILL-1',
                'target_name': 'BILL-1',
            },
        )
        self.assertTrue(any('user@example.test' in message.to for message in mail.outbox))

    def test_send_test_email_does_not_require_switch(self):
        set_setting('notify_email_enabled', False)
        result = send_test_email('probe@example.test')
        self.assertTrue(result['ok'])
        self.assertEqual(mail.outbox[0].to, ['probe@example.test'])

    def test_smtp_password_is_masked_and_kept_on_blank_update(self):
        set_setting('smtp_password', 'smtp-secret')
        setting = SystemSetting.objects.get(key='smtp_password')
        data = SystemSettingSerializer(setting).data
        self.assertTrue(data['secret'])
        self.assertTrue(data['configured'])
        self.assertEqual(data['value'], '')
        self.assertNotIn('smtp-secret', str(data))

        serializer = SystemSettingWriteSerializer(setting, data={'value': ''}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        setting.refresh_from_db()
        self.assertEqual(setting.value, 'smtp-secret')

    def test_ui_smtp_settings_drive_connection(self):
        set_setting('smtp_host', 'smtp.example.test')
        set_setting('smtp_port', 587)
        set_setting('smtp_user', 'noreply@example.test')
        set_setting('smtp_password', 'pw-example')
        set_setting('smtp_use_ssl', False)
        set_setting('smtp_use_tls', True)
        cfg = smtp_config()
        self.assertEqual(cfg['host'], 'smtp.example.test')
        self.assertEqual(cfg['port'], 587)
        self.assertTrue(cfg['use_tls'])
        self.assertFalse(cfg['use_ssl'])
        with patch('system_manager.email_services.get_connection') as get_connection:
            mail_connection()
        kwargs = get_connection.call_args.kwargs
        self.assertEqual(kwargs['backend'], 'django.core.mail.backends.smtp.EmailBackend')
        self.assertEqual(kwargs['host'], 'smtp.example.test')
        self.assertEqual(kwargs['port'], 587)
        self.assertEqual(kwargs['username'], 'noreply@example.test')
        self.assertEqual(kwargs['password'], 'pw-example')

    def test_test_email_api_requires_manage(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        response = client.post(
            '/api/system/notification-rules/test-email/',
            {'to': 'probe@example.test'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['ok'])
        self.assertEqual(mail.outbox[0].to, ['probe@example.test'])
