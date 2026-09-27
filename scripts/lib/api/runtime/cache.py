import threading
import time


class TTLCache:
    """容量有上限的单航班缓存，同一类慢查询只允许一个构建者。"""

    def __init__(self, max_entries):
        self.max_entries = max_entries
        self._values = {}
        self._lock = threading.Lock()

    def get(self, key, seconds, build):
        with self._lock:
            now = time.time()
            hit = self._values.get(key)
            if hit and hit[0] > now:
                return hit[1]
            value = build()
            now = time.time()
            expired = [item for item, entry in self._values.items() if entry[0] <= now]
            for item in expired:
                self._values.pop(item, None)
            if len(self._values) >= self.max_entries:
                oldest = min(self._values, key=lambda item: self._values[item][0])
                self._values.pop(oldest)
            self._values[key] = (now + seconds, value)
            return value

    def clear(self):
        with self._lock:
            self._values.clear()


class CacheSet:
    def __init__(self):
        self.marks = TTLCache(2)
        self.rows = TTLCache(4)
        self.overview = TTLCache(4)
        self.listing = TTLCache(256)

    def clear(self):
        for cache in (self.marks, self.rows, self.overview, self.listing):
            cache.clear()


caches = CacheSet()
