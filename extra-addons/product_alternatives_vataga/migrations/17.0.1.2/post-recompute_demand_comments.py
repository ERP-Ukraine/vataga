from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    primary_lines = env['product.analog'].search([('is_primary_link', '=', True)])
    products = (
        primary_lines.product_id
        | primary_lines.mapped('product_tmpl_id.product_variant_ids')
    )
    if not products:
        return

    product_analytics = env['product.analytic'].search(
        [('product_id', 'in', products.ids)]
    )
    product_analytics._compute_demand_comment()
