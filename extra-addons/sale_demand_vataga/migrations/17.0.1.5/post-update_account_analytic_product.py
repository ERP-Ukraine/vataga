from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    products = env['product.product'].search([])
    for product in products:
        product.account_analytic_ids = product.product_analytic_ids.mapped(
            'sale_contract_id'
        )
