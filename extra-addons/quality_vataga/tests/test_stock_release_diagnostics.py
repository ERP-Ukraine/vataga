"""Portable diagnostic tests; these do not exercise Enterprise stock posting."""

import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    'stock_release_diagnostics', Path(__file__).resolve().parents[1] / 'models' / 'stock_release_diagnostics.py',
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class TestStockReleaseDiagnostics(unittest.TestCase):
    def test_no_timers_does_not_enable_posting(self):
        self.assertEqual(diagnostics.posting_blocker_codes([]), [
            'posting_journal_missing', 'interval_allocation_missing',
            'native_genealogy_overwrite', 'enterprise_validation_pending',
        ])

    def test_closed_named_timer_is_not_an_allocation_or_posting_permission(self):
        codes = diagnostics.posting_blocker_codes([{'end': '2026-01-12 09:00:00', 'has_execution_batch': True}])
        self.assertEqual(codes, diagnostics.posting_blocker_codes([]))

    def test_open_named_timer_blocks_final_cost(self):
        codes = diagnostics.posting_blocker_codes([{'end': False, 'has_execution_batch': True}])
        self.assertIn('open_intervals', codes)
        self.assertNotIn('unmapped_intervals', codes)

    def test_source_operation_without_named_batch_is_reported(self):
        codes = diagnostics.posting_blocker_codes([{'end': '2026-01-12 09:00:00', 'has_execution_batch': False}])
        self.assertIn('unmapped_intervals', codes)
        self.assertNotIn('open_intervals', codes)

    def test_legacy_snapshot_does_not_fabricate_unit_time_allocation(self):
        codes = diagnostics.posting_blocker_codes([{'end': '2026-01-12 09:00:00'}])
        self.assertIn('unmapped_intervals', codes)

    def test_multiple_intervals_produce_no_duplicate_or_mutated_facts(self):
        timers = [{'end': False, 'has_execution_batch': False}] * 5
        before = [dict(timer) for timer in timers]
        first = diagnostics.posting_blocker_codes(timers)
        self.assertEqual(first, diagnostics.posting_blocker_codes(timers))
        self.assertEqual(len(first), len(set(first)))
        self.assertEqual(timers, before)


if __name__ == '__main__':
    unittest.main()
