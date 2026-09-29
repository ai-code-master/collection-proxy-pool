"""按网络区域保存通用连通性结果。"""
import json

from ..policy import EFFECTIVE_STATE, state_args

REGIONS = ('domestic', 'overseas')


def decoded(row):
    value = dict(row)
    try:
        value['exit_info'] = json.loads(value.get('exit_info') or '{}')
    except (TypeError, ValueError):
        value['exit_info'] = {}
    return value


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS connectivity_capabilities(
            url TEXT,region TEXT,state TEXT,checked_at REAL,valid_until REAL,
            http_status INTEGER,latency_ms REAL,endpoint TEXT,reason TEXT,target TEXT,
            PRIMARY KEY(url,region));
        CREATE INDEX IF NOT EXISTS capability_state
            ON connectivity_capabilities(region,state,valid_until);
    ''')


def save(db, url, target, result, valid_seconds):
    values = []
    for region, value in result['capabilities'].items():
        if region not in REGIONS:
            continue
        valid_until = result['checked_at'] + valid_seconds if value['state'] == 'available' else 0
        values.append((url, region, value['state'], result['checked_at'], valid_until,
                       value['http_status'], value['latency_ms'], value['endpoint'],
                       value['reason'], target))
    db.executemany('INSERT OR REPLACE INTO connectivity_capabilities VALUES(?,?,?,?,?,?,?,?,?,?)', values)


def current(row, now, target):
    return bool(row and row['state'] == 'available' and row['target'] == target
                and row['checked_at'] <= now < row['valid_until'])


def available(db, profile, target, now, reach):
    if reach not in ('any', 'both', 'domestic', 'overseas'):
        raise ValueError('未知连通性范围')
    regions = ('domestic', 'overseas') if reach == 'both' else () if reach == 'any' else (reach,)
    filters, extra = [], []
    for region in regions:
        filters.append('''EXISTS(SELECT 1 FROM connectivity_capabilities k
            WHERE k.url=p.url AND k.region=? AND k.state='available'
            AND k.target=? AND k.checked_at<=? AND k.valid_until>?)''')
        extra.extend((region, target, now, now))
    scope = ' AND ' + ' AND '.join(filters) if filters else ''
    rows = db.execute(f'''SELECT p.*,c.*,
        (d.state='available' AND d.target=c.target AND d.checked_at<=? AND d.valid_until>?) domestic,
        d.latency_ms domestic_latency_ms,
        (o.state='available' AND o.target=c.target AND o.checked_at<=? AND o.valid_until>?) overseas,
        o.latency_ms overseas_latency_ms
        FROM proxies p JOIN checks c ON p.url=c.url
        LEFT JOIN connectivity_capabilities d ON d.url=p.url AND d.region='domestic'
        LEFT JOIN connectivity_capabilities o ON o.url=p.url AND o.region='overseas'
        WHERE c.platform=? AND ({EFFECTIVE_STATE})='available'{scope}
        ORDER BY c.latency_ms,p.url''',
        [now, now, now, now, profile]
        + state_args({'fingerprint': target, 'enabled': True}, now) + extra).fetchall()
    return [decoded(row) for row in rows]


def records(db, profiles, now):
    rows = []
    for profile, target in profiles.items():
        rows.extend(decoded(row) for row in db.execute(f'''SELECT p.url,p.sources,p.country,p.exit_ip,
            p.geo_checked,p.exit_info,
            p.source_country,c.platform,c.state,c.checked_at,c.valid_until,c.next_check,
            c.http_status,c.latency_ms,c.reason,c.target,({EFFECTIVE_STATE}) effective_state,
            (c.state='auth_required') authentication_required,
            d.state domestic_state,d.latency_ms domestic_latency_ms,
            o.state overseas_state,o.latency_ms overseas_latency_ms
            FROM proxies p JOIN checks c ON p.url=c.url
            LEFT JOIN connectivity_capabilities d ON d.url=p.url AND d.region='domestic'
            LEFT JOIN connectivity_capabilities o ON o.url=p.url AND o.region='overseas'
            WHERE c.platform=?''', state_args(target, now) + [profile]))
    return rows
