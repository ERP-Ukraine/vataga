from odoo import models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # override Core(odoo/addons/purchase/models/purchase_order.py::_add_supplier_to_product())
    def _add_supplier_to_product(self):
        """Remove the 10 suppliers limit per product."""
        for line in self.order_line:
            # original
            partner = self.partner_id if not self.partner_id.parent_id else self.partner_id.parent_id
            already_seller = (partner | self.partner_id) & line.product_id.seller_ids.mapped('partner_id')
            
            # CUSTOM: Removed "and len(line.product_id.seller_ids) <= 10" condition
            if line.product_id and not already_seller:
                # original
                currency = partner.property_purchase_currency_id or self.env.company.currency_id
                price = self.currency_id._convert(
                    line.price_unit, currency, line.company_id, 
                    line.date_order or fields.Date.today(), round=False
                )
                if line.product_id.product_tmpl_id.uom_po_id != line.product_uom:
                    default_uom = line.product_id.product_tmpl_id.uom_po_id
                    price = line.product_uom._compute_price(price, default_uom)

                supplierinfo = self._prepare_supplier_info(partner, line, price, currency)
                
                seller = line.product_id._select_seller(
                    partner_id=line.partner_id,
                    quantity=line.product_qty,
                    date=line.order_id.date_order and line.order_id.date_order.date(),
                    uom_id=line.product_uom
                )
                if seller:
                    supplierinfo['product_name'] = seller.product_name
                    supplierinfo['product_code'] = seller.product_code
                vals = {
                    'seller_ids': [(0, 0, supplierinfo)],
                }
                line.product_id.product_tmpl_id.sudo().write(vals)
    