"""Execute actual-batch, lot and QC gates together without an Odoo database.

These are policy/adapter tests. They do not simulate quants, valuation,
PostgreSQL concurrency or claim that native stock has actually been posted.
"""
import math
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError, AccessError
    from . import test_quality_full_release_contract as full
    from .test_quality_full_release_contract import Record, Rows
    from .test_quality_full_release_lot_contract import Lot, EmptyLot
    from .test_quality_standard_release_contract import capability
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError, AccessError
    import test_quality_full_release_contract as full
    from test_quality_full_release_contract import Record, Rows
    from test_quality_full_release_lot_contract import Lot, EmptyLot
    from test_quality_standard_release_contract import capability


class TestActualBatchLotPolicy(unittest.TestCase):
    def setUp(self):
        self.qc = full.TestFullBatchReadinessContract()
        self.qc.setUp()
        p = self.production = self.qc.production
        actual = shipped_class('models/quality_actual_batch_release.py', 'QualityActualBatchProduction',
                               {'_validate_quality_actual_batch'}, math=math, _read=lambda rows: rows)
        lot_policy = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                                  {'_validate_full_release_lot', '_check_full_release_alerts'}, _read=self.read)
        p._validate_quality_actual_batch = actual._validate_quality_actual_batch.__get__(p)
        p._validate_full_release_lot = lot_policy._validate_full_release_lot.__get__(p)
        p._check_full_release_alerts = lot_policy._check_full_release_alerts.__get__(p)
        p.product_qty, p.qty_producing, p.allow_workorder_dependencies = 3, 2, False
        p.product_id.tracking = 'lot'
        self.lot = Lot(id=80, product_id=p.product_id, company_id=p.company_id)
        p.lot_producing_id, p.move_finished_ids = self.lot, Rows()
        self.units = self.qc.units
        run = self.qc.run
        run.state, run.quantity, run.production_id = 'registered', 2, p
        run.product_id, run.company_id, run.unit_ids = p.product_id, p.company_id, self.units
        for unit in self.units:
            unit.production_id, unit.registration_run_id, unit.lot_id = p, run, False
        for workorder in self.qc.workorders:
            workorder.operation_id.sequence = workorder.id
            workorder._quality_native_history = lambda: Rows()
        last = self.qc.workorders[-1]
        last.state = 'progress'
        history = Rows([Record(id=unit.id, controlled_unit_id=unit, state='processed') for unit in self.units])
        last._quality_native_history = lambda: history
        self.alert_open, self.hidden_lot = False, False
        self.alert = Record(id=99, display_name='Hold', vataga_failure_sample_ids=self.qc.samples[:1],
                            _matrix_hold_is_open=lambda: self.alert_open)
        original_env = p.env
        class Env:
            companies = original_env.companies
            cr = NS(execute=Mock(), fetchall=lambda: [(99,)])
            def ref(env, name):
                return original_env.ref(name)
            def __getitem__(env, name):
                if name == 'stock.lot':
                    return EmptyLot()
                if name == 'quality.alert':
                    return NS(_fields={}, flush_model=Mock(), browse=lambda ids: Rows([self.alert]))
                if name == 'quality.check.sample':
                    return NS(flush_model=Mock())
                return original_env[name]
        p.env = Env()

    def read(self, records):
        if getattr(self, 'hidden_lot', False) and records is self.lot:
            raise AccessError('lot is not readable')
        return records

    def validate(self):
        return self.production._validate_quality_actual_batch(self.units, 2)

    def test_t1_two_of_three_with_real_lot_pass_and_effective_admissions(self):
        self.assertTrue(self.validate())
        self.assertTrue(all(not unit.lot_id for unit in self.units))
        # At inventory time native splitting has changed the source plan to 2.
        self.production.product_qty = 2
        self.qc.workorders[-1].state = 'done'
        self.assertTrue(self.validate())

    def test_t2_missing_lot_explains_native_field(self):
        self.production.lot_producing_id = False
        with self.assertRaisesRegex(UserError, 'штатному полі'):
            self.validate()

    def test_t3_t4_foreign_product_and_company_are_rejected(self):
        for name in ('product_id', 'company_id'):
            original = getattr(self.lot, name)
            setattr(self.lot, name, Record(id=999))
            with self.subTest(field=name), self.assertRaisesRegex(UserError, 'іншому товару або компанії'):
                self.validate()
            setattr(self.lot, name, original)

    def test_t5_conflicting_registered_lot_is_never_relabelled(self):
        other = Lot(id=81, product_id=self.production.product_id, company_id=self.production.company_id)
        self.units[0].lot_id = other
        with self.assertRaisesRegex(UserError, 'зафіксованій партії'):
            self.validate()
        self.assertIs(self.units[0].lot_id, other)
        self.assertIs(self.production.lot_producing_id, self.lot)

    def test_registered_matching_lot_and_company_neutral_lot_are_valid(self):
        for unit in self.units:
            unit.lot_id = self.lot
        self.assertTrue(self.validate())
        self.lot.company_id = False
        self.assertTrue(self.validate())

    def test_t6_t7_mixed_pending_and_fail_never_release_pass_neighbour(self):
        for result in ('pending', 'fail'):
            self.qc.samples[-1].sample_result = result
            self.qc.check.quality_state = 'none' if result == 'pending' else 'fail'
            with self.subTest(result=result), self.assertRaises(UserError):
                self.validate()

    def test_t8_open_alert_blocks_after_pass_and_admission(self):
        self.alert_open = True
        with self.assertRaisesRegex(UserError, 'Hold'):
            self.validate()

    def test_revoked_missing_or_ineffective_admissions_block(self):
        self.qc.admitted.clear()
        with self.assertRaises(UserError):
            self.validate()
        self.qc.admitted.update(self.units.ids)
        self.qc.admission_error = 'revoked'
        with self.assertRaises(UserError):
            self.validate()

    def test_t12_already_posted_unit_or_completed_mo_is_rejected(self):
        self.units[0].posted = True
        with self.assertRaises(UserError):
            self.validate()
        self.units[0].posted = False
        self.production.state, self.production.qty_produced = 'done', 2
        with self.assertRaises(UserError):
            self.validate()

    def test_t14_untracked_uses_same_qc_gates(self):
        self.production.product_id.tracking, self.production.lot_producing_id = 'none', False
        self.assertTrue(self.validate())

    def test_t15_serial_has_specific_refusal(self):
        self.production.product_id.tracking = 'serial'
        with self.assertRaisesRegex(UserError, 'серійним відстеженням не підтримується'):
            self.validate()

    def test_t17_duplicate_or_foreign_identity_is_rejected(self):
        duplicate = Rows([self.units[0], self.units[0]])
        with self.assertRaises(UserError):
            self.production._validate_quality_actual_batch(duplicate, 2)
        self.units[0].production_id = Record(id=999)
        with self.assertRaises(UserError):
            self.validate()

    def test_registration_product_and_company_cannot_disagree(self):
        for name in ('product_id', 'company_id'):
            original = getattr(self.qc.run, name)
            setattr(self.qc.run, name, Record(id=999))
            with self.subTest(field=name), self.assertRaises(UserError):
                self.validate()
            setattr(self.qc.run, name, original)

    def test_prepared_finished_line_with_other_lot_is_not_rewritten(self):
        p = self.production
        other = Lot(id=81, product_id=p.product_id, company_id=p.company_id)
        line = Record(id=90, quantity=2, lot_id=other)
        p.move_finished_ids = Rows([Record(id=91, product_id=p.product_id, state='assigned', move_line_ids=Rows([line]))])
        with self.assertRaisesRegex(UserError, 'іншу партію'):
            self.validate()
        self.assertIs(line.lot_id, other)

    def test_missing_lot_read_access_cannot_be_bypassed(self):
        self.hidden_lot = True
        with self.assertRaises(AccessError):
            self.validate()


