"""Actual shipped native bridge on disposable Community MRP/Stock, no QC mocks."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from ..models.native_backorder import NativeBackorderOperation, native_operation


@tagged('post_install', '-at_install')
class TestNativeBackorderBridge(StockReleaseNativeCase):
    def _complete(self, production, quantity):
        production.picking_type_id.create_backorder = 'ask'
        operation = NativeBackorderOperation(production, quantity)
        with operation.activate():
            children = production._native_quality_complete()
        self.assertIsNone(native_operation())
        return children

    def test_full_native_five_has_no_backorder(self):
        production = self._production()
        self.assertFalse(self._complete(production, 5))
        self.assertEqual((production.state, production.qty_produced), ('done', 5))
        self.assertEqual(self._stock(), (10, 5))

    def test_four_then_one_has_real_lineage_native_cost_and_unchanged_history(self):
        production = self._production()
        child = self._complete(production, 4)
        self.assertEqual(child.quality_native_parent_id, production)
        self.assertEqual(child.product_qty, 1)
        self.assertEqual(child.workorder_ids.quality_native_parent_workorder_id, production.workorder_ids)
        self.assertEqual(self._stock(), (12, 4))
        lines = production.move_finished_ids.move_line_ids
        links = {line.id: line.consume_line_ids.ids for line in lines}
        values = production.move_finished_ids.stock_valuation_layer_ids.read(['quantity', 'value', 'stock_move_id'])
        timers = production.workorder_ids.time_ids.read(['date_start', 'date_end', 'duration'])
        self.assertFalse(self._complete(child, 1))
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual({line.id: line.consume_line_ids.ids for line in lines}, links)
        self.assertEqual(production.move_finished_ids.stock_valuation_layer_ids.read(['quantity', 'value', 'stock_move_id']), values)
        self.assertEqual(production.workorder_ids.time_ids.read(['date_start', 'date_end', 'duration']), timers)
        self.assertFalse(production.workorder_ids.time_ids & child.workorder_ids.time_ids)
        productions = production | child
        self.assertAlmostEqual(sum(productions.move_raw_ids.stock_valuation_layer_ids.mapped('value')), -100)
        self.assertAlmostEqual(sum(productions.move_finished_ids.stock_valuation_layer_ids.mapped('quantity')), 5)
        self.assertAlmostEqual(sum(productions.move_finished_ids.stock_valuation_layer_ids.mapped('value')),
                               100 + sum(wo._cal_cost() for wo in productions.workorder_ids))

    def test_exception_rolls_back_native_stock_and_backorder_and_clears_capability(self):
        production = self._production()
        ids = self.env['mrp.production'].search([]).ids
        values = self.env['stock.valuation.layer'].search([]).ids
        with self.assertRaisesRegex(UserError, 'audit failed'):
            with self.env.cr.savepoint():
                self._complete(production, 4)
                raise UserError('audit failed')
        self.env.invalidate_all()
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)
        self.assertEqual(self.env['stock.valuation.layer'].search([]).ids, values)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(self._stock(), (20, 0))
        self.assertIsNone(native_operation())

    def test_rpc_context_and_direct_field_writes_do_not_create_native_capability(self):
        production = self._production()
        with self.assertRaises(UserError):
            production.with_context(quality_native_release=True, skip_backorder=True)._native_quality_complete()
        with self.assertRaises(UserError):
            production.write({'quality_native_parent_id': production.id})
        with self.assertRaises(UserError):
            production.workorder_ids.write({'quality_native_parent_workorder_id': production.workorder_ids.id})
        with self.assertRaises(UserError):
            self.env['mrp.production'].create({'quality_native_parent_id': production.id})
        self.assertIsNone(native_operation())

    def test_already_completed_mo_cannot_be_posted_again(self):
        production = self._production()
        self._complete(production, 5)
        before = (self._stock(), self.env['stock.valuation.layer'].search([]).ids)
        with self.assertRaises(UserError):
            self._complete(production, 5)
        self.assertEqual(before, (self._stock(), self.env['stock.valuation.layer'].search([]).ids))

    def test_repair_time_capability_cannot_complete_split_or_post_stock(self):
        production = self._production()
        with NativeBackorderOperation(production, 0).activate():
            for operation in (production.button_mark_done, production._post_inventory,
                              production._split_productions, production.move_finished_ids._action_done):
                with self.assertRaises(UserError):
                    operation()
        self.assertEqual(self._stock(), (20, 0))

    def test_native_workorder_copy_preserves_actual_two_operation_dependencies(self):
        self.bom.operation_ids.create({
            'name': 'Native second operation', 'workcenter_id': self.workcenter.id, 'bom_id': self.bom.id})
        production = self._production()
        production.allow_workorder_dependencies = True
        first, second = production.workorder_ids.sorted('id')
        second.blocked_by_workorder_ids = [(6, 0, first.ids)]
        child = self._complete(production, 4)
        mapped_first = child.workorder_ids.filtered(lambda row: row.quality_native_parent_workorder_id == first)
        mapped_second = child.workorder_ids.filtered(lambda row: row.quality_native_parent_workorder_id == second)
        self.assertEqual(mapped_second.blocked_by_workorder_ids, mapped_first)
        self.assertEqual(second.blocked_by_workorder_ids, first)

    def test_recorded_component_consumption_is_not_silently_resized(self):
        production = self._production()
        production.move_raw_ids.write({'quantity': 10, 'picked': True})
        with self.assertRaises(UserError):
            self._complete(production, 4)
        self.assertEqual(production.move_raw_ids.quantity, 10)
        self.assertEqual(self._stock(), (20, 0))

    def test_consumable_finished_product_is_not_claimed_as_stock_tracked_output(self):
        self.product.type = 'consu'
        production = self._production()
        with self.assertRaises(UserError):
            self._complete(production, 5)
        self.assertEqual(self._stock(), (20, 0))
