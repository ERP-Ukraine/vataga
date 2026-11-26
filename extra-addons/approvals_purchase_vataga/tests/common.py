from odoo import Command
from odoo.tests import TransactionCase, new_test_user


class PurchaseApprovalCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.owner = new_test_user(cls.env, login='vataga_owner', groups='base.group_user')
        cls.approver = new_test_user(cls.env, login='vataga_approver', groups='base.group_user')
        cls.buyer = new_test_user(cls.env, login='vataga_buyer', groups='base.group_user,purchase.group_purchase_user')
        cls.catalog_buyer = new_test_user(cls.env, login='vataga_catalog_buyer',
                                         groups='base.group_user,purchase.group_purchase_manager')
        cls.stranger = new_test_user(cls.env, login='vataga_stranger', groups='base.group_user')
        cls.category = cls.env['approval.category'].create({
            'name': 'Vataga purchase test', 'approval_type': 'purchase',
            'company_id': cls.company.id, 'automated_sequence': False,
            'approval_minimum': 1, 'has_product': 'required',
        })
        cls.other_category = cls.env['approval.category'].create({
            'name': 'Vataga ordinary test', 'company_id': cls.company.id,
            'automated_sequence': False, 'approval_minimum': 1,
        })
        cls.product = cls.env['product.product'].create({'name': 'Vataga existing', 'purchase_ok': True})
        cls.vendor = cls.env['res.partner'].create({'name': 'Vataga supplier', 'supplier_rank': 1})
        cls.default_vendor = cls.env['res.partner'].create({'name': 'довільний постачальник', 'supplier_rank': 1})
        cls.company.vataga_default_supplier_id = cls.default_vendor

    def line_values(self, missing=False, **extra):
        values = {'description': 'Agreed purchase description', 'quantity': 2}
        if missing:
            values['vataga_product_url'] = 'https://example.com/part'
        else:
            values.update(product_id=self.product.id, product_uom_id=self.product.uom_id.id)
        if 'warehouse_id' in self.env['approval.product.line']._fields:
            values['warehouse_id'] = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company.id)], limit=1).id
        return dict(values, **extra)

    def request(self, lines=None, user=None, category=None, **extra):
        return self.env['approval.request'].with_user(user or self.owner).create(dict({
            'name': 'Vataga test request', 'category_id': (category or self.category).id,
            'vataga_purchase_subject': 'Purchase test',
            'product_line_ids': [Command.create(v) for v in (lines or [self.line_values()])],
            'approver_ids': [Command.create({'user_id': self.approver.id, 'required': True}),
                             Command.create({'user_id': self.buyer.id})],
        }, **extra))

    def approve(self, request):
        request.with_user(request.request_owner_id).action_confirm()
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.request_status, 'approved')
        return request

    def add_vendor(self, product=None, partner=None, **extra):
        return self.env['product.supplierinfo'].create(dict({
            'product_tmpl_id': (product or self.product).product_tmpl_id.id,
            'partner_id': (partner or self.vendor).id, 'price': 15,
            'company_id': self.company.id, 'min_qty': 0,
        }, **extra))
