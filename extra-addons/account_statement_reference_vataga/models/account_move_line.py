from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    statement_payment_reference = fields.Char(
        related='statement_line_id.payment_reference',
        string='Референс платежу',
        readonly=True,
    )
    statement_invoice_reference = fields.Char(
        related='statement_line_id.invoice_reference',
        string='Референс рахунку',
        readonly=True,
    )
