"""Characterize the native posting contract, NOT a QUNIT posting adapter.

Run only in a disposable database. TransactionCase rolls back stock and SVLs.
These use ordinary MOs: the protected QUNIT boundary must remain closed until
cost allocation and historical genealogy have their own validated adapter.

Required future journal (in the same transaction as native stock valuation):
* one immutable receipt per QUNIT, UNIQUE(unit_id), UNIQUE(permission_id),
  referencing MO/company, 13.4A permission, plan, actor/date and request key;
* receipt lines reference actual DONE stock.move / stock.move.line, quantity,
  UoM, lot, source/destination and valuation layers, never proposed move IDs;
* raw-line quantity allocations cannot exceed actual consumption; finished
  allocations reconcile bijectively to named QUNIT and MO.qty_produced;
* frozen interval / component cost allocations with conservation checks, no
  second charge for an already allocated interval or valuation-layer slice;
* MO -> QRUN -> source stock locks, revalidation, stock posting, journal and
  chatter are atomic. A failed/retried request cannot leave an orphan move.

Until that contract exists, removing qty_produced guards in 13.3 / 13.4A would
accept unidentified historical production. No such guard is relaxed here.
"""

from datetime import datetime, timedelta
from unittest import SkipTest

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestStockReleaseNativeContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if ('stock.valuation.layer' not in cls.env or not cls.env['ir.module.module'].search_count([
                ('name', '=', 'mrp_account'), ('state', '=', 'installed')])):
            raise SkipTest('Native valuation tests require mrp_account / stock_account.')
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.location = cls.warehouse.lot_stock_id
        category = cls.env['product.category'].create({
            'name': 'QUNIT native contract FIFO', 'property_cost_method': 'fifo',
            'property_valuation': 'manual_periodic',
        })
        cls.component = cls.env['product.product'].create({
            'name': 'QUNIT native contract component', 'type': 'product',
            'categ_id': category.id, 'standard_price': 10,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'QUNIT native contract finished', 'type': 'product', 'categ_id': category.id,
        })
        cls.workcenter = cls.env['mrp.workcenter'].create({'name': 'QUNIT native cost contract', 'costs_hour': 60})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'bom_line_ids': [Command.create({'product_id': cls.component.id, 'product_qty': 2})],
            'operation_ids': [Command.create({
                'name': 'Native timed operation', 'workcenter_id': cls.workcenter.id,
                'time_cycle': 12,
            })],
        })
        # Native inventory receipt gives actual FIFO layers, rather than
        # fabricated valuation rows or mocks of quantity/cost APIs.
        receipt = cls.env['stock.move'].create({
            'name': 'Disposable native contract receipt', 'product_id': cls.component.id,
            'product_uom': cls.component.uom_id.id, 'product_uom_qty': 20,
            'location_id': cls.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': cls.location.id, 'company_id': cls.env.company.id,
            'price_unit': 10,
        })
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 20, 'picked': True})
        receipt._action_done()

    def _production(self, timed=False, serial=False):
        if serial:
            self.product.tracking = 'serial'
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 5,
            'product_uom_id': self.product.uom_id.id, 'bom_id': self.bom.id,
            'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.location.id, 'location_dest_id': self.location.id,
        })
        production.action_confirm()
        production.action_assign()
        if timed:
            start = datetime(2026, 1, 12, 8)
            self.env['mrp.workcenter.productivity'].create({
                'workorder_id': production.workorder_ids.id, 'workcenter_id': self.workcenter.id,
                'loss_id': self.env.ref('mrp.block_reason7').id,
                'date_start': start, 'date_end': start + timedelta(hours=1),
            })
        return production

    def _post_one(self, production):
        # Characterization of the private native API on an ordinary test MO,
        # never skip_mo_check / super() through quality_vataga's boundary.
        production.qty_producing = production.qty_produced + 1
        raw = production.move_raw_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
        raw.write({'quantity': 2, 'picked': True})
        before = production.move_finished_ids.filtered(lambda move: move.state == 'done')
        production._post_inventory(cancel_backorder=False)
        return production.move_finished_ids.filtered(lambda move: move.state == 'done') - before

    def _stock(self):
        # Physical on-hand, including reservations. _get_available_quantity
        # would subtract the eight components reserved for the remainder.
        return tuple(sum(self.env['stock.quant'].search([
            ('product_id', '=', product.id), ('location_id', '=', self.location.id),
            ('company_id', '=', self.env.company.id),
        ]).mapped('quantity')) for product in (self.component, self.product))

    def _snapshot(self, production):
        moves = production.move_raw_ids | production.move_finished_ids
        return (self._stock(), production.state, production.qty_produced,
                production.workorder_ids.mapped('state'), moves.read(), moves.move_line_ids.read(),
                moves.stock_valuation_layer_ids.read(), self.env['stock.quant'].search([
                    ('product_id', 'in', (self.component | self.product).ids),
                    ('company_id', '=', self.env.company.id),
                ]).read())

    def test_one_of_five_moves_only_two_components_and_keeps_mo_open(self):
        production = self._production()
        self.assertEqual(self._stock(), (20, 0))
        finished = self._post_one(production)
        self.assertEqual(self._stock(), (18, 1))
        self.assertEqual(production.qty_produced, 1)
        self.assertNotIn(production.state, ('done', 'cancel'))
        self.assertNotIn('done', production.workorder_ids.mapped('state'))
        self.assertEqual(finished.quantity, 1)
        raw_remaining = production.move_raw_ids.filtered(lambda row: row.state not in ('done', 'cancel'))
        self.assertEqual(raw_remaining.product_uom_qty, 8)
        self.assertEqual(sum(raw_remaining.mapped('quantity')), 8)

    def test_five_successive_native_posts_conserve_components(self):
        production = self._production()
        for index in range(1, 6):
            self._post_one(production)
            self.assertEqual(self._stock(), (20 - 2 * index, index))
            self.assertEqual(production.qty_produced, index)
            remaining = production.move_raw_ids.filtered(lambda row: row.state not in ('done', 'cancel'))
            self.assertEqual(sum(remaining.mapped('product_uom_qty')), (5 - index) * 2)
        self.assertEqual(sum(production.move_raw_ids.filtered(lambda row: row.state == 'done').mapped('quantity')), 10)
        self.assertEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('quantity')), 5)

    def test_native_cost_recharges_full_work_interval_on_every_partial_post(self):
        production = self._production(timed=True)
        self.assertAlmostEqual(production.workorder_ids._cal_cost(), 60)
        first = self._post_one(production)
        second = self._post_one(production)
        first_value = sum(first.stock_valuation_layer_ids.mapped('value'))
        second_value = sum(second.stock_valuation_layer_ids.mapped('value'))
        self.assertAlmostEqual(first_value, 80)  # 2 * 10 + WHOLE hour * 60
        self.assertAlmostEqual(second_value, 80)  # same hour charged AGAIN
        self.assertAlmostEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('value')), -40)
        # Two completed units have absorbed 120 of labour against only 60 of
        # actual time. Never use native _cal_price as a per-QUNIT allocator.
        self.assertAlmostEqual(first_value + second_value - 40, 120)

    def test_second_native_post_replaces_first_finished_component_genealogy(self):
        production = self._production()
        first = self._post_one(production)
        first_components = first.move_line_ids.consume_line_ids
        self.assertTrue(first_components)
        second = self._post_one(production)
        second_components = second.move_line_ids.consume_line_ids
        self.assertFalse(first_components & second_components)
        # Native _post_inventory assigns the latest consume_line_ids to ALL
        # finished lines. A QUNIT journal must protect historical attribution.
        self.assertEqual(first.move_line_ids.consume_line_ids, second_components)
        self.assertNotEqual(first.move_line_ids.consume_line_ids, first_components)

    def test_missing_serial_rolls_back_raw_stock_and_valuation(self):
        production = self._production(serial=True)
        before = self._snapshot(production)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._post_one(production)
        self.env.invalidate_all()
        self.assertEqual(before, self._snapshot(production))

    def test_failure_after_native_post_rolls_back_the_whole_transaction(self):
        production = self._production(timed=True)
        before = self._snapshot(production)
        with self.assertRaisesRegex(UserError, 'Simulated journal failure'), self.env.cr.savepoint():
            self._post_one(production)
            self.assertEqual(self._stock(), (18, 1))
            raise UserError('Simulated journal failure')
        self.env.invalidate_all()
        self.assertEqual(before, self._snapshot(production))
