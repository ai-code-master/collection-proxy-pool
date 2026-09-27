"""搜索候选代理列表；候选仅在用户执行时生成。"""
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from .auth import github_token

API_ROOT = os.environ.get('PROXY_POOL_DISCOVERY_API', 'https://api.github.com').rstrip('/')
REPOSITORY_SCAN_LIMIT = 12
QUERIES = ('free proxy list in:name,description,readme',
           'public http https proxy list in:name,description,readme',
           'socks4 socks5 proxy list in:name,description,readme',
           'clash mihomo subscription in:name,description,readme',
           'v2ray vmess vless subscription in:name,description,readme',
           'shadowsocks trojan subscription in:name,description,readme')
PATH_WORDS = re.compile(
    r'(proxy|proxies|http|socks|nodes?|subscription|clash|mihomo|v2ray|vless|vmess|'
    r'trojan|shadowsocks|ssr|hysteria2?|hy2|tuic|sing.?box|vpn)',
    re.I)
EXCLUDED = re.compile(
    r'(^|/)(docs?|examples?|tests?|fixtures?|schemas?|\.github|vendor)/|placeholder', re.I)


def get_json(url, opener=urllib.request.urlopen):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'proxy-pool-source-discovery'}
    token = github_token()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=20) as response:
        return json.loads(response.read(4_000_000))


def search_url(query):
    params = urllib.parse.urlencode({'q': query, 'sort': 'updated',
                                     'order': 'desc', 'per_page': 20})
    return f'{API_ROOT}/search/repositories?{params}'


def tree_url(repository, branch):
    slug = urllib.parse.quote(repository, safe='/')
    ref = urllib.parse.quote(branch, safe='')
    return f'{API_ROOT}/repos/{slug}/git/trees/{ref}?recursive=1'


def raw_url(repository, branch, path):
    quoted = '/'.join(urllib.parse.quote(part, safe='') for part in path.split('/'))
    return f'https://raw.githubusercontent.com/{repository}/{urllib.parse.quote(branch, safe="")}/{quoted}'


def candidate_paths(tree):
    rows = []
    for item in tree.get('tree', []):
        path = str(item.get('path', ''))
        size = int(item.get('size') or 0)
        if (item.get('type') == 'blob' and 0 < size <= 4_000_000
                and path.lower().endswith(('.txt', '.json', '.yaml', '.yml'))
                and PATH_WORDS.search(path) and not EXCLUDED.search(path)):
            rows.append(path)
    return sorted(rows, key=lambda value: (value.count('/'), len(value)))[:4]


def discover(limit=20, getter=get_json):
    found, seen = [], set()
    per_query = max(1, (limit + len(QUERIES) - 1) // len(QUERIES))
    for query in QUERIES:
        query_count = 0
        scanned = 0
        try:
            repositories = getter(search_url(query)).get('items', [])
        except (OSError, ValueError, KeyError):
            continue
        for repository in repositories:
            slug = repository.get('full_name', '')
            branch = repository.get('default_branch', 'main')
            if not slug or slug in seen or repository.get('archived') or repository.get('fork'):
                continue
            seen.add(slug)
            if scanned >= REPOSITORY_SCAN_LIMIT:
                break
            scanned += 1
            try:
                paths = candidate_paths(getter(tree_url(slug, branch)))
            except (OSError, ValueError, KeyError):
                continue
            for path in paths:
                found.append({'name': slug.replace('/', '-') + '-' + Path(path).stem,
                              'url': raw_url(slug, branch, path),
                              'repository': slug, 'path': path})
                query_count += 1
                if query_count >= per_query:
                    break
            if query_count >= per_query:
                break
    return found[:limit]


def write_catalog(path, candidates):
    target = Path(path)
    target = target.resolve() if target.exists() else target
    try:
        catalog = json.loads(target.read_text())
        catalog = catalog if isinstance(catalog, dict) else {}
    except (OSError, ValueError):
        catalog = {}
    before = len(catalog)
    catalog.update({row['name']: row['url'] for row in candidates})
    temporary = target.with_suffix(target.suffix + '.new')
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    if target.exists():
        temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)
    return len(catalog) - before
