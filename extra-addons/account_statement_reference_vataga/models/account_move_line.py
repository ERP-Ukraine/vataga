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

    def _get_statement_lines_to_sync_references(self):
        moves = self.move_id
        return (
            self.mapped('statement_line_id')
            | moves._get_reconciled_statement_lines()
            | moves.payment_id.reconciled_statement_line_ids
        )

    def reconcile(self):
        statement_lines = self._get_statement_lines_to_sync_references()
        result = super().reconcile()
        (
            statement_lines | self._get_statement_lines_to_sync_references()
        )._sync_payment_invoice_references()
        return result

    def remove_move_reconcile(self):
        statement_lines = self._get_statement_lines_to_sync_references()
        result = super().remove_move_reconcile()
        (
            statement_lines | self._get_statement_lines_to_sync_references()
        )._sync_payment_invoice_references()
        return result
