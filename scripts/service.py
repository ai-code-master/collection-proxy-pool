#!/usr/bin/env python3
"""只管理采集代理池自己的 macOS LaunchAgent。"""
import argparse
import os
import plistlib
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'local.collection-proxy-pool'
DOMAIN = f'gui/{os.getuid()}'
PLIST = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'


def launch(*args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True)


def installed():
    if not PLIST.exists():
        return False
    value = plistlib.loads(PLIST.read_bytes())
    if value.get('ProgramArguments', [None, None])[1] != str(ROOT / 'scripts/pool.py'):
        raise SystemExit('同名服务不属于当前目录，未修改')
    return True


def main():
    parser = argparse.ArgumentParser(description='采集代理池自动启动管理')
    parser.add_argument('command', choices=['install', 'start', 'stop', 'restart', 'status', 'uninstall'])
    args = parser.parse_args()
    exists = installed()
    if args.command == 'status':
        result = launch('print', f'{DOMAIN}/{LABEL}')
        print('\n'.join(line for line in result.stdout.splitlines()
                        if any(key in line for key in ('state =', 'pid =', 'last exit code =')))
              if result.returncode == 0 else '服务未运行')
        return
    if args.command in ('stop', 'restart', 'uninstall', 'install') and exists:
        launch('bootout', f'{DOMAIN}/{LABEL}')
        deadline = time.monotonic() + 15
        while launch('print', f'{DOMAIN}/{LABEL}').returncode == 0:
            if time.monotonic() >= deadline:
                raise SystemExit('旧服务尚未退出，请稍后重试；未启动重复进程')
            time.sleep(0.2)
    if args.command == 'uninstall':
        if exists:
            PLIST.unlink()
        print('已卸载自动启动；数据保留')
        return
    if args.command == 'stop':
        print('服务已停止，下次登录仍可自动启动')
        return
    if args.command == 'install':
        logs = ROOT / 'data/logs'
        logs.mkdir(parents=True, exist_ok=True)
        runner = ROOT / 'scripts/launch_pool.sh'
        runner.chmod(0o755)
        value = {'Label': LABEL, 'ProgramArguments': [str(runner), str(ROOT / 'scripts/pool.py'), 'serve'],
                 'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True,
                 'SoftResourceLimits': {'NumberOfFiles': 4096},
                 'HardResourceLimits': {'NumberOfFiles': 16384},
                 'ThrottleInterval': 30, 'StandardOutPath': str(logs / 'service.log'),
                 'StandardErrorPath': str(logs / 'error.log')}
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps(value))
        PLIST.chmod(0o600)
    elif not exists:
        raise SystemExit('尚未安装，请先运行 install')
    result = launch('bootstrap', DOMAIN, str(PLIST))
    if result.returncode and launch('print', f'{DOMAIN}/{LABEL}').returncode:
        raise SystemExit(result.stderr.strip())
    print(f'自动启动服务已就绪：{LABEL}')


if __name__ == '__main__':
    main()
