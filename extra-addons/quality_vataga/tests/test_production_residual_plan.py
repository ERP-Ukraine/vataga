import copy
import importlib.util
import json
from pathlib import Path
import unittest

try:
    from ..models.production_residual_plan import residual_scope
    from ..models.production_release_plan import plan_production_release
except ImportError:
    def load_plan(name):
        spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / ('models/%s.py' % name))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    residual_scope = load_plan('production_residual_plan').residual_scope
    plan_production_release = load_plan('production_release_plan').plan_production_release


class TestProductionResidualPlan(unittest.TestCase):
    def setUp(self):
        self.sources = [{'id': 10, 'move_id': 20, 'company_id': 1, 'product_id': 2,
                         'lot_id': 3, 'package_id': False, 'owner_id': False, 'quantity': 5, 'exclusive': True}]
        self.snapshot = {'sources': copy.deepcopy(self.sources), 'plan': {
            'ready': True, 'produced': 5, 'bad': 2, 'unidentified': 0, 'reasons': [],
            'allocations': {'10': 3}, 'lots': [{'lot_id': 3, 'quantity': 5, 'bad': 2, 'unidentified': 0}]}}
        self.released = {10: 3}

    def plan(self):
        return residual_scope(self.snapshot, self.sources, self.released, 'lot')

    def test_three_released_leaves_only_two(self):
        result = self.plan()
        self.assertEqual((result[0]['id'], result[0]['quantity']), (10, 2))
        self.assertEqual(self.snapshot['sources'][0]['quantity'], 5)

    def test_full_fail_leaves_five(self):
        self.snapshot['plan'].update(bad=5, allocations={})
        self.snapshot['plan']['lots'][0]['bad'] = 5
        self.released = {}
        self.assertEqual(self.plan()[0]['quantity'], 5)

    def test_release_must_match_the_confirmed_allocation(self):
        for value in (0, 2, 4, 5, -1, 3.5, float('nan')):
            self.released = {10: value}
            with self.assertRaises(ValueError):
                self.plan()

    def test_foreign_source_or_lot_or_company_is_rejected(self):
        for key in ('id', 'move_id', 'lot_id', 'company_id', 'product_id', 'quantity'):
            original = self.sources[0][key]
            self.sources[0][key] = original + 1
            with self.assertRaises(ValueError):
                self.plan()
            self.sources[0][key] = original

    def test_unknown_source_or_allocation_is_rejected(self):
        self.released[99] = 1
        with self.assertRaises(ValueError):
            self.plan()

    def test_shared_identity_is_held(self):
        self.sources[0]['exclusive'] = False
        with self.assertRaises(ValueError):
            self.plan()

    def test_owner_and_package_are_held(self):
        for key in ('owner_id', 'package_id'):
            self.sources[0][key] = self.snapshot['sources'][0][key] = 7
            with self.assertRaises(ValueError):
                self.plan()
            self.sources[0][key] = self.snapshot['sources'][0][key] = False

    def test_pending_or_unidentified_cannot_be_scrapped(self):
        for change in ({'ready': False}, {'unidentified': 5}, {'reasons': ['unit_identity_required']}):
            old = copy.deepcopy(self.snapshot['plan'])
            self.snapshot['plan'].update(change)
            with self.assertRaises(ValueError):
                self.plan()
            self.snapshot['plan'] = old

    def test_bad_count_must_cover_exact_residual(self):
        self.snapshot['plan']['lots'][0]['bad'] = 1
        with self.assertRaises(ValueError):
            self.plan()

    def test_serial_must_be_one_confirmed_unit(self):
        with self.assertRaises(ValueError):
            residual_scope(self.snapshot, self.sources, self.released, 'serial')
        self.sources[0]['quantity'] = self.snapshot['sources'][0]['quantity'] = 1
        self.snapshot['plan'].update(produced=1, bad=1, allocations={})
        self.snapshot['plan']['lots'][0].update(quantity=1, bad=1)
        result = residual_scope(self.snapshot, self.sources, {}, 'serial')
        self.assertEqual((result[0]['lot_id'], result[0]['quantity']), (3, 1))

    def test_multiple_serials_remain_distinct(self):
        self.test_serial_must_be_one_confirmed_unit()
        other = dict(self.sources[0], id=11, lot_id=4)
        self.sources.append(other)
        self.snapshot['sources'].append(copy.deepcopy(other))
        self.snapshot['plan'].update(produced=2, bad=2)
        self.snapshot['plan']['lots'].append({'lot_id': 4, 'quantity': 1, 'bad': 1, 'unidentified': 0})
        result = residual_scope(self.snapshot, self.sources, {}, 'serial')
        self.assertEqual([(s['lot_id'], s['quantity']) for s in result], [(3, 1), (4, 1)])

    def test_full_pass_has_no_scrap_scope(self):
        self.snapshot['plan'].update(bad=0, allocations={'10': 5})
        self.released = {10: 5}
        with self.assertRaises(ValueError):
            self.plan()

    def _check(self, identifier, point, lot, results):
        return {'id': identifier, 'point_id': point, 'lot_id': lot,
                'state': 'fail' if 'fail' in results else 'pass',
                'automatic_count': len(results), 'structure_valid': True, 'equipment_complete': True,
                'samples': [{'id': identifier * 100 + index, 'result': result, 'complete': True}
                            for index, result in enumerate(results)]}

    def test_real_b_json_snapshot_releases_three_and_scopes_two(self):
        self.sources[0]['partial_representable'] = True
        check = self._check(30, 40, 3, ['pass'] * 3 + ['fail'] * 2)
        plan = plan_production_release(self.sources, [check], [40], 'lot')
        snapshot = json.loads(json.dumps({'sources': self.sources, 'checks': [check], 'plan': plan}))
        result = residual_scope(snapshot, self.sources, {10: 3}, 'lot')
        self.assertEqual(result[0]['quantity'], 2)
        self.assertEqual(snapshot['checks'][0]['state'], 'fail')

    def test_real_b_multiple_operations_without_identity_cannot_be_scrapped(self):
        self.sources[0]['partial_representable'] = True
        checks = [self._check(30 + index, point, 3, ['pass'] * 3 + ['fail'] * 2)
                  for index, point in enumerate((40, 41))]
        plan = plan_production_release(self.sources, checks, [40, 41], 'lot')
        self.assertEqual((plan['ready'], plan['unidentified']), (True, 5))
        with self.assertRaises(ValueError):
            residual_scope({'sources': self.sources, 'plan': plan}, self.sources, {}, 'lot')

    def test_real_b_other_fully_released_lot_is_outside_residual(self):
        self.sources[0]['partial_representable'] = True
        other = dict(self.sources[0], id=11, lot_id=4, quantity=4)
        sources = self.sources + [other]
        checks = [self._check(30, 40, 3, ['pass'] * 5), self._check(31, 40, 4, ['pass'] + ['fail'] * 3)]
        plan = plan_production_release(sources, checks, [40], 'lot')
        result = residual_scope({'sources': sources, 'plan': plan}, sources, {10: 5, 11: 1}, 'lot')
        self.assertEqual([(row['id'], row['lot_id'], row['quantity']) for row in result], [(11, 4, 3)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
