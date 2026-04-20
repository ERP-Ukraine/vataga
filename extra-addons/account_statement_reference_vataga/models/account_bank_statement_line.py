from odoo import fields, models


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    linked_payment_ref = fields.Char(
        string='Референс платежу',
        readonly=True,
        copy=False,
    )
    linked_invoice_ref = fields.Char(
        string='Референс рахунку',
        readonly=True,
        copy=False,
    )

    @staticmethod
    def _join_reference_names(records):
        names = [name for name in records.mapped('name') if name]
        return ', '.join(dict.fromkeys(names)) or False

    def _get_reconciled_counterpart_lines(self):
        self.ensure_one()
        _liquidity_lines, suspense_lines, _other_lines = self._seek_for_lines()
        return (
            suspense_lines.mapped('matched_debit_ids.debit_move_id')
            + suspense_lines.mapped('matched_credit_ids.credit_move_id')
        )

    def _get_linked_reference_values(self):
        self.ensure_one()
        counterpart_lines = self._get_reconciled_counterpart_lines()
        payments = counterpart_lines.move_id.payment_id
        invoices = counterpart_lines.move_id.filtered(
            lambda move: move.is_invoice(include_receipts=True)
        ) | payments.move_id._get_reconciled_invoices()

        return {
            'linked_payment_ref': self._join_reference_names(payments),
            'linked_invoice_ref': self._join_reference_names(invoices),
        }

    def _update_linked_references(self):
        for st_line in self:
            values = st_line._get_linked_reference_values()
            if (
                st_line.linked_payment_ref != values['linked_payment_ref']
                or st_line.linked_invoice_ref != values['linked_invoice_ref']
            ):
                st_line.write(values)
