"""Real stock and time keep progressing after the first named receipt."""
from datetime import timedelta
import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from psycopg2 import errors

from odoo import api, Command, sql_db
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_stock_posting_core as kernel_tests


@tagged('post_install', '-at_install')
class TestStockPostingIncremental(StockReleaseNativeCase):
    _keys = kernel_tests.TestStockPostingKernel._keys
    _post = kernel_tests.TestStockPostingKernel._post

    def setUp(self):
        super().setUp()
        if 'quality.production.unit' in self.env:
            self.skipTest('Native-only probe; real QUNIT bridge has its own integration tests.')
        self.env.user.groups_id = [Command.link(self.env.ref(name).id) for name in ('mrp.group_mrp_user', 'stock.group_stock_user')]

    def _scenario(self, two_operations=False):
        if two_operations:
            self.bom.operation_ids = [Command.create({'name': 'Second actual operation', 'workcenter_id': self.workcenter.id, 'sequence': 20})]
            production = self._production(timed=False)
            source, last = production.workorder_ids.sorted('id')
            shared = self.env['mrp.workcenter.productivity'].create({
                'workorder_id': source.id, 'workcenter_id': self.workcenter.id,
                'loss_id': self.env.ref('mrp.block_reason7').id,
                'date_start': '2026-01-12 08:00:00', 'date_end': '2026-01-12 09:00:00'})
        else:
            production = self._production(timed=True)
            shared, last = production.workorder_ids.time_ids, production.workorder_ids
        personal = shared.copy({'workorder_id': last.id, 'date_start': shared.date_end, 'date_end': shared.date_end + timedelta(minutes=10)})
        pending = personal.copy({'date_start': personal.date_end, 'date_end': False})
        keys = self._keys(production)
        members = {shared.id: keys, personal.id: keys[:1], pending.id: keys[1:2]}
        return production, shared, personal, pending, members

    def _freeze(self, production, members, index):
        return self.env['quality.stock.cost.scope']._freeze_incremental(
            production, self._keys(production), members, [self._keys(production)[index]], 'incremental-%s-%s' % (production.id, index))

    def test_five_sequential_receipts_while_remaining_units_continue_real_time(self):
        production, shared, personal, pending, members = self._scenario(two_operations=True)
        self.assertEqual(len(production.workorder_ids), 2)
        first = self._post(self._freeze(production, members, 0), 0)
        first_links = first.finished_line_id.consume_line_ids.ids
        first_value = first.finished_move_id.stock_valuation_layer_ids.value
        self.assertEqual(self._stock(), (18, 1))
        self.assertNotEqual(production.state, 'done')
        for index in range(1, 5):
            self.assertFalse(pending.date_end)
            pending.date_end = pending.date_start + timedelta(minutes=10)
            if index < 4:
                pending = pending.copy({'date_start': pending.date_end, 'date_end': False})
                members[pending.id] = self._keys(production)[index + 1:index + 2]
            receipt = self._post(self._freeze(production, members, index), index)
            self.assertAlmostEqual(receipt.labour_cost, 22)
            self.assertEqual(self._stock(), (18 - 2 * index, index + 1))
            self.assertEqual(first.finished_line_id.consume_line_ids.ids, first_links)
            self.assertEqual(first.finished_move_id.stock_valuation_layer_ids.value, first_value)
        receipts = self.env['quality.stock.posting']._receipts(production)
        self.assertEqual(production.state, 'done')
        self.assertEqual(set(production.workorder_ids.mapped('state')), {'done'})
        self.assertAlmostEqual(sum(receipts.mapped('labour_cost')), 110)
        self.assertAlmostEqual(sum(receipts.mapped('component_cost')), 100)
        self.assertAlmostEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 210)
        self.assertEqual(len(receipts.allocation_ids), 10)

    def test_open_selected_interval_blocks_only_that_unit(self):
        production, shared, personal, pending, members = self._scenario()
        with self.assertRaises(ValidationError):
            self._freeze(production, members, 1)
        self._post(self._freeze(production, members, 0), 0)
        self.assertEqual(self._stock(), (18, 1))
        self.assertFalse(pending.date_end)

    def test_other_unit_time_can_change_between_preparation_and_first_post(self):
        production, shared, personal, pending, members = self._scenario()
        scope = self._freeze(production, members, 0)
        pending.date_start += timedelta(minutes=1)
        self._post(scope, 0)
        self.assertEqual(self._stock(), (18, 1))
        with self.assertRaises(UserError):
            personal.date_end += timedelta(minutes=1)

    def test_new_unaccounted_time_for_issued_unit_requires_explicit_correction(self):
        production, shared, personal, pending, members = self._scenario()
        self._post(self._freeze(production, members, 0), 0)
        pending.date_end = pending.date_start + timedelta(minutes=10)
        extra = shared.copy({'date_start': pending.date_end, 'date_end': pending.date_end + timedelta(minutes=1)})
        members[extra.id] = self._keys(production)[:1]
        before = self._snapshot(production)
        with self.assertRaises(ValidationError):
            self._freeze(production, members, 1)
        self.assertEqual(before, self._snapshot(production))

    def test_unknown_interval_after_preparation_is_stale_not_free_labour(self):
        production, shared, personal, pending, members = self._scenario()
        scope = self._freeze(production, members, 0)
        shared.copy({'date_start': pending.date_start + timedelta(hours=1), 'date_end': pending.date_start + timedelta(hours=1, minutes=1)})
        with self.assertRaises(ValidationError):
            self._post(scope, 0)
        self.assertEqual(self._stock(), (20, 0))

    def test_repeated_scope_and_request_do_not_duplicate_cost(self):
        production, shared, personal, pending, members = self._scenario()
        scope = self._freeze(production, members, 0)
        self.assertEqual(scope, self._freeze(production, members, 0))
        first = self._post(scope, 0)
        self.assertEqual(first, self._post(scope, 0))
        self.assertEqual(len(first.allocation_ids), 2)
        with self.assertRaises(ValidationError):
            self._freeze(production, members, 0)

    def test_future_rate_changes_do_not_reprice_closed_historical_intervals(self):
        production, shared, personal, pending, members = self._scenario()
        first = self._post(self._freeze(production, members, 0), 0)
        self.workcenter.costs_hour = 30
        pending.date_end = pending.date_start + timedelta(minutes=10)
        second = self._post(self._freeze(production, members, 1), 1)
        self.assertAlmostEqual(first.labour_cost, 22)
        self.assertAlmostEqual(second.labour_cost, 17)  # old shared 12 + new personal 5

    def test_concurrent_same_request_serializes_and_retries_without_duplicate_stock(self):
        if not self.cr.dbname.startswith('qunit_kernel_'):
            self.skipTest('Committed concurrent fixtures only belong in the opt-in disposable database.')
        connection = sql_db.db_connect(self.cr.dbname)
        with connection.cursor() as cr:
            env = api.Environment(cr, 1, {})
            warehouse = env.ref('stock.warehouse0')
            category = env['product.category'].create({'name': 'Race FIFO', 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
            component, product = env['product.product'].create([
                {'name': 'Race component', 'type': 'product', 'categ_id': category.id, 'standard_price': 10},
                {'name': 'Race finished', 'type': 'product', 'categ_id': category.id},
            ])
            incoming = env['stock.move'].create({'name': 'Race actual receipt', 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 20, 'price_unit': 10,
                'location_id': env.ref('stock.stock_location_suppliers').id, 'location_dest_id': warehouse.lot_stock_id.id})
            incoming._action_confirm(merge=False)
            incoming.write({'quantity': 20, 'picked': True})
            incoming._action_done()
            center = env['mrp.workcenter'].create({'name': 'Race operation', 'costs_hour': 60})
            bom = env['mrp.bom'].create({'product_tmpl_id': product.product_tmpl_id.id,
                'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': 2})],
                'operation_ids': [Command.create({'name': 'Race operation', 'workcenter_id': center.id})]})
            production = env['mrp.production'].create({'product_id': product.id, 'product_qty': 2,
                'product_uom_id': product.uom_id.id, 'bom_id': bom.id})
            production.action_confirm()
            production.action_assign()
            timer = env['mrp.workcenter.productivity'].create({'workorder_id': production.workorder_ids.id,
                'workcenter_id': center.id, 'loss_id': env.ref('mrp.block_reason7').id,
                'date_start': '2026-01-12 08:00:00', 'date_end': '2026-01-12 09:00:00'})
            keys = ['race:%s:1' % production.id, 'race:%s:2' % production.id]
            scope = env['quality.stock.cost.scope']._freeze_incremental(production, keys, {timer.id: keys}, keys[:1], 'race-scope')
            scope_id, production_id = scope.id, production.id
            cr.commit()
        snapshot_ready, posted, attempting = Event(), Event(), Event()

        def first_request():
            self.assertTrue(snapshot_ready.wait(10))
            with connection.cursor() as cr:
                env = api.Environment(cr, 1, {})
                receipt = env['quality.stock.posting']._post_one(env['quality.stock.cost.scope'].browse(scope_id), keys[0], 'race-permission', 'race-request')
                posted.set()
                self.assertTrue(attempting.wait(10))
                cr.commit()
                return receipt.id

        def second_request():
            with connection.cursor() as cr:
                cr.execute('SELECT quality_stock_revision FROM mrp_production WHERE id=%s', [production_id])
                snapshot_ready.set()
                self.assertTrue(posted.wait(10))
                attempting.set()
                env = api.Environment(cr, 1, {})
                try:
                    env['quality.stock.posting']._post_one(env['quality.stock.cost.scope'].browse(scope_id), keys[0], 'race-permission', 'race-request')
                except errors.SerializationFailure:
                    cr.rollback()
                else:
                    self.fail('An old REPEATABLE READ snapshot must not post after the winner')
            with connection.cursor() as cr:
                env = api.Environment(cr, 1, {})
                receipt = env['quality.stock.posting']._post_one(env['quality.stock.cost.scope'].browse(scope_id), keys[0], 'race-permission', 'race-request')
                cr.commit()
                return receipt.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(first_request), executor.submit(second_request)
            self.assertEqual(first.result(timeout=30), second.result(timeout=30))
        with connection.cursor() as cr:
            env = api.Environment(cr, 1, {})
            production = env['mrp.production'].browse(production_id)
            self.assertEqual(production.qty_produced, 1)
            self.assertEqual(len(env['quality.stock.posting']._receipts(production)), 1)
            self.assertEqual(production.move_finished_ids.filtered(lambda move: move.state == 'done').stock_valuation_layer_ids.value, 50)

    def test_upgrade_drops_only_old_scope_limit_and_preserves_legacy_snapshot(self):
        production = self._production(timed=True)
        scope = self.env['quality.stock.cost.scope']._freeze(
            production, self._keys(production), {timer.id: self._keys(production) for timer in production.workorder_ids.time_ids})
        before = scope.snapshot
        self.env.cr.execute('ALTER TABLE quality_stock_cost_scope ADD CONSTRAINT quality_stock_cost_scope_production_unique UNIQUE(production_id)')
        path = Path(__file__).resolve().parents[1] / 'migrations/17.0.2.41/pre-migration.py'
        spec = importlib.util.spec_from_file_location('incremental_scope_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, '17.0.2.40')
        self.env.cr.execute("SELECT 1 FROM pg_constraint WHERE conname='quality_stock_cost_scope_production_unique'")
        self.assertFalse(self.env.cr.fetchone())
        self.assertEqual(scope.snapshot, before)
        with self.assertRaises(ValidationError):
            self._freeze(production, {timer.id: self._keys(production) for timer in production.workorder_ids.time_ids}, 0)
