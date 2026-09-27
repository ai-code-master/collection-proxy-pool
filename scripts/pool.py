#!/usr/bin/env python3
"""独立采集代理池 CLI，只有 Python 标准库及系统 curl 依赖。"""
import argparse
import fcntl
import json
import signal
import threading
import time
from pathlib import Path

from lib import settings, worker
from lib.api.exports import export, available
from lib.net import normalize
from lib.storage import Store


def migrate(store, config, folder):
    source_file = folder / 'candidates/sources.json'
    if source_file.exists():
        raw = json.loads(source_file.read_text())
        rows = [r for r in raw['candidates'] if normalize(r['proxy'], allow_loopback=True) == r['proxy']]
        store.ingest(rows, source_file.stat().st_mtime)
        store.put_meta('sources', raw.get('sources', []))
    old_file = folder / 'pool.json'
    old = json.loads(old_file.read_text())
    imported = 0
    for row in old['results']:
        if normalize(row['proxy'], allow_loopback=True) != row['proxy']:
            continue
        store.ingest([row], row.get('checked_at', old_file.stat().st_mtime))
        if row.get('country') not in (None, 'unknown') and row.get('exit_ip'):
            store.geo(row['proxy'], row)
    store.put_meta('migration', {'from': str(folder), 'time': time.time(), 'checks': imported})
    print(f'已迁入 {imported} 条检测记录，保留原检测时间', flush=True)


def main():
    parser = argparse.ArgumentParser(description='通用代理池：持续收集、国内外连通性验证、定时复测')
    parser.add_argument('command', choices=['status', 'next', 'collect', 'check', 'once', 'serve',
                                                   'import', 'export', 'restore', 'retired', 'discover'])
    parser.add_argument('--profile', '--platform', dest='profile', default=settings.PROFILE,
                        choices=(settings.PROFILE,))
    parser.add_argument('--reach', default='any', choices=('any', 'both', 'domestic', 'overseas'))
    parser.add_argument('--from-dir', type=Path)
    parser.add_argument('--proxy', help='需要恢复的代理完整地址')
    parser.add_argument('--limit', type=int, default=20, help='发现候选数量（1–100）')
    parser.add_argument('--apply', action='store_true', help='将发现结果合并到本地 sources.json')
    args = parser.parse_args()
    if args.command == 'discover':
        if not 1 <= args.limit <= 100:
            parser.error('--limit 须为 1–100')
        from discovery.sources import discover, write_catalog
        rows = discover(args.limit)
        added = write_catalog(settings.SOURCES, rows) if args.apply and rows else 0
        print(json.dumps({'candidates': rows, 'applied': added}, ensure_ascii=False, indent=2))
        return
    config, store = settings.load(), Store()
    if args.command == 'retired':
        with store.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT url,retired_at,retry_after,reason FROM retired_proxies ORDER BY retired_at DESC LIMIT 100')]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.command == 'status':
        print(json.dumps(store.status(config), ensure_ascii=False, indent=2))
        return
    if args.command == 'next':
        rows = available(store, config, args.profile, args.reach)
        if not rows:
            raise SystemExit('当前没有未过期且通过连通性检测的代理')
        print(json.dumps({'purpose': 'proxy_pool', 'profile': args.profile, 'reach': args.reach,
                          'proxy': rows[0]['url'].replace('socks5://', 'socks5h://', 1)}, ensure_ascii=False))
        return
    with (settings.DATA / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('维护进程已在运行；可用 status/next 查看，避免重复启动检测')
        if args.command == 'serve':
            from lib.api.server import serve
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            serve(store, stopped)
            return
        if args.command == 'restore':
            if not args.proxy:
                parser.error('restore 需要 --proxy 完整代理地址')
            from lib.scheduling.retirement import restore
            print(json.dumps(restore(store, args.proxy), ensure_ascii=False))
        if args.command == 'import':
            if args.from_dir is None:
                parser.error('import 需要 --from-dir')
            migrate(store, config, args.from_dir)
        if args.command in ('collect', 'once'):
            worker.collect(store)
        if args.command in ('check', 'once'):
            worker.cycle(store, config)
        export(store, config)


if __name__ == '__main__':
    main()
