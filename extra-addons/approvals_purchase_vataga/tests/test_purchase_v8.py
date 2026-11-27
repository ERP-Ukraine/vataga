"""Run ONLY in a separate licensed Enterprise test database, never shared staging."""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install')
class TestPurchaseV8(PurchaseApprovalCase):
    def editor(self, mode='new', **line_values):
        request = self.approve(self.request([self.line_values(missing=True, **line_values)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
            'line_id': request.product_line_ids.id, 'mode': mode,
            'new_product_name': 'TEST deferred product', 'product_id': self.product.id if mode == 'existing' else False})
        action = wizard.action_apply_open_product()
        product = self.env['product.product'].with_user(self.catalog_buyer).with_context(action['params']['context'])
        return request, wizard, product

    def test_open_and_cancel_have_no_database_effect(self):
        products = self.env['product.product'].search([]).ids
        sellers = self.env['product.supplierinfo'].search([]).ids
        request, wizard, _product = self.editor()
        self.assertEqual(self.env['product.product'].search([]).ids, products)
        self.assertEqual(self.env['product.supplierinfo'].search([]).ids, sellers)
        self.assertFalse(request.product_line_ids.product_id)
        self.assertFalse(request.product_line_ids.vataga_filled_at)
        self.assertEqual(wizard.mode, 'new')
        self.assertEqual(wizard.new_product_name, 'TEST deferred product')

    def test_final_saved_uom_and_vendor_are_used_once(self):
        request, wizard, products = self.editor()
        metre = self.env.ref('uom.product_uom_meter')
        saved = products.web_save({'name': 'TEST final metre', 'uom_id': metre.id, 'uom_po_id': metre.id}, {})
        product = self.env['product.product'].browse(saved[0]['id'])
        self.assertEqual(request.product_line_ids.product_id, product)
        self.assertEqual(request.product_line_ids.product_uom_id, metre)
        self.assertEqual(product.seller_ids.partner_id, self.default_vendor)
        with self.assertRaises(UserError):
            wizard.action_apply_open_product()

    def test_explicit_approved_incompatible_uom_rolls_back_product_and_supplier(self):
        request, _wizard, products = self.editor(product_uom_id=self.product.uom_id.id)
        before_products = self.env['product.product'].search([]).ids
        before_sellers = self.env['product.supplierinfo'].search([]).ids
        metre = self.env.ref('uom.product_uom_meter')
        with self.assertRaises(ValidationError):
            products.web_save({'name': 'TEST rollback metre', 'uom_id': metre.id, 'uom_po_id': metre.id}, {})
        self.assertEqual(self.env['product.product'].search([]).ids, before_products)
        self.assertEqual(self.env['product.supplierinfo'].search([]).ids, before_sellers)
        self.assertFalse(request.product_line_ids.product_id)
        self.assertFalse(request.product_line_ids.vataga_filled_after_approval)

    def test_existing_unchanged_confirmation_and_cancel(self):
        request, _wizard, products = self.editor(mode='existing')
        before = self.product.read(['name', 'uom_id', 'seller_ids'])
        self.assertFalse(request.product_line_ids.product_id)
        products.browse(self.product.id).web_save({}, {})
        self.assertEqual(request.product_line_ids.product_id, self.product)
        self.assertFalse(request.product_line_ids.vataga_product_created_from_request)
        self.assertEqual(self.product.read(['name', 'uom_id', 'seller_ids']), before)

    def test_resolution_failure_is_atomic_for_existing_product_changes(self):
        request, _wizard, products = self.editor(mode='existing')
        original = self.product.name
        with patch.object(type(request.product_line_ids), '_vataga_resolve_product', side_effect=UserError('TEST rollback')):
            with self.assertRaises(UserError):
                products.browse(self.product.id).web_save({'name': 'TEST must roll back'}, {})
        self.product.invalidate_recordset()
        self.assertEqual(self.product.name, original)
        self.assertFalse(request.product_line_ids.product_id)

    def test_forged_editor_identity_is_rejected(self):
        request, wizard, products = self.editor()
        with self.assertRaises(AccessError):
            products.browse(self.product.id).web_save({}, {})
        with self.assertRaises(AccessError):
            products.with_context(vataga_product_editor_id=str(wizard.id)).web_save({}, {})
        self.assertFalse(request.product_line_ids.product_id)

    def test_po_reference_native_copy_and_explicit_copy_do_not_rewire_lines(self):
        self.add_vendor()
        request = self.approve(self.request(user=self.catalog_buyer))
        request.with_user(self.catalog_buyer).action_create_purchase_orders()
        line = request.product_line_ids
        original_line_id = line.purchase_order_line_id.id
        order = line.purchase_order_line_id.order_id.with_user(self.catalog_buyer)
        self.assertEqual(order.vataga_approval_request_ids, request)
        original_values = order.read(['name', 'origin', 'state', 'vataga_rfq_origin_key'])
        plain = order.copy()
        self.assertFalse(plain.vataga_origin_approval_ids)
        self.assertFalse(plain.vataga_approval_request_ids)
        result = order.action_vataga_duplicate_with_approval()
        special = self.env['purchase.order'].browse(result['res_id'])
        self.assertEqual(special.state, 'draft')
        self.assertNotEqual(special.name, order.name)
        self.assertEqual(special.vataga_approval_request_ids, request)
        self.assertFalse(special.vataga_rfq_origin_key)
        self.assertEqual(line.purchase_order_line_id.id, original_line_id)
        self.assertEqual(line.vataga_purchase_order_id, order)
        self.assertEqual(order.read(['name', 'origin', 'state', 'vataga_rfq_origin_key']), original_values)
        with self.assertRaises(UserError):
            request.with_user(self.catalog_buyer).action_create_purchase_orders()

    def test_po_reference_cannot_be_supplied_by_rpc_defaults_or_write(self):
        for values, context in [({'vataga_origin_approval_ids': []}, {}),
                                ({}, {'default_vataga_origin_approval_ids': [1]})]:
            with self.assertRaises(AccessError):
                self.env['purchase.order'].with_context(context).create(dict(partner_id=self.vendor.id, **values))
        order = self.env['purchase.order'].create({'partner_id': self.vendor.id})
        with self.assertRaises(AccessError):
            order.write({'vataga_origin_approval_ids': []})
        with self.assertRaises(AccessError):
            order.action_vataga_duplicate_with_approval()

    def test_empty_manual_draft_and_whitespace_submission(self):
        request = self.request(name=False)
        self.assertFalse(request.name)
        with self.assertRaisesRegex(ValidationError, 'Вкажіть тему закупівлі'):
            request.action_confirm()
        request.name = '   '
        with self.assertRaisesRegex(ValidationError, 'Вкажіть тему закупівлі'):
            request.action_confirm()
        request.name = 'Valid manual subject'
        request.action_confirm()
        self.assertEqual(request.request_status, 'pending')

    def test_historical_multiple_requests_use_native_links_without_backfill(self):
        from ..internal import RFQ
        first = self.approve(self.request())
        second = self.approve(self.request())
        order = self.env['purchase.order'].create({'partner_id': self.vendor.id})
        for request in (first, second):
            purchase_line = self.env['purchase.order.line'].create({
                'order_id': order.id, 'product_id': self.product.id, 'name': 'TEST historical',
                'product_qty': 2, 'product_uom': self.product.uom_id.id, 'price_unit': 1})
            request.product_line_ids.with_context(_vataga_rfq=RFQ).write({'purchase_order_line_id': purchase_line.id})
        self.assertEqual(order.vataga_approval_request_ids, first | second)
        self.assertFalse(order.vataga_origin_approval_ids)
        self.assertFalse((first | second).filtered('vataga_rfq_created'))
        self.assertEqual((first | second).product_line_ids.vataga_purchase_order_id, order)

    def test_registry_purchase_is_masked_without_native_purchase_read_acl(self):
        self.add_vendor()
        request = self.approve(self.request())
        request.with_user(self.catalog_buyer).action_create_purchase_orders()
        line = request.product_line_ids.with_user(self.owner)
        self.assertFalse(line.vataga_purchase_order_id)
        self.assertFalse(line.search([('id', '=', line.id), ('vataga_purchase_order_id', '!=', False)]))
        groups = line.read_group([('id', '=', line.id)], ['vataga_purchase_order_id'], ['vataga_purchase_order_id'])
        self.assertEqual(groups[0]['vataga_purchase_order_id'], False)
        self.assertEqual(line.export_data(['vataga_purchase_order_id'])['datas'], [['']])
        with self.assertRaises(AccessError):
            line.action_vataga_open_purchase_order()
