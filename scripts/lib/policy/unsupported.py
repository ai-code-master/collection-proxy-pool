"""Permanently remove endpoints that require credentials from the anonymous pool."""

import time


ACTIVE_TABLES = ('checks', 'connectivity_capabilities', 'reliability',
                 'retry_health', 'proxies', 'candidate_queue')


def reject(db, url, reason='proxy_authentication_unsupported', detected_at=None):
    detected_at = time.time() if detected_at is None else detected_at
    db.execute('INSERT OR REPLACE INTO authenticated_proxies VALUES(?,?,0)',
               (url, f'{reason}:{int(detected_at)}'))
    for table in ACTIVE_TABLES:
        db.execute(f'DELETE FROM {table} WHERE url=?', (url,))


def initialize(db):
    # Retain only the old endpoint exclusion table for database compatibility.
    db.execute('''CREATE TABLE IF NOT EXISTS authenticated_proxies(
        url TEXT PRIMARY KEY,version TEXT,enabled INTEGER)''')
    if not db.execute("SELECT 1 FROM meta WHERE key='generic_states_v1'").fetchone():
        db.execute("UPDATE checks SET state='unreachable',valid_until=0,reason='legacy_state_removed' "
                   "WHERE platform='connectivity' AND state IN ('restricted','invalid')")
        db.execute("UPDATE check_events SET state='unreachable',reason='legacy_state_removed' "
                   "WHERE platform='connectivity' AND state IN ('restricted','invalid')")
        db.execute("UPDATE retry_health SET tier='retry' "
                   "WHERE platform='connectivity' AND tier='restricted'")
        db.execute("INSERT INTO meta VALUES('generic_states_v1','true')")
    if not db.execute("SELECT 1 FROM meta WHERE key='credentials_removed_v1'").fetchone():
        db.execute("UPDATE checks SET state='auth_required',valid_until=0,"
                   "reason='proxy_authentication_unsupported' WHERE url IN "
                   "(SELECT url FROM authenticated_proxies)")
        db.execute('UPDATE authenticated_proxies SET enabled=0')
        db.execute("UPDATE retry_health SET tier='auth_required' WHERE url IN "
                   "(SELECT url FROM authenticated_proxies)")
        db.execute("INSERT INTO meta VALUES('credentials_removed_v1','true')")
    if db.execute("SELECT 1 FROM meta WHERE key='auth_endpoints_removed_v2'").fetchone():
        return
    db.execute("INSERT OR IGNORE INTO authenticated_proxies "
               "SELECT url,'detected',0 FROM checks WHERE state='auth_required'")
    for table in ACTIVE_TABLES:
        db.execute(f'DELETE FROM {table} WHERE url IN (SELECT url FROM authenticated_proxies)')
    db.execute("INSERT INTO meta VALUES('auth_endpoints_removed_v2','true')")


def endpoints(store):
    with store.connect() as db:
        return {row[0] for row in db.execute('SELECT url FROM authenticated_proxies')}
