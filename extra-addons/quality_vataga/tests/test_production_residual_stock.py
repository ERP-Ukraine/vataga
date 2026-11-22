from odoo.exceptions import UserError
from odoo.tests import tagged

from . import test_production_release_stock as release_contract
from .test_incoming_stock import IncomingStockFixture
from ..models.production_release_stock import complete_partial_production_release
from ..models.production_residual_stock import complete_residual_scrap
from ..models.stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


@tagged('post_install', '-at_install', 'production_stage_c')
class TestProductionResidualStock(IncomingStockFixture):
    _pending = release_contract.TestProductionReleaseStock._pending

    def _scope(self, hold, quantity):
        return [{'id': hold.receipt_line_id.id, 'move_id': hold.receipt_move_id.id,
                 'company_id': hold.company_id.id, 'product_id': hold.product_id.id,
                 'lot_id': hold.lot_id.id, 'owner_id': False, 'package_id': False, 'quantity': quantity}]

    def test_c_scraps_only_two_and_keeps_released_three(self):
        _picking, hold = self._receipt(quantity=5)
        release = self._pending(hold)
        complete_partial_production_release(release, {hold.receipt_line_id.id: 3})
        scope = self._scope(hold, 2)
        scraps = complete_residual_scrap(self.env(context=dict(self.env.context, default_quantity=99)), scope, 'C native test')
        self.assertEqual((scraps.state, scraps.scrap_qty), ('done', 2))
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (0, 3))
        self.assertEqual((release.state, release.quantity, hold.receipt_move_id.quantity), ('done', 3, 5))
        self.assertEqual(scraps.move_ids.move_line_ids.location_id, hold.location_id)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            complete_residual_scrap(self.env, scope, 'C native repeat')
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (0, 3))

    def test_c_full_fail_scraps_exact_five_with_lot(self):
        self.product.tracking = 'lot'
        lot = self.env['stock.lot'].create({'name': 'C-NATIVE-LOT', 'product_id': self.product.id,
                                          'company_id': self.env.company.id})
        _picking, hold = self._receipt(quantity=5, lot=lot)
        scraps = complete_residual_scrap(self.env, self._scope(hold, 5), 'C native full FAIL')
        self.assertEqual((scraps.state, scraps.scrap_qty), ('done', 5))
        self.assertEqual(scraps.move_ids.move_line_ids.lot_id, lot)
        self.assertEqual((self._quantity(hold.location_id, lot=lot), self._quantity(hold.destination_id, lot=lot)), (0, 0))

    def test_c_excess_quantity_or_foreign_lot_cannot_consume_released_stock(self):
        _picking, hold = self._receipt(quantity=5)
        release = self._pending(hold)
        complete_partial_production_release(release, {hold.receipt_line_id.id: 3})
        for scope in (self._scope(hold, 5), [dict(self._scope(hold, 2)[0], lot_id=-1)]):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                complete_residual_scrap(self.env, scope, 'C native invalid')
            self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 3))

    def test_c_shared_quantity_is_rejected_without_rounding_or_clamping(self):
        _picking, hold = self._receipt(quantity=5)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            complete_residual_scrap(self.env, self._scope(hold, 2), 'C native short scope')
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (5, 0))

    def test_c_shortage_and_reservation_are_rejected_before_scrap(self):
        _picking, hold = self._receipt(quantity=5)
        release = self._pending(hold)
        complete_partial_production_release(release, {hold.receipt_line_id.id: 3})
        quant = self.env['stock.quant'].search([
            ('product_id', '=', self.product.id), ('location_id', '=', hold.location_id.id),
        ])
        for change in ({'quantity': 1}, {'quantity': 1.6}, {'reserved_quantity': 1}):
            with self.env.cr.savepoint():
                original = {key: quant[key] for key in change}
                quant.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).write(change)
                count = self.env['stock.scrap'].search_count([])
                with self.assertRaises(UserError):
                    complete_residual_scrap(self.env, self._scope(hold, 2), 'C unsafe quant')
                self.assertEqual(self.env['stock.scrap'].search_count([]), count)
                quant.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).write(original)
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 3))

    def test_c_owner_and_package_cannot_be_substituted(self):
        package = self.env['stock.quant.package'].create({'name': 'C isolated package'})
        for values in ({'owner': self.vendor}, {'package': package}):
            with self.env.cr.savepoint():
                _picking, hold = self._receipt(quantity=5, **values)
                count = self.env['stock.scrap'].search_count([])
                with self.assertRaises(UserError):
                    complete_residual_scrap(self.env, self._scope(hold, 5), 'C unsafe ownership')
                self.assertEqual(self.env['stock.scrap'].search_count([]), count)

    def test_c_multiple_lots_keep_separate_moves_and_unselected_lot(self):
        self.product.tracking = 'lot'
        lots = self.env['stock.lot'].create([
            {'name': name, 'product_id': self.product.id, 'company_id': self.env.company.id}
            for name in ('C-FIRST', 'C-SECOND', 'C-UNSELECTED')])
        holds = [self._receipt(quantity=quantity, lot=lot)[1] for quantity, lot in zip((2, 1, 4), lots)]
        scopes = self._scope(holds[0], 2) + self._scope(holds[1], 1)
        scraps = complete_residual_scrap(self.env, scopes, 'C two distinct lots')
        self.assertEqual([(scrap.lot_id.id, scrap.scrap_qty) for scrap in scraps],
                         [(lots[0].id, 2), (lots[1].id, 1)])
        self.assertEqual([self._quantity(hold.location_id, lot=lot) for hold, lot in zip(holds, lots)], [0, 0, 4])

    def test_c_serials_are_scrapped_by_confirmed_serial_only(self):
        self.product.tracking = 'serial'
        lots = self.env['stock.lot'].create([
            {'name': name, 'product_id': self.product.id, 'company_id': self.env.company.id}
            for name in ('C-SERIAL-FAIL', 'C-SERIAL-PASS')])
        _picking, failed = self._receipt(quantity=1, lot=lots[0])
        _picking, passed = self._receipt(quantity=1, lot=lots[1])
        release = self._pending(passed)
        complete_partial_production_release(release, {passed.receipt_line_id.id: 1})
        scrap = complete_residual_scrap(self.env, self._scope(failed, 1), 'C exact serial')
        self.assertEqual(scrap.move_ids.move_line_ids.lot_id, lots[0])
        self.assertEqual(self._quantity(passed.destination_id, lot=lots[1]), 1)
        self.assertEqual(self._quantity(failed.location_id, lot=lots[0]), 0)
