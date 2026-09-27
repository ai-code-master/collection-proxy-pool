"""历史成功代理的独立复测队列。"""
import concurrent.futures
import time
import traceback

from .. import checks, settings


def claim_rows(store, target, limit, now, cooldown):
    """原子领取历史通过但当前失效的节点，避免重复检测。"""
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('''SELECT p.*,c.state,c.checked_at,c.valid_until,c.next_check,
                c.failures,c.target
            FROM proxies p JOIN checks c ON c.url=p.url AND c.platform='connectivity'
            WHERE c.next_check<=? AND c.state!='auth_required'
              AND (c.target!=? OR c.state!='available' OR c.valid_until<=?)
              AND EXISTS(SELECT 1 FROM check_events e WHERE e.url=p.url
                  AND e.platform='connectivity' AND e.state='available')
            ORDER BY CASE WHEN c.state='available' AND c.valid_until<=? THEN 0 ELSE 1 END,
                c.next_check,p.last_seen DESC,p.url LIMIT ?''',
            (now, target, now, now, limit)).fetchall()
        db.executemany('''UPDATE checks SET next_check=?
            WHERE url=? AND platform='connectivity' AND next_check<=?''',
            [(now + cooldown, row['url'], now) for row in rows])
    return [dict(row) for row in rows]


def run_batch(store, config, stopped):
    target = config['profiles']['connectivity']
    now = time.time()
    rows = claim_rows(store, target['fingerprint'], config['history_recheck_batch'], now,
                      config['history_recheck_interval'])
    if not rows:
        return 0, 0

    def check(row):
        if stopped.is_set():
            return None
        result = checks.check(row['url'], 'connectivity', target)
        result['started_at'] = time.time()
        return row, result

    recovered = 0
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=config['history_recheck_workers']) as executor:
        jobs = [executor.submit(check, row) for row in rows]
        for job in concurrent.futures.as_completed(jobs):
            value = job.result()
            if value is None:
                continue
            row, result = value
            if store.record(row['url'], 'connectivity', target['fingerprint'], result, config):
                recovered += result['state'] == 'available'
    return len(rows), recovered


def recovering(store, stopped, paused):
    """主池繁忙时仍持续恢复历史成功代理。"""
    while not stopped.is_set():
        try:
            if paused(store):
                stopped.wait(30)
                continue
            screened, recovered = run_batch(store, settings.load(), stopped)
            if screened:
                print(f'历史节点复测：{screened} 个，通过 {recovered} 个', flush=True)
                stopped.wait(2)
            else:
                stopped.wait(settings.load()['history_recheck_interval'])
        except Exception as error:
            traceback.print_exc()
            print(f'历史节点复测失败：{type(error).__name__}', flush=True)
            stopped.wait(30)
