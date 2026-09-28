"""国内、海外并行；每个地区只有失败才继续尝试备用站。"""
import concurrent.futures
import threading
import time
from queue import Empty

from ... import checks
from .model import ProbeJob


class RateGate:
    def __init__(self, interval):
        self.interval = interval
        self.next_at = 0
        self.lock = threading.Lock()

    def enter(self, stopped):
        with self.lock:
            now = time.time()
            slot = max(now, self.next_at)
            self.next_at = slot + self.interval
        if slot > now:
            stopped.wait(slot-now)
        return not stopped.is_set()


def failure(region, started, error='stage_error'):
    return {'state': 'unreachable', 'reason': f'{region}:{error}', 'http_status': 0,
            'latency_ms': round((time.monotonic()-started)*1000), 'endpoint': ''}


def run(feed, store, config, stopped, plan):
    started = time.monotonic()
    executors = {region: concurrent.futures.ThreadPoolExecutor(
        max_workers=plan['region_workers'], thread_name_prefix=region)
        for region in checks.REGIONS}
    identity_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=plan['identity_workers'], thread_name_prefix='identity')
    gates = {region: RateGate(plan['launch_interval']) for region in checks.REGIONS}
    jobs, futures = {}, {}
    exhausted, checked, token, heartbeat_at = False, 0, 0, 0

    def probe(job, region):
        began = time.monotonic()
        if not gates[region].enter(stopped):
            return failure(region, began, 'stopped')
        return checks.probe_region(job.row['url'], job.target['targets'][region])

    def submit(item):
        nonlocal token
        row, platform, target = item
        if (platform != 'connectivity' or stopped.is_set()
                or store.meta('pause:' + platform, 0) > time.time()
                or not store.needs_check(row['url'], platform, target['fingerprint'])):
            return
        token += 1
        job = ProbeJob(token, item, time.time())
        jobs[token] = job
        for region in checks.REGIONS:
            future = executors[region].submit(probe, job, region)
            futures[future] = ('region', token, region)

    def finish(job, identity=None):
        nonlocal checked
        result = checks.compose(job.capabilities, job.started_at, identity)
        if store.record(job.row['url'], job.platform, job.target['fingerprint'], result, config):
            checked += 1
            print(f"{job.row['url']} {job.platform} {result['state']}", flush=True)
        jobs.pop(job.token, None)

    try:
        while jobs or futures or not exhausted:
            while not exhausted and len(jobs) < plan['max_inflight']:
                try:
                    item = feed.get_nowait()
                except Empty:
                    break
                if item is None:
                    exhausted = True
                    break
                submit(item)
            done = set()
            if futures:
                done, _ = concurrent.futures.wait(
                    futures, timeout=.05, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                kind, job_id, region = futures.pop(future)
                job = jobs.get(job_id)
                if job is None:
                    continue
                try:
                    value = future.result()
                except Exception as error:
                    value = failure(region or 'identity', time.monotonic(), type(error).__name__)
                if kind == 'identity':
                    finish(job, value if isinstance(value, dict) and value.get('exit_ip') else None)
                    continue
                job.add(region, value)
                if not job.regions_complete(checks.REGIONS):
                    continue
                targets = job.target.get('identity_targets', ())
                if job.has_success() and targets:
                    identity = identity_pool.submit(checks.probe_identity, job.row['url'], targets)
                    futures[identity] = ('identity', job.token, None)
                else:
                    finish(job)
            if time.time() >= heartbeat_at:
                store.put_meta('heartbeat', {'time': time.time(), 'state': 'staged_pipeline',
                                             'completed': checked, 'remaining': len(jobs)})
                heartbeat_at = time.time() + 1
            if not done and not futures and not exhausted:
                time.sleep(.01)
    finally:
        for executor in tuple(executors.values()) + (identity_pool,):
            executor.shutdown(wait=True)
    return {'checked': checked, 'elapsed_seconds': round(time.monotonic()-started, 2),
            'plan': plan}
