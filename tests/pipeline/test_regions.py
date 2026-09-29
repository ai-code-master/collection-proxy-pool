import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib import checks, settings
from lib.scheduling.stages import regions
from lib.storage import Store


class RegionPipelineTest(unittest.TestCase):
    def test_unified_https_result_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'pool.sqlite3')
            config = settings.load()
            target = config['profiles']['connectivity']
            target['identity_targets'] = []
            store.ingest([{'proxy': 'http://8.8.8.8:80'}])
            row = store.due('connectivity', target['fingerprint'], 1)[0]
            feed = queue.Queue()
            feed.put((row, 'connectivity', target))
            feed.put(None)
            result = dict(state='available', reason='test', http_status=204,
                          latency_ms=1, checked_at=time.time())
            plan = dict(probe_workers=1, max_inflight=4,
                        launch_interval=0, reason='test')
            with patch.object(checks, 'check', return_value=result) as checker:
                outcome = regions.run(feed, store, config, threading.Event(), plan)
            checker.assert_called_once()
            self.assertEqual(outcome['checked'], 1)
            self.assertEqual(len(store.available('connectivity', target['fingerprint'])), 1)


if __name__ == '__main__':
    unittest.main()
