"""通过每条 Mihomo lane 检测连通性并获取出口 IP。"""
import http.client
import time
from concurrent.futures import ThreadPoolExecutor

from .settings import EXIT_IP_TARGET, PROBE_TARGETS, PROBE_TIMEOUT, PROBE_WORKERS


def tunnel_get(port, host, path, timeout=PROBE_TIMEOUT):
    started = time.monotonic()
    connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=timeout)
    try:
        connection.set_tunnel(host, 443)
        headers = {'Host': host, 'User-Agent': 'clash-exit-probe/1.0'}
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        body = response.read(4096)
        return response.status, body, round((time.monotonic() - started) * 1000)
    finally:
        connection.close()


def probe_lanes(ports):
    alive = {}

    def probe(item):
        port = item[0]
        for host, path, expected in PROBE_TARGETS:
            try:
                status, _, latency = tunnel_get(port, host, path)
                if status == expected:
                    return port, latency
            except (OSError, http.client.HTTPException):
                pass
        return port, None

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as executor:
        for port, latency in executor.map(probe, ports):
            if latency is not None:
                alive[port] = {'latency_ms': latency}

    def exit_ip(port):
        if not EXIT_IP_TARGET:
            return port, None
        try:
            host, path, expected = EXIT_IP_TARGET
            status, body, _ = tunnel_get(port, host, path)
            if status == expected:
                return port, body.decode(errors='replace').strip()[:45]
        except (OSError, http.client.HTTPException):
            pass
        return port, None

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as executor:
        for port, ip in executor.map(exit_ip, list(alive)):
            alive[port]['exit_ip'] = ip
    return alive
