"""Real Stock contracts for the executor; source fixture is disposable Stock.

These do not pretend to cover Enterprise MRP/QC integration.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_incoming_stock import IncomingStockFixture
from ..models.production_release_stock import complete_partial_production_release
from ..models.stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


@tagged('post_install', '-at_install', 'production_stage_b')
class TestProductionReleaseStock(IncomingStockFixture):
    def _pending(self, hold):
        return self.env['stock.move'].with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).create({
            'name': 'B disposable release', 'product_id': hold.product_id.id,
            'product_uom': hold.uom_id.id, 'product_uom_qty': hold.received_qty,
            'company_id': hold.company_id.id, 'picking_type_id': self.warehouse.int_type_id.id,
            'location_id': hold.location_id.id, 'location_dest_id': hold.destination_id.id,
            'quality_held_move_id': hold.receipt_move_id.id,
        })

    def test_three_of_five_moves_exactly_three_and_keeps_original_receipt(self):
        _picking, hold = self._receipt(quantity=5)
        release = self._pending(hold)
        pickings = complete_partial_production_release(release.with_context(default_quantity=42),
                                                       {hold.receipt_line_id.id: 3})
        self.assertEqual((release.state, release.quantity, release.product_uom_qty), ('done', 3, 3))
        self.assertEqual((hold.receipt_move_id.quantity, hold.receipt_line_id.quantity), (5, 5))
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 3))
        self.assertEqual(pickings.state, 'done')
        self.assertTrue(pickings.date_done)
        self.assertEqual(hold.receipt_move_id.quality_release_move_ids, release)
        with self.assertRaises(UserError):
            complete_partial_production_release(release, {hold.receipt_line_id.id: 3})
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 3))

    def test_excess_or_foreign_source_cannot_move_stock(self):
        _picking, hold = self._receipt(quantity=5)
        release = self._pending(hold)
        for allocation in ({hold.receipt_line_id.id: 6}, {-1: 3}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                complete_partial_production_release(release, allocation)
            self.assertEqual(release.state, 'draft')
            self.assertFalse(release.picking_id)
            self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (5, 0))

    def test_partial_release_preserves_exact_lot(self):
        self.product.tracking = 'lot'
        lot = self.env['stock.lot'].create({'name': 'B-ONLY-LOT', 'product_id': self.product.id,
                                          'company_id': self.env.company.id})
        _picking, hold = self._receipt(quantity=5, lot=lot)
        release = self._pending(hold)
        complete_partial_production_release(release, {hold.receipt_line_id.id: 3})
        self.assertEqual(release.move_line_ids.lot_id, lot)
        self.assertEqual((self._quantity(hold.location_id, lot=lot),
                          self._quantity(hold.destination_id, lot=lot)), (2, 3))
