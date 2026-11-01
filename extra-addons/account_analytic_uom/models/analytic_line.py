from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    unit_quantity_amount = fields.Float(compute="_compute_unit_quantity_amount", store=True)
    unit_quantity_applicable = fields.Boolean(
        compute='_compute_unit_quantity_applicable',
        string='Product Quantity Applicable',
        help='Whether the analytic quantity is expressed in the product UoM category. '
             'Manufacturing time in hours is not a quantity of finished products.')

    @api.depends('product_uom_id.category_id', 'product_id.product_tmpl_id.uom_id.category_id')
    def _compute_unit_quantity_applicable(self):
        for line in self:
            line.unit_quantity_applicable = bool(
                line.product_id and line.product_uom_id
                and line.product_uom_id.category_id == line.product_id.uom_id.category_id)

    @api.depends('unit_amount', 'product_uom_id', 'product_uom_id.category_id',
                 'product_id.product_tmpl_id.uom_id', 'product_id.product_tmpl_id.uom_id.category_id', 'category')
    def _compute_unit_quantity_amount(self):
        time_category = self.env.ref('uom.uom_categ_wtime')
        for line in self:
            if (line.category == 'manufacturing_order'
                    and line.product_uom_id.category_id == time_category
                    and line.product_id.uom_id.category_id != time_category):
                # Native MRP records WC/employee HOURS against the finished
                # product. This reporting measure counts product units only:
                # time is not convertible to pieces and contributes no units.
                # unit_quantity_applicable=False distinguishes N/A from an
                # actual zero product quantity. Preserve amount/unit_amount,
                # product and native UoM; never suppress a conversion error
                # for a line that actually represents product quantities.
                line.unit_quantity_amount = 0.0
                continue
            line.unit_quantity_amount = line.product_uom_id._compute_quantity(line.unit_amount, line.product_id.product_tmpl_id.uom_id)
