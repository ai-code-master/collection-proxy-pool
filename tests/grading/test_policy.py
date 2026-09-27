import tempfile
import time
import unittest
from pathlib import Path
from collections import Counter
from lib.storage import Store
from lib.settings import load
from lib.api.dashboard import listing


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'pool.db')
        self.config=load()
        self.target=self.config['profiles']['connectivity']['fingerprint']
        self.now=time.time()

    def tearDown(self):
        self.temp.cleanup()

    def record(self,url,state,at,target=None):
        return self.store.record(url,'connectivity',target or self.target,
            dict(state=state,checked_at=at,http_status=200 if state=='available' else 461,
                 latency_ms=1,reason='test'),self.config)

    def test_grade_counts_spaced_successes_and_resets(self):
        url='http://8.8.8.8:80'
        self.store.ingest([{'proxy':url}])
        self.record(url,'available',self.now-1000)
        self.record(url,'available',self.now-999)
        self.record(url,'available',self.now-998)
        row=listing(self.store,self.config,{'state':['all']})['items'][0]
        self.assertEqual((row['grade'],row['success_streak']),('B',1))
        self.assertEqual(row['next_check']-row['checked_at'],600)
        self.record(url,'available',self.now-600)
        self.record(url,'available',self.now-200)
        row=listing(self.store,self.config,{'grade':['A']})['items'][0]
        self.assertEqual(row['next_check']-row['checked_at'],1800)
        self.record(url,'unreachable',self.now-90)
        self.assertEqual(listing(self.store,self.config,{'state':['all']})['items'][0]['grade'],'E')
        self.record(url,'available',self.now-80,'changed')
        self.assertEqual(listing(self.store,self.config,{'state':['all']})['items'][0]['grade'],'D')

    def test_stale_result_cannot_promote_or_demote_grade(self):
        url='http://8.8.8.8:80'
        self.store.ingest([{'proxy':url}])
        self.record(url,'available',self.now-600)
        self.record(url,'available',self.now-300)
        self.record(url,'available',self.now)
        self.assertFalse(self.record(url,'unreachable',self.now-1))
        self.assertEqual(listing(self.store,self.config,{'state':['all']})['items'][0]['grade'],'A')

    def test_all_four_queues_get_quota_and_spare_capacity_is_reused(self):
        with self.store.connect() as db:
            for grade in 'ABDE':
                for i in range(1,131):
                    url=f'http://8.{ord(grade)}.0.{i}:80'
                    db.execute('INSERT INTO proxies(url,first_seen,last_seen) VALUES(?,?,?)',(url,self.now,self.now))
                    if grade=='D':continue
                    state={'A':'available','B':'available','E':'unreachable'}[grade]
                    db.execute('INSERT INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (url,'connectivity',state,self.now-2000,0,self.now-1,0,200,1,self.target,'test'))
                    db.execute('INSERT INTO reliability VALUES(?,?,?,?,?)',
                               (url,'connectivity',self.target,3 if grade=='A' else 1,self.now-2000))
        selected=self.store.due('connectivity',self.target,200,quotas=self.config['grade_quotas'])
        self.assertEqual(Counter(r['grade'] for r in selected),dict(A=30,B=30,D=110,E=30))
        self.assertEqual([r['grade'] for r in selected[:4]],list('ABDE'))
        with self.store.connect() as db:
            db.execute("UPDATE checks SET next_check=? WHERE state!='unreachable'",(self.now+5000,))
        selected=self.store.due('connectivity',self.target,200)
        self.assertEqual(len(selected),200)
        self.assertEqual(Counter(r['grade'] for r in selected),dict(D=130,E=70))

    def test_upgrade_seeds_only_one_success_and_preserves_failure(self):
        url='http://8.8.8.8:80'
        self.store.ingest([{'proxy':url}])
        self.record(url,'unreachable',self.now)
        before=self.store.records()[0]['next_check']
        with self.store.connect() as db:
            db.execute("DELETE FROM meta WHERE key='grading_v1'")
            db.execute('DELETE FROM reliability')
        reopened=Store(self.store.path)
        self.assertEqual(reopened.records()[0]['next_check'],before)
        self.assertEqual(listing(reopened,self.config,{'state':['all']})['items'][0]['grade'],'E')
