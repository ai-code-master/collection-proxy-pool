import threading
from http.server import ThreadingHTTPServer


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """有背压的线程 HTTP 服务，防止慢请求耗尽文件句柄。"""

    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address, handler, max_workers=16):
        super().__init__(address, handler)
        self._slots = threading.BoundedSemaphore(max_workers)

    def process_request(self, request, client_address):
        self._slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
