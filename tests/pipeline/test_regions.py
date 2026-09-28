import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib import checks, settings
from lib.scheduling.stages import regions
from lib.storage import Store


class RegionPipelineTest(unittest.TestCase):
    def test_domestic_and_overseas_start_in_parallel(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'pool.sqlite3')
            config = settings.load()
            target = config['profiles']['connectivity']
            target['identity_targets'] = []
            store.ingest([{'proxy': 'http://8.8.8.8:80'}])
            row = store.due('connectivity', target['fingerprint'], 1)[0]
            feed, barrier = queue.Queue(), threading.Barrier(2)
            feed.put((row, 'connectivity', target))
            feed.put(None)
            capability = dict(state='available', reason='test', http_status=204,
                              latency_ms=1, endpoint='https://example.test')

            def probe(*_):
                barrier.wait(timeout=2)
                return capability

            plan = dict(region_workers=1, identity_workers=1, max_inflight=4,
                        launch_interval=0, reason='test')
            with patch.object(checks, 'probe_region', side_effect=probe):
                result = regions.run(feed, store, config, threading.Event(), plan)
            self.assertEqual(result['checked'], 1)
            self.assertEqual(len(store.available('connectivity', target['fingerprint'])), 1)


if __name__ == '__main__':
    unittest.main()
