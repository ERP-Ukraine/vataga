"""Portable shipped QUNIT selection/WO policy; explicitly not Enterprise ORM."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError, AccessError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError, AccessError

ROOT = Path(__file__).resolve().parents[1]


class Rows(list):
    @property
    def ids(self):
        return [r.id for r in self]

    def __or__(self, other):
        if not isinstance(other, Rows):
            other = Rows([other])
        return Rows(list(self) + [r for r in other if r.id not in self.ids])

    def filtered(self, predicate):
        return Rows(r for r in self if predicate(r))


class TestPartialQunitSelection(unittest.TestCase):
    def setUp(self):
        self.units = Rows(NS(id=i, _stock_posted=lambda: False) for i in range(1, 6))
        self.production = shipped_class('models/quality_partial_release.py', 'QualityPartialProduction',
                                        {'_quality_partial_ready_units'})()
        self.production.ensure_one = Mock()
        self.production._quality_partial_universe = lambda: self.units
        self.production.env = {'quality.production.unit': Rows()}
        self.production._quality_partial_validate_units = Mock()

    def test_exact_four_eligible_identities_with_fifth_held(self):
        def validate(unit):
            if unit.id == 5:
                raise UserError('QC/Alert holds fifth')
        self.production._quality_partial_validate_units.side_effect = validate
        ready, reasons = self.production._quality_partial_ready_units()
        self.assertEqual(ready.ids, [1, 2, 3, 4])
        self.assertEqual(reasons, {5: 'QC/Alert holds fifth'})

    def test_posted_units_are_skipped_and_only_fifth_can_continue(self):
        for unit in self.units[:4]:
            unit._stock_posted = lambda: True
        ready, reasons = self.production._quality_partial_ready_units()
        self.assertEqual(ready.ids, [5])
        self.assertFalse(reasons)
        self.production._quality_partial_validate_units.assert_called_once_with(self.units[-1])

    def test_hidden_qc_evidence_propagates_access_error(self):
        self.production._quality_partial_validate_units.side_effect = AccessError('Hidden QC')
        with self.assertRaises(AccessError):
            self.production._quality_partial_ready_units()

    def test_unknown_error_is_not_treated_as_a_quality_hold(self):
        self.production._quality_partial_validate_units.side_effect = RuntimeError('Broken integration')
        with self.assertRaises(RuntimeError):
            self.production._quality_partial_ready_units()

    def test_no_quantity_fallback_when_all_identities_are_held(self):
        self.production._quality_partial_validate_units.side_effect = UserError('No admission')
        ready, reasons = self.production._quality_partial_ready_units()
        self.assertFalse(ready)
        self.assertEqual(set(reasons), set(self.units.ids))


class TestPartialWorkorderContinuation(unittest.TestCase):
    def test_only_unposted_units_are_available_after_partial_history(self):
        units = Rows([NS(id=1, _stock_posted=lambda: True), NS(id=5, _stock_posted=lambda: False)])
        class Parent:
            def _quality_native_available_units(self, universe):
                return universe, {7: 'Earlier hold'}
        cls = shipped_class('models/quality_partial_release.py', 'QualityPartialWorkorder',
                            {'_quality_native_available_units'}, Parent)
        workorder = cls()
        workorder.production_id = NS(_native_partial_history=lambda: True)
        available, reasons = workorder._quality_native_available_units(units)
        self.assertEqual(available.ids, [5])
        self.assertEqual(reasons, {7: 'Earlier hold'})
        workorder.production_id._native_partial_history = lambda: False
        self.assertEqual(workorder._quality_native_available_units(units)[0], units)

    def test_native_aggregate_target_is_restored_without_posting_stock(self):
        class Parent:
            def _quality_confirm_on_native_finish(self):
                return 'native-finish'
        cls = shipped_class('models/quality_partial_release.py', 'QualityPartialWorkorder',
                            {'_quality_confirm_on_native_finish'}, Parent, partial_operation=lambda p: None)
        workorder = cls()
        production = NS(_native_partial_history=lambda: True, product_qty=5, write=Mock())
        workorder.production_id = production
        self.assertEqual(workorder._quality_confirm_on_native_finish(), 'native-finish')
        production.write.assert_called_once_with({'qty_producing': 5})


class TestPartialProbeContract(unittest.TestCase):
    def test_scheduled_action_contains_only_exact_database_call_and_log(self):
        tree = ast.parse((ROOT / 'scripts/partial_release_scheduled_action.py').read_text(encoding='utf-8'))
        self.assertEqual(len(tree.body), 3)
        self.assertFalse(any(isinstance(n, (ast.Import, ast.ImportFrom, ast.With, ast.Try)) for n in ast.walk(tree)))
        self.assertTrue(all(isinstance(n, ast.Name) for a in tree.body if isinstance(a, ast.Assign) for n in a.targets))
        self.assertEqual(tree.body[0].value.value, 'REPLACE_WITH_EXACT_STAGING_DATABASE')
        self.assertEqual(tree.body[1].value.func.attr, '_run_quality_partial_release_probe')
        self.assertEqual(tree.body[2].value.func.id, 'log')

    def test_version_stays_62_until_enterprise_acceptance(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], '17.0.2.62')
