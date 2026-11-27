from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseApproval(PurchaseApprovalCase):
    def test_combined_tree_shows_purchase_quantity_with_native_header_disabled(self):
        from types import SimpleNamespace
        from lxml import etree
        from odoo.tools.safe_eval import safe_eval
        tree_id = self.env.ref('approvals.approval_product_line_view_tree').id
        view = self.env['approval.product.line'].get_view(tree_id, 'tree')
        quantity = etree.fromstring(view['arch']).xpath("//field[@name='quantity']")[0]
        parent = SimpleNamespace(approval_type='purchase', has_quantity='no')
        self.assertFalse(safe_eval(quantity.get('column_invisible', 'False'), {'parent': parent}))

    def test_existing_product_without_url(self):
        self.approve(self.request())

    def test_submission_fills_missing_unit_for_existing_product(self):
        request = self.request([self.line_values(product_uom_id=False)])
        request.action_confirm()
        self.assertEqual(request.product_line_ids.product_uom_id, self.product.uom_id)

    def test_missing_product_approved_by_native_workflow(self):
        request = self.request([self.line_values(missing=True)])
        before = self.env['product.product'].search_count([])
        self.approve(request)
        self.assertEqual(self.env['product.product'].search_count([]), before)
        line = request.product_line_ids
        self.assertFalse(line.product_id)
        self.assertTrue(line.vataga_approval_recorded)
        self.assertTrue(line.vataga_product_missing_at_approval)
        self.assertFalse(line.vataga_filled_after_approval)

    def test_mixed_request_preserves_snapshot(self):
        request = self.request([self.line_values(), self.line_values(missing=True)])
        self.approve(request)
        self.assertEqual(request.product_line_ids.mapped('vataga_product_missing_at_approval'), [False, True])

    def test_empty_purchase_line_can_be_saved_as_draft(self):
        request = self.request([{'description': False, 'quantity': 0}])
        self.assertEqual(request.request_status, 'new')
        with self.assertRaises(ValidationError):
            request.action_confirm()

    def test_empty_request_cannot_be_submitted(self):
        request = self.request()
        request.product_line_ids.unlink()
        with self.assertRaises(ValidationError):
            request.action_confirm()

    def test_submission_reports_all_invalid_rows(self):
        request = self.request([self.line_values(missing=True, description=False, quantity=0),
                                self.line_values(missing=True, vataga_product_url='ftp://example.com')])
        with self.assertRaises(ValidationError) as error:
            request.action_confirm()
        self.assertIn('рядок 1', str(error.exception))
        self.assertIn('рядок 2', str(error.exception))

    def test_creator_cannot_be_spoofed_by_values_or_context(self):
        model = self.env['approval.request'].with_user(self.owner).with_context(
            default_request_owner_id=self.stranger.id)
        request = model.create({'name': 'Creator check', 'category_id': self.category.id,
                                'request_owner_id': self.stranger.id})
        self.assertEqual(request.request_owner_id, self.owner)
        with self.assertRaises(AccessError):
            request.write({'request_owner_id': self.stranger.id})

    def test_default_audit_flags_cannot_be_forged(self):
        request = self.env['approval.request'].with_user(self.owner).with_context(
            default_vataga_rfq_created=True, default_vataga_approved_once=True).create({
                'name': 'Default check', 'category_id': self.category.id})
        self.assertFalse(request.vataga_rfq_created)
        self.assertFalse(request.vataga_approved_once)
        with self.assertRaises(AccessError):
            request.write({'vataga_rfq_created': True})

    def test_nonpurchase_owner_and_description_keep_native_rules(self):
        request = self.request(category=self.other_category, user=self.env.user,
                               request_owner_id=self.stranger.id)
        self.assertEqual(request.with_user(self.stranger).request_owner_id, self.stranger)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env['approval.product.line'].create({'approval_request_id': request.id, 'description': False})

    def test_required_approver_and_minimum_are_preserved(self):
        request = self.request([self.line_values(missing=True)])
        request.action_confirm()
        request.with_user(self.buyer).action_approve()
        self.assertNotEqual(request.request_status, 'approved')
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.request_status, 'approved')

    def test_sequential_approval_is_preserved(self):
        self.category.approver_sequence = True
        request = self.request([self.line_values(missing=True)])
        request.approver_ids = [Command.clear(),
                               Command.create({'user_id': self.approver.id, 'sequence': 10, 'required': True}),
                               Command.create({'user_id': self.buyer.id, 'sequence': 20, 'required': True})]
        # Sequence is a category setting; use a dedicated category for this test.
        request.action_confirm()
        self.assertEqual(request.approver_ids.filtered(lambda a: a.user_id == self.buyer).status, 'waiting')
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.approver_ids.filtered(lambda a: a.user_id == self.buyer).status, 'pending')
        request.with_user(self.buyer).action_approve()
        self.assertEqual(request.request_status, 'approved')

    def test_unassigned_user_cannot_approve(self):
        request = self.request([self.line_values(missing=True)])
        request.action_confirm()
        with self.assertRaises((AccessError, UserError)):
            request.with_user(self.stranger).action_approve()

    def test_request_number_is_distinct_from_manual_subject(self):
        request = self.request()
        self.assertEqual(request.vataga_display_subject, request.name)
        self.assertTrue(request.vataga_display_number.startswith('VPR/'))
        self.assertEqual(request.vataga_initiator_id, self.owner)

    def test_creator_audit_cannot_be_spoofed(self):
        request = self.request(create_uid=self.stranger.id)
        self.assertEqual(request.vataga_initiator_id, self.owner)
        self.assertEqual(request.request_owner_id, self.owner)

    def test_automatic_number_keeps_subject_separate(self):
        category = self.category.copy({'name': 'Auto-numbered purchase', 'automated_sequence': True, 'sequence_code': 'TESTAUTO'})
        request = self.request(category=category, vataga_purchase_subject='A separate purchase topic')
        self.assertEqual(request.vataga_display_number, request.name)
        self.assertEqual(request.vataga_display_subject, 'A separate purchase topic')

    def test_empty_subject_can_be_created_imported_but_not_submitted(self):
        category = self.category.copy({'name': 'Optional topic auto purchase', 'automated_sequence': True, 'sequence_code': 'TESTOPTIONAL'})
        request = self.request(category=category, vataga_purchase_subject=False)
        number = request.name
        request.write({'vataga_purchase_subject': 'Temporary'})
        result = request.load(['.id', 'vataga_purchase_subject'], [[str(request.id), '']])
        self.assertEqual(result['ids'], [request.id])
        self.assertFalse(request.vataga_purchase_subject)
        with self.assertRaisesRegex(ValidationError, 'Вкажіть тему закупівлі'):
            request.action_confirm()
        request.vataga_purchase_subject = 'Valid topic'
        self.approve(request)
        self.assertEqual(request.name, number)

    def test_standard_activities_and_approver_history_survive(self):
        request = self.request([self.line_values(missing=True)])
        request.action_confirm()
        self.assertTrue(request.activity_ids)
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.approver_ids.filtered(lambda a: a.user_id == self.approver).status, 'approved')
        self.assertTrue(request.message_ids)

    def test_minimum_approval_count_is_preserved(self):
        self.category.approval_minimum = 2
        request = self.request([self.line_values(missing=True)])
        request.action_confirm()
        request.with_user(self.approver).action_approve()
        self.assertNotEqual(request.request_status, 'approved')
        request.with_user(self.buyer).action_approve()
        self.assertEqual(request.request_status, 'approved')

    def test_nonpurchase_workflow_remains_native(self):
        request = self.request(category=self.other_category)
        self.approve(request)
        self.assertFalse(request.vataga_approved_once)
        self.assertFalse(request.product_line_ids.vataga_approval_recorded)

    def test_required_manager_approval_remains_native(self):
        manager = self.env['hr.employee'].create({'name': 'Test purchase manager',
                                                'user_id': self.approver.id, 'company_id': self.company.id})
        self.env['hr.employee'].create({'name': 'Test purchase requester', 'user_id': self.owner.id,
                                       'parent_id': manager.id, 'company_id': self.company.id})
        category = self.category.copy({'name': 'Required manager purchase', 'manager_approval': 'required'})
        request = self.env['approval.request'].with_user(self.owner).create({
            'name': 'Manager approval test', 'category_id': category.id,
            'product_line_ids': [Command.create(self.line_values(missing=True))]})
        request.action_confirm()
        manager_approver = request.approver_ids.filtered(lambda a: a.user_id == self.approver)
        self.assertTrue(manager_approver)
        self.assertTrue(manager_approver.required)
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.request_status, 'approved')
