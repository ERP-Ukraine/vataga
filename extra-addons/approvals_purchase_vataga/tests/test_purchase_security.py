from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseSecurity(PurchaseApprovalCase):
    def test_native_import_cannot_update_approved_quantity(self):
        request = self.approve(self.request())
        line = request.product_line_ids
        quantity = line.quantity
        result = line.with_user(self.buyer).load(['.id', 'quantity'], [[str(line.id), '999']])
        self.assertFalse(result.get('ids'))
        self.assertTrue(any(message.get('type') == 'error' for message in result['messages']))
        line.invalidate_recordset()
        self.assertEqual(line.quantity, quantity)

    def test_open_product_action_does_not_leak_approval_defaults_to_supplier(self):
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).with_context(
            category_id=self.category.id, default_category_id=self.category.id,
            default_line_id=request.product_line_ids.id).create({
                'line_id': request.product_line_ids.id, 'mode': 'new',
                'new_product_name': 'Vataga isolated product context'})
        action = wizard.action_apply_open_product()
        self.assertEqual(action['tag'], 'vataga_product_editor')
        params = action['params']
        self.assertNotIn('default_category_id', params['context'])
        self.assertNotIn('default_line_id', params['context'])
        self.assertEqual(params['context']['active_model'], 'product.product')
        self.assertEqual(params['context']['active_id'], params['res_id'])
        context = dict(wizard.env.context, **params['context'])
        self.assertFalse(context['category_id'])
        vendor = self.env['res.partner'].with_user(self.catalog_buyer).with_context(context).create({
            'name': 'Vataga isolated-context supplier', 'supplier_rank': 1})
        self.assertFalse(vendor.category_id)

    def test_both_new_product_buttons_add_default_supplier_once(self):
        for open_product in (False, True):
            request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
            wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
                'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'TEST wizard vendor'})
            result = wizard.action_apply_open_product() if open_product else wizard.action_apply()
            if open_product:
                self.assertFalse(request.product_line_ids.product_id)
                self.env['product.product'].with_user(self.catalog_buyer).with_context(
                    result['params']['context']).web_save({'name': 'TEST wizard vendor'}, {})
            sellers = request.product_line_ids.product_id.seller_ids
            self.assertEqual(sellers.partner_id, self.default_vendor)
            self.assertEqual(len(sellers), 1)
            self.assertEqual(sellers.price, 0)
            self.assertEqual(result['type'], 'ir.actions.client' if open_product else 'ir.actions.act_window_close')
            with self.assertRaises(UserError):
                wizard.action_apply()
            self.assertEqual(len(sellers), 1)

    def test_missing_default_supplier_rolls_back_and_existing_is_untouched(self):
        self.company.vataga_default_supplier_id = False
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
            'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'TEST must roll back'})
        before = self.env['product.product'].search_count([])
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertEqual(self.env['product.product'].search_count([]), before)
        self.assertFalse(request.product_line_ids.product_id)
        sellers = self.product.seller_ids.ids
        wizard.write({'mode': 'existing', 'product_id': self.product.id})
        wizard.action_apply()
        self.assertEqual(self.product.seller_ids.ids, sellers)

    def test_supplier_creation_failure_rolls_back_new_product_and_line(self):
        from unittest.mock import patch
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
            'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'TEST atomic supplier'})
        before = self.env['product.product'].search_count([])
        with patch.object(type(self.env['product.supplierinfo']), 'create', side_effect=AccessError('Denied')):
            with self.assertRaises(AccessError):
                wizard.action_apply()
        self.assertEqual(self.env['product.product'].search_count([]), before)
        self.assertFalse(request.product_line_ids.product_id)

    def test_new_product_with_supplier_from_an_extension_is_not_duplicated(self):
        from unittest.mock import patch
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
            'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'TEST extension supplier'})
        model = type(self.env['product.product'])
        original = model.create
        def create_with_supplier(records, values):
            product = original(records, values)
            records.env['product.supplierinfo'].create({
                'product_tmpl_id': product.product_tmpl_id.id, 'partner_id': self.default_vendor.id,
                'company_id': self.company.id})
            return product
        with patch.object(model, 'create', create_with_supplier):
            wizard.action_apply()
        self.assertEqual(len(request.product_line_ids.product_id.seller_ids), 1)

    def test_unconfigured_ambiguous_names_never_choose_a_supplier(self):
        self.env['res.partner'].create({'name': 'довільний постачальник', 'supplier_rank': 1})
        self.company.vataga_default_supplier_id = False
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).create({
            'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'TEST ambiguous supplier'})
        before = self.env['product.product'].search_count([])
        with self.assertRaises(ValidationError):
            wizard.action_apply()
        self.assertEqual(self.env['product.product'].search_count([]), before)
        self.assertFalse(request.product_line_ids.product_id)

    def test_new_wizard_permission_defaults_match_existing_acl(self):
        for user in (self.buyer, self.catalog_buyer):
            wizard = self.env['approval.product.resolve.vataga'].with_user(user)
            expected = wizard._catalog_permissions()
            values = wizard.default_get(['can_create_product', 'can_manage_suppliers'])
            self.assertEqual((values['can_create_product'], values['can_manage_suppliers']), expected)

    def test_readonly_workflow_fields_cannot_be_injected_on_create(self):
        for values in ({'approval_type': False}, {'approval_minimum': 0}):
            with self.subTest(values=values), self.assertRaises(AccessError):
                self.request(**values)
        with self.assertRaises(AccessError):
            self.env['approval.request'].with_user(self.owner).with_context(
                default_approval_minimum=0).create({'name': 'Forged defaults', 'category_id': self.category.id})

    def test_readonly_workflow_fields_cannot_be_injected(self):
        request = self.request()
        for values in ({'approval_type': False}, {'approval_minimum': 0}, {'request_status': 'approved'}):
            with self.subTest(values=values), self.assertRaises(AccessError):
                request.write(values)

    def test_direct_approver_writes_cannot_approve_or_reset_request(self):
        request = self.request()
        approver = request.approver_ids.filtered(lambda a: a.user_id == self.approver)
        with self.assertRaises(AccessError):
            approver.write({'status': 'approved'})
        request.action_confirm()
        for operation in (
            lambda: approver.write({'status': 'approved'}),
            lambda: request.write({'approver_ids': [Command.update(approver.id, {'status': 'approved'})]}),
            lambda: request.write({'approver_ids': [Command.clear()]}),
            lambda: approver.unlink(),
        ):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                operation()
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.request_status, 'approved')
        self.assertTrue(request.vataga_approved_once)

    def test_approver_context_cannot_inject_approved_status(self):
        request = self.request()
        with self.assertRaises(AccessError):
            self.env['approval.approver'].with_user(self.owner).with_context(
                default_status='approved', _vataga_workflow=True).create({
                    'request_id': request.id, 'user_id': self.stranger.id})

    def test_pending_lines_are_frozen(self):
        request = self.request()
        request.action_confirm()
        with self.assertRaises(UserError):
            request.product_line_ids.write({'quantity': 99})

    def test_all_approved_business_fields_are_frozen(self):
        request = self.approve(self.request())
        for values in ({'quantity': 5}, {'description': 'Changed'}, {'product_id': False},
                       {'vataga_product_url': 'https://other.example.com'},
                       {'product_uom_id': False}):
            with self.subTest(values=values), self.assertRaises(UserError):
                request.product_line_ids.with_user(self.buyer).write(values)

    def test_approved_add_delete_and_one2many_commands_are_denied(self):
        request = self.approve(self.request()).with_user(self.buyer)
        operations = [lambda: request.write({'product_line_ids': [Command.create(self.line_values())]}),
                      lambda: request.product_line_ids.unlink(),
                      lambda: request.write({'product_line_ids': [Command.clear()]}),
                      lambda: request.write({'product_line_ids': [Command.update(request.product_line_ids.id, {'quantity': 3})]})]
        for operation in operations:
            with self.assertRaises(UserError), self.env.cr.savepoint():
                operation()

    def test_line_move_and_category_change_are_denied(self):
        approved = self.approve(self.request())
        draft = self.request()
        with self.assertRaises(UserError):
            approved.product_line_ids.write({'approval_request_id': draft.id})
        with self.assertRaises(UserError):
            approved.write({'category_id': self.other_category.id})
        with self.assertRaises(UserError):
            self.category.write({'approval_type': False})

    def test_buyer_resolves_once_preserving_description_and_url(self):
        request = self.approve(self.request([self.line_values(missing=True)]))
        line = request.product_line_ids.with_user(self.buyer)
        agreed = (line.description, line.vataga_product_url, line.quantity)
        line.write({'product_id': self.product.id})
        line.invalidate_recordset()
        self.assertEqual((line.description, line.vataga_product_url, line.quantity), agreed)
        self.assertEqual(line.product_uom_id, self.product.uom_id)
        self.assertEqual(line.vataga_filled_by_id, self.buyer)
        self.assertTrue(line.vataga_filled_at)
        self.assertTrue(line.vataga_filled_after_approval)
        with self.assertRaises(UserError):
            line.write({'product_id': self.product.id})

    def test_ordinary_user_cannot_resolve(self):
        request = self.approve(self.request([self.line_values(missing=True)]))
        with self.assertRaises(AccessError):
            request.product_line_ids.write({'product_id': self.product.id})

    def test_incompatible_uom_is_rejected(self):
        mass = self.env.ref('uom.product_uom_kgm')
        request = self.approve(self.request([self.line_values(missing=True, product_uom_id=mass.id)]))
        with self.assertRaises(ValidationError):
            request.product_line_ids.with_user(self.buyer).write({'product_id': self.product.id})

    def test_audit_and_related_fields_cannot_be_forged(self):
        line = self.request().product_line_ids
        for name, value in (('vataga_filled_after_approval', True),
                            ('vataga_approval_type', False), ('vataga_owner_id', self.stranger.id)):
            with self.subTest(name=name), self.assertRaises(AccessError):
                line.write({name: value})

    def test_product_creation_happens_only_on_explicit_wizard_action(self):
        request = self.approve(self.request([self.line_values(missing=True)], user=self.catalog_buyer))
        line = request.product_line_ids.with_user(self.catalog_buyer)
        before = self.env['product.product'].search_count([])
        action = line.action_vataga_resolve()
        self.assertEqual(self.env['product.product'].search_count([]), before)
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.catalog_buyer).with_context(action['context']).create({
            'line_id': line.id, 'mode': 'new', 'new_product_name': 'Vataga created on purpose'})
        wizard.action_apply()
        self.assertTrue(line.vataga_product_created_from_request)
        self.assertEqual(line.product_id.vataga_purchase_url, line.vataga_product_url)

    def test_purchase_group_does_not_grant_product_creation(self):
        request = self.approve(self.request([self.line_values(missing=True)]))
        wizard = self.env['approval.product.resolve.vataga'].with_user(self.buyer).create({
            'line_id': request.product_line_ids.id, 'mode': 'new', 'new_product_name': 'Denied product'})
        self.assertFalse(wizard.can_create_product)
        self.assertFalse(wizard.can_manage_suppliers)
        with self.assertRaises(AccessError):
            wizard.action_apply()
        self.assertFalse(request.product_line_ids.product_id)

    def test_return_to_draft_does_not_unlock_previously_approved_lines(self):
        request = self.approve(self.request())
        request.action_cancel()
        request.action_draft()
        with self.assertRaises(UserError):
            request.product_line_ids.write({'quantity': 7})

    def test_catalog_recomputation_cannot_change_agreed_description_or_uom(self):
        request = self.approve(self.request([self.line_values(missing=True)]))
        line = request.product_line_ids.with_user(self.buyer)
        line.write({'product_id': self.product.id})
        original = (line.description, line.product_uom_id)
        self.product.description_purchase = 'Changed catalog purchase description'
        for name in ('description', 'product_uom_id'):
            field = line._fields[name]
            if field.compute:
                self.env.add_to_compute(field, line)
                line._recompute_recordset([name])
        line.flush_recordset()
        line.invalidate_recordset()
        self.assertEqual((line.description, line.product_uom_id), original)
