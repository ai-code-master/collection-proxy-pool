"""通过代理访问 IP 回显站，获取真实出口标识。"""
import ipaddress
import re
from urllib.parse import urlsplit

from ..net import fetch


def public_ip(value):
    try:
        address = ipaddress.ip_address(value.strip())
        return address.compressed if address.is_global else ''
    except ValueError:
        return ''


def parse_trace(body, provider='trace'):
    values = dict(line.split('=', 1) for line in body.splitlines() if '=' in line)
    address = public_ip(values.get('ip', ''))
    if not address:
        return None
    country = values.get('loc', '').upper()
    if not re.fullmatch(r'[A-Z]{2}', country) or country == 'XX':
        country = 'unknown'
    details = {'provider': provider, 'colo': values.get('colo', ''),
               'http': values.get('http', ''), 'tls': values.get('tls', ''),
               'warp': values.get('warp', '')}
    return {'exit_ip': address, 'country': country, 'exit_info': details}


def probe(proxy, targets=(), timeout=8, fetcher=fetch):
    for target in targets:
        response = fetcher(target['url'], proxy, timeout=timeout, headers=('Accept: text/plain',))
        if response['status'] != 200 or response['error']:
            continue
        provider = urlsplit(target['url']).hostname or 'configured'
        if target['format'] == 'trace':
            identity = parse_trace(response['body'], provider)
        else:
            address = public_ip(response['body'])
            identity = ({'exit_ip': address, 'country': 'unknown',
                         'exit_info': {'provider': provider}} if address else None)
        if identity:
            return identity
    return None
