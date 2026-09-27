"""Mihomo 适配器设置；所有外部地址均来自本地私有配置。"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = ROOT / 'mihomo.json'
CONFIG = Path(os.environ.get('PROXY_POOL_MIHOMO_CONFIG',
              LOCAL_CONFIG if LOCAL_CONFIG.exists() else ROOT / 'examples/mihomo.json')).expanduser()
DOCUMENT = json.loads(CONFIG.read_text())
BASE = Path(os.environ.get('PROXY_POOL_MIHOMO_DATA',
            DOCUMENT.get('data_dir', Path.home() / 'clash-exit'))).expanduser()

NEW_BATCH = int(DOCUMENT.get('new_batch', 1800))
BASE_PORT = int(DOCUMENT.get('base_port', 25001))
CONTROLLER = DOCUMENT.get('controller', '127.0.0.1:20099')
LAN_BIND_HOST = os.environ.get('MIHOMO_LAN_BIND_HOST', '0.0.0.0')
ROTATING_PORT = int(os.environ.get('MIHOMO_ROTATING_PORT', '21993'))
PROBE_WORKERS = int(DOCUMENT.get('probe_workers', 50))
PROBE_TIMEOUT = int(DOCUMENT.get('probe_timeout', 5))
RESERVE_RATIO = int(DOCUMENT.get('reserve_ratio', 4))
RESERVE_CAP = int(DOCUMENT.get('reserve_cap', 50))
DEAD_TTL = int(DOCUMENT.get('dead_ttl', 4 * 3600))
SEEN_TTL = int(DOCUMENT.get('seen_ttl', 7 * 86400))
QUEUE_CAP = int(DOCUMENT.get('queue_cap', 30000))
SUPPORTED = {'ss', 'ssr', 'vmess', 'vless', 'trojan', 'hysteria', 'hysteria2',
             'hy2', 'tuic', 'socks5', 'http', 'snell'}


def target(entry, default_status=200):
    if not entry:
        return None
    parsed = urlsplit(entry['url'])
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('Mihomo 检测目标必须是 HTTPS')
    path = parsed.path or '/'
    if parsed.query:
        path += '?' + parsed.query
    return parsed.hostname, path, int(entry.get('status', default_status))


PROBE_TARGETS = tuple(target(entry) for entry in DOCUMENT.get('probe_targets', []))
EXIT_IP_TARGET = target(DOCUMENT.get('exit_ip_target'))
GROUP_HEALTH_URL = DOCUMENT.get('group_health_url')
SOURCES = list(DOCUMENT.get('sources', []))
PRIORITY_SOURCES = set(DOCUMENT.get('priority_sources', []))
DATED_SOURCE_TEMPLATE = DOCUMENT.get('dated_source_template', '')
REVISION_API = DOCUMENT.get('revision_api', '')
REVISION_SOURCE_TEMPLATE = DOCUMENT.get('revision_source_template', '')

for source in SOURCES:
    if urlsplit(source).scheme != 'https':
        raise ValueError('Mihomo 来源必须是 HTTPS')
if GROUP_HEALTH_URL and urlsplit(GROUP_HEALTH_URL).scheme != 'https':
    raise ValueError('Mihomo 组检测地址必须是 HTTPS')
