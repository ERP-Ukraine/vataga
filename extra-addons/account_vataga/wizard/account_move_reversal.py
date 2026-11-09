from odoo import models


class AccountMoveReversal(models.TransientModel):
    _inherit = 'account.move.reversal'

    def _prepare_default_reversal(self, move):
        vals = super()._prepare_default_reversal(move)
        if move.move_type == 'in_invoice':
            vals['vataga_storno_bill_id'] = move.id
        return vals
