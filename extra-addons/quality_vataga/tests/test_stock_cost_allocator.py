"""Exact allocator arithmetic, supplemental to real ORM valuation tests."""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('stock_cost_allocator', Path(__file__).resolve().parents[1] / 'models/stock_cost_allocator.py')
allocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allocator)


class TestStockCostAllocator(unittest.TestCase):
    def _interval(self, **values):
        return dict(dict(id=1, workorder=1, start='2026-01-12 08:00:00', end='2026-01-12 09:00:00', rate=60, members=['a', 'b', 'c', 'd', 'e']), **values)

    def test_shared_hour_is_allocated_once_across_five_units(self):
        shares = allocator.allocate_intervals(list('abcde'), [self._interval()], 0.01)
        self.assertEqual([row['amount'] for row in shares], [12] * 5)

    def test_currency_remainder_is_stable_and_conserved(self):
        timer = self._interval(rate=0.03, members=['b', 'a'])
        self.assertEqual(allocator.allocate_intervals(['a', 'b'], [timer], 0.01), [
            dict(timer=1, unit_key='a', amount=0.02), dict(timer=1, unit_key='b', amount=0.01),
        ])

    def test_personal_time_is_not_charged_to_other_units(self):
        shares = allocator.allocate_intervals(['a', 'b'], [self._interval(members=['a'])], 0.01)
        self.assertEqual(shares, [dict(timer=1, unit_key='a', amount=60)])

    def test_open_foreign_duplicate_or_missing_members_are_rejected(self):
        for changes in ({'end': False}, {'members': []}, {'members': ['z']}, {'members': ['a', 'a']}, {'rate': float('inf')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                allocator.allocate_intervals(list('abcde'), [self._interval(**changes)], 0.01)

    def test_overlap_of_same_operation_is_ambiguous(self):
        with self.assertRaises(ValueError):
            allocator.allocate_intervals(list('abcde'), [self._interval(), self._interval(id=2)], 0.01)

    def test_different_operations_can_overlap_without_losing_either_cost(self):
        shares = allocator.allocate_intervals(list('abcde'), [self._interval(), self._interval(id=2, workorder=2)], 0.01)
        self.assertEqual(sum(row['amount'] for row in shares), 120)

    def test_multiple_requests_do_not_mutate_or_redivide_the_snapshot(self):
        timer = self._interval()
        shares = allocator.allocate_intervals(list('abcde'), [timer], 0.01)
        for _index in range(5):
            self.assertEqual(shares, allocator.allocate_intervals(list('abcde'), [timer], 0.01))
        self.assertEqual(timer['members'], list('abcde'))


if __name__ == '__main__':
    unittest.main()
