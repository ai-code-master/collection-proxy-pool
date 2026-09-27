import json
import tempfile
import unittest
from pathlib import Path

from scripts.lib.settings import ROOT, load


class SettingsTest(unittest.TestCase):
    def test_public_template_contains_no_external_targets(self):
        config = load(ROOT / 'examples/config.json')
        profile = config['profiles']['connectivity']
        self.assertFalse(profile['enabled'])
        self.assertEqual(profile['targets'], {'domestic': [], 'overseas': []})
        self.assertEqual(profile['identity_targets'], [])

    def test_enabled_profile_requires_user_targets(self):
        document = json.loads((ROOT / 'examples/config.json').read_text())
        document['profiles']['connectivity']['enabled'] = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, '启用检测'):
                load(path)


if __name__ == '__main__':
    unittest.main()
