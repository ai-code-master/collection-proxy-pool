"""候选来源的自动审核规则；通过后才允许进入私有来源目录。"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from lib import checks
from lib.collection.records import source_records
from lib.collection.transport import download
from lib.net import normalize


UNSUPPORTED = re.compile(r'(socks4|mtproto|telegram|vless|vmess|trojan|wireguard)', re.I)


def _protocol(row):
    value = ' '.join((row.get('name', ''), row.get('path', ''))).lower()
    if UNSUPPORTED.search(value):
        return 'unsupported'
    if 'socks5' in value:
        return 'socks5'
    if 'http' in value or 'https' in value:
        return 'http'
    return 'mixed'


def _records(row, body, protocol):
    parser_name = row['name'] + '-' + protocol
    valid, invalid = set(), 0
    try:
        records = source_records(parser_name, body)
        for value, _country in records:
            normalized = normalize(value) if isinstance(value, str) else None
            if normalized:
                valid.add(normalized)
            else:
                invalid += 1
    except (ValueError, KeyError, TypeError, AttributeError):
        return set(), 1
    return valid, invalid


def _sample(urls, profile, size, workers, checker):
    if not profile.get('enabled') or not urls:
        return 0, 0
    selected = sorted(urls)[:size]

    def run(url):
        try:
            return checker(url, 'connectivity', profile).get('state') == 'available'
        except (OSError, ValueError, KeyError, TypeError):
            return False

    with ThreadPoolExecutor(max_workers=workers) as executor:
        passed = sum(executor.map(run, selected))
    return len(selected), passed


def source_name(row, protocol):
    suffix = protocol if protocol in ('http', 'socks5') else 'mixed'
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', row['name']).strip('-')
    return value if value.lower().endswith('-' + suffix) else value + '-' + suffix


def review(row, config, known, available, downloader=download, checker=checks.check):
    previous = json.loads(row.get('review') or '{}')
    attempts = int(previous.get('attempts', 0)) + 1
    protocol = _protocol(row)
    base = {'attempts': attempts, 'protocol': protocol, 'records': 0, 'novel': 0,
            'known': 0, 'known_available': 0, 'sampled': 0, 'sample_available': 0}
    if protocol == 'unsupported':
        return 'rejected', dict(base, reason='unsupported_protocol')
    response = downloader(row['url'], previous.get('transport'))
    base['transport'] = {key: response.get(key) for key in
                         ('fetch_url', 'http_status', 'error', 'failures', 'next_fetch')}
    if response.get('status') != 200 or response.get('error'):
        return 'pending', dict(base, reason='download_failed')
    valid, invalid = _records(row, response.get('body', ''), protocol)
    novel, known_rows = valid-known, valid & known
    base.update(records=len(valid), invalid=invalid, novel=len(novel), known=len(known_rows),
                known_available=len(valid & available))
    if len(valid) < config['source_review_min_records']:
        state = 'rejected' if attempts >= 2 else 'pending'
        return state, dict(base, reason='too_few_records')
    if invalid > len(valid):
        return 'rejected', dict(base, reason='invalid_format')
    if len(novel) < config['source_review_min_novel']:
        return 'covered', dict(base, reason='no_meaningful_novelty')
    if row['discoveries'] < 2:
        return 'pending', dict(base, reason='awaiting_rediscovery')
    sampled, passed = (0, 0)
    if base['known_available'] < config['source_review_min_success']:
        sampled, passed = _sample(novel, config['profiles']['connectivity'],
                                  config['source_review_sample_size'],
                                  config['source_review_workers'], checker)
    base.update(sampled=sampled, sample_available=passed)
    evidence = base['known_available'] + passed
    if evidence < config['source_review_min_success']:
        state = 'rejected' if attempts >= 3 else 'pending'
        return state, dict(base, reason='insufficient_live_evidence')
    return 'approved', dict(base, reason='quality_gate_passed',
                            source_name=source_name(row, protocol))
