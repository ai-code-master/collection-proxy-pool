"""Retry frequency follows observed connectivity failures and successes."""

NAMES = {'healthy': '正常复测', 'recovery': '近期可用，优先恢复', 'retry': '失败重试',
         'cold': '冷队列，每天复测', 'auth_required': '需认证，已停检'}


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS retry_health(
        url TEXT,platform TEXT,target TEXT,last_success REAL,failed_since REAL,
        failures INTEGER,tier TEXT,PRIMARY KEY(url,platform))''')
    if db.execute("SELECT 1 FROM meta WHERE key='retry_policy_v1'").fetchone():
        return
    # Historical failures may be superseded; only seed the latest failure observation.
    db.execute('''INSERT OR IGNORE INTO retry_health
        SELECT c.url,c.platform,c.target,COALESCE(e.last_success,0),
        CASE WHEN c.state='unreachable' THEN c.checked_at ELSE 0 END,c.failures,
        CASE WHEN c.state='available' THEN 'healthy'
             WHEN c.state='auth_required' THEN 'auth_required' ELSE 'retry' END
        FROM checks c LEFT JOIN (SELECT url,platform,target,MAX(checked_at) last_success
        FROM check_events WHERE state='available' GROUP BY url,platform,target) e
        ON c.url=e.url AND c.platform=e.platform AND c.target=e.target''')
    db.execute("INSERT INTO meta VALUES('retry_policy_v1','true')")


def advance(db, url, platform, target, result, wait):
    now, state = result['checked_at'], result['state']
    old = db.execute('SELECT * FROM retry_health WHERE url=? AND platform=?', (url, platform)).fetchone()
    same = old is not None and old['target'] == target
    success = old['last_success'] if same else 0
    since = old['failed_since'] if same else 0
    failures = old['failures'] if same else 0
    if state == 'unreachable':
        since, failures = since or now, failures + 1
        tier = 'retry'
        if success and now-success <= 86400 and failures <= 3:
            tier, wait = 'recovery', min(wait, 900)
        elif failures >= 5 and now-since >= 86400:
            tier, wait = 'cold', 86400
    else:
        since, failures = 0, 0
        tier = {'available': 'healthy', 'auth_required': 'auth_required'}.get(state, 'retry')
        if state == 'available':
            success = now
    db.execute('INSERT OR REPLACE INTO retry_health VALUES(?,?,?,?,?,?,?)',
               (url, platform, target, success, since, failures, tier))
    return wait
