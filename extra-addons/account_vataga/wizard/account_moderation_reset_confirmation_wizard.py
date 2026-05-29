from odoo import fields, models


class AccountModerationResetConfirmationWizard(models.TransientModel):
    _name = 'account.moderation.reset.confirmation.wizard'
    _description = 'Confirm moderation reset before draft'

    move_ids = fields.Many2many('account.move', required=True)

    def action_confirm(self):
        result = self.move_ids.with_context(
            skip_moderation_reset_confirmation=True,
        ).button_draft()
        if isinstance(result, dict):
            return result
        return {'type': 'ir.actions.client', 'tag': 'reload'}
