from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseStudio(PurchaseApprovalCase):
    def employee(self, name, **values):
        return self.env['hr.employee'].create(dict(name=name, company_id=self.company.id, **values))

    def test_independent_optional_people_and_employee_projections(self):
        initiator = self.employee('TEST-VATAGA-APPROVALS Initiator')
        recipient = self.employee('TEST-VATAGA-APPROVALS Recipient')
        request = self.request(x_studio_iniciation=initiator.id, x_studio_who_recived=recipient.id)
        self.assertEqual(request.request_owner_id, self.owner)
        self.assertNotEqual(initiator, recipient)
        self.assertEqual(request.product_line_ids.vataga_employee_initiator_id, initiator)
        self.assertEqual(request.product_line_ids.vataga_employee_recipient_id, recipient)
        self.assertEqual(request.vataga_initiator_id, self.owner)  # retained legacy audit
        empty = self.request()
        self.assertFalse(empty.x_studio_iniciation)
        self.assertFalse(empty.x_studio_who_recived)

    def test_lock_is_server_side_and_legacy_relations_are_unchanged(self):
        employee = self.employee('TEST-VATAGA-APPROVALS Lock')
        request = self.approve(self.request(x_studio_iniciation=employee.id))
        with self.assertRaises(UserError):
            request.with_user(self.owner).write({'x_studio_who_recived': employee.id})
        with self.assertRaises(UserError):
            request.with_user(self.owner).write({'x_studio_iniciation': False})
        self.assertEqual(request._fields['vataga_initiator_id'].comodel_name, 'res.users')
        self.assertEqual(request._fields['vataga_recipient_id'].comodel_name, 'res.partner')
        with self.assertRaises(AccessError):
            request.write({'vataga_recipient_id': self.vendor.id})

    def test_archived_employee_cannot_be_newly_assigned(self):
        employee = self.employee('TEST-VATAGA-APPROVALS Archived', active=False)
        with self.assertRaises(ValidationError):
            self.request(x_studio_iniciation=employee.id)

    def test_selected_employee_does_not_become_an_approver_or_owner(self):
        employee = self.employee('TEST-VATAGA-APPROVALS No grant', user_id=self.stranger.id)
        request = self.request(x_studio_who_recived=employee.id)
        self.assertEqual(request.request_owner_id, self.owner)
        self.assertNotIn(self.stranger, request.approver_ids.user_id)

    def test_registry_employee_search_group_and_export(self):
        employee = self.employee('TEST-VATAGA-APPROVALS Search')
        request = self.request(x_studio_iniciation=employee.id)
        lines = self.env['approval.product.line'].with_user(self.owner)
        domain = [('approval_request_id', '=', request.id), ('vataga_employee_initiator_id', '=', employee.id)]
        self.assertEqual(lines.search(domain), request.product_line_ids)
        self.assertEqual(lines.read_group(domain, ['quantity:sum'], ['vataga_employee_initiator_id'])[0]['vataga_employee_initiator_id'][0], employee.id)
        self.assertTrue(lines.search(domain).export_data(['vataga_employee_initiator_id'])['datas'])

    def test_form_does_not_duplicate_studio_fields(self):
        from lxml import etree
        view = self.env.ref('approvals.approval_request_view_form')
        arch = etree.fromstring(self.env['approval.request'].get_view(view.id, 'form')['arch'].encode())
        for name in ('x_studio_iniciation', 'x_studio_who_recived'):
            self.assertEqual(len(arch.xpath(".//field[@name='%s']" % name)), 1)
        for name in ('vataga_initiator_id', 'vataga_recipient_id'):
            self.assertFalse(arch.xpath(".//field[@name='%s']" % name))
