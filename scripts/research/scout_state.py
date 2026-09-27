"""自动找源的已见节点与失败来源冷却状态。"""
import json
import time


def known_keys(base):
    keys = set()
    for name in ('alive.json', 'queue.json', 'dead.json'):
        try:
            document = json.loads((base / name).read_text())
            keys.update(document.get('nodes', {}) if name == 'alive.json' else document)
        except (OSError, ValueError, TypeError):
            pass
    return keys


def active_rejections(path, now=None):
    now = time.time() if now is None else now
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return set()
    return {url for url, until in document.items()
            if isinstance(url, str) and isinstance(until, (int, float)) and until > now}


def remember_rejections(path, urls, now=None, ttl=24 * 3600):
    now = time.time() if now is None else now
    try:
        document = json.loads(path.read_text())
        document = document if isinstance(document, dict) else {}
    except (OSError, ValueError, TypeError):
        document = {}
    document = {url: until for url, until in document.items()
                if isinstance(until, (int, float)) and until > now}
    document.update({url: now + ttl for url in urls})
    candidate = path.with_suffix('.json.new')
    candidate.write_text(json.dumps(document, ensure_ascii=False, indent=1))
    candidate.replace(path)
