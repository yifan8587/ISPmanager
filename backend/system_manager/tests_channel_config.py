"""A3：通道层按环境变量在内存 / Redis 之间切换。"""
from django.test import SimpleTestCase

from backend.channel_config import (
    INMEMORY_BACKEND,
    REDIS_BACKEND,
    build_channel_layers,
    redis_connection_url,
)


class ChannelLayerConfigTests(SimpleTestCase):
    def test_default_is_inmemory(self):
        cfg = build_channel_layers({})
        self.assertEqual(cfg['default']['BACKEND'], INMEMORY_BACKEND)
        self.assertNotIn('CONFIG', cfg['default'])

    def test_channel_redis_url_selects_redis(self):
        cfg = build_channel_layers(
            {'CHANNEL_REDIS_URL': 'redis://cache:6379/2'},
            secret_key='test-secret',
        )
        self.assertEqual(cfg['default']['BACKEND'], REDIS_BACKEND)
        self.assertEqual(cfg['default']['CONFIG']['hosts'], ['redis://cache:6379/2'])
        self.assertEqual(cfg['default']['CONFIG']['prefix'], 'ispmanager')
        self.assertEqual(cfg['default']['CONFIG']['symmetric_encryption_keys'], ['test-secret'])

    def test_redis_url_fallback(self):
        url = redis_connection_url({'REDIS_URL': 'rediss://secure.example:6380/1'})
        self.assertEqual(url, 'rediss://secure.example:6380/1')
        cfg = build_channel_layers({'REDIS_URL': 'rediss://secure.example:6380/1'})
        self.assertEqual(cfg['default']['BACKEND'], REDIS_BACKEND)

    def test_memory_backend_overrides_url(self):
        cfg = build_channel_layers({
            'CHANNEL_LAYER_BACKEND': 'memory',
            'CHANNEL_REDIS_URL': 'redis://cache:6379/2',
        })
        self.assertEqual(cfg['default']['BACKEND'], INMEMORY_BACKEND)

    def test_explicit_redis_builds_url_from_host(self):
        cfg = build_channel_layers({
            'CHANNEL_LAYER_BACKEND': 'redis',
            'REDIS_HOST': '10.0.0.8',
            'REDIS_PORT': '6380',
            'REDIS_DB': '3',
            'REDIS_PASSWORD': 'p@ss/word',
            'CHANNEL_REDIS_PREFIX': 'isp-ws',
        })
        self.assertEqual(cfg['default']['BACKEND'], REDIS_BACKEND)
        self.assertEqual(
            cfg['default']['CONFIG']['hosts'],
            ['redis://:p%40ss%2Fword@10.0.0.8:6380/3'],
        )
        self.assertEqual(cfg['default']['CONFIG']['prefix'], 'isp-ws')
