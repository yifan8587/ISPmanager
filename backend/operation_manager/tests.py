from datetime import timedelta
from subprocess import CompletedProcess
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from customer_manager.models import Customer
from resource_manager.models import City, Country, EdgeAcceleration, PopPoint, Region

from .models import (
    EdgeInterfaceQualitySample,
    EdgeInterfaceQualityRollup,
    PopQualityMonitor,
    PopQualityProbe,
    PopTelemetryCredential,
)
from .edge_quality_rollups import rebuild_edge_quality_rollups
from .pop_quality import (
    discover_pop_quality_monitors,
    probe_icmp,
    run_monitor_probe,
)


class PopQualityMonitorTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='apac', name='亚太')
        country = Country.objects.create(region=region, name='中国')
        city = City.objects.create(country=country, name='上海')
        self.pop = PopPoint.objects.create(
            name='上海品质测试 POP',
            city=city,
            mgmt_ip='198.51.100.10',
            mgmt_url='https://pop.example.test/',
        )
        self.monitor = PopQualityMonitor.objects.create(pop_point=self.pop)

    @staticmethod
    def _icmp(loss, avg=18.5):
        received = 0 if loss >= 100 else 4
        return {
            'target': '198.51.100.10',
            'sent': 4,
            'received': received,
            'packet_loss_pct': loss,
            'min_ms': avg - 1 if received else None,
            'avg_ms': avg if received else None,
            'max_ms': avg + 1 if received else None,
            'success': received > 0,
            'error': '',
        }

    @staticmethod
    def _http(success=True):
        return {
            'target': 'https://pop.example.test/',
            'status_code': 200 if success else 503,
            'response_ms': 30.2,
            'success': success,
            'error': '' if success else 'HTTP 503',
        }

    @patch('operation_manager.pop_quality.subprocess.run')
    def test_icmp_parser_reads_loss_and_latency(self, run):
        run.return_value = CompletedProcess(
            args=['ping'],
            returncode=0,
            stdout=(
                '4 packets transmitted, 3 received, 25% packet loss, time 3004ms\n'
                'rtt min/avg/max/mdev = 10.100/20.200/30.300/1.000 ms\n'
            ),
            stderr='',
        )

        result = probe_icmp('198.51.100.10', count=4, timeout_seconds=2)

        self.assertEqual(result['sent'], 4)
        self.assertEqual(result['received'], 3)
        self.assertEqual(result['packet_loss_pct'], 25.0)
        self.assertEqual(result['avg_ms'], 20.2)
        self.assertTrue(result['success'])

    @patch('system_manager.im_services.notify')
    @patch('operation_manager.pop_quality.probe_http')
    @patch('operation_manager.pop_quality.probe_icmp')
    def test_loss_alert_requires_two_consecutive_samples_then_recovers(
        self,
        probe_icmp_mock,
        probe_http_mock,
        notify,
    ):
        probe_http_mock.return_value = self._http()
        probe_icmp_mock.return_value = self._icmp(50)

        with self.captureOnCommitCallbacks(execute=True):
            first = run_monitor_probe(self.monitor.pk)
        self.monitor.refresh_from_db()
        self.assertEqual(first.alert_kind, '')
        self.assertFalse(self.monitor.alert_active)
        notify.assert_not_called()

        with self.captureOnCommitCallbacks(execute=True):
            second = run_monitor_probe(self.monitor.pk)
        self.monitor.refresh_from_db()
        self.assertEqual(second.alert_kind, PopQualityProbe.AlertKind.ALERT)
        self.assertTrue(self.monitor.alert_active)
        self.assertEqual(self.monitor.consecutive_breaches, 2)
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], 'system_alert')
        self.assertEqual(notify.call_args.kwargs['context']['action'], 'alert')

        probe_icmp_mock.return_value = self._icmp(0)
        with self.captureOnCommitCallbacks(execute=True):
            recovered = run_monitor_probe(self.monitor.pk)
        self.monitor.refresh_from_db()
        self.assertEqual(recovered.alert_kind, PopQualityProbe.AlertKind.RECOVERED)
        self.assertFalse(self.monitor.alert_active)
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(notify.call_args.kwargs['context']['action'], 'recovered')

    @patch('operation_manager.pop_quality.probe_http')
    @patch('operation_manager.pop_quality.probe_icmp')
    def test_maintenance_pop_suppresses_new_alerts(self, probe_icmp_mock, probe_http_mock):
        self.pop.status = PopPoint.PopStatus.MAINTENANCE
        self.pop.save(update_fields=['status'])
        probe_icmp_mock.return_value = self._icmp(100)
        probe_http_mock.return_value = self._http()

        probe = run_monitor_probe(self.monitor.pk)

        self.monitor.refresh_from_db()
        self.assertEqual(probe.status, PopQualityProbe.Status.FAILED)
        self.assertEqual(probe.alert_kind, '')
        self.assertFalse(self.monitor.alert_active)
        self.assertEqual(self.monitor.consecutive_breaches, 0)

    def test_discovery_creates_only_pops_with_management_targets(self):
        city = self.pop.city
        PopPoint.objects.create(name='无目标 POP', city=city)
        second = PopPoint.objects.create(
            name='仅 URL POP',
            city=city,
            mgmt_url='https://url-only.example.test/',
        )

        result = discover_pop_quality_monitors()

        self.assertEqual(result['created'], 1)
        self.assertTrue(PopQualityMonitor.objects.filter(pop_point=second).exists())
        self.assertEqual(PopQualityMonitor.objects.count(), 2)


class PopQualityMonitorApiTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='apac', name='亚太')
        country = Country.objects.create(region=region, name='中国')
        city = City.objects.create(country=country, name='上海')
        self.pop = PopPoint.objects.create(
            name='API 品质测试 POP',
            city=city,
            mgmt_ip='203.0.113.20',
            mgmt_url='https://quality.example.test/',
        )
        self.monitor = PopQualityMonitor.objects.create(pop_point=self.pop)
        PopQualityProbe.objects.create(
            monitor=self.monitor,
            status=PopQualityProbe.Status.SUCCESS,
            icmp_target=self.pop.mgmt_ip,
            icmp_sent=4,
            icmp_received=4,
            icmp_packet_loss_pct=0,
            icmp_avg_ms=12.5,
            icmp_success=True,
            http_target=self.pop.mgmt_url,
            http_status_code=200,
            http_response_ms=20,
            http_success=True,
        )
        self.user = User.objects.create_superuser(
            username='quality-admin',
            email='quality-admin@example.test',
            password='test-password',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_history_returns_two_generic_chart_payloads(self):
        url = reverse('pop-quality-monitor-history', kwargs={'pk': self.monitor.pk})

        response = self.client.get(url, {'period': '24h'})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['quality_chart']['chart_type'], 'generic')
        self.assertEqual(response.data['latency_chart']['chart_type'], 'generic')
        self.assertEqual(response.data['summary']['sample_count'], 1)
        self.assertTrue(response.data['quality_chart']['series'][0]['points'])

    def test_discover_endpoint_returns_existing_and_created_counts(self):
        url = reverse('pop-quality-monitor-discover')

        response = self.client.post(url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['created'], 0)
        self.assertEqual(response.data['existing'], 1)

    def test_configuration_update_is_scheduled_for_immediate_probe(self):
        self.monitor.next_probe_at = timezone.now() + timedelta(hours=1)
        self.monitor.save(update_fields=['next_probe_at'])
        url = reverse('pop-quality-monitor-detail', kwargs={'pk': self.monitor.pk})

        response = self.client.patch(
            url,
            {'probe_interval_seconds': 120},
            format='json',
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.monitor.refresh_from_db()
        self.assertLessEqual(self.monitor.next_probe_at, timezone.now())


class EdgeQualityRollupTests(TestCase):
    def setUp(self):
        region = Region.objects.create(code='rollup-apac', name='汇总亚太')
        country = Country.objects.create(region=region, name='汇总国')
        city = City.objects.create(country=country, name='汇总市')
        self.pop = PopPoint.objects.create(name='汇总 POP', city=city)
        self.customer = Customer.objects.create(name='汇总客户', contact_name='汇总联系人')
        self.edge = EdgeAcceleration.objects.create(
            name='汇总边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre-rollup',
        )

    def _sample(self, source_sample_id, sampled_at, latency, *, ok=True):
        return EdgeInterfaceQualitySample.objects.create(
            pop=self.pop,
            edge=self.edge,
            customer=self.customer,
            interface_name=self.edge.node_ifname,
            target_address='198.51.100.30',
            source_sample_id=source_sample_id,
            sampled_at=sampled_at,
            latency_ms=latency,
            packet_loss_pct=0 if ok else 100,
            jitter_ms=1,
            packets_sent=5,
            packets_recv=5 if ok else 0,
            ok=ok,
        )

    def test_rebuilds_hour_and_day_rollups_before_pruning_old_raw_samples(self):
        now = timezone.now().replace(minute=30, second=0, microsecond=0)
        self._sample('rollup-recent-1', now - timedelta(minutes=20), 10)
        self._sample('rollup-recent-2', now - timedelta(minutes=10), 20, ok=False)
        self._sample('rollup-old', now - timedelta(days=2), 30)

        result = rebuild_edge_quality_rollups(
            now=now,
            raw_retention_days=1,
        )

        self.assertGreaterEqual(result['hour_rollups'], 2)
        self.assertGreaterEqual(result['day_rollups'], 2)
        self.assertEqual(result['raw_pruned'], 1)
        recent_rollup = EdgeInterfaceQualityRollup.objects.get(
            edge=self.edge,
            bucket_kind=EdgeInterfaceQualityRollup.BucketKind.HOUR,
            bucket_ts=now.replace(minute=0),
        )
        self.assertEqual(recent_rollup.sample_count, 2)
        self.assertEqual(recent_rollup.ok_samples, 1)
        self.assertEqual(recent_rollup.latency_avg_ms, 15)
        self.assertEqual(recent_rollup.availability_pct, 50)
        self.assertFalse(
            EdgeInterfaceQualitySample.objects.filter(source_sample_id='rollup-old').exists(),
        )


class EdgeQualityTelemetryIngestTests(TestCase):
    """POP 专用令牌与边缘接口样本 ingest 的权限、绑定和幂等性。"""

    def setUp(self):
        region = Region.objects.create(code='telemetry-apac', name='遥测亚太')
        country = Country.objects.create(region=region, name='遥测国')
        city = City.objects.create(country=country, name='遥测市')
        self.pop = PopPoint.objects.create(name='遥测 POP', city=city)
        self.other_pop = PopPoint.objects.create(name='其他 POP', city=city)
        self.customer = Customer.objects.create(name='遥测客户', contact_name='遥测联系人')
        self.edge = EdgeAcceleration.objects.create(
            name='中心边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.pop,
            customer=self.customer,
            node_ifname='gre-telemetry-a',
        )
        self.foreign_edge = EdgeAcceleration.objects.create(
            name='异地边缘',
            protocol=EdgeAcceleration.Protocol.GRE,
            pop_point=self.other_pop,
            customer=self.customer,
            node_ifname='gre-telemetry-b',
        )
        self.admin = User.objects.create_superuser(
            username='telemetry-admin',
            email='telemetry-admin@example.test',
            password='test-password',
        )
        self.admin_client = APIClient()
        self.admin_client.force_authenticate(self.admin)
        self.ingest_client = APIClient()

    def _create_token(self):
        response = self.admin_client.post(
            reverse('pop-telemetry-credential-list'),
            {'pop_point': self.pop.pk},
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        token = response.data.get('token')
        self.assertTrue(token)
        credential = PopTelemetryCredential.objects.get(pop_point=self.pop)
        self.assertNotEqual(credential.token_hash, token)
        self.assertTrue(credential.rotated_at)
        self.assertNotIn('token_hash', response.data)
        return token

    def _sample(self, **overrides):
        sample = {
            'source_sample_id': 'sample-001',
            'interface_name': self.edge.node_ifname,
            'target_address': '198.51.100.20',
            'sampled_at': timezone.now().isoformat(),
            'latency_ms': 12.5,
            'packet_loss_pct': 0.0,
            'jitter_ms': 1.2,
            'packets_sent': 5,
            'packets_recv': 5,
            'ok': True,
            # 故意写错，中心必须按凭据 POP + interface 自行解析 edge。
            'central_edge_id': self.foreign_edge.pk,
        }
        sample.update(overrides)
        return sample

    def _ingest(self, token, samples):
        return self.ingest_client.post(
            reverse('edge-quality-ingest'),
            {'samples': samples},
            format='json',
            HTTP_X_POP_TELEMETRY_TOKEN=token,
        )

    def test_issuer_returns_raw_token_once_and_ingest_is_idempotent(self):
        token = self._create_token()
        credential = PopTelemetryCredential.objects.get(pop_point=self.pop)

        detail = self.admin_client.get(
            reverse('pop-telemetry-credential-detail', kwargs={'pk': credential.pk}),
        )
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertNotIn('token', detail.data)
        self.assertNotIn('token_hash', detail.data)

        payload = self._sample()
        first = self._ingest(token, [payload])
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['accepted'], 1)
        self.assertEqual(first.data['duplicates'], 0)
        sample = EdgeInterfaceQualitySample.objects.get()
        self.assertEqual(sample.pop_id, self.pop.pk)
        self.assertEqual(sample.edge_id, self.edge.pk)
        self.assertEqual(sample.customer_id, self.customer.pk)
        self.assertNotIn('central_edge_id', sample.raw['sample'])

        second = self._ingest(token, [payload])
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data['accepted'], 0)
        self.assertEqual(second.data['duplicates'], 1)
        self.assertEqual(EdgeInterfaceQualitySample.objects.count(), 1)

    def test_foreign_unknown_and_stale_samples_are_rejected(self):
        token = self._create_token()
        response = self._ingest(token, [
            self._sample(
                source_sample_id='foreign-pop',
                interface_name=self.foreign_edge.node_ifname,
            ),
            self._sample(
                source_sample_id='unknown-interface',
                interface_name='does-not-exist',
            ),
            self._sample(
                source_sample_id='too-old',
                sampled_at=(timezone.now() - timedelta(days=8)).isoformat(),
            ),
        ])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['accepted'], 0)
        self.assertEqual(response.data['duplicates'], 0)
        self.assertEqual(response.data['rejected'], 3)
        self.assertEqual(EdgeInterfaceQualitySample.objects.count(), 0)
        self.assertEqual(
            {item['reason'] for item in response.data['rejections']},
            {'unknown_interface', 'sampled_at_out_of_range'},
        )
        self.assertEqual(
            {item['source_sample_id'] for item in response.data['rejections']},
            {'foreign-pop', 'unknown-interface', 'too-old'},
        )

    def test_rotate_replaces_the_only_valid_token(self):
        old_token = self._create_token()
        credential = PopTelemetryCredential.objects.get(pop_point=self.pop)
        response = self.admin_client.post(
            reverse('pop-telemetry-credential-rotate', kwargs={'pk': credential.pk}),
            {},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        new_token = response.data.get('token')
        self.assertTrue(new_token)
        self.assertNotEqual(new_token, old_token)

        old_attempt = self._ingest(old_token, [self._sample()])
        self.assertEqual(old_attempt.status_code, 401)
        new_attempt = self._ingest(new_token, [self._sample()])
        self.assertEqual(new_attempt.status_code, 200, new_attempt.data)
        self.assertEqual(new_attempt.data['accepted'], 1)
