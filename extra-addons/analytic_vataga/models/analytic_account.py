from odoo import api, fields, models, _


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'
    _rec_names_search = ['name', 'code', 'display_name']

    is_plan_project = fields.Boolean(compute='_compute_analytic_plan_subdivision', store=True, compute_sudo=True)
    is_plan_budget = fields.Boolean(compute='_compute_analytic_plan_subdivision', store=True, compute_sudo=True)
    is_plan_cash_flow_item = fields.Boolean(compute='_compute_analytic_plan_subdivision', store=True, compute_sudo=True)
    is_plan_seller_contract = fields.Boolean(compute='_compute_analytic_plan_subdivision', store=True, compute_sudo=True)
    display_name = fields.Char(store=True)

    @api.depends('plan_id')
    def _compute_analytic_plan_subdivision(self):
        plan_project = self.env.ref('analytic_vataga.account_analytic_plan_project', raise_if_not_found=False)
        plan_project_id = plan_project.id if plan_project else 0
        plan_budget = self.env.ref('analytic_vataga.account_analytic_plan_budget', raise_if_not_found=False)
        plan_budget_id = plan_budget.id if plan_budget else 0
        plan_cash_flow_item = self.env.ref('analytic_vataga.account_analytic_plan_cash_flow_item', raise_if_not_found=False)
        plan_cash_flow_item_id = plan_cash_flow_item.id if plan_cash_flow_item else 0
        plan_seller_contract = self.env.ref('analytic_vataga.account_analytic_plan_seller_contract', raise_if_not_found=False)
        plan_seller_contract_id = plan_seller_contract.id if plan_seller_contract else 0
        self.is_plan_project = False
        self.is_plan_budget = False
        self.is_plan_cash_flow_item = False
        self.is_plan_seller_contract = False
        for account in self:
            if not account.plan_id:
                continue
            account.is_plan_project = account.plan_id.id == plan_project_id
            account.is_plan_budget = account.plan_id.id == plan_budget_id
            account.is_plan_cash_flow_item = account.plan_id.id == plan_cash_flow_item_id
            account.is_plan_seller_contract = account.plan_id.id == plan_seller_contract_id
