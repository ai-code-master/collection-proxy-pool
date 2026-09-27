#!/usr/bin/env python3
"""外部调度器的安全探源入口：只写候选库，不直接修改正式来源。"""
import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from discovery import sources  # noqa: E402
from lib.settings import load  # noqa: E402
from lib.storage import Store  # noqa: E402
from lib.tasks.discovery import ingest  # noqa: E402


def query_file(value):
    if value:
        return Path(value).expanduser()
    sibling = Path(sys.argv[0]).expanduser().with_name('proxy_source_queries.json')
    return sibling if sibling.exists() else None


def read_queries(path):
    if path is None:
        return sources.QUERIES
    values = json.loads(path.read_text())
    if (not isinstance(values, list) or not 1 <= len(values) <= 20
            or any(not isinstance(value, str) or not value.strip() for value in values)):
        raise ValueError('搜索词文件必须是 1–20 条非空字符串')
    return tuple(values)


def main():
    parser = argparse.ArgumentParser(description='将外部搜索结果写入代理来源候选库')
    parser.add_argument('--queries')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    config = load()
    limit = config['discovery_limit'] if args.limit is None else args.limit
    if not 1 <= limit <= 100:
        parser.error('--limit 须为 1–100')
    sources.QUERIES = read_queries(query_file(args.queries))
    store = Store()
    try:
        result = ingest(store, sources.discover(limit))
    finally:
        store.close()
    print(json.dumps({'provider': 'external', **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
