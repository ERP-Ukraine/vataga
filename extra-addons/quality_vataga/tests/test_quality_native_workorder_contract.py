"""Portable behavioral tests of the shipped phase 2 methods, without an ORM DB."""
import ast
from contextlib import contextmanager
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def load(methods, parent=object):
    path = ROOT / 'models/quality_native_workorder.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'QualityNativeWorkorder')
    cls.bases = [ast.Name(id='Parent', ctx=ast.Load())]
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    for method in cls.body:
        method.decorator_list = []
    scope = {'Parent': parent, 'math': math, 'UserError': ValueError, 'ValidationError': ValueError,
             '_': lambda text: text, 'native_operation_allows': lambda productions: False,
             'PARTIAL_WO_MESSAGE': 'Partial WO requires Enterprise validation'}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'), scope)
    return scope['QualityNativeWorkorder']


class Rows(list):
    def check_access_rights(self, operation):
        return True

    def check_access_rule(self, operation):
        return True

    def __sub__(self, other):
        return Rows(row for row in self if row.id not in other.ids)

    @property
    def ids(self):
        return [row.id for row in self]

    def filtered(self, predicate):
        return Rows(row for row in self if predicate(row))

    def __getattr__(self, name):
        return getattr(self[0], name) if len(self) == 1 else Rows()


class Recordset:
    def __iter__(self):
        return iter(self.rows)


class TestNativeWorkorderValidation(unittest.TestCase):
    def setUp(self):
        cls = load({'_check_quality_native_finish', '_check_quality_native_start', '_quality_validate_native_checks'}, Recordset)
        self.scope = cls()
        self.unit = SimpleNamespace(id=11)
        self.units = Rows([self.unit, SimpleNamespace(id=12)])
        uom = SimpleNamespace(category_id=1, _compute_quantity=lambda value, unit, round=False: value)
        self.scope.env = SimpleNamespace(ref=lambda xmlid: uom)
        self.production = SimpleNamespace(_full_release_units=lambda: self.units,
                                          _check_quality_transfer_boundary=Mock())
        self.row = SimpleNamespace(state='progress', product_uom_id=uom, qty_producing=2,
                                   qty_production=2, qty_produced=0, production_id=self.production,
                                   _quality_native_checks=Mock(return_value=[]),
                                   _quality_native_units=Mock(return_value=self.units),
                                   _check_quality_execution_access=Mock(),
                                   _quality_validate_native_checks=Mock())
        self.scope.rows = [self.row]
        self.scope.production_id = self.production
        self.scope._check_quality_native_start = Mock()

    def test_full_batch_allows_finish_with_exact_physical_units(self):
        self.scope._check_quality_native_finish()
        self.row._quality_validate_native_checks.assert_called_once_with([], self.units)

    def test_partial_count_cannot_close_workorder(self):
        self.row._quality_native_units.return_value = self.units[:1]
        with self.assertRaisesRegex(ValueError, 'Partial WO'):
            self.scope._check_quality_native_finish()

    def test_partial_native_quantity_cannot_lose_remainder(self):
        self.row.qty_producing = 1
        with self.assertRaisesRegex(ValueError, 'Partial WO'):
            self.scope._check_quality_native_finish()

    def test_existing_partial_qty_and_nonfinite_quantities_are_rejected(self):
        for name, value in (('qty_produced', 1), ('qty_producing', math.nan),
                            ('qty_producing', math.inf), ('qty_producing', -1), ('qty_producing', 1.5)):
            with self.subTest(name=name, value=value):
                self.row.qty_produced, self.row.qty_producing = 0, 2
                setattr(self.row, name, value)
                with self.assertRaises(ValueError):
                    self.scope._check_quality_native_finish()

    def test_wrong_uom_category_is_rejected(self):
        self.row.product_uom_id = SimpleNamespace(category_id=2)
        with self.assertRaises(ValueError):
            self.scope._check_quality_native_finish()

    def test_start_waits_for_previous_operation_even_when_it_has_no_qc(self):
        source = SimpleNamespace(state='progress', _quality_native_checks=lambda: [],
                                 _quality_native_history=lambda: Rows())
        self.row._quality_native_predecessors = lambda: [source]
        del self.scope._check_quality_native_start
        with self.assertRaisesRegex(ValueError, 'Enterprise'):
            self.scope._check_quality_native_start()

    def test_completed_historical_operation_is_not_backfilled(self):
        self.row.state = 'done'
        self.scope._check_quality_native_finish()
        self.row._quality_native_units.assert_not_called()
        self.row._quality_validate_native_checks.assert_not_called()

    def test_unidentified_ordinary_operation_keeps_native_flow(self):
        self.row._quality_native_units.return_value = Rows()
        self.scope._check_quality_native_finish()
        self.row._check_quality_execution_access.assert_not_called()
        self.row._quality_validate_native_checks.assert_called_once()

    def test_start_validates_previous_quality_without_reading_own_future_qc(self):
        source = SimpleNamespace(state='done', quality_execution_ids=Rows(),
                                 _quality_native_history=lambda: Rows(),
                                 _quality_native_checks=Mock(return_value=['upstream']),
                                 _quality_validate_native_checks=Mock())
        self.row._quality_native_predecessors = lambda: [source]
        del self.scope._check_quality_native_start
        self.scope._check_quality_native_start()
        source._quality_validate_native_checks.assert_called_once_with(['upstream'], self.units)
        self.row._quality_native_checks.assert_not_called()

    def test_start_cannot_advance_from_unfinished_controlled_predecessor(self):
        source = SimpleNamespace(state='progress', quality_execution_ids=Rows(),
                                 _quality_native_history=lambda: Rows(),
                                 _quality_native_checks=lambda: ['upstream'])
        self.row._quality_native_predecessors = lambda: [source]
        del self.scope._check_quality_native_start
        with self.assertRaisesRegex(ValueError, 'Enterprise'):
            self.scope._check_quality_native_start()

    def _evidence(self):
        sample = SimpleNamespace(controlled_unit_id=self.unit)
        admission = SimpleNamespace(controlled_unit_id=self.unit, state='confirmed',
                                    _get_effectiveness_error=Mock(return_value=False))
        check = SimpleNamespace(_lock_unit_admission_scope=Mock(), sample_ids=Rows([sample]),
                                _get_unit_admission_error=Mock(return_value=False),
                                unit_admission_ids=Rows([admission]))
        return check, admission

    def test_completed_pass_with_effective_admission_is_accepted(self):
        check, _ = self._evidence()
        self.scope._quality_validate_native_checks([check], [self.unit])
        check._get_unit_admission_error.assert_called_once_with(check.sample_ids)

    def test_fail_pending_open_alert_and_missing_qcp_evidence_are_held(self):
        for reason in ('FAIL', 'Pending QC', 'Open Alert', 'Missing mandatory QCP'):
            with self.subTest(reason=reason):
                check, _ = self._evidence()
                check._get_unit_admission_error.return_value = reason
                with self.assertRaisesRegex(ValueError, reason):
                    self.scope._quality_validate_native_checks([check], [self.unit])

    def test_missing_revoked_or_ineffective_admission_is_held(self):
        for mode in ('missing', 'revoked', 'ineffective'):
            with self.subTest(mode=mode):
                check, admission = self._evidence()
                if mode == 'missing':
                    check.unit_admission_ids = Rows()
                elif mode == 'revoked':
                    admission.state = 'revoked'
                else:
                    admission._get_effectiveness_error.return_value = 'Alert'
                with self.assertRaises(ValueError):
                    self.scope._quality_validate_native_checks([check], [self.unit])

    def test_foreign_or_ambiguous_sample_cannot_identify_a_unit(self):
        for samples in (Rows(), Rows([SimpleNamespace(controlled_unit_id=99)]),
                        Rows([SimpleNamespace(controlled_unit_id=self.unit)] * 2)):
            check, _ = self._evidence()
            check.sample_ids = samples
            with self.assertRaises(ValueError):
                self.scope._quality_validate_native_checks([check], [self.unit])


