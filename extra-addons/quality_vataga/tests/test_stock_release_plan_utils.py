"""Portable tests of quantity arithmetic; these are not Enterprise ORM tests."""

import importlib.util
from pathlib import Path
import unittest

if __package__:
    from ..models.stock_release_plan_utils import (
        StockReleasePlanError, allocate_reserved_quantity, exact_quantity, proportional_quantity,
    )
else:
    spec = importlib.util.spec_from_file_location(
        'stock_release_plan_utils', Path(__file__).resolve().parents[1] / 'models' / 'stock_release_plan_utils.py',
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    StockReleasePlanError = module.StockReleasePlanError
    allocate_reserved_quantity = module.allocate_reserved_quantity
    exact_quantity = module.exact_quantity
    proportional_quantity = module.proportional_quantity


class TestStockReleasePlanUtils(unittest.TestCase):
    def test_one_of_five_consumes_only_one_fifth(self):
        self.assertEqual(proportional_quantity(10, 1, 5, 1), 2)

    def test_second_proposal_does_not_mutate_original_reservations(self):
        reservations = [(17, 10)]
        for _attempt in range(2):
            self.assertEqual(allocate_reserved_quantity(2, reservations, 1), [(17, 2)])
        self.assertEqual(reservations, [(17, 10)])

    def test_no_silent_rounding_of_fractional_component(self):
        with self.assertRaises(StockReleasePlanError):
            proportional_quantity(2, 1, 5, 1)

    def test_exact_decimal_uom(self):
        self.assertEqual(proportional_quantity('1.25', 1, 5, '0.01'), 0.25)

    def test_float_noise_is_not_a_material_quantity_change(self):
        self.assertEqual(exact_quantity(0.1 + 0.2, 0.01), 0.3)

    def test_zero_negative_fractional_or_out_of_batch_selection(self):
        for selected, batch in ((0, 5), (-1, 5), (6, 5), (1.5, 5), (True, 5), (1, 0), (1, 5.0)):
            with self.subTest(selected=selected, batch=batch), self.assertRaises(StockReleasePlanError):
                proportional_quantity(10, selected, batch, 1)

    def test_nonfinite_missing_or_negative_demand_and_precision(self):
        for value in (None, 'NaN', 'Infinity', '-Infinity', -1, 0, '0.000000000001'):
            with self.subTest(value=value), self.assertRaises(StockReleasePlanError):
                proportional_quantity(value, 1, 5, 1)
        for precision in (0, -1, None, 'NaN'):
            with self.subTest(precision=precision), self.assertRaises(StockReleasePlanError):
                exact_quantity(1, precision)

    def test_split_across_two_existing_lines(self):
        self.assertEqual(allocate_reserved_quantity(2, [(11, 1), (12, 9)], 1), [(11, 1), (12, 1)])

    def test_insufficient_reservation_blocks(self):
        with self.assertRaises(StockReleasePlanError):
            allocate_reserved_quantity(2, [(11, 1)], 1)

    def test_duplicate_negative_or_unrepresentable_lines_block(self):
        for rows in ([(11, 1), (11, 3)], [(11, -1)], [(0, 2)], [(11, 1.5)]):
            with self.subTest(rows=rows), self.assertRaises(StockReleasePlanError):
                allocate_reserved_quantity(2, rows, 1)

    def test_zero_quantity_does_not_invent_a_line(self):
        self.assertEqual(allocate_reserved_quantity(0, [(11, 5)], 1), [])

    def test_quantity_conservation_for_whole_piece_batches(self):
        for batch in range(1, 21):
            for selected in range(1, batch + 1):
                for per_unit in range(1, 6):
                    with self.subTest(batch=batch, selected=selected, per_unit=per_unit):
                        total = batch * per_unit
                        taken = proportional_quantity(total, selected, batch, 1)
                        allocations = allocate_reserved_quantity(taken, [(1, per_unit), (2, total - per_unit)], 1)
                        self.assertEqual(taken, selected * per_unit)
                        self.assertEqual(sum(qty for _line, qty in allocations), taken)
                        self.assertEqual(taken + (total - taken), total)
                        self.assertTrue(all(qty >= 0 for _line, qty in allocations))


if __name__ == '__main__':
    unittest.main()
