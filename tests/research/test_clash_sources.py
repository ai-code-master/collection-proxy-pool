import base64
import json
import sys
import tempfile
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault('yaml', SimpleNamespace(
    YAMLError=ValueError, safe_load=lambda _: {}, safe_dump=lambda *_args, **_kwargs: ''))
from mihomo.refresh import priority_source, select_nodes
from mihomo.quality import record_quality
from mihomo.runtime import drop_invalid_proxy
from mihomo.source_io.download import retry_url
from mihomo.sources import download, fetch_bodies, parse_proxies, sane, source_label
from mihomo.uris import parse_subscription

CANDIDATE_SPEC = spec_from_file_location(
    'mihomo_candidates', SCRIPTS / 'research' / 'mihomo_candidates.py')
CANDIDATE_MODULE = module_from_spec(CANDIDATE_SPEC)
CANDIDATE_SPEC.loader.exec_module(CANDIDATE_MODULE)
sample_rows = CANDIDATE_MODULE.sample_rows
class ClashSourceTest(unittest.TestCase):
    def test_reality_short_id_must_be_even_hex(self):
        base = {'type': 'vless', 'server': 'example.com', 'port': 443,
                'reality-opts': {'public-key': 'A' * 43}}
        self.assertTrue(sane({**base, 'reality-opts': {**base['reality-opts'], 'short-id': '01ab'}}))
        for value in ('xyz', '123', '0' * 18):
            self.assertFalse(sane({**base, 'reality-opts': {
                **base['reality-opts'], 'short-id': value}}))

    def test_pretested_source_moves_to_queue_front(self):
        old = {'old': {'row': {'__source': 'old'}, 'seen': 1}}
        nodes = {'normal': {'__source': 'other'},
                 'priority-a': {'__source': 'owner/priority-a'},
                 'priority-b': {'__source': 'owner/priority-b'}}
        with patch('mihomo.refresh.PRIORITY_SOURCES',
                   {'owner/priority-a', 'owner/priority-b'}):
            _, _, batch, _ = select_nodes(nodes, {}, {}, old, 3)
        self.assertEqual(batch[:3], ['priority-a', 'priority-b', 'old'])

    def test_duplicate_repository_urls_have_distinct_quality_labels(self):
        url = 'https://cdn.example.test/gh/owner/repo@main/lists/proxy-2.yaml'
        with patch('mihomo.sources.SOURCES', [url,
                   'https://cdn.example.test/gh/owner/repo@main/lists/proxy-1.yaml']):
            label = source_label(url)
        self.assertEqual(label, 'owner/repo#lists/proxy-2.yaml')
        with patch('mihomo.refresh.PRIORITY_SOURCES', {'owner/repo'}):
            self.assertTrue(priority_source(label))

    def test_raw_github_source_uses_repository_label(self):
        label = source_label(
            'https://raw.githubusercontent.com/owner/repo/HEAD/lists/merged.yaml')
        self.assertEqual(label, 'owner/repo')
        with patch('mihomo.refresh.PRIORITY_SOURCES', {'owner/repo'}):
            self.assertTrue(priority_source(label))

    def test_mihomo_validation_can_drop_one_bad_proxy(self):
        config = {'proxies': [{'name': 'one', 'server': 'bad.test', 'port': 443},
                              {'name': 'two', 'server': 'ok.test', 'port': 443}],
                  'proxy-groups': [{'name': 'LANE-1', 'proxies': ['one']},
                                   {'name': 'LANE-2', 'proxies': ['two']}],
                  'listeners': [{'name': 'lane-1'}, {'name': 'lan-1'}, {'name': 'lane-2'}],
                  'rules': ['IN-NAME,lane-1,LANE-1', 'IN-NAME,lan-1,LANE-1',
                            'IN-NAME,lane-2,LANE-2', 'MATCH,DIRECT']}
        removed = drop_invalid_proxy(config, 'proxy 0: ss bad.test:443 cipher key error')
        self.assertEqual(removed, 'one')
        self.assertEqual([row['name'] for row in config['proxies']], ['two'])
        self.assertEqual([row['name'] for row in config['listeners']], ['lane-2'])
        self.assertEqual(config['rules'], ['IN-NAME,lane-2,LANE-2', 'MATCH,DIRECT'])

    def test_uri_and_base64_subscriptions_are_parsed(self):
        vmess = {'add': 'vmess.test', 'port': '443', 'id': 'uuid', 'aid': '0',
                 'ps': 'vmess', 'net': 'ws', 'path': '/ws', 'host': 'cdn.test', 'tls': 'tls'}
        encoded = base64.b64encode(json.dumps(vmess).encode()).decode().rstrip('=')
        vless = ('vless://id@vless.test:443?security=reality&type=ws&pbk='
                 + 'A' * 43 + '&sid=01&path=%2Fx&host=cdn.test#vless')
        payload = f'vmess://{encoded}\n{vless}\n'
        body = base64.b64encode(payload.encode())
        rows = parse_subscription(body)
        self.assertEqual([row['type'] for row in rows], ['vmess', 'vless'])
        self.assertEqual(rows[0]['ws-opts']['headers']['Host'], 'cdn.test')
        self.assertEqual(rows[1]['reality-opts']['short-id'], '01')

    def test_plain_multiline_uri_falls_back_after_yaml_error(self):
        cipher = base64.b64encode(b'aes-128-gcm:secret').decode().rstrip('=')
        body = f'ss://{cipher}@one.test:443#one\nss://{cipher}@two.test:8443#two\n'
        failure = lambda _: (_ for _ in ()).throw(ValueError('yaml'))
        with patch.object(sys.modules['yaml'], 'safe_load', failure):
            rows = parse_proxies(body)
        self.assertEqual([(row['server'], row['port']) for row in rows],
                         [('one.test', 443), ('two.test', 8443)])

    def test_empty_yaml_proxy_list_falls_back_without_crashing(self):
        with patch.object(sys.modules['yaml'], 'safe_load',
                          return_value={'proxies': None}):
            self.assertEqual(parse_proxies(b'proxies:'), [])

    def test_candidate_sampling_supports_offset(self):
        valid = {str(i): {'name': str(i)} for i in range(6)}
        seen = {'1'}
        rows, unseen = sample_rows(valid, seen, 'source', limit=2, offset=2)
        self.assertEqual(unseen, 5)
        self.assertEqual([row['name'] for row in rows], ['3', '4'])
        self.assertEqual({row['__source'] for row in rows}, {'source'})
        self.assertTrue({'1', '3', '4'} <= seen)

    def test_source_quality_keeps_history_and_error_streak(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            record_quality(base, {'source': '10 parsed, 4 added'}, {'source': 2}, 1,
                           {'source': 2})
            result = record_quality(
                base, {'source': 'error: HTTP 404'}, {}, 2)
        source = result['sources']['source']
        self.assertEqual(len(source['events']), 2)
        self.assertEqual(source['error_streak'], 1)
        self.assertEqual(source['alive_latest'], 0)
        self.assertEqual(source['alive_peak'], 2)
        self.assertEqual(source['exit_ips_peak'], 2)

    def test_parallel_source_fetch_isolates_failure_and_keeps_results(self):
        def fake_download(url, timeout=30):
            if url == 'bad':
                raise OSError('unavailable')
            return url.encode()

        with patch('mihomo.source_io.download.download', fake_download):
            bodies, errors = fetch_bodies(['one', 'bad', 'two'])
        self.assertEqual(bodies, {'one': b'one', 'two': b'two'})
        self.assertEqual(errors, {'bad': 'error: OSError unavailable'})

    def test_parallel_source_fetch_retries_transient_failure(self):
        attempts = {}

        def flaky_download(url, timeout=30):
            attempts[url] = attempts.get(url, 0) + 1
            if attempts[url] == 1:
                raise OSError('reset')
            return b'recovered'

        with patch('mihomo.source_io.download.download', flaky_download):
            bodies, errors = fetch_bodies(['flaky'])
        self.assertEqual(bodies, {'flaky': b'recovered'})
        self.assertEqual(errors, {})

    def test_raw_github_retry_uses_cdn_mirror(self):
        source = 'https://raw.githubusercontent.com/owner/repo/main/path/sub.yaml'
        self.assertEqual(retry_url(source),
                         'https://cdn.jsdelivr.net/gh/owner/repo@main/path/sub.yaml')

    def test_download_enforces_https_and_total_timeout(self):
        result = SimpleNamespace(returncode=0, stdout=b'ok', stderr=b'')
        with patch('mihomo.source_io.download.subprocess.run', return_value=result) as run:
            self.assertEqual(download('https://example.test/sub', timeout=7), b'ok')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--proto') + 1], '=https')
        self.assertEqual(command[command.index('--max-time') + 1], '7')
        self.assertEqual(run.call_args.kwargs['timeout'], 12)

    def test_sources_receive_bounded_total_timeout(self):
        seen = {}

        def capture(url, timeout=30):
            seen[url] = timeout
            return b'ok'

        urls = ['https://cdn.example.test/gh/owner/large@sha/nodes.yaml',
                'https://mirror.example.test/list.yaml',
                'https://cdn.example.test/gh/owner/medium@main/all.yaml',
                'https://example.test/small.yaml']
        with patch('mihomo.source_io.download.download', capture):
            fetch_bodies(urls)
        self.assertEqual([seen[url] for url in urls], [60, 60, 60, 60])


if __name__ == '__main__':
    unittest.main()
