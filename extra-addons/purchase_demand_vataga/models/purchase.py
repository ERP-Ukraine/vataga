from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _get_product_catalog_order_line_info(self, product_ids, **kwargs):
        result = super()._get_product_catalog_order_line_info(product_ids, **kwargs)
        product_ids = [product_id for product_id in result.keys()]
        for product_id in result.keys():
            result[product_id]['demand'] = 0
            result[product_id]['in_contract'] = False
            result[product_id]['start_quantity'] = result[product_id]['quantity']
        if self.seller_contract_id:
            products_analytic = self.seller_contract_id.product_analytic_ids.filtered(
                lambda p_a: p_a.product_id.id in product_ids
                or [p_id for p_id in p_a.kit_bom_ids.product_id.ids
                + p_a.kit_bom_ids.product_tmpl_id.product_variant_ids.ids if p_id in product_ids]
            )
            for product_id in result.keys():
                product_analytic = products_analytic.filtered(
                    lambda analytic: analytic.product_id.id == product_id
                )
                if product_analytic:
                    demand = self._find_purchase_demand_for_product(product_analytic)
                    result[product_id]['demand'] = demand
                    result[product_id]['in_contract'] = True
                kit_products_analytic = products_analytic.filtered(
                    lambda analytic: product_id
                    in analytic.kit_bom_ids.product_id.ids
                    + analytic.kit_bom_ids.product_tmpl_id.product_variant_ids.ids
                )
                if kit_products_analytic:
                    demand = self._find_purchase_demand_for_kit_product(product_id, products_analytic)
                    result[product_id]['demand'] = demand
                    result[product_id]['in_contract'] = True
        return result

    def _find_purchase_demand_for_kit_product(self, product_id, products_analytic):
        demand_by_products_in_bom = {}
        for product_analytic in products_analytic:
            mrp_bom = product_analytic.kit_bom_ids.filtered(lambda bom: product_id in bom.product_id.ids + bom.product_tmpl_id.product_variant_ids.ids)
            bom_line = mrp_bom.bom_line_ids.filtered(lambda line: line.product_id == product_analytic.product_id)
            in_bom_product_qty = bom_line.product_uom_id._compute_quantity(bom_line.product_qty, bom_line.product_id.uom_id)
            product_demand = self._find_purchase_demand_for_product(product_analytic, for_kit_product=True)
            demand_by_products_in_bom[product_analytic.product_id.id] = (product_demand / (in_bom_product_qty or 1)) * mrp_bom.product_qty
        lines_with_kit_product = self.order_line.filtered(
            lambda line: line.product_id.id == product_id
        )
        return max(demand_by_products_in_bom.values()) - sum(lines_with_kit_product.mapped('product_qty'))

    def _find_purchase_demand_for_product(self, product_analytic, for_kit_product=False):
        demand = (
            product_analytic.demand
            - product_analytic.in_invoice
        )
        lines_with_product = self.order_line.filtered(
            lambda line: line.product_id.id == product_analytic.product_id.id
        )
        if lines_with_product:
            for line in lines_with_product:
                demand -= line.product_uom._compute_quantity(
                    line.product_qty, line.product_id.uom_id
                )
        if not for_kit_product:
            lines_with_kit_product = self.order_line.filtered(
                lambda line: line.product_id.id in product_analytic.kit_bom_ids.product_id.ids + product_analytic.kit_bom_ids.product_tmpl_id.product_variant_ids.ids
            )
            if lines_with_kit_product:
                for line in lines_with_kit_product:
                    total_kit_qty = line.product_uom._compute_quantity(line.product_qty, line.product_id.uom_id)
                    mrp_bom = product_analytic.kit_bom_ids.filtered(lambda bom: line.product_id in bom.product_id + bom.product_tmpl_id.product_variant_ids)
                    bom_line = mrp_bom.bom_line_ids.filtered(lambda bom_line: bom_line.product_id.id == product_analytic.product_id.id)
                    demand -= (
                        bom_line.product_uom_id._compute_quantity(
                            bom_line.product_qty, bom_line.product_id.uom_id
                        )
                        * total_kit_qty
                    ) / mrp_bom.product_qty
        return demand

    def _get_action_add_from_catalog_extra_context(self):
        res = super()._get_action_add_from_catalog_extra_context()
        if self.seller_contract_id:
            res.update(
                {
                    'search_default_account_analytic_ids': self.seller_contract_id.display_name
                }
            )
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    seller_contract_id = fields.Many2one(
        'account.analytic.account', compute='_compute_seller_contract_id', store=True
    )

    @api.depends('analytic_distribution')
    def _compute_seller_contract_id(self):
        for line in self:
            line.seller_contract_id = self.env['account.analytic.account']
            if line.analytic_distribution:
                account_analytics_ids = [
                    analytic_id
                    for key in line.analytic_distribution.keys()
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
                    line.seller_contract_id = valid_analytic[0][0]
