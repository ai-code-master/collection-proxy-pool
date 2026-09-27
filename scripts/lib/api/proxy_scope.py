from urllib.parse import urlsplit


VALID_SCOPES = {'local', 'lan'}


def scoped_proxy(url, scope, lan_host):
    if scope not in VALID_SCOPES:
        return None
    parsed = urlsplit(url)
    if scope == 'lan' and parsed.hostname in ('127.0.0.1', 'localhost'):
        if not lan_host or parsed.port is None:
            return None
        host = f'[{lan_host}]' if ':' in lan_host else lan_host
        url = parsed._replace(netloc=f'{host}:{parsed.port}').geturl()
    return url.replace('socks5://', 'socks5h://', 1)


def scoped_rows(rows, scope, lan_host):
    if scope not in VALID_SCOPES:
        return None
    return [{**row, 'url': scoped_proxy(row['url'], scope, lan_host)} for row in rows]
