import json
from pathlib import Path


def clash_exit(folder=None):
    """读取本机 clash-exit 汇总信息，缺失或损坏时降级为空。"""
    folder = folder or Path.home() / 'clash-exit'
    try:
        meta = json.loads((folder / 'refresh_meta.json').read_text())
        alive = meta.get('alive') or {}
        return {'lanes': meta.get('nodes', 0), 'ports': meta.get('ports', []),
                'alive': alive.get('total', 0), 'reserve': alive.get('reserve', 0),
                'unique_exit_ips': alive.get('unique_exit_ips', 0),
                'median_ms': alive.get('median_ms'), 'refreshed_at': meta.get('refreshed_at', 0)}
    except (OSError, ValueError, AttributeError):
        return {}
