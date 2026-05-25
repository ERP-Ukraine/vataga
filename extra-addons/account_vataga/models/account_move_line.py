from odoo import _, api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.depends(
        'account_id', 'partner_id', 'product_id',
        'move_id.project_account_id', 'move_id.budget_account_id',
        'move_id.cash_flow_item_account_id', 'move_id.seller_contract_id'
    )
    def _compute_analytic_distribution(self):
        for line in self:
            if line.display_type == 'product' or not line.move_id.is_invoice(include_receipts=True):
                set_analytic_accounts = [
                    str(account.id) for account in [
                        line.move_id.project_account_id,
                        line.move_id.budget_account_id,
                        line.move_id.cash_flow_item_account_id,
                        line.move_id.seller_contract_id
                    ] if account]
                if set_analytic_accounts:
                    ids_sts = ','.join(sorted(set_analytic_accounts))
                    line.analytic_distribution = {ids_sts: 100}
            else:
                super(AccountMoveLine, line)._compute_analytic_distribution()
