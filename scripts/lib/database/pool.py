import queue
import sqlite3
import threading
from contextlib import contextmanager


class SQLitePool:
    """有上限的 SQLite 连接池。

    长运行进程不再为每个 HTTP/检查请求反复打开数据库文件，
    并在高并发时通过等待施加背压，而不是耗尽文件句柄。
    """

    def __init__(self, path, size=16, timeout=15):
        self.path = path
        self.size = size
        self.timeout = timeout
        self._idle = queue.LifoQueue(size)
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._created = 0
        self._closed = False

    @property
    def created(self):
        with self._lock:
            return self._created

    def _open(self):
        db = sqlite3.connect(self.path, timeout=self.timeout, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute(f'PRAGMA busy_timeout={int(self.timeout * 1000)}')
        return db

    def _checkout(self):
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        create = False
        with self._lock:
            if self._closed:
                raise RuntimeError('SQLite connection pool is closed')
            if self._created < self.size:
                self._created += 1
                create = True
        if not create:
            return self._idle.get()
        try:
            return self._open()
        except Exception:
            with self._lock:
                self._created -= 1
            raise

    def _checkin(self, db):
        with self._lock:
            closed = self._closed
            if closed:
                self._created -= 1
        if closed:
            db.close()
        else:
            self._idle.put(db)

    @contextmanager
    def connection(self):
        db = self._checkout()
        try:
            with db:
                yield db
        finally:
            self._checkin(db)

    @contextmanager
    def write_connection(self):
        # SQLite 在 WAL 下可多读单写；先在进程内排队，
        # 避免反馈请求和后台检测同时 BEGIN IMMEDIATE 抢锁。
        with self._write_lock:
            with self.connection() as db:
                yield db

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        while True:
            try:
                db = self._idle.get_nowait()
            except queue.Empty:
                return
            db.close()
            with self._lock:
                self._created -= 1

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
