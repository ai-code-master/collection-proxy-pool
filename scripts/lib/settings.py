import hashlib
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit
from .scheduling.grading import QUOTAS

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('PROXY_POOL_DATA', ROOT / 'data')).expanduser()
LOCAL_CONFIG = ROOT / 'config.json'
LOCAL_SOURCES = ROOT / 'sources.json'
CONFIG = Path(os.environ.get('PROXY_POOL_CONFIG',
              LOCAL_CONFIG if LOCAL_CONFIG.exists() else ROOT / 'examples/config.json')).expanduser()
SOURCES = Path(os.environ.get('PROXY_POOL_SOURCES',
               LOCAL_SOURCES if LOCAL_SOURCES.exists() else ROOT / 'examples/sources.json')).expanduser()
PROFILE = 'connectivity'
PROFILES = (PROFILE,)
SCHEDULE_KEYS = ('source_interval', 'discovery_interval', 'source_review_interval',
                 'discovery_enabled',
                 'history_recheck_interval', 'recheck_interval',
                 'new_recheck_interval', 'export_interval')
_CONFIG_LOCK = threading.Lock()


def load(path=None):
    config = json.loads(Path(path or CONFIG).read_text())
    if config['host'] not in ('127.0.0.1', '0.0.0.0') or not 1024 <= config['port'] <= 65535:
        raise ValueError('服务只允许监听 127.0.0.1 或 0.0.0.0 的非特权端口')
    if not 1 <= config['workers'] <= 16 or not 1 <= config['batch_size'] <= 500:
        raise ValueError('探测并发为 1–16，每轮检测 1–500 个')
    defaults = dict(prefilter_workers=32, prefilter_timeout=3, cycle_interval=5, probe_interval=2,
                    export_interval=600, candidate_queue_limit=20000, candidate_source_limit=5000,
                    admission_batch=200, candidate_retry_hours=24,
                    history_recheck_workers=4, history_recheck_batch=100,
                    history_recheck_interval=300, max_failure_retry_ratio=.25,
                    api_rate_limit_per_minute=120, discovery_interval=3600,
                    discovery_enabled=True, discovery_limit=20,
                    source_review_interval=3600, source_review_batch=20,
                    source_review_workers=4, source_review_sample_size=8,
                    source_review_min_records=20, source_review_min_novel=10,
                    source_review_min_success=1)
    for key, value in defaults.items():
        config.setdefault(key, value)
    if not 60 <= config['export_interval'] <= 3600:
        raise ValueError('导出间隔须在 60–3600 秒之间')
    if (not 3600 <= config['discovery_interval'] <= 604800
            or type(config['discovery_enabled']) is not bool
            or not 1 <= config['discovery_limit'] <= 100):
        raise ValueError('新来源发现间隔须在 1 小时至 7 天之间')
    if (not 3600 <= config['source_review_interval'] <= 604800
            or not 1 <= config['source_review_batch'] <= 100
            or not 1 <= config['source_review_workers'] <= 8
            or not 1 <= config['source_review_sample_size'] <= 20
            or not 10 <= config['source_review_min_records'] <= 1000
            or not 1 <= config['source_review_min_novel'] <= config['source_review_min_records']
            or not 1 <= config['source_review_min_success'] <= 5):
        raise ValueError('来源审核参数超出安全范围')
    if not 1 <= config['prefilter_workers'] <= 64 or not 1 <= config['prefilter_timeout'] <= 5:
        raise ValueError('端口预筛并发 1–64，超时 1–5 秒')
    if (not 1000 <= config['candidate_queue_limit'] <= 100000
            or not 100 <= config['candidate_source_limit'] <= config['candidate_queue_limit']
            or not 10 <= config['admission_batch'] <= 1000
            or not 1 <= config['candidate_retry_hours'] <= 168):
        raise ValueError('候选准入参数超出安全范围')
    if (not 1 <= config['history_recheck_workers'] <= 8
            or not 10 <= config['history_recheck_batch'] <= 500
            or not 60 <= config['history_recheck_interval'] <= 3600):
        raise ValueError('历史节点复测参数超出安全范围')
    if not .1 <= config['max_failure_retry_ratio'] <= .5:
        raise ValueError('失败重试最多占每轮 10%–50%')
    if not 10 <= config['api_rate_limit_per_minute'] <= 10000:
        raise ValueError('API 每分钟限额须为 10–10000')
    if not .5 <= config['probe_interval'] <= 60 or not 1 <= config['cycle_interval'] <= 300:
        raise ValueError('连通性请求发起间隔 0.5–60 秒，轮间等待 1–300 秒')
    if config['source_interval'] < 300 or config['recheck_interval'] < 300:
        raise ValueError('收集与复测间隔至少 300 秒')
    if not config['recheck_interval'] < config['valid_seconds'] <= 3600:
        raise ValueError('有效期须大于复测间隔，且不超过 3600 秒')
    config.setdefault('new_recheck_interval', 300)
    config.setdefault('grade_quotas', dict(QUOTAS))
    quotas = config['grade_quotas']
    if (not isinstance(quotas, dict) or set(quotas) != set(QUOTAS)
            or any(type(v) is not int or v < 1 for v in quotas.values()) or sum(quotas.values()) != 100):
        raise ValueError('调度等级名额须为正整数百分比，合计 100')
    if not 300 <= config['new_recheck_interval'] <= config['recheck_interval']:
        raise ValueError('观察节点复测间隔须为 300 秒到稳定节点复测间隔之间')
    profiles = config.get('profiles', {})
    if set(profiles) != set(PROFILES):
        raise ValueError('必须且只能配置 connectivity 连通性探测')
    for profile, target in profiles.items():
        groups = target.get('targets', {})
        if set(groups) != {'domestic', 'overseas'}:
            raise ValueError('连通性目标必须包含 domestic 和 overseas')
        for entries in groups.values():
            if len(entries) > 3 or (target['enabled'] and not entries):
                raise ValueError('启用检测时每组连通性目标须为 1–3 个')
            for entry in entries:
                parsed = urlsplit(entry.get('url', ''))
                if parsed.scheme != 'https' or not parsed.hostname or entry.get('status') not in range(200, 400):
                    raise ValueError('连通性目标必须是 HTTPS，预期状态码须为 2xx/3xx')
        identity_targets = target.setdefault('identity_targets', [])
        if len(identity_targets) > 3:
            raise ValueError('出口识别目标最多 3 个')
        for entry in identity_targets:
            parsed = urlsplit(entry.get('url', ''))
            if (parsed.scheme != 'https' or not parsed.hostname
                    or entry.get('format') not in ('trace', 'ip')):
                raise ValueError('出口识别目标须为 HTTPS，格式须为 trace 或 ip')
        target['fingerprint'] = hashlib.sha256(json.dumps(
            groups, sort_keys=True).encode()).hexdigest()[:16]
    return config


def update_schedule(changes, path=None):
    """原子更新私有配置；若配置是软链接，写入实际目标。"""
    if not isinstance(changes, dict) or not changes or set(changes) - set(SCHEDULE_KEYS):
        raise ValueError('调度设置字段无效')
    target = Path(path or CONFIG).expanduser()
    target = target.resolve() if target.exists() else target
    with _CONFIG_LOCK:
        value = json.loads(target.read_text())
        for key, item in changes.items():
            if key == 'discovery_enabled':
                if type(item) is not bool:
                    raise ValueError('发现开关必须是布尔值')
            elif type(item) is not int:
                raise ValueError('调度间隔必须是整数秒')
            value[key] = item
        temporary = target.with_suffix(target.suffix + '.new')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary.chmod(target.stat().st_mode & 0o777)
        try:
            checked = load(temporary)
            temporary.replace(target)
        finally:
            if temporary.exists():
                temporary.unlink()
    return {key: checked[key] for key in SCHEDULE_KEYS}
