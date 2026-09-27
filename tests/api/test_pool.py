import json
import sys
import tempfile
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib import settings
from lib.storage import Store
from lib.api.exports import render
from lib.api.server import response, make_handler, reset_caches
from lib.api.runtime.cache import TTLCache
from pool import migrate


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.store = Store(self.path / 'pool.sqlite3')
        self.config = settings.load()
        self.target = self.config['profiles']['connectivity']['fingerprint']
        self.url = 'http://8.8.8.8:80'
        self.store.ingest([{'proxy': self.url}])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sqlite_connections_are_bounded_and_reused(self):
        def read(_):
            with self.store.connect() as db:
                time.sleep(.01)
                return db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]

        with ThreadPoolExecutor(max_workers=32) as clients:
            self.assertEqual(list(clients.map(read, range(96))), [1] * 96)
        self.assertEqual(self.store._connections.created, 16)
        for _ in range(100):
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT 1').fetchone()[0], 1)
        self.assertEqual(self.store._connections.created, 16)

    def test_sqlite_writes_are_serialized(self):
        def write(index):
            self.store.put_meta(f'concurrent:{index}', index)

        with ThreadPoolExecutor(max_workers=32) as clients:
            list(clients.map(write, range(96)))
        self.assertEqual(self.store.meta('concurrent:95'), 95)

    def test_cache_is_single_flight_and_bounded(self):
        cache = TTLCache(4)
        built = []

        def get(_):
            return cache.get('same', 60, lambda: built.append(1) or 'value')

        with ThreadPoolExecutor(max_workers=16) as clients:
            self.assertEqual(list(clients.map(get, range(32))), ['value'] * 32)
        self.assertEqual(len(built), 1)
        for index in range(10):
            cache.get(index, 60, lambda value=index: value)
        self.assertEqual(len(cache._values), 4)

    def record(self, state='available', checked=None, platform='connectivity'):
        self.store.record(self.url, platform, self.target,
                          {'state': state, 'checked_at': time.time() if checked is None else checked,
                           'http_status': 200, 'latency_ms': 100, 'reason': 'test'}, self.config)

    def test_expiry_and_sample_change(self):
        now = time.time()
        self.record(checked=now)
        self.assertEqual(len(self.store.available('connectivity', self.target)), 1)
        self.assertEqual(self.store.available('connectivity', 'changed'), [])
        self.assertEqual(self.store.available('connectivity', self.target, now + 3601), [])
        self.assertEqual(self.store.due('connectivity', self.target, 5, now + 599), [])
        self.assertEqual(len(self.store.due('connectivity', self.target, 5, now + 601)), 1)

    def test_failed_recheck_removes_available_immediately(self):
        self.record()
        self.record('unreachable')
        self.assertEqual(self.store.available('connectivity', self.target), [])
        row = self.store.records()[0]
        self.assertGreaterEqual(row['next_check'] - row['checked_at'], 300)

    def test_platforms_do_not_overwrite_and_cooldown_persists(self):
        self.record()
        self.record('unreachable', platform='another_platform')
        self.assertEqual(len(self.store.available('connectivity', self.target)), 1)
        self.assertEqual(self.store.available('another_platform', self.target), [])
        reopened = Store(self.path / 'pool.sqlite3')
        self.assertEqual(reopened.meta('pause:another_platform', 0), 0)
        self.assertEqual(reopened.due('another_platform', self.target, 5), [])
        self.assertEqual(len(reopened.due('another_platform', 'changed_target', 5)), 1)

    def test_collection_api_and_rules(self):
        self.record()
        self.assertEqual(response(self.store, self.config, '/next?purpose=browsing')[0], 200)
        self.assertEqual(response(self.store, self.config, '/next?platform=wechat')[0], 400)
        status, value, _ = response(self.store, self.config, '/next?profile=connectivity')
        self.assertEqual(status, 200)
        self.assertEqual(value['proxy'], self.url)
        self.assertEqual(response(self.store, self.config, '/../config.json')[0], 404)
        config = json.loads(render([], self.config['profiles']['connectivity'], 'clash', 'connectivity')[0])
        self.assertEqual(config['rules'][-1], 'MATCH,CONNECTIVITY')
        self.assertEqual(config['proxy-groups'][0]['proxies'], ['REJECT'])
        self.record('unreachable')
        reset_caches()
        self.assertEqual(response(self.store, self.config, '/next')[0], 503)

    def test_import_does_not_trust_legacy_business_state(self):
        old = self.path / 'old'
        old.mkdir()
        checked = time.time() - 2000
        (old / 'pool.json').write_text(json.dumps({'results': [
            {'proxy': self.url, 'business': 'ok', 'business_checked_at': checked}]}))
        migrate(self.store, self.config, old)
        self.assertEqual(self.store.records(), [])
        self.assertEqual(self.store.available('connectivity', self.target), [])

    def test_http_round_robin_ignores_non_pool_routes(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})

        def get(path):
            client = HTTPConnection(*server.server_address, timeout=5)
            try:
                client.request('GET', path)
                reply = client.getresponse()
                return reply.status, json.loads(reply.read())
            finally:
                client.close()

        with patch('lib.api.server.settings.load', return_value=self.config):
            thread.start()
            try:
                self.assertEqual(get('/next')[0], 503)
                self.record()
                other = 'http://1.1.1.1:80'
                self.store.ingest([{'proxy': other}])
                self.store.record(other, 'connectivity', self.target,
                                  dict(state='available', checked_at=time.time(), http_status=200,
                                       latency_ms=200, reason='test'), self.config)
                reset_caches()
                issued = []
                for route in ('/next', '/next.json', '/next', '/next.json'):
                    self.assertEqual(get('/next?purpose=browsing')[0], 200)
                    self.assertEqual(get('/next?platform=unknown')[0], 400)
                    self.assertEqual(get('/status.json')[0], 200)
                    self.assertEqual(get('/api/proxies')[0], 200)
                    self.assertEqual(get('/missing')[0], 404)
                    issued.append(get(route)[1]['proxy'])
                self.assertEqual(issued, [other, other, other, other])
                with ThreadPoolExecutor(max_workers=4) as clients:
                    concurrent = list(clients.map(get, ['/next'] * 12))
                self.assertTrue(all(status == 200 for status, _ in concurrent))
                self.assertEqual(sum(body['proxy'] == self.url for _, body in concurrent), 6)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == '__main__':
    unittest.main()
