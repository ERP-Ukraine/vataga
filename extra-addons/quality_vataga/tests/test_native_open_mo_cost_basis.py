"""Actual native cost inputs and the Backorder alternative, without QC mocks.

These probes do not implement or authorize a QUNIT partial-release action.
"""
from datetime import timedelta
import json
import logging

from odoo.tests import tagged

from . import test_native_open_mo_feasibility as feasibility
from .test_stock_release_native_contract import StockReleaseNativeCase
from ..models.native_backorder import NativeBackorderOperation

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'native_open_mo_feasibility')
class TestNativeOpenMoCostBasis(StockReleaseNativeCase):
    _timed_production = feasibility.TestNativeOpenMoFeasibility._timed_production

    def test_native_quantity_preparation_consumes_only_the_pending_remainder(self):
        production = self._timed_production()
        production.write({'qty_producing': 4})
        production._set_qty_producing()
        self.assertEqual(production.move_raw_ids.quantity, 8)
        production._post_inventory(cancel_backorder=False)
        consumed = production.move_raw_ids.filtered(lambda m: m.state == 'done')
        old_quantities = consumed.read(['quantity', 'product_uom_qty', 'state'])
        production.write({'qty_producing': 5})
        production._set_qty_producing()
        pending = production.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        self.assertEqual(pending.quantity, 2)
        self.assertEqual(consumed.read(['quantity', 'product_uom_qty', 'state']), old_quantities)
        production._post_inventory(cancel_backorder=False)
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(consumed.read(['quantity', 'product_uom_qty', 'state']), old_quantities)
        _logger.info('NATIVE_QUANTITY_PREPARATION: native _set_qty_producing consumed 8 then 2; no repeated quantity')

    def test_native_cumulative_cost_difference_tracks_new_closed_work(self):
        production = self._timed_production()
        workorder = production.workorder_ids
        checkpoint = workorder._cal_cost()
        previous = workorder.time_ids.read(['date_start', 'date_end', 'duration'])
        start = max(workorder.time_ids.mapped('date_end'))
        self.env['mrp.workcenter.productivity'].create(
            workorder._prepare_timeline_vals(0, start, start + timedelta(minutes=15)))
        cumulative = workorder._cal_cost()
        self.assertAlmostEqual(checkpoint, 60)
        self.assertAlmostEqual(cumulative, 75)
        self.assertAlmostEqual(cumulative - checkpoint, 15)
        self.assertEqual(workorder.time_ids.browse([r['id'] for r in previous]).read(
            ['date_start', 'date_end', 'duration']), previous)
        _logger.info('NATIVE_COST_DELTA: %s', json.dumps({
            'checkpoint': checkpoint, 'native_cumulative': cumulative,
            'new_work_cost': cumulative - checkpoint,
        }, sort_keys=True))

    def test_changed_rate_invalidates_naive_cumulative_difference(self):
        production = self._timed_production()
        workorder = production.workorder_ids
        checkpoint = workorder._cal_cost()
        timers = workorder.time_ids.read(['date_start', 'date_end', 'duration'])
        self.workcenter.costs_hour = 120
        cumulative = workorder._cal_cost()
        self.assertEqual(workorder.time_ids.read(['date_start', 'date_end', 'duration']), timers)
        self.assertAlmostEqual(cumulative - checkpoint, 60)
        _logger.info('NATIVE_COST_RATE_DRIFT: no new intervals, but native cost delta=%s', cumulative - checkpoint)

    def test_backorder_with_real_closed_work_preserves_parent_stock_history(self):
        production = self._timed_production()
        production.picking_type_id.create_backorder = 'ask'
        with NativeBackorderOperation(production, 4).activate():
            child = production._native_quality_complete()
        self.assertEqual((production.state, production.qty_produced), ('done', 4))
        self.assertEqual(child.product_qty, 1)
        self.assertEqual(child.quality_native_parent_id, production)
        self.assertEqual(self._stock(), (12, 4))
        parent_lines = production.move_finished_ids.filtered(lambda m: m.state == 'done').move_line_ids
        links = {line.id: line.consume_line_ids.ids for line in parent_lines}
        layers = production.move_finished_ids.stock_valuation_layer_ids
        layer_snapshot = layers.read(['quantity', 'value', 'stock_move_id'])
        timers = production.workorder_ids.time_ids
        timer_snapshot = timers.read(['date_start', 'date_end', 'duration'])
        self.assertFalse(child.workorder_ids.time_ids)
        start = max(timers.mapped('date_end'))
        self.env['mrp.workcenter.productivity'].create(
            child.workorder_ids._prepare_timeline_vals(0, start, start + timedelta(minutes=15)))
        with NativeBackorderOperation(child, 1).activate():
            self.assertFalse(child._native_quality_complete())
        self.assertEqual((child.state, child.qty_produced), ('done', 1))
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual({line.id: line.consume_line_ids.ids for line in parent_lines}, links)
        self.assertEqual(layers.read(['quantity', 'value', 'stock_move_id']), layer_snapshot)
        self.assertEqual(timers.read(['date_start', 'date_end', 'duration']), timer_snapshot)
        self.assertFalse(timers & child.workorder_ids.time_ids)
        orders = production | child
        raw = -sum(orders.move_raw_ids.stock_valuation_layer_ids.mapped('value'))
        output = sum(orders.move_finished_ids.stock_valuation_layer_ids.mapped('value'))
        self.assertAlmostEqual(raw, 100)
        self.assertAlmostEqual(production.workorder_ids._cal_cost(), 60)
        self.assertAlmostEqual(child.workorder_ids._cal_cost(), 15)
        self.assertAlmostEqual(output, 175)
        _logger.info('NATIVE_BACKORDER_TIMED: %s', json.dumps({
            'raw_value': raw, 'output_value': output, 'parent_work_cost': 60,
            'child_work_cost': 15, 'parent_links_unchanged': True,
            'parent_svl_unchanged': True, 'parent_intervals_unchanged': True,
        }, sort_keys=True))
