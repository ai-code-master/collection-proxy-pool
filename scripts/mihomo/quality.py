"""持久化每个订阅来源的跨轮次质量记录。"""
import json
import re


STAT_PATTERN = re.compile(r'^(\d+) parsed, (\d+) added$')


def parse_status(status):
    match = STAT_PATTERN.match(status)
    if match:
        return {'parsed': int(match.group(1)), 'added': int(match.group(2)),
                'error': ''}
    return {'parsed': 0, 'added': 0,
            'error': status[7:] if status.startswith('error: ') else status}


def load_history(path):
    try:
        document = json.loads(path.read_text())
        return document if isinstance(document, dict) else {}
    except (OSError, ValueError):
        return {}


def record_quality(base, stats, alive_by_source, now, exit_ips_by_source=None, keep=48):
    path = base / 'source_quality.json'
    document = load_history(path)
    sources = document.get('sources', {})
    exit_ips_by_source = exit_ips_by_source or {}
    for label, status in stats.items():
        event = {'time': now, **parse_status(status),
                 'alive': alive_by_source.get(label, 0),
                 'exit_ips': exit_ips_by_source.get(label, 0)}
        previous = sources.get(label, {}).get('events', [])
        events = (previous + [event])[-keep:]
        error_streak = 0
        for item in reversed(events):
            if not item.get('error'):
                break
            error_streak += 1
        sources[label] = {
            'events': events,
            'error_streak': error_streak,
            'alive_latest': event['alive'],
            'alive_peak': max(item.get('alive', 0) for item in events),
            'exit_ips_latest': event['exit_ips'],
            'exit_ips_peak': max(item.get('exit_ips', 0) for item in events),
        }
    output = {'updated_at': now, 'sources': sources}
    candidate = path.with_suffix('.json.new')
    candidate.write_text(json.dumps(output, ensure_ascii=False, indent=1))
    candidate.replace(path)
    return output
