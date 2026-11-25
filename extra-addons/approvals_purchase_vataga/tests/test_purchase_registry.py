from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseRegistry(PurchaseApprovalCase):
    def test_registry_period_widget_follows_source_types_only_in_registry(self):
        from lxml import etree
        lines = self.env['approval.product.line']
        view = self.env.ref('approvals_purchase_vataga.view_purchase_registry_tree')
        arch = etree.fromstring(lines.get_view(view.id, 'tree')['arch'].encode())
        for source_name in ('date_start', 'date_end'):
            node = arch.xpath(".//field[@name='vataga_%s']" % source_name)[0]
            expected = 'vataga_calendar_date' if self.env['approval.request']._fields[source_name].type == 'date' else None
            self.assertEqual(node.get('widget'), expected)
        ordinary = self.env.ref('approvals.approval_product_line_view_tree')
        self.assertNotIn('vataga_calendar_date', lines.get_view(ordinary.id, 'tree')['arch'])

    def test_period_fields_match_datetime_without_losing_time(self):
        from datetime import datetime
        request = self.request()
        source = request._fields['date_start']
        start = datetime(2026, 11, 24, 11, 27, 33)
        end = datetime(2026, 11, 25, 18, 44, 55)
        if source.type == 'date':
            request.write({'date_start': start.date(), 'date_end': end.date()})
            start, end = start.replace(hour=0, minute=0, second=0), end.replace(hour=0, minute=0, second=0)
        else:
            request.write({'date_start': start, 'date_end': end})
        self.assertEqual(request.product_line_ids.vataga_date_start, start)
        self.assertEqual(request.product_line_ids.vataga_date_end, end)

    def registry(self, user):
        return self.env['approval.product.line'].with_user(user)

    def test_one_line_per_item_across_purchase_categories(self):
        second_category = self.category.copy({'name': 'Another purchase category'})
        first = self.request([self.line_values(), self.line_values(missing=True)])
        second = self.request(category=second_category)
        ordinary = self.request(category=self.other_category)
        domain = [('vataga_approval_type', '=', 'purchase'),
                  ('approval_request_id', 'in', (first | second | ordinary).ids)]
        lines = self.registry(self.owner).search(domain)
        self.assertEqual(len(lines), 3)
        self.assertNotIn(ordinary, lines.approval_request_id)

    def test_registry_does_not_grant_purchase_users_visibility(self):
        request = self.request()
        inaccessible_buyer = self.buyer.copy({'login': 'vataga_other_buyer', 'name': 'Unassigned buyer'})
        domain = [('approval_request_id', '=', request.id)]
        self.assertFalse(self.registry(inaccessible_buyer).search(domain))
        with self.assertRaises(AccessError):
            self.registry(inaccessible_buyer).browse(request.product_line_ids.ids).read(['description'])

    def test_owner_and_assigned_approver_keep_native_visibility(self):
        request = self.request([self.line_values(missing=True)])
        for user in (self.owner, self.approver, self.buyer):
            self.assertEqual(self.registry(user).search_count([('approval_request_id', '=', request.id)]), 1)

    def test_search_group_and_export_do_not_leak(self):
        request = self.request([self.line_values(missing=True)])
        lines = self.registry(self.stranger)
        domain = [('approval_request_id', '=', request.id)]
        self.assertFalse(lines.search(domain))
        self.assertFalse(lines.read_group(domain, ['quantity:sum'], ['vataga_category_id']))
        with self.assertRaises(AccessError):
            lines.browse(request.product_line_ids.ids).export_data(['description', 'vataga_product_url'])

    def test_registry_owner_scope_is_distinct_from_native_approver_document_access(self):
        request = self.request([self.line_values(missing=True)])
        native = self.registry(self.approver)
        domain = [('approval_request_id', '=', request.id)]
        self.assertEqual(native.search_count(domain), 1)
        registry = native.with_context(vataga_purchase_registry=True)
        self.assertFalse(registry.search(domain))
        self.assertFalse(registry.read_group(domain, ['quantity:sum'], ['vataga_category_id']))
        with self.assertRaises(AccessError):
            registry.browse(request.product_line_ids.ids).read(['description'])
        with self.assertRaises(AccessError):
            registry.browse(request.product_line_ids.ids).export_data(['description'])
        with self.assertRaises(AccessError):
            registry.browse(request.product_line_ids.ids).action_vataga_open_request()
        # Leaving the registry context does not grant any new access. The
        # assigned approver retains their pre-existing native document access.
        self.assertEqual(native.browse(request.product_line_ids.ids).read(['description'])[0]['id'], request.product_line_ids.id)
        self.assertEqual(self.registry(self.owner).with_context(vataga_purchase_registry=True).search_count(domain), 1)

    def test_link_to_request_checks_parent_access(self):
        request = self.request()
        action = request.product_line_ids.action_vataga_open_request()
        self.assertEqual(action['res_id'], request.id)
        self.assertEqual(action['res_model'], 'approval.request')
        # Direct ORM field-widget calls do not pass through call_button's
        # clean_action(). The native action service requires explicit views.
        self.assertEqual(action['views'], [(False, 'form')])
        with self.assertRaises(AccessError):
            request.product_line_ids.with_user(self.stranger).action_vataga_open_request()

    def test_company_context_is_respected(self):
        other_company = self.env['res.company'].create({'name': 'Other purchase company'})
        category = self.category.with_company(other_company).copy({'company_id': other_company.id})
        owner = self.owner.copy({'login': 'vataga_other_company_owner',
                                 'company_id': other_company.id,
                                 'company_ids': [(6, 0, [other_company.id])]})
        request = self.env['approval.request'].with_user(owner).with_company(other_company).create({
            'name': 'Other company request', 'category_id': category.id,
            'product_line_ids': [(0, 0, self.line_values(missing=True))]})
        lines = self.registry(self.owner).with_context(allowed_company_ids=[self.company.id])
        self.assertFalse(lines.search([('approval_request_id', '=', request.id)]))
        with self.assertRaises(AccessError):
            lines.browse(request.product_line_ids.ids).read(['description'])

    def test_native_all_requests_group_retains_visibility(self):
        from odoo.tests import new_test_user
        manager = new_test_user(self.env, login='vataga_all_requests',
                                groups='base.group_user,approvals.group_approval_user')
        first = self.request()
        second = self.request(user=self.stranger)
        lines = self.registry(manager).with_context(vataga_purchase_registry=True).search([('approval_request_id', 'in', (first | second).ids)])
        self.assertEqual(len(lines), 2)
