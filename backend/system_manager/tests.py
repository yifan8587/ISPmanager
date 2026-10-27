from django.test import TestCase
from unittest.mock import Mock, patch

from . import im_services
from .models import (
    IMChannel, IMChannelType, IMTestStatus, NotificationEvent, NotificationFormatMode,
    NotificationRule, PaymentChannel,
)
from .notification_templates import render_rule_message
from .serializers import IMChannelSerializer, NotificationRuleSerializer, PaymentChannelSerializer
from . import currency_services as cs
from decimal import Decimal


class PaymentCallbackUrlTests(TestCase):
    def test_callback_url_uses_public_site_domain(self):
        from django.test import RequestFactory, override_settings

        from .payment_urls import payment_callback_url

        channel = PaymentChannel.objects.create(provider='alipay', name='支付宝', enabled=False)
        request = RequestFactory().get('/api/system/payment-channels/')
        request.META['HTTP_HOST'] = '127.0.0.1:3000'
        with override_settings(SITE_DOMAIN='pay.example.test', PAYMENT_PUBLIC_BASE_URL='', ADMIN_DOMAIN='admin.example.test'):
            url = payment_callback_url(channel.id, request)
        self.assertEqual(
            url,
            f'https://pay.example.test/api/customer/payments/webhooks/{channel.id}/',
        )
        with override_settings(
            SITE_DOMAIN='',
            PAYMENT_PUBLIC_BASE_URL='',
            ADMIN_DOMAIN='admin.example.test',
            ADMIN_PUBLIC_PORT='20080',
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        ):
            url = payment_callback_url(channel.id, request)
        self.assertEqual(
            url,
            f'https://admin.example.test:20080/api/customer/payments/webhooks/{channel.id}/',
        )
        data = PaymentChannelSerializer(channel, context={'request': request}).data
        self.assertIn(f'/api/customer/payments/webhooks/{channel.id}/', data['callback_url'])
        self.assertIn('notify_url', data['callback_usage'])