class NativeParent(Recordset):
    def write(self, vals):
        for row in self:
            for key, value in vals.items():
                setattr(row, key, value)
        return 'native write'

    def button_start(self, bypass=False):
        self.bypasses.append(bypass)
        return 'native start'

    def button_finish(self):
        self.timer_open = False
        return self.write({'state': 'done', 'qty_produced': 2})


class TestNativeUnitSelection(unittest.TestCase):
    def setUp(self):
        self.scope = load({'_quality_native_units'})()
        self.scope.ensure_one = lambda: None
        self.scope.company_id, self.scope.product_id = 1, 2
        production = SimpleNamespace()
        self.units = Rows([SimpleNamespace(id=index, company_id=1, product_id=2,
                                          _quality_current_production=lambda: production)
                           for index in (11, 12)])
        production._full_release_units = lambda: self.units
        self.scope.production_id = production
        self.scope.quality_native_unit_ids = Rows()
        self.own, self.previous = Rows(), Rows()
        self.scope._quality_native_checks = lambda: SimpleNamespace(
            operation_run_id=SimpleNamespace(unit_ids=self.own))
        self.scope._quality_native_predecessors = lambda: SimpleNamespace(
            _quality_native_history=lambda: SimpleNamespace(
                filtered=lambda predicate: SimpleNamespace(controlled_unit_id=self.previous)))

    def test_equal_count_does_not_guess_unbound_identities(self):
        with self.assertRaises(ValueError):
            self.scope._quality_native_units()

    def test_explicit_choice_is_retained(self):
        self.scope.quality_native_unit_ids = Rows(self.units[:1])
        self.assertEqual(self.scope._quality_native_units(), self.units[:1])

    def test_own_qc_proves_full_identity_regardless_of_row_order(self):
        self.own = Rows(reversed(self.units))
        self.assertEqual(set(self.scope._quality_native_units().ids), {11, 12})

    def test_previous_confirmed_full_batch_needs_no_extra_selection(self):
        self.previous = self.units
        self.assertEqual(self.scope._quality_native_units(), self.units)

    def test_partial_preceding_evidence_is_not_automatic_identity(self):
        self.previous = Rows(self.units[:1])
        with self.assertRaises(ValueError):
            self.scope._quality_native_units()

    def test_foreign_unit_or_company_or_product_is_rejected(self):
        foreign = SimpleNamespace(id=99, company_id=1, product_id=2,
                                  _quality_current_production=lambda: self.scope.production_id)
        self.scope.quality_native_unit_ids = Rows([foreign])
        with self.assertRaises(ValueError):
            self.scope._quality_native_units()
        for name, value in (('company_id', 99), ('product_id', 99),
                            ('_quality_current_production', lambda: object())):
            with self.subTest(name=name):
                self.scope.quality_native_unit_ids = Rows(self.units[:1])
                original = getattr(self.units[0], name)
                setattr(self.units[0], name, value)
                with self.assertRaises(ValueError):
                    self.scope._quality_native_units()
                setattr(self.units[0], name, original)

    def test_unidentified_operation_keeps_an_empty_identity_set(self):
        self.units = Rows()
        self.assertFalse(self.scope._quality_native_units())


