"""无登录态的有界 HTTPS 请求；不继承环境代理、不关闭证书验证。"""
import ipaddress
import os
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


def is_loopback_url(url):
    try:
        return ipaddress.ip_address(urlsplit(url).hostname or '').is_loopback
    except ValueError:
        return False


def normalize(value, allow_loopback=False):
    # allow_loopback 仅放行回环地址（本机 mihomo 出口等本地可信节点）；
    # 公开来源保持默认 False，仍只收公网地址。
    try:
        parsed = urlsplit(value.strip())
        address = ipaddress.ip_address(parsed.hostname or '')
        routable = address.is_global or (allow_loopback and address.is_loopback)
        if (parsed.scheme not in ('http', 'socks4', 'socks5') or not routable
                or address.is_multicast or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or not parsed.port or not 1 <= parsed.port <= 65535):
            return None
        host = f'[{address}]' if address.version == 6 else str(address)
        return f'{parsed.scheme}://{host}:{parsed.port}'
    except ValueError:
        return None


def fetch(url, proxy='', timeout=12, headers=()):
    # 回环 HTTP 仅用于本机静态来源服务（如 http://127.0.0.1:21995/nodes.txt），其余仍强制 HTTPS。
    loopback_http = is_loopback_url(url) and urlsplit(url).scheme == 'http'
    if urlsplit(url).scheme != 'https' and not loopback_http:
        raise ValueError('测试目标和来源必须使用 HTTPS')
    # 池内代理允许回环地址：回环节点只会来自 sources.py 显式放行的本地来源。
    if proxy and normalize(proxy, allow_loopback=True) != proxy:
        raise ValueError('代理不是规范化公网地址')
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    proxy = proxy.replace('socks5://', 'socks5h://', 1)
    proxy = proxy.replace('socks4://', 'socks4a://', 1)
    started = time.monotonic()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        command = ['/usr/bin/curl', '-q', '-sS', '--proxy', proxy, '--noproxy', '',
                   '--connect-timeout', '5', '--max-time', str(timeout), '--max-filesize', '4194304',
                   '--proto', '=http' if loopback_http else '=https',
                   '--write-out', '\n__CONNECT__%{http_connect}\n__STATUS__%{http_code}', url]
        for header in headers:
            command.extend(['--header', header])
        try:
            process = subprocess.run(command, stdout=output, stderr=errors,
                                     env=env, timeout=timeout + 2)
            size = output.tell()
            output.seek(0)
            raw = output.read(4194400)
            body, _, status = raw.decode('utf-8', errors='replace').rpartition('\n__STATUS__')
            content, marker, connected = body.rpartition('\n__CONNECT__')
            if marker:
                body = content
                if connected.strip() == '407':
                    status = '407'
            error = 'response_too_large' if size > 4194400 else f'curl_{process.returncode}' if process.returncode else ''
            errors.seek(0)
            diagnostic = errors.read(4096).decode(errors='replace').lower()
            if 'authentication failed' in diagnostic or 'user was rejected by the socks5' in diagnostic:
                error = 'proxy_auth_failed'
            return {'status': int(status or 0), 'body': body, 'error': error,
                    'latency_ms': round((time.monotonic() - started) * 1000)}
        except (subprocess.TimeoutExpired, OSError, ValueError) as error:
            return {'status': 0, 'body': '', 'error': type(error).__name__,
                    'latency_ms': round((time.monotonic() - started) * 1000)}
