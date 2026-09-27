"""按客户端地址计数的固定窗口限速。"""
import json
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit


class Limiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._requests = defaultdict(deque)

    def allow(self, handler, config):
        if urlsplit(handler.path).path == '/healthz':
            return True
        if not self._within_limit(handler.client_address[0], config['api_rate_limit_per_minute']):
            self._reject(handler)
            return False
        return True

    def _within_limit(self, address, limit):
        now = time.monotonic()
        with self._lock:
            bucket = self._requests[address]
            while bucket and bucket[0] <= now - 60:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            if len(self._requests) > 4096:
                self._requests = defaultdict(deque, {
                    key: value for key, value in self._requests.items()
                    if value and value[-1] > now - 60})
            return True

    @staticmethod
    def _reject(handler):
        content = json.dumps({'error': 'rate_limit_exceeded'}).encode()
        handler.send_response(429)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(content)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Retry-After', '60')
        handler.end_headers()
        handler.wfile.write(content)
