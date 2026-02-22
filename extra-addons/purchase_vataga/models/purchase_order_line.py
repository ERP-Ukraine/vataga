from odoo import api, fields, models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    supplier_product_name_for_import = fields.Char()
    supplier_product_code_for_import = fields.Char()

    @api.depends(
        'product_id', 'order_id.partner_id',
        'order_id.project_account_id', 'order_id.budget_account_id',
        'order_id.cash_flow_item_account_id', 'order_id.seller_contract_id'
    )
    def _compute_analytic_distribution(self):
        for line in self:
            set_analytic_accounts = [
                str(account.id) for account in [
                    line.order_id.project_account_id,
                    line.order_id.budget_account_id,
                    line.order_id.cash_flow_item_account_id,
                    line.order_id.seller_contract_id
                ] if account]
            if set_analytic_accounts:
                ids_sts = ','.join(sorted(set_analytic_accounts))
                line.analytic_distribution = {ids_sts: 100}
            else:
                super(PurchaseOrderLine, line)._compute_analytic_distribution()

    # this is not the most effective way, but they do not seem to have large POs and this is easier to change if
    # search priority will become different
    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {vals['order_id'] for vals in vals_list if (
            'supplier_product_name_for_import' in vals or 'supplier_product_code_for_import' in vals) and 'product_id' not in vals}
        if order_ids:
            orders = self.env['purchase.order'].browse(order_ids)
            order_partner_map = {order.id: order.partner_id.id for order in orders}
            for vals in vals_list:
                supplier_product_name_for_import = vals.get('supplier_product_name_for_import')
                supplier_product_code_for_import = vals.get('supplier_product_code_for_import')
                if (supplier_product_name_for_import or supplier_product_code_for_import) and 'product_id' not in vals:
                    partner_id = order_partner_map.get(vals['order_id'])
                    domain = [('partner_id', '=', partner_id),]
                    if supplier_product_name_for_import and supplier_product_code_for_import:
                        domain.extend([
                            '|',
                            ('product_name', '=', supplier_product_name_for_import),
                            ('product_code', '=', supplier_product_code_for_import),
                        ])
                    elif supplier_product_name_for_import:
                        domain.extend([('product_name', '=', supplier_product_name_for_import),])
                    else:
                        domain.extend([('product_code', '=', supplier_product_code_for_import),])

                    product_by_supplier_name = self.env['product.supplierinfo'].search(domain, limit=1)
                    if product_by_supplier_name:
                        vals['product_id'] = product_by_supplier_name.product_id.id or product_by_supplier_name.product_tmpl_id.product_variant_id.id

        return super().create(vals_list)
