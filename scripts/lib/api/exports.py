import base64
import csv
import io
import json
import os
import tempfile
import zipfile
from urllib.parse import urlsplit

from ..settings import DATA
from ..policy.unsupported import endpoints

EXPORT_FORMATS = {
    'proxies.txt': 'txt',
    'proxies.uri': 'uri',
    'proxies.base64': 'base64',
    'proxies.csv': 'csv',
    'clash.yaml': 'clash',
    'provider.yaml': 'provider',
    'records.json': 'json',
}


def available(store, config, platform, reach='any'):
    target = config['profiles'][platform]
    private = endpoints(store)
    return [r for r in store.available(platform, target['fingerprint'], reach=reach)
            if r['url'] not in private] if target['enabled'] else []


def render(rows, target, kind, platform):
    nodes = []
    for row in rows:
        parsed = urlsplit(row['url'])
        nodes.append({'name': f'{row["country"]}-{parsed.scheme}-{parsed.hostname}-{parsed.port}',
                      'type': parsed.scheme, 'server': parsed.hostname, 'port': parsed.port})
    if kind in ('txt', 'uri'):
        return ''.join(row['url'] + '\n' for row in rows), 'text/plain; charset=utf-8'
    if kind == 'base64':
        payload = ''.join(row['url'] + '\n' for row in rows).encode()
        return base64.b64encode(payload).decode() + '\n', 'text/plain; charset=utf-8'
    if kind == 'csv':
        stream = io.StringIO()
        fields = ['url', 'state', 'exit_ip', 'country', 'geo_checked', 'latency_ms',
                  'domestic', 'domestic_latency_ms',
                  'overseas', 'overseas_latency_ms', 'checked_at', 'valid_until']
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue(), 'text/csv; charset=utf-8'
    if kind == 'provider':
        value = {'proxies': nodes}
    elif kind == 'clash':
        group = 'CONNECTIVITY'
        value = {'mixed-port': 21993, 'allow-lan': False, 'bind-address': '127.0.0.1',
                 'mode': 'rule', 'proxies': nodes,
                 'proxy-groups': [{'name': group, 'type': 'select',
                                   'proxies': [n['name'] for n in nodes] or ['REJECT']}],
                 'rules': [f'MATCH,{group}']}
    elif kind == 'json':
        value = {'purpose': 'proxy_pool', 'profile': platform, 'proxies': rows}
    else:
        raise KeyError(kind)
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'application/json; charset=utf-8'


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == content:
        return
    handle, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def export(store, config, folder=None):
    folder = folder or DATA / 'exports'
    for platform, target in config['profiles'].items():
        rows = available(store, config, platform)
        for name, kind in EXPORT_FORMATS.items():
            atomic(folder / platform / name, render(rows, target, kind, platform)[0])
    atomic(folder / 'status.json', json.dumps(store.status(config), ensure_ascii=False, indent=2))
    atomic(folder / 'marks.json', json.dumps(store.records(config), ensure_ascii=False, indent=2))
    temporary = folder / '.bundle.tmp'
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob('*'):
            if path.is_file() and path.name not in ('.bundle.tmp', 'collection-proxy-pool.zip'):
                archive.write(path, str(path.relative_to(folder)))
    os.replace(temporary, folder / 'collection-proxy-pool.zip')