class TestLotNativeAdapter(unittest.TestCase):
    """Native callbacks are test doubles: assert orchestration, never stock."""
    def setUp(self):
        self.scope = capability()
        cls = shipped_class('models/native_backorder.py', 'NativeBackorderProduction',
                            {'_native_quality_complete'}, native_operation=self.scope['native_operation'])
        p = self.production = cls()
        p.ensure_one = Mock()
        p.id, p.state, p.product_qty, p.qty_produced, p.qty_producing = 1, 'progress', 3, 0, 2
        p.product_id = Record(id=7, tracking='lot', type='product')
        p.company_id = Record(id=8)
        p.picking_type_id = NS(create_backorder='ask')
        p.move_byproduct_ids, p.move_raw_ids, p.workorder_ids = Rows(), Rows(), Rows()
        p.lot_producing_id = Lot(id=80, product_id=p.product_id, company_id=p.company_id)
        p.lot_producing_id.check_access_rights = Mock()
        p.lot_producing_id.check_access_rule = Mock()
        p._set_qty_producing = Mock()
        self.child = Record(id=2, product_qty=1, workorder_ids=Rows(), _fields={})
        p.browse = lambda ids: Rows([self.child]) if ids else Rows()
        self.operation = self.scope['NativeBackorderOperation'](
            p, 2, (501, 502), actual_batch=True, finished_lot_id=80)
        p.button_mark_done = Mock(return_value={
            'res_model': 'mrp.production.backorder', 'context': {'default_mrp_production_ids': [1]}})
        def backorder():
            self.operation.children.add(2)
            p.state, p.product_qty, p.qty_produced = 'done', 2, 2
        self.wizard = NS(action_backorder=Mock(side_effect=backorder))
        self.create = Mock(return_value=self.wizard)
        self.context = Mock(return_value=NS(create=self.create))
        p.env = {'mrp.production.backorder': NS(with_context=self.context)}

    def complete(self):
        with self.operation.activate():
            return self.production._native_quality_complete()

    def test_t1_native_quantity_preparation_and_backorder_wizard_are_used(self):
        self.assertEqual(self.complete().ids, [2])
        self.production._set_qty_producing.assert_called_once()
        self.production.button_mark_done.assert_called_once()
        self.context.assert_called_once_with({'default_mrp_production_ids': [1]})
        self.create.assert_called_once_with({})
        self.wizard.action_backorder.assert_called_once()
        self.assertIsNone(self.scope['native_operation']())

    def test_legacy_partial_lot_capability_does_not_gain_actual_batch_permission(self):
        self.operation.actual_batch = False
        with self.assertRaises(UserError):
            self.complete()
        self.production.button_mark_done.assert_not_called()

    def test_wrong_dialog_or_wrong_remainder_aborts(self):
        self.production.button_mark_done.return_value = {'res_model': 'mrp.consumption.warning'}
        with self.assertRaises(UserError):
            self.complete()
        self.wizard.action_backorder.assert_not_called()
        self.assertIsNone(self.scope['native_operation']())
        self.production.button_mark_done.return_value = {
            'res_model': 'mrp.production.backorder', 'context': {}}
        self.child.product_qty = 2
        with self.assertRaises(UserError):
            self.complete()

    def test_t15_serial_is_rejected_before_any_native_preparation(self):
        self.production.product_id.tracking = 'serial'
        with self.assertRaisesRegex(UserError, 'серійне відстеження'):
            self.complete()
        self.production._set_qty_producing.assert_not_called()

    def test_native_error_always_resets_capability(self):
        self.wizard.action_backorder.side_effect = UserError('stock failed')
        with self.assertRaisesRegex(UserError, 'stock failed'):
            self.complete()
        self.assertIsNone(self.scope['native_operation']())

    def test_backorder_defaults_clear_lot_and_quantity_only_in_actual_batch(self):
        class Parent:
            def _get_backorder_mo_vals(self):
                return {'lot_producing_id': 80, 'qty_producing': 2}
        cls = shipped_class('models/native_backorder.py', 'NativeBackorderProduction',
                            {'_get_backorder_mo_vals'}, parent=Parent, native_operation=self.scope['native_operation'])
        p = cls()
        p.id = 1
        self.operation.production = p
        with self.operation.activate():
            self.assertEqual(p._get_backorder_mo_vals(), {
                'quality_native_parent_id': 1, 'lot_producing_id': False, 'qty_producing': 0})
        self.assertEqual(p._get_backorder_mo_vals(), {'lot_producing_id': 80, 'qty_producing': 2})


if __name__ == '__main__':
    unittest.main()
