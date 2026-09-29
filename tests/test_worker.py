import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib import worker, settings
from lib.storage import Store


class NoWait:
    def is_set(self):
        return False

    def wait(self, seconds):
        return False


class WorkerTest(unittest.TestCase):
    def test_concurrency_and_failures_do_not_stop_other_nodes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            store.ingest([{'proxy': f'http://8.8.8.{i}:80'} for i in range(1, 9)])
            barrier = threading.Barrier(4)
            def check(*args):
                barrier.wait(timeout=3)
                return {'state': 'unreachable', 'reason': 'test', 'endpoint': 'test',
                        'http_status': 461, 'latency_ms': 1}
            config = settings.load()
            config['workers'] = 4
            config['probe_interval'] = 0  # 无需等待真实发起间隔，独立测试四槽位及冷却。
            with patch.object(worker.prefilter, 'probe', return_value=None), \
                    patch.object(worker.checks, 'probe_region', side_effect=check) as checker:
                result = worker.cycle(store, config, NoWait())
            self.assertEqual(result['tested'], 8)
            targets = config['profiles']['connectivity']['targets']
            sites = min(worker.checks.MAX_SITES, sum(map(len, targets.values())))
            self.assertEqual(checker.call_count, 8 * sites)
            with patch.object(worker.checks, 'probe_region') as checker:
                result = worker.cycle(Store(store.path), settings.load(), NoWait())
            self.assertEqual(result['tested'], 0)
            checker.assert_not_called()

    def test_failed_sources_keep_existing_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            store.ingest([{'proxy': 'http://8.8.8.8:80'}])
            with patch.object(worker.sources, 'collect', return_value=([], [{'error': 'timeout'}])):
                worker.collect(store)
            self.assertEqual(store.status(settings.load())['candidates'], 1)

    def test_collection_error_clears_after_success_and_history_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            job = worker.scheduled_jobs(store)['source_collection']
            with patch.object(worker.sources, 'collect', side_effect=[ValueError('test'), ([], [])]):
                with self.assertRaises(ValueError):
                    job(settings.load())
                failed = store.status(settings.load())
                self.assertEqual(failed['errors']['collection'], 'ValueError')
                self.assertIsNone(failed['last_errors']['collection']['recovered_at'])
                job(settings.load())
            self.assertIsNone(store.status(settings.load())['errors']['collection'])
            history = store.status(settings.load())['last_errors']['collection']
            self.assertEqual(history['error'], 'ValueError')
            self.assertGreaterEqual(history['recovered_at'], history['time'])
            self.assertEqual(Store(store.path).status(settings.load())['last_errors']['collection'], history)

    def test_check_and_export_errors_clear_only_after_successful_iteration(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            stopped = Mock()
            stopped.is_set.side_effect = [False, False, True]
            observed = []
            stopped.wait.side_effect = lambda _: observed.append(store.status(settings.load()))
            with patch.object(worker.threading, 'Thread'), patch.object(
                    worker, 'cycle', side_effect=[ValueError('test'), None]):
                worker.maintain(store, stopped)
            self.assertEqual(observed[0]['errors']['checks'], 'ValueError')
            self.assertIsNone(observed[1]['errors']['checks'])
            history = observed[1]['last_errors']['checks']
            self.assertEqual(history['error'], 'ValueError')
            self.assertGreaterEqual(history['recovered_at'], history['time'])

    def test_legacy_error_recovers_without_inventing_failure_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            store.put_meta('collection_error', 'ValueError')
            with patch.object(worker.sources, 'collect', return_value=([], [])):
                worker.collect(store)
            status = store.status(settings.load())
            self.assertIsNone(status['errors']['collection'])
            self.assertEqual(status['last_errors']['collection']['error'], 'ValueError')
            self.assertIsNone(status['last_errors']['collection']['time'])
            self.assertIsNotNone(status['last_errors']['collection']['recovered_at'])

    def test_collection_job_is_independent_from_previous_refresh_time(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'pool.sqlite3')
            store.put_meta('last_collection', time.time())
            store.put_meta('collection_error', 'ValueError')
            with patch.object(worker.sources, 'collect', return_value=([], [])) as collect:
                worker.scheduled_jobs(store)['source_collection'](settings.load())
            collect.assert_called_once()
            self.assertIsNone(store.status(settings.load())['errors']['collection'])


if __name__ == '__main__':
    unittest.main()
