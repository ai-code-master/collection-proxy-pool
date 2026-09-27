import json
import threading
import time

from . import __version__
from .settings import DATA, load
from . import history
from .database import SQLitePool
from .database.system import clash_exit
from .collection import admission
from .policy import retries, unsupported
from .probing import capabilities
from .scheduling import grading, allocation, retirement
from .tasks import state as task_state


class Store:
    META_CACHE_SECONDS = 2

    def __init__(self, path=None):
        self.path = path or DATA / 'pool.sqlite3'
        self._meta_lock = threading.Lock()
        self._meta_cache = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connections = SQLitePool(self.path)
        with self.write() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS proxies(
                    url TEXT PRIMARY KEY, first_seen REAL, last_seen REAL,
                    sources TEXT, source_country TEXT, country TEXT DEFAULT 'unknown',
                    exit_ip TEXT DEFAULT '', geo_checked REAL DEFAULT 0,
                    exit_info TEXT DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS checks(
                    url TEXT, platform TEXT, state TEXT, checked_at REAL, valid_until REAL,
                    next_check REAL, failures INTEGER, http_status INTEGER,
                    latency_ms REAL, target TEXT, reason TEXT,
                    PRIMARY KEY(url,platform));
                CREATE INDEX IF NOT EXISTS due_checks ON checks(platform,next_check);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            ''')
            history.initialize(db)
            capabilities.initialize(db)
            admission.initialize(db)
            grading.initialize(db)
            retirement.initialize(db)
            retries.initialize(db)
            unsupported.initialize(db)
            task_state.initialize(db)
            columns = {row[1] for row in db.execute('PRAGMA table_info(proxies)')}
            if 'exit_info' not in columns:
                db.execute("ALTER TABLE proxies ADD COLUMN exit_info TEXT DEFAULT '{}'")

    def connect(self):
        return self._connections.connection()

    def write(self):
        return self._connections.write_connection()

    def close(self):
        self._connections.close()

    def meta(self, key, default=None):
        with self._meta_lock:
            cached = self._meta_cache.get(key)
            if cached and time.time() < cached[0]:
                return cached[1]
            with self.connect() as db:
                row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            value = json.loads(row[0]) if row else default
            self._meta_cache[key] = (time.time() + self.META_CACHE_SECONDS, value)
            return value

    def put_meta(self, key, value):
        with self._meta_lock:
            with self.write() as db:
                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value)))
            self._meta_cache.pop(key, None)

    def set_error(self, kind, error=None):
        key = kind + '_error'
        with self._meta_lock:
            with self.write() as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                previous = json.loads(current[0]) if current else None
                if error is None and previous is None:
                    return
                last = db.execute('SELECT value FROM meta WHERE key=?', ('last_' + key,)).fetchone()
                detail = json.loads(last[0]) if last else {'error': previous, 'time': None}
                if error is not None:
                    detail = {'error': error, 'time': time.time(), 'recovered_at': None}
                else:
                    detail['recovered_at'] = time.time()
                db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',
                               [(key, json.dumps(error)), ('last_' + key, json.dumps(detail))])
            self._meta_cache.pop(key, None)
            self._meta_cache.pop('last_' + key, None)

    def ingest(self, rows, seen=None):
        now = time.time() if seen is None else seen
        values = [(r['proxy'], now, now, json.dumps(r.get('sources', [])),
                   r.get('source_country')) for r in rows]
        with self.write() as db:
            db.execute('BEGIN IMMEDIATE')
            before = db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
            blocked = {r[0] for r in db.execute('SELECT url FROM retired_proxies WHERE retry_after>?', (now,))}
            values = [value for value in values if value[0] not in blocked]
            db.executemany('''INSERT INTO proxies(url,first_seen,last_seen,sources,source_country)
                VALUES(?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET last_seen=excluded.last_seen,
                sources=(SELECT json_group_array(value) FROM (
                    SELECT value FROM json_each(COALESCE(proxies.sources,'[]'))
                    UNION SELECT value FROM json_each(excluded.sources))),
                source_country=COALESCE(excluded.source_country,proxies.source_country)''', values)
            return db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0] - before

    def due(self, platform, target, limit, now=None, quotas=None, max_retry_ratio=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            return allocation.select(db, platform, target, limit, now, quotas, max_retry_ratio)

    def needs_check(self, url, platform, target):
        with self.connect() as db:
            row = db.execute('SELECT state,target,next_check FROM checks WHERE url=? AND platform=?',
                             (url, platform)).fetchone()
        if row is not None and row['state'] == 'auth_required':
            return False
        return row is None or row['next_check'] <= time.time() or row['target'] != target

    def record(self, url, platform, target, result, config):
        from .scheduling.recording import save
        with self.write() as db:
            db.execute('BEGIN IMMEDIATE')
            return save(db, url, platform, target, result, config)

    def geo(self, url, value):
        if value:
            with self.write() as db:
                db.execute('''UPDATE proxies SET country=?,exit_ip=?,geo_checked=?,exit_info=?
                    WHERE url=?''', (value['country'], value['exit_ip'], time.time(),
                                     json.dumps(value.get('exit_info', {})), url))

    def available(self, platform, target, now=None, reach='any'):
        now = time.time() if now is None else now
        with self.connect() as db:
            return capabilities.available(db, platform, target, now, reach)

    def records(self, config=None):
        config = load() if config is None else config
        now = time.time()
        with self.connect() as db:
            return capabilities.records(db, config['profiles'], now)

    def status(self, config):
        now = time.time()
        profile_rows = {name: self.available(name, target['fingerprint'])
                        if target['enabled'] else []
                        for name, target in config['profiles'].items()}
        with self.connect() as db:
            candidates = db.execute('SELECT COUNT(*) FROM proxies').fetchone()[0]
            queued = db.execute('SELECT COUNT(*) FROM candidate_queue').fetchone()[0]
            names = tuple(config['profiles'])
            placeholders = ','.join('?' for _ in names)
            counts = db.execute(f'''SELECT platform,state,COUNT(*) n FROM checks
                WHERE platform IN ({placeholders}) GROUP BY platform,state''', names).fetchall()
            target = config['profiles']['connectivity']['fingerprint']
            region_counts = {row['region']: row['n'] for row in db.execute('''SELECT region,COUNT(*) n
                FROM connectivity_capabilities WHERE state='available' AND target=?
                AND checked_at<=? AND valid_until>? GROUP BY region''', (target, now, now))}
        return {'purpose': 'proxy_pool', 'version': __version__, 'candidates': candidates,
                'queued_candidates': queued,
                'profiles': {p: {'enabled': target['enabled'], 'available': len(profile_rows[p]),
                                  'identified_routes': sum(bool(r['exit_ip']) for r in profile_rows[p]),
                                  'unique_exit_ips': len({r['exit_ip'] for r in profile_rows[p]
                                                          if r['exit_ip']}),
                                  'paused_until': self.meta('pause:' + p, 0)}
                              for p, target in config['profiles'].items()},
                'connectivity': {'domestic': region_counts.get('domestic', 0),
                                 'overseas': region_counts.get('overseas', 0)},
                'marks': [dict(r) for r in counts], 'updated_at': now,
                'heartbeat': self.meta('heartbeat', {}), 'sources': self.meta('sources', []),
                'errors': {'collection': self.meta('collection_error'), 'checks': self.meta('check_error')},
                'last_errors': {'collection': self.meta('last_collection_error'), 'checks': self.meta('last_check_error')},
                'last_collection': self.meta('last_collection', 0), 'clash_exit': clash_exit(),
                'last_cycle': self.meta('last_cycle', {}), 'retirement': self.meta('retirement_last', {})}
