import logging

from odoo import models


_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_studio_boolean_fields_to_reset(self):
        fields_to_reset = self.env['ir.model.fields'].sudo().search([
            ('model', '=', 'account.move'),
            ('ttype', '=', 'boolean'),
            ('state', '=', 'manual'),
            ('name', '=like', 'x_studio_%'),
        ])
        return [
            field.name
            for field in fields_to_reset
            if field.name in self._fields
        ]

    def button_draft(self):
        posted_invoice_moves = self.filtered(
            lambda move: move.state == 'posted' and move.move_type == 'in_invoice'
        )

        res = super().button_draft()

        fields_to_reset = self._get_studio_boolean_fields_to_reset()
        if posted_invoice_moves and fields_to_reset:
            _logger.info("Resetting Studio boolean fields on posted vendor bills: %s", fields_to_reset)
            posted_invoice_moves.write({
                field_name: False
                for field_name in fields_to_reset
            })

        return res
