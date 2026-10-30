"""Native adapter integration, real MRP/Stock/SVL. No posting/price mocks."""
from datetime import timedelta
import logging
import os
import threading
import time
from unittest.mock import patch

import psycopg2

from odoo import api, fields, sql_db, SUPERUSER_ID
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from ..models.native_partial_release import partial_operation

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'native_partial_adapter')
class TestNativePartialAdapter(StockReleaseNativeCase):
    def _timed(self):
        production = self._production()
        end = fields.Datetime.now().replace(microsecond=0) - timedelta(minutes=30)
        self.env['mrp.workcenter.productivity'].create(
            production.workorder_ids._prepare_timeline_vals(0, end - timedelta(hours=1), end))
        return production

    def _new_work(self, production):
        workorder = production.workorder_ids
        start = max(workorder.time_ids.mapped('date_end'))
        return self.env['mrp.workcenter.productivity'].create(
            workorder._prepare_timeline_vals(0, start, start + timedelta(minutes=15)))

    def _release(self, production, keys=('1', '2', '3', '4'), request='first'):
        return production._native_partial_release(list(keys), request)

    def _snapshot(self, production):
        self.env.flush_all()
        models = ('stock.move', 'stock.move.line', 'stock.valuation.layer',
                  'quality.native.partial.release', 'quality.native.partial.unit', 'quality.native.partial.cost')
        return {'ids': {n: self.env[n].search([]).ids for n in models}, 'stock': self._stock(),
                'produced': production.qty_produced,
                'mo': production.read(['state', 'qty_producing', 'date_finished', 'priority', 'is_locked']),
                'workorders': production.workorder_ids.sorted('id').read(
                    ['state', 'qty_produced', 'date_start', 'date_finished', 'costs_hour']),
                'timers': production.workorder_ids.time_ids.sorted('id').read(['date_start', 'date_end', 'duration']),
                'checkpoint': production._native_partial_history().cost_ids.sorted('id').read(['cumulative_cost', 'basis', 'timer_ids']),
                'lines': production.move_finished_ids.move_line_ids.read(['quantity', 'consume_line_ids']),
                'svl': (production.move_raw_ids | production.move_finished_ids).stock_valuation_layer_ids.read(
                    ['quantity', 'value', 'remaining_qty', 'remaining_value'])}

    def _denied(self, production, call):
        before = self._snapshot(production)
        with self.assertRaises(UserError):
            call()
        self.assertEqual(self._snapshot(production), before)
        self.assertIsNone(partial_operation())

    def test_four_then_one_140_plus_35_equals_175_and_immutable_history(self):
        production = self._timed()
        ids = self.env['mrp.production'].search([]).ids
        first = self._release(production)
        first_lines = first.unit_ids.move_line_id
        first_layers = first_lines.move_id.stock_valuation_layer_ids
        self.assertEqual((production.state, production.qty_produced), ('progress', 4))
        self.assertEqual(self._stock(), (12, 4))
        self.assertAlmostEqual(sum(first_layers.mapped('value')), 140)
        links = first_lines.read(['quantity', 'consume_line_ids'])
        svl = first_layers.read(['quantity', 'value', 'remaining_qty', 'remaining_value'])
        timers = production.workorder_ids.time_ids
        old_timers = timers.read(['date_start', 'date_end', 'duration'])
        self._new_work(production)
        second = self._release(production, ('5',), 'second')
        second_layers = second.unit_ids.move_line_id.move_id.stock_valuation_layer_ids
        self.assertAlmostEqual(sum(second_layers.mapped('value')), 35)
        self.assertAlmostEqual(sum((first_layers | second_layers).mapped('value')), 175)
        self.assertEqual(first_lines.read(['quantity', 'consume_line_ids']), links)
        self.assertEqual(first_layers.read(['quantity', 'value', 'remaining_qty', 'remaining_value']), svl)
        self.assertEqual(timers.read(['date_start', 'date_end', 'duration']), old_timers)
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(production.qty_produced, 5)
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)
        self.assertAlmostEqual(second.cost_ids.cumulative_cost, 75)
        before = self._snapshot(production)
        production.button_mark_done()  # Closed by native state computation: no second Stock.
        self.assertEqual(self._snapshot(production), before)
        _logger.info('PARTIAL_ADAPTER_PASS: 140 + 35 = 175; raw=100, WO=75; old links/SVL/timers unchanged; no Backorder')

    def test_idempotent_same_request_and_deny_overlapping_units(self):
        production = self._timed()
        first = self._release(production)
        before = self._snapshot(production)
        self.assertEqual(self._release(production), first)
        self.assertEqual(self._snapshot(production), before)
        self._denied(production, lambda: self._release(production, request='another'))
        self._denied(production, lambda: self._release(production, ('4', '5'), 'overlap'))
        self._denied(production, lambda: self._release(production, ('5',), 'first'))

    def test_rate_drift_rejects_without_stock_or_checkpoint_changes(self):
        production = self._timed()
        self._release(production)
        self.workcenter.costs_hour = 120
        self._denied(production, lambda: self._release(production, ('5',), 'next'))

    def test_capitalized_timer_edits_and_deletion_are_denied(self):
        production = self._timed()
        self._release(production)
        timer = production.workorder_ids.time_ids
        self._denied(production, lambda: timer.write({'date_end': timer.date_end + timedelta(minutes=1)}))
        self._denied(production, timer.unlink)

    def test_open_timer_rejected(self):
        production = self._timed()
        start = fields.Datetime.now()
        self.env['mrp.workcenter.productivity'].create(production.workorder_ids._prepare_timeline_vals(0, start))
        self._denied(production, lambda: self._release(production))

    def test_new_work_overlapping_a_checkpoint_is_rejected(self):
        production = self._timed()
        self._release(production)
        timer = production.workorder_ids.time_ids
        self.env['mrp.workcenter.productivity'].create(production.workorder_ids._prepare_timeline_vals(
            0, timer.date_start, timer.date_end))
        self._denied(production, lambda: self._release(production, ('5',), 'overlap-work'))

    def test_stock_remainder_cannot_be_completed_or_relinked_directly(self):
        production = self._timed()
        self._release(production)
        pending = production.move_finished_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        self._denied(production, lambda: pending.write({'state': 'done'}))
        self._denied(production, lambda: pending.write({'quantity': 1, 'picked': True}))
        self._denied(production, pending._action_done)
        self._denied(production, lambda: pending.write({'production_id': False}))

    def test_remaining_native_wo_can_close_without_posting_stock(self):
        production = self._timed()
        self._release(production)
        before = self._stock()
        production.qty_producing = 5
        production.workorder_ids.button_finish()
        self.assertEqual(self._stock(), before)
        self.assertEqual(production.qty_produced, 4)
        self.assertEqual(production.state, 'to_close')
        self._new_work(production)
        self._release(production, ('5',), 'final')
        self.assertEqual(production.state, 'done')

    def test_rpc_context_cannot_create_or_edit_a_checkpoint(self):
        production = self._timed()
        self._denied(production, lambda: self.env['quality.native.partial.release'].with_context(
            quality_native_partial=True).create({'production_id': production.id, 'request_key': 'forged',
                'before_quantity': 0, 'quantity': 4, 'configuration': {}}))
        self._denied(production, lambda: production.write({'quality_native_partial_revision': 0}))

    def test_unknown_cost_provider_rejected_before_stock(self):
        production = self._timed()
        cls = type(production.workorder_ids)
        original = cls._cal_cost
        def unknown_provider(rows):
            return original(rows)
        # Inject an unapproved extension, never a mocked Stock or price result.
        with patch.object(cls, '_cal_cost', unknown_provider):
            self._denied(production, lambda: self._release(production))

    def test_negative_delta_rejected_even_if_checkpoint_is_corrupt(self):
        production = self._timed()
        release = self._release(production)
        checkpoint = release.cost_ids
        self.env.flush_all()
        # Fault injection below immutable ORM API: test the arithmetic guard.
        self.env.cr.execute('UPDATE quality_native_partial_cost SET cumulative_cost=1000 WHERE id=%s', [checkpoint.id])
        checkpoint.invalidate_recordset()
        self._denied(production, lambda: self._release(production, ('5',), 'negative'))

    def test_exception_after_real_stock_rolls_back_all(self):
        production = self._timed()
        def fail(record, operation):
            self.assertEqual(record.qty_produced, 4)
            self.assertEqual(self._stock(), (12, 4))
            raise UserError('Injected after real Stock')
        with patch.object(type(production), '_native_partial_after_stock', fail):
            self._denied(production, lambda: self._release(production))

    def test_exception_after_checkpoint_rolls_back_all(self):
        production = self._timed()
        def fail(record, release, operation):
            self.assertEqual(release.cost_ids.cumulative_cost, 60)
            self.assertEqual(len(release.unit_ids), 4)
            self.assertEqual(self._stock(), (12, 4))
            raise UserError('Injected after checkpoint')
        with patch.object(type(production), '_native_partial_after_post', fail):
            self._denied(production, lambda: self._release(production))

    def test_old_finished_and_consumed_lines_moves_audit_are_immutable(self):
        production = self._timed()
        release = self._release(production)
        lines = release.unit_ids.move_line_id | release.unit_ids.move_line_id.consume_line_ids
        for line in lines:
            self._denied(production, lambda: line.write({'quantity': 7}))
            self._denied(production, lambda: line.write({'consume_line_ids': [(5, 0, 0)]}))
            self._denied(production, line.unlink)
        self._denied(production, lambda: release.unit_ids.move_line_id.move_id.write({'price_unit': 1}))
        self._denied(production, lambda: release.cost_ids.write({'cumulative_cost': 0}))
        self._denied(production, release.unlink)
        self._denied(production, lambda: production.with_context(quality_partial_release=True)._post_inventory())
        self._denied(production, production._split_productions)

    def test_real_lot_two_postings(self):
        self.product.tracking = 'lot'
        production = self._timed()
        lot = self.env['stock.lot'].create({'name': 'Disposable partial lot', 'product_id': self.product.id,
                                           'company_id': self.env.company.id})
        production.lot_producing_id = lot
        first = self._release(production)
        self._new_work(production)
        second = self._release(production, ('5',), 'second')
        self.assertEqual((first | second).unit_ids.move_line_id.lot_id, lot)
        self.assertAlmostEqual(sum((first | second).unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 175)
        self.assertEqual(self._stock(), (10, 5))

    def test_unsupported_serial_manual_and_byproduct_are_rejected(self):
        self.product.tracking = 'serial'
        production = self._timed()
        self._denied(production, lambda: self._release(production))
        self.product.tracking = 'none'
        production.move_raw_ids.manual_consumption = True
        self._denied(production, lambda: self._release(production))
        production.move_raw_ids.manual_consumption = False
        other = self.env['product.product'].create({'name': 'Unsupported partial byproduct', 'type': 'product'})
        self.env['stock.move'].create({
            'name': 'Unsupported partial byproduct', 'product_id': other.id, 'product_uom': other.uom_id.id,
            'product_uom_qty': 1, 'production_id': production.id,
            'location_id': self.product.property_stock_production.id, 'location_dest_id': self.location.id,
            'company_id': self.env.company.id,
        })
        self._denied(production, lambda: self._release(production))

    def test_unverified_finished_and_component_cost_configurations_are_rejected(self):
        production = self._timed()
        self.product.categ_id.property_cost_method = 'average'
        self._denied(production, lambda: self._release(production))
        self.product.categ_id.property_cost_method = 'fifo'
        category = self.env['product.category'].create({'name': 'Unsupported raw standard cost',
            'property_cost_method': 'standard', 'property_valuation': 'manual_periodic'})
        self.component.categ_id = category
        self._denied(production, lambda: self._release(production))
        category.property_cost_method = 'fifo'
        category.property_valuation = 'real_time'
        self._denied(production, lambda: self._release(production))

    def test_picked_actual_consumption_is_not_silently_reduced(self):
        production = self._timed()
        production.move_raw_ids.write({'quantity': 10, 'picked': True})
        self._denied(production, lambda: self._release(production))

    def test_stock_worker_needs_no_partial_acl_on_an_ordinary_mo(self):
        production = self._timed()
        worker = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Disposable stock worker', 'login': 'qunit_partial_stock_worker',
            'company_id': self.env.company.id, 'company_ids': [(6, 0, self.env.company.ids)],
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('stock.group_stock_user').id])],
        })
        visible = production.with_user(worker)
        visible.check_access_rights('read')
        self.assertFalse(visible._native_partial_history())
        # Same worker may not operate on partial history he cannot read.
        self._release(production)
        with self.assertRaises(AccessError):
            visible._native_partial_history()

    def test_native_finalization_metadata_and_failure_rollback(self):
        production = self._timed()
        self._release(production)
        self._new_work(production)
        original = type(production)._native_partial_finalize

        def fail_after_native_close(record, operation):
            original(record, operation)
            self.assertEqual(record.state, 'done')
            self.assertTrue(record.date_finished)
            self.assertTrue(record.is_locked)
            self.assertEqual(record.workorder_ids.state, 'done')
            raise UserError('Injected after real native MO closure')

        with patch.object(type(production), '_native_partial_finalize', fail_after_native_close):
            self._denied(production, lambda: self._release(production, ('5',), 'final-failure'))
        self._release(production, ('5',), 'final-success')
        self.assertEqual(production.state, 'done')
        self.assertTrue(production.date_finished)
        self.assertTrue(production.is_locked)
        self.assertEqual(production.workorder_ids.state, 'done')

    def test_parallel_transactions_post_same_units_only_once(self):
        if (not self.env.cr.dbname.startswith('qunit_partial_')
                or os.environ.get('QUNIT_DISPOSABLE_TEST_DATABASE') != self.env.cr.dbname):
            self.skipTest('Committed concurrency fixture requires the runner-owned disposable database.')
        database = self.env.cr.dbname
        # Independent committed fixture, never commit the TransactionCase's
        # cursor. These rows live only until the runner drops its OWN database.
        with sql_db.db_connect(database).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
            category = env['product.category'].create({'name': 'Disposable concurrent FIFO',
                'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
            component = env['product.product'].create({'name': 'Concurrent component', 'type': 'product',
                'categ_id': category.id, 'standard_price': 10})
            product = env['product.product'].create({'name': 'Concurrent finished', 'type': 'product', 'categ_id': category.id})
            center = env['mrp.workcenter'].create({'name': 'Concurrent native work', 'costs_hour': 60})
            bom = env['mrp.bom'].create({'product_tmpl_id': product.product_tmpl_id.id, 'product_qty': 1,
                'product_uom_id': product.uom_id.id,
                'bom_line_ids': [(0, 0, {'product_id': component.id, 'product_qty': 2})],
                'operation_ids': [(0, 0, {'name': 'Concurrent WO', 'workcenter_id': center.id})]})
            receipt = env['stock.move'].create({'name': 'Concurrent receipt', 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 20, 'price_unit': 10,
                'location_id': env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': warehouse.lot_stock_id.id, 'company_id': env.company.id})
            receipt._action_confirm(merge=False)
            receipt.write({'quantity': 20, 'picked': True})
            receipt._action_done()
            production = env['mrp.production'].create({'product_id': product.id, 'product_qty': 5,
                'product_uom_id': product.uom_id.id, 'bom_id': bom.id,
                'picking_type_id': warehouse.manu_type_id.id,
                'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id})
            production.action_confirm()
            production.action_assign()
            end = fields.Datetime.now().replace(microsecond=0)
            env['mrp.workcenter.productivity'].create(
                production.workorder_ids._prepare_timeline_vals(0, end - timedelta(hours=1), end))
            production_id, product_id = production.id, product.id
            cr.commit()
        posted = threading.Event()
        release_commit = threading.Event()
        second_ready = threading.Event()
        outcome, backend = {}, {}

        def first_worker():
            try:
                with sql_db.db_connect(database).cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    result = env['mrp.production'].browse(production_id)._native_partial_release(['1', '2', '3', '4'], 'race-a')
                    outcome['first'] = result.id
                    posted.set()
                    if not release_commit.wait(20):
                        raise AssertionError('No release of concurrency fixture lock')
                    cr.commit()
            except Exception as error:
                outcome['first_error'] = repr(error)
                posted.set()

        def second_worker():
            try:
                with sql_db.db_connect(database).cursor() as cr:
                    cr.execute('SELECT pg_backend_pid()')
                    backend['second'] = cr.fetchone()[0]
                    second_ready.set()
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    try:
                        env['mrp.production'].browse(production_id)._native_partial_release(['1', '2', '3', '4'], 'race-b')
                    except psycopg2.errors.SerializationFailure:
                        outcome['serialized'] = True
                        cr.rollback()
                    else:
                        raise AssertionError('Stale transaction unexpectedly passed revision UPDATE')
                with sql_db.db_connect(database).cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    try:
                        env['mrp.production'].browse(production_id)._native_partial_release(['1', '2', '3', '4'], 'race-b')
                    except UserError:
                        outcome['duplicate_denied'] = True
                    else:
                        raise AssertionError('Duplicate units accepted after serialization retry')
            except Exception as error:
                outcome['second_error'] = repr(error)
                second_ready.set()

        first = threading.Thread(target=first_worker, daemon=True)
        second = threading.Thread(target=second_worker, daemon=True)
        first.start()
        try:
            self.assertTrue(posted.wait(20), outcome)
            self.assertNotIn('first_error', outcome)
            second.start()
            self.assertTrue(second_ready.wait(10), outcome)
            blocked = False
            deadline = time.monotonic() + 10
            with sql_db.db_connect(database).cursor() as cr:
                while time.monotonic() < deadline:
                    cr.execute('SELECT cardinality(pg_blocking_pids(%s))', [backend['second']])
                    if cr.fetchone()[0]:
                        blocked = True
                        break
                    time.sleep(0.05)
            self.assertTrue(blocked, 'Second real PostgreSQL transaction did not wait on the MO lock')
        finally:
            release_commit.set()
            first.join(20)
            if second.ident:
                second.join(20)
        self.assertFalse(first.is_alive() or second.is_alive(), outcome)
        self.assertNotIn('first_error', outcome)
        self.assertNotIn('second_error', outcome)
        self.assertTrue(outcome.get('serialized'), outcome)
        self.assertTrue(outcome.get('duplicate_denied'), outcome)
        with sql_db.db_connect(database).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            production = env['mrp.production'].browse(production_id)
            releases = production._native_partial_history()
            self.assertEqual((len(releases), len(releases.unit_ids), len(releases.cost_ids)), (1, 4, 1))
            self.assertEqual(production.qty_produced, 4)
            self.assertAlmostEqual(sum(releases.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 140)
            self.assertEqual(sum(env['stock.quant'].search([('product_id', '=', product_id),
                ('location_id', '=', production.location_dest_id.id)]).mapped('quantity')), 4)
        _logger.info('PARTIAL_CONCURRENCY_PASS: two real transactions, lock wait, serialization/retry, one posting')
