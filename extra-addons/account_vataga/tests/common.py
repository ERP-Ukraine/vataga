from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class InvoiceHeaderAnalyticsCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.headers = {}
        for field_name, plan_name in (
            ('project_account_id', 'project'),
            ('budget_account_id', 'budget'),
            ('cash_flow_item_account_id', 'cash_flow_item'),
            ('seller_contract_id', 'seller_contract'),
        ):
            cls.headers[field_name] = cls.env['account.analytic.account'].create({
                'name': f'Invoice header {plan_name}',
                'plan_id': cls.env.ref(
                    f'analytic_vataga.account_analytic_plan_{plan_name}'
                ).id,
            }).id
        cls.replacement_project = cls.env['account.analytic.account'].create({
            'name': 'Replacement invoice project',
            'plan_id': cls.env.ref('analytic_vataga.account_analytic_plan_project').id,
        })

    def _distribution(self, headers):
        ids = sorted({str(value) for value in headers.values() if value})
        return {','.join(ids): 100} if ids else False

    def _invoice_line_vals(self, **kwargs):
        return {
            'product_id': self.product_a.id, 'quantity': 2,
            'price_unit': 100, 'tax_ids': [Command.clear()], **kwargs,
        }

    def _create_header_invoice(self, headers=None, **kwargs):
        return self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner_a.id,
            'invoice_date': fields.Date.today(),
            **(self.headers if headers is None else headers),
            'invoice_line_ids': [Command.create(self._invoice_line_vals())],
            **kwargs,
        })

    def _assert_invoice_distribution(self, invoice, headers):
        self.env.flush_all()
        lines = invoice.invoice_line_ids.filtered(lambda line: line.display_type == 'product')
        self.assertTrue(lines)
        lines.invalidate_recordset(['analytic_distribution'])
        for line in lines:
            self.assertEqual(line.analytic_distribution or False, self._distribution(headers))
