"""连接公开代理并检查协议握手；成功仅进入业务队列。"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from ..net import normalize
from ..checks import primary_host
from ..probing.protocol import inspect


def probe(url, timeout, host):
    started, started_at = time.monotonic(), time.time()
    try:
        # 池内回环节点（本地 mihomo 出口）允许进入 TCP 预筛；私网/非公网地址仍在此拦截。
        if normalize(url, allow_loopback=True) != url:
            raise ValueError('invalid_proxy_address')
        parsed = urlsplit(url)
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout) as connection:
            outcome = inspect(connection, parsed.scheme, host, timeout)
            if outcome is None:
                return None
            state, reason, status = outcome
            return {'state': state, 'checked_at': time.time(), 'started_at': started_at,
                    'http_status': status, 'latency_ms': round((time.monotonic()-started)*1000),
                    'reason': reason}
    except (OSError, ValueError) as error:
        return {'state': 'unreachable', 'checked_at': time.time(), 'started_at': started_at, 'http_status': 0,
                'latency_ms': round((time.monotonic()-started)*1000),
                'reason': 'tcp_prefilter:' + type(error).__name__}


def screen(items, store, config, stopped, on_ready=None):
    ready, rejected = set(), 0

    def run(index, item):
        if stopped.is_set():
            return index, 'stopped'
        row, _, target = item
        # 已通过完整 HTTPS 探测的节点直接复测，避免短预筛误伤。
        result = None if row.get('state') == 'available' else probe(
            row['url'], config['prefilter_timeout'], primary_host(target))
        return index, result

    with ThreadPoolExecutor(max_workers=config['prefilter_workers']) as executor:
        jobs = [executor.submit(run, index, item) for index, item in enumerate(items)]
        for job in as_completed(jobs):
            index, result = job.result()
            if result == 'stopped':
                continue
            if result is None:
                ready.add(index)
                if on_ready is not None:
                    on_ready(items[index])
            else:
                row, platform, target = items[index]
                store.record(row['url'], platform, target['fingerprint'], result, config)
                rejected += 1
            if on_ready is None:
                store.put_meta('heartbeat', {'time': time.time(), 'state': 'screening',
                                        'completed': rejected, 'screened': len(ready)+rejected})
    return [item for index, item in enumerate(items) if index in ready], rejected
