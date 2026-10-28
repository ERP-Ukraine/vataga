"""Public native MRP baseline on a disposable DB; no custom posting API."""

from unittest import SkipTest

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class StockReleaseNativeCase(TransactionCase):
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

    def _production(self, quantity=5):
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': quantity,
            'product_uom_id': self.product.uom_id.id, 'bom_id': self.bom.id,
            'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.location.id, 'location_dest_id': self.location.id,
        })
        production.action_confirm()
        production.action_assign()
        return production


    def _stock(self):
        # Physical on-hand, including reservations. _get_available_quantity
        # would subtract the eight components reserved for the remainder.
        return tuple(sum(self.env['stock.quant'].search([
            ('product_id', '=', product.id), ('location_id', '=', self.location.id),
            ('company_id', '=', self.env.company.id),
        ]).mapped('quantity')) for product in (self.component, self.product))





@tagged('post_install', '-at_install')
class TestNativePartialProduction(StockReleaseNativeCase):
    """Public native Community baseline, not Enterprise Quality integration."""

    def _prepare_native(self, quantity):
        production = self._production()
        production.picking_type_id.create_backorder = 'ask'
        production.qty_producing = quantity
        production.move_raw_ids.write({'quantity': quantity * 2, 'picked': True})
        # Public completion fills finished quantities itself. Pre-picking
        # them changes native qty_produced before inventory is posted.
        return production

    def test_public_full_quantity_completes_native_mo_without_backorder(self):
        production = self._prepare_native(5)
        ids = self.env['mrp.production'].search([]).ids
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 5))
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)

    def test_public_partial_quantity_requires_backorder_instead_of_same_open_mo(self):
        production = self._prepare_native(4)
        ids = self.env['mrp.production'].search([]).ids
        action = production.button_mark_done()
        self.assertEqual(action['res_model'], 'mrp.production.backorder')
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(self._stock(), (20, 0))
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)
        wizard = self.env['mrp.production.backorder'].with_context(action['context']).create({})
        wizard.action_backorder()
        remaining = self.env['mrp.production'].search([('id', 'not in', ids)])
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining.product_qty, 1)
        self.assertNotIn(remaining.state, ('done', 'cancel'))
        self.assertEqual((production.state, production.qty_produced), ('done', 4))
        self.assertEqual(self._stock(), (12, 4))

    def test_repeated_native_completion_does_not_duplicate_stock_or_documents(self):
        production = self._prepare_native(5)
        production.button_mark_done()
        ids = self.env['mrp.production'].search([]).ids
        moves = (production.move_raw_ids | production.move_finished_ids).ids
        production.button_mark_done()
        self.assertEqual(self.env['mrp.production'].search([]).ids, ids)
        self.assertEqual((production.move_raw_ids | production.move_finished_ids).ids, moves)
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(production.qty_produced, 5)
