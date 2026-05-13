from odoo import _, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    ANALYTIC_HEADER_FIELDS = {
        'project_account_id',
        'budget_account_id',
        'cash_flow_item_account_id',
        'seller_contract_id',
    }
    STUDIO_MODERATION_FIELDS = (
        'x_studio_boolean_field_507_likh7qd9',
        'x_studio_boolean_field_8co_ii153r8h',
        'x_studio_taras_ok',
    )

    project_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_project', '=', True)]"
    )
    budget_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_budget', '=', True)]"
    )
    cash_flow_item_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_cash_flow_item', '=', True)]"
    )
    seller_contract_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_seller_contract', '=', True)]"
    )

    def _get_studio_boolean_fields_to_reset(self):
        return [
            field_name
            for field_name in self.STUDIO_MODERATION_FIELDS
            if field_name in self._fields
        ]

    def write(self, vals):
        posted_invoice_lines = self.env['account.move.line']
        if set(vals) & self.ANALYTIC_HEADER_FIELDS:
            posted_invoice_lines = self.filtered(
                lambda move: move.state == 'posted' and move.is_invoice(include_receipts=True)
            ).invoice_line_ids

        res = super().write(vals)

        if posted_invoice_lines:
            posted_invoice_lines._inverse_analytic_distribution()

        return res

    def button_draft(self):
        posted_invoice_moves = self.filtered(
            lambda move: move.state == 'posted' and move.move_type == 'in_invoice'
        )

        res = super().button_draft()

        fields_to_reset = self._get_studio_boolean_fields_to_reset()
        if posted_invoice_moves and fields_to_reset:
            posted_invoice_moves.write({
                field_name: False
                for field_name in fields_to_reset
            })

        return res
