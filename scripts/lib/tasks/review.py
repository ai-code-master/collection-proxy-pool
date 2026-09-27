"""审核候选来源并将合格项自动加入私有来源目录。"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from discovery.review import review
from discovery.sources import write_catalog
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
        configured = ' '.join(json.loads(settings.SOURCES.read_text()).values()).lower()
    except (OSError, ValueError, AttributeError):
        configured = ''
    return rows, known, available, configured


def run(store, config):
    rows, known, available, configured = _context(store, config)
    results = []
    pending = []
    for row in rows:
        repository = (row.get('repository') or '').lower()
        if repository and repository in configured:
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
    approved = [{'name': detail['source_name'], 'url': row['url']}
                for row, state, detail in results if state == 'approved']
    added = write_catalog(settings.SOURCES, approved) if approved else 0
    now = time.time()
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        for row, state, detail in results:
            final = 'active' if state == 'approved' else state
            db.execute('''UPDATE source_candidates SET state=?,reviewed_at=?,review=?
                WHERE url=?''', (final, now, json.dumps(detail), row['url']))
        if added:
            db.execute('''UPDATE scheduled_tasks SET next_run=MIN(next_run,?)
                WHERE name='source_collection' ''', (now,))
    counts = {name: sum(state == name for _, state, _ in results)
              for name in ('approved', 'pending', 'covered', 'rejected')}
    value = {'reviewed': len(results), 'added': added, **counts}
    print(f'来源自动审核：{len(results)} 个，接入 {added} 个，拒绝 {counts["rejected"]} 个', flush=True)
    return value
