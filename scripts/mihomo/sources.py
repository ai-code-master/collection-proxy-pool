"""下载、容错解析并去重 Clash 免费订阅。"""
import base64
import binascii
import datetime
import json
import re

import yaml

from .settings import (DATED_SOURCE_TEMPLATE, REVISION_API, REVISION_SOURCE_TEMPLATE,
                       SOURCES, SUPPORTED)
from .source_io.download import DOWNLOAD_WORKERS, download, fetch_bodies
from .uris import parse_subscription

FALLBACK_KEYS = ('name', 'type', 'server', 'port', 'cipher', 'uuid', 'password',
                 'alterId', 'network', 'tls', 'servername', 'sni', 'flow',
                 'skip-cert-verify', 'udp', 'country', 'protocol', 'obfs')
def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def credential(row):
    for key in ('uuid', 'password', 'auth-pass', 'passwd'):
        if row.get(key):
            return str(row[key])
    return ''


def stable_key(row):
    return f"{row.get('type')}|{row['server']}|{int(row['port'])}|{credential(row)}"


def repo_label(url):
    for pattern in (r'gh/([^/]+/[^/@]+)',
                    r'raw\.githubusercontent\.com/([^/]+/[^/]+)'):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url


def source_label(url):
    label = repo_label(url)
    if sum(repo_label(source) == label for source in SOURCES) <= 1:
        return label
    path = re.search(r'(?:@[^/]+|raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+)/'
                     r'(.+?)(?:\?.*)?$', url)
    return f'{label}#{path.group(1)}' if path else label


def aibobox_urls(days=3):
    if not DATED_SOURCE_TEMPLATE:
        return
    today = datetime.date.today()
    for index in range(days):
        yield DATED_SOURCE_TEMPLATE % (today - datetime.timedelta(days=index)).strftime('%Y%m%d')


def barabama_url():
    if not REVISION_API or not REVISION_SOURCE_TEMPLATE:
        return None
    return REVISION_SOURCE_TEMPLATE % json.loads(download(REVISION_API, timeout=15))['sha']


def parse_proxies(body):
    try:
        document = yaml.safe_load(body)
    except (yaml.YAMLError, ValueError):
        parsed = parse_flow_fallback(body)
        return parsed or parse_subscription(body)
    if isinstance(document, dict):
        rows = document.get('proxies') or []
    else:
        rows = document if isinstance(document, list) else []
    parsed = [row for row in rows
              if isinstance(row, dict) and row.get('server') and row.get('port')]
    return parsed or parse_subscription(body)


def parse_flow_fallback(body):
    text = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    rows = []
    for chunk in re.split(r'^\s*-\s*(?=\{)', text, flags=re.M):
        if not chunk.lstrip().startswith('{'):
            continue
        row = {}
        for key in FALLBACK_KEYS:
            pattern = r'(?:^|[{,]\s*)' + re.escape(key)
            match = re.search(pattern + r'\s*:\s*("[^"]*"|\'[^\']*\'|[^,}]+)', chunk)
            if not match:
                continue
            value = match.group(1).strip().strip('"\'')
            if key in ('port', 'alterId'):
                try:
                    value = int(value)
                except ValueError:
                    continue
            elif value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            row[key] = value
        if row.get('server') and row.get('port'):
            rows.append(row)
    return rows


def sane(row):
    options = row.get('reality-opts')
    if not isinstance(options, dict):
        return True
    if options.get('public-key') is not None:
        value = str(options['public-key'])
        try:
            decoded = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
            if len(decoded) != 32:
                return False
        except (binascii.Error, ValueError):
            return False
    if options.get('short-id') is not None:
        value = str(options['short-id'])
        if len(value) > 16 or len(value) % 2 or not re.fullmatch(r'[0-9a-fA-F]*', value):
            return False
    return True


def fetch_all():
    stats, nodes, bodies = {}, {}, {}
    urls = list(SOURCES)
    for candidate in aibobox_urls():
        try:
            bodies[candidate] = download(candidate)
            urls.append(candidate)
            break
        except Exception as error:
            stats[candidate] = f'error: {type(error).__name__} {error}'
    if REVISION_API and REVISION_SOURCE_TEMPLATE:
        try:
            urls.append(barabama_url())
        except Exception as error:
            stats['revision_source'] = f'error: {type(error).__name__} {error}'
    fetched, errors = fetch_bodies([source for source in urls if source not in bodies])
    bodies.update(fetched)
    for source, error in errors.items():
        stats[source_label(source)] = error
    for source in urls:
        label = source_label(source)
        if source not in bodies:
            continue
        try:
            rows = parse_proxies(bodies[source])
        except Exception as error:
            stats[label] = f'error: {type(error).__name__} {error}'
            continue
        added = 0
        for row in rows:
            if str(row.get('type', '')).lower() not in SUPPORTED or not sane(row):
                continue
            key = stable_key(row)
            if key not in nodes:
                row['__source'] = label
                nodes[key] = dict(row)
                added += 1
        stats[label] = f'{len(rows)} parsed, {added} added'
    return stats, nodes
