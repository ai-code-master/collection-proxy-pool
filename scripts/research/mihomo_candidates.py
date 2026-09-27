#!/usr/bin/env python3
"""在隔离 Mihomo 进程中评估候选订阅，不修改正式状态。"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import mihomo.runtime as runtime
from mihomo.probe import probe_lanes
from mihomo.settings import BASE, SUPPORTED
from mihomo.sources import download, load_json, parse_proxies, sane, stable_key


def parse_source(value):
    if '=' not in value:
        raise argparse.ArgumentTypeError('来源格式必须为 label=url')
    label, url = value.split('=', 1)
    if not label or not url.startswith('https://'):
        raise argparse.ArgumentTypeError('label 不能为空且 URL 必须使用 HTTPS')
    return label, url


def existing_keys():
    alive = set(load_json(BASE / 'alive.json', {}).get('nodes', {}))
    queue = set(load_json(BASE / 'queue.json', {}))
    dead = set(load_json(BASE / 'dead.json', {}))
    return alive, alive | queue | dead


def sample_rows(valid, seen, label, limit, offset):
    unseen = [key for key in valid if key not in seen]
    picked = unseen[offset:offset + limit]
    seen.update(picked)
    return [{**valid[key], '__source': label} for key in picked], len(unseen)


def fetch(label, url, alive, seen, limit, offset):
    body = download(url, timeout=90)
    rows = parse_proxies(body)
    valid, selected = {}, []
    for row in rows:
        if str(row.get('type', '')).lower() not in SUPPORTED or not sane(row):
            continue
        key = stable_key(row)
        valid.setdefault(key, row)
    selected, unseen_count = sample_rows(valid, seen, label, limit, offset)
    report = {'url': url, 'bytes': len(body), 'parsed': len(rows),
              'valid_unique': len(valid), 'unseen': unseen_count,
              'alive_overlap': len(set(valid) & alive), 'sample_offset': offset,
              'sampled': len(selected)}
    return report, selected


def isolated_config(selected, base_port, controller):
    runtime.BASE_PORT = base_port
    config, ports = runtime.build_config(selected)
    config['allow-lan'] = False
    config['external-controller'] = controller
    config['listeners'] = [item for item in config['listeners']
                           if item['name'].startswith('lane-')]
    config['rules'] = [rule for rule in config['rules']
                       if not rule.startswith('IN-NAME,lan-')]
    return config, ports


def probe(config, ports):
    with tempfile.TemporaryDirectory(prefix='mihomo-candidate-') as folder:
        candidate = Path(folder) / 'config.yaml'
        dropped = runtime.validate(config, candidate)
        candidate.write_text(runtime.render(config))
        process = subprocess.Popen(
            [runtime.MIHOMO, '-d', folder, '-f', str(candidate)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(3)
            alive = probe_lanes(ports)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    return dropped, alive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='append', type=parse_source, required=True)
    parser.add_argument('--sample-per-source', type=int, default=500)
    parser.add_argument('--sample-offset', type=int, default=0)
    parser.add_argument('--base-port', type=int, default=31001)
    parser.add_argument('--controller', default='127.0.0.1:20199')
    args = parser.parse_args()
    known_alive, seen = existing_keys()
    reports, selected = {}, []
    for label, url in args.source:
        try:
            report, rows = fetch(label, url, known_alive, seen,
                                 args.sample_per_source, args.sample_offset)
            reports[label] = report
            selected.extend(rows)
        except Exception as error:
            reports[label] = {'url': url, 'error': f'{type(error).__name__}: {error}'}
    if not selected:
        print(json.dumps({'sources': reports, 'sampled': 0, 'alive': 0},
                         ensure_ascii=False, indent=2))
        return
    config, ports = isolated_config(selected, args.base_port, args.controller)
    dropped, alive = probe(config, ports)
    labels = {port: label for port, label, _ in ports}
    by_source, latencies, exit_ips, source_rows = {}, [], set(), {}
    for port, result in alive.items():
        label = labels[port]
        by_source[label] = by_source.get(label, 0) + 1
        source_rows.setdefault(label, []).append(result)
        latencies.append(result['latency_ms'])
        if result.get('exit_ip'):
            exit_ips.add(result['exit_ip'])
    latencies.sort()
    source_results = {}
    for label, rows in source_rows.items():
        values = sorted(row['latency_ms'] for row in rows)
        source_results[label] = {
            'alive': len(rows),
            'median_ms': values[len(values) // 2],
            'exit_ips': len({row.get('exit_ip') for row in rows if row.get('exit_ip')}),
        }
    output = {'sources': reports, 'sampled': len(selected),
              'valid_config_nodes': len(config['proxies']), 'dropped': dropped,
              'alive': len(alive), 'by_source': by_source,
              'source_results': source_results,
              'median_ms': latencies[len(latencies) // 2] if latencies else None,
              'exit_ips': len(exit_ips)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
