from odoo import api, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    @api.depends(
        'account_id', 'partner_id', 'product_id',
        'move_id.project_account_id', 'move_id.budget_account_id',
        'move_id.cash_flow_item_account_id', 'move_id.seller_contract_id'
    )
    def _compute_analytic_distribution(self):
        for line in self:
            if line.display_type in ('line_section', 'line_note'):
                continue
            if line.display_type == 'product' or not line.move_id.is_invoice(include_receipts=True):
                set_analytic_accounts = {
                    str(line.move_id[field_name].id)
                    for field_name in line.move_id.ANALYTIC_HEADER_FIELDS
                    if line.move_id[field_name]
                }
                if set_analytic_accounts:
                    ids_sts = ','.join(sorted(set_analytic_accounts))
                    line.analytic_distribution = {ids_sts: 100}
                    continue
            super(AccountMoveLine, line)._compute_analytic_distribution()
