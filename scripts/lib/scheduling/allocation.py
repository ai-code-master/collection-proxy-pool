"""各等级拥有独立到期队列和基础名额；未使用名额按维护优先级借用。"""
from collections import deque
from .grading import EXPRESSION, QUOTAS


def select(db, platform, target, limit, now, quotas=None, max_retry_ratio=None):
    quotas=quotas or QUOTAS
    sql=f'''SELECT p.*,c.state,c.checked_at,c.valid_until,c.next_check,{EXPRESSION} grade,
        COALESCE(h.tier,'retry') retry_tier
        FROM proxies p LEFT JOIN checks c ON p.url=c.url AND c.platform=?
        LEFT JOIN reliability q ON q.url=c.url AND q.platform=c.platform AND q.target=c.target
        LEFT JOIN retry_health h ON h.url=c.url AND h.platform=c.platform AND h.target=c.target
        WHERE p.last_seen>?
        AND (c.state IS NULL OR c.state!='auth_required')
        AND NOT EXISTS(SELECT 1 FROM authenticated_proxies a WHERE a.url=p.url)
        AND (c.url IS NULL OR c.next_check<=? OR c.target!=?)'''
    args=[target,platform,now-604800,now,target]
    # 单次扫描按等级与冷队列分桶编号，替代每等级一遍全量连接查询。
    # 回环节点（本机 mihomo 出口）已经过上自测、质量高，排在各自桶最前，
    # 避免被数万条公开候选的 FIFO 长队饿死（实测排队 14 小时轮不到）。
    rows=db.execute(f'''SELECT * FROM (SELECT *,ROW_NUMBER() OVER (
        PARTITION BY grade,(retry_tier='cold')
        ORDER BY CASE WHEN state='available' AND valid_until<=strftime('%s','now')
                      THEN 0 ELSE 1 END,
        CASE WHEN retry_tier='recovery' THEN 0 ELSE 1 END,
        CASE WHEN url LIKE 'http://127.0.0.1:%' OR url LIKE 'http://localhost:%' THEN 0 ELSE 1 END,
        COALESCE(next_check,0),first_seen,url) rn FROM ({sql}))
        WHERE rn<=CASE WHEN retry_tier='cold' THEN ? ELSE ? END
        ORDER BY grade,(retry_tier='cold'),rn''',
        args+[max(1,limit//20),limit]).fetchall()
    queues={grade:deque() for grade in QUOTAS}
    for row in rows:
        queues[row['grade']].append(dict(row))
    budget={grade:max(1 if limit>=5 else 0,int(limit*quotas[grade]/100)) for grade in QUOTAS}
    while sum(budget.values())>limit:
        largest=max(budget,key=budget.get)
        budget[largest]-=1
    chosen={grade:deque() for grade in QUOTAS}
    for grade in QUOTAS:
        for _ in range(min(budget[grade],len(queues[grade]))):
            chosen[grade].append(queues[grade].popleft())
    remaining=limit-sum(map(len,chosen.values()))
    if max_retry_ratio is not None:
        retry_cap=max(1, int(limit*max_retry_ratio))
        if len(chosen['E']) > retry_cap:
            remaining += len(chosen['E']) - retry_cap
            chosen['E']=deque(list(chosen['E'])[:retry_cap])
        # E 类只允许使用明确保留的名额，空出的名额给 A/B/D 类复用。
        queues['E']=deque(list(queues['E'])[:max(0, retry_cap-len(chosen['E']))])
    fill_grades = [grade for grade in QUOTAS
                   if max_retry_ratio is None or grade != 'E']
    for grade in fill_grades:
        take=min(remaining,len(queues[grade]))
        chosen[grade].extend(queues[grade].popleft() for _ in range(take))
        remaining-=take
    # 交错提交，避免重试名额虽然分到了却总等到整批末尾。
    rows=[]
    while any(chosen.values()):
        for grade in QUOTAS:
            if chosen[grade]:
                rows.append(chosen[grade].popleft())
    return rows
