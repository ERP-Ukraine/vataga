"""Characterize native open-MO posting, NOT acceptance of QUNIT release.

These probes deliberately use ordinary MOs without QUNIT authority. They must
run on a disposable database. No posting, costing or valuation method is mocked.
The two-post probe documents why exposing _post_inventory alone is unsafe.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase


@tagged('post_install', '-at_install', 'native_open_mo_feasibility')
class TestNativeOpenMoFeasibility(StockReleaseNativeCase):
    def _timed_production(self):
        production = self._production()
        workorder = production.workorder_ids
        end = fields.Datetime.now().replace(microsecond=0)
        # Closed test interval only: this does not exercise Enterprise timers
        # or prove that Shop Floor starts its own timer.
        self.env['mrp.workcenter.productivity'].create(
            workorder._prepare_timeline_vals(0, end - timedelta(hours=1), end))
        self.assertGreater(workorder._cal_cost(), 0)
        return production

    def _post_cumulative(self, production, quantity):
        delta = quantity - production.qty_produced
        self.assertGreater(delta, 0)
        pending = production.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        pending.ensure_one()
        # Explicit native quantity entry in this fixed 2-components/unit BOM.
        # This fixture is not a component-consumption implementation.
        production.write({'qty_producing': quantity})
        pending.write({'quantity': delta * 2, 'picked': True})
        production._post_inventory(cancel_backorder=False)

    def test_first_four_can_post_without_splitting_the_mo(self):
        production = self._timed_production()
        ids = self.env['mrp.production'].search([]).ids
        self._post_cumulative(production, 4)
        self.assertEqual(production.qty_produced, 4)
        self.assertEqual(production.product_qty, 5)
        self.assertNotIn(production.state, ('done', 'cancel'))
        self.assertEqual(self._stock(), (12, 4))
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)
        finished = production.move_finished_ids.filtered(lambda m: m.state == 'done')
        self.assertEqual(sum(finished.mapped('quantity')), 4)
        self.assertEqual(sum(finished.stock_valuation_layer_ids.mapped('quantity')), 4)
        remaining = production.move_finished_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        self.assertEqual(sum(remaining.mapped('product_uom_qty')), 1)

    def test_second_post_rewrites_old_consumption_links_and_reuses_all_wo_cost(self):
        production = self._timed_production()
        self._post_cumulative(production, 4)
        first = production.move_finished_ids.filtered(lambda m: m.state == 'done')
        old_lines = first.move_line_ids
        old_consumption = old_lines.consume_line_ids
        self.assertTrue(old_consumption)
        first_layers = first.stock_valuation_layer_ids
        layer_snapshot = first_layers.read(['quantity', 'value', 'stock_move_id'])
        timer_snapshot = production.workorder_ids.time_ids.read(['date_start', 'date_end', 'duration'])
        self._post_cumulative(production, 5)
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(production.qty_produced, 5)
        # Native _post_inventory assigns this relation on ALL finished lines,
        # including those posted by the first call. Quantity alone hides this.
        self.assertNotEqual(set(old_lines.consume_line_ids.ids), set(old_consumption.ids))
        self.assertFalse(old_lines.consume_line_ids & old_consumption)
        self.assertEqual(first_layers.read(['quantity', 'value', 'stock_move_id']), layer_snapshot)
        self.assertEqual(production.workorder_ids.time_ids.read(['date_start', 'date_end', 'duration']), timer_snapshot)
        raw_value = -sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('value'))
        output_value = sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value'))
        wo_cost = production.workorder_ids._cal_cost()
        # Characterization of the blocker, NOT the desired business behavior:
        # unchanged WO intervals are included once per posting, hence twice.
        self.assertAlmostEqual(output_value, raw_value + 2 * wo_cost)
        self.assertGreater(output_value, raw_value + wo_cost)

    def test_error_after_real_posting_rolls_back_moves_quants_and_svl(self):
        production = self._timed_production()
        self.env.flush_all()
        models = ('stock.move', 'stock.move.line', 'stock.valuation.layer', 'mrp.production')
        before = {name: self.env[name].search([]).ids for name in models}
        quantities = self._stock()
        with self.assertRaisesRegex(UserError, 'Native feasibility rollback'):
            with self.env.cr.savepoint():
                self._post_cumulative(production, 4)
                self.assertEqual(self._stock(), (12, 4))
                raise UserError('Native feasibility rollback')
        self.env.invalidate_all()
        self.assertEqual({name: self.env[name].search([]).ids for name in models}, before)
        self.assertEqual(self._stock(), quantities)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(production.product_qty, 5)
