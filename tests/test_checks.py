import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import checks, net, sources


class CheckTest(unittest.TestCase):
    def test_status_validation_has_no_business_semantics(self):
        result = {'status': 204, 'error': '', 'body': ''}
        self.assertEqual(checks.classify(result, 204)[0], 'available')
        result['status'] = 461
        self.assertEqual(checks.classify(result, 204)[0], 'unreachable')
        result.update(status=407, error='')
        self.assertEqual(checks.classify(result, 204)[0], 'auth_required')

    def test_region_uses_backup_after_primary_failure(self):
        entries = [{'url': 'https://one.test/generate_204', 'status': 204},
                   {'url': 'https://two.test/generate_204', 'status': 204}]
        replies = [dict(status=461, error='', body='', latency_ms=10),
                   dict(status=204, error='', body='', latency_ms=20)]
        with patch.object(checks, 'fetch', side_effect=replies) as fetch:
            result = checks.probe_region('http://8.8.8.8:80', entries)
        self.assertEqual((result['state'], result['endpoint']), ('available', entries[1]['url']))
        self.assertEqual(fetch.call_count, 2)

    def test_untrusted_proxy_addresses(self):
        for value in ('http://127.0.0.1:80', 'http://10.0.0.1:80', 'http://[::1]:80',
                      'http://169.254.169.254:80', 'http://example.org:80',
                      'http://u:p@8.8.8.8:80', 'http://8.8.8.8:80/path', 'http://224.0.0.1:80'):
            self.assertIsNone(net.normalize(value))
        self.assertEqual(net.normalize('socks5://8.8.8.8:1080'), 'socks5://8.8.8.8:1080')
        self.assertEqual(net.normalize('socks4://8.8.8.8:1080'), 'socks4://8.8.8.8:1080')

    def test_loopback_opt_in(self):
        # 默认仍拒绝回环；仅本地可信来源显式放行，私网地址无论如何都拒绝。
        self.assertEqual(net.normalize('http://127.0.0.1:25001', allow_loopback=True), 'http://127.0.0.1:25001')
        self.assertIsNone(net.normalize('http://10.0.0.1:80', allow_loopback=True))

    def test_curl_total_timeout_and_tls_and_no_environment_proxy(self):
        def run(*args, **kwargs):
            kwargs['stdout'].write(b'hello\n__STATUS__200')
            return SimpleNamespace(returncode=0)
        with patch.object(net.subprocess, 'run', side_effect=run) as mocked:
            with patch.dict('os.environ', {'HTTPS_PROXY': 'http://private:9000'}):
                result = net.fetch('https://example.org', 'socks5://8.8.8.8:1080')
        self.assertEqual(result['body'], 'hello')
        command = mocked.call_args.args[0]
        self.assertIn('--max-time', command)
        self.assertIn('socks5h://8.8.8.8:1080', command)
        self.assertNotIn('-k', command)
        self.assertNotIn('HTTPS_PROXY', mocked.call_args.kwargs['env'])

        with patch.object(net.subprocess, 'run', side_effect=run) as socks4:
            net.fetch('https://example.org', 'socks4://8.8.8.8:1080')
        self.assertIn('socks4a://8.8.8.8:1080', socks4.call_args.args[0])

    def test_protocol_prefix_and_global_source(self):
        self.assertEqual(list(sources.source_records('new-http', '8.8.8.8:80')), [('http://8.8.8.8:80', None)])
        body = json.dumps([{'protocol': 'socks5', 'host': '8.8.8.8', 'port': 1080,
                            'geolocation': {'country': {'iso_code': 'US'}}}])
        self.assertEqual(list(sources.source_records('json-api', body))[0][1], 'US')


if __name__ == '__main__':
    unittest.main()
