import time
from urllib.parse import urlsplit, quote

from ..net import fetch


def endpoints(url):
    parsed = urlsplit(url)
    values = [url]
    parts = parsed.path.strip('/').split('/', 3)
    if parsed.hostname == 'raw.githubusercontent.com' and len(parts) == 4:
        owner, repo, branch, path = parts
        values.append('https://cdn.jsdelivr.net/gh/' + quote(owner, safe='') + '/' +
                      quote(repo, safe='') + '@' + quote(branch, safe='') + '/' + quote(path, safe='/'))
    return values


def download(url, previous=None):
    previous = previous or {}
    choices = endpoints(url)
    preferred = previous.get('fetch_url')
    if preferred in choices and not previous.get('error'):
        choices.remove(preferred)
        choices.insert(0, preferred)
    attempts = []
    for endpoint in choices:
        result = fetch(endpoint, timeout=20)
        attempts.append({'url': endpoint, 'http_status': result['status'], 'error': result['error']})
        if result['status'] == 200 and not result['error'] or result['status'] == 429:
            break
    result = dict(result, fetch_url=endpoint, attempts=attempts, checked_at=time.time())
    if result['status'] != 200 and not result['error']:
        result['error'] = 'HTTP ' + str(result['status'])
    failures = min(6, previous.get('failures', 0)+1) if result['error'] else 0
    delay = max(1800, 900*2**(failures-1)) if result['status'] == 429 else 900*2**max(0, failures-1)
    result.update(failures=failures, next_fetch=time.time()+min(21600, delay))
    return result
