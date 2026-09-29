"""有界通用 HTTPS 检测池；任意测试站成功即完成任务。"""
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


def failure(started, error='stage_error'):
    return {'state': 'unreachable', 'reason': error, 'http_status': 0,
            'checked_at': time.time(), 'started_at': time.time(),
            'latency_ms': round((time.monotonic()-started)*1000)}


def run(feed, store, config, stopped, plan):
    started = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=plan['probe_workers'], thread_name_prefix='https-probe')
    gate = RateGate(plan['launch_interval'])
    jobs, futures = {}, {}
    exhausted, checked, token, heartbeat_at = False, 0, 0, 0

    def probe(job):
        began = time.monotonic()
        if not gate.enter(stopped):
            return failure(began, 'stopped')
        return checks.check(job.row['url'], job.platform, job.target)

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
        futures[executor.submit(probe, job)] = token

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
                job = jobs.pop(futures.pop(future), None)
                if job is None:
                    continue
                try:
                    result = future.result()
                except Exception as error:
                    result = failure(time.monotonic(), type(error).__name__)
                result['started_at'] = job.started_at
                if store.record(job.row['url'], job.platform,
                                job.target['fingerprint'], result, config):
                    checked += 1
                    print(f"{job.row['url']} {job.platform} {result['state']}", flush=True)
            if time.time() >= heartbeat_at:
                store.put_meta('heartbeat', {'time': time.time(), 'state': 'staged_pipeline',
                                             'completed': checked, 'remaining': len(jobs)})
                heartbeat_at = time.time() + 1
            if not done and not futures and not exhausted:
                time.sleep(.01)
    finally:
        executor.shutdown(wait=True)
    return {'checked': checked, 'elapsed_seconds': round(time.monotonic()-started, 2),
            'plan': plan}
