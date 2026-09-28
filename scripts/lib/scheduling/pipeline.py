"""预筛结果实时进入业务队列，不等待本批最慢端口；队列受候选批次上限约束。"""
import queue
import threading
from . import prefilter
from .stages import run as run_stages
from .stages.capacity import choose


def run(items, store, config, stopped):
    feed = queue.Queue(maxsize=max(1, config['batch_size']))
    outcome = {'rejected': 0, 'error': None}
    cancelled = threading.Event()

    def enqueue(item):
        while not cancelled.is_set():
            try:
                feed.put(item, timeout=.1)
                return
            except queue.Full:
                continue

    def produce():
        try:
            _, outcome['rejected'] = prefilter.screen(items, store, config, stopped,
                                                       on_ready=enqueue)
        except Exception as error:
            outcome['error'] = error
        finally:
            enqueue(None)

    producer = threading.Thread(target=produce, name='proxy-prefilter')
    producer.start()
    try:
        plan = choose(store, config, config['profiles']['connectivity']['fingerprint'])
        stages = run_stages(feed, store, config, stopped, plan)
    finally:
        cancelled.set()
        producer.join()
    if outcome['error']:
        raise outcome['error']
    stages['prefilter_rejected'] = outcome['rejected']
    store.put_meta('pipeline_tuning', stages)
    return stages['checked'], outcome['rejected'], stages
