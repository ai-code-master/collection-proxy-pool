"""识别候选内容应进入直连代理池还是 Mihomo 节点管线。"""
from lib.collection.records import source_records
from lib.net import normalize
from mihomo.settings import SUPPORTED
from mihomo.sources import parse_proxies, sane, stable_key


def direct_records(row, body):
    name = row['name'] + '-' + protocol_hint(row)
    valid, invalid = set(), 0
    try:
        records = source_records(name, body)
        for value, _country in records:
            normalized = normalize(value) if isinstance(value, str) else None
            if normalized:
                valid.add(normalized)
            else:
                invalid += 1
    except (ValueError, KeyError, TypeError, AttributeError):
        return set(), 1
    return valid, invalid


def mihomo_records(body):
    try:
        rows = parse_proxies(body)
    except (ValueError, KeyError, TypeError, AttributeError):
        return set(), 1
    valid = {stable_key(row) for row in rows
             if str(row.get('type', '')).lower() in SUPPORTED and sane(row)}
    return valid, max(0, len(rows) - len(valid))


def protocol_hint(row):
    value = ' '.join((row.get('name', ''), row.get('path', ''))).lower()
    if 'socks5' in value:
        return 'socks5'
    if 'http' in value or 'https' in value:
        return 'http'
    return 'mixed'


def unsupported_hint(row, body):
    value = ' '.join((row.get('name', ''), row.get('path', ''), str(body)[:4000])).lower()
    if 'mtproto://' in value or 'proxy-secret' in value:
        return 'mtproto'
    if 'socks4://' in value or 'socks4' in value:
        return 'socks4'
    if 'wireguard' in value:
        return 'wireguard'
    return ''