class PaymentChannelCredentialTests(TestCase):
    def test_credentials_are_encrypted_and_not_serialized(self):
        channel = PaymentChannel(
            provider='stripe',
            name='Stripe 测试',
            enabled=True,
        )
        channel.set_config({
            'publishable_key': 'pk_test_123',
            'secret_key': 'sk_test_super_secret',
            'webhook_secret': 'whsec_secret',
        })
        channel.save()

        channel.refresh_from_db()
        self.assertNotIn('sk_test_super_secret', channel.encrypted_config)
        self.assertEqual(channel.get_config()['secret_key'], 'sk_test_super_secret')

        data = PaymentChannelSerializer(channel).data
        self.assertEqual(data['config_view']['publishable_key'], 'pk_test_123')
        self.assertTrue(data['config_view']['secret_key_configured'])
        self.assertNotIn('secret_key', data['config_view'])
        self.assertNotIn('sk_test_super_secret', str(data))

    def test_enabled_channel_requires_provider_credentials(self):
        serializer = PaymentChannelSerializer(data={
            'provider': 'paypal',
            'name': 'PayPal',
            'enabled': True,
            'config': {'client_id': 'client-only'},
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('config', serializer.errors)

    def test_blank_secret_update_preserves_existing_secret(self):
        channel = PaymentChannel(provider='stripe', name='Stripe', enabled=True)
        channel.set_config({
            'publishable_key': 'pk_test_old',
            'secret_key': 'sk_test_keep_me',
            'webhook_secret': 'whsec_keep_me',
        })
        channel.save()

        serializer = PaymentChannelSerializer(
            channel,
            data={
                'name': 'Stripe 更新',
                'config': {'publishable_key': 'pk_test_new', 'secret_key': ''},
            },
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        saved = serializer.save()
        self.assertEqual(saved.get_config()['publishable_key'], 'pk_test_new')
        self.assertEqual(saved.get_config()['secret_key'], 'sk_test_keep_me')


class WeComWebhookTests(TestCase):
    def setUp(self):
        self.channel = IMChannel.objects.create(
            name='企业微信群机器人',
            channel_type=IMChannelType.WECOM_WEBHOOK,
            webhook_url=(
                'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
                '?key=00000000-0000-0000-0000-000000000000'
            ),
        )

    @patch('system_manager.im_services.requests.post')
    def test_group_robot_uses_official_text_message_format(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'errcode': 0, 'errmsg': 'ok'}
        response.text = '{"errcode":0,"errmsg":"ok"}'
        post.return_value = response

        result = im_services.send_to_channel(
            self.channel,
            '测试标题',
            '测试正文',
            event='custom',
        )

        self.assertEqual(result['sent'], 1)
        self.assertEqual(result['failed'], 0)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['msgtype'], 'text')
        self.assertEqual(payload['text']['content'], '【测试标题】\n测试正文')
        self.assertNotIn('title', payload)

    @patch('system_manager.im_services.requests.post')
    def test_http_200_with_wecom_error_is_still_failure(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {
            'errcode': 40008,
            'errmsg': 'invalid message type',
        }
        response.text = '{"errcode":40008,"errmsg":"invalid message type"}'
        post.return_value = response

        result = im_services.test_channel(self.channel)
        self.channel.refresh_from_db()

        self.assertFalse(result['success'])
        self.assertEqual(result['failed'], 1)
        self.assertEqual(self.channel.last_test_status, IMTestStatus.FAILED)

    def test_group_robot_rejects_application_message_url(self):
        serializer = IMChannelSerializer(data={
            'name': '错误地址',
            'channel_type': IMChannelType.WECOM_WEBHOOK,
            'webhook_url': (
                'https://qyapi.weixin.qq.com/cgi-bin/message/send'
                '?access_token=token'
            ),
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn('webhook_url', serializer.errors)


class NotificationRuleRenderingTests(TestCase):
    def setUp(self):
        self.channel = IMChannel.objects.create(
            name='测试 Webhook',
            channel_type=IMChannelType.WEBHOOK,
            webhook_url='https://example.test/notifications',
        )

    def test_detailed_template_masks_sensitive_change_values(self):
        title, body, context = render_rule_message(
            NotificationEvent.CUSTOMER_CHANGE,
            context={
                'module': '客户管理',
                'action': 'update',
                'action_label': '更新',
                'customer_name': '示例客户',
                'target_type': '客户',
                'target_name': '示例客户',
                'operator_name': '管理员',
                'old_value': {'status': 'pending', 'api_token': 'secret-old'},
                'new_value': {'status': 'active', 'api_token': 'secret-new'},
            },
            format_mode=NotificationFormatMode.DETAILED,
        )

        self.assertEqual(title, '【客户变更】更新 · 示例客户')
        self.assertIn('【客户变更】更新', body)
        self.assertIn('操作人：管理员', body)
        self.assertIn('时间：', body)
        self.assertIn('客户：示例客户', body)
        self.assertIn('变更明细：', body)
        self.assertIn('• 状态：pending → active', body)
        self.assertNotIn('未记录变更前快照', body)
        self.assertNotIn('api_token', context['change_summary'])
        self.assertNotIn('secret-old', body)
        self.assertNotIn('secret-new', body)
        self.assertNotIn('secret-old', context['change_summary'])
        self.assertNotIn('secret-new', context['change_summary'])

    def test_default_bodies_differ_by_event_topic(self):
        inquiry_title, inquiry_body, _ = render_rule_message(
            NotificationEvent.SERVICE_INQUIRY,
            context={
                'action': 'create',
                'action_label': '提交需求',
                'target_name': 'INQ20260001',
                'inquiry_type': '需要帮助',
                'contact_name': '王客户',
                'contact': '13800000000',
                'requirements': '想开通一台东京 VPS',
                'operator_name': '王客户',
                'occurred_at': '2026-08-23 01:00:00',
            },
        )
        pay_title, pay_body, _ = render_rule_message(
            NotificationEvent.BILLING_PAYMENT,
            context={
                'action': 'succeeded',
                'action_label': '支付成功',
                'customer_name': '示例客户',
                'bill_no': 'BILL20260001',
                'amount': '199.00 CNY',
                'operator_name': '支付网关',
                'occurred_at': '2026-08-23 01:05:00',
            },
        )
        alert_title, alert_body, _ = render_rule_message(
            NotificationEvent.SYSTEM_ALERT,
            context={
                'action': 'alert',
                'action_label': 'POP 丢包告警',
                'target_name': 'Tokyo-01',
                'pop_name': 'Tokyo-01',
                'operator_name': '系统品质监控',
                'detail': 'ICMP 丢包 12%，连续 3 次超阈值',
                'occurred_at': '2026-08-23 01:10:00',
            },
        )

        self.assertIn('【客户需求】提交需求', inquiry_title)
        self.assertIn('联系人：王客户', inquiry_body)
        self.assertIn('需求：想开通一台东京 VPS', inquiry_body)
        self.assertIn('【客户收款】支付成功', pay_title)
        self.assertIn('账单：BILL20260001', pay_body)
        self.assertIn('金额：199.00 CNY', pay_body)
        self.assertIn('触发：系统品质监控', alert_body)
        self.assertIn('说明：ICMP 丢包 12%', alert_body)
        self.assertNotEqual(inquiry_body, pay_body)
        self.assertNotEqual(pay_body, alert_body)
        self.assertNotIn('未记录变更前快照', inquiry_body)
        self.assertNotIn('未记录变更前快照', pay_body)
        self.assertNotIn('未记录变更前快照', alert_body)

    @patch('system_manager.im_services.send_to_channel')
    def test_rule_uses_structured_title_and_body_templates(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='客户变更',
            event=NotificationEvent.CUSTOMER_CHANGE,
            title_template='客户：{customer_name} / {action_label}',
            template='编号：{customer_no}\n对象：{target_name}\n说明：{detail}',
            format_mode=NotificationFormatMode.CUSTOM,
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.CUSTOMER_CHANGE,
            context={
                'action': 'update',
                'action_label': '更新',
                'customer_name': '测试客户',
                'customer_no': 'CUS20260001',
                'target_name': '测试客户',
                'detail': '调整客户等级',
            },
        )

        self.assertEqual(result['dispatched'], 1)
        send_to_channel.assert_called_once()
        self.assertEqual(send_to_channel.call_args.args[1], '客户：测试客户 / 更新')
        self.assertIn('编号：CUS20260001', send_to_channel.call_args.args[2])

    @patch('system_manager.im_services.send_to_channel')
    def test_action_filters_skip_unmatched_events(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='仅收款成功',
            event=NotificationEvent.BILLING_PAYMENT,
            action_filters=['payment'],
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.BILLING_PAYMENT,
            context={'action': 'failed', 'action_label': '支付失败', 'bill_no': 'BILL001'},
        )

        self.assertEqual(result['matched_rules'], 0)
        self.assertEqual(result['dispatched'], 0)
        send_to_channel.assert_not_called()
        self.assertEqual(result['skip_reasons'][0]['reason'], 'action_filter')

    @patch('system_manager.im_services.send_to_channel')
    def test_empty_action_does_not_skip_filtered_rule(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='仅收款成功',
            event=NotificationEvent.BILLING_PAYMENT,
            action_filters=['payment'],
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.BILLING_PAYMENT,
            title='手动测试',
            body='请发送',
        )

        self.assertEqual(result['matched_rules'], 1)
        self.assertEqual(result['dispatched'], 1)
        send_to_channel.assert_called_once()

    @patch('system_manager.im_services.send_to_channel')
    def test_manual_dispatch_ignores_action_filters(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='仅收款成功',
            event=NotificationEvent.BILLING_PAYMENT,
            action_filters=['payment'],
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.BILLING_PAYMENT,
            title='手动触发',
            body='测试正文',
            context={'action': 'failed'},
            skip_action_filters=True,
            rule_id=rule.pk,
        )

        self.assertEqual(result['dispatched'], 1)
        send_to_channel.assert_called_once()

    @patch('system_manager.im_services.send_to_channel')
    def test_payment_succeeded_matches_payment_filter(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='收款',
            event=NotificationEvent.BILLING_PAYMENT,
            action_filters=['payment'],
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.BILLING_PAYMENT,
            context={'action': 'succeeded', 'bill_no': 'BILL001'},
        )

        self.assertEqual(result['dispatched'], 1)
        send_to_channel.assert_called_once()

    def test_rule_can_match_multiple_events_with_per_event_actions(self):
        serializer = NotificationRuleSerializer(data={
            'name': '客户和收款',
            'events': [
                NotificationEvent.CUSTOMER_CHANGE,
                NotificationEvent.BILLING_PAYMENT,
            ],
            'event_action_filters': {
                NotificationEvent.CUSTOMER_CHANGE: ['update'],
                NotificationEvent.BILLING_PAYMENT: ['payment'],
            },
            'channels': [self.channel.pk],
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        rule = serializer.save()
        self.assertEqual(rule.event, NotificationEvent.CUSTOMER_CHANGE)
        self.assertEqual(rule.events, [
            NotificationEvent.CUSTOMER_CHANGE,
            NotificationEvent.BILLING_PAYMENT,
        ])
        self.assertTrue(rule.matches_event(NotificationEvent.BILLING_PAYMENT))
        self.assertEqual(
            rule.action_filters_for_event(NotificationEvent.BILLING_PAYMENT),
            ['payment'],
        )
        serialized = NotificationRuleSerializer(rule).data
        self.assertEqual(serialized['events'], [
            NotificationEvent.CUSTOMER_CHANGE,
            NotificationEvent.BILLING_PAYMENT,
        ])
        self.assertEqual(
            serialized['event_action_filters'][NotificationEvent.BILLING_PAYMENT],
            ['payment'],
        )

    @patch('system_manager.im_services.send_to_channel')
    def test_multi_event_rule_only_uses_matched_event_actions(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='客户和收款',
            event=NotificationEvent.CUSTOMER_CHANGE,
            events=[
                NotificationEvent.CUSTOMER_CHANGE,
                NotificationEvent.BILLING_PAYMENT,
            ],
            event_action_filters={
                NotificationEvent.CUSTOMER_CHANGE: ['update'],
                NotificationEvent.BILLING_PAYMENT: ['payment'],
            },
        )
        rule.channels.add(self.channel)

        result = im_services.dispatch_event(
            NotificationEvent.BILLING_PAYMENT,
            context={'action': 'payment', 'action_label': '支付成功', 'bill_no': 'BILL001'},
        )

        self.assertEqual(result['matched_rules'], 1)
        self.assertEqual(result['dispatched'], 1)
        send_to_channel.assert_called_once()

    @patch('system_manager.im_services.send_to_channel')
    def test_resource_change_rule_matches_vps_and_edge_events(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='全部资源',
            event=NotificationEvent.RESOURCE_CHANGE,
            events=[NotificationEvent.RESOURCE_CHANGE],
        )
        rule.channels.add(self.channel)

        vps_result = im_services.dispatch_event(
            NotificationEvent.VPS_CHANGE,
            context={'action': 'update', 'action_label': '更新', 'vps_no': 'VPS1'},
        )
        edge_result = im_services.dispatch_event(
            NotificationEvent.EDGE_ACCELERATION_CHANGE,
            context={'action': 'config_change', 'action_label': '协议配置变更', 'edge_no': 'EDGE1'},
        )

        self.assertEqual(vps_result['dispatched'], 1)
        self.assertEqual(edge_result['dispatched'], 1)
        self.assertEqual(send_to_channel.call_count, 2)

    @patch('system_manager.im_services.send_to_channel')
    def test_resource_parent_action_filters_apply_to_child_events(self, send_to_channel):
        rule = NotificationRule.objects.create(
            name='仅新建资源',
            event=NotificationEvent.RESOURCE_CHANGE,
            events=[NotificationEvent.RESOURCE_CHANGE],
            event_action_filters={NotificationEvent.RESOURCE_CHANGE: ['create']},
        )
        rule.channels.add(self.channel)

        skipped = im_services.dispatch_event(
            NotificationEvent.IP_CHANGE,
            context={'action': 'update', 'action_label': '更新'},
        )
        matched = im_services.dispatch_event(
            NotificationEvent.IP_CHANGE,
            context={'action': 'create', 'action_label': '新建'},
        )

        self.assertEqual(skipped['dispatched'], 0)
        self.assertEqual(skipped['skip_reasons'][0]['reason'], 'action_filter')
        self.assertEqual(matched['dispatched'], 1)
        send_to_channel.assert_called_once()


class CustomerChangeSignalTests(TestCase):
    @patch('system_manager.im_services.notify')
    def test_customer_operation_log_is_routed_to_customer_event(self, notify):
        from customer_manager.models import Customer, CustomerOperationLog, record_operation_log

        customer = Customer.objects.create(
            name='信号测试客户',
            contact_name='联系人',
            status='active',
        )
        with self.captureOnCommitCallbacks(execute=True):
            record_operation_log(
                operator=None,
                action_type=CustomerOperationLog.ActionType.STATUS_CHANGE,
                description='客户状态变更',
                customer=customer,
                target_model=CustomerOperationLog.TargetModel.CUSTOMER,
                target_id=customer.pk,
                old_value={'status': 'pending'},
                new_value={'status': 'active'},
            )

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], NotificationEvent.CUSTOMER_CHANGE)
        context = notify.call_args.kwargs['context']
        self.assertEqual(context['customer_name'], '信号测试客户')
        self.assertEqual(context['action'], CustomerOperationLog.ActionType.STATUS_CHANGE)


class ResourceChangeSignalTests(TestCase):
    @patch('system_manager.im_services.notify')
    def test_vps_operation_log_routes_to_vps_event(self, notify):
        from resource_manager.models import ResourceOperationLog, record_resource_log

        with self.captureOnCommitCallbacks(execute=True):
            record_resource_log(
                operator=None,
                action_type=ResourceOperationLog.ActionType.UPDATE,
                description='更新 VPS 规格',
                target_model=ResourceOperationLog.TargetModel.VPS,
                target_id=1,
                target_repr='VPS20260001',
            )

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], NotificationEvent.VPS_CHANGE)
        self.assertEqual(notify.call_args.kwargs['context']['action'], 'update')

    @patch('system_manager.im_services.notify')
    def test_edge_operation_log_routes_to_edge_event(self, notify):
        from resource_manager.models import ResourceOperationLog, record_resource_log

        with self.captureOnCommitCallbacks(execute=True):
            record_resource_log(
                operator=None,
                action_type=ResourceOperationLog.ActionType.CONFIG_CHANGE,
                description='门户更新边缘协议配置',
                target_model=ResourceOperationLog.TargetModel.EDGE,
                target_id=2,
                target_repr='EDGE20260001',
            )

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], NotificationEvent.EDGE_ACCELERATION_CHANGE)


class MonitoringAlertSignalTests(TestCase):
    @patch('system_manager.im_services.notify')
    def test_failed_monitoring_sync_is_routed_to_system_alert(self, notify):
        from operation_manager.models import MonitoringSyncLog, ZabbixServer

        server = ZabbixServer.objects.create(
            name='Zabbix 测试',
            api_url='https://zabbix.example.test/api_jsonrpc.php',
        )
        with self.captureOnCommitCallbacks(execute=True):
            MonitoringSyncLog.objects.create(
                zabbix_server=server,
                action_type=MonitoringSyncLog.ActionType.FULL_SYNC,
                status=MonitoringSyncLog.Status.FAILED,
                message='无法连接监控服务',
                detail={'api_token': 'must-not-leak'},
            )

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], 'system_alert')
        context = notify.call_args.kwargs['context']
        self.assertEqual(context['target_name'], 'Zabbix 测试')
        self.assertEqual(context['action'], 'alert')


