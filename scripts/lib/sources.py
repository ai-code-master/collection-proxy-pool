import json
import os
from pathlib import Path
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from .settings import ROOT
from .collection.transport import download
from .collection.records import source_records
from .net import normalize, is_loopback_url


def fetch_source(url, previous=None):
    return download(url, previous)


def catalog_path():
    explicit = os.environ.get('PROXY_POOL_SOURCES')
    if explicit:
        return Path(explicit).expanduser()
    local = ROOT / 'sources.json'
    return local if local.exists() else ROOT / 'examples/sources.json'


def collect(previous=None, on_batch=None):
    previous = {row['name']: {key: value for key, value in row.items() if not key.startswith('credentials_')}
                for row in previous or [] if 'name' in row}
    source_map = json.loads(catalog_path().read_text())
    for url in source_map.values():
        # 回环 HTTP 来源（本机 mihomo 出口列表等本地可信服务）例外放行，其余来源仍必须 HTTPS。
        if urlsplit(url).scheme != "https" and not (urlsplit(url).scheme == "http" and is_loopback_url(url)):
            raise ValueError("来源必须为 HTTPS 地址")
    candidates, reports = {}, []
    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs = {}
        for name, url in source_map.items():
            old = previous.get(name, {})
            if old.get('next_fetch', 0) > time.time():
                reports.append(dict(old, deferred=True, accepted=0, new_candidates=0, invalid_records=0))
            else:
                jobs[executor.submit(fetch_source, url, old)] = (name, url)
        for job in as_completed(jobs):
            name, url = jobs[job]
            response = job.result()
            accepted, invalid = 0, 0
            batch = []
            if response['status'] == 200 and not response['error']:
                try:
                    lines = list(source_records(name, response['body']))
                except (ValueError, KeyError, TypeError, AttributeError):
                    lines = []
                    response['error'] = 'invalid_source'
                for line, country in lines:
                    # 只有回环来源才允许回环节点入库，公开来源仍只收公网地址。
                    proxy = normalize(line, allow_loopback=is_loopback_url(url)) if isinstance(line, str) else None
                    if not proxy:
                        invalid += 1
                        continue
                    accepted += 1
                    row = candidates.setdefault(proxy, {'proxy': proxy, 'sources': []})
                    row['sources'].append(name)
                    if country:
                        row['source_country'] = country
                    batch.append(dict(proxy=proxy, sources=[name], source_country=country))
            added = on_batch(batch) if on_batch and batch else 0
            report = {'name': name, 'url': url, 'accepted': accepted, 'new_candidates': added or 0,
                      'http_status': response['status'], 'error': response['error'], 'deferred': False,
                      'invalid_records': invalid}
            report.update({key: response[key] for key in
                           ('fetch_url', 'attempts', 'checked_at', 'next_fetch', 'failures') if key in response})
            reports.append(report)
    rows = list(candidates.values())
    return rows, sorted(reports, key=lambda row: list(source_map).index(row['name']))
