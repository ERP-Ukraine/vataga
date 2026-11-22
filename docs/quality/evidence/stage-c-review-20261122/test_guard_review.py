"""Diagnostic reproductions against real Community Stock/MRP and unchanged C guard.

The case flag/link are declarations only. Automatic EE Alert is not exercised.
"""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.quality_incoming_stock_contract.tests.test_incoming_stock import IncomingStockFixture
from odoo.addons.quality_incoming_stock_contract.tests import test_production_release_stock as release_contract
from odoo.addons.quality_incoming_stock_contract.models.production_release_stock import complete_partial_production_release


@tagged('post_install', '-at_install', 'stage_c_guard_review')
class TestGuardReview(IncomingStockFixture):
    _pending = release_contract.TestProductionReleaseStock._pending

    def _scenario(self, controlled=True):
        _picking, hold = self._receipt(quantity=5)
        complete_partial_production_release(self._pending(hold), {hold.receipt_line_id.id: 3})
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 5, 'product_uom_id': self.product.uom_id.id,
            'picking_type_id': self.warehouse.manu_type_id.id, 'quality_production_case_enabled': controlled,
        })
        if controlled:
            self.env['quality.alert'].create({'name': 'C guard declaration', 'production_case_mo_id': production.id})
        workcenter = self.env['mrp.workcenter'].create({'name': 'Guard review workcenter'})
        workorder = self.env['mrp.workorder'].create({
            'name': 'Guard review operation', 'production_id': production.id,
            'workcenter_id': workcenter.id, 'product_uom_id': self.product.uom_id.id,
        })
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Disposable stock/MRP operator without board', 'login': 'c-guard-stock-operator',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set((self.env.ref('mrp.group_mrp_user') | self.env.ref('stock.group_stock_user')).ids)],
        })
        return hold, production, workorder, user

    def _values(self, hold):
        return {'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
                'scrap_qty': 2, 'location_id': hold.destination_id.id}

    def test_direct_c_mo_links_are_rejected(self):
        hold, production, _workorder, user = self._scenario()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['stock.scrap'].with_user(user).create(dict(self._values(hold), production_id=production.id))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.env['mrp.unbuild'].with_user(user).create({
                'mo_id': production.id, 'product_id': self.product.id,
                'product_uom_id': self.product.uom_id.id, 'product_qty': 2,
                'location_id': hold.destination_id.id})
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 3))

    def test_reproduce_workorder_only_c_scrap_consumes_released_stock(self):
        hold, production, workorder, user = self._scenario()
        scrap = self.env['stock.scrap'].with_user(user).create(dict(self._values(hold), workorder_id=workorder.id))
        self.assertFalse(scrap.production_id)
        self.assertFalse(scrap.quality_production_case_id)
        self.assertEqual(scrap.workorder_id.production_id, production)
        scrap.action_validate()
        self.assertEqual((scrap.state, scrap.scrap_qty), ('done', 2))
        # Reproduction success = safety defect confirmed, NOT acceptance PASS.
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 1))

    def test_non_c_standard_scrap_still_works(self):
        hold, production, workorder, user = self._scenario(controlled=False)
        scrap = self.env['stock.scrap'].with_user(user).create(dict(self._values(hold), production_id=production.id,
                                                                workorder_id=workorder.id))
        scrap.action_validate()
        self.assertEqual((scrap.state, scrap.scrap_qty), ('done', 2))
        self.assertEqual((self._quantity(hold.location_id), self._quantity(hold.destination_id)), (2, 1))

    def test_non_c_standard_manufacture_and_unbuild_still_work(self):
        product = self.env['product.product'].create({'name': 'Guard review ordinary finished item', 'type': 'product'})
        production = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 5, 'product_uom_id': product.uom_id.id,
            'picking_type_id': self.warehouse.manu_type_id.id,
        })
        production.action_confirm()
        production.qty_producing = 5
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        self.assertEqual(self._quantity(self.warehouse.lot_stock_id, product=product), 5)
        unbuild = self.env['mrp.unbuild'].create({
            'product_id': product.id, 'product_qty': 2, 'product_uom_id': product.uom_id.id,
            'mo_id': production.id, 'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
        })
        unbuild.action_validate()
        self.assertEqual(unbuild.state, 'done')
        self.assertEqual(self._quantity(self.warehouse.lot_stock_id, product=product), 3)
