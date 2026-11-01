"""Opt-in real native MRP/analytic regressions with account_analytic_uom installed."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_native_partial_adapter as adapter


@tagged('post_install', '-at_install', '-standard', 'native_analytic_uom')
class TestNativeAnalyticUom(StockReleaseNativeCase):
    _timed = adapter.TestNativePartialAdapter._timed
    _new_work = adapter.TestNativePartialAdapter._new_work
    _release = adapter.TestNativePartialAdapter._release

    def setUp(self):
        super().setUp()
        self.assertIn('unit_quantity_applicable', self.env['account.analytic.line']._fields,
                      'Run this opt-in class with the actual account_analytic_uom addon installed')
        self.account = self.env['account.analytic.account'].create({
            'name': 'Own analytic quantity account', 'plan_id': self.env.ref('analytic.analytic_plan_projects').id})
        self.hour = self.env.ref('uom.product_uom_hour')
        self.unit = self.env.ref('uom.product_uom_unit')

    def _line(self, **values):
        return self.env['account.analytic.line'].create({
            'name': 'Own analytic quantity', 'account_id': self.account.id,
            'product_id': self.product.id, 'product_uom_id': self.unit.id,
            'unit_amount': 2, 'amount': -123, **values})

    def test_native_mrp_hours_preserve_cost_and_stock_in_pieces(self):
        self.workcenter.analytic_distribution = {str(self.account.id): 100}
        production = self._timed()
        production.analytic_distribution = {str(self.account.id): 100}
        self.env.flush_all()
        wo = production.workorder_ids
        lines = wo.mo_analytic_account_line_ids | wo.wc_analytic_account_line_ids
        line_ids = sorted(lines.ids)
        self.assertEqual(len(lines), 2)
        self.assertEqual(production.product_uom_id, self.unit)
        self.assertEqual(self.product.uom_id, self.unit)
        self.assertEqual(self.component.uom_id, self.unit)
        self.assertEqual(wo._cal_cost(), 60)
        for line in lines:
            self.assertEqual(line.product_id, self.product)
            self.assertEqual(line.product_uom_id, self.hour)
            self.assertEqual((line.amount, line.unit_amount), (-60, 1))
            self.assertFalse(line.unit_quantity_applicable)
            self.assertEqual(line.unit_quantity_amount, 0)
        first = self._release(production)
        self.assertEqual(sum(first.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 140)
        self._new_work(production)
        second = self._release(production, ('5',), 'analytic-uom-second')
        self.assertEqual(sum(second.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 35)
        self.env.flush_all()
        self.assertEqual(sorted((wo.mo_analytic_account_line_ids | wo.wc_analytic_account_line_ids).ids), line_ids)
        self.assertEqual(sorted(lines.mapped('amount')), [-75, -75])
        self.assertEqual(lines.mapped('unit_amount'), [1.25, 1.25])
        self.assertFalse(any(lines.mapped('unit_quantity_applicable')))
        with self.assertRaises(UserError):
            self.hour._compute_quantity(1, self.unit)

    def test_same_category_product_quantities_use_native_conversion(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        line = self._line(product_uom_id=dozen.id)
        self.env.flush_all()
        self.assertTrue(line.unit_quantity_applicable)
        self.assertEqual(line.unit_quantity_amount, 24)
        self.assertEqual((line.amount, line.unit_amount, line.product_uom_id), (-123, 2, dozen))
        line.unit_amount = 3
        self.env.flush_all()
        self.assertEqual(line.unit_quantity_amount, 36)

    def test_non_manufacturing_hour_to_piece_is_still_rejected_and_rolled_back(self):
        before = self.env['account.analytic.line'].search([]).ids
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._line(product_uom_id=self.hour.id, unit_amount=1)
            self.env.flush_all()
        self.assertEqual(self.env['account.analytic.line'].search([]).ids, before)

    def test_manufacturing_non_time_mismatch_is_still_rejected(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._line(product_uom_id=self.env.ref('uom.product_uom_kgm').id,
                       category='manufacturing_order', unit_amount=1)
            self.env.flush_all()

    def test_time_based_product_has_real_convertible_product_quantity(self):
        product = self.env['product.product'].create({
            'name': 'Own time product', 'type': 'service', 'uom_id': self.hour.id, 'uom_po_id': self.hour.id})
        minute = self.env.ref('uom.product_uom_minute')
        line = self._line(product_id=product.id, product_uom_id=minute.id,
                          category='manufacturing_order', unit_amount=90, amount=-90)
        self.env.flush_all()
        self.assertTrue(line.unit_quantity_applicable)
        self.assertEqual(line.unit_quantity_amount, 1.5)
        self.assertEqual((line.amount, line.unit_amount), (-90, 90))

    def test_category_recompute_preserves_time_and_enforces_other_quantity_validation(self):
        line = self._line(product_uom_id=self.hour.id, unit_amount=0)
        self.env.flush_all()
        line.write({'category': 'manufacturing_order', 'unit_amount': 1.25})
        self.env.flush_all()
        self.assertFalse(line.unit_quantity_applicable)
        self.assertEqual((line.unit_quantity_amount, line.unit_amount, line.amount), (0, 1.25, -123))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            line.category = 'other'
            self.env.flush_all()
        self.assertEqual(line.category, 'manufacturing_order')
        self.assertEqual(line.unit_amount, 1.25)