class PortalNotifyActionTests(TestCase):
    def test_portal_order_subjects_use_stable_actions(self):
        from unittest.mock import MagicMock
        from service_manager.notify_helpers import _order_context

        order = MagicMock()
        order.pk = 1
        order.order_no = 'SO1'
        order.customer.name = '客户'
        order.customer.customer_no = 'CUS1'
        order.customer.contact_email = 'a@example.test'
        order.preferred_pop_id = None
        order.handler = None
        order.approved_by = None
        order.created_by = None
        order.quoted_price = None
        order.requested_vcpu = None
        order.requested_ram_mb = None
        order.requested_disk_gb = None
        order.requested_bandwidth_mbps = None
        order.requested_ip_count = None
        order.requirements = ''

        self.assertEqual(_order_context(order, '门户下单')['action'], 'create_order')
        self.assertEqual(_order_context(order, '门户直付订单已确认')['action'], 'create_order')
        self.assertEqual(_order_context(order, '门户变更申请')['action'], 'change')
        self.assertEqual(_order_context(order, '门户一键续费')['action'], 'renew')


class SettlementCurrencyTests(TestCase):
    def test_rates_follow_selected_settlement_currency(self):
        from .services import set_setting
        set_setting('default_currency', 'USD')
        set_setting('exchange_rate_usd_cny', '8.00')
        set_setting('exchange_rate_eur_cny', '8.80')

        shown = cs.rates_to_settlement('USD')
        self.assertEqual(shown['USD'], Decimal('1'))
        self.assertEqual(shown['CNY'], Decimal('0.125'))
        self.assertEqual(shown['EUR'], Decimal('1.1'))

        pivoted = cs.pivot_rates_from_settlement('USD', {
            'CNY': '0.125',
            'EUR': '1.10',
            'CREDIT': '8',
        })
        self.assertEqual(pivoted['USD'], Decimal('8'))
        self.assertEqual(pivoted['EUR'], Decimal('8.8'))
        self.assertEqual(cs.convert(Decimal('10'), 'EUR', 'USD'), Decimal('11.00'))

    def test_sum_in_settlement_converts_mixed_currencies(self):
        from .services import set_setting
        set_setting('default_currency', 'USD')
        set_setting('exchange_rate_usd_cny', '8.00')
        result = cs.sum_in_settlement(
            [
                {'currency': 'CNY', 'amount': '16.00'},
                {'currency': 'USD', 'amount': '3.00'},
            ],
            ('amount',),
        )
        self.assertEqual(result['currency'], 'USD')
        self.assertEqual(result['amount'], '5.00')