class TestNativeWorkorderTransactions(unittest.TestCase):
    def setUp(self):
        cls = load({'write', 'button_start', 'button_finish'}, NativeParent)
        self.wo = cls()
        self.wo.bypasses, self.wo.facts, self.wo.timer_open = [], [], True
        self.units = Rows([SimpleNamespace(id=11), SimpleNamespace(id=12)])
        self.row = SimpleNamespace(id=1, state='progress', qty_produced=0, qty_production=2,
                                   quality_execution_ids=[], _quality_native_units=lambda: self.units)
        self.wo.rows = [self.row]
        self.wo._check_quality_native_start = Mock()
        self.wo._check_quality_native_finish = Mock()
        self.wo._quality_native_history = lambda: Rows()
        self.wo.filtered = lambda predicate: self.wo if predicate(self.row) else Rows()
        self.wo.browse = lambda: Rows()
        self.wo.production_id = self.row.production_id = SimpleNamespace(
            _lock_quality_transfer_scope=Mock(), _full_release_units=lambda: self.units)
        @contextmanager
        def savepoint():
            snapshot = self.row.state, self.row.qty_produced, self.wo.timer_open, list(self.wo.facts)
            try:
                yield
            except Exception:
                self.row.state, self.row.qty_produced, self.wo.timer_open, facts = snapshot
                self.wo.facts[:] = facts
                raise
        self.registrar = SimpleNamespace(_record_native_completion=self.record)
        class Env:
            cr = SimpleNamespace(savepoint=savepoint)
            def __getitem__(env, name):
                return self.registrar
        self.wo.env = Env()

    def record(self, workorder, units):
        self.wo.facts.extend(units.ids)

    def test_bypass_true_and_false_retain_quality_gate_and_native_argument(self):
        for bypass in (False, True):
            self.assertEqual(self.wo.button_start(bypass=bypass), 'native start')
        self.assertEqual(self.wo.bypasses, [False, True])
        self.assertEqual(self.wo._check_quality_native_start.call_count, 2)

    def test_bypass_does_not_skip_quality_rejection(self):
        self.wo._check_quality_native_start.side_effect = ValueError('held')
        with self.assertRaisesRegex(ValueError, 'held'):
            self.wo.button_start(bypass=True)
        self.assertEqual(self.wo.bypasses, [])

    def test_native_completion_records_facts_after_native_write(self):
        self.assertEqual(self.wo.button_finish(), 'native write')
        self.assertEqual((self.row.state, self.row.qty_produced), ('done', 2))
        self.assertEqual(self.wo.facts, [11, 12])

    def test_fact_error_rolls_back_state_qty_facts_and_already_closed_timer(self):
        def failure(workorder, units):
            self.record(workorder, units)
            raise ValueError('post-fact failure')
        self.registrar._record_native_completion = failure
        with self.assertRaisesRegex(ValueError, 'post-fact'):
            self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced), ('progress', 0))
        self.assertTrue(self.wo.timer_open)
        self.assertEqual(self.wo.facts, [])

    def test_partial_direct_orm_quantity_is_rejected_before_mutation(self):
        with self.assertRaisesRegex(ValueError, 'Partial WO'):
            self.wo.write({'qty_produced': 1})
        self.assertEqual(self.row.qty_produced, 0)
        self.assertEqual(self.wo.facts, [])

    def test_done_retry_does_not_backfill_facts(self):
        self.row.state, self.row.qty_produced = 'done', 2
        self.wo.write({'state': 'done', 'qty_produced': 2})
        self.assertEqual(self.wo.facts, [])

    def test_completion_cannot_change_quantity_and_validate_old_value(self):
        with self.assertRaises(ValueError):
            self.wo.write({'state': 'done', 'qty_produced': 2, 'qty_producing': 1})
        self.assertEqual((self.row.state, self.row.qty_produced), ('progress', 0))
        self.assertEqual(self.wo.facts, [])


if __name__ == '__main__':
    unittest.main()
