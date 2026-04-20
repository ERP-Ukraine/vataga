from odoo import models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _get_statement_lines_to_sync_reference(self):
        return self.mapped('statement_line_id') | self.move_id.payment_id.reconciled_statement_line_ids

    def reconcile(self):
        statement_lines = self._get_statement_lines_to_sync_reference()
        result = super().reconcile()
        (
            statement_lines | self._get_statement_lines_to_sync_reference()
        )._update_linked_references()
        return result

    def remove_move_reconcile(self):
        statement_lines = self._get_statement_lines_to_sync_reference()
        result = super().remove_move_reconcile()
        (
            statement_lines | self._get_statement_lines_to_sync_reference()
        )._update_linked_references()
        return result
