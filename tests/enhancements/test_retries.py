import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from lib.storage import Store
from lib.settings import load
from lib.api.dashboard import listing
from lib.scheduling.retirement import cleanup, restore


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.db')
        self.config = load()
        self.target = self.config['profiles']['connectivity']['fingerprint']
        self.now = time.time()
        self.url = 'http://8.8.8.8:80'
        self.store.ingest([{'proxy': self.url}])

    def tearDown(self):
        self.temp.cleanup()

    def record(self, state, at):
        self.store.record(self.url, 'connectivity', self.target, dict(state=state, checked_at=at,
            http_status=0, latency_ms=1, reason='test'), self.config)

    def row(self):
        return listing(self.store, self.config, {'state': ['all']})['items'][0]

    def test_cold_requires_failure_span_and_resets_on_success(self):
        for i in range(5):
            self.record('unreachable', self.now-25*3600+i*60)
        self.assertEqual(self.row()['retry_tier'], 'retry')
        self.record('unreachable', self.now-1)
        row = self.row()
        self.assertEqual(row['retry_tier'], 'cold')
        self.assertEqual(row['next_check']-row['checked_at'], 86400)
        self.record('available', self.now)
        self.assertEqual(self.row()['retry_tier'], 'healthy')

    def test_recent_success_gets_recovery_queue_and_stale_failure_cannot_change_it(self):
        self.record('available', self.now-100)
        self.record('unreachable', self.now-50)
        self.record('unreachable', self.now-10)
        self.assertEqual(self.row()['retry_tier'], 'recovery')
        self.assertLessEqual(self.row()['next_check']-self.row()['checked_at'], 900)
        self.record('unreachable', self.now-200)
        self.assertEqual(self.row()['retry_tier'], 'recovery')

    def test_auth_required_never_enters_cold_queue(self):
        self.record('auth_required', self.now-1)
        self.assertEqual(self.row()['retry_tier'], 'auth_required')
        self.assertEqual(self.store.due('connectivity', 'changed', 200, self.now+86400), [])

    def test_cold_candidates_cannot_borrow_entire_batch(self):
        with self.store.connect() as db:
            for i in range(1, 101):
                url = f'http://8.8.4.{i}:80'
                db.execute('INSERT INTO proxies(url,first_seen,last_seen) VALUES(?,?,?)', (url, self.now, self.now))
                db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (url, 'connectivity', 'unreachable', self.now-1, 0, self.now-1, 5, 0, 1, self.target, 'test'))
                db.execute('INSERT INTO retry_health VALUES(?,?,?,?,?,?,?)',
                    (url, 'connectivity', self.target, 0, self.now-86400, 5, 'cold'))
        picked = self.store.due('connectivity', self.target, 200, self.now)
        self.assertEqual(Counter(row['retry_tier'] for row in picked)['cold'], 10)

    def test_failed_queue_borrows_to_cap_and_reserves_cold_slots(self):
        with self.store.connect() as db:
            db.execute('DELETE FROM proxies')
            for tier in ('retry','cold'):
                for i in range(1,101):
                    url=f'http://{8 if tier=="retry" else 9}.8.4.{i}:80'
                    db.execute('INSERT INTO proxies(url,first_seen,last_seen) VALUES(?,?,?)',
                               (url,self.now,self.now))
                    db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (url,'connectivity','unreachable',self.now-1,0,self.now-1,
                                5,0,1,self.target,'test'))
                    db.execute('INSERT INTO retry_health VALUES(?,?,?,?,?,?,?)',
                               (url,'connectivity',self.target,0,self.now-86400,5,tier))
        picked=self.store.due('connectivity',self.target,200,self.now,
                              max_retry_ratio=.25)
        tiers=Counter(row['retry_tier'] for row in picked)
        self.assertEqual((len(picked),tiers['retry'],tiers['cold']),(50,40,10))

    def test_disappeared_failed_source_archives_without_recent_probe_and_restores(self):
        with self.store.connect() as db:
            db.execute('UPDATE proxies SET last_seen=?', (self.now-8*86400,))
        self.record('unreachable', self.now-2*86400)
        self.assertEqual(self.store.due('connectivity', self.target, 200), [])
        self.assertEqual(cleanup(self.store, self.now, True), 1)
        restore(self.store, self.url)
        self.assertEqual(len(self.store.due('connectivity', self.target, 200)), 1)
        self.assertEqual(self.store.available('connectivity', self.target), [])

    def test_disappeared_auth_required_source_is_retained(self):
        with self.store.connect() as db:
            db.execute('UPDATE proxies SET last_seen=?', (self.now-8*86400,))
        self.record('auth_required', self.now-86400)
        self.assertEqual(cleanup(self.store, self.now, True), 0)
