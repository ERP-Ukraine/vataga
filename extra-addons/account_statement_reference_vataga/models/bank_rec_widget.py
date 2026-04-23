from odoo import models


class BankRecWidget(models.Model):
    _inherit = 'bank.rec.widget'

    def _get_statement_lines_to_sync_references(self):
        return self.mapped('st_line_id')

    def validate(self):
        statement_lines = self._get_statement_lines_to_sync_references()
        result = super().validate()
        statement_lines._sync_payment_invoice_references()
        return result

    def reset(self):
        statement_lines = self._get_statement_lines_to_sync_references()
        result = super().reset()
        statement_lines._sync_payment_invoice_references()
        return result
