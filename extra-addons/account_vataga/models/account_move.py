from odoo import _, api, fields, models
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

    def _has_analytic_header(self):
        self.ensure_one()
        return any(self[field_name] for field_name in self.ANALYTIC_HEADER_FIELDS)

    @api.onchange(
        'project_account_id', 'budget_account_id',
        'cash_flow_item_account_id', 'seller_contract_id',
    )
    def _onchange_analytic_header(self):
        for move in self:
            if not move._has_analytic_header():
                # A header edit ending with no accounts is an explicit clear,
                # unlike an ordinary compute on an invoice without headers.
                move.invoice_line_ids.filtered(
                    lambda line: line.display_type == 'product'
                ).update({'analytic_distribution': False})

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        # Explicit distributions in incoming line values bypass precompute.
        moves.filtered(lambda move: move._has_analytic_header()).invoice_line_ids.filtered(
            lambda line: line.display_type == 'product'
        )._compute_analytic_distribution()
        return moves

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
        header_changed = bool(set(vals) & self.ANALYTIC_HEADER_FIELDS)
        moves_with_header = self.env['account.move']
        if header_changed:
            moves_with_header = self.filtered(lambda move: move._has_analytic_header())
            posted_invoice_lines = self.filtered(
                lambda move: move.state == 'posted'
                and move.is_invoice(include_receipts=True)
            ).invoice_line_ids.filtered(lambda line: line.display_type == 'product')

        res = super().write(vals)

        if header_changed:
            self.filtered(lambda move: move._has_analytic_header()).invoice_line_ids.filtered(
                lambda line: line.display_type == 'product'
            )._compute_analytic_distribution()
            moves_with_header.filtered(
                lambda move: not move._has_analytic_header()
            ).invoice_line_ids.filtered(
                lambda line: line.display_type == 'product'
            ).write({'analytic_distribution': False})

        if posted_invoice_lines:
            posted_invoice_lines._inverse_analytic_distribution()

        return res
