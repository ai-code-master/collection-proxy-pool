"""把常见分享 URI/Base64 订阅转换为 Mihomo proxy 字典。"""
import base64
import json
from urllib.parse import parse_qs, unquote, urlsplit


def _b64(value):
    compact = ''.join(value.split()).replace('-', '+').replace('_', '/')
    return base64.b64decode(compact + '=' * (-len(compact) % 4)).decode()


def _first(query, key, default=''):
    return query.get(key, [default])[0]


def _truthy(value):
    return str(value).lower() in {'1', 'true', 'yes'}


def _transport(row, query):
    network = _first(query, 'type', _first(query, 'network', 'tcp'))
    row['network'] = network
    host, path = _first(query, 'host'), unquote(_first(query, 'path'))
    if network == 'ws':
        row['ws-opts'] = {'path': path or '/', 'headers': {'Host': host}} if host else {
            'path': path or '/'}
    elif network == 'grpc':
        row['grpc-opts'] = {'grpc-service-name': _first(query, 'serviceName')}
    security = _first(query, 'security')
    if security in {'tls', 'reality'}:
        row['tls'] = True
    sni = _first(query, 'sni', _first(query, 'servername'))
    if sni:
        row['servername'] = sni
    fingerprint = _first(query, 'fp')
    if fingerprint:
        row['client-fingerprint'] = fingerprint
    alpn = _first(query, 'alpn')
    if alpn:
        row['alpn'] = [item for item in alpn.split(',') if item]
    if _truthy(_first(query, 'allowInsecure')):
        row['skip-cert-verify'] = True
    if security == 'reality':
        row['reality-opts'] = {'public-key': _first(query, 'pbk'),
                               'short-id': _first(query, 'sid')}


def _generic(uri, scheme):
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    if not parsed.hostname or not parsed.port or not parsed.username:
        return None
    secret = unquote(parsed.username)
    row = {'name': unquote(parsed.fragment) or parsed.hostname, 'type': scheme,
           'server': parsed.hostname, 'port': parsed.port, 'udp': True}
    row['uuid' if scheme in {'vless', 'tuic'} else 'password'] = secret
    if scheme == 'vless':
        flow = _first(query, 'flow')
        if flow:
            row['flow'] = flow
        _transport(row, query)
    elif scheme == 'trojan':
        row['tls'] = True
        _transport(row, query)
    elif scheme in {'hysteria2', 'hy2'}:
        row['type'] = 'hysteria2'
        row['sni'] = _first(query, 'sni')
        if _first(query, 'obfs'):
            row['obfs'] = _first(query, 'obfs')
            row['obfs-password'] = _first(query, 'obfs-password')
        row['skip-cert-verify'] = _truthy(_first(query, 'insecure'))
    elif scheme == 'tuic':
        row['password'] = unquote(parsed.password or '')
        row['uuid'] = unquote(parsed.username or '')
        row['sni'] = _first(query, 'sni')
        row['alpn'] = [_first(query, 'alpn', 'h3')]
        row['congestion-controller'] = _first(query, 'congestion_control', 'bbr')
        row['skip-cert-verify'] = _truthy(_first(query, 'allow_insecure'))
    return row


def _vmess(uri):
    data = json.loads(_b64(uri.split('://', 1)[1]))
    row = {'name': data.get('ps') or data['add'], 'type': 'vmess',
           'server': data['add'], 'port': int(data['port']), 'uuid': data['id'],
           'alterId': int(data.get('aid') or 0), 'cipher': data.get('scy') or 'auto',
           'udp': True, 'network': data.get('net') or 'tcp'}
    if data.get('tls'):
        row['tls'] = True
    if data.get('sni'):
        row['servername'] = data['sni']
    if row['network'] == 'ws':
        row['ws-opts'] = {'path': data.get('path') or '/',
                          'headers': {'Host': data.get('host')}}
    elif row['network'] == 'grpc':
        row['grpc-opts'] = {'grpc-service-name': data.get('path') or ''}
    return row


def _ss(uri):
    parsed = urlsplit(uri)
    name = unquote(parsed.fragment) or 'ss'
    if parsed.hostname and parsed.port and parsed.username:
        userinfo = unquote(parsed.username)
        try:
            userinfo = _b64(userinfo)
        except (ValueError, UnicodeError):
            pass
        if ':' not in userinfo:
            return None
        cipher, password = userinfo.split(':', 1)
        return {'name': name, 'type': 'ss', 'server': parsed.hostname,
                'port': parsed.port, 'cipher': cipher, 'password': password, 'udp': True}
    payload = uri.split('://', 1)[1].split('#', 1)[0].split('?', 1)[0]
    decoded = _b64(payload)
    userinfo, address = decoded.rsplit('@', 1)
    cipher, password = userinfo.split(':', 1)
    host, port = address.rsplit(':', 1)
    return {'name': name, 'type': 'ss', 'server': host.strip('[]'), 'port': int(port),
            'cipher': cipher, 'password': password, 'udp': True}


def parse_uri(uri):
    scheme = uri.split('://', 1)[0].lower()
    try:
        if scheme == 'vmess':
            return _vmess(uri)
        if scheme == 'ss':
            return _ss(uri)
        if scheme in {'vless', 'trojan', 'hysteria2', 'hy2', 'tuic'}:
            return _generic(uri, scheme)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        return None
    return None


def parse_subscription(body):
    text = body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body
    if '://' not in text:
        try:
            text = _b64(text)
        except (ValueError, UnicodeError):
            return []
    rows = []
    for line in text.splitlines():
        uri = line.strip()
        if '://' not in uri:
            continue
        row = parse_uri(uri)
        if row and row.get('server') and row.get('port'):
            rows.append(row)
    return rows
