from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    analog_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        compute='_compute_analog_uom_id',
        store=True,
        string='Analog Unit of Measure',
    )
    analog_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_tmpl_id',
        string='Analogs',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id


class ProductProduct(models.Model):
    _inherit = 'product.product'

    analog_source_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_id',
        string='Used as Analog For',
    )
    analog_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        compute='_compute_analog_uom_id',
        store=True,
        string='Analog Unit of Measure',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id


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

    def _recompute_product_analytic_demand_comments(self, products):
        if products:
            self.env['product.analytic'].search(
                [('product_id', 'in', products.ids)]
            )._compute_demand_comment()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._recompute_product_analytic_demand_comments(records.product_id)
        return records

    def write(self, vals):
        products = self.product_id
        res = super().write(vals)
        if 'product_id' in vals:
            products |= self.product_id
        self._recompute_product_analytic_demand_comments(products)
        return res

    def unlink(self):
        products = self.product_id
        res = super().unlink()
        self._recompute_product_analytic_demand_comments(products)
        return res


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    demand_comment = fields.Char(
        compute='_compute_demand_comment',
        store=True,
        group_operator='max',
        string='Comment',
    )

    @api.depends(
        'comment',
        'product_id',
        'product_id.analog_source_line_ids',
    )
    def _compute_demand_comment(self):
        for product_analytic in self:
            comment = product_analytic.comment or ''
            if product_analytic.product_id.analog_source_line_ids:
                product_analytic.demand_comment = (
                    f'{comment} (A)' if comment else '(A)'
                )
            else:
                product_analytic.demand_comment = comment


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    analog_marker = fields.Text(
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_product_names = fields.Text(
        compute='_compute_analog_products',
        string='Analog Products',
    )

    @api.depends(
        'bom_line_ids.product_id',
        'bom_line_ids.product_id.product_tmpl_id.analog_line_ids.product_id',
    )
    def _compute_analog_products(self):
        for bom in self:
            analog_pairs = []
            for line in bom.bom_line_ids:
                analog_pairs.extend(line._get_analog_product_pairs())
            analog_product_names = '\n'.join(
                f'{component}\t{analog}'
                for component, analog in analog_pairs
            )
            bom.analog_marker = f'(A)\n{analog_product_names}' if analog_pairs else ''
            bom.analog_product_names = analog_product_names

    def get_analog_product_names(self):
        self.ensure_one()
        return self.analog_product_names.split('\n') if self.analog_product_names else []


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    analog_product_ids = fields.Many2many(
        comodel_name='product.product',
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_marker = fields.Text(
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_product_names = fields.Text(
        compute='_compute_analog_products',
        string='Analog Products',
    )

    @api.depends('product_id', 'product_id.product_tmpl_id.analog_line_ids.product_id')
    def _compute_analog_products(self):
        for line in self:
            analog_products = line.product_id.product_tmpl_id.analog_line_ids.product_id
            analog_product_names = '\n'.join(
                f'{component}\t{analog}'
                for component, analog in line._get_analog_product_pairs()
            )
            line.analog_product_ids = analog_products
            line.analog_marker = (
                f'(A)\n{analog_product_names}' if analog_product_names else ''
            )
            line.analog_product_names = analog_product_names

    def _get_analog_product_pairs(self):
        self.ensure_one()
        component_name = self.product_id.display_name
        return [
            (component_name, analog.display_name)
            for analog in self.product_id.product_tmpl_id.analog_line_ids.product_id
        ]

    def get_analog_product_names(self):
        self.ensure_one()
        return self.analog_product_names.split('\n') if self.analog_product_names else []
