"""用国内、国外小响应 HTTPS 端点验证代理连通性。"""
import time
from urllib.parse import urlsplit

from .net import fetch
from .probing.identity import probe as probe_identity

REGIONS = ('domestic', 'overseas')


def primary_host(target):
    return urlsplit(target['targets']['domestic'][0]['url']).hostname


def classify(response, expected_status):
    if response['status'] == 407 or response['error'] == 'proxy_auth_failed':
        return 'auth_required', 'proxy_authentication_required'
    if response['error']:
        return 'unreachable', response['error']
    if response['status'] == expected_status:
        return 'available', f'HTTP {response["status"]}'
    return 'unreachable', f'unexpected_HTTP_{response["status"]}'


def probe_region(proxy, entries):
    last = None
    for entry in entries:
        response = fetch(entry['url'], proxy, timeout=8, headers=('Accept: */*',))
        state, reason = classify(response, entry['status'])
        last = {'state': state, 'reason': reason, 'http_status': response['status'],
                'latency_ms': response['latency_ms'], 'endpoint': entry['url']}
        if state in ('available', 'auth_required'):
            return last
    return last


def check(proxy, profile, target):
    if profile != 'connectivity':
        raise ValueError('当前只支持通用连通性检测')
    started = time.time()
    capabilities = {region: probe_region(proxy, target['targets'][region]) for region in REGIONS}
    states = {value['state'] for value in capabilities.values()}
    successes = [value for value in capabilities.values() if value['state'] == 'available']
    state = 'auth_required' if 'auth_required' in states else 'available' if successes else 'unreachable'
    reason = '+'.join(region for region in REGIONS if capabilities[region]['state'] == 'available') or state
    representative = min(successes or capabilities.values(), key=lambda value: value['latency_ms'])
    identity = probe_identity(proxy, target.get('identity_targets', ())) if successes else None
    return {'state': state, 'reason': reason, 'http_status': representative['http_status'],
            'checked_at': time.time(), 'started_at': started,
            'latency_ms': representative['latency_ms'], 'capabilities': capabilities,
            'identity': identity}
