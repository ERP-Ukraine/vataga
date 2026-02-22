from odoo import api, fields, models
from datetime import timedelta


class ProductProduct(models.Model):
    _inherit = 'product.product'

    product_analytic_ids = fields.One2many('product.analytic', 'product_id')
    account_analytic_ids = fields.Many2many(comodel_name='account.analytic.account')

    def write(self, vals):
        res = super().write(vals)
        if vals.get('name') or vals.get('default_code'):
            self.product_analytic_ids._update_product_fields_translations()
        return res


class ProductAnalytic(models.Model):
    _name = 'product.analytic'
    _description = 'Product Analytic'
    _sql_constraints = [
        (
            'product_and_seller_uniq',
            'unique(product_id, sale_contract_id)',
            'This product is already linked to the selected analytic account.',
        )
    ]
    name = fields.Char(translate=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='cascade')
    sale_contract_id = fields.Many2one(
        'account.analytic.account', required=True, ondelete='cascade'
    )
    need_to_purchase_ids = fields.One2many(
        'sale.order.line.purchase', 'product_analytic_id'
    )
    demand = fields.Float(compute='_compute_numbers', store=True)
    in_invoice = fields.Float(compute='_compute_numbers', store=True)
    closed = fields.Float(compute='_compute_numbers', store=True, group_operator='avg')
    comment = fields.Char(
        related='sale_contract_id.seller_analytic_comment',
        store=True,
        group_operator='max',
    )
    product_name = fields.Char('Product Name', translate=True)

    category_name = fields.Char('Category', related='product_id.categ_id.name', store=True)
    manager_name = fields.Char('Manager', related='product_id.responsible_id.name', store=True)
    ua_sale_contract_ids = fields.Many2many(
        comodel_name='l10n_ua.contract',
        relation='sale_contract_rel',
        compute='_compute_ua_sale_contract_ids',
        store=True,
    )
    ua_purchase_contract_ids = fields.Many2many(
        comodel_name='l10n_ua.contract',
        relation='purchase_contract_rel',
        compute='_compute_ua_purchase_contract_ids',
        store=True,
    )
    account_move_ids = fields.Many2many(comodel_name='account.move')
    
    kit_bom_ids = fields.Many2many(comodel_name='mrp.bom', compute='_compute_kit_bom_ids', store=True)

    @api.depends('product_id')
    def _compute_kit_bom_ids(self):
        for product_analytic in self:
            product_analytic.kit_bom_ids = self.env['mrp.bom'].search(
                [
                    ('bom_line_ids.product_id', '=', product_analytic.product_id.id),
                    ('type', '=', 'phantom'),
                ]
            )

    @api.depends('need_to_purchase_ids.order_line_id.order_id.ua_contract_id')
    def _compute_ua_sale_contract_ids(self):
        for product_analytic in self:
            product_analytic.ua_sale_contract_ids = (
                product_analytic.need_to_purchase_ids.order_line_id.order_id.mapped(
                    'ua_contract_id'
                )
            )

    @api.depends('sale_contract_id.seller_purchase_ids.ua_contract_id')
    def _compute_ua_purchase_contract_ids(self):
        for product_analytic in self:
            product_analytic.ua_purchase_contract_ids = (
                product_analytic.sale_contract_id.seller_purchase_ids.mapped(
                    'ua_contract_id'
                )
            )

    @api.depends(
        'sale_contract_id.seller_move_line_ids.product_id',
        'sale_contract_id.seller_move_line_ids.quantity',
        'sale_contract_id.seller_move_line_ids.move_id.state',
        'need_to_purchase_ids',
        'need_to_purchase_ids.product_qty',
        'kit_bom_ids',
    )
    def _compute_numbers(self):
        for product_analytic in self:
            product_analytic.demand = sum(
                product_analytic.need_to_purchase_ids.mapped('product_qty')
            )

            product_seller_line_ids = (
                product_analytic.sale_contract_id.seller_move_line_ids.filtered(
                    lambda line: line.move_id.state == 'posted'
                    and line.product_id == product_analytic.product_id and line.move_type == 'in_invoice'
                )
            )
            product_seller_refund_line_ids = (
                product_analytic.sale_contract_id.seller_move_line_ids.filtered(
                    lambda line: line.move_id.state == 'posted'
                    and line.product_id == product_analytic.product_id
                    and line.move_type == 'in_refund'
                )
            )
            total_in_invoice = 0
            for line in product_seller_line_ids:
                total_in_invoice += line.product_uom_id._compute_quantity(
                    line.quantity, line.product_id.uom_id
                )
            for line in product_seller_refund_line_ids:
                total_in_invoice -= line.product_uom_id._compute_quantity(
                    line.quantity, line.product_id.uom_id
                )

            for bom in product_analytic.kit_bom_ids:
                for product in bom.product_id + bom.product_tmpl_id.product_variant_ids:
                    seller_line_ids = product_analytic.sale_contract_id.seller_move_line_ids.filtered(
                        lambda line: line.product_id == product and line.move_id.state == 'posted' and line.move_type == 'in_invoice'
                    )
                    seller_refund_line_ids = (
                        product_analytic.sale_contract_id.seller_move_line_ids.filtered(
                            lambda line: line.product_id == product
                            and line.move_id.state == 'posted'
                            and line.move_type == 'in_refund'
                        )
                    )
                    kit_total_in_invoice = 0
                    for line in seller_line_ids:
                        kit_total_in_invoice += line.product_uom_id._compute_quantity(
                            line.quantity, line.product_id.uom_id
                        )
                    for line in seller_refund_line_ids:
                        kit_total_in_invoice -= line.product_uom_id._compute_quantity(
                            line.quantity, line.product_id.uom_id
                        )
                    need_bom_lines = bom.bom_line_ids.filtered(
                        lambda line: line.product_id == product_analytic.product_id
                    )
                    bom_lines_uom_qty = 0
                    for bom_line in need_bom_lines:
                        bom_lines_uom_qty += bom_line.product_uom_id._compute_quantity(bom_line.product_qty, bom_line.product_id.uom_id)
                    total_in_invoice += kit_total_in_invoice * bom_lines_uom_qty

            product_analytic.in_invoice = total_in_invoice
            if product_analytic.demand:
                product_analytic.closed = (
                    product_analytic.in_invoice / product_analytic.demand
                )
            else:
                product_analytic.closed = 0

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        self.env.ref('sale_demand_vataga.cron_update_product_analytic_moves').with_context({'by_all_time': True})._trigger()
        for product_analytic in res:
            product_analytic.product_id.account_analytic_ids = product_analytic.product_id.product_analytic_ids.mapped('sale_contract_id')
        for kit_bom_id in res.kit_bom_ids:
            products_analytic = self.env['product.analytic'].search([('kit_bom_ids', 'in', kit_bom_id.ids)])
            (kit_bom_id.product_id + kit_bom_id.product_tmpl_id.product_variant_ids).account_analytic_ids = products_analytic.mapped('sale_contract_id')
        res._update_product_fields_translations()
        return res

    def write(self, vals):
        res = super().write(vals)
        for product_analytic in self:
            product_analytic.product_id.account_analytic_ids = (
                product_analytic.product_id.product_analytic_ids.mapped(
                    'sale_contract_id'
                )
            )
        for kit_bom_id in self.kit_bom_ids:
            products_analytic = self.env['product.analytic'].search([('kit_bom_ids', 'in', kit_bom_id.ids)])
            (kit_bom_id.product_id + kit_bom_id.product_tmpl_id.product_variant_ids).account_analytic_ids = products_analytic.mapped('sale_contract_id')
        if 'product_id' in vals:
            self._update_product_fields_translations()
        return res

    def unlink(self):
        product_ids = self.product_id
        kit_bom_ids = self.kit_bom_ids
        res = super().unlink()
        for product in product_ids:
            product.account_analytic_ids = product.product_analytic_ids.mapped('sale_contract_id')
        for kit_bom_id in kit_bom_ids:
            products_analytic = self.env['product.analytic'].search([('kit_bom_ids', 'in', kit_bom_id.ids)])
            (kit_bom_id.product_id + kit_bom_id.product_tmpl_id.product_variant_ids).account_analytic_ids = products_analytic.mapped('sale_contract_id')
        return res

    @api.model
    def _cron_create_product_analytic(self):
        sale_order_line_purchase = self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
            limit=10,
        )
        if sale_order_line_purchase:
            for line in sale_order_line_purchase:
                product_analytic = self.env['product.analytic'].search(
                    [
                        ('product_id', '=', line.product_id.id),
                        ('sale_contract_id', '=', line.sale_contract_id.id),
                    ]
                )
                if not product_analytic:
                    product_analytic = self.env['product.analytic'].create(
                        {
                            'product_id': line.product_id.id,
                            'sale_contract_id': line.sale_contract_id.id,
                        }
                    )
                line.product_analytic_id = product_analytic
            self.env.ref('sale_demand_vataga.cron_create_product_analytic')._trigger()

    @api.model
    def _cron_sync_account_move_ids(self):
        min_time = fields.Datetime.now() - timedelta(hours=3)
        domain = [('write_date', '>', min_time), ('seller_contract_id', '!=', False)]
        if self.env.context.get('by_all_time'):
            domain = [('seller_contract_id', '!=', False)]
        move_lines = self.env['account.move.line'].search(
            domain
        )
        if move_lines:
            seller_contracts = move_lines.mapped('seller_contract_id')
            product_analytics = self.env['product.analytic'].search(
                [('sale_contract_id', 'in', seller_contracts.ids)]
            )
            for product_analytic in product_analytics:
                kit_ids = set(product_analytic.kit_bom_ids.product_id.ids + product_analytic.kit_bom_ids.product_tmpl_id.product_variant_ids.ids)
                moves = move_lines.filtered(
                    lambda line: line.seller_contract_id
                    == product_analytic.sale_contract_id
                    and (line.product_id == product_analytic.product_id or line.product_id.id in kit_ids)
                ).mapped('move_id')
                product_analytic.account_move_ids = moves

    def _update_translations(self, other_model, source_field_name, field_name):
        if other_model:
            translations = other_model.get_field_translations(source_field_name)[0]
        else:
            translations = []
            for lang in self.env['res.lang'].get_installed():
                translations.append({'lang': lang[0], 'value': ''})
        for line in self:
            for translation in translations:
                line.update_field_translations(
                    field_name, {translation.get('lang'): translation.get('value', '')}
                )
    def _update_product_fields_translations(self):
        for product_analytic in self:
            product_analytic._update_translations(product_analytic.product_id, 'name', 'product_name')
            product_analytic._update_full_name_translations()

    def _update_full_name_translations(self):
        for line in self:
            product_name_translations = line.get_field_translations('product_name')[0]
            default_code = line.product_id.default_code
            category_name = line.category_name
            manager_name = line.manager_name
            for translation in product_name_translations:
                line.update_field_translations(
                    'name',
                    {
                        translation.get(
                            'lang'
                        ): f'{translation.get("value")} [{default_code}] {translation.get("value")} {category_name} {manager_name}'
                    },
                )
