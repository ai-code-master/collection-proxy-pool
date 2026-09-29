import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib.probing.identity import parse_trace, probe, public_ip
from lib import checks, settings, worker
from lib.storage import Store


class IdentityTest(unittest.TestCase):
    def test_parses_stable_cloudflare_fields(self):
        value = parse_trace('''ip=2606:4700:4700::1111
loc=US
colo=LAX
http=http/2
tls=TLSv1.3
warp=off
uag=noise
ts=123''', provider='cloudflare')
        self.assertEqual(value['exit_ip'], '2606:4700:4700::1111')
        self.assertEqual(value['country'], 'US')
        self.assertEqual(value['exit_info'], {'provider': 'cloudflare', 'colo': 'LAX',
                                              'http': 'http/2', 'tls': 'TLSv1.3',
                                              'warp': 'off'})

    def test_rejects_non_public_or_invalid_ip(self):
        self.assertEqual(public_ip('8.8.8.8'), '8.8.8.8')
        self.assertEqual(public_ip('127.0.0.1'), '')
        self.assertIsNone(parse_trace('ip=not-an-ip\nloc=US'))

    def test_ipify_fallback_keeps_unknown_country(self):
        replies = iter([{'status': 500, 'error': '', 'body': ''},
                        {'status': 200, 'error': '', 'body': '1.1.1.1'}])
        targets = [{'url': 'https://trace.test', 'format': 'trace'},
                   {'url': 'https://ip.test', 'format': 'ip'}]
        value = probe('http://8.8.8.8:80', targets, fetcher=lambda *a, **k: next(replies))
        self.assertEqual(value, {'exit_ip': '1.1.1.1', 'country': 'unknown',
                                 'exit_info': {'provider': 'ip.test'}})

    def test_connectivity_check_collects_identity_after_success(self):
        capability = {'state': 'available', 'reason': 'HTTP 204', 'http_status': 204,
                      'latency_ms': 10, 'endpoint': 'https://example.test'}
        identity = {'exit_ip': '1.1.1.1', 'country': 'US', 'exit_info': {}}
        with patch.object(checks, 'probe_region', return_value=capability), \
                patch.object(checks, 'probe_identity', return_value=identity) as detect:
            result = checks.check('http://8.8.8.8:80', 'connectivity',
                                  {'targets': {'domestic': [{'url': 'https://one.test', 'status': 204}],
                                               'overseas': []},
                                   'identity_targets': []})
        self.assertEqual(result['identity'], identity)
        detect.assert_called_once()

    def test_backfill_persists_identity_and_counts_unique_ips(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            config = settings.load()
            target = config['profiles']['connectivity']['fingerprint']
            for proxy in ('http://8.8.8.8:80', 'http://1.1.1.1:80'):
                store.ingest([{'proxy': proxy}])
                store.record(proxy, 'connectivity', target,
                             {'state': 'available', 'checked_at': time.time(),
                              'http_status': 200, 'latency_ms': 1, 'reason': 'test'}, config)
            value = {'exit_ip': '9.9.9.9', 'country': 'US',
                     'exit_info': {'provider': 'test'}}
            summary = worker.identify(store, config, lambda *_: value)
            self.assertEqual((summary['identified_routes'], summary['unique_exit_ips']), (2, 1))
            self.assertTrue(all(row['exit_ip'] == '9.9.9.9' for row in store.records(config)))


if __name__ == '__main__':
    unittest.main()
