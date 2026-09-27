"""编排订阅刷新、节点轮测、状态持久化。"""
import fcntl
import json
import random
import statistics
import time

from .probe import probe_lanes
from .quality import record_quality
from .runtime import build_config, validate_and_reload
from .settings import (BASE, BASE_PORT, DEAD_TTL, NEW_BATCH, PRIORITY_SOURCES,
                       QUEUE_CAP, RESERVE_CAP, RESERVE_RATIO, SEEN_TTL)
from .sources import fetch_all, load_json, sane, stable_key


def priority_source(label):
    return any(label == source or label.startswith(source + '#')
               for source in PRIORITY_SOURCES)


def select_nodes(nodes, previous, dead, queue, now):
    priority = {}
    for key, row in nodes.items():
        if key not in previous and key not in dead:
            entry = {'row': row, 'seen': now}
            if priority_source(row.get('__source', '')):
                queue.pop(key, None)
                priority[key] = entry
            else:
                queue[key] = entry
    queue = {**priority, **queue}
    while len(queue) > QUEUE_CAP:
        queue.pop(random.choice(list(queue)))
    retained = []
    for key, meta in previous.items():
        row = nodes.get(key)
        if row is not None and isinstance(meta, dict) and meta.get('port'):
            retained.append((key, {**row, '__port': int(meta['port'])}))
    batch_keys = list(queue)[:NEW_BATCH]
    selected = [row for _, row in retained] + [queue[key]['row'] for key in batch_keys]
    return queue, retained, batch_keys, selected


def update_queue(queue, dead, retained, batch_keys, alive_keys, now):
    for key in batch_keys:
        queue.pop(key, None)
        if key not in alive_keys:
            dead[key] = now
    retry = {}
    for key, row in retained:
        if key in alive_keys:
            continue
        if queue.pop(key, {}).get('retry'):
            dead[key] = now
        else:
            retry[key] = {'row': row, 'seen': now, 'retry': True}
    return {**retry, **queue}


def slim_config(selected, ports, alive_keys):
    key_to_port = {key: port for port, _, key in ports}
    key_to_row = {stable_key(row): row for row in selected}
    failed = [key for _, _, key in ports if key not in alive_keys]
    random.shuffle(failed)
    reserve_size = min(len(alive_keys) // RESERVE_RATIO, RESERVE_CAP)
    reserve_keys = failed[:reserve_size]
    slim = []
    for key in list(alive_keys) + reserve_keys:
        slim.append({**key_to_row[key], '__port': key_to_port[key]})
    if not slim:
        return random.sample(ports, min(200, len(ports)))
    config, slim_ports = build_config(slim)
    validate_and_reload(config)
    return slim_ports


def write_outputs(stats, alive, ports, slim_ports, retained, queue, dead, now):
    port_meta = {port: (source, key) for port, source, key in ports}
    ordered = sorted(alive, key=lambda port: alive[port]['latency_ms'])
    alive_keys = {port_meta[port][1] for port in ordered}
    alive_ports = [port for port, _, key in slim_ports if key in alive_keys]
    reserve_ports = [port for port, _, key in slim_ports if key not in alive_keys]
    exported = sorted(alive_ports, key=lambda port: alive[port]['latency_ms'])
    lines = [f'http://127.0.0.1:{port}' for port in exported + reserve_ports]
    (BASE / 'nodes.txt').write_text('\n'.join(lines) + '\n')
    document = {'probed_at': now, 'nodes': {}}
    for port in ordered:
        source, key = port_meta[port]
        document['nodes'][key] = {
            'port': port, 'source': source, 'exit_ip': alive[port].get('exit_ip'),
            'latency_ms': alive[port]['latency_ms'], 'last_alive': now,
        }
    (BASE / 'alive.json').write_text(json.dumps(document, ensure_ascii=False, indent=1))
    (BASE / 'queue.json').write_text(json.dumps(queue, ensure_ascii=False))
    (BASE / 'dead.json').write_text(json.dumps(dead, ensure_ascii=False))
    by_source, source_exit_ips = {}, {}
    for port in ordered:
        source = port_meta[port][0]
        by_source[source] = by_source.get(source, 0) + 1
        if alive[port].get('exit_ip'):
            source_exit_ips.setdefault(source, set()).add(alive[port]['exit_ip'])
    exit_ips_by_source = {source: len(values)
                          for source, values in source_exit_ips.items()}
    record_quality(BASE, stats, by_source, now, exit_ips_by_source)
    latencies = [alive[port]['latency_ms'] for port in ordered]
    unique_exit_ips = len({row.get('exit_ip') for row in alive.values()
                           if row.get('exit_ip')})
    meta = {
        'refreshed_at': now, 'nodes': len(slim_ports),
        'ports': [BASE_PORT, max(port for port, _, _ in slim_ports)] if slim_ports else [],
        'sources': stats,
        'alive': {'total': len(ordered), 'reserve': len(reserve_ports),
                  'retained': len(retained), 'tested': len(ports), 'queue': len(queue),
                  'dead': len(dead),
                  'median_ms': round(statistics.median(latencies)) if latencies else None,
                  'unique_exit_ips': unique_exit_ips,
                  'by_source': by_source, 'exit_ips_by_source': exit_ips_by_source},
    }
    (BASE / 'refresh_meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False, indent=2))


def run():
    now = time.time()
    stats, nodes = fetch_all()
    previous = load_json(BASE / 'alive.json', {}).get('nodes', {})
    dead = {key: stamp for key, stamp in load_json(BASE / 'dead.json', {}).items()
            if now - stamp < DEAD_TTL}
    queue = {key: entry for key, entry in load_json(BASE / 'queue.json', {}).items()
             if isinstance(entry, dict) and isinstance(entry.get('row'), dict)
             and sane(entry['row']) and now - entry.get('seen', 0) < SEEN_TTL}
    queue, retained, batch_keys, selected = select_nodes(
        nodes, previous, dead, queue, now)
    config, ports = build_config(selected)
    if not validate_and_reload(config):
        return
    time.sleep(3)
    alive = probe_lanes(ports)
    port_meta = {port: (source, key) for port, source, key in ports}
    alive_keys = {port_meta[port][1] for port in alive}
    queue = update_queue(queue, dead, retained, batch_keys, alive_keys, now)
    slim_ports = slim_config(selected, ports, alive_keys)
    write_outputs(stats, alive, ports, slim_ports, retained, queue, dead, now)


def main():
    random.seed()
    BASE.mkdir(exist_ok=True)
    with (BASE / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('another refresh is running, exit')
            return
        run()
