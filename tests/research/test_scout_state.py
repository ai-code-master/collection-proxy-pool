import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/research'))

from scout_state import active_rejections, known_keys, remember_rejections


class ScoutStateTest(unittest.TestCase):
    def test_known_keys_reads_all_runtime_sets(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'alive.json').write_text(json.dumps({'nodes': {'a': {}}}))
            (base / 'queue.json').write_text(json.dumps({'b': {}}))
            (base / 'dead.json').write_text(json.dumps({'c': 1}))
            self.assertEqual(known_keys(base), {'a', 'b', 'c'})

    def test_rejections_expire_and_are_refreshed_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rejections.json'
            path.write_text(json.dumps({'old': 99, 'live': 150}))
            self.assertEqual(active_rejections(path, now=100), {'live'})
            remember_rejections(path, {'new'}, now=100, ttl=20)
            self.assertEqual(active_rejections(path, now=110), {'live', 'new'})
            self.assertEqual(active_rejections(path, now=151), set())


if __name__ == '__main__':
    unittest.main()
