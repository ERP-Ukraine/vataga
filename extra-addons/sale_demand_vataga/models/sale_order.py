import math

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    deal_closed = fields.Boolean(
        'Deal closed', help='Everything is signed, paid, shipped and documented.'
    )


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    need_to_purchase_ids = fields.One2many('sale.order.line.purchase', 'order_line_id')
    bom_id = fields.Many2one('mrp.bom')

    def set_bom_id(self):
        for line in self:
            product_bom = line.product_id.variant_bom_ids.filtered(
                lambda bom: bom.company_id == line.order_id.company_id
            )
            product_template_bom = line.product_id.product_tmpl_id.bom_ids.filtered(
                lambda bom: bom.company_id == line.order_id.company_id
                and not bom.product_id
            )
            if product_bom:
                line.bom_id = product_bom[0]
            elif product_template_bom:
                line.bom_id = product_template_bom[0]
            else:
                line.bom_id = self.env['mrp.bom']

    @api.model_create_multi
    def create(self, vals_list):
        result = super().create(vals_list)
        result.set_bom_id()
        result.create_need_to_purchase_ids()
        return result

    def write(self, vals):
        res = super().write(vals)
        if vals.get('product_id') or vals.get('product_uom_qty'):
            self.set_bom_id()
            for line in self:
                line.create_need_to_purchase_ids()
        return res

    def create_need_to_purchase_ids(self):
        for line in self.filtered(lambda line: not line.display_type):
            if line.bom_id:
                products = line.bom_id.need_to_purchase_ids.mapped('product_id')
                line.need_to_purchase_ids.filtered(
                    lambda p_line: p_line.product_id.id not in products.ids
                ).unlink()
                for bom_line in line.bom_id.need_to_purchase_ids:
                    multiplier = math.ceil(
                        line.product_uom._compute_quantity(
                            line.product_uom_qty, line.bom_id.product_uom_id
                        )
                        / line.bom_id.product_qty
                    )
                    total_qty = (
                        multiplier
                        * line.bom_id.product_uom_id._compute_quantity(
                            bom_line.product_qty, line.product_id.uom_id
                        )
                    )
                    p_line = line.need_to_purchase_ids.filtered(
                        lambda p_line: p_line.product_id.id == bom_line.product_id.id
                    )
                    if p_line:
                        p_line.product_qty = total_qty
                    if not p_line:
                        line.env['sale.order.line.purchase'].create(
                            {
                                'order_line_id': line.id,
                                'product_id': bom_line.product_id.id,
                                'product_qty': total_qty,
                            }
                        )
            else:
                line.need_to_purchase_ids.filtered(
                    lambda p_line: p_line.product_id.id != line.product_id.id
                ).unlink()
                p_line = line.need_to_purchase_ids.filtered(
                    lambda p_line: p_line.product_id.id == line.product_id.id
                )
                qty = line.product_uom._compute_quantity(
                    line.product_uom_qty, line.product_id.uom_id
                )
                if p_line:
                    p_line.product_qty = qty
                else:
                    line.env['sale.order.line.purchase'].create(
                        {
                            'order_line_id': line.id,
                            'product_id': line.product_id.id,
                            'product_qty': qty,
                        }
                    )


class SaleOrderLinePurchase(models.Model):
    _name = 'sale.order.line.purchase'
    _description = 'Sale Order Line Purchase'

    order_line_id = fields.Many2one(
        'sale.order.line', required=True, ondelete='cascade'
    )
    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    product_qty = fields.Float(required=True)
    sale_contract_id = fields.Many2one(
        'account.analytic.account', compute='_compute_sale_contract_id', store=True
    )
    product_analytic_id = fields.Many2one(
        'product.analytic', compute='_compute_product_analytic_id', store=True
    )
    state = fields.Selection(related='order_line_id.order_id.state', store=True)

    @api.depends('order_line_id', 'order_line_id.analytic_distribution')
    def _compute_sale_contract_id(self):
        for line in self:
            line.sale_contract_id = self.env['account.analytic.account']
            if line.order_line_id.analytic_distribution:
                account_analytics_ids = [
                    analytic_id
                    for key in line.order_line_id.analytic_distribution.keys()
                    for analytic_id in key.split(',')
                ]
                valid_analytic = line.env['account.analytic.account']._read_group(
                    [
                        ('is_plan_seller_contract', '=', True),
                        ('id', 'in', account_analytics_ids),
                    ],
                    ['id'],
                )
                if valid_analytic:
                    line.sale_contract_id = valid_analytic[0][0]

    @api.depends('product_id', 'sale_contract_id', 'order_line_id.order_id.state')
    def _compute_product_analytic_id(self):
        need_trigger = False
        for line in self:
            old_product_analytic_id = line.product_analytic_id
            line.product_analytic_id = self.env['product.analytic']
            if line.sale_contract_id and line.state == 'sale':
                product_analytic = self.env['product.analytic']._read_group(
                    [
                        ('product_id', '=', line.product_id.id),
                        ('sale_contract_id', '=', line.sale_contract_id.id),
                    ],
                    ['id'],
                )
                if product_analytic:
                    line.product_analytic_id = product_analytic[0][0]
                else:
                    need_trigger = True
            elif (
                old_product_analytic_id
                and not old_product_analytic_id.need_to_purchase_ids
            ):
                old_product_analytic_id.unlink()
        if need_trigger:
            self.env.ref('sale_demand_vataga.cron_create_product_analytic')._trigger()

    def unlink(self):
        products_analytic = self.mapped('product_analytic_id')
        res = super().unlink()
        products_analytic.filtered(
            lambda analytic: not analytic.need_to_purchase_ids
        ).unlink()
        return res
