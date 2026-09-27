import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lib.api.traffic import Limiter


class Handler:
    client_address = ('127.0.0.1', 1234)

    def __init__(self, path='/', headers=None):
        self.path = path
        self.headers = headers or {}
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers[name] = value

    def end_headers(self):
        pass


class TrafficTests(unittest.TestCase):
    def test_health_is_public_and_rate_limit_is_bounded(self):
        limiter = Limiter()
        config = {'api_rate_limit_per_minute': 1}
        self.assertTrue(limiter.allow(Handler('/healthz'), config))
        self.assertTrue(limiter.allow(Handler(), config))
        limited = Handler()
        self.assertFalse(limiter.allow(limited, config))
        self.assertEqual(limited.status, 429)
        self.assertEqual(limited.response_headers['Retry-After'], '60')


if __name__ == '__main__':
    unittest.main()
