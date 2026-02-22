from odoo import api, fields, models


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    need_to_purchase_ids = fields.One2many('mrp.bom.purchase.line', 'bom_id')
    sale_order_line_ids = fields.One2many('sale.order.line', 'bom_id')
    need_update_to_purchase = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['need_update_to_purchase'] = True
        res = super().create(vals_list)
        sols = res._update_all_upper_boms()
        sols.set_bom_id()
        self.env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()
        return res

    def write(self, vals):
        res = super().write(vals)
        if (
            'product_id' in vals
            or 'product_tmpl_id' in vals
            or 'bom_line_ids' in vals
            or 'company_id' in vals
            or vals.get('need_update_to_purchase')
        ):
            sols = self.mapped('sale_order_line_ids')
            sols |= self._update_all_upper_boms()
            self.filtered(lambda bom: bom.need_update_to_purchase == False).need_update_to_purchase = True
            sols.set_bom_id()
        self.env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()
        return res

    @api.model
    def _cron_create_total_product_line_ids(self):
        boms = self.env['mrp.bom'].search([('need_update_to_purchase', '=', True)], limit=2)
        if boms:
            for bom in boms:
                bom.need_to_purchase_ids.unlink()
                warehouse = self.env['stock.warehouse'].browse(
                    self.env['report.mrp.report_bom_structure'].get_warehouses()[0]['id']
                )
                data = self.env['report.mrp.report_bom_structure']._get_bom_data(
                    bom, warehouse
                )
                lines = self.get_all_product_lines(data)
                for line in lines:
                    need_to_purchase_line = bom.need_to_purchase_ids.filtered(lambda l: l.product_id.id == line['product_id'])
                    product = self.env['product.product'].search([('id', '=', line['product_id'])])
                    if need_to_purchase_line:
                        need_to_purchase_line.product_qty += line['uom']._compute_quantity(
                            line['quantity'], product.uom_id
                        )
                    else:
                        self.env['mrp.bom.purchase.line'].create(
                            {
                                'bom_id': bom.id,
                                'product_id': line['product_id'],
                                'product_qty': line['uom']._compute_quantity(
                                    line['quantity'], product.uom_id
                                ),
                            }
                        )
            boms.mapped('sale_order_line_ids').create_need_to_purchase_ids()
            boms.need_update_to_purchase = False
            self.env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()

    def get_all_product_lines(self, data):
        lines = []
        bom_lines = data['components']
        for bom_line in bom_lines:
            if bom_line.get('components'):
                lines += self.get_all_product_lines(bom_line)
            else:
                lines.append(
                    {
                        'product_id': bom_line['product_id'],
                        'quantity': bom_line['quantity'],
                        'uom': bom_line['uom'],
                    }
                )
        return lines

    def unlink(self):
        sale_order_lines = self.mapped('sale_order_line_ids')
        res = super().unlink()
        sale_order_lines.set_bom_id()
        sale_order_lines.create_need_to_purchase_ids()
        return res

    def _update_all_upper_boms(self):
        sols = self.env['sale.order.line']
        for bom in self:
            sol_domain = [
                ('bom_id', '=', False),
                ('company_id', '=', bom.company_id.id),
            ]
            bom_line_domain = [('company_id', '=', bom.company_id.id)]
            if bom.product_id:
                sol_domain.append(('product_id', '=', bom.product_id.id))
                bom_line_domain.append(('product_id', '=', bom.product_id.id))
            else:
                sol_domain.append(
                    ('product_id', 'in', bom.product_tmpl_id.product_variant_ids.ids)
                )
                bom_line_domain.append(
                    (
                        'product_id',
                        'in',
                        bom.product_tmpl_id.product_variant_ids.ids,
                    )
                )
            sols |= bom.env['sale.order.line'].search(sol_domain)
            mrp_bom_lines = bom.env['mrp.bom.line'].search(bom_line_domain)
            mrp_bom_lines.mapped('bom_id').need_update_to_purchase = True
        return sols


class MrpBomPurchaseLine(models.Model):
    _name = 'mrp.bom.purchase.line'
    _description = 'MRP Bom Purchase'

    bom_id = fields.Many2one('mrp.bom', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    product_qty = fields.Float()


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        res.bom_id.need_update_to_purchase = True
        sols = res.bom_id._update_all_upper_boms()
        sols.set_bom_id()
        self.env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()
        return res

    def write(self, vals):
        res = super().write(vals)
        self.bom_id.need_update_to_purchase = True
        sols = self.bom_id._update_all_upper_boms()
        sols.set_bom_id()
        self.env.ref('sale_demand_vataga.cron_update_need_to_purchase')._trigger()
        return res
