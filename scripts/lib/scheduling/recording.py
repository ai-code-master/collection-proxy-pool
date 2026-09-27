"""保存连通性检测结果；迟到结果不能覆盖新结果。"""
import json

from .. import history
from ..probing import capabilities
from .grading import advance
from ..policy import retries


def save(db, url, platform, target, result, config):
    if result['state'] not in ('available', 'unreachable', 'auth_required'):
        raise ValueError('不支持的连通性状态')
    now = result['checked_at']
    old = db.execute('SELECT * FROM checks WHERE url=? AND platform=?', (url, platform)).fetchone()
    if old and result.get('started_at', now) < old['checked_at']:
        return False
    history.append(db, url, platform, target, result)
    failures = 0 if result['state'] == 'available' else min(10, (old['failures'] if old else 0)+1)
    successes = advance(db, url, platform, target, result)
    interval = config['recheck_interval'] if successes >= 3 else config.get('new_recheck_interval', 300)
    wait = interval if not failures else min(21600, 300*2**(failures-1))
    wait = retries.advance(db, url, platform, target, result, wait)
    expires = now+config['valid_seconds'] if not failures else 0
    db.execute('INSERT OR REPLACE INTO checks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (url, platform, result['state'], now, expires, now+wait, failures,
                result['http_status'], result['latency_ms'], target, result['reason']))
    if result.get('capabilities'):
        capabilities.save(db, url, target, result, config['valid_seconds'])
    identity = result.get('identity')
    if identity:
        db.execute('''UPDATE proxies SET exit_ip=?,country=?,geo_checked=?,exit_info=?
            WHERE url=?''', (identity['exit_ip'], identity['country'], now,
                              json.dumps(identity['exit_info']), url))
    return True
