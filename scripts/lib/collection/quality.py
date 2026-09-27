import time


def enrich(store, reports, config):
    now = time.time()
    target = config['profiles']['connectivity']['fingerprint']
    with store.connect() as db:
        values = {r['name']: dict(r) for r in db.execute('''SELECT j.value name,COUNT(*) stored_candidates,
            SUM(CASE WHEN c.state='available' AND c.target=? AND c.checked_at<=? AND c.valid_until>?
                THEN 1 ELSE 0 END) available,
            SUM(CASE WHEN h.target=? AND h.last_success>=? THEN 1 ELSE 0 END) verified_24h
            FROM proxies p JOIN json_each(COALESCE(p.sources,'[]')) j
            LEFT JOIN checks c ON c.url=p.url AND c.platform='connectivity'
            LEFT JOIN retry_health h ON h.url=p.url AND h.platform='connectivity'
            GROUP BY j.value''', (target, now, now, target, now-86400))}
        queued = {r['name']: r['queued_candidates'] for r in db.execute('''
            SELECT j.value name,COUNT(*) queued_candidates FROM candidate_queue q,
            json_each(COALESCE(q.sources,'[]')) j GROUP BY j.value''')}
    for report in reports:
        value = values.get(report.get('name'), {})
        report.update({key: value.get(key, 0) for key in ('stored_candidates', 'available', 'verified_24h')})
        report['queued_candidates'] = queued.get(report.get('name'), 0)
    return reports
