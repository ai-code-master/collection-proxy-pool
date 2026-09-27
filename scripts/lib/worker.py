import concurrent.futures
import threading
import time
import traceback
from collections import Counter

from . import checks, settings, sources
from .maintenance import recovery
from .scheduling import prefilter, dispatch, pipeline, retirement
from .collection import quality, admission
from .probing.identity import probe as probe_identity


def paused(store):
    return store.meta('pause:connectivity', 0) > time.time()


def collect(store):
    ingested = False
    config = settings.load()

    def ingest(batch):
        nonlocal ingested
        ingested = True
        return admission.enqueue(store, batch, config['candidate_queue_limit'],
                                 config['candidate_source_limit'])

    rows, reports = sources.collect(previous=store.meta('sources', []), on_batch=ingest)
    if rows and not ingested:
        admission.enqueue(store, rows, config['candidate_queue_limit'], config['candidate_source_limit'])
    store.put_meta('sources', quality.enrich(store, reports, settings.load()))
    store.put_meta('last_collection', time.time())
    store.put_meta('source_transport_v2', True)
    store.set_error('collection')
    print(f'收集完成：本批 {len(rows)} 个去重候选', flush=True)


def cycle(store, config, stopped=None):
    stopped = stopped or threading.Event()
    selected = []
    for profile, target in config['profiles'].items():
        if target['enabled'] and store.meta('pause:' + profile, 0) <= time.time():
            selected.extend((row, profile, target) for row in store.due(
                profile, target['fingerprint'], config['batch_size'], quotas=config['grade_quotas'],
                max_retry_ratio=config['max_failure_retry_ratio']))
    started = time.monotonic()

    def run(item):
        row, profile, target = item
        if stopped.is_set() or store.meta('pause:' + profile, 0) > time.time():
            return None
        if not store.needs_check(row['url'], profile, target['fingerprint']):
            return None
        started_at = time.time()
        result = checks.check(row['url'], profile, target)
        result['started_at'] = started_at
        saved = store.record(row['url'], profile, target['fingerprint'], result, config)
        return row['url'], profile, result['state'] if saved else 'superseded'

    checked, rejected = pipeline.run(selected, store, config, stopped, run)
    result = {'finished_at': time.time(), 'tested': rejected + checked, 'selected': len(selected),
              'prefilter_failed': rejected, 'connectivity_checked': checked,
              'grades_selected': dict(Counter(row.get('grade', 'D') for row, _, _ in selected)),
              'elapsed_seconds': round(time.monotonic()-started, 2)}
    store.put_meta('last_cycle', result)
    store.put_meta('heartbeat', {'time': time.time(), 'state': 'waiting'})
    return result


def identify(store, config, prober=probe_identity):
    profile = config['profiles']['connectivity']
    rows = store.available('connectivity', profile['fingerprint'])

    def run(row):
        value = prober(row['url'], profile.get('identity_targets', ()))
        if value:
            store.geo(row['url'], value)
        return value

    with concurrent.futures.ThreadPoolExecutor(max_workers=config['workers']) as executor:
        values = [value for value in executor.map(run, rows) if value]
    return {'routes': len(rows), 'identified_routes': len(values),
            'unique_exit_ips': len({value['exit_ip'] for value in values}),
            'countries': dict(Counter(value['country'] for value in values))}


def collecting(store, stopped):
    while not stopped.is_set():
        try:
            if paused(store):
                stopped.wait(30)
                continue
            config = settings.load()
            due = store.meta('last_collection', 0) + config['source_interval']
            if (due <= time.time() or store.meta('collection_error') is not None
                    or not store.meta('source_transport_v2', False)):
                collect(store)
        except Exception as error:
            store.set_error('collection', type(error).__name__)
            traceback.print_exc()
            print(f'收集失败：{type(error).__name__}', flush=True)
        stopped.wait(60)


def admitting(store, stopped):
    """独立消化首次候选，避免被主池慢检测阻塞。"""
    while not stopped.is_set():
        try:
            if paused(store):
                stopped.wait(10)
                continue
            admission.promote(store, settings.load(), stopped)
        except Exception as error:
            traceback.print_exc()
            print(f'候选准入失败：{type(error).__name__}', flush=True)
        stopped.wait(1)


def maintain(store, stopped):
    threading.Thread(target=collecting, args=(store, stopped), daemon=True).start()
    threading.Thread(target=admitting, args=(store, stopped), daemon=True).start()
    threading.Thread(target=recovery.recovering, args=(store, stopped, paused), daemon=True).start()
    config = {}
    exported = 0
    while not stopped.is_set():
        try:
            if paused(store):
                store.put_meta('heartbeat', {'time': time.time(), 'state': 'paused'})
                stopped.wait(10)
                continue
            from .api.exports import export
            config = settings.load()
            retirement.cleanup(store)
            cycle(store, config, stopped)
            # 导出是全表渲染加压缩，按间隔节流而不是每轮重写。
            if time.time() - exported >= config['export_interval']:
                export(store, config)
                exported = time.time()
            store.set_error('check')
        except Exception as error:
            store.set_error('check', type(error).__name__)
            traceback.print_exc()
            print(f'检测失败：{type(error).__name__}', flush=True)
        stopped.wait(config.get('cycle_interval', 5))
