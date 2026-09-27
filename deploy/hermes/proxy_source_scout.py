#!/usr/bin/env python3
"""Hermes 定时任务入口：调用代理池的统一探源程序。"""
import sys
from pathlib import Path

SCRIPTS = Path.home() / 'Services/collection-proxy-pool/scripts'
sys.path.insert(0, str(SCRIPTS))

from external_discovery import main  # noqa: E402


if __name__ == '__main__':
    main()
