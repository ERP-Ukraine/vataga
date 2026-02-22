from odoo import models, fields, api


class AccountPayment(models.Model):
    _inherit = "account.payment"

    purpose_pumb = fields.Char(readonly=True)
    purpose_dcu = fields.Char(readonly=True)
    exported_payment = fields.Boolean(
        tracking=True,
        default=False,
    )

    bank_payment_date = fields.Date(
        string='Payment Date',
        compute='_compute_bank_payment_date',
        store=False,
        help='Date from the bank statement line that is reconciled with this payment'
    )

    @api.depends(
        'reconciled_statement_line_ids',
    )
    def _compute_bank_payment_date(self):
        for payment in self:
            if payment.reconciled_statement_line_ids:
                payment.bank_payment_date = max(payment.reconciled_statement_line_ids.mapped('date'))
            else:
                payment.bank_payment_date = False
