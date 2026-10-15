"""Evidence gates must reject partial or unsuccessful checker output."""
import unittest
from gitpickle_model import completed


class CompletionTests(unittest.TestCase):
    def test_only_complete_exploration_passes(self):
        output = ('Model checking completed. No error has been found.\n'
                  '10 states generated, 5 distinct states found, 0 states left on queue.')
        self.assertTrue(completed(0, output))
        self.assertFalse(completed(12, output))
        self.assertFalse(completed(None, output))
        self.assertFalse(completed(0, '10 states generated, 5 distinct states found'))
        self.assertFalse(completed(0, 'Model checking completed. No error has been found.'))
