from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from portal.monitoring import _fill_monitor_charts, _quality_from_node_points, quality_series


class NodeQualityMappingTests(SimpleTestCase):
    def test_maps_vpopmanager_points_into_portal_payload(self):
        edge = SimpleNamespace(pk=42)
        payload = _quality_from_node_points(edge, 24, {
            'status': 'ok',
            'points': [{
                'time': timezone.now().isoformat(),
                'latency_ms': 12.5,
                'packet_loss_pct': 0,
                'jitter_ms': 0.4,
                'ok': True,
            }],
        })
        self.assertEqual(payload['data_source'], 'vpop_node')
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['summary']['sample_count'], 1)
        self.assertEqual(payload['current']['latency_ms'], 12.5)
        self.assertEqual(payload['series'][0]['packet_loss_pct'], 0)

    def test_missing_node_target_is_not_configured(self):
        payload = _quality_from_node_points(SimpleNamespace(pk=1), 24, {
            'status': 'not_configured',
            'points': [],
        })
        self.assertEqual(payload['status'], 'not_configured')
        self.assertEqual(payload['summary']['sample_count'], 0)

    def test_monitor_charts_are_shown_when_quality_samples_are_empty(self):
        edge = SimpleNamespace(pk=7, node_ifname='gre7', pop_point=object())
        node = {
            'status': 'ok',
            'points': [],
            'latency_chart': {
                'bucket': 'hour',
                'target_name': 'isp-edge-quality-7',
                'target_address': '203.0.113.10',
                'points': [{
                    'bucket': '2026-10-07T12:00:00+00:00',
                    'rtt_avg_ms': 10,
                    'rtt_max_ms': 14,
                    'loss_pct': 0,
                }],
            },
            'traffic_chart': {
                'interface_name': 'gre7',
                'bucket': 'hour',
                'points': [{
                    'bucket': '2026-10-07T12:00:00+00:00',
                    'rx_bps_avg': 1_000_000,
                    'tx_bps_avg': 500_000,
                }],
            },
        }
        with patch('portal.monitoring._node_edge_quality', return_value=node):
            payload = quality_series(edge, hours=24)
        self.assertEqual(payload['status'], 'ok')
        self.assertEqual(payload['data_source'], 'vpop_node')
        self.assertEqual(payload['latency_chart']['target_name'], 'isp-edge-quality-7')
        self.assertEqual(len(payload['latency_chart']['points']), 1)
        self.assertEqual(payload['traffic_chart']['interface_name'], 'gre7')
        self.assertEqual(payload['traffic_chart']['points'][0]['rx_bps_avg'], 1_000_000)


class LegacyMonitorChartTests(SimpleTestCase):
    def test_missing_chart_fields_are_loaded_from_monitor_series(self):
        class Client:
            def monitor_targets(self):
                return [{
                    'id': 'target-1',
                    'name': 'isp-edge-quality-7',
                    'address': '203.0.113.10',
                    'source_interface': 'gre7',
                    'central_edge_id': 7,
                }]

            def monitor_target_series(self, target_id, bucket='hour', since=None, until=None):
                self.target_id = target_id
                self.bucket = bucket
                return {
                    'bucket': bucket,
                    'source': 'rollup',
                    'points': [{'bucket': since, 'rtt_avg_ms': 9, 'loss_pct': 0}],
                }

            def interface_traffic_series(self, interface_name, bucket='hour', since=None, until=None):
                self.interface_name = interface_name
                return {
                    'bucket': bucket,
                    'source': 'raw',
                    'interface_name': interface_name,
                    'points': [{'bucket': since, 'rx_bps_avg': 2_000_000, 'tx_bps_avg': 1_000_000}],
                }

        client = Client()
        edge = SimpleNamespace(pk=7, node_ifname='gre7')
        data = _fill_monitor_charts(client, edge, 24, {'status': 'no_data', 'points': []}, 'gre7')
        self.assertEqual(client.target_id, 'target-1')
        self.assertEqual(client.bucket, 'hour')
        self.assertEqual(client.interface_name, 'gre7')
        self.assertEqual(data['latency_chart']['points'][0]['rtt_avg_ms'], 9)
        self.assertEqual(data['traffic_chart']['points'][0]['rx_bps_avg'], 2_000_000)

    def test_existing_chart_fields_are_left_unchanged(self):
        class Client:
            def monitor_targets(self):
                raise AssertionError('should not list targets')

        edge = SimpleNamespace(pk=7, node_ifname='gre7')
        original = {
            'latency_chart': {'points': [{'rtt_avg_ms': 1}]},
            'traffic_chart': {'points': [{'rx_bps_avg': 2}]},
        }
        data = _fill_monitor_charts(Client(), edge, 24, original, 'gre7')
        self.assertEqual(data['latency_chart']['points'][0]['rtt_avg_ms'], 1)
        self.assertEqual(data['traffic_chart']['points'][0]['rx_bps_avg'], 2)

    def test_interface_name_selects_the_service_chart_on_pemanager_nodes(self):
        class Client:
            def monitor_targets(self):
                return [
                    {'id': 'other', 'name': 'manual', 'address': '198.51.100.1', 'source_interface': 'eth0'},
                    {
                        'id': 'edge-target',
                        'name': '客户链路',
                        'address': '203.0.113.10',
                        'source_interface': 'gre7',
                        'customer': 'C2026001',
                    },
                ]

            def monitor_target_series(self, target_id, bucket='hour', since=None, until=None):
                self.target_id = target_id
                return {'bucket': bucket, 'source': 'rollup', 'points': [{'bucket': since, 'rtt_avg_ms': 11, 'loss_pct': 0}]}

            def interface_traffic_series(self, interface_name, bucket='hour', since=None, until=None):
                return {
                    'interface': interface_name,
                    'bucket': bucket,
                    'source': 'raw',
                    'points': [{'bucket': since, 'rx_bps_avg': 3_000_000, 'tx_bps_avg': 1_000_000}],
                }

        edge = SimpleNamespace(pk=7, node_ifname='gre7', edge_no='EDGE7', customer=SimpleNamespace(customer_no='C2026001'))
        data = _fill_monitor_charts(
            Client(), edge, 24,
            {'status': 'no_data', 'points': [], 'latency_chart': {'points': []}, 'traffic_chart': {'points': []}},
            'gre7',
        )
        self.assertEqual(data['latency_chart']['target_name'], '客户链路')
        self.assertEqual(data['latency_chart']['points'][0]['rtt_avg_ms'], 11)
        self.assertEqual(data['traffic_chart']['interface_name'], 'gre7')
        self.assertEqual(data['traffic_chart']['points'][0]['rx_bps_avg'], 3_000_000)
