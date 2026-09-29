import tempfile
import time
import unittest
from pathlib import Path
from lib.storage import Store
from lib.settings import load
from lib.scheduling.retirement import cleanup,restore


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'pool.db')
        self.config=load()
        self.target=self.config['profiles']['connectivity']['fingerprint']
        self.now=time.time()
        self.url='http://8.8.8.8:80'
        self.store.ingest([{'proxy':self.url}])

    def tearDown(self):
        self.temp.cleanup()

    def record(self,state,at):
        self.store.record(self.url,'connectivity',self.target,
                          dict(state=state,checked_at=at,http_status=0,latency_ms=3,reason='test'),self.config)

    def failing(self,count=10,days=8):
        for i in range(count):
            self.record('unreachable',self.now-days*86400+i*(days*86400-1)/max(1,count-1))

    def test_long_failure_archives_suppresses_and_can_restore(self):
        self.failing()
        self.assertEqual(cleanup(self.store,self.now,True),1)
        self.assertEqual(self.store.status(self.config)['candidates'],0)
        self.store.ingest([{'proxy':self.url}],seen=self.now+1)
        self.assertEqual(self.store.status(self.config)['candidates'],0)
        restore(self.store,self.url)
        self.assertEqual(self.store.status(self.config)['candidates'],1)
        self.assertEqual(self.store.available('connectivity',self.target),[])
        self.assertEqual(len(self.store.due('connectivity',self.target,5)),1)

    def test_recent_failure_or_too_few_observations_are_retained(self):
        self.failing(count=9)
        self.assertEqual(cleanup(self.store,self.now,True),0)
        self.record('available',self.now)
        for i in range(11):self.record('unreachable',self.now+86400+i*60)
        self.assertEqual(cleanup(self.store,self.now+2*86400,True),0)

    def test_historically_successful_proxy_is_never_auto_archived(self):
        self.record('available',self.now-9*86400)
        self.failing()
        self.assertEqual(cleanup(self.store,self.now,True),0)
        with self.store.write() as db:
            db.execute('UPDATE proxies SET last_seen=?',(self.now-8*86400,))
        self.assertEqual(cleanup(self.store,self.now,True),0)

    def test_auth_required_interrupts_failure_period(self):
        self.failing()
        self.record('auth_required',self.now+1)
        self.assertEqual(cleanup(self.store,self.now,True),0)
        self.record('unreachable',self.now+2)
        self.assertEqual(cleanup(self.store,self.now,True),0)
        self.assertEqual(self.store.status(self.config)['candidates'],1)

    def test_old_burst_without_week_of_observation_is_retained(self):
        for i in range(10):
            self.record('unreachable',self.now-8*86400+i*60)
        self.assertEqual(cleanup(self.store,self.now,True),0)

    def test_source_can_reintroduce_after_thirty_days(self):
        self.failing()
        cleanup(self.store,self.now,True)
        self.store.ingest([{'proxy':self.url}],seen=self.now+31*86400)
        self.assertEqual(self.store.status(self.config)['candidates'],1)
        self.assertEqual(self.store.records(),[])
