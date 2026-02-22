from odoo import api, fields, models


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'

    unit_quantity_amount = fields.Float(compute="_compute_unit_quantity_amount", store=True)

    @api.depends('unit_amount', 'product_uom_id', 'product_id.product_tmpl_id.uom_id')
    def _compute_unit_quantity_amount(self):
        for line in self:
            line.unit_quantity_amount = line.product_uom_id._compute_quantity(line.unit_amount, line.product_id.product_tmpl_id.uom_id)
