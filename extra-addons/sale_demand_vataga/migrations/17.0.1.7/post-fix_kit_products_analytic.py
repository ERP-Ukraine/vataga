from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    products_analytic = env['product.analytic'].search([])
    for kit_bom_id in products_analytic.kit_bom_ids:
        kit_bom_products_analytic = products_analytic.filtered(lambda p_a: kit_bom_id.id in p_a.kit_bom_ids.ids)
        (
            kit_bom_id.product_id + kit_bom_id.product_tmpl_id.product_variant_ids
        ).account_analytic_ids = kit_bom_products_analytic.mapped('sale_contract_id')
