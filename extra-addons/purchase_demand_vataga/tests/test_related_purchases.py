from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged

from ..models.product import ProductAnalytic


@tagged('post_install', '-at_install')
class TestRelatedPurchases(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user.groups_id += cls.env.ref('purchase.group_purchase_user')
        plan = cls.env.ref('analytic_vataga.account_analytic_plan_seller_contract')
        cls.contract = cls.env['account.analytic.account'].create({
            'name': 'Demand contract', 'plan_id': plan.id, 'company_id': False,
        })
        cls.other_contract = cls.contract.copy({'name': 'Other demand contract'})
        cls.orders = cls.env['purchase.order'].create([
            {
                'partner_id': cls.partner_a.id,
                'order_line': [Command.create({
                    'product_id': cls.product_a.id,
                    'name': 'Demand purchase',
                    'product_qty': 1,
                    'product_uom': cls.product_a.uom_id.id,
                    'price_unit': 100,
                    'date_planned': fields.Datetime.now(),
                })],
            } for unused in range(3)
        ])

    def _bill(self, orders, move_type='in_invoice'):
        return self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner_a.id,
            'invoice_line_ids': [Command.create({
                'product_id': order.order_line.product_id.id,
                'analytic_distribution': {str(self.contract.id): 100},
                'quantity': 1,
                'price_unit': 100,
                'account_id': self.company_data['default_account_expense'].id,
                'purchase_line_id': order.order_line.id,
            }) for order in orders],
        })

    def _demand(self, bills):
        # Isolate the new relation from the existing stored invoice computation.
        return self.env['product.analytic'].new({
            'product_id': self.product_a.id,
            'sale_contract_id': self.contract.id,
            'account_move_ids': [Command.set(bills.ids)],
        })

    def test_one_bill_one_purchase(self):
        bill = self._bill(self.orders[:1])
        demand = self._demand(bill)
        self.assertEqual(demand.purchase_order_ids, self.orders[:1])
        self.assertEqual(demand.account_move_ids, bill)

    def test_one_bill_multiple_purchases(self):
        demand = self._demand(self._bill(self.orders[:2]))
        self.assertEqual(demand.purchase_order_ids, self.orders[:2])

    def test_shared_purchase_and_refund(self):
        bills = self._bill(self.orders[:2]) | self._bill(
            self.orders[1:2], move_type='in_refund',
        )
        self.assertEqual(self._demand(bills).purchase_order_ids, self.orders[:2])

    def test_multiple_bills_multiple_purchases(self):
        bills = self._bill(self.orders[:1]) | self._bill(self.orders[1:2])
        self.assertEqual(self._demand(bills).purchase_order_ids, self.orders[:2])

    def test_empty_and_unlinked(self):
        self.assertFalse(self._demand(self.env['account.move']).purchase_order_ids)
        bill = self._bill(self.orders[:1])
        bill.invoice_line_ids.purchase_line_id = False
        self.assertFalse(self._demand(bill).purchase_order_ids)

    def test_relation_updates(self):
        bill = self._bill(self.orders[:1])
        demand = self._demand(bill)
        self.assertEqual(demand.purchase_order_ids, self.orders[:1])
        bill.invoice_line_ids.purchase_line_id = self.orders[1].order_line
        # A new form read must reflect current links, without synchronization.
        demand.invalidate_recordset(['purchase_order_ids'])
        self.assertEqual(demand.purchase_order_ids, self.orders[1:2])

    def test_purchase_record_rules(self):
        self.env['ir.rule'].create({
            'name': 'Hide first purchase for demand test',
            'model_id': self.env['ir.model']._get_id('purchase.order'),
            'domain_force': repr([('id', '!=', self.orders[0].id)]),
        })
        demand = self._demand(self._bill(self.orders[:2]))
        self.assertEqual(demand.purchase_order_ids, self.orders[1:2])

    def test_unbilled_without_purchase_contract(self):
        order = self.orders[0]
        order.seller_contract_id = self.contract
        self.assertFalse(order.ua_contract_id)
        self.assertFalse(order.invoice_ids)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, order)
        order.button_cancel()
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, order)

    def test_line_contract_and_header_fallback(self):
        order = self.orders[0]
        order.order_line.analytic_distribution = {str(self.contract.id): 100}
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, order)
        order.seller_contract_id = self.contract
        order.order_line.analytic_distribution = False
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, order)
        order.order_line.analytic_distribution = {str(self.other_contract.id): 100}
        self.assertFalse(self._demand(self.env['account.move']).purchase_order_ids)

    def test_other_contract_same_product(self):
        self.orders[0].seller_contract_id = self.other_contract
        self.assertFalse(self._demand(self.env['account.move']).purchase_order_ids)

    def test_common_bill_excludes_other_product_and_contract(self):
        self.orders[1].order_line.product_id = self.product_b
        bill = self._bill(self.orders[:2])
        self.assertEqual(self._demand(bill).purchase_order_ids, self.orders[:1])
        bill.invoice_line_ids.filtered(
            lambda line: line.product_id == self.product_a
        ).analytic_distribution = {str(self.other_contract.id): 100}
        self.assertFalse(self._demand(bill).purchase_order_ids)

    def test_kit_purchase(self):
        kit = self.env['product.product'].create({'name': 'Demand kit'})
        self.env['mrp.bom'].create({
            'product_tmpl_id': kit.product_tmpl_id.id,
            'type': 'phantom',
            'bom_line_ids': [Command.create({
                'product_id': self.product_a.id, 'product_qty': 1,
            })],
        })
        order = self.orders[0]
        order.order_line.product_id = kit
        order.seller_contract_id = self.contract
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, order)
        order.seller_contract_id = False
        order.order_line.analytic_distribution = False
        self.assertEqual(self._demand(self._bill(order)).purchase_order_ids, order)

    def test_direct_and_invoice_union(self):
        self.orders[:2].seller_contract_id = self.contract
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, self.orders[:2])
        bill = self._bill(self.orders[:2])
        self.assertEqual(self._demand(bill).purchase_order_ids, self.orders[:2])

    def test_direct_purchase_record_rules(self):
        self.orders[:2].seller_contract_id = self.contract
        self.env['ir.rule'].create({
            'name': 'Hide direct purchase for demand test',
            'model_id': self.env['ir.model']._get_id('purchase.order'),
            'domain_force': repr([('id', '!=', self.orders[0].id)]),
        })
        self.assertEqual(self._demand(self.env['account.move']).purchase_order_ids, self.orders[1:2])

    def test_direct_multi_company(self):
        other_company = self.company_data_2['company']
        other_order = self.env['purchase.order'].with_company(other_company).create({
            'company_id': other_company.id,
            'partner_id': self.partner_a.id,
            'seller_contract_id': self.contract.id,
            'order_line': [Command.create({
                'product_id': self.product_a.id,
                'name': 'Other company demand purchase',
                'product_qty': 1,
                'product_uom': self.product_a.uom_id.id,
                'price_unit': 100,
                'date_planned': fields.Datetime.now(),
            })],
        })
        demand = self._demand(self.env['account.move']).with_context(
            allowed_company_ids=[self.env.company.id],
        )
        self.assertFalse(demand.purchase_order_ids)
        demand = demand.with_context(
            allowed_company_ids=[self.env.company.id, other_company.id],
        )
        self.assertEqual(demand.purchase_order_ids, other_order)

    def test_default_form_preserves_fields_and_layout(self):
        model = self.env['product.analytic']
        # Simulate a runtime Studio field without changing the DB schema.
        with patch.dict(model._fields, {'x_studio_demand_note': fields.Char()}):
            baseline = super(ProductAnalytic, model)._get_default_form_view()
            arch = model._get_default_form_view()
        purchase = arch.xpath("./sheet/group/field[@name='purchase_order_ids']")
        self.assertEqual(len(purchase), 1)
        self.assertEqual(len(arch.xpath(".//field[@name='purchase_order_ids']")), 1)
        group = purchase[0].getparent()
        self.assertEqual(group.getprevious()[0].get('name'), 'account_move_ids')
        self.assertEqual(group.getnext()[0].get('name'), 'kit_bom_ids')
        self.assertEqual(purchase[0].get('readonly'), '1')
        tree = purchase[0].find('tree')
        self.assertEqual([node.get('name') for node in tree], [
            'name', 'partner_id', 'date_order', 'amount_total', 'currency_id', 'state',
        ])
        self.assertEqual(tree.find("field[@name='date_order']").get('string'), 'Дата замовлення')
        for attr in ('create', 'edit', 'delete'):
            self.assertEqual(tree.get(attr), '0')
        self.assertTrue(arch.xpath(".//field[@name='x_studio_demand_note']"))
        # Every other node and attribute must be byte-for-byte unchanged.
        for root in (baseline, arch):
            node = root.xpath("./sheet/group/field[@name='purchase_order_ids']")[0]
            root.find('sheet').remove(node.getparent())
        self.assertEqual(etree.tostring(arch), etree.tostring(baseline))

    def test_processed_form(self):
        result = self.env['product.analytic'].get_view(view_type='form')
        arch = etree.fromstring(result['arch'])
        self.assertEqual(len(arch.xpath(".//field[@name='purchase_order_ids']")), 1)
