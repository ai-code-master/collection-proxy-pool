"""公开代理先进入有界候选区，通过廉价协议预筛后才进入主池。"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..net import is_loopback_url
from ..checks import primary_host
from ..scheduling.prefilter import probe


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS candidate_queue(
            url TEXT PRIMARY KEY,first_seen REAL,last_seen REAL,sources TEXT,
            source_country TEXT,next_check REAL) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS candidate_due ON candidate_queue(next_check,first_seen);
        CREATE TABLE IF NOT EXISTS candidate_rejections(
            url TEXT PRIMARY KEY,rejected_at REAL,retry_after REAL,reason TEXT,
            failures INTEGER) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS candidate_retry ON candidate_rejections(retry_after);
    ''')


def enqueue(store, rows, limit=20000, source_limit=5000):
    now = time.time()
    trusted = [row for row in rows if is_loopback_url(row['proxy'])]
    public = [row for row in rows if not is_loopback_url(row['proxy'])][:source_limit]
    added = store.ingest(trusted) if trusted else 0
    values = [(row['proxy'], now, now, json.dumps(row.get('sources', [])),
               row.get('source_country'), 0, row['proxy'], row['proxy'], now) for row in public]
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        before = db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0]
        db.executemany('''INSERT INTO candidate_queue
            SELECT ?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM proxies WHERE url=?)
            AND NOT EXISTS(SELECT 1 FROM candidate_rejections WHERE url=? AND retry_after>?)
            ON CONFLICT(url) DO UPDATE SET last_seen=excluded.last_seen,
            sources=(SELECT json_group_array(value) FROM (
                SELECT value FROM json_each(COALESCE(candidate_queue.sources,'[]'))
                UNION SELECT value FROM json_each(excluded.sources))),
            source_country=COALESCE(excluded.source_country,candidate_queue.source_country)''', values)
        count = db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0]
        if count > limit:
            db.execute('''DELETE FROM candidate_queue WHERE url IN
                (SELECT url FROM candidate_queue ORDER BY last_seen,first_seen LIMIT ?)''', (count-limit,))
        after = db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0]
    return added + max(0, after-before)


def _rows(store, limit):
    with store.connect() as db:
        return [dict(row) for row in db.execute('''SELECT * FROM candidate_queue
            WHERE next_check<=? ORDER BY first_seen LIMIT ?''', (time.time(), limit))]


def promote(store, config, stopped):
    rows = _rows(store, config['admission_batch'])
    if not rows:
        return {'screened': 0, 'promoted': 0, 'rejected': 0}
    passed, failed, interrupted = [], [], []
    host = primary_host(config['profiles']['connectivity'])

    def check(row):
        return row, 'stopped' if stopped.is_set() else probe(
            row['url'], config['prefilter_timeout'], host)

    with ThreadPoolExecutor(max_workers=config['prefilter_workers']) as executor:
        jobs = [executor.submit(check, row) for row in rows]
        for job in as_completed(jobs):
            row, result = job.result()
            if result == 'stopped':
                interrupted.append(row)
            elif result is None:
                passed.append(row)
            else:
                failed.append((row, result))
    promoted = store.ingest([dict(proxy=row['url'], sources=json.loads(row['sources'] or '[]'),
                                  source_country=row['source_country']) for row in passed])
    processed = [row['url'] for row in passed] + [row['url'] for row, _ in failed]
    now = time.time()
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        db.executemany('DELETE FROM candidate_queue WHERE url=?', [(url,) for url in processed])
        db.executemany('''INSERT INTO candidate_rejections VALUES(?,?,?,?,1)
            ON CONFLICT(url) DO UPDATE SET rejected_at=excluded.rejected_at,
            retry_after=excluded.retry_after,reason=excluded.reason,failures=failures+1''',
            [(row['url'], now, now+config['candidate_retry_hours']*3600,
              result['reason']) for row, result in failed])
        db.execute('DELETE FROM candidate_rejections WHERE retry_after<?', (now-30*86400,))
    result = {'finished_at': now, 'screened': len(processed), 'promoted': promoted,
              'rejected': len(failed), 'interrupted': len(interrupted)}
    store.put_meta('admission_last', result)
    return result
