"""预筛结果实时进入业务队列，不等待本批最慢端口；队列受候选批次上限约束。"""
import queue
import threading
from . import prefilter, dispatch


def run(items, store, config, stopped, check):
    feed = queue.Queue()
    outcome = {'rejected': 0, 'error': None}

    def produce():
        try:
            _, outcome['rejected'] = prefilter.screen(items, store, config, stopped, on_ready=feed.put)
        except Exception as error:
            outcome['error'] = error
        finally:
            feed.put(None)

    producer = threading.Thread(target=produce, name='proxy-prefilter')
    producer.start()
    try:
        checked = dispatch.run([], store, config, stopped, check, feed=feed)
    finally:
        producer.join()
    if outcome['error']:
        raise outcome['error']
    return checked, outcome['rejected']
