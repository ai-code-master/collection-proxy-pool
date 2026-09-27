"""Read public proxy lists without importing authenticated endpoints."""
import json


def _country(row):
    value = row.get('country') or row.get('geolocation', {}).get('country')
    return value.get('iso_code') if isinstance(value, dict) else value


def _json_records(document):
    rows = document.get('data') if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError('invalid_source')
    for row in rows:
        try:
            if any(row.get(key) not in (None, '') for key in ('username', 'password')):
                continue
            if row.get('proxy'):
                yield row['proxy'], _country(row)
                continue
            protocol = row.get('protocol')
            if not protocol:
                protocols = row.get('protocols') or []
                protocol = ('http' if 'http' in protocols else
                            'socks5' if 'socks5' in protocols else
                            'socks4' if 'socks4' in protocols else None)
            host = row.get('host', row.get('ip'))
            if not protocol or not host:
                yield None, None
                continue
            host = f'[{host}]' if ':' in host and not host.startswith('[') else host
            yield f'{protocol}://{host}:{row["port"]}', _country(row)
        except (KeyError, TypeError, AttributeError):
            yield None, None


def source_records(name, body):
    try:
        document = json.loads(body)
    except json.JSONDecodeError:
        document = None
    if document is not None:
        yield from _json_records(document)
        return
    protocol = name.rsplit('-', 1)[-1]
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        yield line if '://' in line else f'{protocol}://{line.strip()}', None
