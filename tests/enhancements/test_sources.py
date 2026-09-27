import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import sources, settings
from lib.collection import transport, quality
from lib.storage import Store


class SourceTests(unittest.TestCase):
    def result(self, status=200, error='', body='http://8.8.8.8:80'):
        return dict(status=status, error=error, body=body, latency_ms=1)

    def test_fallback_preserves_repository_path_and_is_preferred_next_time(self):
        url = 'https://raw.githubusercontent.com/example-org/proxy-list/main/http.txt'
        with patch.object(transport, 'fetch', side_effect=[self.result(0, 'curl_28'), self.result()]) as fetch:
            result = transport.download(url)
        self.assertEqual(result['fetch_url'], 'https://cdn.jsdelivr.net/gh/example-org/proxy-list@main/http.txt')
        self.assertEqual(fetch.call_count, 2)
        with patch.object(transport, 'fetch', return_value=self.result()) as fetch:
            transport.download(url, result)
        self.assertEqual(fetch.call_args.args[0], result['fetch_url'])

    def test_429_is_failure_and_cools_down_without_trying_other_endpoint(self):
        url = 'https://raw.githubusercontent.com/example/list/main/http.txt'
        with patch.object(transport, 'fetch', return_value=self.result(429)) as fetch:
            result = transport.download(url)
        fetch.assert_called_once()
        self.assertEqual(result['error'], 'HTTP 429')
        self.assertGreaterEqual(result['next_fetch']-result['checked_at'], 1800)

    def test_source_cooldown_survives_via_reports_and_streams_completed_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'sources.json').write_text(json.dumps({'first-http': 'https://example.com/first',
                                                         'second-http': 'https://example.com/second'}))
            batches = []
            previous = [dict(name='first-http', next_fetch=time.time()+3600, http_status=429,
                             error='HTTP 429', accepted=0, credentials_found=4, credentials_added=4)]
            with patch.object(sources, 'ROOT', root), patch.object(sources, 'fetch_source', return_value=self.result()) as fetch:
                rows, reports = sources.collect(previous, lambda batch: batches.extend(batch))
            fetch.assert_called_once()
            self.assertEqual(len(rows), 1)
            self.assertEqual(batches[0]['proxy'], 'http://8.8.8.8:80')
            self.assertTrue(reports[0]['deferred'])
            self.assertNotIn('credentials_found', reports[0])
            self.assertNotIn('credentials_added', reports[0])

    def test_ingest_preserves_source_attribution_and_quality_counts_actual_success(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.db')
            url = 'http://8.8.8.8:80'
            for name in ('first', 'second', 'first'):
                store.ingest([{'proxy': url, 'sources': [name]}])
            config = settings.load()
            store.record(url, 'connectivity', config['profiles']['connectivity']['fingerprint'],
                dict(state='available', checked_at=time.time(), http_status=200, latency_ms=1, reason='test'), config)
            reports = quality.enrich(store, [{'name': 'first'}, {'name': 'second'}], config)
            self.assertTrue(all(row['stored_candidates'] == row['available'] == row['verified_24h'] == 1 for row in reports))
            with store.connect() as db:
                self.assertEqual(set(json.loads(db.execute('SELECT sources FROM proxies').fetchone()[0])), {'first', 'second'})
