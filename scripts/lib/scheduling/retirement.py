"""长期连接失败退出活跃池；恢复记录与来源抑制防止反复导入。"""
import json
import time


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS retired_proxies(
        url TEXT PRIMARY KEY,retired_at REAL,retry_after REAL,reason TEXT,snapshot TEXT)''')


def cleanup(store, now=None, force=False):
    now=time.time() if now is None else now
    if not force and store.meta('retirement_checked',0)>now-3600:
        return 0
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        # 任意非连接失败观测都会打断失败周期。
        rows=db.execute('''SELECT e.url,e.platform,MIN(e.checked_at) first_failure,COUNT(*) failures
            FROM check_events e JOIN checks c ON e.url=c.url AND e.platform=c.platform AND e.target=c.target
            WHERE e.platform='connectivity' AND e.state='unreachable'
            AND c.state='unreachable' AND e.checked_at<=?
            AND NOT EXISTS(SELECT 1 FROM authenticated_proxies a WHERE a.url=e.url)
            AND NOT EXISTS(SELECT 1 FROM checks other WHERE other.url=e.url
                AND other.platform='connectivity' AND other.state!='unreachable')
            AND e.checked_at>COALESCE((SELECT MAX(h.checked_at) FROM check_events h
                WHERE h.url=e.url AND h.platform=e.platform AND h.state!='unreachable'),0)
            GROUP BY e.url,e.platform HAVING COUNT(*)>=10 AND MIN(e.checked_at)<=?
            AND MAX(e.checked_at)-MIN(e.checked_at)>=? AND MAX(e.checked_at)>=? LIMIT 500''',
            (now,now-7*86400,7*86400,now-86400)).fetchall()
        selected = {r['url']: (r, f"连续连接失败至少7天，本周期{r['failures']}次") for r in rows}
        stale = db.execute('''SELECT p.url FROM proxies p WHERE p.last_seen<=?
            AND NOT EXISTS(SELECT 1 FROM authenticated_proxies a WHERE a.url=p.url)
            AND EXISTS(SELECT 1 FROM checks c WHERE c.url=p.url
                AND c.platform='connectivity' AND c.state='unreachable')
            AND NOT EXISTS(SELECT 1 FROM checks c WHERE c.url=p.url
                AND c.platform='connectivity' AND c.state!='unreachable')
            AND NOT EXISTS(SELECT 1 FROM check_events e WHERE e.url=p.url
                AND e.state!='unreachable' AND e.checked_at>?)
            ORDER BY p.last_seen LIMIT 500''', (now-7*86400, now-7*86400)).fetchall()
        for row in stale:
            if len(selected) >= 500:
                break
            selected.setdefault(row['url'], (row, '来源失联超过7天且当前连接失败；移出活跃库'))
        removed=0
        for row, reason in selected.values():
            proxy=db.execute('SELECT * FROM proxies WHERE url=?',(row['url'],)).fetchone()
            if proxy is None:
                continue
            checks=[dict(r) for r in db.execute('SELECT * FROM checks WHERE url=?',(row['url'],))]
            snapshot=json.dumps({'proxy':dict(proxy),'checks':checks},ensure_ascii=False)
            db.execute('INSERT OR REPLACE INTO retired_proxies VALUES(?,?,?,?,?)',
                       (row['url'],now,now+30*86400,reason,snapshot))
            for table in ('checks','connectivity_capabilities','reliability','retry_health','proxies'):
                db.execute(f'DELETE FROM {table} WHERE url=?',(row['url'],))
            removed+=1
        db.execute('DELETE FROM retired_proxies WHERE retired_at<?',(now-90*86400,))
        total=db.execute('SELECT COUNT(*) FROM retired_proxies').fetchone()[0]
        for key,value in [('retirement_checked',now),('retirement_last',{'time':now,'removed':removed,'archived':total})]:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value)))
    return removed


def restore(store,url):
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT snapshot FROM retired_proxies WHERE url=?',(url,)).fetchone()
        if row is None:
            raise ValueError('未找到该代理的恢复记录')
        proxy=json.loads(row['snapshot'])['proxy']
        db.execute('''INSERT OR IGNORE INTO proxies(url,first_seen,last_seen,sources,source_country,country,exit_ip,geo_checked)
            VALUES(?,?,?,?,?,?,?,?)''',(url,proxy['first_seen'],time.time(),proxy['sources'],proxy['source_country'],
                                      proxy['country'],proxy['exit_ip'],proxy['geo_checked']))
        db.execute('DELETE FROM retired_proxies WHERE url=?',(url,))
    return {'proxy':url,'state':'待重新验证；旧可用状态未恢复'}
