"""Actual 3/5 native stock + quality integration; disposable Enterprise DB only."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_quality_full_release import QualityFullReleaseCase


@tagged('post_install', '-at_install', 'quality_actual_batch')
class TestQualityActualBatchRelease(QualityFullReleaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bom.operation_ids.sorted('id')[1].unlink()
        cls.warehouse.manu_type_id.create_backorder = 'ask'

    def _actual(self, result='pass', admit=True, finish=True):
        self.point.picking_type_ids = [Command.clear()]
        self.point.quality_unit_route_required = False
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 5,
            'product_uom_id': self.product.uom_id.id, 'bom_id': self.bom.id,
            'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id})
        production.action_confirm()
        production.action_assign()
        production.qty_producing = 3
        production._set_qty_producing()
        first, last = production.workorder_ids.sorted('id')
        first.button_start()
        first.button_finish()
        last.button_start(bypass=True)
        self.point.picking_type_ids = [Command.set(self.warehouse.manu_type_id.ids)]
        check = self._check(production, workorder_id=last.id)
        self._fill(check)
        if result == 'fail':
            check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
            check.do_fail()
        elif result == 'pass':
            check.do_pass()
        else:
            check.initialize_qc_unit_identity()
        if finish:
            last.button_finish()
        else:
            last.button_pending()
        if not admit:
            for admission in check.unit_admission_ids:
                admission._revoke('Disposable missing admission')
        return production, check

    def _children(self, production):
        return self.env['mrp.production'].search([('quality_native_parent_id', '=', production.id)])

    def _denied(self, production, call=None):
        before = self._snapshot(production)
        children = self._children(production)
        with self.assertRaises(UserError):
            (call or production.action_release_quality_actual_batch)()
        self.assertEqual(self._snapshot(production), before)
        self.assertEqual(self._children(production), children)

    def test_p1_n8_n13_n14_three_of_five_real_stock_cost_and_native_remainder(self):
        production, check = self._actual()
        first, last = production.workorder_ids.sorted('id')
        self.assertEqual((first.state, first.qty_produced, last.state, last.qty_produced),
                         ('done', 3, 'progress', 3))
        self.assertFalse(first.quality_execution_ids)
        self.assertEqual(len(last.quality_execution_ids), 3)
        units = check.sample_ids.controlled_unit_id
        original = (check.sample_ids.read(), check.sample_ids.measurement_value_ids.read(),
                    last.quality_execution_ids.read())
        unit_ids = self.env['quality.production.unit'].search([]).ids
        stock_before = self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id)
        production.action_release_quality_actual_batch()
        self.assertEqual((production.state, production.product_qty, production.qty_produced), ('done', 3, 3))
        child = self._children(production)
        self.assertEqual((len(child), child.product_qty, child.quality_native_parent_id), (1, 2, production))
        self.assertEqual(child.procurement_group_id, production.procurement_group_id)
        self.assertFalse(child._full_release_units())
        self.assertFalse(child.quality_transfer_controlled or child.quality_unit_route_required)
        self.assertEqual(set(child.workorder_ids.quality_native_parent_workorder_id.ids), set(production.workorder_ids.ids))
        self.assertEqual(set(child.workorder_ids.mapped('qty_produced')), {0})
        self.assertEqual(set(child.workorder_ids.mapped('qty_reported_from_previous_wo')), {0})
        self.assertEqual(set(production.workorder_ids.mapped('state')), {'done'})
        self.assertEqual(production.native_stock_fact_ids.unit_id, units)
        self.assertEqual(len(production.native_stock_fact_ids), 3)
        self.assertEqual(sum(production.native_stock_fact_ids.move_line_id.mapped('quantity')), 3)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id) - stock_before, 3)
        raw = production.move_raw_ids.filtered(lambda move: move.state == 'done')
        finished = production.move_finished_ids.filtered(lambda move: move.state == 'done')
        self.assertEqual(sum(raw.mapped('quantity')), 6)
        self.assertEqual(sum(raw.stock_valuation_layer_ids.mapped('quantity')), -6)
        self.assertEqual(sum(finished.stock_valuation_layer_ids.mapped('quantity')), 3)
        self.assertAlmostEqual(sum(finished.stock_valuation_layer_ids.mapped('value')),
                               -sum(raw.stock_valuation_layer_ids.mapped('value'))
                               + sum(wo._cal_cost() for wo in production.workorder_ids))
        self.assertEqual(set(finished.move_line_ids.consume_line_ids.ids), set(raw.move_line_ids.ids))
        self.assertEqual(self.env['quality.production.unit'].search([]).ids, unit_ids)
        self.assertEqual((check.sample_ids.read(), check.sample_ids.measurement_value_ids.read(),
                          last.quality_execution_ids.read()), original)
        self.assertFalse(first.quality_execution_ids)
        self._denied(production)  # Repeated user click cannot post again.

    def test_n1_n2_fail_and_pending_block_whole_actual_batch(self):
        for result in ('fail', 'pending'):
            with self.subTest(result=result):
                production, check = self._actual(result=result)
                self._denied(production)

    def test_n3_revoked_admission_blocks_release(self):
        production, check = self._actual(admit=False)
        self._denied(production)

    def test_n4_open_hold_after_pass_blocks_release(self):
        production, check = self._actual()
        alert = self.env['quality.alert'].create({'name': 'Actual batch hold', 'check_id': check.id, 'team_id': self.team.id})
        alert._bind_matrix_failure_scope(check.sample_ids[:1], 'Unresolved defect')
        self._denied(production)

    def test_n5_unfinished_early_operation_blocks_release(self):
        production, check = self._actual()
        operation = self.env['mrp.routing.workcenter'].create({
            'name': 'Additional required operation', 'bom_id': self.bom.id,
            'workcenter_id': production.workorder_ids[0].workcenter_id.id, 'sequence': 0})
        self.env['mrp.workorder'].create({
            'production_id': production.id, 'name': operation.name, 'operation_id': operation.id,
            'workcenter_id': operation.workcenter_id.id, 'product_uom_id': production.product_uom_id.id})
        self._denied(production)

    def test_n5_missing_final_execution_blocks_release(self):
        production, check = self._actual(finish=False)
        self._denied(production)

    def test_n6_other_production_units_rejected(self):
        production, check = self._actual()
        other, foreign = self._actual()
        self._denied(production, lambda: production._validate_quality_actual_batch(
            foreign.sample_ids.controlled_unit_id, 3))

    def test_n7_previously_posted_units_rejected(self):
        production, check = self._actual()
        production.action_release_quality_actual_batch()
        with self.assertRaises(UserError):
            check.sample_ids.controlled_unit_id._assert_not_stock_posted()
        self._denied(production)

    def test_n9_missing_backorder_rolls_back_all_native_facts(self):
        production, check = self._actual()
        original = type(self.env['mrp.production.backorder']).action_backorder
        def incomplete(wizard):
            original(wizard)
            from ..models.native_backorder import native_operation
            native_operation().children.clear()
        with patch.object(type(self.env['mrp.production.backorder']), 'action_backorder', incomplete):
            self._denied(production)

    def test_n10_stock_error_after_consumption_rolls_back(self):
        production, check = self._actual()
        original = type(self.env['stock.move'])._action_done
        def fail(moves, cancel_backorder=False):
            result = original(moves, cancel_backorder=cancel_backorder)
            if production in moves.raw_material_production_id:
                raise UserError('Injected failure after consumption')
            return result
        with patch.object(type(self.env['stock.move']), '_action_done', fail):
            self._denied(production)

    def test_n10_fact_failure_after_stock_rolls_back(self):
        production, check = self._actual()
        original = type(production)._record_standard_stock_facts
        def fail(record, units):
            original(record, units)
            raise UserError('Injected failure after stock facts')
        with patch.object(type(production), '_record_standard_stock_facts', fail):
            self._denied(production)

    def test_n12_required_route_and_client_context_cannot_open_release(self):
        production, check = self._actual()
        production.action_require_quality_unit_route()
        self._denied(production, lambda: production.with_context(
            quality_actual_batch=True, skip_backorder=True, skip_quality=True,
            mo_ids_to_backorder=production.ids).action_release_quality_actual_batch())

    def test_new_pending_requirement_at_inventory_boundary_rolls_back(self):
        production, check = self._actual()
        original = type(production)._post_inventory
        def add_check(records, cancel_backorder=False):
            if production in records:
                self._check(production, point=self.point.copy({'title': 'Late mandatory check'}),
                            workorder_id=production.workorder_ids.sorted('id')[-1].id)
            return original(records, cancel_backorder=cancel_backorder)
        with patch.object(type(production), '_post_inventory', add_check):
            self._denied(production)

    def test_fixed_consumption_is_not_overwritten(self):
        production, check = self._actual()
        production.move_raw_ids.write({'quantity': 10, 'picked': True})
        self._denied(production)

    def test_plain_manufacturing_still_uses_native_completion(self):
        # Existing stock regression exercises a genuinely uncontrolled order.
        from .test_quality_full_release import TestQualityFullRelease
        TestQualityFullRelease.test_n10_ordinary_mo_still_uses_native_standard_completion(self)
