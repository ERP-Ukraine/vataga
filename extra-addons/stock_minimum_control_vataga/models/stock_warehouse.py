from odoo import fields, models


class StockWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    minimum_stock_control = fields.Boolean(
        string='Виконувати контроль мінімальних залишків', default=False,
    )
