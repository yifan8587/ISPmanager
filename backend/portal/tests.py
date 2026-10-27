from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from accounts.models import UserProfile
from customer_manager.models import Customer, PaymentTransaction
from operation_manager.models import (
    EdgeInterfaceQualityRollup,
    EdgeInterfaceQualitySample,
    MonitoringGraphBinding,
    MonitoringHost,
    MonitoringItem,
    ZabbixServer,
)
from operation_manager.zabbix_client import ZabbixError
from product_manager.models import Product
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region
from system_manager.models import IMChannel, IMChannelType, PaymentChannel, SystemSetting
from system_manager.support_public import CACHE_KEY, invalidate_public_support_cache


class PortalRegistrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_invalid_password_returns_specific_reason(self):
        response = self.client.post('/api/portal/register/', {
            'username': 'new-customer',
            'password': '12345678',
            'email': 'new@example.com',
            'contact_name': '新客户',
            'customer_type': 'personal',
        }, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn(response.data['message'], {'这个密码太常见了。', '密码只包含数字。'})
        self.assertIn('password', response.data['detail'])
        self.assertFalse(User.objects.filter(username='new-customer').exists())

    def test_valid_registration_creates_customer_and_tokens(self):
        response = self.client.post('/api/portal/register/', {
            'username': 'new-customer',
            'password': 'SafePass_2026!',
            'email': 'new@example.com',
            'contact_name': '新客户',
            'customer_type': 'personal',
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['message'], '注册成功')
        self.assertTrue(response.data['data']['access'])
        self.assertTrue(response.data['data']['refresh'])
        customer = Customer.objects.get(account__username='new-customer')
        self.assertEqual(customer.contact_email, 'new@example.com')
        self.assertEqual(customer.status, 'active')
        profile = customer.account.profile
        self.assertEqual(profile.account_type, UserProfile.AccountType.CUSTOMER)
        self.assertEqual(profile.role, UserProfile.Role.CUSTOMER_USER)
        perms = profile.get_permissions()
        self.assertIn('service.manage', perms)
        self.assertTrue(any(p.startswith('service.change.') for p in perms))


class PortalDirectCheckoutTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='direct-payment-customer',
            password='SafePass_2026!',
        )
        self.customer = Customer.objects.create(
            name='直付测试客户',
            contact_name='直付联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.save(update_fields=['account_type'])
        region = Region.objects.create(code='portal-pay-apac', name='门户支付亚太')
        country = Country.objects.create(region=region, name='门户支付国')
        city = City.objects.create(country=country, name='门户支付市')
        self.pop = PopPoint.objects.create(
            code='PORTALPAY',
            name='门户支付 POP',
            city=city,
            config_mode=PopPoint.ConfigMode.AUTO,
        )
        self.product = Product.objects.create(
            name='门户直付 VPS',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price='88.00',
            setup_price='12.00',
            currency='CNY',
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=5,
            ip_count=1,
        )
        self.channel = PaymentChannel.objects.create(
            provider='wechat',
            name='微信测试通道',
            enabled=True,
        )
        self.client.force_authenticate(self.user)

    @patch('portal.serializers.billing_payment_service.available_channels')
    @patch('portal.views.billing_payment_service.issue_gateway_checkout')
    @patch('portal.views.billing_payment_service.create_payment_transaction')
    def test_direct_checkout_creates_approved_order_billing_and_gateway_transaction(
        self,
        create_payment,
        issue_checkout,
        available_channels,
    ):
        available_channels.return_value = [self.channel]

        def create_transaction(*, billing, channel, operator, return_url):
            return PaymentTransaction.objects.create(
                billing=billing,
                channel=channel,
                provider=channel.provider,
                amount=billing.amount,
                currency=billing.currency,
                billing_amount=billing.amount,
                billing_currency=billing.currency,
                initiated_by=operator,
                return_url=return_url,
            )

        def issue_transaction(*, payment, callback_url, gateway_return_url):
            payment.status = PaymentTransaction.Status.PENDING
            payment.save(update_fields=['status', 'updated_at'])
            return payment, {
                'mode': 'qr',
                'qr_code_text': 'weixin://test-payment',
                'transaction_no': payment.transaction_no,
            }

        create_payment.side_effect = create_transaction
        issue_checkout.side_effect = issue_transaction

        response = self.client.post('/api/portal/orders/direct-checkout/', {
            'product_id': self.product.pk,
            'channel_id': self.channel.pk,
            'quantity': 1,
        }, format='json')

        self.assertEqual(response.status_code, 201, response.data)
        order = self.customer.service_orders.get()
        self.assertEqual(order.status, 'approved')
        self.assertEqual(order.auto_provision_status, 'payment_pending')
        self.assertEqual(order.quoted_price, Decimal('100.00'))
        self.assertEqual(order.payment_billing.amount, Decimal('100.00'))
        self.assertEqual(order.request_snapshot['quote']['monthly_total'], '88.00')
        self.assertEqual(order.request_snapshot['quote']['setup_total'], '12.00')
        self.assertEqual(order.payment_billing.items.get().detail['setup_total'], '12.00')
        self.assertEqual(response.data['data']['transaction']['status'], 'pending')
        self.assertEqual(response.data['data']['checkout']['mode'], 'qr')


class PortalRealMonitoringTests(TestCase):
    """门户只能读取自身资源的真实监控绑定与中心遥测样本。"""

    def setUp(self):
        region = Region.objects.create(code='portal-monitor', name='门户监控大区')
        country = Country.objects.create(region=region, name='门户监控国')
        city = City.objects.create(country=country, name='门户监控市')
        self.pop = PopPoint.objects.create(name='门户监控 POP', city=city)
        self.user = User.objects.create_user(
            username='portal-monitor-customer',
            password='SafePass_2026!',
        )
        self.customer = Customer.objects.create(
            name='门户监控客户',
            contact_name='门户监控联系人',
            account=self.user,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.save(update_fields=['account_type'])
        self.other_customer = Customer.objects.create(
            name='其他监控客户',
            contact_name='其他联系人',
        )
        self.edge = EdgeAcceleration.objects.create(
            name='客户边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre-portal-a',
            bandwidth_mbps=100,
        )
        self.other_edge = EdgeAcceleration.objects.create(
            name='其他客户边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.other_customer,
            node_ifname='gre-portal-b',
            bandwidth_mbps=100,
        )
        self.server = ZabbixServer.objects.create(
            name='门户测试 Zabbix',
            api_url='https://zabbix.example.test/api_jsonrpc.php',
            api_token='portal-test-token',
        )
        self.host = MonitoringHost.objects.create(
            zabbix_server=self.server,
            host_kind=MonitoringHost.HostKind.GENERIC,
            service_pop_point=self.pop,
            hostname='portal-monitor-host',
            is_enabled=True,
        )
        self.item = MonitoringItem.objects.create(
            monitoring_host=self.host,
            zabbix_itemid='1001',
            item_key='net.if.in[gre-portal-a]',
            name='gre-portal-a inbound',
            traffic_direction='in',
            interface_name='gre-portal-a',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _binding(self, *, slot=MonitoringGraphBinding.PanelSlot.TRAFFIC):
        return MonitoringGraphBinding.objects.create(
            monitoring_item=self.item,
            monitoring_host=self.host,
            target_type=MonitoringHost.TargetType.EDGE,
            edge=self.edge,
            panel_slot=slot,
            is_visible=True,
        )

    @staticmethod
    def _traffic_result():
        return {
            'timeseries': [
                {
                    'time': '2026-08-19T00:00:00+00:00',
                    'in_bps': 1_000_000,
                    'out_bps': 2_000_000,
                },
            ],
            'billing': {
                'total_in_bytes': 1024 ** 3,
                'total_out_bytes': 2 * 1024 ** 3,
                'peak_mbps': 2,
                'percentile_mbps': 1.9,
                'avg_mbps': 1.5,
            },
        }

    def test_traffic_uses_current_resource_traffic_binding_not_arbitrary_ids(self):
        overview_binding = self._binding(slot=MonitoringGraphBinding.PanelSlot.OVERVIEW)
        traffic_binding = self._binding(slot=MonitoringGraphBinding.PanelSlot.TRAFFIC)
        resolved = {
            'monitoring_host': self.host,
            'graph': None,
            'interface_name': self.item.interface_name,
            'in_item': self.item,
            'out_item': None,
            'title': '客户流量',
        }
        with patch('portal.monitoring.resolve_traffic_from_binding', return_value=resolved) as resolve, patch(
            'portal.monitoring.query_traffic',
            return_value=self._traffic_result(),
        ):
            response = self.client.get(
                f'/api/portal/edge/{self.edge.pk}/traffic/',
                {'hours': 24},
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(resolve.call_args.args[0].pk, traffic_binding.pk)
        self.assertNotEqual(resolve.call_args.args[0].pk, overview_binding.pk)
        data = response.data['data']
        self.assertEqual(data['data_source'], 'zabbix')
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['series'][0]['in_mbps'], 1.0)
        self.assertEqual(data['summary']['p95_mbps'], 1.9)

    def test_traffic_has_no_mock_for_missing_binding_no_data_or_zabbix_error(self):
        with patch('portal.monitoring.query_traffic') as query:
            missing = self.client.get(f'/api/portal/edge/{self.edge.pk}/traffic/')
        self.assertEqual(missing.status_code, 200, missing.data)
        self.assertEqual(missing.data['data']['status'], 'not_configured')
        self.assertEqual(missing.data['data']['series'], [])
        query.assert_not_called()

        self._binding()
        resolved = {
            'monitoring_host': self.host,
            'graph': None,
            'interface_name': self.item.interface_name,
            'in_item': self.item,
            'out_item': None,
            'title': '客户流量',
        }
        with patch('portal.monitoring.resolve_traffic_from_binding', return_value=resolved), patch(
            'portal.monitoring.query_traffic',
            return_value={'timeseries': [], 'billing': {}},
        ):
            no_data = self.client.get(f'/api/portal/edge/{self.edge.pk}/traffic/')
        self.assertEqual(no_data.data['data']['status'], 'no_data')
        self.assertEqual(no_data.data['data']['series'], [])

        with patch('portal.monitoring.resolve_traffic_from_binding', return_value=resolved), patch(
            'portal.monitoring.query_traffic',
            side_effect=ZabbixError('unreachable'),
        ):
            unavailable = self.client.get(f'/api/portal/edge/{self.edge.pk}/traffic/')
        self.assertEqual(unavailable.data['data']['status'], 'unavailable')
        self.assertEqual(unavailable.data['data']['series'], [])

    def test_traffic_supports_custom_time_from_till_window(self):
        self._binding(slot=MonitoringGraphBinding.PanelSlot.TRAFFIC)
        resolved = {
            'monitoring_host': self.host,
            'graph': None,
            'interface_name': self.item.interface_name,
            'in_item': self.item,
            'out_item': None,
            'title': '客户流量',
        }
        time_from = '2026-08-18T00:00:00'
        time_till = '2026-08-19T00:00:00'
        with patch('portal.monitoring.resolve_traffic_from_binding', return_value=resolved), patch(
            'portal.monitoring.query_traffic',
            return_value=self._traffic_result(),
        ) as query:
            response = self.client.get(
                f'/api/portal/edge/{self.edge.pk}/traffic/',
                {'time_from': time_from, 'time_till': time_till},
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['status'], 'ok')
        self.assertIn('~', response.data['data']['period'])
        self.assertIsNotNone(response.data['data']['time_from'])
        self.assertIsNotNone(response.data['data']['time_till'])
        kwargs = query.call_args.kwargs
        self.assertEqual(kwargs['time_from'].strftime('%Y-%m-%d %H:%M:%S'), '2026-08-18 00:00:00')
        self.assertEqual(kwargs['time_till'].strftime('%Y-%m-%d %H:%M:%S'), '2026-08-19 00:00:00')

    def test_traffic_rejects_invalid_custom_time_window(self):
        missing = self.client.get(
            f'/api/portal/edge/{self.edge.pk}/traffic/',
            {'time_from': '2026-08-18T00:00:00'},
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn('同时提供', missing.data['message'])

        too_long = self.client.get(
            f'/api/portal/edge/{self.edge.pk}/traffic/',
            {
                'time_from': '2026-08-01T00:00:00',
                'time_till': '2026-08-10T00:00:00',
            },
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertIn('最长 7 天', too_long.data['message'])

    def test_customer_cannot_read_another_customers_monitoring_resource(self):
        response = self.client.get(f'/api/portal/edge/{self.other_edge.pk}/traffic/')
        self.assertEqual(response.status_code, 404)

    def test_quality_aggregates_central_samples_and_marks_stale_data(self):
        bucket_start = timezone.now().replace(second=0, microsecond=0) - timedelta(minutes=2)
        for source_id, latency, loss, jitter, ok, sampled_at in (
            ('quality-1', 10, 0, 1, True, bucket_start),
            ('quality-2', 20, 2, 3, False, bucket_start + timedelta(seconds=20)),
        ):
            EdgeInterfaceQualitySample.objects.create(
                pop=self.pop,
                edge=self.edge,
                customer=self.customer,
                interface_name=self.edge.node_ifname,
                target_address='198.51.100.20',
                source_sample_id=source_id,
                sampled_at=sampled_at,
                latency_ms=latency,
                packet_loss_pct=loss,
                jitter_ms=jitter,
                packets_sent=5,
                packets_recv=5 if ok else 0,
                ok=ok,
            )

        response = self.client.get(f'/api/portal/edge/{self.edge.pk}/quality/', {'hours': 1})
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['data_source'], 'vpop_telemetry')
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(len(data['series']), 1)
        self.assertEqual(data['series'][0]['latency_ms'], 15.0)
        self.assertEqual(data['series'][0]['availability_pct'], 50.0)
        self.assertEqual(data['summary']['sample_count'], 2)

        EdgeInterfaceQualitySample.objects.filter(edge=self.edge).update(
            sampled_at=timezone.now() - timedelta(seconds=181),
        )
        stale = self.client.get(f'/api/portal/edge/{self.edge.pk}/quality/', {'hours': 1})
        self.assertEqual(stale.status_code, 200, stale.data)
        self.assertEqual(stale.data['data']['status'], 'stale')

    def test_quality_uses_hourly_rollups_for_longer_time_ranges(self):
        now = timezone.now().replace(second=0, microsecond=0)
        EdgeInterfaceQualitySample.objects.create(
            pop=self.pop,
            edge=self.edge,
            customer=self.customer,
            interface_name=self.edge.node_ifname,
            target_address='198.51.100.20',
            source_sample_id='quality-current',
            sampled_at=now,
            latency_ms=12,
            packet_loss_pct=0,
            jitter_ms=1,
            packets_sent=5,
            packets_recv=5,
            ok=True,
        )
        bucket_ts = (now - timedelta(hours=47)).replace(minute=0)
        EdgeInterfaceQualityRollup.objects.create(
            pop=self.pop,
            edge=self.edge,
            customer=self.customer,
            bucket_kind=EdgeInterfaceQualityRollup.BucketKind.HOUR,
            bucket_ts=bucket_ts,
            sample_count=6,
            ok_samples=5,
            latency_avg_ms=22,
            latency_max_ms=35,
            packet_loss_avg_pct=1,
            jitter_avg_ms=2,
            availability_pct=83.33,
            last_sampled_at=bucket_ts + timedelta(minutes=55),
        )

        response = self.client.get(f'/api/portal/edge/{self.edge.pk}/quality/', {'hours': 48})

        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(len(data['series']), 1)
        self.assertEqual(data['series'][0]['latency_ms'], 22.0)
        self.assertEqual(data['summary']['sample_count'], 6)


class PortalRolePermissionGateTests(TestCase):
    """只读客户账号不得调用变更 / 下单 / 电源等写接口。"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='readonly-portal',
            password='SafePass_2026!',
        )
        self.customer = Customer.objects.create(
            name='只读门户客户',
            contact_name='只读',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.READONLY
        profile.save(update_fields=['account_type', 'role'])
        # keepdb 可能残留只读角色的管理授权，清空后回退到静态 VIEW 权限
        from accounts.models import RolePermissionGrant
        RolePermissionGrant.objects.filter(role=UserProfile.Role.READONLY).delete()
        self.user = User.objects.select_related('profile').get(pk=self.user.pk)
        self.client.force_authenticate(self.user)

    def test_readonly_cannot_submit_change(self):
        response = self.client.post('/api/portal/services/1/change_request/', {
            'change_type': 'renew',
        }, format='json')
        self.assertEqual(response.status_code, 403, response.data)

    def test_readonly_cannot_renew(self):
        response = self.client.post('/api/portal/services/1/renew/', {
            'billing_cycle': 'monthly',
        }, format='json')
        self.assertEqual(response.status_code, 403, response.data)

    def test_readonly_cannot_create_order(self):
        response = self.client.post('/api/portal/orders/', {
            'product': 1,
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_readonly_cannot_power_vps(self):
        response = self.client.post('/api/portal/vps/1/power/', {
            'action': 'start',
        }, format='json')
        self.assertEqual(response.status_code, 403)

    def test_dashboard_capabilities_are_false(self):
        response = self.client.get('/api/portal/dashboard/')
        self.assertEqual(response.status_code, 200, response.data)
        caps = response.data['data']['capabilities']
        self.assertFalse(caps['service_change'])
        self.assertFalse(caps['order_create'])
        self.assertFalse(caps['vps_manage'])
        self.assertFalse(caps['edge_manage'])
        self.assertFalse(caps['profile_update'])
        self.assertFalse(caps['ticket_manage'])
        notices = response.data['data']['notices']
        self.assertEqual(notices['provisioning'], [])
        self.assertEqual(notices['ready'], [])

    def test_readonly_cannot_patch_profile(self):
        response = self.client.patch('/api/portal/profile/', {
            'contact_name': '只读改名',
        }, format='json')
        self.assertEqual(response.status_code, 403, response.data)


class PortalProfilePermissionTests(TestCase):
    """S-L1：客户用户可改资料，只读角色不可。"""

    def test_customer_user_can_patch_profile(self):
        user = User.objects.create_user('s3-profile', password='SafePass_2026!')
        Customer.objects.create(
            name='可写资料客户',
            contact_name='原名',
            account=user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        client = APIClient()
        client.force_authenticate(user)
        response = client.patch('/api/portal/profile/', {
            'contact_name': '新联系人',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['data']['contact_name'], '新联系人')


class PortalSupportConfigSplitTests(TestCase):
    """对外客服只读公开参数，不回退 IM 对内目标。"""

    def setUp(self):
        self.client = APIClient()
        invalidate_public_support_cache()
        for key in (
            'support_qq', 'support_wechat', 'support_wechat_qr',
            'support_telegram', 'support_wecom_kf_url',
            'support_email', 'support_phone',
        ):
            setting, _ = SystemSetting.objects.get_or_create(
                key=key,
                defaults={'label': key, 'category': 'support', 'is_public': True},
            )
            setting.category = 'support'
            setting.is_public = True
            setting.value = ''
            setting.save()

    def test_does_not_fallback_to_im_telegram_chat_id(self):
        IMChannel.objects.create(
            name='运维 Telegram',
            channel_type=IMChannelType.TELEGRAM,
            enabled=True,
            default_target='-1001234567890',
        )
        response = self.client.get('/api/portal/support-config/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['telegram'], '')
        self.assertEqual(response.get('Cache-Control'), 'no-store')

    def test_uses_public_telegram_not_im_target(self):
        setting = SystemSetting.objects.get(key='support_telegram')
        setting.value = '@helpdesk'
        setting.save()
        IMChannel.objects.create(
            name='运维 Telegram',
            channel_type=IMChannelType.TELEGRAM,
            enabled=True,
            default_target='-1001234567890',
        )
        response = self.client.get('/api/portal/support-config/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['telegram'], '@helpdesk')

    def test_wecom_url_comes_from_setting_not_im_channel(self):
        IMChannel.objects.create(
            name='企微客服通道',
            channel_type=IMChannelType.WECOM_KF,
            enabled=True,
            wecom_kf_url='https://work.weixin.qq.com/kfid/internal-only',
        )
        response = self.client.get('/api/portal/support-config/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['wecom_kf_url'], '')

        setting = SystemSetting.objects.get(key='support_wecom_kf_url')
        setting.value = 'https://work.weixin.qq.com/kfid/public'
        setting.save()
        response = self.client.get('/api/portal/support-config/')
        self.assertEqual(response.data['wecom_kf_url'], 'https://work.weixin.qq.com/kfid/public')

    def test_setting_save_invalidates_cache(self):
        from django.core.cache import cache

        setting = SystemSetting.objects.get(key='support_qq')
        setting.value = '11111'
        setting.save()
        first = self.client.get('/api/portal/support-config/')
        self.assertEqual(first.data['qq'], '11111')
        self.assertIsNotNone(cache.get(CACHE_KEY))

        setting.value = '22222'
        setting.save()
        second = self.client.get('/api/portal/support-config/')
        self.assertEqual(second.data['qq'], '22222')


class PortalRenewApiTests(TestCase):
    def setUp(self):
        from resource_manager.models import VPS
        from service_manager.models import ServiceInstance, ServiceOrder

        self.client = APIClient()
        self.user = User.objects.create_user(
            username='portal-renew-user',
            password='SafePass_2026!',
        )
        self.customer = Customer.objects.create(
            name='门户续费客户',
            contact_name='续费联系人',
            account=self.user,
            status='active',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.account_type = UserProfile.AccountType.CUSTOMER
        profile.role = UserProfile.Role.CUSTOMER_USER
        profile.save(update_fields=['account_type', 'role'])
        self.user = User.objects.select_related('profile').get(pk=self.user.pk)
        region = Region.objects.create(code='portal-rnw', name='门户续费区')
        country = Country.objects.create(region=region, name='门户续费国')
        city = City.objects.create(country=country, name='门户续费市')
        self.pop = PopPoint.objects.create(name='门户续费 POP', city=city)
        self.product = Product.objects.create(
            name='门户续费产品',
            status=Product.Status.ONLINE,
            pop_point=self.pop,
            monthly_price='80.00',
            setup_price='0.00',
            currency='CNY',
            cpu_cores=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=5,
            ip_count=1,
        )
        self.order = ServiceOrder.objects.create(
            order_type=ServiceOrder.OrderType.NEW,
            business_type=ServiceOrder.BusinessType.VPS,
            status=ServiceOrder.OrderStatus.ACTIVE,
            customer=self.customer,
            product=self.product,
            preferred_pop=self.pop,
            quoted_price=Decimal('80.00'),
        )
        self.vps = VPS.objects.create(
            name='门户续费 VPS',
            pop_point=self.pop,
            customer=self.customer,
            product=self.product,
            vcpu=1,
            ram_mb=1024,
            disk_gb=20,
            bandwidth_mbps=5,
            status='running',
        )
        self.instance = ServiceInstance.objects.create(
            service_order=self.order,
            service_type='vps',
            status='active',
            vps=self.vps,
            monthly_fee=Decimal('80.00'),
            fee_currency='CNY',
            expire_time=timezone.now() + timedelta(days=8),
        )
        self.channel = PaymentChannel.objects.create(
            provider='wechat',
            name='续费微信通道',
            enabled=True,
        )
        self.client.force_authenticate(self.user)

    def test_renew_quote_returns_cycle_amount(self):
        response = self.client.post(
            f'/api/portal/services/{self.instance.pk}/renew_quote/',
            {'billing_cycle': 'annual'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual(data['due_now'], '960.00')
        self.assertEqual(data['months'], 12)
        self.assertEqual(data['billing_cycle'], 'annual')

    @patch('portal.views.billing_payment_service.available_channels')
    @patch('portal.views.billing_payment_service.issue_gateway_checkout')
    @patch('portal.views.billing_payment_service.create_payment_transaction')
    def test_renew_creates_billing_and_checkout(
        self,
        create_payment,
        issue_checkout,
        available_channels,
    ):
        available_channels.return_value = [self.channel]

        def create_transaction(*, billing, channel, operator, return_url):
            return PaymentTransaction.objects.create(
                billing=billing,
                channel=channel,
                provider=channel.provider,
                amount=billing.amount,
                currency=billing.currency,
                billing_amount=billing.amount,
                billing_currency=billing.currency,
                initiated_by=operator,
                return_url=return_url,
            )

        def issue_transaction(*, payment, callback_url, gateway_return_url):
            payment.status = PaymentTransaction.Status.PENDING
            payment.save(update_fields=['status', 'updated_at'])
            return payment, {
                'mode': 'redirect',
                'redirect_url': 'https://pay.example/renew',
            }

        create_payment.side_effect = create_transaction
        issue_checkout.side_effect = issue_transaction

        response = self.client.post(
            f'/api/portal/services/{self.instance.pk}/renew/',
            {'billing_cycle': 'quarterly', 'channel_id': self.channel.id},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data['data']
        self.assertTrue(data['payment_required'])
        self.assertEqual(data['quote']['due_now'], '240.00')
        self.assertEqual(data['billing']['amount'], '240.00')
        self.assertEqual(data['checkout']['redirect_url'], 'https://pay.example/renew')
        self.assertEqual(data['order']['order_type'], 'renew')
