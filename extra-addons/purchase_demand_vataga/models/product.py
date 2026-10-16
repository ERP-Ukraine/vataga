from collections import defaultdict

from lxml import etree

from odoo import api, fields, models


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    qty_received = fields.Float(compute='_compute_qty_received', store=True)
    purchase_order_ids = fields.Many2many(
        'purchase.order', string='Закупівлі',
        compute='_compute_purchase_order_ids', compute_sudo=False,
    )

    @api.depends('account_move_ids.invoice_line_ids.purchase_line_id.order_id')
    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_purchase_order_ids(self):
        Move = self.env['account.move']
        Line = self.env['account.move.line']
        PurchaseLine = self.env['purchase.order.line']
        Order = self.env['purchase.order']
        for demand in self:
            demand.purchase_order_ids = False
        if not all(model.check_access_rights('read', raise_exception=False)
                   for model in (Move, Line, PurchaseLine, Order)):
            return
        # Search each model in batch so record rules apply at every step.
        moves = Move.search([
            ('id', 'in', self.account_move_ids.ids),
            ('move_type', 'in', ('in_invoice', 'in_refund')),
            ('company_id', 'in', self.env.companies.ids),
        ])
        lines = Line.search([
            ('move_id', 'in', moves.ids),
            ('display_type', 'in', ('product', 'line_section', 'line_note')),
            ('purchase_line_id', '!=', False),
        ])
        purchase_lines = PurchaseLine.search([
            ('id', 'in', lines.purchase_line_id.ids),
        ])
        orders = Order.search([
            ('id', 'in', purchase_lines.order_id.ids),
            ('company_id', 'in', self.env.companies.ids),
        ])
        visible_order_ids = set(orders.ids)
        order_by_line = {
            line.id: line.order_id.id for line in purchase_lines
            if line.order_id.id in visible_order_ids
        }
        orders_by_move = defaultdict(set)
        for line in lines:
            order_id = order_by_line.get(line.purchase_line_id.id)
            if order_id:
                orders_by_move[line.move_id.id].add(order_id)
        for demand in self:
            order_ids = set()
            for move in demand.account_move_ids:
                order_ids.update(orders_by_move[move.id])
            demand.purchase_order_ids = Order.browse(sorted(order_ids))

    @api.model
    def _get_default_form_view(self):
        arch = super()._get_default_form_view()
        purchase_field = arch.xpath("./sheet/group/field[@name='purchase_order_ids']")[0]
        invoice_field = arch.xpath("./sheet/group/field[@name='account_move_ids']")[0]
        invoice_field.getparent().addnext(purchase_field.getparent())
        purchase_field.set('readonly', '1')
        purchase_field.set('options', "{'no_create': True, 'no_create_edit': True}")
        tree = etree.SubElement(
            purchase_field, 'tree', string='Закупівлі',
            create='0', edit='0', delete='0',
        )
        for name in ('name', 'partner_id', 'date_order', 'amount_total', 'currency_id', 'state'):
            column = etree.SubElement(tree, 'field', name=name)
            if name == 'amount_total':
                column.set('widget', 'monetary')
                column.set('options', "{'currency_field': 'currency_id'}")
        return arch

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

            for bom in product_analytic.kit_bom_ids:
                for product in bom.product_id + bom.product_tmpl_id.product_variant_ids:
                    kit_purchase_lines = product_analytic.sale_contract_id.seller_purchase_line_ids.filtered(
                        lambda line: line.product_id == product
                    )
                    kit_total_in_received = 0
                    for line in kit_purchase_lines:
                        kit_total_in_received += line.product_uom._compute_quantity(
                            line.qty_received, line.product_id.uom_id
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
