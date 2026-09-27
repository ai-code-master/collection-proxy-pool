import json
import tempfile
import unittest
from pathlib import Path

from scripts.discovery.sources import candidate_paths, discover, write_catalog


class SourceDiscoveryTest(unittest.TestCase):
    def test_discovers_without_bundled_results(self):
        def getter(url):
            if '/search/' in url:
                return {'items': [{'full_name': 'owner/repo', 'default_branch': 'main'}]}
            return {'tree': [{'type': 'blob', 'path': 'lists/http.txt', 'size': 200},
                             {'type': 'blob', 'path': 'docs/proxies.txt', 'size': 20}]}
        rows = discover(getter=getter)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'],
                         'https://raw.githubusercontent.com/owner/repo/main/lists/http.txt')

    def test_filters_artifacts_and_writes_private_catalog(self):
        tree = {'tree': [{'type': 'blob', 'path': 'proxy.json', 'size': 1},
                         {'type': 'blob', 'path': 'proxy.yaml', 'size': 1},
                         {'type': 'blob', 'path': 'tests/proxy.txt', 'size': 1}]}
        self.assertEqual(candidate_paths(tree), ['proxy.json'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sources.json'
            count = write_catalog(path, [{'name': 'found', 'url': 'https://example.test/list'}])
            self.assertEqual(count, 1)
            self.assertEqual(json.loads(path.read_text()), {'found': 'https://example.test/list'})
            self.assertEqual(write_catalog(path, [
                {'name': 'found', 'url': 'https://example.test/list'}]), 0)


if __name__ == '__main__':
    unittest.main()
