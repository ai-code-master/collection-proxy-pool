"""稳定的 v1 客户端 API；与看板接口分离，避免页面改版影响调用方。"""
import itertools
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlsplit

from .proxy_scope import scoped_rows

ROUTES = {'/api/v1/proxies/random': 'random', '/api/v1/proxies': 'list',
          '/api/v1/stats': 'stats'}
TARGETS = {'general': 'connectivity', 'connectivity': 'connectivity'}
PROTOCOLS = {'http', 'socks5'}


def error(code, message, status=400):
    return status, {'error': {'code': code, 'message': message}}, 'json'


def first(query, key, default):
    return query.get(key, [default])[0]


def advertised_host(config, header=''):
    configured = config.get('lan_proxy_host')
    if configured and configured != 'auto':
        return configured
    try:
        host = urlsplit('//' + header).hostname
    except ValueError:
        return None
    return host if host and len(host) <= 253 else None


def timestamp(value):
    if not value:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize(row):
    proxy = row.get('url', row.get('proxy', ''))
    parsed = urlsplit(proxy)
    return {'proxy': proxy, 'scheme': parsed.scheme, 'host': parsed.hostname,
            'port': parsed.port, 'country': row.get('country', 'unknown'),
            'exit_ip': row.get('exit_ip', ''),
            'latency_ms': row.get('latency_ms'),
            'domestic': bool(row.get('domestic')),
            'overseas': bool(row.get('overseas')),
            'checked_at': timestamp(row.get('checked_at')),
            'expires_at': timestamp(row.get('valid_until'))}


def validated(query, lan_host):
    target_name = first(query, 'target', 'general')
    if target_name not in TARGETS:
        raise ValueError('unsupported_target')
    reach = first(query, 'reach', 'any')
    if reach not in ('any', 'both', 'domestic', 'overseas'):
        raise ValueError('unsupported_reach')
    protocol = first(query, 'protocol', '')
    if protocol and protocol not in PROTOCOLS:
        raise ValueError('unsupported_protocol')
    default_scope = 'lan' if lan_host else 'local'
    scope = first(query, 'scope', default_scope)
    if scope not in ('local', 'lan'):
        raise ValueError('invalid_scope')
    return TARGETS[target_name], reach, protocol, scope


def load_rows(store, config, query, rows_provider, lan_host):
    target, reach, protocol, scope = validated(query, lan_host)
    rows = rows_provider(store, config, target, reach)
    rows = scoped_rows(rows, scope, lan_host)
    if rows is None:
        raise ValueError('invalid_scope')
    if protocol:
        rows = [row for row in rows
                if urlsplit(row['url']).scheme.removesuffix('h') == protocol]
    return target, rows


def page_link(path, query, offset):
    values = {key: list(value) for key, value in query.items()}
    values['offset'] = [str(offset)]
    return path + '?' + urlencode(values, doseq=True)


def listing(path, query, target, rows):
    try:
        limit = int(first(query, 'limit', '100'))
        offset = int(first(query, 'offset', '0'))
    except ValueError as exc:
        raise ValueError('invalid_pagination') from exc
    if not 1 <= limit <= 1000 or offset < 0:
        raise ValueError('invalid_pagination')
    values = [normalize(row) for row in rows[offset:offset + limit]]
    if first(query, 'format', 'json') == 'txt':
        return 200, ''.join(row['proxy'] + '\n' for row in values), 'text/plain; charset=utf-8'
    if first(query, 'format', 'json') != 'json':
        raise ValueError('unsupported_format')
    previous = page_link(path, query, max(0, offset - limit)) if offset else None
    next_page = page_link(path, query, offset + limit) if offset + limit < len(rows) else None
    return 200, {'count': len(rows), 'next': next_page, 'previous': previous,
                 'target': target, 'results': values}, 'json'


def stats(store, config):
    value = store.status(config)
    profile = value.get('profiles', {}).get('connectivity', {})
    return {'api_version': 'v1', 'status': 'ok',
            'available': profile.get('available', 0),
            'unique_exit_ips': profile.get('unique_exit_ips', 0),
            'candidates': value.get('candidates', 0),
            'connectivity': value.get('connectivity', {}),
            'updated_at': timestamp(value.get('updated_at'))}


def response(store, config, path, rows_provider, cursor=None, lan_host=None):
    parsed = urlsplit(path)
    action = ROUTES.get(parsed.path)
    if action is None:
        return None
    query = parse_qs(parsed.query)
    try:
        if action == 'stats':
            return 200, stats(store, config), 'json'
        target, rows = load_rows(store, config, query, rows_provider,
                                 lan_host or advertised_host(config))
        if action == 'list':
            return listing(parsed.path, query, target, rows)
        if not rows:
            return error('no_available_proxy', '当前没有符合条件的可用代理', 503)
        position = next(cursor or itertools.count()) % len(rows)
        return 200, {'target': target, **normalize(rows[position])}, 'json'
    except ValueError as exc:
        return error(str(exc), '请求参数不受支持')
