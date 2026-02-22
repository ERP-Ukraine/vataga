from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountAnalyticPlan(models.Model):
    _inherit = 'account.analytic.plan'

    @api.ondelete(at_uninstall=False)
    def _no_deleting_special_plans(self):
        reserved_plans = []
        plan_project = self.env.ref('analytic_vataga.account_analytic_plan_project', raise_if_not_found=False)
        if plan_project:
            reserved_plans.append(plan_project.id)
        plan_budget = self.env.ref('analytic_vataga.account_analytic_plan_budget', raise_if_not_found=False)
        if plan_budget:
            reserved_plans.append(plan_budget.id)
        plan_cash_flow_item = self.env.ref('analytic_vataga.account_analytic_plan_cash_flow_item', raise_if_not_found=False)
        if plan_cash_flow_item:
            reserved_plans.append(plan_cash_flow_item.id)
        plan_seller_contract = self.env.ref('analytic_vataga.account_analytic_plan_seller_contract', raise_if_not_found=False)
        if plan_project:
            reserved_plans.append(plan_seller_contract.id)

        for plan in self:
            if plan.id in reserved_plans:
                raise UserError(_(
                    'This analytic plan has special status and can not be deleted'
                ))
