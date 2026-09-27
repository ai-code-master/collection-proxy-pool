import json
import statistics
import time
from urllib.parse import urlsplit

from ..settings import ROOT
from ..scheduling import grading
from ..collection import quality
from ..policy import EFFECTIVE_STATE, state_args
from ..policy.retries import NAMES as RETRY_NAMES
from .metrics import scheduling

ASSETS = {'/guide': ('guide.html', 'text/html'), '/disclaimer': ('../DISCLAIMER.md', 'text/markdown'),
          '/assets/guide.css': ('guide.css', 'text/css'), '/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          **{f'/assets/{name}': (name, kind) for name, kind in [
              ('style.css', 'text/css'), ('app.js', 'text/javascript'),
              ('format.js', 'text/javascript'), ('table.js', 'text/javascript'), ('detail.js', 'text/javascript')]}}


def static(path):
    name, kind = ASSETS[path]
    return (ROOT / 'web' / name).read_text(), kind + '; charset=utf-8'


def base(config, platform):
    now = time.time()
    sql = f'''SELECT p.*,c.platform,c.state,c.checked_at,c.valid_until,c.next_check,
        c.http_status,c.latency_ms,c.reason,c.failures,c.target,
        (d.state='available' AND d.target=? AND d.checked_at<=? AND d.valid_until>?) domestic,
        d.latency_ms domestic_latency_ms,
        (o.state='available' AND o.target=? AND o.checked_at<=? AND o.valid_until>?) overseas,
        o.latency_ms overseas_latency_ms,
        COALESCE(q.successes,0) success_streak,{grading.EXPRESSION} grade,
        COALESCE(h.tier,'retry') retry_tier,
        ({EFFECTIVE_STATE}) effective_state,
        COALESCE(NULLIF(p.country,'unknown'),p.source_country,'unknown') display_country
        FROM proxies p LEFT JOIN checks c ON p.url=c.url AND c.platform=?
        LEFT JOIN connectivity_capabilities d ON d.url=p.url AND d.region='domestic'
        LEFT JOIN connectivity_capabilities o ON o.url=p.url AND o.region='overseas'
        LEFT JOIN reliability q ON q.url=c.url AND q.platform=c.platform AND q.target=c.target
        LEFT JOIN retry_health h ON h.url=c.url AND h.platform=c.platform AND h.target=c.target'''
    target = config['profiles'][platform]
    capability_args = [target['fingerprint'], now, now, target['fingerprint'], now, now]
    return sql, capability_args + [target['fingerprint']] + state_args(target, now) + [platform]


def decorate(row, platform):
    row = dict(row)
    parsed = urlsplit(row['url'])
    row.update(protocol=parsed.scheme.upper(), host=parsed.hostname, port=parsed.port,
               project='通用网络连通性', platform=platform)
    row['sources'] = json.loads(row['sources'] or '[]')
    row['grade_name'] = grading.NAMES[row['grade']]
    row['retry_name'] = '等待连通性检测' if row['grade'] == 'D' else RETRY_NAMES.get(row['retry_tier'], '失败重试')
    return row


