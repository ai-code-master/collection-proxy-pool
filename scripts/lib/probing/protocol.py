"""在同一 TCP 连接上检查代理握手；不发送商品接口请求，不把握手成功当作业务成功。"""
import socket
import time

def inspect(sock, scheme, host, timeout):
    deadline = time.monotonic() + timeout

    def receive(size):
        sock.settimeout(max(.001, deadline-time.monotonic()))
        return sock.recv(size)

    try:
        if scheme == 'http':
            authority = host + ':443'
            sock.sendall(f'CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n'.encode())
            data = b''
            while b'\n' not in data and len(data) < 4096:
                block = receive(min(512, 4096-len(data)))
                if not block:
                    return 'unreachable', 'proxy_prefilter:early_eof', 0
                data += block
            line = data.split(b'\n', 1)[0].split()
            if len(line) < 2 or line[0] not in (b'HTTP/1.0', b'HTTP/1.1') or not line[1].isdigit():
                return 'unreachable', 'proxy_prefilter:invalid_http', 0
            code = int(line[1])
            if code == 200:
                return None
            state = 'auth_required' if code == 407 else 'unreachable'
            return state, f'proxy_prefilter:CONNECT_HTTP_{code}', code
        sock.sendall(b'\x05\x01\x00')
        data = b''
        while len(data) < 2:
            block = receive(2-len(data))
            if not block:
                return 'unreachable', 'proxy_prefilter:early_eof', 0
            data += block
        if data != b'\x05\x00':
            state = 'auth_required' if data in (b'\x05\x02', b'\x05\xff') else 'unreachable'
            return state, 'proxy_prefilter:socks5_no_anonymous_method', 0
    except (socket.timeout, TimeoutError):
        # 短握手时限内未完成不判死，仍让完整业务检测确认。
        return None
    except OSError as error:
        return 'unreachable', 'proxy_prefilter:' + type(error).__name__, 0
    return None
