"""任意小响应 HTTPS 端点成功即可确认代理可用。"""
import time
from urllib.parse import urlsplit

from .net import fetch
from .probing.identity import probe as probe_identity

REGIONS = ('domestic', 'overseas')
MAX_SITES = 3


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


def probe_any(proxy, target):
    """交错尝试少量站点，任意一个成功或要求认证即停止。"""
    groups, sites = target['targets'], []
    for index in range(max(map(len, groups.values()))):
        for region in REGIONS:
            if index < len(groups[region]):
                sites.append((region, groups[region][index]))
    if not sites:
        raise ValueError('未配置连通性测试站')
    capabilities = {}
    for region, entry in sites[:MAX_SITES]:
        value = probe_region(proxy, (entry,))
        capabilities[region] = value
        if value['state'] in ('available', 'auth_required'):
            break
    return capabilities


def compose(capabilities, started=None, identity=None):
    started = time.time() if started is None else started
    states = {value['state'] for value in capabilities.values()}
    successes = [value for value in capabilities.values() if value['state'] == 'available']
    state = 'auth_required' if 'auth_required' in states else 'available' if successes else 'unreachable'
    reason = '+'.join(region for region in REGIONS
                      if capabilities.get(region, {}).get('state') == 'available') or state
    representative = min(successes or capabilities.values(), key=lambda value: value['latency_ms'])
    return {'state': state, 'reason': reason, 'http_status': representative['http_status'],
            'checked_at': time.time(), 'started_at': started,
            'latency_ms': representative['latency_ms'], 'capabilities': capabilities,
            'identity': identity}


def check(proxy, profile, target):
    if profile != 'connectivity':
        raise ValueError('当前只支持通用连通性检测')
    started = time.time()
    capabilities = probe_any(proxy, target)
    successes = any(value['state'] == 'available' for value in capabilities.values())
    identity = probe_identity(proxy, target.get('identity_targets', ())) if successes else None
    return compose(capabilities, started, identity)
