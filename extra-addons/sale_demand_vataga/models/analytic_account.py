from odoo import fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    seller_analytic_comment = fields.Char(default='')
    seller_move_line_ids = fields.One2many('account.move.line', 'seller_contract_id')
    product_analytic_ids = fields.One2many('product.analytic', 'sale_contract_id')
    seller_purchase_ids = fields.One2many('purchase.order', 'seller_contract_id')
