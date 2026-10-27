"""D2：客户技术工单与 SLA 字段。"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import ContactRecord, Customer
from service_manager.models import ServiceInquiry, SupportTicket, SupportTicketMessage
from service_manager.support_ticket import (
    add_staff_message,
    create_support_ticket,
    get_sla_hours,
    process_support_sla,
)


class SupportTicketTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('st-admin', 'st@example.test', 'password')
        self.customer = Customer.objects.create(
            name='工单客户甲',
            contact_name='甲联系人',
            status='active',
        )
        self.other = Customer.objects.create(
            name='工单客户乙',
            contact_name='乙联系人',
            status='active',
        )
        self.portal_user = User.objects.create_user('st-portal', password='SafePass_2026!')
        self.customer.account = self.portal_user
        self.customer.save(update_fields=['account'])
        profile, _ = UserProfile.objects.get_or_create(user=self.portal_user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])

        self.other_user = User.objects.create_user('st-other', password='SafePass_2026!')
        self.other.account = self.other_user
        self.other.save(update_fields=['account'])
        other_profile, _ = UserProfile.objects.get_or_create(user=self.other_user)
        other_profile.account_type = UserProfile.AccountType.CUSTOMER
        other_profile.role = UserProfile.Role.CUSTOMER_USER
        other_profile.save(update_fields=['account_type', 'role'])

        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.portal = APIClient()
        self.portal.force_authenticate(self.portal_user)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_user)

    def test_create_computes_sla_and_writes_contact(self):
        ticket = create_support_ticket(
            customer=self.customer,
            subject='线路中断',
            description='POP 不通',
            priority=SupportTicket.Priority.P1,
            source=SupportTicket.Source.ADMIN,
            created_by=self.admin,
        )
        response_hours, resolve_hours = get_sla_hours('p1')
        self.assertEqual(response_hours, 1)
        self.assertEqual(resolve_hours, 4)
        delta_response = ticket.first_response_due - ticket.created_at
        delta_resolve = ticket.resolve_due - ticket.created_at
        self.assertAlmostEqual(delta_response.total_seconds(), 3600, delta=5)
        self.assertAlmostEqual(delta_resolve.total_seconds(), 14400, delta=5)
        self.assertTrue(
            ContactRecord.objects.filter(
                customer=self.customer,
                summary__contains=ticket.ticket_no,
            ).exists()
        )
        self.assertEqual(ticket.messages.count(), 1)

    def test_first_public_reply_sets_first_responded_at(self):
        ticket = create_support_ticket(
            customer=self.customer,
            subject='丢包',
            description='间歇丢包',
            created_by=self.admin,
            source=SupportTicket.Source.ADMIN,
        )
        add_staff_message(ticket, '内部先看日志', author=self.admin, is_internal=True)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.first_responded_at)

        add_staff_message(ticket, '已在处理', author=self.admin, is_internal=False)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.first_responded_at)
        self.assertEqual(ticket.status, SupportTicket.Status.IN_PROGRESS)

    def test_portal_isolation_and_hides_internal_notes(self):
        created = self.portal.post('/api/portal/tickets/', {
            'subject': '无法登录',
            'description': '控制台 403',
            'category': 'access',
            'priority': 'p2',
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        ticket_id = created.data['id']

        add_staff_message(
            SupportTicket.objects.get(pk=ticket_id),
            '内部备注不可见',
            author=self.admin,
            is_internal=True,
        )
        add_staff_message(
            SupportTicket.objects.get(pk=ticket_id),
            '请重试登录',
            author=self.admin,
            is_internal=False,
        )

        mine = self.portal.get(f'/api/portal/tickets/{ticket_id}/')
        self.assertEqual(mine.status_code, 200)
        contents = [m['content'] for m in mine.data['messages']]
        self.assertIn('请重试登录', contents)
        self.assertNotIn('内部备注不可见', contents)

        hidden = self.other_client.get(f'/api/portal/tickets/{ticket_id}/')
        self.assertEqual(hidden.status_code, 404)

        listed = self.other_client.get('/api/portal/tickets/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data.get('count', len(listed.data.get('results') or [])), 0)

    def test_inquiry_convert_to_ticket(self):
        inquiry = ServiceInquiry.objects.create(
            inquiry_type=ServiceInquiry.InquiryType.HELP,
            contact_name='甲联系人',
            requirements='需要排查防火墙',
            customer=self.customer,
        )
        resp = self.admin_client.post(
            f'/api/service/inquiries/{inquiry.pk}/convert-to-ticket/',
            {'category': 'technical', 'priority': 'p3'},
            format='json',
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        ticket = SupportTicket.objects.get(ticket_no=resp.data['ticket_no'])
        self.assertEqual(ticket.related_inquiry_id, inquiry.pk)
        self.assertEqual(ticket.source, SupportTicket.Source.INQUIRY)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.related_ticket.ticket_no, ticket.ticket_no)

        again = self.admin_client.post(
            f'/api/service/inquiries/{inquiry.pk}/convert-to-ticket/',
            {},
            format='json',
        )
        self.assertEqual(again.status_code, 400)

    def test_closed_ticket_rejects_customer_reply(self):
        ticket = create_support_ticket(
            customer=self.customer,
            subject='关单测试',
            description='描述',
            source=SupportTicket.Source.PORTAL,
            created_by=self.portal_user,
        )
        self.admin_client.post(f'/api/service/tickets/{ticket.pk}/close/', {}, format='json')
        resp = self.portal.post(
            f'/api/portal/tickets/{ticket.pk}/reply/',
            {'content': '还没好'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_process_support_sla_dry_run_then_apply(self):
        ticket = create_support_ticket(
            customer=self.customer,
            subject='超时扫描',
            description='描述',
            priority=SupportTicket.Priority.P3,
            created_by=self.admin,
            source=SupportTicket.Source.ADMIN,
        )
        past = timezone.now() - timedelta(hours=2)
        ticket.first_response_due = past
        ticket.save(update_fields=['first_response_due'])

        stats = process_support_sla(dry_run=True)
        ticket.refresh_from_db()
        self.assertFalse(ticket.sla_response_breached)
        self.assertGreaterEqual(stats['response_breached'], 1)

        out = StringIO()
        call_command('process_support_sla', stdout=out)
        ticket.refresh_from_db()
        self.assertTrue(ticket.sla_response_breached)
        self.assertIn('响应超时', out.getvalue())

    def test_admin_create_and_list(self):
        resp = self.admin_client.post('/api/service/tickets/', {
            'customer': self.customer.pk,
            'subject': '后台建单',
            'description': '后台描述',
            'priority': 'p4',
        }, format='json')
        self.assertEqual(resp.status_code, 201, resp.data)
        listed = self.admin_client.get('/api/service/tickets/', {'search': '后台建单'})
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.data['count'], 1)
