import threading
import time
import traceback

from .. import settings
from . import state


class Scheduler:
    """轻量级调度器：每个到期任务独立运行，慢任务不阻塞代理检测。"""

    def __init__(self, store, stopped, jobs):
        self.store = store
        self.stopped = stopped
        self.jobs = jobs
        self.running = set()
        self.lock = threading.Lock()

    def execute(self, name):
        try:
            result = self.jobs[name](settings.load())
            state.finish(self.store, name, result=result)
        except Exception as error:
            state.finish(self.store, name, error=f'{type(error).__name__}: {error}')
            traceback.print_exc()
            print(f'调度任务失败 {name}：{type(error).__name__}', flush=True)
        finally:
            with self.lock:
                self.running.discard(name)

    def start_due(self, name):
        with self.lock:
            if name in self.running or not state.claim(self.store, name):
                return
            self.running.add(name)
        threading.Thread(target=self.execute, args=(name,), daemon=True,
                         name='schedule-' + name).start()

    def run(self):
        while not self.stopped.is_set():
            try:
                state.sync(self.store, settings.load())
                for name in self.jobs:
                    self.start_due(name)
            except Exception:
                traceback.print_exc()
            self.stopped.wait(5)

