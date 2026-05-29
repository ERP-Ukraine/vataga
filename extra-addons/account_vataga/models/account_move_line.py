from odoo import _, api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    vataga_locked_analytic_plan_ids = fields.Json(
        compute='_compute_vataga_locked_analytic_plan_ids',
    )

    @api.depends('move_id.state')
    def _compute_vataga_locked_analytic_plan_ids(self):
        project_plan = self.env.ref(
            'analytic_vataga.account_analytic_plan_project',
            raise_if_not_found=False,
        )
        seller_contract_plan = self.env.ref(
            'analytic_vataga.account_analytic_plan_seller_contract',
            raise_if_not_found=False,
        )
        locked_plan_ids = [
            plan.id
            for plan in (project_plan, seller_contract_plan)
            if plan
        ]
        for line in self:
            line.vataga_locked_analytic_plan_ids = (
                locked_plan_ids if line.move_id.state != 'draft' else []
            )

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
