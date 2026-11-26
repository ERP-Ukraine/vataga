from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseRFQ(PurchaseApprovalCase):
    def test_rpc_cannot_forge_or_change_new_order_key(self):
        with self.assertRaises(AccessError):
            self.env['purchase.order'].create({'partner_id': self.vendor.id, 'vataga_rfq_origin_key': 'forged'})
        with self.assertRaises(AccessError):
            self.env['purchase.order'].with_context(default_vataga_rfq_origin_key='forged').create({'partner_id': self.vendor.id})
        order = self.env['purchase.order'].create({'partner_id': self.vendor.id})
        with self.assertRaises(AccessError):
            order.write({'vataga_rfq_origin_key': 'forged'})

    def test_missing_product_blocks_rfq(self):
        request = self.approve(self.request([self.line_values(missing=True)]))
        with self.assertRaises(UserError):
            request.with_user(self.buyer).action_create_purchase_orders()

    def test_supplier_must_match_quantity_dates_company_and_variant(self):
        request = self.approve(self.request()).with_user(self.buyer)
        seller = self.add_vendor(min_qty=100)
        self.assertFalse(request.vataga_can_create_rfq)
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()
        seller.min_qty = 0
        seller.date_end = fields.Date.subtract(fields.Date.today(), days=1)
        request.invalidate_recordset()
        self.assertFalse(request.vataga_can_create_rfq)
        seller.date_end = False
        variant = self.env['product.product'].create({'name': 'Different product', 'purchase_ok': True})
        seller.product_id = variant
        request.invalidate_recordset()
        self.assertFalse(request.vataga_can_create_rfq)

    def test_supplier_changes_refresh_nonstored_readiness(self):
        request = self.approve(self.request()).with_user(self.buyer)
        self.assertFalse(request.vataga_can_create_rfq)
        self.add_vendor()
        request.invalidate_recordset()
        self.assertTrue(request.vataga_can_create_rfq)

    def test_native_creation_and_repeat_do_not_increase_quantities(self):
        self.add_vendor()
        request = self.approve(self.request()).with_user(self.buyer)
        request.action_create_purchase_orders()
        po_line = request.product_line_ids.purchase_order_line_id
        self.assertTrue(po_line)
        quantity = po_line.product_qty
        self.assertTrue(request.vataga_rfq_created)
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()
        self.assertEqual(po_line.product_qty, quantity)

    def test_existing_draft_is_never_reused_for_another_request(self):
        self.add_vendor()
        first = self.approve(self.request()).with_user(self.buyer)
        first.action_create_purchase_orders()
        existing_orders = first.product_line_ids.purchase_order_line_id.order_id
        before = existing_orders.order_line.read(['product_id', 'product_qty', 'price_unit', 'taxes_id'])
        self.assertEqual(existing_orders.state, 'draft')
        second = self.approve(self.request()).with_user(self.buyer)
        domain = second.product_line_ids._get_purchase_orders_domain(self.vendor)
        self.assertTrue(existing_orders & self.env['purchase.order'].search(domain),
                        'The existing draft must really match the native ERP-UA grouping domain')
        second.action_create_purchase_orders()
        self.assertFalse(existing_orders & second.product_line_ids.purchase_order_line_id.order_id)
        self.assertEqual(existing_orders.order_line.read(['product_id', 'product_qty', 'price_unit', 'taxes_id']), before)
        orders = existing_orders | second.product_line_ids.purchase_order_line_id.order_id
        lines = orders.order_line.filtered(lambda l: l.product_id == self.product)
        self.assertEqual(sum(lines.mapped('product_qty')), 4)
        self.assertTrue(first.vataga_rfq_created and second.vataga_rfq_created)

    def test_same_vendor_groups_only_within_current_request(self):
        self.add_vendor()
        request = self.approve(self.request([self.line_values(), self.line_values(quantity=3)])).with_user(self.buyer)
        request.action_create_purchase_orders()
        orders = request.product_line_ids.purchase_order_line_id.order_id
        self.assertEqual(len(orders), 1)
        self.assertEqual(sum(orders.order_line.mapped('product_qty')), 5)

    def test_native_failure_on_second_order_rolls_back_first(self):
        other = self.env['product.product'].create({'name': 'Rollback second product', 'purchase_ok': True})
        vendor = self.env['res.partner'].create({'name': 'Rollback second vendor', 'supplier_rank': 1})
        self.add_vendor()
        self.add_vendor(product=other, partner=vendor)
        request = self.approve(self.request([self.line_values(), self.line_values(
            product_id=other.id, product_uom_id=other.uom_id.id)])).with_user(self.buyer)
        before = self.env['purchase.order'].search([]).ids
        model = type(self.env['purchase.order.line'])
        original = model.create
        calls = []
        def fail_second(records, values):
            calls.append(True)
            if len(calls) == 2:
                raise UserError('Injected second vendor failure')
            return original(records, values)
        with patch.object(model, 'create', fail_second):
            with self.assertRaises(UserError):
                request.action_create_purchase_orders()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.env['purchase.order'].search([]).ids, before)
        request.invalidate_recordset()
        self.assertFalse(request.vataga_rfq_created)
        self.assertFalse(request.product_line_ids.purchase_order_line_id)

    def test_multiple_vendors_keep_native_grouping(self):
        second_product = self.env['product.product'].create({'name': 'Second product', 'purchase_ok': True})
        second_vendor = self.env['res.partner'].create({'name': 'Second vendor', 'supplier_rank': 1})
        self.add_vendor()
        self.add_vendor(product=second_product, partner=second_vendor)
        request = self.approve(self.request([
            self.line_values(), self.line_values(product_id=second_product.id, product_uom_id=second_product.uom_id.id)
        ])).with_user(self.buyer)
        request.action_create_purchase_orders()
        self.assertEqual(len(request.product_line_ids.purchase_order_line_id.order_id), 2)

    def test_cancelled_or_deleted_rfq_does_not_enable_recreation(self):
        self.add_vendor()
        request = self.approve(self.request()).with_user(self.buyer)
        request.action_create_purchase_orders()
        orders = request.product_line_ids.purchase_order_line_id.order_id
        orders.button_cancel()
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()
        orders.unlink()
        self.assertFalse(request.product_line_ids.purchase_order_line_id)
        self.assertTrue(request.vataga_rfq_created)
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()

    def test_native_failure_rolls_back_links_and_marker(self):
        self.add_vendor()
        request = self.approve(self.request()).with_user(self.buyer)
        before = self.env['purchase.order'].search_count([])
        with patch.object(type(self.env['purchase.order.line']), 'create', side_effect=UserError('Injected failure')):
            with self.assertRaises(UserError):
                request.action_create_purchase_orders()
        request.invalidate_recordset()
        self.assertFalse(request.vataga_rfq_created)
        self.assertFalse(request.product_line_ids.purchase_order_line_id)
        self.assertEqual(self.env['purchase.order'].search_count([]), before)
        request.action_create_purchase_orders()
        self.assertTrue(request.vataga_rfq_created)

    def test_requester_without_purchase_rights_cannot_create_rfq(self):
        self.add_vendor()
        request = self.approve(self.request())
        with self.assertRaises((AccessError, UserError)):
            request.action_create_purchase_orders()

    def test_legacy_links_are_honored_and_survive_deletion(self):
        self.add_vendor()
        request = self.approve(self.request()).with_user(self.buyer)
        request.action_create_purchase_orders()
        # Simulate only the pre-module marker state in an isolated fixture;
        # bypassing guards is confined to this test's SQL setup, not the API.
        self.env.cr.execute('UPDATE approval_request SET vataga_rfq_created = FALSE WHERE id = %s', [request.id])
        request.invalidate_recordset()
        with self.assertRaises(UserError):
            request.action_create_purchase_orders()
        request.product_line_ids.purchase_order_line_id.order_id.unlink()
        request.invalidate_recordset()
        self.assertTrue(request.vataga_rfq_created)
