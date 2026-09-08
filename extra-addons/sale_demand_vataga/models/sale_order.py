import math

from psycopg2 import IntegrityError

from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    ANALYTIC_HEADER_FIELDS = (
        'project_account_id',
        'budget_account_id',
        'cash_flow_item_account_id',
        'seller_contract_id',
    )

    project_account_id = fields.Many2one(
        'account.analytic.account', string='Проект',
        domain="[('is_plan_project', '=', True)]",
    )
    budget_account_id = fields.Many2one(
        'account.analytic.account', string='Бюджет',
        domain="[('is_plan_budget', '=', True)]",
    )
    cash_flow_item_account_id = fields.Many2one(
        'account.analytic.account', string='Стаття Cashflow',
        domain="[('is_plan_cash_flow_item', '=', True)]",
    )
    seller_contract_id = fields.Many2one(
        'account.analytic.account', string='Контракт продажу',
        domain="[('is_plan_seller_contract', '=', True)]",
    )

    deal_closed = fields.Boolean(
        'Deal closed', help='Everything is signed, paid, shipped and documented.'
    )

    def _get_header_analytic_distribution(self):
        self.ensure_one()
        account_ids = sorted({
            str(self[field_name].id)
            for field_name in self.ANALYTIC_HEADER_FIELDS if self[field_name]
        })
        return {','.join(account_ids): 100} if account_ids else False

    def _sync_header_analytic_distribution(self):
        for order in self:
            distribution = order._get_header_analytic_distribution()
            order.order_line.filtered(lambda line: not line.display_type).update({
                'analytic_distribution': distribution,
            })

    @api.onchange(*ANALYTIC_HEADER_FIELDS)
    def _onchange_header_analytic_distribution(self):
        self._sync_header_analytic_distribution()

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        # Also covers header values supplied through context defaults.
        orders.filtered(
            lambda order: order._get_header_analytic_distribution()
        )._sync_header_analytic_distribution()
        return orders

    def write(self, vals):
        result = super().write(vals)
        if set(vals).intersection(self.ANALYTIC_HEADER_FIELDS):
            # Explicitly writing empty headers must clear the previous distribution.
            self._sync_header_analytic_distribution()
        return result


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    need_to_purchase_ids = fields.One2many('sale.order.line.purchase', 'order_line_id')
    bom_id = fields.Many2one('mrp.bom')

    @api.depends('product_id', 'order_id', 'order_id.partner_id')
    def _compute_analytic_distribution(self):
        product_lines = self.filtered(lambda line: not line.display_type)
        super(SaleOrderLine, product_lines)._compute_analytic_distribution()
        for line in product_lines.filtered('order_id'):
            distribution = line.order_id._get_header_analytic_distribution()
            if distribution:
                line.analytic_distribution = distribution

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
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            order = self.env['sale.order'].browse(
                vals.get('order_id') or self.env.context.get('default_order_id')
            )
            display_type = vals.get(
                'display_type', self.env.context.get('default_display_type')
            )
            if order and not display_type:
                distribution = order._get_header_analytic_distribution()
                if distribution:
                    vals['analytic_distribution'] = distribution
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
        'product.analytic',
        compute='_compute_product_analytic_id',
        compute_sudo=True,
        store=True,
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
        self._sync_product_analytic_id()

    def _get_product_analytic_domain(self):
        self.ensure_one()
        return [
            ('product_id', '=', self.product_id.id),
            ('sale_contract_id', '=', self.sale_contract_id.id),
        ]

    def _get_or_create_product_analytic(self):
        self.ensure_one()
        product_analytic_model = self.env['product.analytic'].sudo()
        product_analytic = product_analytic_model.search(
            self._get_product_analytic_domain(),
            limit=1,
        )
        if product_analytic:
            return product_analytic

        try:
            with self.env.cr.savepoint():
                return product_analytic_model.create(
                    {
                        'product_id': self.product_id.id,
                        'sale_contract_id': self.sale_contract_id.id,
                    }
                )
        except IntegrityError:
            return product_analytic_model.search(
                self._get_product_analytic_domain(),
                limit=1,
            )

    def _sync_product_analytic_id(self):
        for line in self:
            old_product_analytic_id = line.product_analytic_id
            new_product_analytic_id = self.env['product.analytic']
            if line.sale_contract_id and line.state == 'sale':
                new_product_analytic_id = line._get_or_create_product_analytic()
            line.sudo().product_analytic_id = new_product_analytic_id
            if (
                old_product_analytic_id
                and old_product_analytic_id != new_product_analytic_id
            ):
                old_product_analytic_id = old_product_analytic_id.sudo()
                old_product_analytic_id.invalidate_recordset(['need_to_purchase_ids'])
                if not old_product_analytic_id.need_to_purchase_ids:
                    old_product_analytic_id.unlink()

    def unlink(self):
        products_analytic = self.mapped('product_analytic_id').sudo()
        res = super().unlink()
        products_analytic.filtered(
            lambda analytic: not analytic.need_to_purchase_ids
        ).unlink()
        return res
