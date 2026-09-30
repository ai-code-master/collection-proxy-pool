import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from lib import net, settings, sources
from lib.api import server
from lib.api.server import make_handler, response
from lib.storage import Store


class AnonymousOnlyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / 'pool.db')
        self.config = settings.load()
        self.target = self.config['profiles']['connectivity']['fingerprint']

    def record(self, url, state):
        self.store.ingest([{'proxy': url}])
        self.store.record(url, 'connectivity', self.target, dict(state=state, checked_at=time.time(),
                          http_status=407 if state == 'auth_required' else 200,
                          latency_ms=1, reason='test'), self.config)

    def test_requires_auth_is_removed_and_permanently_blocked(self):
        url = 'http://8.8.8.8:80'
        self.record(url, 'auth_required')
        self.assertFalse(self.store.needs_check(url, 'connectivity', self.target))
        self.assertEqual(self.store.due('connectivity', self.target, 10), [])
        self.assertEqual(self.store.status(self.config)['candidates'], 0)
        self.assertEqual(self.store.ingest([{'proxy': url}]), 0)
        self.assertEqual(response(self.store, self.config, '/next')[0], 503)
        self.assertEqual(response(self.store, self.config, '/proxies.txt')[1], '')
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                'SELECT COUNT(*) FROM authenticated_proxies WHERE url=?', (url,)).fetchone()[0], 1)
        self.record('http://8.8.4.4:80', 'available')
        server.reset_caches()
        self.assertEqual(response(self.store, self.config, '/next')[1]['proxy'], 'http://8.8.4.4:80')

    def test_legacy_authenticated_success_is_disabled_on_upgrade(self):
        url = 'http://8.8.8.8:80'
        self.record(url, 'available')
        with self.store.connect() as db:
            db.execute("DELETE FROM meta WHERE key='credentials_removed_v1'")
            db.execute("DELETE FROM meta WHERE key='auth_endpoints_removed_v2'")
            db.execute('INSERT INTO authenticated_proxies VALUES(?,?,1)', (url, 'old-hash'))
        store = Store(self.store.path)
        self.assertEqual(response(store, self.config, '/next')[0], 503)
        self.assertEqual(store.due('connectivity', self.target, 10), [])
        self.assertEqual(store.records(self.config), [])
        self.assertEqual(store.status(self.config)['candidates'], 0)
        with store.connect() as db:
            self.assertEqual(db.execute('SELECT enabled FROM authenticated_proxies').fetchone()[0], 0)

    def test_removed_routes_and_asset_return_404(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        try:
            for path in ['/api/auth/next', '/api/auth/credentials']:
                client = HTTPConnection(*server.server_address, timeout=5)
                try:
                    client.request('POST', path, headers={'X-Collection-Client': 'collection-proxy-pool'})
                    reply = client.getresponse()
                    self.assertEqual(reply.status, 404)
                    reply.read()
                finally:
                    client.close()
            self.assertEqual(response(self.store, self.config, '/assets/auth/credentials.js')[0], 404)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_source_credentials_are_skipped_without_stripping_into_anonymous_nodes(self):
        examples = {
            'sample-http': 'http://u:p@8.8.8.8:80\n8.8.8.8:80:u:p\n8.8.4.4:80',
            'proxy-api': json.dumps([{'proxy': 'http://8.8.8.8:80', 'username': 'u', 'password': 'p'},
                                    {'proxy': 'http://8.8.8.8:80', 'username': 'u'},
                                    {'proxy': 'http://8.8.4.4:80', 'username': None, 'password': None}]),
        }
        for name, body in examples.items():
            (self.root / 'sources.json').write_text(json.dumps({name:'https://example.com/list'}))
            with patch.object(sources, 'ROOT', self.root), patch.object(sources, 'fetch_source',
                       return_value=dict(status=200, error='', body=body)):
                rows, reports = sources.collect()
            self.assertEqual([row['proxy'] for row in rows], ['http://8.8.4.4:80'])
            self.assertFalse(any(key.startswith('credentials_') for key in reports[0]))
        self.assertFalse((self.root / 'private').exists())

    def test_network_client_rejects_credential_url(self):
        with patch.object(net.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                net.fetch('https://example.com', 'http://u:p@8.8.8.8:80')
        run.assert_not_called()
