"""检测事件只记真实观测；迁移时仅补录现有的最后一次检测。"""
import json
import time


def initialize(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS check_events(
        id INTEGER PRIMARY KEY, url TEXT, platform TEXT, state TEXT, checked_at REAL,
        http_status INTEGER, latency_ms REAL, reason TEXT, target TEXT, origin TEXT);
        CREATE INDEX IF NOT EXISTS event_lookup ON check_events(url,platform,checked_at DESC);
        CREATE INDEX IF NOT EXISTS event_time ON check_events(checked_at);''')
    if not db.execute("SELECT 1 FROM meta WHERE key='history_started'").fetchone():
        db.execute('''INSERT INTO check_events(url,platform,state,checked_at,http_status,
            latency_ms,reason,target,origin) SELECT url,platform,state,checked_at,http_status,
            latency_ms,reason,target,'legacy_latest' FROM checks''')
        db.execute('INSERT INTO meta VALUES(?,?)', ('history_started', json.dumps(time.time())))


def append(db, url, platform, target, result):
    db.execute('''INSERT INTO check_events(url,platform,state,checked_at,http_status,
        latency_ms,reason,target,origin) VALUES(?,?,?,?,?,?,?,?,?)''',
        (url, platform, result['state'], result['checked_at'], result['http_status'],
         result['latency_ms'], result['reason'], target, result.get('origin', 'probe')))
    row = db.execute("SELECT value FROM meta WHERE key='history_pruned'").fetchone()
    if row is None or time.time() - json.loads(row[0]) > 3600:
        cutoff = time.time() - 14 * 86400
        success_cutoff = time.time() - 6 * 3600
        db.execute("DELETE FROM check_events WHERE checked_at<? OR "
                   "(origin='radar' AND state='available' AND checked_at<?)",
                   (cutoff, success_cutoff))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('history_pruned', json.dumps(time.time())))
