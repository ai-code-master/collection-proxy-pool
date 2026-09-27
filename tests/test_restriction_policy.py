import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.api.dashboard import listing
from lib.settings import load
from lib.storage import Store


class ConnectivityPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.sqlite3')
        self.config = load()
        self.target = self.config['profiles']['connectivity']['fingerprint']
        self.url = 'http://8.8.8.8:80'
        self.store.ingest([{'proxy': self.url}])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_region_capabilities_are_independent(self):
        now = time.time()
        capabilities = {
            'domestic': dict(state='available', reason='HTTP 204', http_status=204,
                             latency_ms=10, endpoint='https://domestic.test/generate_204'),
            'overseas': dict(state='unreachable', reason='timeout', http_status=0,
                             latency_ms=80, endpoint='https://overseas.test/generate_204'),
        }
        self.store.record(self.url, 'connectivity', self.target,
                          dict(state='available', checked_at=now, http_status=204,
                               latency_ms=10, reason='domestic', capabilities=capabilities), self.config)
        self.assertEqual(len(self.store.available('connectivity', self.target, reach='any')), 1)
        self.assertEqual(len(self.store.available('connectivity', self.target, reach='domestic')), 1)
        self.assertEqual(self.store.available('connectivity', self.target, reach='overseas'), [])
        self.assertEqual(self.store.available('connectivity', self.target, reach='both'), [])

    def test_dashboard_has_no_restricted_filter(self):
        with self.assertRaisesRegex(ValueError, '未知状态'):
            listing(self.store, self.config, {'state': ['restricted']})

    def test_legacy_business_rows_are_not_part_of_active_pool(self):
        now = time.time()
        with self.store.write() as db:
            db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (self.url, 'business', 'restricted', now, 0, now + 1800, 1, 461, 1, 'old', 'legacy'))
        self.assertEqual(self.store.records(self.config), [])
        self.assertEqual(self.store.status(self.config)['marks'], [])


if __name__ == '__main__':
    unittest.main()
