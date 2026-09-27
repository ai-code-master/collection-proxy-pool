"""候选来源的自动审核规则；通过后才允许进入私有来源目录。"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

from lib import checks
from lib.collection.transport import download
from .routes import direct_records, mihomo_records, protocol_hint, unsupported_hint


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
    suffix = protocol if protocol in ('http', 'socks4', 'socks5') else 'mixed'
    value = re.sub(r'[^A-Za-z0-9._-]+', '-', row['name']).strip('-')
    return value if value.lower().endswith('-' + suffix) else value + '-' + suffix


def review(row, config, known, available, downloader=download, checker=checks.check):
    previous = json.loads(row.get('review') or '{}')
    attempts = int(previous.get('attempts', 0)) + 1
    protocol = protocol_hint(row)
    base = {'attempts': attempts, 'protocol': protocol, 'route': '', 'records': 0, 'novel': 0,
            'known': 0, 'known_available': 0, 'sampled': 0, 'sample_available': 0}
    response = downloader(row['url'], previous.get('transport'))
    base['transport'] = {key: response.get(key) for key in
                         ('fetch_url', 'http_status', 'error', 'failures', 'next_fetch')}
    if response.get('status') != 200 or response.get('error'):
        return 'pending', dict(base, reason='download_failed')
    body = response.get('body', '')
    valid, invalid = direct_records(row, body)
    mihomo, mihomo_invalid = mihomo_records(body)
    if len(mihomo) >= config['source_review_min_records'] and len(mihomo) > len(valid):
        base.update(route='mihomo', protocol='mihomo', records=len(mihomo),
                    invalid=mihomo_invalid, novel=len(mihomo))
        if row['discoveries'] < 2:
            return 'pending', dict(base, reason='awaiting_rediscovery')
        return 'approved', dict(base, reason='quality_gate_passed')
    novel, known_rows = valid-known, valid & known
    base.update(route='direct', records=len(valid), invalid=invalid, novel=len(novel),
                known=len(known_rows),
                known_available=len(valid & available))
    if len(valid) < config['source_review_min_records']:
        unsupported = unsupported_hint(row, body)
        if unsupported:
            return 'deferred', dict(base, route='deferred', protocol=unsupported,
                                    reason='engine_protocol_not_supported')
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
