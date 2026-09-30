import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from lib import settings
from lib.collection import admission
from lib.storage import Store


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.db')
        self.config = settings.load()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_public_candidates_require_prefilter_and_rejections_cool_down(self):
        good, bad = 'http://8.8.8.8:80', 'http://1.1.1.1:80'
        rows = [dict(proxy=good, sources=['one']), dict(proxy=bad, sources=['one'])]
        self.assertEqual(admission.enqueue(self.store, rows), 2)
        unreachable = dict(state='unreachable', reason='tcp_prefilter:TimeoutError')
        hosts = []
        def probe(url, _timeout, host):
            hosts.append(host)
            return None if url == good else unreachable
        with patch.object(admission, 'probe', side_effect=probe):
            result = admission.promote(self.store, self.config, threading.Event())
        self.assertEqual((result['promoted'], result['rejected']), (1, 1))
        expected = urlsplit(self.config['profiles']['connectivity']['targets']
                            ['domestic'][0]['url']).hostname
        self.assertEqual(set(hosts), {expected})
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidate_rejections').fetchone()[0], 1)
        self.assertEqual(admission.enqueue(self.store, [dict(proxy=bad, sources=['two'])]), 0)

    def test_loopback_source_bypasses_public_candidate_queue(self):
        local = 'http://127.0.0.1:25029'
        self.assertEqual(admission.enqueue(self.store, [dict(proxy=local, sources=['mihomo'])]), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0], 0)

    def test_auth_candidate_is_permanently_rejected(self):
        url = 'http://8.8.8.8:80'
        self.assertEqual(admission.enqueue(self.store, [dict(proxy=url)]), 1)
        result = dict(state='auth_required', reason='proxy_authentication_required')
        with patch.object(admission, 'probe', return_value=result):
            self.assertEqual(admission.promote(
                self.store, self.config, threading.Event())['rejected'], 1)
        self.assertEqual(admission.enqueue(self.store, [dict(proxy=url)]), 0)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candidate_rejections').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM authenticated_proxies').fetchone()[0], 1)
