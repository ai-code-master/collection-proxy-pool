import base64
import sys
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib.settings import load
from lib.storage import Store
from lib.api.server import response
from lib.api import server
from lib.api.exports import export


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.sqlite3')
        self.config = load()
        self.target = self.config['profiles']['connectivity']['fingerprint']
        self.url = 'http://8.8.8.8:80'
        self.store.ingest([{'proxy': self.url, 'source_country': 'US'},
                           {'proxy': 'socks5://1.1.1.1:1080'}])

    def tearDown(self):
        self.temp.cleanup()

    def record(self, state, checked=None):
        self.store.record(self.url, 'connectivity', self.target,
                          {'state': state, 'checked_at': checked or time.time(),
                           'http_status': 200, 'latency_ms': 100, 'reason': '<script>test</script>'}, self.config)

    def get(self, path):
        status, value, _ = response(self.store, self.config, path)
        self.assertEqual(status, 200)
        return value

    def test_filters_pagination_country_and_unknown_history(self):
        data = self.get('/api/proxies?state=all&country=US')
        self.assertEqual(data['total'], 1)
        self.assertIsNone(data['items'][0]['last_success'])
        self.assertEqual(data['items'][0]['effective_state'], 'untested')
        self.assertEqual(self.get('/api/proxies?state=all&protocol=socks5')['total'], 1)
        self.assertEqual(self.get('/api/proxies?state=all&search=%27%20OR%201%3D1')['total'], 0)
        self.store.ingest([{'proxy': f'http://8.8.4.{i}:80'} for i in range(1, 65)])
        page = self.get('/api/proxies?state=all&page=999')
        self.assertEqual((page['page'], page['total'], len(page['items'])), (3, 66, 6))

    def test_transition_times_and_target_isolation(self):
        now = time.time()
        self.record('available', now-20)
        self.record('unreachable', now-10)
        row = self.get('/api/proxies?state=unavailable')['items'][0]
        self.assertEqual(row['last_success'], now-20)
        self.assertEqual(row['last_failure'], now-10)
        detail = self.get('/api/history?proxy='+self.url)
        self.assertEqual([e['state'] for e in detail['events']], ['unreachable','available'])
        Store(self.store.path)
        self.assertEqual(len(self.get('/api/history?proxy='+self.url)['events']), 2)
        self.config['profiles']['connectivity']['fingerprint'] = 'changed'
        self.assertEqual(self.get('/api/history?proxy='+self.url)['events'], [])
        self.assertEqual(self.get('/api/proxies?state=unverified')['total'], 1)

    def test_expiry_disabled_and_overview(self):
        self.record('available', time.time()-4000)
        self.assertEqual(self.get('/api/overview')['states']['expired'], 1)
        self.record('available')
        server.reset_caches()
        self.assertEqual(self.get('/api/overview')['median_ms'], 100)
        self.config['profiles']['connectivity']['enabled'] = False
        server.reset_caches()
        self.assertEqual(self.get('/api/proxies')['total'], 0)
        self.assertEqual(self.get('/api/proxies?state=disabled')['total'], 1)

    def test_overview_reports_live_source_contribution(self):
        self.store.ingest([{'proxy': self.url, 'sources': ['first']},
                           {'proxy': 'socks5://1.1.1.1:1080', 'sources': ['second']}])
        self.record('available')
        self.store.put_meta('sources', [
            {'name': 'first', 'http_status': 200, 'error': ''},
            {'name': 'second', 'http_status': 500, 'error': 'HTTP 500'}])
        server.reset_caches()
        status = self.get('/api/overview')['status']
        self.assertEqual(status['source_summary'], {'total': 2, 'healthy': 1, 'contributing': 1})
        first = next(row for row in status['sources'] if row['name'] == 'first')
        self.assertEqual((first['stored_candidates'], first['available'],
                          first['contribution_percent']), (1, 1, 100.0))

    def test_legacy_seed_once_and_retention(self):
        self.record('available')
        with self.store.connect() as db:
            db.execute('DELETE FROM check_events')
            db.execute("DELETE FROM meta WHERE key='history_started'")
        Store(self.store.path)
        Store(self.store.path)
        events = self.get('/api/history?proxy='+self.url)['events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['origin'], 'legacy_latest')
        with self.store.connect() as db:
            db.execute('UPDATE check_events SET checked_at=?', (time.time()-15*86400,))
            db.execute("DELETE FROM meta WHERE key='history_pruned'")
        self.record('unreachable')
        self.assertEqual(len(self.get('/api/history?proxy='+self.url)['events']), 1)

    def test_static_and_invalid_queries(self):
        self.assertIn('采集代理池', self.get('/'))
        self.assertIn('refresh', self.get('/assets/app.js'))
        self.assertIn('禁止用途', self.get('/disclaimer'))
        self.assertEqual(response(self.store,self.config,'/assets/../config.json')[0],404)
        for path in ('/api/proxies?page=oops','/api/proxies?state=oops',
                     '/api/proxies?project=oops','/api/history?proxy=oops'):
            self.assertEqual(response(self.store,self.config,path)[0],400)

    def test_marks_exports_dashboard_and_pick_share_validity_policy(self):
        now = time.time()
        self.record('available', now-1)
        cases = [
            ('available', self.target, True, now-1, now+1800, 'available'),
            ('available', 'changed', True, now-1, now+1800, 'unverified'),
            ('available', self.target, False, now-1, now+1800, 'disabled'),
            ('available', self.target, True, now-1800, now, 'expired'),
            ('available', self.target, True, now+1, now+1800, 'unverified'),
            ('unreachable', self.target, True, now-1, 0, 'unreachable'),
        ]
        folder = Path(self.temp.name) / 'exports'
        for state, target, enabled, checked, expires, expected in cases:
            with self.subTest(expected=expected), patch('time.time', return_value=now):
                self.config['profiles']['connectivity'].update(fingerprint=target, enabled=enabled)
                with self.store.connect() as db:
                    db.execute('UPDATE checks SET state=?,checked_at=?,valid_until=? WHERE url=?',
                               (state, checked, expires, self.url))
                server.reset_caches()
                marks = json.loads(self.get('/marks.json'))
                self.assertEqual(marks[0]['effective_state'], expected)
                self.assertEqual(marks[0]['target'], self.target)
                self.assertEqual(self.get('/api/history?proxy='+self.url)['proxy']['effective_state'], expected)
                self.assertEqual(self.get('/api/overview')['states'][expected], 1)
                self.assertEqual(self.get('/api/proxies')['total'], int(expected == 'available'))
                self.assertEqual(response(self.store, self.config, '/next')[0],
                                 200 if expected == 'available' else 503)
                export(self.store, self.config, folder)
                self.assertEqual(json.loads((folder / 'marks.json').read_text()), marks)
                self.assertEqual(bool((folder / 'connectivity/proxies.txt').read_text()), expected == 'available')

    def test_uri_and_base64_subscription_exports(self):
        self.record('available')
        server.reset_caches()
        uri = self.get('/connectivity/proxies.uri')
        encoded = self.get('/connectivity/proxies.base64')
        self.assertEqual(uri, self.url + '\n')
        self.assertEqual(base64.b64decode(encoded).decode(), uri)
        self.assertEqual(self.get('/proxies.uri'), uri)
        folder = Path(self.temp.name) / 'exports'
        export(self.store, self.config, folder)
        self.assertEqual((folder / 'connectivity/proxies.uri').read_text(), uri)
        self.assertEqual(base64.b64decode(
            (folder / 'connectivity/proxies.base64').read_text()).decode(), uri)

    def test_clash_exit_status_field(self):
        from lib.storage import clash_exit
        self.assertEqual(clash_exit(Path(self.temp.name)), {})
        folder = Path(self.temp.name) / 'clash-exit'
        folder.mkdir()
        (folder / 'refresh_meta.json').write_text(json.dumps(
            {'refreshed_at': 123, 'nodes': 3000, 'ports': [25001, 28000],
             'alive': {'total': 42, 'reserve': 10, 'unique_exit_ips': 31,
                       'median_ms': 880}}))
        self.assertEqual(clash_exit(folder)['alive'], 42)
        self.assertEqual(clash_exit(folder)['unique_exit_ips'], 31)
        self.assertIn('clash_exit', self.get('/status.json'))


if __name__ == '__main__':
    unittest.main()
