"""N2：门户隧道下发支持 dry_run 预览，确认后再真下发。"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserProfile
from customer_manager.models import Customer
from resource_manager.models import (
    City,
    Country,
    EdgeAcceleration,
    GREConfig,
    PopPoint,
    Region,
    ResourceOperationLog,
)


class PortalEdgePushPreviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('n2-portal', password='SafePass_2026!')
        self.customer = Customer.objects.create(
            name='N2 门户客户',
            contact_name='预览',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        region = Region.objects.create(code='n2p', name='预览区')
        country = Country.objects.create(region=region, name='预览国')
        city = City.objects.create(country=country, name='预览市')
        self.pop = PopPoint.objects.create(name='预览 POP', city=city)
        self.edge = EdgeAcceleration.objects.create(
            name='预览边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            bandwidth_mbps=100,
        )
        GREConfig.objects.create(
            edge=self.edge,
            local_ip='192.0.2.1',
            remote_ip='198.51.100.1',
            tunnel_ip='10.0.0.1/30',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @patch('resource_manager.deploy_service.push_pop_to_node')
    def test_dry_run_returns_preview_and_does_not_claim_applied(self, push):
        push.return_value = {
            'ok': True,
            'dry_run': True,
            'summary': {
                'edge_tunnels': {
                    'plan': [{'ifname': 'gre1', 'state': '新建', 'protocol': 'gre'}],
                    'would_create': 1,
                },
            },
        }
        response = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {'dry_run': True},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['message'], '已生成下发计划')
        self.assertIn('gre1', response.data.get('preview_text') or '')
        self.assertTrue(push.call_args.kwargs.get('dry_run'))
        log = ResourceOperationLog.objects.get(edge=self.edge)
        self.assertIn('演练', log.description)

    @patch('resource_manager.deploy_service.push_pop_to_node')
    def test_real_push_still_defaults_to_execute(self, push):
        def _push(pop, sections=None, dry_run=False):
            if not dry_run:
                self.edge.node_pushed = True
                self.edge.node_ifname = 'gre-n2'
                self.edge.is_synced = True
                self.edge.save(update_fields=['node_pushed', 'node_ifname', 'is_synced', 'updated_at'])
            return {
                'ok': True,
                'apply_ok': True,
                'dry_run': dry_run,
                'summary': {
                    'edge_tunnels': {'created': 1, 'reconfigured': 0, 'unchanged': 0},
                },
            }

        push.side_effect = _push
        response = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(push.call_args.kwargs.get('dry_run'))
        self.assertIn('生效', response.data['message'])

    @patch('resource_manager.deploy_service.push_pop_to_node')
    def test_failed_push_deduplicates_reconfigure_message(self, push):
        msg = (
            'gre12: 操作 /api/interfacemanage/db/desired-tunnels/x/reconfigure/ 失败 '
            '(HTTP 400) : 新接口下发失败, 已回滚数据库'
        )
        push.return_value = {
            'ok': False,
            'apply_ok': False,
            'dry_run': False,
            'summary': {
                'edge_tunnels': {
                    'reconfigure_errors': [msg],
                    'bandwidth_errors': [],
                },
            },
            'apply_errors': {'edge_tunnels': [msg]},
            'errors': {},
        }
        response = self.client.post(
            f'/api/portal/edge/{self.edge.pk}/push_to_node/',
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data['message'].count(msg), 1)
