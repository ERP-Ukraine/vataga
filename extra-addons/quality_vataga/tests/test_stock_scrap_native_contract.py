"""Native Community MRP/Stock counterexamples on a disposable DB only.

These prove native behavior, not a working Enterprise QUNIT Scrap integration.
No quality models, quants, valuation layers or accounting moves are mocked.
"""

from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase


@tagged('post_install', '-at_install')
class TestNativeScrapContract(StockReleaseNativeCase):
    def _scrap(self, production, product):
        return self.env['stock.scrap'].create({
            'product_id': product.id, 'product_uom_id': product.uom_id.id,
            'production_id': production.id, 'company_id': self.env.company.id,
            'location_id': self.location.id, 'scrap_qty': 1, 'should_replenish': False,
        })

    def test_no_finished_stock_returns_warning_without_scrap_or_svl(self):
        production = self._production()
        scrap = self._scrap(production, self.product)
        before = (self._stock(), production.state, production.qty_produced,
                  self.env['stock.valuation.layer'].search([]).ids)
        action = scrap.action_validate()
        self.assertEqual(action['res_model'], 'stock.warn.insufficient.qty.scrap')
        self.assertEqual(scrap.state, 'draft')
        self.assertFalse(scrap.move_ids)
        self.assertEqual(before, (self._stock(), production.state, production.qty_produced,
                                 self.env['stock.valuation.layer'].search([]).ids))

    def test_matching_unrelated_receipt_can_be_scrapped_but_is_not_mo_provenance(self):
        production = self._production()
        receipt = self.env['stock.move'].create({
            'name': 'Unrelated finished inventory receipt', 'product_id': self.product.id,
            'product_uom': self.product.uom_id.id, 'product_uom_qty': 1,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.location.id, 'company_id': self.env.company.id,
            'price_unit': 17,
        })
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 1, 'picked': True})
        receipt._action_done()
        self.assertEqual(self._stock(), (20, 1))
        self.assertFalse(production.move_finished_ids.filtered(lambda move: move.state == 'done'))
        self.assertFalse(receipt.production_id)
        historical = receipt.stock_valuation_layer_ids.read(['quantity', 'value', 'stock_move_id'])
        scrap = self._scrap(production, self.product)
        scrap.action_validate()
        self.assertEqual(scrap.state, 'done')
        self.assertEqual(self._stock(), (20, 0))
        self.assertEqual(scrap.move_ids.production_id, production)
        self.assertTrue(all(scrap.move_ids.mapped('scrapped')))
        self.assertEqual(sum(scrap.move_ids.stock_valuation_layer_ids.mapped('quantity')), -1)
        self.assertEqual(sum(scrap.move_ids.stock_valuation_layer_ids.mapped('value')), -17)
        self.assertEqual(production.qty_produced, 0)
        self.assertNotEqual(production.state, 'done')
        self.assertEqual(receipt.stock_valuation_layer_ids.read(['quantity', 'value', 'stock_move_id']), historical)

    def test_component_scrap_is_not_a_finished_qunit_and_does_not_create_replacement_mo(self):
        production = self._production()
        productions = self.env['mrp.production'].search([]).ids
        scrap = self._scrap(production, self.component)
        scrap.action_validate()
        self.assertEqual(scrap.state, 'done')
        self.assertEqual(self._stock(), (19, 0))
        self.assertEqual(scrap.move_ids.raw_material_production_id, production)
        self.assertFalse(scrap.move_ids.production_id)
        self.assertEqual(sum(scrap.move_ids.stock_valuation_layer_ids.mapped('value')), -10)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(self.env['mrp.production'].search([]).ids, productions)
