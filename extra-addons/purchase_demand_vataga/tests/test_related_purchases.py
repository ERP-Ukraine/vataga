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
                'product_id': self.product_a.id,
                'quantity': 1,
                'price_unit': 100,
                'account_id': self.company_data['default_account_expense'].id,
                'purchase_line_id': order.order_line.id,
            }) for order in orders],
        })

    def _demand(self, bills):
        # Isolate the new relation from the existing stored invoice computation.
        return self.env['product.analytic'].new({
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
