from odoo import _, api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.depends(
        'account_id', 'partner_id', 'product_id',
        'move_id.project_account_id', 'move_id.budget_account_id',
        'move_id.cash_flow_item_account_id', 'move_id.seller_contract_id'
    )
    def _compute_analytic_distribution(self):
        return super()._compute_analytic_distribution()
