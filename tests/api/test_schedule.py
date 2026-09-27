import json
import tempfile
import time
import unittest
import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib import settings
from lib.storage import Store
from lib.tasks import state
from lib.tasks.discovery import run as discover
from lib.api.server import make_handler


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.store = Store(self.folder / 'pool.sqlite3')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_tasks_are_persistent_and_claimed_once(self):
        config = settings.load()
        state.sync(self.store, config, now=100)
        self.assertTrue(state.claim(self.store, 'source_discovery', now=100))
        self.assertFalse(state.claim(self.store, 'source_discovery', now=100))
        state.finish(self.store, 'source_discovery', {'new': 2}, now=110)
        value = state.snapshot(self.store, config)
        task = next(row for row in value['tasks'] if row['name'] == 'source_discovery')
        self.assertEqual(task['result'], {'new': 2})
        self.assertEqual(task['next_run'], 3700)

    def test_discovery_only_enters_candidate_store(self):
        rows = [{'name': 'owner-repo-http', 'url': 'https://example.test/http.txt',
                 'repository': 'owner/repo', 'path': 'http.txt'}]
        with patch('lib.tasks.discovery.discover', return_value=rows):
            self.assertEqual(discover(self.store, settings.load())['new'], 1)
            self.assertEqual(discover(self.store, settings.load())['new'], 0)
        value = state.snapshot(self.store, settings.load())
        self.assertEqual(value['source_candidates']['pending'], 1)

    def test_schedule_update_is_atomic_and_validated(self):
        source = Path(__file__).resolve().parents[1] / 'fixtures/config.json'
        target = self.folder / 'config.json'
        target.write_text(source.read_text())
        updated = settings.update_schedule(
            {'discovery_interval': 7200, 'discovery_enabled': False}, target)
        self.assertEqual(updated['discovery_interval'], 7200)
        self.assertFalse(json.loads(target.read_text())['discovery_enabled'])
        before = target.read_text()
        with self.assertRaises(ValueError):
            settings.update_schedule({'discovery_interval': 60}, target)
        self.assertEqual(target.read_text(), before)

    def test_snapshot_exposes_hourly_discovery_default(self):
        value = state.snapshot(self.store, settings.load())
        self.assertEqual(value['settings']['discovery_interval'], 3600)
        self.assertTrue(value['settings']['discovery_enabled'])
        self.assertGreater(value['tasks'][0]['next_run'], time.time() - 2)

    def test_web_api_updates_schedule_without_restart(self):
        source = Path(__file__).resolve().parents[1] / 'fixtures/config.json'
        target = self.folder / 'config.json'
        target.write_text(source.read_text())
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        body = json.dumps({'discovery_interval': 7200})
        with patch.object(settings, 'CONFIG', target):
            thread.start()
            try:
                client = HTTPConnection(*server.server_address, timeout=5)
                client.request('POST', '/api/schedule', body,
                               {'Content-Type': 'application/json'})
                reply = client.getresponse()
                value = json.loads(reply.read())
                client.close()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
        self.assertEqual(reply.status, 200)
        self.assertEqual(value['settings']['discovery_interval'], 7200)
        self.assertEqual(settings.load(target)['discovery_interval'], 7200)


if __name__ == '__main__':
    unittest.main()
