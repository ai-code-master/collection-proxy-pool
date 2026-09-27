"""生成 Mihomo lane 配置、校验并热重载。"""
import json
import re
import subprocess
import urllib.request

import yaml

from .settings import BASE, BASE_PORT, CONTROLLER, LAN_BIND_HOST
from .sources import stable_key

MIHOMO = '/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo'
QUOTED_FIELDS = re.compile(
    r'(?m)^(\s*(?:short-id|password|uuid|public-key|sni|host|path|serviceName|'
    r'service-name): )([^"\x27\s][^\s]*)$')


def without_rotating_gateway(config):
    """移除旧版统一轮换入口，保留每节点独立端口。"""
    config['listeners'] = [row for row in config.get('listeners', [])
                           if row.get('name') not in ('rotate-local', 'rotate-lan')]
    config['proxy-groups'] = [row for row in config.get('proxy-groups', [])
                              if row.get('name') != 'ROTATE']
    config['rules'] = [rule for rule in config.get('rules', [])
                       if not rule.endswith(',ROTATE')]
    return config


def build_config(selected):
    used = {row['__port'] for row in selected if '__port' in row}
    listeners, proxies, groups, rules, ports = [], [], [], [], []
    next_port = BASE_PORT
    for original in selected:
        row = dict(original)
        if '__port' in row:
            port = row['__port']
        else:
            while next_port in used:
                next_port += 1
            port = next_port
            used.add(port)
        name = f'free-{port}'
        source = row.pop('__source', '')
        row.pop('__port', None)
        row['name'] = name
        row.pop('nameserver', None)
        listeners.extend([
            {'name': f'lane-{port}', 'type': 'mixed', 'listen': '127.0.0.1',
             'port': port, 'udp': True},
            {'name': f'lan-{port}', 'type': 'mixed', 'listen': LAN_BIND_HOST,
             'port': port, 'udp': True},
        ])
        proxies.append(row)
        groups.append({'name': f'LANE-{port}', 'type': 'select', 'proxies': [name]})
        rules.extend([f'IN-NAME,lane-{port},LANE-{port}',
                      f'IN-NAME,lan-{port},LANE-{port}'])
        ports.append((port, source, stable_key(row)))
    rules.append('MATCH,DIRECT')
    config = {'allow-lan': True, 'log-level': 'warning', 'mode': 'rule',
              'external-controller': CONTROLLER, 'secret': '', 'listeners': listeners,
              'proxies': proxies, 'proxy-groups': groups, 'rules': rules}
    return config, ports


def drop_invalid_proxy(config, diagnostic):
    match = re.search(r'proxy (\d+):', diagnostic, re.IGNORECASE)
    if not match:
        return None
    reported = int(match.group(1))
    candidates = [index for index in (reported, reported - 1)
                  if 0 <= index < len(config['proxies'])]
    index = candidates[0] if candidates else None
    for candidate in candidates:
        row = config['proxies'][candidate]
        if (str(row.get('server', '')) in diagnostic
                and str(row.get('port', '')) in diagnostic):
            index = candidate
            break
    if index is None:
        return None
    row = config['proxies'].pop(index)
    groups = {group['name'] for group in config['proxy-groups']
              if row['name'] in group.get('proxies', [])}
    config['proxy-groups'] = [group for group in config['proxy-groups']
                              if group['name'] not in groups]
    removed = [rule for rule in config['rules']
               if any(rule.endswith(',' + group) for group in groups)]
    listeners = {rule.split(',', 2)[1] for rule in removed
                 if rule.startswith('IN-NAME,')}
    config['rules'] = [rule for rule in config['rules'] if rule not in removed]
    config['listeners'] = [item for item in config['listeners']
                           if item.get('name') not in listeners]
    return row['name']


def render(config):
    document = yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=4096)
    return QUOTED_FIELDS.sub(lambda match: match.group(1) + '"' + match.group(2) + '"',
                             document)


def validate(config, candidate):
    dropped = []
    for _ in range(100):
        candidate.write_text(render(config))
        check = subprocess.run([MIHOMO, '-t', '-d', str(BASE), '-f', str(candidate)],
                               capture_output=True, text=True, timeout=60)
        if check.returncode == 0:
            return dropped
        diagnostic = check.stderr or check.stdout
        removed = drop_invalid_proxy(config, diagnostic)
        if removed is None:
            raise ValueError(diagnostic[-300:])
        dropped.append(removed)
    raise ValueError('too many invalid proxies')


def validate_and_reload(config):
    candidate = BASE / 'config.yaml.new'
    try:
        dropped = validate(config, candidate)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        candidate.unlink(missing_ok=True)
        print('config validation failed, keep old config:', error)
        return False
    if dropped:
        print('dropped invalid proxies:', len(dropped), dropped[:5])
    candidate.replace(BASE / 'config.yaml')
    try:
        request = urllib.request.Request(
            f'http://{CONTROLLER}/configs?force=true',
            data=json.dumps({'path': str(BASE / 'config.yaml')}).encode(),
            headers={'Content-Type': 'application/json'}, method='PUT')
        with urllib.request.urlopen(request, timeout=30) as response:
            print('mihomo reload:', response.status)
        return True
    except Exception as error:
        print('mihomo reload skipped:', type(error).__name__, error)
        return False
