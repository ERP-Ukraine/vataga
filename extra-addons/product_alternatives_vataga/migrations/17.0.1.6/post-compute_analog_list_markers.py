from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    analog_lines = env['product.analog'].search([])
    templates = (
        analog_lines.mapped('product_tmpl_id')
        | analog_lines.mapped('product_id.product_tmpl_id')
    )
    products = (
        analog_lines.mapped('product_id')
        | analog_lines.mapped('product_tmpl_id.product_variant_ids')
    )
    templates._compute_analog_list_marker()
    products._compute_analog_list_marker()
