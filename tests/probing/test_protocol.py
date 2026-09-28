import socket
import unittest
from unittest.mock import Mock
from lib.probing.protocol import inspect


class ProtocolTests(unittest.TestCase):
    def sock(self, *blocks):
        return Mock(recv=Mock(side_effect=blocks))

    def test_http_split_headers_and_success(self):
        sock = self.sock(b'HTTP/1.', b'1 200 Connection established\r\n')
        self.assertIsNone(inspect(sock,'http','example.com',3))
        self.assertIn(b'CONNECT example.com:443',sock.sendall.call_args.args[0])

    def test_explicit_connect_failures_never_pass(self):
        for response in (b'HTTP/1.1 400 Bad Request\r\n', b'', b'SSH-2.0-test\r\n'):
            with self.subTest(response=response):
                self.assertEqual(inspect(self.sock(response),'http','example.com',3)[0],'unreachable')

    def test_non_success_connect_status_is_unreachable(self):
        for code in (403,429,461):
            result=inspect(self.sock(f'HTTP/1.1 {code} Restricted\r\n'.encode()),'http','example.com',3)
            self.assertEqual(result[0],'unreachable')
            self.assertEqual(result[2],code)

    def test_slow_handshake_gets_full_business_check(self):
        self.assertIsNone(inspect(self.sock(socket.timeout()),'http','example.com',3))

    def test_socks5_anonymous_only_and_fragmented_response(self):
        self.assertIsNone(inspect(self.sock(b'\x05',b'\x00'),'socks5','example.com',3))
        self.assertEqual(inspect(self.sock(b'\x05\x02'),'socks5','example.com',3)[0],'auth_required')

    def test_socks4a_fragmented_success_and_rejection(self):
        sock = self.sock(b'\x00\x5a', b'\x00\x00\x00\x00\x00\x00')
        self.assertIsNone(inspect(sock, 'socks4', 'example.com', 3))
        self.assertIn(b'example.com\x00', sock.sendall.call_args.args[0])
        self.assertEqual(inspect(self.sock(b'\x00\x5b\x00\x00\x00\x00\x00\x00'),
                                 'socks4', 'example.com', 3)[0], 'unreachable')

    def test_http_authentication_is_not_a_dead_proxy(self):
        result = inspect(self.sock(b'HTTP/1.1 407 Auth required\r\n'),'http','example.com',3)
        self.assertEqual((result[0],result[2]),('auth_required',407))

    def test_early_eof_never_enters_business_queue(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from lib import worker, settings
        from lib.storage import Store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'pool.sqlite3')
            store.ingest([{'proxy':'http://8.8.8.8:80'}])
            connection=self.sock(b'')
            context=Mock()
            context.__enter__=Mock(return_value=connection)
            context.__exit__=Mock(return_value=False)
            with patch('lib.scheduling.prefilter.socket.create_connection',return_value=context), \
                    patch.object(worker.checks,'probe_region') as checker:
                result=worker.cycle(store,settings.load())
            checker.assert_not_called()
            self.assertEqual(result['prefilter_failed'],1)
            self.assertEqual(store.records()[0]['reason'],'proxy_prefilter:early_eof')
