import time


def scheduling(store):
    now = time.time()
    with store.connect() as db:
        counts = dict(db.execute('''SELECT
            SUM(CASE WHEN c.state!='auth_required' AND c.next_check<=? THEN 1 ELSE 0 END) due,
            SUM(CASE WHEN c.state!='auth_required' AND c.next_check>? THEN 1 ELSE 0 END) cooling,
            SUM(CASE WHEN h.tier='cold' THEN 1 ELSE 0 END) cold,
            SUM(CASE WHEN h.tier='recovery' THEN 1 ELSE 0 END) recovery,
            SUM(CASE WHEN c.state='auth_required' THEN 1 ELSE 0 END) auth_required
            FROM checks c LEFT JOIN retry_health h ON c.url=h.url AND c.platform=h.platform
            WHERE c.platform='connectivity' ''', (now, now)).fetchone())
        recent = db.execute('''SELECT COUNT(*) checked_10m,
            SUM(CASE WHEN reason NOT LIKE '%prefilter:%' THEN 1 ELSE 0 END) probes_10m
            FROM check_events WHERE platform='connectivity' AND checked_at>? AND origin='probe' ''',
            (now-600,)).fetchone()
        counts.update(dict(recent))
    return {key: value or 0 for key, value in counts.items()}
