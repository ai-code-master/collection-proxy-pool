"""调度等级与瞬时可用性分开。"""
import time

QUOTAS = {'A': 15, 'B': 15, 'D': 55, 'E': 15}
NAMES = {'A': '稳定节点', 'B': '观察节点', 'D': '待检测/复核', 'E': '失败重试'}
EXPRESSION = """CASE WHEN c.url IS NULL THEN 'D' WHEN c.target!=? THEN 'D' WHEN c.state='available' THEN
    CASE WHEN COALESCE(q.successes,0)>=3 THEN 'A' ELSE 'B' END ELSE 'E' END"""


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS reliability(
        url TEXT,platform TEXT,target TEXT,successes INTEGER,last_qualified REAL,
        PRIMARY KEY(url,platform))''')
    if db.execute("SELECT 1 FROM meta WHERE key='grading_v1'").fetchone():
        return
    # 旧事件包含迟到结果；只从当前记录补一个成功，不推算历史稳定等级。
    db.execute('''INSERT OR IGNORE INTO reliability SELECT url,platform,target,
        CASE WHEN state='available' THEN 1 ELSE 0 END,
        CASE WHEN state='available' THEN checked_at ELSE 0 END FROM checks''')
    db.execute("UPDATE checks SET next_check=MIN(next_check,checked_at+300) WHERE state='available'")
    db.execute('INSERT INTO meta VALUES(?,?)',('grading_v1',str(time.time())))


def advance(db, url, platform, target, result):
    row=db.execute('SELECT * FROM reliability WHERE url=? AND platform=?',(url,platform)).fetchone()
    same=row is not None and row['target']==target
    count,last=(row['successes'],row['last_qualified']) if same else (0,0)
    if result['state']!='available':
        count,last=0,0
    elif not count or result['checked_at']-last>=300:
        count,last=min(3,count+1),result['checked_at']
    db.execute('INSERT OR REPLACE INTO reliability VALUES(?,?,?,?,?)',(url,platform,target,count,last))
    return count
