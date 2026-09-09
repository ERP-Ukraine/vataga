from odoo import Command
from odoo.tests import tagged
from odoo.addons.account_vataga.tests.common import InvoiceHeaderAnalyticsCommon


@tagged('post_install', '-at_install')
class TestSaleInvoiceHeaderAnalytics(InvoiceHeaderAnalyticsCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product_a.invoice_policy = 'order'

    def _create_invoicable_order(self, headers):
        order = self.env['sale.order'].create({
            'partner_id': self.partner_a.id, **headers,
            'order_line': [
                Command.create({'display_type': 'line_section', 'name': 'Section', 'sequence': 1}),
                Command.create({
                    'product_id': self.product_a.id, 'product_uom_qty': 2,
                    'price_unit': 100, 'tax_id': [Command.clear()], 'sequence': 2,
                }),
                Command.create({
                    'product_id': self.product_a.id, 'product_uom_qty': 1,
                    'price_unit': 200, 'tax_id': [Command.clear()], 'sequence': 3,
                }),
                Command.create({'display_type': 'line_note', 'name': 'Note', 'sequence': 4}),
            ],
        })
        order.action_confirm()
        return order

    def test_sale_to_invoice_full_partial_and_empty_headers(self):
        for headers in (
            self.headers,
            {'project_account_id': self.headers['project_account_id']},
            {},
        ):
            with self.subTest(headers=headers):
                order = self._create_invoicable_order(headers)
                invoice = order._create_invoices()
                self.assertEqual(len(invoice), 1)
                self.assertEqual(invoice.move_type, 'out_invoice')
                self.assertEqual(invoice.state, 'draft')
                for field_name in self.headers:
                    self.assertEqual(invoice[field_name], order[field_name])
                self._assert_invoice_distribution(invoice, headers)
                self.assertEqual(len(invoice.invoice_line_ids.filtered(
                    lambda line: line.display_type == 'product'
                )), 2)
                display_lines = invoice.invoice_line_ids.filtered(
                    lambda line: line.display_type in ('line_section', 'line_note')
                )
                self.assertEqual(len(display_lines), 2)
                self.assertTrue(all(not line.analytic_distribution for line in display_lines))

    def test_invoice_edit_is_independent_of_sale(self):
        for headers in (self.headers, {}):
            with self.subTest(headers=headers):
                order = self._create_invoicable_order(headers)
                original = {line.id: line.analytic_distribution for line in order.order_line}
                invoice = order._create_invoices()
                invoice.write(self.headers)
                invoice.write({'project_account_id': self.replacement_project.id})
                self._assert_invoice_distribution(invoice, dict(
                    self.headers, project_account_id=self.replacement_project.id
                ))
                invoice.write({'budget_account_id': False})
                invoice.write(dict.fromkeys(self.headers, False))
                self._assert_invoice_distribution(invoice, {})
                for field_name in self.headers:
                    self.assertEqual(order[field_name].id, headers.get(field_name, False))
                for line in order.order_line:
                    self.assertEqual(line.analytic_distribution, original[line.id])

    def test_grouping_keeps_different_headers_separate(self):
        first = self._create_invoicable_order(self.headers)
        same = self._create_invoicable_order(self.headers)
        different = self._create_invoicable_order(dict(
            self.headers, project_account_id=self.replacement_project.id
        ))
        empty = self._create_invoicable_order({})
        invoices = (first | same | different | empty)._create_invoices()
        self.assertEqual(len(invoices), 3)
        self.assertEqual(first.invoice_ids, same.invoice_ids)
        self.assertNotEqual(first.invoice_ids, different.invoice_ids)
        self._assert_invoice_distribution(first.invoice_ids, self.headers)
        self._assert_invoice_distribution(different.invoice_ids, dict(
            self.headers, project_account_id=self.replacement_project.id
        ))
        self._assert_invoice_distribution(empty.invoice_ids, {})
