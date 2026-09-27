import time

from discovery.sources import discover


def run(store, config):
    rows = discover(config['discovery_limit'])
    now = time.time()
    with store.write() as db:
        db.execute('BEGIN IMMEDIATE')
        before = db.execute('SELECT COUNT(*) FROM source_candidates').fetchone()[0]
        db.executemany('''INSERT INTO source_candidates(
            url,name,repository,path,first_seen,last_seen) VALUES(?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET name=excluded.name,
            repository=excluded.repository,path=excluded.path,last_seen=excluded.last_seen,
            discoveries=source_candidates.discoveries+1,
            state=CASE WHEN source_candidates.state IN ('rejected','deferred')
                AND excluded.last_seen-COALESCE(source_candidates.reviewed_at,0)>=86400
                THEN 'pending' ELSE source_candidates.state END,
            reviewed_at=CASE WHEN source_candidates.state IN ('rejected','deferred')
                AND excluded.last_seen-COALESCE(source_candidates.reviewed_at,0)>=86400
                THEN NULL ELSE source_candidates.reviewed_at END''', [
                (row['url'], row['name'], row.get('repository'), row.get('path'), now, now)
                for row in rows])
        total = db.execute('SELECT COUNT(*) FROM source_candidates').fetchone()[0]
    result = {'found': len(rows), 'new': total - before, 'pending_total': total}
    print(f'新来源发现：本轮 {len(rows)} 个，新增 {total-before} 个候选', flush=True)
    return result
