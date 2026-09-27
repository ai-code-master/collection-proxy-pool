"""Download configured or researched sources into an isolated research folder."""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from source_snapshot import ROOT, snapshot
from source_download import download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--catalog', type=Path)
    args = parser.parse_args()
    args.folder.mkdir(parents=True, exist_ok=True)
    snap = snapshot()
    (args.folder / 'snapshot.json').write_text(json.dumps(snap, ensure_ascii=False))
    if args.catalog:
        entries = json.loads(args.catalog.read_text())
    else:
        source_map = json.loads((ROOT / 'sources.json').read_text())
        entries = [dict(name=name, url=url, configured=True,
                        parser=name, protocol=name.rsplit('-', 1)[-1]) for name, url in source_map.items()]
    with ThreadPoolExecutor(max_workers=3) as executor:
        jobs = [executor.submit(download, entry, args.folder / 'sources') for entry in entries]
        for job in as_completed(jobs):
            try:
                report = job.result()
                print(json.dumps({key: report.get(key) for key in
                      ('name', 'status', 'curl_error', 'nodes', 'parsed', 'last-modified', 'age') }), flush=True)
            except Exception as error:
                print(json.dumps({'error':type(error).__name__}), flush=True)


if __name__ == '__main__':
    main()
