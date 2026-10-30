"""Phase 3 Enterprise ORM scenarios. Run only on a disposable Enterprise DB."""
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_quality_full_release import QualityFullReleaseCase


@tagged('post_install', '-at_install', 'quality_stock_phase3')
class TestQualityStandardRelease(QualityFullReleaseCase):
    def test_mixed_five_and_one_unit_retest_release_without_rechecking_four(self):
        production, check = self._prepared(quantity=5, result='fail')
        last = production.workorder_ids.sorted('id')[-1]
        last.button_start(bypass=True)
        last.button_finish()
        self.assertEqual((last.state, last.qty_produced), ('progress', 5))
        failed = check.sample_ids.filtered(lambda row: row.sample_result == 'fail').controlled_unit_id
        repeat = last.quality_execution_ids.filtered(
            lambda row: row.controlled_unit_id == failed)._create_control(last, self.point)
        self._fill(repeat)
        repeat.do_pass()
        last.button_finish()
        self.assertEqual(last.state, 'done')
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 5))
        self.assertEqual(len(production.native_stock_fact_ids), 5)
        self.assertEqual(len(repeat.sample_ids), 1)
        self.assertEqual(check.quality_state, 'fail')
        self.assertEqual(check.x_admitted_qty, 4)

    def test_stock_error_after_real_consumption_rolls_back_whole_transaction(self):
        production, check = self._prepared()
        before = self._snapshot(production)
        original = type(self.env['stock.move'])._action_done
        def fail_after_consumption(moves, cancel_backorder=False):
            result = original(moves, cancel_backorder=cancel_backorder)
            if production in moves.raw_material_production_id:
                raise UserError('Injected failure after native consumption')
            return result
        with patch.object(type(self.env['stock.move']), '_action_done', fail_after_consumption):
            with self.assertRaisesRegex(UserError, 'Injected'):
                production.button_mark_done()
        self.assertEqual(self._snapshot(production), before)
        self.assertFalse(production.native_stock_fact_ids)

    def test_fact_error_after_native_stock_rolls_back_stock_svl_and_facts(self):
        production, check = self._prepared()
        before = self._snapshot(production)
        original = type(production)._record_standard_stock_facts
        def fail_after_facts(record, units):
            original(record, units)
            raise UserError('Injected failure after native facts')
        with patch.object(type(production), '_record_standard_stock_facts', fail_after_facts):
            with self.assertRaisesRegex(UserError, 'Injected'):
                production.button_mark_done()
        self.assertEqual(self._snapshot(production), before)

    def test_native_consumption_dialog_does_not_create_stock_facts(self):
        production, check = self._prepared()
        action = {'type': 'ir.actions.act_window', 'res_model': 'mrp.consumption.warning'}
        original = type(production).pre_button_mark_done
        def show_dialog(records):
            original(records)
            return action
        with patch.object(type(production), 'pre_button_mark_done', show_dialog):
            self.assertEqual(production.button_mark_done(), action)
        self.assertNotEqual(production.state, 'done')
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(production.native_stock_fact_ids)

    def test_new_pending_qcp_before_post_inventory_rolls_back_everything(self):
        production, check = self._prepared()
        before = self._snapshot(production)
        original = type(production)._post_inventory
        def add_qcp(records, cancel_backorder=False):
            self._check(production, point=self.point.copy({'title': 'New mandatory QC'}),
                        workorder_id=production.workorder_ids.sorted('id')[-1].id)
            return original(records, cancel_backorder=cancel_backorder)
        with patch.object(type(production), '_post_inventory', add_qcp):
            with self.assertRaises(UserError):
                production.button_mark_done()
        self.assertEqual(self._snapshot(production), before)

    def test_partial_and_forged_backorder_context_cannot_close_full_batch(self):
        production, check = self._prepared()
        production.qty_producing = 1
        self._assert_denied_unchanged(production)
        production.qty_producing = 2
        self._assert_denied_unchanged(production, lambda: production.with_context(
            skip_backorder=True, mo_ids_to_backorder=production.ids).button_mark_done())

    def test_legacy_transfer_mo_stays_protected_and_keeps_identity_history(self):
        production, check = self._prepared()
        production._enable_quality_transfer_control()
        before = (check.read(), check.sample_ids.read(), check.sample_ids.controlled_unit_id.read(),
                  production.workorder_ids.quality_execution_ids.read())
        self._assert_denied_unchanged(production)
        self.assertEqual(before, (check.read(), check.sample_ids.read(),
                                 check.sample_ids.controlled_unit_id.read(),
                                 production.workorder_ids.quality_execution_ids.read()))

    def test_retired_actions_and_wizard_cannot_restore_old_release_flow(self):
        production, check = self._prepared()
        for call in (production.action_enable_quality_full_release_test,
                     production.action_open_quality_native_release,
                     lambda: self.env['quality.full.release.wizard'].create({'production_id': production.id})):
            with self.assertRaises((AccessError, UserError)):
                call()
        self.assertFalse(production.native_stock_fact_ids)
        self.assertFalse(self.env.ref('quality_vataga.mrp_production_full_release_test').active)
        view = etree.fromstring(production.get_view(view_type='form')['arch'])
        self.assertFalse(view.xpath("//button[@name='action_enable_quality_full_release_test' or @name='action_open_quality_native_release']"))

    def test_missing_final_stage_qcp_blocks_complete_interoperation_pass(self):
        production, check = self._prepared()
        self.point.copy({'title': 'Additional final-stage QCP', 'x_control_stage': 'final',
                         'quality_unit_route_required': False})
        self._assert_denied_unchanged(production)

    def test_plain_rpc_context_cannot_create_release_facts(self):
        production, check = self._prepared()
        before = self._snapshot(production)
        with self.assertRaises(UserError):
            self.env['quality.unit.stock.fact'].with_context(
                quality_vataga_native_release=True, standard_full={production.id: check.sample_ids.controlled_unit_id.ids}
            ).create({'production_id': production.id, 'unit_id': check.sample_ids[0].controlled_unit_id.id,
                      'move_line_id': production.move_finished_ids.move_line_ids[:1].id,
                      'request_key': 'forged'})
        self.assertEqual(self._snapshot(production), before)


@tagged('post_install', '-at_install', 'quality_stock_phase3')
class TestQualityStandardSerialRelease(QualityFullReleaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.tracking = 'serial'  # Disposable fixture only.

    def test_single_serial_mo_maps_one_qunit_to_one_actual_serial(self):
        production, check = self._prepared(quantity=1)
        lot = self.env['stock.lot'].create({
            'name': 'PHASE3-SERIAL-%s' % production.id, 'product_id': self.product.id,
            'company_id': production.company_id.id})
        production.lot_producing_id = lot
        before_units = check.sample_ids.controlled_unit_id.read(['name', 'lot_id'])
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 1))
        self.assertEqual(len(production.native_stock_fact_ids), 1)
        self.assertEqual(production.native_stock_fact_ids.unit_id, check.sample_ids.controlled_unit_id)
        self.assertEqual(production.native_stock_fact_ids.move_line_id.lot_id, lot)
        self.assertEqual(production.native_stock_fact_ids.move_line_id.quantity, 1)
        self.assertEqual(before_units, check.sample_ids.controlled_unit_id.read(['name', 'lot_id']))
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -2)
        before = self._snapshot(production)
        production.button_mark_done()
        self.assertEqual(before, self._snapshot(production))
