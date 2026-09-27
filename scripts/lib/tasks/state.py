import json
import time


SPECS = {
    'source_collection': ('公开来源采集', 'source_interval', None),
    'source_discovery': ('新来源发现', 'discovery_interval', 'discovery_enabled'),
    'export_snapshot': ('导出快照', 'export_interval', None),
}


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS scheduled_tasks(
            name TEXT PRIMARY KEY, interval_seconds INTEGER NOT NULL,
            enabled INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'idle',
            last_started REAL, last_finished REAL, next_run REAL NOT NULL DEFAULT 0,
            lease_until REAL NOT NULL DEFAULT 0, result TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS source_candidates(
            url TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT,
            path TEXT, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
            discoveries INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'pending');
        CREATE INDEX IF NOT EXISTS source_candidate_state
            ON source_candidates(state,last_seen DESC);
    ''')


def sync(store, config, now=None):
    now = time.time() if now is None else now
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        for name, (_, interval_key, enabled_key) in SPECS.items():
            interval = config[interval_key]
            enabled = enabled_key is None or config[enabled_key]
            db.execute('''INSERT INTO scheduled_tasks(name,interval_seconds,enabled,next_run)
                VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                interval_seconds=excluded.interval_seconds,enabled=excluded.enabled,
                next_run=CASE WHEN scheduled_tasks.interval_seconds!=excluded.interval_seconds
                    THEN MIN(scheduled_tasks.next_run,?+excluded.interval_seconds)
                    ELSE scheduled_tasks.next_run END,
                status=CASE WHEN excluded.enabled=0 THEN 'disabled'
                    WHEN scheduled_tasks.status='disabled' THEN 'idle'
                    ELSE scheduled_tasks.status END''',
                       (name, interval, int(enabled), now, now))


def claim(store, name, now=None):
    now = time.time() if now is None else now
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM scheduled_tasks WHERE name=?', (name,)).fetchone()
        if not row or not row['enabled'] or row['next_run'] > now or row['lease_until'] > now:
            return False
        lease = now + max(1800, min(row['interval_seconds'] * 2, 7200))
        db.execute('''UPDATE scheduled_tasks SET status='running',last_started=?,
            next_run=?,lease_until=?,error=NULL WHERE name=?''',
                   (now, now + row['interval_seconds'], lease, name))
        return True


def finish(store, name, result=None, error=None, now=None):
    now = time.time() if now is None else now
    status = 'error' if error else 'idle'
    payload = json.dumps(result, ensure_ascii=False) if result is not None else None
    with store.write() as db:
        db.execute('''UPDATE scheduled_tasks SET status=?,last_finished=?,lease_until=0,
            result=?,error=? WHERE name=?''', (status, now, payload, error, name))


def snapshot(store, config):
    sync(store, config)
    with store.connect() as db:
        rows = [dict(row) for row in db.execute(
            'SELECT * FROM scheduled_tasks ORDER BY name')]
        counts = {row['state']: row['n'] for row in db.execute(
            'SELECT state,COUNT(*) n FROM source_candidates GROUP BY state')}
    for row in rows:
        row['label'] = SPECS[row['name']][0]
        row['enabled'] = bool(row['enabled'])
        row['result'] = json.loads(row['result']) if row['result'] else None
    keys = ('source_interval', 'discovery_interval', 'discovery_enabled',
            'history_recheck_interval', 'recheck_interval',
            'new_recheck_interval', 'export_interval')
    return {'settings': {key: config[key] for key in keys}, 'tasks': rows,
            'source_candidates': counts}
