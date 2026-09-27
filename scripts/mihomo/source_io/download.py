"""安全、有界并发地下载公开订阅。"""
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

DOWNLOAD_WORKERS = 4
RETRY_WORKERS = 2
SOURCE_TIMEOUT = 60


def retry_url(source):
    match = re.fullmatch(
        r'https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)', source)
    if not match:
        return source
    owner, repo, branch, path = match.groups()
    return f'https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}'


def download(url, timeout=30):
    command = ['/usr/bin/curl', '-q', '-fsSL', '--compressed', '--proto', '=https',
               '--proxy', '', '--connect-timeout', '10', '--max-time', str(timeout),
               '-A', 'clash-exit-refresh/1.0', url]
    result = subprocess.run(command, capture_output=True, timeout=timeout + 5)
    if result.returncode:
        detail = result.stderr.decode(errors='replace').strip()
        raise OSError(detail or f'curl {result.returncode}')
    return result.stdout


def fetch_one(source, retry=False):
    try:
        target = retry_url(source) if retry else source
        return source, download(target, timeout=30 if retry else SOURCE_TIMEOUT), ''
    except Exception as error:
        return source, None, f'error: {type(error).__name__} {error}'


def fetch_many(urls, workers, retry=False):
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as executor:
        return list(executor.map(lambda source: fetch_one(source, retry), urls))


def fetch_bodies(urls):
    pairs = fetch_many(urls, DOWNLOAD_WORKERS)
    failed = [source for source, _, error in pairs if error]
    retried = {source: (source, body, error) for source, body, error
               in fetch_many(failed, RETRY_WORKERS, retry=True)}
    final = [retried.get(source, (source, body, error))
             for source, body, error in pairs]
    bodies = {source: body for source, body, error in final if not error}
    errors = {source: error for source, _, error in final if error}
    return bodies, errors
