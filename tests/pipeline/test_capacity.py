import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from lib.scheduling.stages import capacity


class CapacityTest(unittest.TestCase):
    def setUp(self):
        self.config = {'workers': 8, 'batch_size': 200, 'probe_interval': 2}

    def choose(self, backlog, pressure):
        with patch.object(capacity, 'due_count', return_value=backlog), \
                patch.object(capacity, 'resource_pressure', return_value=pressure):
            return capacity.choose(object(), self.config, 'target')

    def test_deep_backlog_expands_with_a_bounded_limit(self):
        plan = self.choose(1000, {'load_ratio': .4, 'fd_ratio': .1})
        self.assertEqual((plan['probe_workers'], plan['launch_interval']), (48, .35))
        self.assertEqual(plan['reason'], 'deep_backlog')

    def test_resource_pressure_reduces_concurrency(self):
        plan = self.choose(1000, {'load_ratio': 1.6, 'fd_ratio': .1})
        self.assertEqual((plan['probe_workers'], plan['launch_interval']), (8, 2))
        self.assertEqual(plan['reason'], 'resource_pressure')


if __name__ == '__main__':
    unittest.main()
