"""根据积压、CPU 与句柄余量为每轮流水线选择并发。"""
import os
import resource
import time


def due_count(store, target, now=None):
    now = time.time() if now is None else now
    with store.connect() as db:
        return db.execute('''SELECT COUNT(*) FROM proxies p
            LEFT JOIN checks c ON c.url=p.url AND c.platform='connectivity'
            WHERE p.last_seen>? AND (c.state IS NULL OR c.state!='auth_required')
            AND NOT EXISTS(SELECT 1 FROM authenticated_proxies a WHERE a.url=p.url)
            AND (c.url IS NULL OR c.next_check<=? OR c.target!=?)''',
                          (now-604800, now, target)).fetchone()[0]


def resource_pressure():
    cpus = max(1, os.cpu_count() or 1)
    try:
        load_ratio = os.getloadavg()[0] / cpus
    except OSError:
        load_ratio = 0
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    fd_root = '/dev/fd' if os.path.isdir('/dev/fd') else '/proc/self/fd'
    try:
        fd_ratio = len(os.listdir(fd_root)) / max(1, soft)
    except OSError:
        fd_ratio = 0
    return {'load_ratio': round(load_ratio, 3), 'fd_ratio': round(fd_ratio, 3)}


def choose(store, config, target):
    backlog = due_count(store, target)
    pressure = resource_pressure()
    base = config['workers']
    maximum = min(24, max(base, base * 3))
    interval = config['probe_interval']
    reason = 'balanced'
    if pressure['load_ratio'] >= 1.5 or pressure['fd_ratio'] >= .7:
        workers, interval, reason = max(2, base // 2), max(1, interval), 'resource_pressure'
    elif backlog >= config['batch_size'] * 4:
        workers, interval, reason = maximum, max(.35, interval / 6), 'deep_backlog'
    elif backlog >= config['batch_size']:
        workers, interval, reason = min(maximum, base + max(2, base // 2)), max(.5, interval / 2), 'backlog'
    else:
        workers, interval = base, max(.5, min(interval, 1))
    if config['probe_interval'] <= 0:
        interval = 0
    return {'region_workers': workers, 'identity_workers': max(1, workers // 4),
            'max_inflight': max(16, workers * 4), 'launch_interval': interval,
            'backlog': backlog, 'reason': reason, **pressure}
