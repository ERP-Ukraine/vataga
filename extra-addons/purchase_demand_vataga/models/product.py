from odoo import api, fields, models


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    qty_received = fields.Float(compute='_compute_qty_received', store=True)

    def _get_related_purchase_lines(self):
        self.ensure_one()
        parent_kit_boms = self._get_parent_kit_boms()
        kit_products = (
            parent_kit_boms.product_id
            + parent_kit_boms.product_tmpl_id.product_variant_ids
        )
        return self.sale_contract_id.seller_purchase_line_ids.filtered(
            lambda line: line.product_id == self.product_id
            or line.product_id in kit_products
        )

    @api.depends(
        'product_id',
        'sale_contract_id.seller_purchase_line_ids',
        'sale_contract_id.seller_purchase_line_ids.product_id',
        'sale_contract_id.seller_purchase_line_ids.order_id.ua_contract_id',
        'kit_bom_ids',
    )
    def _compute_ua_purchase_contract_ids(self):
        for product_analytic in self:
            product_analytic.ua_purchase_contract_ids = (
                product_analytic._get_related_purchase_lines().order_id.mapped(
                    'ua_contract_id'
                )
            )

    @api.depends(
        'sale_contract_id.seller_purchase_line_ids',
        'sale_contract_id.seller_purchase_line_ids.seller_contract_id',
        'sale_contract_id.seller_purchase_line_ids.qty_received',
        'sale_contract_id.seller_purchase_line_ids.product_id',
    )
    def _compute_qty_received(self):
        for product_analytic in self:
            purchase_lines = (
                product_analytic.sale_contract_id.seller_purchase_line_ids.filtered(
                    lambda line: line.product_id == product_analytic.product_id
                )
            )
            total_qty_received = 0
            for line in purchase_lines:
                total_qty_received += line.product_uom._compute_quantity(
                    line.qty_received, line.product_id.uom_id
                )

            for bom in product_analytic._get_parent_kit_boms():
                for product in bom.product_id + bom.product_tmpl_id.product_variant_ids:
                    kit_purchase_lines = product_analytic.sale_contract_id.seller_purchase_line_ids.filtered(
                        lambda line: line.product_id == product
                    )
                    kit_total_in_received = 0
                    for line in kit_purchase_lines:
                        kit_total_in_received += line.product_uom._compute_quantity(
                            line.product_qty, line.product_id.uom_id
                        )
                    need_bom_lines = bom.bom_line_ids.filtered(
                        lambda line: line.product_id == product_analytic.product_id
                    )
                    bom_lines_uom_qty = 0
                    for bom_line in need_bom_lines:
                        bom_lines_uom_qty += (
                            bom_line.product_uom_id._compute_quantity(
                                bom_line.product_qty, bom_line.product_id.uom_id
                            )
                            / bom_line.bom_id.product_qty
                        )
                    total_qty_received += kit_total_in_received * bom_lines_uom_qty
            product_analytic.qty_received = total_qty_received
