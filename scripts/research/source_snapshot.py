"""Read-only, point-in-time snapshots for source membership research."""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib import settings
from lib.policy import EFFECTIVE_STATE, state_args


def snapshot():
    config, now = settings.load(), time.time()
    target = config['profiles']['connectivity']
    db = sqlite3.connect(f'file:{ROOT}/data/pool.sqlite3?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        rows = db.execute(f'''SELECT p.url,p.first_seen,p.last_seen,p.sources,c.checked_at,
            c.valid_until,c.latency_ms,c.reason FROM proxies p JOIN checks c ON p.url=c.url
            WHERE c.platform='connectivity' AND ({EFFECTIVE_STATE})='available'
            AND NOT EXISTS(SELECT 1 FROM authenticated_proxies a WHERE a.url=p.url)
            ORDER BY p.url''', state_args(target, now)).fetchall()
        alive = [dict(row, sources=json.loads(row['sources'])) for row in rows]
        candidates = [row[0] for row in db.execute('SELECT url FROM proxies')]
        previous = db.execute("SELECT value FROM meta WHERE key='sources'").fetchone()
        heartbeat = db.execute("SELECT value FROM meta WHERE key='heartbeat'").fetchone()
        return dict(time=now, alive=alive, candidates=candidates,
                    previous=json.loads(previous[0]) if previous else [],
                    heartbeat=json.loads(heartbeat[0]) if heartbeat else {})
    finally:
        db.close()
