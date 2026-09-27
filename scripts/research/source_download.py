"""Fetch public lists without importing candidates or running proxy checks."""
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from source_snapshot import ROOT
from lib.collection.records import source_records
from lib.net import normalize


def alternatives(url):
    values = [url]
    if urlsplit(url).hostname == 'raw.githubusercontent.com':
        owner, repo, branch, path = urlsplit(url).path.strip('/').split('/', 3)
        values.append(f'https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}')
    return values


def download(entry, folder):
    started = time.time()
    attempts = []
    for url in alternatives(entry['url']):
        with tempfile.TemporaryDirectory() as directory:
            body_path, headers_path = Path(directory)/'body', Path(directory)/'headers'
            result = subprocess.run(['/usr/bin/curl', '-q', '-sS', '-L', '--proxy', '',
                '--max-time', '25', '--connect-timeout', '6', '--max-filesize', '4194304',
                '--proto', '=https', '--proto-redir', '=https',
                '-H', 'User-Agent: collection-proxy-pool-source-review',
                '-D', str(headers_path), '-o', str(body_path), '-w', '%{http_code}', url],
                capture_output=True, timeout=28)
            status = int(result.stdout.decode().strip() or 0)
            raw = body_path.read_bytes() if body_path.exists() else b''
            headers = headers_path.read_text(errors='replace') if headers_path.exists() else ''
        attempts.append(dict(url=url, status=status, curl_error=result.returncode))
        if (status == 200 and result.returncode == 0) or status == 429:
            break
    report = dict(entry, started_at=started, fetched_at=time.time(), attempts=attempts,
                  status=status, curl_error=result.returncode, bytes=len(raw))
    for key in ('last-modified', 'date', 'age', 'etag'):
        values = [line.partition(':')[2].strip() for line in headers.splitlines()
                  if line.lower().startswith(key+':')]
        if values:
            report[key] = values[-1]
    nodes, invalid = set(), 0
    if status == 200 and result.returncode == 0:
        try:
            body = raw.decode('utf-8-sig')
            if body.lstrip().lower().startswith(('<!doctype', '<html')):
                raise ValueError('html_not_list')
            parser = entry.get('parser', 'review-' + entry.get('protocol', 'http'))
            for value, _ in source_records(parser, body):
                proxy = normalize(value) if isinstance(value, str) else None
                if proxy:
                    nodes.add(proxy)
                else:
                    invalid += 1
            report['parsed'] = True
        except (ValueError, TypeError, AttributeError, KeyError) as error:
            report.update(parsed=False, parse_error=type(error).__name__)
    else:
        report['parsed'] = False
    report.update(nodes=len(nodes), invalid=invalid, sha256=hashlib.sha256(raw).hexdigest())
    # Persist only normalized endpoints; response bodies may contain unwanted data.
    target = folder / entry['name']
    target.mkdir(parents=True, exist_ok=True)
    (target / 'nodes.json').write_text(json.dumps(sorted(nodes)))
    (target / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
