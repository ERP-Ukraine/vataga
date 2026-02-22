from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

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

    def _prepare_invoice(self):
        self.ensure_one()
        vals = super()._prepare_invoice()
        self = self.sudo()
        if self.project_account_id:
            vals['project_account_id'] = self.project_account_id.id
        if self.budget_account_id:
            vals['budget_account_id'] = self.budget_account_id.id
        if self.cash_flow_item_account_id:
            vals['cash_flow_item_account_id'] = self.cash_flow_item_account_id.id
        if self.seller_contract_id:
            vals['seller_contract_id'] = self.seller_contract_id.id

        return vals
