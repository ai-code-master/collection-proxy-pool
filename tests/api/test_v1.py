import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

from lib import settings
from lib.api import v1
from lib.storage import Store
from mihomo import runtime


class V1ApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.sqlite3')
        self.config = settings.load()
        self.config['lan_proxy_host'] = '192.168.1.9'
        self.target = self.config['profiles']['connectivity']['fingerprint']
        self.proxy = 'http://127.0.0.1:25001'
        self.store.ingest([{'proxy': self.proxy}])
        self.store.record(self.proxy, 'connectivity', self.target,
                          {'state': 'available', 'checked_at': time.time(),
                           'http_status': 204, 'latency_ms': 120, 'reason': 'test'},
                          self.config)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rows(self, store, config, target, reach):
        fingerprint = config['profiles'][target]['fingerprint']
        return store.available(target, fingerprint, reach=reach)

    def call(self, path):
        return v1.response(self.store, self.config, path, self.rows)

    def test_random_defaults_to_directly_usable_lan_address(self):
        status, body, kind = self.call('/api/v1/proxies/random')
        self.assertEqual((status, kind), (200, 'json'))
        self.assertEqual(body['proxy'], 'http://192.168.1.9:25001')
        self.assertEqual((body['scheme'], body['host'], body['port']),
                         ('http', '192.168.1.9', 25001))
        self.assertTrue(body['checked_at'].endswith('Z'))

    def test_auto_host_uses_the_address_called_by_client(self):
        self.config['lan_proxy_host'] = 'auto'
        host = v1.advertised_host(self.config, '100.64.0.10:21992')
        result = v1.response(self.store, self.config, '/api/v1/proxies/random',
                             self.rows, lan_host=host)
        self.assertEqual(result[1]['proxy'], 'http://100.64.0.10:25001')

    def test_list_is_paginated_and_supports_text(self):
        status, body, _ = self.call('/api/v1/proxies?limit=1')
        self.assertEqual((status, body['count'], len(body['results'])), (200, 1, 1))
        status, body, kind = self.call('/api/v1/proxies?format=txt')
        self.assertEqual((status, kind), (200, 'text/plain; charset=utf-8'))
        self.assertEqual(body, 'http://192.168.1.9:25001\n')

    def test_validation_and_stats(self):
        self.assertEqual(self.call('/api/v1/proxies?limit=0')[0], 400)
        self.assertEqual(self.call('/api/v1/proxies?protocol=ftp')[0], 400)
        self.assertEqual(self.call('/api/v1/proxies?target=business')[0], 400)
        status, body, _ = self.call('/api/v1/stats')
        self.assertEqual((status, body['api_version']), (200, 'v1'))
        self.assertEqual(body['available'], 1)

    def test_mihomo_config_exposes_one_configurable_port_per_node(self):
        with patch.object(runtime, 'BASE_PORT', 31001):
            config, _ = runtime.build_config([
                {'type': 'socks5', 'server': '8.8.8.8', 'port': 1080},
                {'type': 'http', 'server': '1.1.1.1', 'port': 8080},
            ])
        listeners = {row['name']: row for row in config['listeners']}
        self.assertNotIn('rotate-local', listeners)
        self.assertNotIn('rotate-lan', listeners)
        self.assertEqual(listeners['lan-31001']['listen'], '0.0.0.0')
        self.assertEqual({row['port'] for row in config['listeners']}, {31001, 31002})
        self.assertNotIn('ROTATE', {row['name'] for row in config['proxy-groups']})

    def test_legacy_rotating_gateway_can_be_removed_without_touching_lanes(self):
        config = {'listeners': [{'name': 'lan-25001'}, {'name': 'rotate-lan'}],
                  'proxy-groups': [{'name': 'LANE-25001'}, {'name': 'ROTATE'}],
                  'rules': ['IN-NAME,lan-25001,LANE-25001', 'IN-NAME,rotate-lan,ROTATE']}
        cleaned = runtime.without_rotating_gateway(config)
        self.assertEqual(cleaned['listeners'], [{'name': 'lan-25001'}])
        self.assertEqual(cleaned['proxy-groups'], [{'name': 'LANE-25001'}])
        self.assertEqual(cleaned['rules'], ['IN-NAME,lan-25001,LANE-25001'])


if __name__ == '__main__':
    unittest.main()
