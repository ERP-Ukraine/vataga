from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    move_lines = env['account.move.line'].search([('seller_contract_id', '!=', False)])
    if move_lines:
        seller_contracts = move_lines.mapped('seller_contract_id')
        product_analytics = env['product.analytic'].search(
            [('sale_contract_id', 'in', seller_contracts.ids)]
        )
        for product_analytic in product_analytics:
            moves = move_lines.filtered(
                lambda line: line.seller_contract_id
                == product_analytic.sale_contract_id
                and line.product_id == product_analytic.product_id
            ).mapped('move_id')
            product_analytic.account_move_ids = moves
