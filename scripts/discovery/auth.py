"""GitHub API 凭据发现，优先环境变量，其次复用 gh 登录态。"""
import functools
import os
import shutil
import subprocess
from pathlib import Path


@functools.lru_cache(maxsize=1)
def cli_token():
    executable = shutil.which('gh')
    if not executable:
        executable = next((str(path) for path in (
            Path('/opt/homebrew/bin/gh'), Path('/usr/local/bin/gh')) if path.exists()), '')
    if not executable:
        return ''
    try:
        result = subprocess.run(
            [executable, 'auth', 'token', '--hostname', 'github.com'],
            capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def github_token():
    return os.environ.get('GITHUB_TOKEN', '').strip() or cli_token()
