from odoo import Command
from odoo.tests import Form, tagged

from .common import InvoiceHeaderAnalyticsCommon


@tagged('post_install', '-at_install')
class TestInvoiceHeaderAnalytics(InvoiceHeaderAnalyticsCommon):
    def test_draft_headers_change_and_clear(self):
        for move_type in ('out_invoice', 'in_invoice'):
            with self.subTest(move_type=move_type):
                invoice = self._create_header_invoice(
                    move_type=move_type, invoice_line_ids=[
                        Command.create(self._invoice_line_vals()),
                        Command.create(self._invoice_line_vals(analytic_distribution={
                            str(self.replacement_project.id): 100,
                        })),
                        Command.create({'display_type': 'line_section', 'name': 'Section'}),
                        Command.create({'display_type': 'line_note', 'name': 'Note'}),
                    ],
                )
                self.assertEqual(invoice.state, 'draft')
                self._assert_invoice_distribution(invoice, self.headers)
                headers = dict(self.headers, project_account_id=self.replacement_project.id)
                invoice.write({'project_account_id': self.replacement_project.id})
                self._assert_invoice_distribution(invoice, headers)
                invoice.write({'budget_account_id': False})
                headers['budget_account_id'] = False
                self._assert_invoice_distribution(invoice, headers)
                invoice.write(dict.fromkeys(self.headers, False))
                self._assert_invoice_distribution(invoice, {})
                for line in invoice.invoice_line_ids.filtered(
                    lambda line: line.display_type in ('line_section', 'line_note')
                ):
                    self.assertFalse(line.analytic_distribution)
                for line in invoice.line_ids - invoice.invoice_line_ids:
                    self.assertFalse(line.analytic_distribution)

    def test_distribution_model_without_headers_and_header_priority(self):
        model_distribution = {str(self.replacement_project.id): 100}
        self.env['account.analytic.distribution.model'].create({
            'partner_id': self.partner_a.id,
            'analytic_distribution': model_distribution,
        })
        invoice = self._create_header_invoice(headers={})
        self.assertEqual(invoice.invoice_line_ids.analytic_distribution, model_distribution)
        # Empty, unused headers must not clear a standard distribution on RPC writes.
        invoice.write(dict.fromkeys(self.headers, False))
        self.assertEqual(invoice.invoice_line_ids.analytic_distribution, model_distribution)
        invoice.write(self.headers)
        self._assert_invoice_distribution(invoice, self.headers)
        invoice.write(dict.fromkeys(self.headers, False))
        self._assert_invoice_distribution(invoice, {})
        # A later normal recompute once headers are unused follows standard Odoo again.
        invoice.invoice_line_ids._compute_analytic_distribution()
        self.assertEqual(invoice.invoice_line_ids.analytic_distribution, model_distribution)

    def test_manual_distribution_without_headers_is_preserved(self):
        distribution = {str(self.replacement_project.id): 100}
        invoice = self._create_header_invoice(headers={}, invoice_line_ids=[
            Command.create(self._invoice_line_vals(analytic_distribution=distribution)),
        ])
        invoice.write({'ref': 'Unrelated change'})
        self.assertEqual(invoice.invoice_line_ids.analytic_distribution, distribution)

    def test_posted_invoice_can_start_using_headers(self):
        invoice = self._create_header_invoice(headers={})
        invoice.action_post()
        self.assertFalse(invoice.invoice_line_ids.analytic_line_ids)
        invoice.write(self.headers)
        self._assert_invoice_distribution(invoice, self.headers)
        self.assertTrue(invoice.invoice_line_ids.analytic_line_ids)

    def test_posted_headers_rebuild_analytic_items(self):
        for move_type in ('out_invoice', 'in_invoice'):
            with self.subTest(move_type=move_type):
                invoice = self._create_header_invoice(move_type=move_type)
                invoice.action_post()
                line = invoice.invoice_line_ids
                self.assertTrue(line.analytic_line_ids)
                old_items = line.analytic_line_ids
                invoice.write({'project_account_id': self.replacement_project.id})
                headers = dict(self.headers, project_account_id=self.replacement_project.id)
                self._assert_invoice_distribution(invoice, headers)
                self.assertFalse(old_items.exists())
                self.assertEqual(len(line.analytic_line_ids), 1)
                for account_id in headers.values():
                    account = self.env['account.analytic.account'].browse(account_id)
                    self.assertEqual(
                        line.analytic_line_ids[account.plan_id._column_name()], account
                    )
                self.assertAlmostEqual(line.analytic_line_ids.amount, -line.balance)
                invoice.write({'budget_account_id': False})
                budget = self.env['account.analytic.account'].browse(headers['budget_account_id'])
                self.assertFalse(line.analytic_line_ids[budget.plan_id._column_name()])
                invoice.write(dict.fromkeys(self.headers, False))
                self._assert_invoice_distribution(invoice, {})
                self.assertFalse(line.analytic_line_ids)
                self.assertEqual(invoice.state, 'posted')
                self.assertEqual(invoice.amount_total, 200)

    def test_form_header_clear_before_save(self):
        self.env.user.groups_id |= self.env.ref('analytic.group_analytic_accounting')
        invoice = self._create_header_invoice()
        with Form(invoice) as form:
            form.project_account_id = self.replacement_project
            with form.invoice_line_ids.edit(0) as line:
                self.assertEqual(line.analytic_distribution, self._distribution(
                    dict(self.headers, project_account_id=self.replacement_project.id)
                ))
            for field_name in self.headers:
                setattr(form, field_name, self.env['account.analytic.account'])
            with form.invoice_line_ids.edit(0) as line:
                self.assertFalse(line.analytic_distribution)
        self._assert_invoice_distribution(invoice, {})

    def test_new_line_and_batch_header_write(self):
        first = self._create_header_invoice()
        second = self._create_header_invoice(headers={
            'seller_contract_id': self.headers['seller_contract_id'],
        })
        first.write({'invoice_line_ids': [Command.create(self._invoice_line_vals())]})
        self.assertEqual(len(first.invoice_line_ids), 2)
        self._assert_invoice_distribution(first, self.headers)
        (first | second).write({'project_account_id': self.replacement_project.id})
        self._assert_invoice_distribution(first, dict(
            self.headers, project_account_id=self.replacement_project.id
        ))
        self._assert_invoice_distribution(second, {
            'project_account_id': self.replacement_project.id,
            'seller_contract_id': self.headers['seller_contract_id'],
        })
