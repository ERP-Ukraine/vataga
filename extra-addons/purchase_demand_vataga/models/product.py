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

    @api.depends(
        'product_id', 'sale_contract_id', 'kit_bom_ids',
        'account_move_ids.invoice_line_ids.purchase_line_id.order_id',
        'account_move_ids.invoice_line_ids.product_id',
        'account_move_ids.invoice_line_ids.analytic_distribution',
        'account_move_ids.invoice_line_ids.seller_contract_id',
        'account_move_ids.seller_contract_id',
        'sale_contract_id.seller_purchase_line_ids.product_id',
        'sale_contract_id.seller_purchase_line_ids.analytic_distribution',
        'sale_contract_id.seller_purchase_line_ids.order_id.seller_contract_id',
    )
    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_purchase_order_ids(self):
        Move = self.env['account.move']
        Line = self.env['account.move.line']
        PurchaseLine = self.env['purchase.order.line']
        Order = self.env['purchase.order']
        Bom = self.env['mrp.bom']
        for demand in self:
            demand.purchase_order_ids = False
        if not all(model.check_access_rights('read', raise_exception=False)
                   for model in (PurchaseLine, Order)):
            return

        # Batch equivalent of _get_invoice_kit_parent_boms(), using current BOMs.
        products_by_component = {product.id: {product.id} for product in self.product_id}
        if Bom.check_access_rights('read', raise_exception=False):
            boms = Bom.search([
                ('bom_line_ids.product_id', 'in', self.product_id.ids),
                ('type', '=', 'phantom'),
                '|', ('company_id', '=', False),
                ('company_id', 'in', self.env.companies.ids),
            ])
            for bom in boms:
                kit_ids = (bom.product_id + bom.product_tmpl_id.product_variant_ids).ids
                for component in bom.bom_line_ids.product_id:
                    if component.id in products_by_component:
                        products_by_component[component.id].update(kit_ids)
        product_ids = set().union(*products_by_component.values()) if products_by_component else set()

        # Orders are searched separately before reading their fields: line rules
        # do not necessarily grant access to the parent purchase order.
        orders = Order.search([
            ('company_id', 'in', self.env.companies.ids),
            ('order_line.product_id', 'in', sorted(product_ids)),
            '|', ('seller_contract_id', 'in', self.sale_contract_id.ids),
            ('order_line.seller_contract_id', 'in', self.sale_contract_id.ids),
        ])
        direct_lines = PurchaseLine.search([
            ('order_id', 'in', orders.ids),
            ('product_id', 'in', sorted(product_ids)),
            '|', ('seller_contract_id', 'in', self.sale_contract_id.ids),
            ('order_id.seller_contract_id', 'in', self.sale_contract_id.ids),
        ])
        direct_orders = defaultdict(set)
        for line in direct_lines:
            contract = line.seller_contract_id
            header_contract = line.order_id.seller_contract_id
            if contract and header_contract and contract != header_contract:
                continue
            if not contract and not line.analytic_distribution:
                contract = header_contract
            if contract:
                direct_orders[(line.product_id.id, contract.id)].add(line.order_id.id)

        # Accounting access is optional for the direct purchase source.
        invoice_lines_by_product = defaultdict(list)
        order_by_line = {}
        if all(model.check_access_rights('read', raise_exception=False)
               for model in (Move, Line)):
            moves = Move.search([
                ('id', 'in', self.account_move_ids.ids),
                ('move_type', 'in', ('in_invoice', 'in_refund')),
                ('company_id', 'in', self.env.companies.ids),
            ])
            lines = Line.search([
                ('move_id', 'in', moves.ids),
                ('display_type', 'in', ('product', 'line_section', 'line_note')),
                ('product_id', 'in', sorted(product_ids)),
                ('purchase_line_id', '!=', False),
            ])
            purchase_lines = PurchaseLine.search([('id', 'in', lines.purchase_line_id.ids)])
            invoice_orders = Order.search([
                ('id', 'in', purchase_lines.order_id.ids),
                ('company_id', 'in', self.env.companies.ids),
            ])
            visible_ids = set(invoice_orders.ids)
            order_by_line = {
                line.id: line.order_id.id for line in purchase_lines
                if line.order_id.id in visible_ids
            }
            for line in lines:
                invoice_lines_by_product[line.product_id.id].append(line)
        for demand in self:
            order_ids = set()
            if demand.product_id and demand.sale_contract_id:
                move_ids = set(demand.account_move_ids.ids)
                for product_id in products_by_component[demand.product_id.id]:
                    order_ids.update(direct_orders[(product_id, demand.sale_contract_id.id)])
                    for line in invoice_lines_by_product[product_id]:
                        if (line.move_id.id in move_ids
                                and demand._has_related_sale_contract(line)):
                            order_id = order_by_line.get(line.purchase_line_id.id)
                            if order_id:
                                order_ids.add(order_id)
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
            if name == 'date_order':
                column.set('string', 'Дата замовлення')
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
