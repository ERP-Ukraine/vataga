"""Real Community Stock/MRP/SVL terminal loss allocation. Quality tested separately."""
from unittest.mock import patch
import logging

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_native_partial_adapter as adapter

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install', 'native_production_loss')
class TestNativeProductionLoss(StockReleaseNativeCase):
    _timed = adapter.TestNativePartialAdapter._timed
    _denied = adapter.TestNativePartialAdapter._denied

    def _snapshot(self, production):
        result = adapter.TestNativePartialAdapter._snapshot(self, production)
        result['loss'] = self.env['quality.native.production.loss'].search([]).read(
            ['release_id', 'unit_key', 'value', 'accounting_status'])
        result['accounting'] = self.env['account.move'].search([]).ids
        result['scrap'] = self.env['stock.scrap'].search([]).ids
        return result

    def _close(self, production, good=('1', '2', '3', '4'), loss=('5',), request='loss-final'):
        return production._native_loss_close(list(good), list(loss), request)

    def _fixture190(self, tracking='none'):
        self.workcenter.costs_hour = 90
        self.product.tracking = tracking
        production = self._timed()
        production.qty_producing = 5
        production._set_qty_producing()
        production.move_raw_ids.picked = True
        if tracking == 'lot':
            production.lot_producing_id = self.env['stock.lot'].create({
                'name': 'Terminal loss lot', 'product_id': self.product.id, 'company_id': self.env.company.id})
        return production

    def _assert190(self, tracking='none'):
        production = self._fixture190(tracking)
        mos = self.env['mrp.production'].search([]).ids
        ledger = self.env['account.move'].search([]).ids
        scraps = self.env['stock.scrap'].search([]).ids
        release = self._close(production)
        self.assertEqual((production.product_qty, production.qty_produced, production.state), (5, 4, 'done'))
        self.assertEqual(self._stock(), (10, 4))
        self.assertEqual(release.allocation['total_value'], 190)
        self.assertEqual(release.allocation['good_value'], 152)
        self.assertEqual(release.allocation['loss_value'], 38)
        self.assertEqual((release.loss_ids.unit_key, release.loss_ids.quantity, release.loss_ids.value), ('5', 1, 38))
        self.assertEqual(release.loss_ids.accounting_status, 'manual_periodic_pending')
        self.assertEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 152)
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('value')), -100)
        self.assertEqual(release.cost_ids.cumulative_cost, 90)
        self.assertEqual(sum(production.move_finished_ids.move_line_ids.mapped('quantity')), 4)
        self.assertEqual(self.env['mrp.production'].search([]).ids, mos)
        self.assertEqual(self.env['account.move'].search([]).ids, ledger)
        self.assertEqual(self.env['stock.scrap'].search([]).ids, scraps)
        self.assertEqual(len(release.unit_ids), 4)
        if tracking == 'lot':
            self.assertEqual(release.unit_ids.move_line_id.lot_id, production.lot_producing_id)
        _logger.info('PRODUCTION_LOSS_PASS tracking=%s: plan=5, good=4/value=152, lost=1/value=38; '
                     'components=100, workcenter=90, total=190; MO=done; no Backorder/Scrap/GL entry', tracking)
        return production, release

    def test_none_actual_190_becomes_152_output_and_38_loss_without_backorder(self):
        self._assert190()

    def test_lot_actual_190_becomes_152_output_and_38_loss_without_backorder(self):
        self._assert190('lot')

    def test_retry_is_idempotent_and_another_request_or_duplicate_loss_is_denied(self):
        production, release = self._assert190()
        before = self._snapshot(production)
        self.assertEqual(self._close(production), release)
        production.button_mark_done()
        self.assertEqual(self._snapshot(production), before)
        self._denied(production, lambda: self._close(production, request='again'))
        self._denied(production, lambda: self._close(production, good=('1', '2', '3', '5'), loss=('4',)))
        self._denied(production, lambda: production._native_partial_release(['5'], 'ordinary-retry'))

    def test_failure_after_native_stock_and_mo_closure_rolls_back_all(self):
        production = self._fixture190()
        reached = []

        def fail(record, operation):
            reached.append(True)
            self.assertEqual((record.state, record.qty_produced), ('done', 4))
            self.assertEqual(sum(record.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 152)
            raise UserError('Injected after actual terminal Stock')

        with patch.object(type(production), '_native_partial_after_stock', fail):
            self._denied(production, lambda: self._close(production))
        self.assertTrue(reached)

    def test_failure_after_loss_checkpoint_rolls_back_all(self):
        production = self._fixture190()
        reached = []

        def fail(record, release, operation):
            reached.append(True)
            self.assertEqual(release.loss_ids.value, 38)
            self.assertEqual(release.cost_ids.cumulative_cost, 90)
            raise UserError('Injected after actual loss checkpoint')

        with patch.object(type(production), '_native_partial_after_post', fail):
            self._denied(production, lambda: self._close(production))
        self.assertTrue(reached)

    def test_already_capitalized_intervals_cannot_be_redistributed(self):
        production = self._fixture190()
        production.move_raw_ids.write({'quantity': 8, 'picked': True})
        production._native_partial_release(['1', '2', '3', '4'], 'old-incremental')
        self._denied(production, lambda: self._close(production))

    def test_exact_partition_and_full_picked_consumption_are_required(self):
        production = self._fixture190()
        self._denied(production, lambda: self._close(production, loss=('4',)))
        self._denied(production, lambda: self._close(production, loss=('5', '6')))
        production.move_raw_ids.write({'quantity': 8, 'picked': True})
        self._denied(production, lambda: self._close(production))

    def test_all_five_good_have_no_loss(self):
        production = self._fixture190()
        release = production._native_partial_release(['1', '2', '3', '4', '5'], 'all-good')
        self.assertFalse(release.loss_ids)
        self.assertEqual(self._stock(), (10, 5))
        self.assertEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 190)

    def test_loss_evidence_and_capitalized_timers_are_immutable(self):
        production, release = self._assert190()
        for record in (release, release.loss_ids, release.cost_ids):
            self._denied(production, lambda: record.write({'id': record.id}))
            self._denied(production, record.unlink)
        self._denied(production, lambda: production.workorder_ids.time_ids.write({'duration': 30}))
        timer = production.workorder_ids.time_ids
        self._denied(production, lambda: timer._compute_field_value(timer._fields['duration']))
        self._denied(production, lambda: production.write({'product_qty': 4}))

    def test_currency_rounding_and_per_unit_extra_cost_balance_to_total(self):
        production = self._fixture190()
        production.extra_cost = 0.01
        release = self._close(production)
        self.assertEqual(release.allocation['total_value'], 190.05)
        self.assertEqual(release.allocation['good_value'], 152.04)
        self.assertEqual(release.loss_ids.value, 38.01)
        self.assertAlmostEqual(release.allocation['good_value'] + release.loss_ids.value, 190.05)

    def test_existing_finished_stock_is_never_used_to_dispose_the_defective_unit(self):
        production = self._fixture190()
        receipt = self.env['stock.move'].create({
            'name': 'Unrelated genuine earlier receipt', 'product_id': self.product.id,
            'product_uom': self.product.uom_id.id, 'product_uom_qty': 7, 'price_unit': 12,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.location.id, 'company_id': self.env.company.id})
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 7, 'picked': True})
        receipt._action_done()
        before = receipt.stock_valuation_layer_ids.read(['value', 'quantity', 'remaining_qty', 'remaining_value'])
        self._close(production)
        self.assertEqual(self._stock(), (10, 11))
        self.assertEqual(receipt.stock_valuation_layer_ids.read(['value', 'quantity', 'remaining_qty', 'remaining_value']), before)

    def test_unconfirmed_or_negative_stock_consumption_is_not_estimated(self):
        production = self._fixture190()
        production.move_raw_ids.picked = False
        self._denied(production, lambda: self._close(production))
        production.move_raw_ids.picked = True
        production.move_raw_ids.quantity = 30
        self._denied(production, lambda: self._close(production))
        production.move_raw_ids.quantity = 10
        delivery = self.env['stock.move'].create({
            'name': 'Genuine component delivery leaving insufficient physical stock',
            'product_id': self.component.id, 'product_uom': self.component.uom_id.id,
            'product_uom_qty': 15, 'location_id': self.location.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'company_id': self.env.company.id})
        delivery._action_confirm(merge=False)
        delivery.write({'quantity': 15, 'picked': True})
        delivery._action_done()
        self.assertEqual(self._stock()[0], 5)
        before = self._snapshot(production)
        with self.assertRaisesRegex(UserError, 'Недостатній фактичний запас'):
            self._close(production)
        self.assertEqual(self._snapshot(production), before)
