"""空闲槽位滚动补充；发往平台的请求保持间隔，并在每次提交前核查冷却。"""
import time
from queue import Empty
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def run(items, store, config, stopped, check, feed=None):
    pending, running = deque(items), set()
    completed = 0
    exhausted = feed is None
    next_at = store.meta('last_probe_started', 0) + config['probe_interval']
    heartbeat_at = 0
    with ThreadPoolExecutor(max_workers=config['workers']) as executor:
        while pending or running or not exhausted:
            if not exhausted:
                while True:
                    try:
                        item = feed.get_nowait()
                    except Empty:
                        break
                    if item is None:
                        exhausted = True
                        break
                    pending.append(item)
            if stopped.is_set():
                pending.clear()
            if pending and len(running) < config['workers'] and time.time() >= next_at:
                item = pending.popleft()
                if store.meta('pause:' + item[1], 0) <= time.time():
                    at = time.time()
                    store.put_meta('last_probe_started', at)
                    running.add(executor.submit(check, item))
                    next_at = at + config['probe_interval']
            if running:
                done, running = wait(running, timeout=.1, return_when=FIRST_COMPLETED)
                for job in done:
                    value = job.result()
                    if value is not None:
                        completed += 1
                        print(' '.join(value), flush=True)
            elif pending or not exhausted:
                # stop 后也等待预筛线程退出，避免事件已置位时的忙循环。
                time.sleep(.01 if stopped.is_set() else min(.1, max(.01, next_at-time.time())))
            if time.time() >= heartbeat_at:
                store.put_meta('heartbeat', {'time': time.time(), 'state': 'pipeline' if feed is not None else 'checking',
                                            'completed': completed, 'remaining': len(pending)})
                heartbeat_at = time.time() + 1
    return completed
