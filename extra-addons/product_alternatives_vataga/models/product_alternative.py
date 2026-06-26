from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    analog_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        compute='_compute_analog_uom_id',
        store=True,
        string='Analog Unit of Measure',
    )
    analog_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_tmpl_id',
        string='Analogs',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id


class ProductProduct(models.Model):
    _inherit = 'product.product'

    analog_source_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_id',
        domain=[('is_primary_link', '=', True)],
        string='Used as Analog For',
    )
    analog_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        compute='_compute_analog_uom_id',
        store=True,
        string='Analog Unit of Measure',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id

    def _get_primary_analog_products(self):
        return self.mapped('product_tmpl_id.analog_line_ids').filtered(
            'is_primary_link'
        ).product_id

    def _get_primary_analog_main_products(self):
        products = self.env['product.product']
        for line in self.mapped('analog_source_line_ids').filtered('is_primary_link'):
            products |= line.product_tmpl_id.product_variant_ids
        return products

    def _get_analog_rollup_products(self):
        return self | self._get_primary_analog_products()

    def _is_analog_rollup_child(self):
        self.ensure_one()
        return bool(self.analog_source_line_ids.filtered('is_primary_link'))


class ProductAnalog(models.Model):
    _name = 'product.analog'
    _description = 'Product Analog'
    _order = 'sequence, id'
    _sql_constraints = [
        (
            'product_tmpl_product_uniq',
            'unique(product_tmpl_id, product_id)',
            'This analog is already linked to the product.',
        ),
    ]

    sequence = fields.Integer(default=10)
    product_tmpl_id = fields.Many2one(
        comodel_name='product.template',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Product',
        required=True,
        ondelete='cascade',
    )
    description = fields.Char(
        string='Description',
        related='product_id.name',
        readonly=True,
    )
    uom_id = fields.Many2one(
        comodel_name='uom.uom',
        string='Unit of Measure',
        related='product_id.uom_id',
        readonly=True,
    )
    reciprocal_line_id = fields.Many2one(
        comodel_name='product.analog',
        string='Reciprocal Analog Line',
        copy=False,
        ondelete='set null',
    )
    is_primary_link = fields.Boolean(
        string='Primary Link',
        default=True,
        copy=False,
    )

    @api.constrains('product_tmpl_id', 'product_id', 'is_primary_link')
    def _check_product_id(self):
        for line in self:
            if not line.product_tmpl_id or not line.product_id:
                continue
            if line.product_id.product_tmpl_id == line.product_tmpl_id:
                raise ValidationError(
                    _('A product cannot be selected as its own analog.')
                )
            if line.product_id.uom_id != line.product_tmpl_id.uom_id:
                raise ValidationError(
                    _(
                        'The analog "%(product)s" must use the same unit of measure '
                        'as the main product "%(main_product)s".'
                    )
                    % {
                        'product': line.product_id.display_name,
                        'main_product': line.product_tmpl_id.display_name,
                    }
                )
            if line.is_primary_link and len(line.product_tmpl_id.product_variant_ids) != 1:
                raise ValidationError(
                    _(
                        'The product "%(product)s" has multiple variants. '
                        'Create analog links from a product template with exactly one variant.'
                    )
                    % {'product': line.product_tmpl_id.display_name}
                )

    def _recompute_product_analytic_demand_comments(self, products):
        if products:
            self.env['product.analytic'].search(
                [('product_id', 'in', products.ids)]
            )._compute_demand_comment()

    @api.model
    def _get_single_variant(self, product_tmpl, raise_on_ambiguous=True):
        variants = product_tmpl.product_variant_ids
        if len(variants) == 1:
            return variants
        if raise_on_ambiguous:
            raise ValidationError(
                _(
                    'The product "%(product)s" has multiple variants. '
                    'Create analog links from a product template with exactly one variant.'
                )
                % {'product': product_tmpl.display_name}
            )
        return self.env['product.product']

    @api.model
    def _find_line(self, product_tmpl_id, product_id, exclude_ids=None):
        domain = [
            ('product_tmpl_id', '=', product_tmpl_id),
            ('product_id', '=', product_id),
        ]
        if exclude_ids:
            domain.append(('id', 'not in', exclude_ids))
        return self.search(domain, limit=1)

    def _find_reciprocal_line(self, raise_on_ambiguous=True):
        self.ensure_one()
        main_product = self._get_single_variant(
            self.product_tmpl_id,
            raise_on_ambiguous=raise_on_ambiguous,
        )
        if not main_product:
            return self.env['product.analog']
        return self._find_line(
            self.product_id.product_tmpl_id.id,
            main_product.id,
            exclude_ids=self.ids,
        )

    def _sync_reciprocal_line(self, raise_on_ambiguous=True):
        self.ensure_one()
        main_product = self._get_single_variant(
            self.product_tmpl_id,
            raise_on_ambiguous=raise_on_ambiguous,
        )
        if not main_product:
            return self.env['product.analog']
        reciprocal_line = self._find_line(
            self.product_id.product_tmpl_id.id,
            main_product.id,
            exclude_ids=self.ids,
        )
        reciprocal_vals = {
            'product_tmpl_id': self.product_id.product_tmpl_id.id,
            'product_id': main_product.id,
            'sequence': self.sequence,
            'reciprocal_line_id': self.id,
            'is_primary_link': False,
        }
        if reciprocal_line:
            reciprocal_line.with_context(
                product_alternatives_skip_reciprocal_sync=True
            ).write(reciprocal_vals)
        else:
            reciprocal_line = self.with_context(
                product_alternatives_skip_reciprocal_sync=True
            ).create([reciprocal_vals])
        if self.reciprocal_line_id != reciprocal_line:
            self.with_context(product_alternatives_skip_reciprocal_sync=True).write(
                {'reciprocal_line_id': reciprocal_line.id}
            )
        return reciprocal_line

    def _get_demand_recompute_products(self):
        return self.filtered('is_primary_link').product_id

    def _get_product_analytic_recompute_products(self):
        products = self.env['product.product']
        for line in self:
            products |= line.product_id
            products |= line.product_tmpl_id.product_variant_ids
        return products

    def _recompute_product_analytics(self, products):
        product_analytics = self.env['product.analytic'].search(
            [('product_id', 'in', products.ids)]
        )
        if not product_analytics:
            return
        product_analytics._compute_demand_comment()
        product_analytics._compute_numbers()
        product_analytics._compute_qty_received()
        product_analytics._compute_account_move_ids()
        product_analytics._compute_ua_purchase_contract_ids()

    @api.model
    def _get_existing_or_create_link(self, vals):
        product_tmpl_id = vals.get('product_tmpl_id')
        product_id = vals.get('product_id')
        exact_line = self._find_line(product_tmpl_id, product_id)
        if exact_line:
            if not exact_line.reciprocal_line_id and exact_line.is_primary_link:
                exact_line._sync_reciprocal_line()
            return exact_line

        product_tmpl = self.env['product.template'].browse(product_tmpl_id)
        product = self.env['product.product'].browse(product_id)
        main_product = self._get_single_variant(product_tmpl)
        reciprocal_line = self._find_line(product.product_tmpl_id.id, main_product.id)
        if reciprocal_line:
            if not reciprocal_line.reciprocal_line_id:
                reciprocal_line._sync_reciprocal_line()
            return reciprocal_line.reciprocal_line_id

        create_vals = dict(vals)
        create_vals.setdefault('is_primary_link', True)
        record = super(ProductAnalog, self).create([create_vals])
        record._sync_reciprocal_line()
        return record

    def _check_no_existing_pair_on_write(self, vals):
        for line in self:
            product_tmpl_id = vals.get('product_tmpl_id', line.product_tmpl_id.id)
            product_id = vals.get('product_id', line.product_id.id)
            excluded_ids = (line | line.reciprocal_line_id).ids
            exact_line = self._find_line(
                product_tmpl_id,
                product_id,
                exclude_ids=excluded_ids,
            )
            if exact_line:
                raise ValidationError(
                    _('This analog is already linked to the product.')
                )
            product_tmpl = self.env['product.template'].browse(product_tmpl_id)
            product = self.env['product.product'].browse(product_id)
            main_product = self._get_single_variant(product_tmpl)
            reciprocal_line = self._find_line(
                product.product_tmpl_id.id,
                main_product.id,
                exclude_ids=excluded_ids,
            )
            if reciprocal_line:
                raise ValidationError(
                    _('This analog pair is already linked to the product.')
                )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('product_alternatives_skip_reciprocal_sync'):
            return super().create(vals_list)

        records = self.browse()
        products_to_recompute = self.env['product.product']
        for vals in vals_list:
            record = self._get_existing_or_create_link(vals)
            records |= record
            products_to_recompute |= (
                record | record.reciprocal_line_id
            )._get_product_analytic_recompute_products()
        self._recompute_product_analytics(products_to_recompute)
        return records

    def write(self, vals):
        if self.env.context.get('product_alternatives_skip_reciprocal_sync'):
            return super().write(vals)

        related_lines = self | self.mapped('reciprocal_line_id')
        products_to_recompute = related_lines._get_product_analytic_recompute_products()
        link_changed = bool({'product_tmpl_id', 'product_id'} & set(vals))
        old_reciprocal_lines = self.mapped('reciprocal_line_id')
        write_vals = dict(vals)
        if link_changed:
            self._check_no_existing_pair_on_write(vals)
            write_vals.setdefault('is_primary_link', True)
            write_vals['reciprocal_line_id'] = False
            (old_reciprocal_lines - self).with_context(
                product_alternatives_skip_reciprocal_sync=True
            ).unlink()
        res = super().write(write_vals)
        if link_changed:
            for line in self:
                line._sync_reciprocal_line()
        elif 'sequence' in vals:
            for line in self.filtered('reciprocal_line_id'):
                line.reciprocal_line_id.with_context(
                    product_alternatives_skip_reciprocal_sync=True
                ).write({'sequence': line.sequence})
        products_to_recompute |= (
            self | self.mapped('reciprocal_line_id')
        )._get_product_analytic_recompute_products()
        self._recompute_product_analytics(products_to_recompute)
        return res

    def unlink(self):
        if self.env.context.get('product_alternatives_skip_reciprocal_sync'):
            return super().unlink()

        related_lines = self | self.mapped('reciprocal_line_id')
        products = related_lines._get_product_analytic_recompute_products()
        (self.mapped('reciprocal_line_id') - self).with_context(
            product_alternatives_skip_reciprocal_sync=True
        ).unlink()
        res = super().unlink()
        self._recompute_product_analytics(products)
        return res

    @api.model
    def _backfill_reciprocal_links(self):
        self.search(
            [
                ('reciprocal_line_id', '=', False),
                ('is_primary_link', '=', False),
            ]
        ).with_context(product_alternatives_skip_reciprocal_sync=True).write(
            {'is_primary_link': True}
        )
        products_to_recompute = self.env['product.product']
        for line in self.search([], order='id'):
            if not line.exists() or line.reciprocal_line_id:
                continue
            reciprocal_line = line._find_reciprocal_line(raise_on_ambiguous=False)
            if reciprocal_line:
                primary_line = line
                secondary_line = reciprocal_line
                if not primary_line.is_primary_link and secondary_line.is_primary_link:
                    primary_line, secondary_line = secondary_line, primary_line
                elif primary_line.is_primary_link == secondary_line.is_primary_link:
                    primary_line, secondary_line = sorted(
                        primary_line | secondary_line,
                        key=lambda record: record.id,
                    )
                primary_line.with_context(
                    product_alternatives_skip_reciprocal_sync=True
                ).write(
                    {
                        'is_primary_link': True,
                        'reciprocal_line_id': secondary_line.id,
                    }
                )
                secondary_line.with_context(
                    product_alternatives_skip_reciprocal_sync=True
                ).write(
                    {
                        'is_primary_link': False,
                        'reciprocal_line_id': primary_line.id,
                    }
                )
                products_to_recompute |= (
                    primary_line | secondary_line
                )._get_product_analytic_recompute_products()
                continue
            if line.is_primary_link:
                reciprocal_line = line._sync_reciprocal_line(
                    raise_on_ambiguous=False
                )
                products_to_recompute |= (
                    line | reciprocal_line
                )._get_product_analytic_recompute_products()
        self._recompute_product_analytics(products_to_recompute)


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    demand_comment = fields.Char(
        compute='_compute_demand_comment',
        store=True,
        group_operator='max',
        string='Comment',
    )

    @api.depends(
        'comment',
        'product_id',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
    )
    def _compute_demand_comment(self):
        for product_analytic in self:
            comment = product_analytic.comment or ''
            if product_analytic.product_id.analog_source_line_ids:
                product_analytic.demand_comment = (
                    f'{comment} (A)' if comment else '(A)'
                )
            else:
                product_analytic.demand_comment = comment

    def _is_analog_rollup_child(self):
        self.ensure_one()
        return self.product_id._is_analog_rollup_child()

    def _get_direct_rollup_products(self):
        self.ensure_one()
        if self._is_analog_rollup_child():
            return self.env['product.product']
        return self.product_id._get_analog_rollup_products()

    def _get_invoice_kit_parent_boms(self):
        self.ensure_one()
        if self._is_analog_rollup_child():
            return self.env['mrp.bom']
        return super()._get_invoice_kit_parent_boms()

    def _get_kit_products_for_rollup(self):
        self.ensure_one()
        parent_kit_boms = self._get_invoice_kit_parent_boms()
        return (
            parent_kit_boms.product_id
            + parent_kit_boms.product_tmpl_id.product_variant_ids
        )

    def _get_related_invoice_move_lines(self):
        self.ensure_one()
        products = (
            self._get_direct_rollup_products()
            + self._get_kit_products_for_rollup()._get_analog_rollup_products()
        )
        if not products:
            return self.env['account.move.line']
        invoice_lines = self.env['account.move.line'].search(
            [
                ('product_id', 'in', products.ids),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['in_invoice', 'in_refund']),
            ]
        )
        return invoice_lines.filtered(
            lambda line: self._has_related_sale_contract(line)
        )

    def _get_related_purchase_contract_lines(self):
        self.ensure_one()
        products = (
            self._get_direct_rollup_products()
            + self._get_kit_products_for_rollup()._get_analog_rollup_products()
        )
        if not products:
            return self.env['purchase.order.line']
        purchase_line_model = self.env['purchase.order.line']
        domain = [
            ('product_id', 'in', products.ids),
            ('order_id.state', 'in', ['purchase', 'done']),
            ('order_id.ua_contract_id', '!=', False),
        ]
        if 'seller_contract_id' in purchase_line_model._fields:
            domain.append(('seller_contract_id', '=', self.sale_contract_id.id))
        else:
            domain.append(('order_id.seller_contract_id', '=', self.sale_contract_id.id))
        return purchase_line_model.search(domain)

    def _sum_invoice_quantity_for_products(self, products, target_uom):
        self.ensure_one()
        if not products:
            return 0
        total_quantity = 0
        product_ids = set(products.ids)
        seller_lines = self.sale_contract_id.seller_move_line_ids.filtered(
            lambda line: line.move_id.state == 'posted'
            and line.move_type in ('in_invoice', 'in_refund')
            and line.product_id.id in product_ids
        )
        for line in seller_lines:
            quantity = line.product_uom_id._compute_quantity(
                line.quantity,
                target_uom,
            )
            total_quantity += quantity if line.move_type == 'in_invoice' else -quantity
        return total_quantity

    def _sum_purchase_quantity_for_products(self, products, target_uom, quantity_field):
        self.ensure_one()
        if not products:
            return 0
        total_quantity = 0
        product_ids = set(products.ids)
        purchase_lines = self.sale_contract_id.seller_purchase_line_ids.filtered(
            lambda line: line.product_id.id in product_ids
        )
        for line in purchase_lines:
            total_quantity += line.product_uom._compute_quantity(
                line[quantity_field],
                target_uom,
            )
        return total_quantity

    def _sum_kit_invoice_quantity(self):
        self.ensure_one()
        total_quantity = 0
        for bom in self.kit_bom_ids:
            for product in bom.product_id + bom.product_tmpl_id.product_variant_ids:
                kit_products = product._get_analog_rollup_products()
                kit_total_in_invoice = self._sum_invoice_quantity_for_products(
                    kit_products,
                    product.uom_id,
                )
                need_bom_lines = bom.bom_line_ids.filtered(
                    lambda line: line.product_id == self.product_id
                )
                bom_lines_uom_qty = 0
                for bom_line in need_bom_lines:
                    bom_lines_uom_qty += bom_line.product_uom_id._compute_quantity(
                        bom_line.product_qty,
                        bom_line.product_id.uom_id,
                    )
                total_quantity += kit_total_in_invoice * bom_lines_uom_qty
        return total_quantity

    def _sum_kit_received_quantity(self):
        self.ensure_one()
        total_quantity = 0
        for bom in self.kit_bom_ids:
            for product in bom.product_id + bom.product_tmpl_id.product_variant_ids:
                kit_products = product._get_analog_rollup_products()
                kit_total_received = self._sum_purchase_quantity_for_products(
                    kit_products,
                    product.uom_id,
                    'product_qty',
                )
                need_bom_lines = bom.bom_line_ids.filtered(
                    lambda line: line.product_id == self.product_id
                )
                bom_lines_uom_qty = 0
                for bom_line in need_bom_lines:
                    bom_lines_uom_qty += (
                        bom_line.product_uom_id._compute_quantity(
                            bom_line.product_qty,
                            bom_line.product_id.uom_id,
                        )
                        / bom_line.bom_id.product_qty
                    )
                total_quantity += kit_total_received * bom_lines_uom_qty
        return total_quantity

    @api.depends(
        'sale_contract_id.seller_move_line_ids',
        'sale_contract_id.seller_move_line_ids.product_id',
        'sale_contract_id.seller_move_line_ids.quantity',
        'sale_contract_id.seller_move_line_ids.analytic_distribution',
        'sale_contract_id.seller_move_line_ids.seller_contract_id',
        'sale_contract_id.seller_move_line_ids.product_uom_id',
        'sale_contract_id.seller_move_line_ids.move_type',
        'sale_contract_id.seller_move_line_ids.move_id.state',
        'sale_contract_id.seller_move_line_ids.move_id.move_type',
        'sale_contract_id.seller_move_line_ids.move_id.seller_contract_id',
        'need_to_purchase_ids',
        'need_to_purchase_ids.product_qty',
        'kit_bom_ids',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
    )
    def _compute_numbers(self):
        for product_analytic in self:
            if product_analytic._is_analog_rollup_child():
                product_analytic.demand = 0
                product_analytic.in_invoice = 0
                product_analytic.closed = 0
                continue

            product_analytic.demand = sum(
                product_analytic.need_to_purchase_ids.mapped('product_qty')
            )
            total_in_invoice = product_analytic._sum_invoice_quantity_for_products(
                product_analytic._get_direct_rollup_products(),
                product_analytic.product_id.uom_id,
            )
            total_in_invoice += product_analytic._sum_kit_invoice_quantity()
            product_analytic.in_invoice = total_in_invoice
            product_analytic.closed = (
                product_analytic.in_invoice / product_analytic.demand
                if product_analytic.demand
                else 0
            )

    @api.depends(
        'sale_contract_id.seller_purchase_line_ids',
        'sale_contract_id.seller_purchase_line_ids.seller_contract_id',
        'sale_contract_id.seller_purchase_line_ids.qty_received',
        'sale_contract_id.seller_purchase_line_ids.product_qty',
        'sale_contract_id.seller_purchase_line_ids.product_uom',
        'sale_contract_id.seller_purchase_line_ids.product_id',
        'kit_bom_ids',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
    )
    def _compute_qty_received(self):
        for product_analytic in self:
            if product_analytic._is_analog_rollup_child():
                product_analytic.qty_received = 0
                continue

            total_qty_received = product_analytic._sum_purchase_quantity_for_products(
                product_analytic._get_direct_rollup_products(),
                product_analytic.product_id.uom_id,
                'qty_received',
            )
            total_qty_received += product_analytic._sum_kit_received_quantity()
            product_analytic.qty_received = total_qty_received

    def _recompute_analog_invoice_fields(self):
        if not self:
            return
        self._compute_numbers()
        self._compute_account_move_ids()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        res = super().action_post()
        self.line_ids._recompute_analog_product_analytics()
        return res

    def button_draft(self):
        analytics = self.line_ids._get_analog_product_analytic_recompute_targets()
        res = super().button_draft()
        (
            analytics
            | self.line_ids._get_analog_product_analytic_recompute_targets()
        )._recompute_analog_invoice_fields()
        return res


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _get_analog_product_analytic_recompute_targets(self):
        contracts = self.mapped('seller_contract_id')
        if 'seller_contract_id' in self.env['account.move']._fields:
            contracts |= self.mapped('move_id.seller_contract_id')
        return contracts.mapped('product_analytic_ids')

    def _recompute_analog_product_analytics(self):
        product_analytics = self._get_analog_product_analytic_recompute_targets()
        product_analytics._recompute_analog_invoice_fields()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._recompute_analog_product_analytics()
        return lines

    def write(self, vals):
        analytics = self._get_analog_product_analytic_recompute_targets()
        res = super().write(vals)
        (
            analytics | self._get_analog_product_analytic_recompute_targets()
        )._recompute_analog_invoice_fields()
        return res

    def unlink(self):
        analytics = self._get_analog_product_analytic_recompute_targets()
        res = super().unlink()
        analytics._recompute_analog_invoice_fields()
        return res


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    analog_marker = fields.Text(
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_product_names = fields.Text(
        compute='_compute_analog_products',
        string='Analog Products',
    )

    @api.depends(
        'bom_line_ids.product_id',
        'bom_line_ids.product_id.product_tmpl_id.analog_line_ids.product_id',
    )
    def _compute_analog_products(self):
        for bom in self:
            analog_pairs = []
            for line in bom.bom_line_ids:
                analog_pairs.extend(line._get_analog_product_pairs())
            analog_product_names = '\n'.join(
                f'{component}\t{analog}'
                for component, analog in analog_pairs
            )
            bom.analog_marker = f'(A)\n{analog_product_names}' if analog_pairs else ''
            bom.analog_product_names = analog_product_names

    def get_analog_product_names(self):
        self.ensure_one()
        return self.analog_product_names.split('\n') if self.analog_product_names else []


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    analog_product_ids = fields.Many2many(
        comodel_name='product.product',
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_marker = fields.Text(
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_product_names = fields.Text(
        compute='_compute_analog_products',
        string='Analog Products',
    )

    @api.depends('product_id', 'product_id.product_tmpl_id.analog_line_ids.product_id')
    def _compute_analog_products(self):
        for line in self:
            analog_products = line.product_id.product_tmpl_id.analog_line_ids.product_id
            analog_product_names = '\n'.join(
                f'{component}\t{analog}'
                for component, analog in line._get_analog_product_pairs()
            )
            line.analog_product_ids = analog_products
            line.analog_marker = (
                f'(A)\n{analog_product_names}' if analog_product_names else ''
            )
            line.analog_product_names = analog_product_names

    def _get_analog_product_pairs(self):
        self.ensure_one()
        component_name = self.product_id.display_name
        return [
            (component_name, analog.display_name)
            for analog in self.product_id.product_tmpl_id.analog_line_ids.product_id
        ]

    def get_analog_product_names(self):
        self.ensure_one()
        return self.analog_product_names.split('\n') if self.analog_product_names else []


class StockMove(models.Model):
    _inherit = 'stock.move'

    analog_product_ids = fields.Many2many(
        comodel_name='product.product',
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_marker = fields.Text(
        compute='_compute_analog_products',
        string='Analogs',
    )
    analog_product_data = fields.Json(
        compute='_compute_analog_products',
        string='Analog Products',
    )
    raw_material_production_state = fields.Selection(
        related='raw_material_production_id.state',
        string='Manufacturing Order State',
    )

    @api.depends(
        'product_id',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
    )
    def _compute_analog_products(self):
        for move in self:
            analog_products = move.product_id.product_tmpl_id.analog_line_ids.product_id
            move.analog_product_ids = analog_products
            move.analog_marker = '(A)' if analog_products else ''
            move.analog_product_data = [
                {
                    'id': product.id,
                    'display_name': product.display_name,
                }
                for product in analog_products
            ]

    def action_replace_with_analog_product(self, analog_product_id):
        self.ensure_one()
        if self.raw_material_production_state in ('done', 'cancel'):
            raise UserError(
                _('You cannot replace components on a done or cancelled manufacturing order.')
            )

        analog_product = self.env['product.product'].browse(analog_product_id).exists()
        if not analog_product:
            raise UserError(_('The selected analog product no longer exists.'))
        if analog_product not in self.analog_product_ids:
            raise ValidationError(
                _('The selected product is not an allowed analog for this component.')
            )
        if analog_product.uom_id != self.product_id.uom_id:
            raise ValidationError(
                _('The selected analog must use the same unit of measure.')
            )

        should_reassign = self.state in {'confirmed', 'partially_available', 'assigned'}
        if self.move_line_ids:
            self._do_unreserve()
        self.write(
            {
                'product_id': analog_product.id,
                'product_uom': analog_product.uom_id.id,
            }
        )
        if should_reassign and self.state not in {'done', 'cancel'}:
            self._action_assign()
        self.invalidate_recordset(
            [
                'analog_marker',
                'analog_product_data',
                'analog_product_ids',
            ]
        )
        return {
            'product_id': self.product_id.id,
            'product_display_name': self.product_id.display_name,
            'product_uom_id': self.product_uom.id,
            'product_uom_name': self.product_uom.display_name,
            'analog_marker': self.analog_marker,
            'analog_product_data': self.analog_product_data,
        }
