import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import settings, worker
from lib.storage import Store
from lib.scheduling import prefilter, dispatch


class SchedulingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'pool.sqlite3')
        self.config = settings.load()
        self.target = self.config['profiles']['connectivity']
        self.stopped = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def items(self, count):
        self.store.ingest([{'proxy': f'http://8.8.8.{i}:80'} for i in range(1, count+1)])
        return [(row, 'connectivity', self.target) for row in self.store.due('connectivity', self.target['fingerprint'], count)]

    def result(self, state='unreachable'):
        return dict(state=state, checked_at=time.time(), http_status=0, latency_ms=1, reason='test')

    def test_closed_ports_never_call_platform_or_become_available(self):
        self.items(12)
        with patch.object(prefilter, 'probe', side_effect=lambda *_: self.result()), \
                patch.object(worker.checks, 'probe_region') as checker:
            result = worker.cycle(self.store, self.config)
        checker.assert_not_called()
        self.assertEqual((result['tested'], result['prefilter_failed'], result['connectivity_checked']), (12,12,0))
        self.assertEqual(self.store.available('connectivity', self.target['fingerprint']), [])

    def test_open_ports_do_not_mark_success_and_preserve_order(self):
        items = self.items(6)
        with patch.object(prefilter, 'probe', return_value=None):
            ready, rejected = prefilter.screen(items, self.store, self.config, self.stopped)
        self.assertEqual(ready, items)
        self.assertEqual(rejected, 0)
        self.assertEqual(self.store.records(), [])

    def test_tcp_bound_and_skip_existing_healthy_nodes(self):
        items = self.items(8)
        self.config['prefilter_workers'] = 3
        active, peak = 0, 0
        lock = threading.Lock()
        def probe(*_):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(.01)
            with lock:
                active -= 1
            return None
        items[0][0]['state'] = 'available'
        items[1][0]['state'] = 'available'
        with patch.object(prefilter, 'probe', side_effect=probe) as checker:
            prefilter.screen(items, self.store, self.config, self.stopped)
        self.assertEqual(checker.call_count, 6)
        self.assertLessEqual(peak, 3)
        self.assertGreater(peak, 1)

    def test_explicit_platform_pause_stops_dispatch_before_next_request(self):
        items = self.items(4)
        self.config['probe_interval'] = .03
        def check(item):
            self.store.put_meta('pause:connectivity', time.time()+1800)
            return item[0]['url'], 'connectivity', 'unreachable'
        checker = Mock(side_effect=check)
        count = dispatch.run(items, self.store, self.config, self.stopped, checker)
        self.assertEqual(count, 1)
        self.assertEqual(checker.call_count, 1)

    def test_dispatch_spacing_and_stop(self):
        items, starts = self.items(3), []
        self.config['probe_interval'] = .04
        def check(item):
            starts.append(time.monotonic())
            return item[0]['url'], 'connectivity', 'unreachable'
        self.assertEqual(dispatch.run(items,self.store,self.config,self.stopped,check),3)
        self.assertTrue(all(b-a >= .035 for a,b in zip(starts,starts[1:])))
        self.stopped.set()
        checker = Mock()
        self.assertEqual(dispatch.run(items,self.store,self.config,self.stopped,checker),0)
        checker.assert_not_called()

    def test_prefilter_rejects_private_address_without_socket(self):
        with patch.object(prefilter.socket, 'create_connection') as connect:
            result = prefilter.probe('http://10.0.0.1:80',3,'example.com')
        connect.assert_not_called()
        self.assertEqual(result['state'], 'unreachable')

    def test_prefilter_allows_loopback_pool_nodes(self):
        # 本地 mihomo 出口等回环节点应进入 TCP 预筛，而非在地址校验处被拦截。
        with patch.object(prefilter.socket, 'create_connection', side_effect=OSError('refused')) as connect:
            result = prefilter.probe('http://127.0.0.1:80',3,'example.com')
        connect.assert_called_once()
        self.assertEqual(result['state'], 'unreachable')


if __name__ == '__main__':
    unittest.main()