def listing(store, config, query):
    platform = query.get('project', ['connectivity'])[0]
    if platform not in config['profiles']:
        raise ValueError('未知项目')
    page = max(1, min(100000, int(query.get('page', ['1'])[0])))
    limit = 30
    sql, args = base(config, platform)
    clauses, filters = [], []
    state = query.get('state', ['available'])[0]
    if state == 'unavailable':
        clauses.append("effective_state IN ('unreachable','expired','unverified','disabled','auth_required')")
    elif state == 'failed':
        clauses.append("effective_state IN ('unreachable','expired','unverified','disabled')")
    elif state != 'all':
        if state not in ('available', 'unreachable', 'expired', 'untested', 'unverified', 'disabled', 'auth_required'):
            raise ValueError('未知状态')
        clauses.append('effective_state=?')
        filters.append(state)
    retry = query.get('retry', [''])[0]
    if retry:
        if retry not in RETRY_NAMES:
            raise ValueError('未知复测队列')
        clauses.append('retry_tier=?')
        filters.append(retry)
    grade = query.get('grade', [''])[0]
    if grade:
        if grade not in grading.NAMES:
            raise ValueError('未知等级')
        clauses.append('grade=?')
        filters.append(grade)
    for key, column in [('country', 'country')]:
        if query.get(key, [''])[0]:
            clauses.append(f'{column}=?')
            filters.append(query[key][0])
    protocol = query.get('protocol', [''])[0]
    if protocol:
        if protocol not in ('http', 'socks5'):
            raise ValueError('未知协议')
        clauses.append('url LIKE ?')
        filters.append(protocol + '://%')
    search = query.get('search', [''])[0][:200]
    if search:
        clauses.append('instr(lower(url || exit_ip),lower(?))>0')
        filters.append(search)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    columns = {'speed': 'COALESCE(latency_ms,999999999)', 'checked': 'COALESCE(checked_at,0)',
               'country': 'country', 'address': 'url', 'grade': 'grade'}
    sort = columns.get(query.get('sort', ['speed'])[0], columns['speed'])
    order = 'DESC' if query.get('direction', ['asc'])[0] == 'desc' else 'ASC'
    with store.connect() as db:
        total = db.execute(f'SELECT COUNT(*) FROM ({sql})' + where, args + filters).fetchone()[0]
        page = min(page, max(1, (total + limit - 1) // limit))
        rows = db.execute(f'SELECT * FROM ({sql})' + where + f' ORDER BY {sort} {order},url LIMIT ? OFFSET ?',
                          args + filters + [limit, (page - 1) * limit]).fetchall()
        # 本页全部节点的最近成功/失败一次取回，避免逐行查历史。
        marks = ','.join('?' * len(rows)) or "''"
        times = {r['url']: r for r in db.execute(f'''SELECT url,
            MAX(CASE WHEN state='available' THEN checked_at END) last_success,
            MAX(CASE WHEN state!='available' THEN checked_at END) last_failure
            FROM check_events WHERE platform=? AND target=? AND url IN ({marks}) GROUP BY url''',
            [platform, config['profiles'][platform]['fingerprint']] + [row['url'] for row in rows])}
        result = []
        for row in rows:
            value = decorate(row, platform)
            row_times = times.get(row['url'])
            value.update(last_success=row_times['last_success'] if row_times else None,
                         last_failure=row_times['last_failure'] if row_times else None)
            result.append(value)
    return {'items': result, 'total': total, 'page': page, 'page_size': limit, 'time': time.time()}


def overview(store, config):
    sql, args = base(config, 'connectivity')
    # 单次扫描在 Python 侧聚合，替代 states/grades/countries/latencies 四遍全表 GROUP BY。
    states, grades, country_counts, latencies = {}, {}, {}, []
    split = {'clash': 0, 'public': 0}
    with store.connect() as db:
        for state, grade, country, latency, srcs in db.execute(
                f'SELECT effective_state,grade,country,latency_ms,sources FROM ({sql})', args):
            states[state] = states.get(state, 0) + 1
            grades[grade] = grades.get(grade, 0) + 1
            if state == 'available':
                if country and country != 'unknown':
                    country_counts[country] = country_counts.get(country, 0) + 1
                split['clash' if 'clash-free-http' in (srcs or '') else 'public'] += 1
                if latency is not None:
                    latencies.append(latency)
    countries = [{'code': code, 'count': count} for code, count in
                 sorted(country_counts.items(), key=lambda item: item[1], reverse=True)]
    status = store.status(config)
    reports = quality.enrich(store, [dict(row) for row in status['sources']], config)
    available = max(1, states.get('available', 0))
    for report in reports:
        report['contribution_percent'] = round(report['available'] * 100 / available, 1)
    status['sources'] = reports
    status['source_summary'] = {
        'total': len(reports),
        'healthy': sum(row.get('http_status') == 200 and not row.get('error') for row in reports),
        'contributing': sum(row['available'] > 0 for row in reports),
    }
    return {'status': status, 'states': states, 'countries': countries, 'grades': grades,
            'available_sources': split,
            'scheduling': scheduling(store),
            'median_ms': round(statistics.median(latencies)) if latencies else None,
            'config': {key: config[key] for key in ('workers', 'source_interval', 'recheck_interval', 'valid_seconds', 'prefilter_workers', 'cycle_interval', 'probe_interval', 'new_recheck_interval', 'grade_quotas')},
            'history_started': store.meta('history_started')}


def detail(store, config, query):
    proxy = query.get('proxy', [''])[0]
    platform = query.get('project', ['connectivity'])[0]
    if platform not in config['profiles']:
        raise ValueError('未知项目')
    sql, args = base(config, platform)
    with store.connect() as db:
        row = db.execute(f'SELECT * FROM ({sql}) WHERE url=?', args + [proxy]).fetchone()
        if row is None:
            raise ValueError('未找到代理')
        events = db.execute('''SELECT state,checked_at,http_status,latency_ms,reason,origin
            FROM check_events WHERE url=? AND platform=? AND target=? ORDER BY checked_at DESC,id DESC LIMIT 40''',
            (proxy, platform, config['profiles'][platform]['fingerprint'])).fetchall()
    return {'proxy': decorate(row, platform), 'events': [dict(r) for r in events],
            'history_started': store.meta('history_started')}
