import os
import unittest
from unittest.mock import patch

from scripts.discovery import auth


class DiscoveryAuthTest(unittest.TestCase):
    def setUp(self):
        auth.cli_token.cache_clear()

    def tearDown(self):
        auth.cli_token.cache_clear()

    def test_environment_token_takes_priority(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'environment-token'}):
            with patch.object(auth, 'cli_token') as fallback:
                self.assertEqual(auth.github_token(), 'environment-token')
                fallback.assert_not_called()

    def test_cli_login_is_optional_fallback(self):
        result = type('Result', (), {'returncode': 0, 'stdout': 'cli-token\n'})()
        with patch.dict(os.environ, {}, clear=True), patch.object(
                auth.shutil, 'which', return_value='/opt/homebrew/bin/gh'), patch.object(
                    auth.subprocess, 'run', return_value=result):
            self.assertEqual(auth.github_token(), 'cli-token')


if __name__ == '__main__':
    unittest.main()
