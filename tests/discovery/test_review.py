import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.discovery.review import _sample, review
from scripts.lib import settings
from scripts.lib.storage import Store
from scripts.lib.tasks.review import run as review_sources


def response(body):
    return {'status': 200, 'error': '', 'body': body, 'fetch_url': 'https://cdn.test/list',
            'http_status': 200, 'failures': 0, 'next_fetch': 0}


class SourceReviewTest(unittest.TestCase):
    def setUp(self):
        self.config = settings.load(Path(__file__).resolve().parents[1] / 'fixtures/config.json')
        self.config.update(source_review_min_records=20, source_review_min_novel=10,
                           source_review_sample_size=4, source_review_workers=2,
                           source_review_min_success=1)
        self.row = {'name': 'owner-repo-http', 'url': 'https://example.test/http.txt',
                    'path': 'http.txt', 'discoveries': 2, 'review': '{}'}
        self.body = '\n'.join(f'8.8.8.8:{port}' for port in range(1000, 1030))

    def test_approves_rediscovered_live_source(self):
        checker = lambda *_: {'state': 'available'}
        state, detail = review(self.row, self.config, set(), set(),
                               downloader=lambda *_: response(self.body), checker=checker)
        self.assertEqual(state, 'approved')
        self.assertEqual(detail['novel'], 30)
        self.assertEqual(detail['sample_available'], 4)
        self.assertTrue(detail['source_name'].endswith('-http'))

    def test_sampling_spreads_across_large_source(self):
        seen = []
        checker = lambda url, *_: seen.append(url) or {'state': 'unreachable'}
        _sample({f'http://8.8.8.8:{port}' for port in range(1000, 1100)},
                {'enabled': True}, 4, 1, checker)
        ports = sorted(int(url.rsplit(':', 1)[1]) for url in seen)
        self.assertGreater(ports[-1] - ports[0], 50)

    def test_waits_for_second_discovery(self):
        self.row['discoveries'] = 1
        state, detail = review(self.row, self.config, set(), set(),
                               downloader=lambda *_: response(self.body))
        self.assertEqual((state, detail['reason']), ('pending', 'awaiting_rediscovery'))

    def test_routes_socks4_to_direct_pool(self):
        self.row['path'] = 'socks4.txt'
        body = '\n'.join(f'socks4://8.8.8.8:{port}' for port in range(1000, 1030))
        checker = lambda *_: {'state': 'available'}
        state, detail = review(self.row, self.config, set(), set(),
                               downloader=lambda *_: response(body), checker=checker)
        self.assertEqual((state, detail['route'], detail['protocol']),
                         ('approved', 'direct', 'socks4'))

    def test_routes_vless_subscription_to_mihomo(self):
        self.row['path'] = 'clash.yaml'
        body = '\n'.join(
            f'vless://00000000-0000-0000-0000-{port:012d}@8.8.8.8:{port}#node-{port}'
            for port in range(1000, 1030))
        state, detail = review(self.row, self.config, set(), set(),
                               downloader=lambda *_: response(body))
        self.assertEqual((state, detail['route']), ('approved', 'mihomo'))
        self.assertEqual(detail['records'], 30)

    def test_preserves_catalog_permissions(self):
        from scripts.discovery.sources import write_catalog
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sources.json'
            path.write_text('{}')
            path.chmod(0o600)
            write_catalog(path, [{'name': 'approved-http', 'url': 'https://example.test/list'}])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_task_auto_adds_approved_source(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Path(folder) / 'sources.json'
            catalog.write_text('{}')
            store = Store(Path(folder) / 'pool.sqlite3')
            now = 1000
            with store.write() as db:
                db.execute('''INSERT INTO source_candidates(url,name,repository,path,
                    first_seen,last_seen,discoveries) VALUES(?,?,?,?,?,?,?)''',
                           (self.row['url'], self.row['name'], 'owner/repo',
                            self.row['path'], now, now, 2))
            outcome = ('approved', {'source_name': 'owner-repo-http', 'route': 'direct',
                       'reason': 'quality_gate_passed', 'records': 30, 'novel': 30})
            with patch.object(settings, 'SOURCES', catalog), \
                    patch('scripts.lib.tasks.review.review', return_value=outcome):
                result = review_sources(store, self.config)
            self.assertEqual(result['added'], 1)
            with store.connect() as db:
                self.assertEqual(db.execute(
                    'SELECT state FROM source_candidates').fetchone()[0], 'active')
            self.assertIn('owner-repo-http', catalog.read_text())
            store.close()


if __name__ == '__main__':
    unittest.main()
