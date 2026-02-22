from odoo import fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    seller_purchase_line_ids = fields.One2many('purchase.order.line', 'seller_contract_id')
