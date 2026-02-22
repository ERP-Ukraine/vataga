from odoo import models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    def write(self, vals):
        res = super().write(vals)
        if vals.get('name'):
            product_ids = [
                el['id'][0]
                for el in self.env['product.product'].read_group(
                    [('categ_id', 'in', self.ids)], ['id'], ['id']
                )
            ]
            self.env['product.analytic'].search(
                [('product_id', 'in', product_ids)]
            )._update_full_name_translations()
        return res
