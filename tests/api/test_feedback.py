import json
import sys
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib import settings
from lib.api.server import make_handler
from lib.storage import Store


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.db')
        self.config = settings.load()
        self.proxy = 'http://8.8.8.8:80'
        target = self.config['profiles']['connectivity']['fingerprint']
        self.store.ingest([{'proxy': self.proxy}])
        self.store.record(self.proxy, 'connectivity', target,
                          dict(state='available', checked_at=time.time(), http_status=204,
                               latency_ms=1, reason='domestic'), self.config)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_business_feedback_endpoint_is_gone_and_cannot_change_state(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        before = self.store.records(self.config)
        with patch('lib.api.server.settings.load', return_value=self.config):
            thread.start()
            try:
                client = HTTPConnection(*server.server_address, timeout=5)
                body = json.dumps({'outcome': 'restricted', 'http_status': 461})
                client.request('POST', '/api/feedback', body, {'Content-Type': 'application/json'})
                reply = client.getresponse()
                self.assertEqual(reply.status, 410)
                self.assertEqual(json.loads(reply.read())['error'], 'business_feedback_not_supported')
                client.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
        self.assertEqual(self.store.records(self.config), before)


if __name__ == '__main__':
    unittest.main()
