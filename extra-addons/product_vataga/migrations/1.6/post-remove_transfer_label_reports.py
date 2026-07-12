from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    env['ir.actions.report'].sudo().search([
        ('report_name', '=', 'product_vataga.report_stock_picking_transfer_label_v2'),
    ]).unlink()
    env['ir.ui.view'].sudo().search([
        ('key', '=', 'product_vataga.report_stock_picking_transfer_label_v2'),
    ]).unlink()
    env['ir.model.data'].sudo().search([
        ('module', '=', 'product_vataga'),
        (
            'name',
            'in',
            [
                'action_report_stock_picking_transfer_labels_v2',
                'report_stock_picking_transfer_label_v2',
            ],
        ),
    ]).unlink()
