import math

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    minimum_stock_qty = fields.Float(
        string='Мінімальна кількість залишків на складі',
        default=0.0,
        digits='Product Unit of Measure',
    )

    @api.constrains('minimum_stock_qty')
    def _check_minimum_stock_qty(self):
        for product in self:
            if not math.isfinite(product.minimum_stock_qty) or product.minimum_stock_qty < 0:
                raise ValidationError(
                    'Мінімальна кількість залишків на складі не може бути від’ємною.'
                )
