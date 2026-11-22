"""Run directly with Python as well as through the Odoo test loader."""
import unittest

try:
    from ..models.production_release_plan import plan_production_release
except ImportError:
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        'production_release_plan', Path(__file__).parents[1] / 'models/production_release_plan.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plan_production_release = module.plan_production_release


class TestProductionReleasePlan(unittest.TestCase):
    def source(self, key=1, lot=10, quantity=5, **values):
        return dict(id=key, lot_id=lot, quantity=quantity, exclusive=True,
                    partial_representable=True, **values)

    def check(self, results, lot=10, point=1, state=None):
        return {'lot_id': lot, 'point_id': point, 'state': state or ('fail' if 'fail' in results else 'pass'),
                'automatic_count': len(results), 'structure_valid': True, 'equipment_complete': True,
                'samples': [{'id': i + 1, 'result': value, 'complete': value != 'pending'}
                            for i, value in enumerate(results)]}

    def plan(self, results, **values):
        return plan_production_release([self.source()], [self.check(results, **values)], [1], 'lot')

    def test_full_pass(self):
        result = self.plan(['pass'] * 5)
        self.assertEqual((result['good'], result['bad'], result['allocations']), (5, 0, {1: 5}))

    def test_partial_fail(self):
        result = self.plan(['pass'] * 3 + ['fail'] * 2)
        self.assertEqual((result['good'], result['bad'], result['allocations']), (3, 2, {1: 3}))

    def test_all_fail(self):
        result = self.plan(['fail'] * 5)
        self.assertTrue(result['ready'])
        self.assertEqual((result['bad'], result['allocations']), (5, {}))

    def test_pending_sample_blocks_everything(self):
        result = self.plan(['pass'] * 3 + ['fail', 'pending'])
        self.assertFalse(result['ready'])
        self.assertFalse(result['allocations'])

    def test_pending_check_blocks_complete_matrix(self):
        self.assertFalse(self.plan(['pass'] * 5, state='none')['ready'])

    def test_multiple_operations_do_not_match_ordinals(self):
        checks = [self.check(['pass'] * 3 + ['fail'] * 2),
                  self.check(['fail'] * 2 + ['pass'] * 3, point=2)]
        result = plan_production_release([self.source()], checks, [1, 2], 'lot')
        self.assertTrue(result['ready'])
        self.assertEqual(result['unidentified'], 5)
        self.assertFalse(result['allocations'])

    def test_multiple_operations_full_pass_needs_no_unit_ids(self):
        checks = [self.check(['pass'] * 5), self.check(['pass'] * 5, point=2)]
        self.assertEqual(plan_production_release([self.source()], checks, [1, 2], 'lot')['allocations'], {1: 5})

    def test_serial_identity_across_operations(self):
        sources = [self.source(key=i, lot=10 + i, quantity=1) for i in range(1, 6)]
        checks = [self.check(['pass' if i <= 3 else 'fail'], lot=10 + i, point=point)
                  for i in range(1, 6) for point in (1, 2)]
        result = plan_production_release(sources, checks, [1, 2], 'serial')
        self.assertEqual((result['good'], result['bad'], result['allocations']), (3, 2, {1: 1, 2: 1, 3: 1}))

    def test_serial_global_matrix_cannot_be_distributed_by_order(self):
        sources = [self.source(key=i, lot=10 + i, quantity=1) for i in range(1, 6)]
        result = plan_production_release(sources, [self.check(['pass'] * 5, lot=11)], [1], 'serial')
        self.assertFalse(result['ready'])
        self.assertFalse(result['allocations'])

    def test_lots_remain_independent(self):
        sources = [self.source(), self.source(key=2, lot=20, quantity=2)]
        checks = [self.check(['pass'] * 3 + ['fail'] * 2), self.check(['fail'] * 2, lot=20)]
        result = plan_production_release(sources, checks, [1], 'lot')
        self.assertEqual((result['produced'], result['good'], result['bad'], result['allocations']), (7, 3, 4, {1: 3}))

    def test_missing_other_lot_check_blocks_release(self):
        result = plan_production_release([self.source(), self.source(key=2, lot=20)],
                                         [self.check(['pass'] * 5)], [1], 'lot')
        self.assertFalse(result['ready'])
        self.assertFalse(result['allocations'])

    def test_multiple_source_lines_cannot_receive_arbitrary_good_units(self):
        result = plan_production_release([self.source(quantity=2), self.source(key=2, quantity=3)],
                                         [self.check(['pass'] * 3 + ['fail'] * 2)], [1], 'lot')
        self.assertFalse(result['allocations'])
        self.assertIn('ambiguous_source_lines', result['reasons'])

    def test_shared_quarantine_key_blocks_partial(self):
        source = self.source()
        source['exclusive'] = False
        result = plan_production_release([source], [self.check(['pass'] * 3 + ['fail'] * 2)], [1], 'lot')
        self.assertFalse(result['allocations'])

    def test_bad_structure_or_equipment_blocks_release(self):
        for key in ('structure_valid', 'equipment_complete'):
            check = self.check(['pass'] * 5)
            check[key] = False
            self.assertFalse(plan_production_release([self.source()], [check], [1], 'lot')['ready'])

    def test_duplicate_points_or_foreign_lots_block_release(self):
        for checks in ([self.check(['pass'] * 5)] * 2, [self.check(['pass'] * 5, lot=999)]):
            self.assertFalse(plan_production_release([self.source()], checks, [1], 'lot')['ready'])

    def test_inconsistent_pass_or_global_fail_cannot_release(self):
        self.assertFalse(self.plan(['pass'] * 3 + ['fail'] * 2, state='pass')['ready'])
        result = self.plan(['pass'] * 5, state='fail')
        self.assertFalse(result['allocations'])
        self.assertEqual(result['unidentified'], 5)

    def test_fractional_and_duplicate_sources_are_rejected(self):
        for sources in ([self.source(quantity=0.5)], [self.source()] * 2):
            self.assertFalse(plan_production_release(sources, [self.check(['pass'] * 5)], [1], 'lot')['ready'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
