from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    analog_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_tmpl_id',
        string='Analogs',
    )


class ProductAnalog(models.Model):
    _name = 'product.analog'
    _description = 'Product Analog'
    _order = 'sequence, id'
    _sql_constraints = [
        (
            'product_tmpl_product_uniq',
            'unique(product_tmpl_id, product_id)',
            'This analog is already linked to the product.',
        ),
    ]

    sequence = fields.Integer(default=10)
    product_tmpl_id = fields.Many2one(
        comodel_name='product.template',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Product',
        required=True,
        ondelete='cascade',
    )
    description = fields.Char(
        string='Description',
        related='product_id.name',
        readonly=True,
    )
    uom_id = fields.Many2one(
        comodel_name='uom.uom',
        string='Unit of Measure',
        related='product_id.uom_id',
        readonly=True,
    )

    @api.constrains('product_tmpl_id', 'product_id')
    def _check_product_id(self):
        for line in self:
            if not line.product_tmpl_id or not line.product_id:
                continue
            if line.product_id.product_tmpl_id == line.product_tmpl_id:
                raise ValidationError(
                    _('A product cannot be selected as its own analog.')
                )
            if line.product_id.uom_id != line.product_tmpl_id.uom_id:
                raise ValidationError(
                    _(
                        'The analog "%(product)s" must use the same unit of measure '
                        'as the main product "%(main_product)s".'
                    )
                    % {
                        'product': line.product_id.display_name,
                        'main_product': line.product_tmpl_id.display_name,
                    }
                )
