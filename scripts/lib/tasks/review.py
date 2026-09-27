"""审核候选来源并将合格项自动加入私有来源目录。"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.review import review
from discovery.sources import write_catalog
from mihomo.settings import CONFIG as MIHOMO_CONFIG, add_sources
from .. import settings


def _context(store, config):
    now = time.time()
    target = config['profiles']['connectivity']['fingerprint']
    with store.connect() as db:
        known = {row[0] for row in db.execute('SELECT url FROM proxies')}
        available = {row[0] for row in db.execute('''SELECT url FROM checks
            WHERE platform='connectivity' AND target=? AND state='available'
            AND checked_at<=? AND valid_until>?''', (target, now, now))}
        rows = [dict(row) for row in db.execute('''SELECT * FROM source_candidates
            WHERE state='pending' ORDER BY discoveries DESC,last_seen DESC LIMIT ?''',
                                                (config['source_review_batch'],))]
    try:
        direct = json.loads(settings.SOURCES.read_text()).values()
        mihomo = json.loads(MIHOMO_CONFIG.read_text()).get('sources', [])
        configured = {url.lower() for url in [*direct, *mihomo]}
    except (OSError, ValueError, AttributeError):
        configured = set()
    return rows, known, available, configured


def run(store, config):
    rows, known, available, configured = _context(store, config)
    results = []
    pending = []
    for row in rows:
        if row['url'].lower() in configured:
            detail = {'attempts': 1, 'reason': 'repository_already_configured',
                      'records': 0, 'novel': 0, 'known': 0, 'known_available': 0,
                      'sampled': 0, 'sample_available': 0}
            results.append((row, 'covered', detail))
        else:
            pending.append(row)
    with ThreadPoolExecutor(max_workers=min(4, config['source_review_workers'])) as executor:
        jobs = {executor.submit(review, row, config, known, available): row for row in pending}
        for job in as_completed(jobs):
            row = jobs[job]
            state, detail = job.result()
            results.append((row, state, detail))
    direct = [{'name': detail['source_name'], 'url': row['url']}
              for row, state, detail in results
              if state == 'approved' and detail.get('route') == 'direct']
    mihomo = [row['url'] for row, state, detail in results
              if state == 'approved' and detail.get('route') == 'mihomo']
    direct_added = write_catalog(settings.SOURCES, direct) if direct else 0
    mihomo_added = add_sources(mihomo, MIHOMO_CONFIG) if mihomo else 0
    added = direct_added + mihomo_added
    now = time.time()
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        for row, state, detail in results:
            final = 'active' if state == 'approved' else state
            db.execute('''UPDATE source_candidates SET state=?,reviewed_at=?,review=?
                WHERE url=?''', (final, now, json.dumps(detail), row['url']))
        if direct_added:
            db.execute('''UPDATE scheduled_tasks SET next_run=MIN(next_run,?)
                WHERE name='source_collection' ''', (now,))
        if mihomo_added:
            db.execute('''UPDATE scheduled_tasks SET next_run=MIN(next_run,?)
                WHERE name='mihomo_refresh' ''', (now,))
    counts = {name: sum(state == name for _, state, _ in results)
              for name in ('approved', 'pending', 'deferred', 'covered', 'rejected')}
    value = {'reviewed': len(results), 'added': added, 'direct_added': direct_added,
             'mihomo_added': mihomo_added, **counts}
    print(f'来源自动审核：{len(results)} 个，接入 {added} 个，拒绝 {counts["rejected"]} 个', flush=True)
    return value
