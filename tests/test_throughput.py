import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib import settings, worker
from lib.storage import Store
from lib.scheduling import dispatch


class ThroughputTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'pool.sqlite3')
        self.config=settings.load()
        self.config['probe_interval']=0
        self.target=self.config['profiles']['connectivity']
        self.stopped=threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def items(self,count):
        self.store.ingest([{'proxy':f'http://8.8.4.{i}:80'} for i in range(1,count+1)])
        return [(r,'connectivity',self.target) for r in self.store.due('connectivity',self.target['fingerprint'],count)]

    def test_twelve_slots_used_without_exceeding_limit(self):
        self.config['workers']=12
        barrier=threading.Barrier(12)
        lock=threading.Lock()
        active=peak=0
        def check(item):
            nonlocal active,peak
            with lock:
                active+=1
                peak=max(peak,active)
            barrier.wait(timeout=5)
            with lock:
                active-=1
            return item[0]['url'],'connectivity','unreachable'
        count=dispatch.run(self.items(24),self.store,self.config,self.stopped,check)
        self.assertEqual(count,24)
        self.assertEqual(peak,12)

    def test_available_result_is_recorded(self):
        self.items(6)
        def check(*_):
            return dict(state='available',http_status=200,checked_at=time.time(),
                        latency_ms=1,reason='matched_product_id')
        with patch.object(worker.prefilter,'probe',return_value=None), \
                patch.object(worker.checks,'check',side_effect=check):
            result=worker.cycle(self.store,self.config)
        self.assertEqual(result['connectivity_checked'],6)
        self.assertEqual(len(self.store.available('connectivity',self.target['fingerprint'])),6)


    def test_business_starts_before_slowest_prefilter_finishes(self):
        self.items(3)
        business_started=threading.Event()
        delayed=[]
        def probe(url,*_):
            if url!='http://8.8.4.1:80' and not business_started.wait(timeout=2):
                delayed.append(url)
            return None
        def check(*_):
            business_started.set()
            return dict(state='available',http_status=200,checked_at=time.time(),
                        latency_ms=1,reason='test')
        with patch.object(worker.prefilter,'probe',side_effect=probe), \
                patch.object(worker.checks,'check',side_effect=check):
            result=worker.cycle(self.store,self.config)
        self.assertEqual(result['connectivity_checked'],3)
        self.assertEqual(delayed,[])

    def test_fresh_result_while_queued_skips_duplicate_probe(self):
        self.items(1)
        def screen(items,store,config,stopped,on_ready):
            row,platform,target=items[0]
            store.record(row['url'],platform,target['fingerprint'],
                         dict(state='available',http_status=200,checked_at=time.time(),
                              latency_ms=1,reason='concurrent_probe'),config)
            on_ready(items[0])
            return items,0
        with patch.object(worker.prefilter,'screen',side_effect=screen), \
                patch.object(worker.checks,'check') as checker:
            result=worker.cycle(self.store,self.config)
        checker.assert_not_called()
        self.assertEqual(result['connectivity_checked'],0)
        self.assertEqual(len(self.store.available('connectivity',self.target['fingerprint'])),1)

    def test_inflight_success_does_not_erase_newer_failure(self):
        self.items(1)
        def check(url,platform,target):
            self.store.record(url,platform,target['fingerprint'],
                              dict(state='unreachable',http_status=0,checked_at=time.time(),
                                   latency_ms=1,reason='concurrent_probe'),self.config)
            return dict(state='available',http_status=200,checked_at=time.time(),
                        latency_ms=1,reason='test')
        with patch.object(worker.prefilter,'probe',return_value=None), \
                patch.object(worker.checks,'check',side_effect=check):
            worker.cycle(self.store,self.config)
        self.assertEqual(self.store.records()[0]['state'],'unreachable')


if __name__=='__main__':
    unittest.main()
