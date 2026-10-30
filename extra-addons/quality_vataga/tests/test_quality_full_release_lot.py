"""Lot-tracked full-release regression; disposable Enterprise DB only."""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_quality_full_release import QualityFullReleaseCase


@tagged('post_install', '-at_install', 'quality_stock_phase3')
class TestQualityFullReleaseLot(QualityFullReleaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.tracking = 'lot'  # New disposable fixture product, never a staging product.

    def _lot_prepared(self):
        production, check = self._prepared(finish=False)
        lot = self.env['stock.lot'].create({
            'name': 'REAL-QA-LOT-' + str(production.id), 'product_id': self.product.id,
            'company_id': production.company_id.id})
        production.lot_producing_id = lot
        last = production.workorder_ids.sorted('id')[-1]
        last.button_start()
        last.button_finish()
        return production, check, lot

    def _snapshot(self, production):
        return (super()._snapshot(production), production.lot_producing_id.id,
                production.move_finished_ids.move_line_ids.read(['lot_id', 'quantity']),
                self.env['stock.lot'].search([]).ids)

    def test_lot_full_two_and_repeat_preserve_real_lines_and_quantities(self):
        production, check, lot = self._lot_prepared()
        original_units = check.sample_ids.controlled_unit_id.read(['name', 'lot_id'])
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 2))
        facts = production.native_stock_fact_ids
        self.assertEqual(len(facts), 2)
        self.assertEqual(set(facts.unit_id.ids), set(check.sample_ids.controlled_unit_id.ids))
        self.assertEqual(facts.move_line_id.lot_id, lot)
        self.assertEqual(sum(facts.move_line_id.mapped('quantity')), 2)
        self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, production.location_dest_id, lot_id=lot), 2)
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -4)
        self.assertFalse(self.env['mrp.production'].search([('quality_native_parent_id', '=', production.id)]))
        self.assertEqual(check.sample_ids.controlled_unit_id.read(['name', 'lot_id']), original_units)
        before = self._snapshot(production)
        production.button_mark_done()
        self.assertEqual(before, self._snapshot(production))

    def test_missing_lot_and_foreign_product_or_company_preserve_stock(self):
        production, check, lot = self._lot_prepared()
        other = self.env['res.company'].create({'name': 'Unrelated lot company'})
        foreign_product = self.env['stock.lot'].create({
            'name': 'Wrong product lot', 'product_id': self.component.id, 'company_id': self.env.company.id})
        foreign_company = self.env['stock.lot'].create({
            'name': 'Wrong company lot', 'product_id': self.product.id, 'company_id': other.id})
        for candidate in (self.env['stock.lot'], foreign_product, foreign_company):
            def release():
                production.lot_producing_id = candidate
                production.button_mark_done()
            self._assert_denied_unchanged(production, release)
        self.assertEqual(self.product.tracking, 'lot')

    def test_lot_assignment_is_rolled_back_when_native_posting_fails(self):
        production, check, lot = self._lot_prepared()
        before = self._snapshot(production)
        with patch.object(type(self.env['stock.move']), '_action_done', side_effect=UserError('Native posting failed')):
            with self.assertRaises(UserError):
                production.button_mark_done()
        self.assertEqual(before, self._snapshot(production))
        self.assertEqual(production.lot_producing_id, lot)
