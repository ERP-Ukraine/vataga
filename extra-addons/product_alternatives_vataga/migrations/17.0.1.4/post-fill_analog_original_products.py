from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    purchase_lines = env['purchase.order.line'].search(
        [
            ('product_id', '!=', False),
            ('analog_original_product_id', '=', False),
        ]
    )
    for line in purchase_lines:
        original_product = line.product_id._get_single_primary_analog_main_product()
        if original_product:
            line.with_context(
                product_alternatives_skip_analog_origin_sync=True
            ).write({'analog_original_product_id': original_product.id})

    move_lines = env['account.move.line'].search(
        [
            ('product_id', '!=', False),
            ('analog_original_product_id', '=', False),
        ]
    )
    for line in move_lines:
        original_product = env['product.product']
        purchase_lines = line._get_purchase_lines_for_analog_origin()
        purchase_line_origins = purchase_lines.mapped('analog_original_product_id')
        if len(purchase_line_origins) == 1:
            original_product = purchase_line_origins
        else:
            original_product = line.product_id._get_single_primary_analog_main_product()
        if original_product:
            line.with_context(
                product_alternatives_skip_analog_origin_sync=True
            ).write({'analog_original_product_id': original_product.id})
