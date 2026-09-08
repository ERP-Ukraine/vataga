from odoo import _, fields, models
from odoo.exceptions import AccessError


class AccountMove(models.Model):
    _inherit = 'account.move'

    ANALYTIC_HEADER_FIELDS = {
        'project_account_id',
        'budget_account_id',
        'cash_flow_item_account_id',
        'seller_contract_id',
    }

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

    def js_remove_outstanding_partial(self, partial_id):
        if not self.env.user.has_group(
            'account_vataga.group_account_payment_unreconcile'
        ):
            raise AccessError(_(
                "У вас немає прав для відв'язки платежів від рахунків."
            ))
        return super().js_remove_outstanding_partial(partial_id)

    def write(self, vals):
        posted_invoice_lines = self.env['account.move.line']
        if set(vals) & self.ANALYTIC_HEADER_FIELDS:
            posted_invoice_lines = self.filtered(
                lambda move: move.state == 'posted'
                and move.is_invoice(include_receipts=True)
            ).invoice_line_ids

        res = super().write(vals)

        if posted_invoice_lines:
            posted_invoice_lines._inverse_analytic_distribution()

        return res
