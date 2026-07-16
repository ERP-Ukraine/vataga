import logging

from psycopg2 import IntegrityError, errorcodes
from psycopg2.errors import SerializationFailure

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class RetryableProductAnalyticCreationError(SerializationFailure):
    @property
    def pgcode(self):
        return errorcodes.SERIALIZATION_FAILURE


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
    analog_list_marker = fields.Char(
        compute='_compute_analog_list_marker',
        store=True,
        string='Аналоги',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id

    @api.depends(
        'analog_line_ids',
        'analog_line_ids.product_id',
        'product_variant_ids.analog_any_source_line_ids',
        'product_variant_ids.analog_any_source_line_ids.product_tmpl_id',
    )
    def _compute_analog_list_marker(self):
        for product in self:
            product.analog_list_marker = (
                '(A)'
                if product.analog_line_ids
                or product.product_variant_ids.mapped('analog_any_source_line_ids')
                else ''
            )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    analog_any_source_line_ids = fields.One2many(
        comodel_name='product.analog',
        inverse_name='product_id',
        string='Used in Analog Links',
    )
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
    analog_list_marker = fields.Char(
        compute='_compute_analog_list_marker',
        store=True,
        string='Аналоги',
    )

    @api.depends('uom_id')
    def _compute_analog_uom_id(self):
        for product in self:
            product.analog_uom_id = product.uom_id

    @api.depends(
        'analog_any_source_line_ids',
        'analog_any_source_line_ids.product_tmpl_id',
        'product_tmpl_id.analog_line_ids',
        'product_tmpl_id.analog_line_ids.product_id',
    )
    def _compute_analog_list_marker(self):
        for product in self:
            product.analog_list_marker = (
                '(A)'
                if product.analog_any_source_line_ids
                or product.product_tmpl_id.analog_line_ids
                else ''
            )

    def _get_primary_analog_products(self):
        return self.mapped('product_tmpl_id.analog_line_ids').filtered(
            'is_primary_link'
        ).product_id

    def _get_primary_analog_main_products(self):
        products = self.env['product.product']
        for line in self.mapped('analog_source_line_ids').filtered('is_primary_link'):
            products |= line.product_tmpl_id.product_variant_ids
        return products

    def _get_single_primary_analog_main_product(self):
        self.ensure_one()
        main_products = self._get_primary_analog_main_products()
        return main_products if len(main_products) == 1 else self.env['product.product']

    def _get_direct_analog_counterpart_products(self):
        products = self.env['product.product']
        for product in self:
            products |= (
                product._get_primary_analog_products()
                | product._get_primary_analog_main_products()
            )
        return products - self

    def _get_allowed_analog_rollup_target_products(self):
        if not self:
            return self.env['product.product']
        self.ensure_one()
        counterpart_products = self._get_direct_analog_counterpart_products()
        if not counterpart_products:
            return counterpart_products
        return self | counterpart_products

    def _get_bidirectional_analog_group_products(self):
        products = self.env['product.product']
        line_model = self.env['product.analog']
        for product in self:
            group_products = product
            previous_ids = set()
            while set(group_products.ids) != previous_ids:
                previous_ids = set(group_products.ids)
                analog_lines = line_model.search(
                    [
                        '|',
                        ('product_tmpl_id', 'in', group_products.product_tmpl_id.ids),
                        ('product_id', 'in', group_products.ids),
                    ]
                )
                group_products |= (
                    analog_lines.product_id
                    | analog_lines.mapped('product_tmpl_id.product_variant_ids')
                )
            products |= group_products
        return products

    def _get_selected_analog_rollup_target_product(self, selected_product):
        if not self:
            return self.env['product.product']
        self.ensure_one()
        if (
            selected_product
            and selected_product in self._get_allowed_analog_rollup_target_products()
        ):
            return selected_product
        return self.env['product.product']

    def _get_default_analog_counterpart_product(self):
        if not self:
            return self.env['product.product']
        self.ensure_one()
        if self._get_primary_analog_products():
            return self.env['product.product']
        main_products = self._get_primary_analog_main_products()
        return main_products if len(main_products) == 1 else self.env['product.product']

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
            self.env['product.analytic'].sudo().search(
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
        product_analytics = self.env['product.analytic'].sudo().search(
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


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _check_required_analog_original_products(self):
        for order in self:
            missing_lines = order.order_line.filtered(
                lambda line: line._requires_analog_original_product()
                and not line.analog_original_product_id
            )
            if missing_lines:
                raise ValidationError(
                    _('Для товару-аналога необхідно вибрати оригінал аналога.')
                )

    def button_confirm(self):
        self._check_required_analog_original_products()
        res = super().button_confirm()
        self.order_line._ensure_analog_rollup_target_product_analytics()
        self.order_line._recompute_analog_product_analytics()
        return res

    def button_approve(self, force=False):
        self._check_required_analog_original_products()
        res = super().button_approve(force=force)
        self.order_line._ensure_analog_rollup_target_product_analytics()
        self.order_line._recompute_analog_product_analytics()
        return res

    def write(self, vals):
        should_recompute = 'seller_contract_id' in vals
        old_analytics = (
            self.filtered(
                lambda order: order.state in ('purchase', 'done')
            ).order_line._get_analog_product_analytic_recompute_targets()
            if should_recompute
            else self.env['product.analytic']
        )
        res = super().write(vals)
        if should_recompute:
            confirmed_lines = self.filtered(
                lambda order: order.state in ('purchase', 'done')
            ).order_line
            confirmed_lines._ensure_analog_rollup_target_product_analytics()
            new_analytics = (
                confirmed_lines._get_analog_product_analytic_recompute_targets()
            )
            analytics = old_analytics | new_analytics
            analytics._recompute_analog_rollup_fields()
            analytics._compute_ua_purchase_contract_ids()
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    ANALOG_RECOMPUTE_FIELDS = {
        'product_id',
        'analog_original_product_id',
        'analytic_distribution',
        'seller_contract_id',
        'order_id',
    }

    analog_original_product_ids = fields.Many2many(
        comodel_name='product.product',
        compute='_compute_analog_original_product_fields',
        string='Analog Original Options',
    )
    analog_original_product_id = fields.Many2one(
        comodel_name='product.product',
        string='Оригінал аналога',
        domain="[('id', 'in', analog_original_product_ids)]",
        copy=False,
    )
    has_analog_original_options = fields.Boolean(
        compute='_compute_analog_original_product_fields',
    )
    has_multiple_analog_original_options = fields.Boolean(
        compute='_compute_analog_original_product_fields',
    )

    @api.depends(
        'product_id',
        'product_id.product_tmpl_id.analog_line_ids',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
        'product_id.analog_source_line_ids.product_tmpl_id',
    )
    def _compute_analog_original_product_fields(self):
        for line in self:
            original_products = (
                line.product_id._get_allowed_analog_rollup_target_products()
            )
            line.analog_original_product_ids = original_products
            line.has_analog_original_options = bool(original_products)
            line.has_multiple_analog_original_options = len(original_products) > 1

    def _get_allowed_analog_original_products(self):
        self.ensure_one()
        return self.product_id._get_allowed_analog_rollup_target_products()

    def _requires_analog_original_product(self):
        self.ensure_one()
        return len(self.product_id._get_primary_analog_main_products()) > 1

    def _get_analog_original_product_for_rollup(self):
        self.ensure_one()
        return self.product_id._get_selected_analog_rollup_target_product(
            self.analog_original_product_id
        )

    def _get_default_analog_original_product(self):
        self.ensure_one()
        return self.product_id._get_default_analog_counterpart_product()

    def _get_analytic_distribution_account_ids(self):
        analytic_ids = set()
        for line in self:
            for key in (line.analytic_distribution or {}):
                for analytic_id in str(key).split(','):
                    analytic_id = analytic_id.strip()
                    if analytic_id.isdigit():
                        analytic_ids.add(int(analytic_id))
        return analytic_ids

    def _get_seller_contracts_from_analytic_distribution(self):
        analytic_ids = self._get_analytic_distribution_account_ids()
        if not analytic_ids:
            return self.env['account.analytic.account']
        return self.env['account.analytic.account'].search(
            [
                ('id', 'in', list(analytic_ids)),
                ('is_plan_seller_contract', '=', True),
            ]
        )

    def _get_demand_report_seller_contracts(self):
        contracts = (
            self.mapped('seller_contract_id')
            | self._get_seller_contracts_from_analytic_distribution()
            | self.mapped('order_id.seller_contract_id')
        )
        return contracts

    def _has_demand_report_seller_contract(self, contract):
        self.ensure_one()
        return bool(
            contract
            and (
                self.seller_contract_id == contract
                or self.order_id.seller_contract_id == contract
                or contract.id in self._get_analytic_distribution_account_ids()
            )
        )

    def _get_analog_rollup_product_analytic_pairs(self):
        target_pairs = set()
        affected_pairs = set()
        distribution_contract_ids = set(
            self._get_seller_contracts_from_analytic_distribution().ids
        )
        for line in self:
            target_product = line._get_analog_original_product_for_rollup()
            contract_ids = set(
                (line.seller_contract_id | line.order_id.seller_contract_id).ids
            )
            contract_ids.update(
                line._get_analytic_distribution_account_ids()
                & distribution_contract_ids
            )
            for contract_id in contract_ids:
                affected_pairs.add((line.product_id.id, contract_id))
                if not target_product:
                    continue
                target_pair = (target_product.id, contract_id)
                target_pairs.add(target_pair)
                affected_pairs.add(target_pair)
        return target_pairs, affected_pairs

    def _ensure_analog_rollup_target_product_analytics(self):
        target_pairs, _affected_pairs = (
            self._get_analog_rollup_product_analytic_pairs()
        )
        return self.env['product.analytic']._ensure_analog_rollup_pairs(
            target_pairs
        )[0]

    @api.onchange('product_id')
    def _onchange_product_id_analog_original_product_id(self):
        for line in self:
            line._set_default_analog_original_product()

    def _set_default_analog_original_product(self):
        self.ensure_one()
        allowed_products = self._get_allowed_analog_original_products()
        default_product = self._get_default_analog_original_product()
        if not allowed_products:
            self.analog_original_product_id = False
        elif (
            self.analog_original_product_id
            and self.analog_original_product_id in allowed_products
        ):
            return
        elif default_product:
            self.analog_original_product_id = default_product
        elif len(allowed_products) == 1:
            self.analog_original_product_id = False
        else:
            self.analog_original_product_id = False

    @api.model
    def _prepare_analog_original_product_vals(
        self,
        vals,
        current_selected_product=False,
    ):
        vals = dict(vals)
        product_id = vals.get('product_id')
        if not product_id:
            return vals

        product = self.env['product.product'].browse(product_id)
        allowed_products = product._get_allowed_analog_rollup_target_products()
        selected_id = vals.get('analog_original_product_id')
        if (
            'analog_original_product_id' not in vals
            and current_selected_product
        ):
            selected_id = current_selected_product.id
        if not allowed_products:
            vals['analog_original_product_id'] = False
        elif selected_id and selected_id in allowed_products.ids:
            vals['analog_original_product_id'] = selected_id
        else:
            default_product = product._get_default_analog_counterpart_product()
            vals['analog_original_product_id'] = default_product.id or False
        return vals

    def _get_analog_sync_target_product(self):
        self.ensure_one()
        allowed_products = self._get_allowed_analog_original_products()
        if not allowed_products:
            return self.env['product.product']
        if (
            self.analog_original_product_id
            and self.analog_original_product_id in allowed_products
        ):
            return self.analog_original_product_id
        return self._get_default_analog_original_product()

    def _get_analog_product_analytic_recompute_targets(self):
        confirmed_lines = self.filtered(
            lambda line: line.order_id.state in ('purchase', 'done')
        )
        _target_pairs, affected_pairs = (
            confirmed_lines._get_analog_rollup_product_analytic_pairs()
        )
        return self.env[
            'product.analytic'
        ]._find_analog_rollup_product_analytics(affected_pairs)

    def _recompute_analog_product_analytics(self):
        analytics = self._get_analog_product_analytic_recompute_targets()
        analytics._recompute_analog_rollup_fields()
        analytics._compute_ua_purchase_contract_ids()
        return analytics

    def _sync_analog_original_product(self):
        if self.env.context.get('product_alternatives_skip_analog_origin_sync'):
            return
        for line in self:
            target_product = line._get_analog_sync_target_product()

            if line.analog_original_product_id != target_product:
                line.with_context(
                    product_alternatives_skip_analog_origin_sync=True
                ).write({'analog_original_product_id': target_product.id or False})

    @api.constrains('product_id', 'analog_original_product_id')
    def _check_analog_original_product_id(self):
        for line in self:
            if not line.analog_original_product_id:
                continue
            if line.analog_original_product_id not in (
                line._get_allowed_analog_original_products()
            ):
                raise ValidationError(
                    _('Обраний оригінал аналога не відповідає товару рядка.')
                )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [
            self._prepare_analog_original_product_vals(vals)
            for vals in vals_list
        ]
        lines = super().create(vals_list)
        confirmed_lines = lines.filtered(
            lambda line: line.order_id.state in ('purchase', 'done')
        )
        confirmed_lines._ensure_analog_rollup_target_product_analytics()
        confirmed_lines._recompute_analog_product_analytics()
        return lines

    def write(self, vals):
        if self.env.context.get('product_alternatives_skip_analog_origin_sync'):
            return super().write(vals)
        should_recompute = bool(self.ANALOG_RECOMPUTE_FIELDS & set(vals))
        old_analytics = (
            self._get_analog_product_analytic_recompute_targets()
            if should_recompute
            else self.env['product.analytic']
        )
        current_selected_product = self.env['product.product']
        if self and all(
            line.analog_original_product_id
            == self[0].analog_original_product_id
            for line in self
        ):
            current_selected_product = self[0].analog_original_product_id
        write_vals = (
            self._prepare_analog_original_product_vals(
                vals,
                current_selected_product=current_selected_product,
            )
            if 'product_id' in vals
            else vals
        )
        res = super().write(write_vals)
        if {'product_id', 'analog_original_product_id'} & set(vals):
            self._sync_analog_original_product()
        if should_recompute:
            confirmed_lines = self.filtered(
                lambda line: line.order_id.state in ('purchase', 'done')
            )
            confirmed_lines._ensure_analog_rollup_target_product_analytics()
            new_analytics = (
                confirmed_lines._get_analog_product_analytic_recompute_targets()
            )
            analytics = old_analytics | new_analytics
            analytics._recompute_analog_rollup_fields()
            analytics._compute_ua_purchase_contract_ids()
        return res

    def unlink(self):
        _target_pairs, affected_pairs = (
            self._get_analog_rollup_product_analytic_pairs()
        )
        analytics = self.env[
            'product.analytic'
        ]._find_analog_rollup_product_analytics(affected_pairs)
        res = super().unlink()
        analytics._recompute_analog_rollup_fields()
        analytics._compute_ua_purchase_contract_ids()
        return res


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    demand_comment = fields.Char(
        compute='_compute_demand_comment',
        compute_sudo=True,
        store=True,
        group_operator='max',
        string='Comment',
    )

    @api.model
    def _find_analog_rollup_product_analytics(self, pair_keys):
        pair_keys = {
            (int(product_id), int(contract_id))
            for product_id, contract_id in pair_keys
            if product_id and contract_id
        }
        if not pair_keys:
            return self.browse()

        product_analytics = self.sudo().search(
            [
                ('product_id', 'in', list({key[0] for key in pair_keys})),
                ('sale_contract_id', 'in', list({key[1] for key in pair_keys})),
            ]
        )
        return product_analytics.filtered(
            lambda analytic: (
                analytic.product_id.id,
                analytic.sale_contract_id.id,
            )
            in pair_keys
        )

    @api.model
    def _ensure_analog_rollup_pairs(self, pair_keys):
        """Return matching analytics and the subset created by this call.

        The savepoint handles a concurrent transaction creating the same pair
        after our lookup but before our insert.  After the conflicting insert
        rolls back to the savepoint, the winning row is fetched and reused.
        """
        pair_keys = {
            (int(product_id), int(contract_id))
            for product_id, contract_id in pair_keys
            if product_id and contract_id
        }
        existing = self._find_analog_rollup_product_analytics(pair_keys)
        existing_keys = {
            (analytic.product_id.id, analytic.sale_contract_id.id)
            for analytic in existing
        }
        created = self.browse()
        product_analytics = existing
        ProductAnalytic = self.sudo()
        for product_id, contract_id in sorted(pair_keys - existing_keys):
            analytic = ProductAnalytic.search(
                [
                    ('product_id', '=', product_id),
                    ('sale_contract_id', '=', contract_id),
                ],
                limit=1,
            )
            if not analytic:
                try:
                    with self.env.cr.savepoint():
                        analytic = ProductAnalytic.create(
                            {
                                'product_id': product_id,
                                'sale_contract_id': contract_id,
                            }
                        )
                    created |= analytic
                except IntegrityError as error:
                    if (
                        error.pgcode != errorcodes.UNIQUE_VIOLATION
                        or error.diag.constraint_name
                        != 'product_analytic_product_and_seller_uniq'
                    ):
                        raise
                    analytic = ProductAnalytic.search(
                        [
                            ('product_id', '=', product_id),
                            ('sale_contract_id', '=', contract_id),
                        ],
                        limit=1,
                    )
                    if not analytic:
                        # Odoo uses REPEATABLE READ.  A concurrently committed
                        # winner may therefore be enforced by the unique index
                        # but remain invisible to this transaction's snapshot.
                        # Convert that race to Odoo's standard retryable error
                        # instead of leaking a unique-constraint failure.
                        raise RetryableProductAnalyticCreationError(
                            'Concurrent product analytic pair creation; retry.'
                        ) from error
            product_analytics |= analytic
        return product_analytics, created

    def _recompute_analog_rollup_fields(self, batch_size=500):
        analytic_ids = sorted(set(self.ids))
        recomputed_count = 0
        for start in range(0, len(analytic_ids), batch_size):
            product_analytics = self.sudo().browse(
                analytic_ids[start:start + batch_size]
            ).exists()
            if not product_analytics:
                continue
            product_analytics._compute_numbers()
            product_analytics._compute_qty_received()
            product_analytics._compute_account_move_ids()
            product_analytics._compute_demand_comment()
            recomputed_count += len(product_analytics)
        return recomputed_count

    @api.model
    def _backfill_analog_rollup_product_analytics(
        self,
        batch_size=500,
        dry_run=False,
    ):
        target_pairs = set()
        affected_pairs = set()
        source_counts = {
            'invoice_line_count': 0,
            'purchase_line_count': 0,
        }
        sources = (
            (
                self.env['account.move.line'].sudo(),
                [
                    ('product_id', '!=', False),
                    ('move_id.state', '=', 'posted'),
                    ('move_id.move_type', 'in', ('in_invoice', 'in_refund')),
                ],
                'invoice_line_count',
            ),
            (
                self.env['purchase.order.line'].sudo(),
                [
                    ('product_id', '!=', False),
                    ('order_id.state', 'in', ('purchase', 'done')),
                ],
                'purchase_line_count',
            ),
        )
        for source_model, domain, count_key in sources:
            last_id = 0
            while True:
                lines = source_model.search(
                    domain + [('id', '>', last_id)],
                    order='id',
                    limit=batch_size,
                )
                if not lines:
                    break
                line_target_pairs, line_affected_pairs = (
                    lines._get_analog_rollup_product_analytic_pairs()
                )
                target_pairs.update(line_target_pairs)
                affected_pairs.update(line_affected_pairs)
                source_counts[count_key] += len(lines)
                last_id = lines[-1].id

        existing_targets = self._find_analog_rollup_product_analytics(
            target_pairs
        )
        existing_target_pairs = {
            (analytic.product_id.id, analytic.sale_contract_id.id)
            for analytic in existing_targets
        }
        missing_target_pairs = target_pairs - existing_target_pairs

        affected_analytics = self._find_analog_rollup_product_analytics(
            affected_pairs
        )
        stored_value_analytics = self.sudo().search(
            [
                '|',
                '|',
                '|',
                '|',
                '|',
                ('need_to_purchase_ids', '!=', False),
                ('demand', '!=', 0),
                ('in_invoice', '!=', 0),
                ('qty_received', '!=', 0),
                ('closed', '!=', 0),
                ('account_move_ids', '!=', False),
            ]
        )
        existing_to_recompute = affected_analytics | stored_value_analytics
        planned_recompute_count = (
            len(existing_to_recompute) + len(missing_target_pairs)
        )
        result = {
            **source_counts,
            'target_pair_count': len(target_pairs),
            'missing_target_count': len(missing_target_pairs),
            'would_create_count': len(missing_target_pairs),
            'would_recompute_count': planned_recompute_count,
            'created_count': 0,
            'recomputed_count': 0,
            'dry_run': bool(dry_run),
        }
        if dry_run:
            _logger.info('Analog rollup backfill dry run: %s', result)
            return result

        ensured_targets, created = self._ensure_analog_rollup_pairs(
            target_pairs
        )
        to_recompute = existing_to_recompute | ensured_targets
        result.update(
            {
                'created_count': len(created),
                'recomputed_count': to_recompute._recompute_analog_rollup_fields(
                    batch_size=batch_size
                ),
            }
        )
        _logger.info('Analog rollup backfill completed: %s', result)
        return result

    @api.depends(
        'comment',
        'product_id',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
        'sale_contract_id.seller_move_line_ids',
        'sale_contract_id.seller_move_line_ids.product_id',
        'sale_contract_id.seller_move_line_ids.analog_original_product_id',
        'sale_contract_id.seller_move_line_ids.analytic_distribution',
        'sale_contract_id.seller_move_line_ids.seller_contract_id',
        'sale_contract_id.seller_move_line_ids.move_id.state',
        'sale_contract_id.seller_move_line_ids.move_id.move_type',
        'sale_contract_id.seller_move_line_ids.move_id.seller_contract_id',
        'sale_contract_id.seller_purchase_line_ids',
        'sale_contract_id.seller_purchase_line_ids.product_id',
        'sale_contract_id.seller_purchase_line_ids.analog_original_product_id',
        'sale_contract_id.seller_purchase_line_ids.seller_contract_id',
        'sale_contract_id.seller_purchase_line_ids.order_id.state',
    )
    def _compute_demand_comment(self):
        for product_analytic in self:
            comment = product_analytic.comment or ''
            if product_analytic._should_show_demand_analog_marker():
                product_analytic.demand_comment = (
                    f'{comment} (A)' if comment else '(A)'
                )
            else:
                product_analytic.demand_comment = comment

    def _should_show_demand_analog_marker(self):
        self.ensure_one()
        if self.product_id.analog_source_line_ids:
            return True
        return self._has_direct_analog_purchase_activity()

    def _has_direct_analog_purchase_activity(self):
        self.ensure_one()
        analog_products = self.product_id._get_primary_analog_products()
        if not analog_products:
            return False
        if self._get_related_invoice_lines_for_products(analog_products):
            return True
        return bool(
            self._get_related_purchase_contract_lines_for_products(
                analog_products,
                self.product_id,
            )
        )

    def _is_analog_rollup_child(self):
        self.ensure_one()
        return self.product_id._is_analog_rollup_child()

    def _get_direct_rollup_products(self):
        self.ensure_one()
        return self.product_id._get_bidirectional_analog_group_products()

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

    def _is_product_related_to_rollup_product(
        self,
        product,
        rollup_product,
        selected_original_product=False,
        keep_legacy_ambiguous=False,
    ):
        if selected_original_product:
            return selected_original_product == rollup_product
        if rollup_product._is_analog_rollup_child():
            return False
        if product == rollup_product:
            return True
        if product not in rollup_product._get_primary_analog_products():
            return False

        original_products = product._get_primary_analog_main_products()
        if len(original_products) == 1:
            return original_products == rollup_product
        return keep_legacy_ambiguous and rollup_product in original_products

    def _is_invoice_line_related_to_rollup_product(self, line, rollup_product):
        selected_original_product = line._get_analog_original_product_for_rollup()
        return self._is_product_related_to_rollup_product(
            line.product_id,
            rollup_product,
            selected_original_product=selected_original_product,
            keep_legacy_ambiguous=line.move_id.state == 'posted',
        )

    def _is_purchase_line_related_to_rollup_product(self, line, rollup_product):
        return self._is_product_related_to_rollup_product(
            line.product_id,
            rollup_product,
            selected_original_product=line._get_analog_original_product_for_rollup(),
            keep_legacy_ambiguous=line.order_id.state in ('purchase', 'done'),
        )

    def _get_related_invoice_lines_for_products(self, products, rollup_product=False):
        self.ensure_one()
        if not products:
            return self.env['account.move.line']
        rollup_product = rollup_product or self.product_id
        invoice_lines = self.env['account.move.line'].search(
            [
                ('product_id', 'in', products.ids),
                ('move_id.state', '=', 'posted'),
                ('move_id.move_type', 'in', ['in_invoice', 'in_refund']),
            ]
        )
        return invoice_lines.filtered(
            lambda line: self._has_related_sale_contract(line)
            and self._is_invoice_line_related_to_rollup_product(
                line,
                rollup_product,
            )
        )

    def _get_related_invoice_move_lines(self):
        self.ensure_one()
        invoice_lines = self._get_related_invoice_lines_for_products(
            self._get_direct_rollup_products(),
            self.product_id,
        )
        for product in self._get_kit_products_for_rollup():
            invoice_lines |= self._get_related_invoice_lines_for_products(
                product._get_analog_rollup_products(),
                product,
            )
        return invoice_lines

    def _get_related_purchase_contract_lines_for_products(
        self,
        products,
        rollup_product=False,
    ):
        self.ensure_one()
        if not products:
            return self.env['purchase.order.line']
        rollup_product = rollup_product or self.product_id
        domain = [
            ('product_id', 'in', products.ids),
            ('order_id.state', 'in', ['purchase', 'done']),
        ]
        return self.env['purchase.order.line'].search(domain).filtered(
            lambda line: (
                line._has_demand_report_seller_contract(self.sale_contract_id)
                and self._is_purchase_line_related_to_rollup_product(
                    line,
                    rollup_product,
                )
            )
        )

    def _get_related_purchase_contract_lines(self):
        self.ensure_one()
        purchase_lines = self._get_related_purchase_contract_lines_for_products(
            self._get_direct_rollup_products(),
            self.product_id,
        )
        for product in self._get_kit_products_for_rollup():
            purchase_lines |= self._get_related_purchase_contract_lines_for_products(
                product._get_analog_rollup_products(),
                product,
            )
        return purchase_lines

    def _sum_invoice_quantity_for_products(
        self,
        products,
        target_uom,
        rollup_product=False,
    ):
        self.ensure_one()
        if not products:
            return 0
        total_quantity = 0
        for line in self._get_related_invoice_lines_for_products(
            products,
            rollup_product,
        ):
            quantity = line.product_uom_id._compute_quantity(
                line.quantity,
                target_uom,
            )
            total_quantity += quantity if line.move_type == 'in_invoice' else -quantity
        return total_quantity

    def _sum_purchase_quantity_for_products(
        self,
        products,
        target_uom,
        quantity_field,
        rollup_product=False,
    ):
        self.ensure_one()
        if not products:
            return 0
        total_quantity = 0
        purchase_lines = self._get_related_purchase_contract_lines_for_products(
            products,
            rollup_product,
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
                    product,
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
                    product,
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
        'sale_contract_id.seller_move_line_ids.analog_original_product_id',
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
        'sale_contract_id.seller_purchase_line_ids.order_id.state',
        'sale_contract_id.seller_purchase_line_ids.qty_received',
        'sale_contract_id.seller_purchase_line_ids.product_qty',
        'sale_contract_id.seller_purchase_line_ids.product_uom',
        'sale_contract_id.seller_purchase_line_ids.product_id',
        'sale_contract_id.seller_purchase_line_ids.analog_original_product_id',
        'sale_contract_id.seller_purchase_ids',
        'sale_contract_id.seller_purchase_ids.state',
        'sale_contract_id.seller_purchase_ids.order_line',
        'sale_contract_id.seller_purchase_ids.order_line.qty_received',
        'sale_contract_id.seller_purchase_ids.order_line.product_qty',
        'sale_contract_id.seller_purchase_ids.order_line.product_uom',
        'sale_contract_id.seller_purchase_ids.order_line.product_id',
        'sale_contract_id.seller_purchase_ids.order_line.analytic_distribution',
        'sale_contract_id.seller_purchase_ids.order_line.seller_contract_id',
        'sale_contract_id.seller_purchase_ids.order_line.analog_original_product_id',
        'kit_bom_ids',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
    )
    def _compute_qty_received(self):
        for product_analytic in self:
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
        product_analytics = self.sudo()
        product_analytics._compute_numbers()
        product_analytics._compute_account_move_ids()
        product_analytics._compute_demand_comment()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_post(self):
        self.line_ids._sync_analog_original_product()
        self._check_required_analog_original_products()
        res = super().action_post()
        self.line_ids._ensure_analog_rollup_target_product_analytics()
        self.line_ids._recompute_analog_product_analytics()
        return res

    def button_draft(self):
        analytics = self.line_ids._get_analog_product_analytic_recompute_targets()
        res = super().button_draft()
        analytics._recompute_analog_invoice_fields()
        return res

    def _check_required_analog_original_products(self):
        for move in self.filtered(lambda record: record.move_type in ('in_invoice', 'in_refund')):
            missing_lines = move.invoice_line_ids.filtered(
                lambda line: line._requires_analog_original_product()
                and not line.analog_original_product_id
            )
            if missing_lines:
                raise ValidationError(
                    _('Для товару-аналога необхідно вибрати оригінал аналога.')
                )


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    ANALOG_RECOMPUTE_FIELDS = {
        'product_id',
        'quantity',
        'product_uom_id',
        'analytic_distribution',
        'seller_contract_id',
        'analog_original_product_id',
        'purchase_line_id',
        'purchase_line_ids',
        'move_id',
    }

    analog_original_product_ids = fields.Many2many(
        comodel_name='product.product',
        compute='_compute_analog_original_product_fields',
        string='Analog Original Options',
    )
    analog_original_product_id = fields.Many2one(
        comodel_name='product.product',
        string='Оригінал аналога',
        domain="[('id', 'in', analog_original_product_ids)]",
        copy=False,
    )
    has_analog_original_options = fields.Boolean(
        compute='_compute_analog_original_product_fields',
    )
    has_multiple_analog_original_options = fields.Boolean(
        compute='_compute_analog_original_product_fields',
    )

    @api.depends(
        'product_id',
        'product_id.product_tmpl_id.analog_line_ids',
        'product_id.product_tmpl_id.analog_line_ids.product_id',
        'product_id.product_tmpl_id.analog_line_ids.is_primary_link',
        'product_id.analog_source_line_ids',
        'product_id.analog_source_line_ids.is_primary_link',
        'product_id.analog_source_line_ids.product_tmpl_id',
    )
    def _compute_analog_original_product_fields(self):
        for line in self:
            original_products = (
                line.product_id._get_allowed_analog_rollup_target_products()
            )
            line.analog_original_product_ids = original_products
            line.has_analog_original_options = bool(original_products)
            line.has_multiple_analog_original_options = len(original_products) > 1

    def _get_purchase_lines_for_analog_origin(self):
        self.ensure_one()
        purchase_lines = self.env['purchase.order.line']
        if 'purchase_line_id' in self._fields:
            purchase_lines |= self.purchase_line_id
        if 'purchase_line_ids' in self._fields:
            purchase_lines |= self.purchase_line_ids
        return purchase_lines

    def _get_purchase_line_analog_original_product(self):
        self.ensure_one()
        purchase_line_origins = self._get_purchase_lines_for_analog_origin().mapped(
            'analog_original_product_id'
        )
        return (
            purchase_line_origins
            if len(purchase_line_origins) == 1
            else self.env['product.product']
        )

    def _get_analog_original_product_for_rollup(self):
        self.ensure_one()
        selected_product = (
            self.analog_original_product_id
            or self._get_purchase_line_analog_original_product()
        )
        return self.product_id._get_selected_analog_rollup_target_product(
            selected_product
        )

    def _requires_analog_original_product(self):
        self.ensure_one()
        return len(self.product_id._get_primary_analog_main_products()) > 1

    def _get_default_analog_original_product(self):
        self.ensure_one()
        return self.product_id._get_default_analog_counterpart_product()

    def _get_seller_contracts_for_analog_target(self):
        self.ensure_one()
        contracts = (
            self.seller_contract_id
            | self._get_seller_contracts_from_analytic_distribution()
        )
        if 'seller_contract_id' in self.move_id._fields:
            contracts |= self.move_id.seller_contract_id
        return contracts

    def _get_analog_rollup_product_analytic_pairs(self):
        target_pairs = set()
        affected_pairs = set()
        for line in self:
            target_product = line._get_analog_original_product_for_rollup()
            if not target_product:
                continue
            for contract in line._get_seller_contracts_for_analog_target():
                target_pair = (target_product.id, contract.id)
                target_pairs.add(target_pair)
                affected_pairs.add(target_pair)
                affected_pairs.add((line.product_id.id, contract.id))
        return target_pairs, affected_pairs

    def _ensure_analog_rollup_target_product_analytics(self):
        target_pairs, _affected_pairs = (
            self._get_analog_rollup_product_analytic_pairs()
        )
        return self.env['product.analytic']._ensure_analog_rollup_pairs(
            target_pairs
        )[0]

    @api.onchange('product_id')
    def _onchange_product_id_analog_original_product_id(self):
        for line in self:
            line._set_default_analog_original_product()

    def _set_default_analog_original_product(self):
        self.ensure_one()
        purchase_line_origin = self._get_purchase_line_analog_original_product()
        allowed_products = (
            self.product_id._get_allowed_analog_rollup_target_products()
        )
        if purchase_line_origin and purchase_line_origin in allowed_products:
            self.analog_original_product_id = purchase_line_origin
            return

        default_product = self._get_default_analog_original_product()
        if not allowed_products:
            self.analog_original_product_id = False
        elif (
            self.analog_original_product_id
            and self.analog_original_product_id in allowed_products
        ):
            return
        elif default_product:
            self.analog_original_product_id = default_product
        elif self.analog_original_product_id not in allowed_products:
            self.analog_original_product_id = False

    @api.model
    def _prepare_analog_original_product_vals(
        self,
        vals,
        current_selected_product=False,
    ):
        vals = dict(vals)
        product_id = vals.get('product_id')
        if not product_id:
            return vals

        purchase_line = self.env['purchase.order.line']
        purchase_line_id = vals.get('purchase_line_id')
        if purchase_line_id:
            purchase_line = purchase_line.browse(purchase_line_id)
        purchase_line_origin = purchase_line.mapped('analog_original_product_id')

        product = self.env['product.product'].browse(product_id)
        allowed_products = product._get_allowed_analog_rollup_target_products()
        if len(purchase_line_origin) == 1 and purchase_line_origin in allowed_products:
            vals['analog_original_product_id'] = purchase_line_origin.id
            return vals
        selected_id = vals.get('analog_original_product_id')
        if (
            'analog_original_product_id' not in vals
            and current_selected_product
        ):
            selected_id = current_selected_product.id
        if not allowed_products:
            vals['analog_original_product_id'] = False
        elif selected_id and selected_id in allowed_products.ids:
            vals['analog_original_product_id'] = selected_id
        else:
            default_product = product._get_default_analog_counterpart_product()
            vals['analog_original_product_id'] = default_product.id or False
        return vals

    def _get_analog_sync_target_product(self):
        self.ensure_one()
        purchase_line_origin = self._get_purchase_line_analog_original_product()
        allowed_products = (
            self.product_id._get_allowed_analog_rollup_target_products()
        )
        if purchase_line_origin and purchase_line_origin in allowed_products:
            return purchase_line_origin
        if not allowed_products:
            return self.env['product.product']
        if (
            self.analog_original_product_id
            and self.analog_original_product_id in allowed_products
        ):
            return self.analog_original_product_id
        return self._get_default_analog_original_product()

    def _sync_analog_original_product(self):
        if self.env.context.get('product_alternatives_skip_analog_origin_sync'):
            return
        for line in self:
            target_product = line._get_analog_sync_target_product()

            if line.analog_original_product_id != target_product:
                line.with_context(
                    product_alternatives_skip_analog_origin_sync=True
                ).write({'analog_original_product_id': target_product.id or False})

    @api.constrains('product_id', 'analog_original_product_id')
    def _check_analog_original_product_id(self):
        for line in self:
            if not line.analog_original_product_id:
                continue
            if line.analog_original_product_id not in (
                line.product_id._get_allowed_analog_rollup_target_products()
            ):
                raise ValidationError(
                    _('Обраний оригінал аналога не відповідає товару рядка.')
                )

    def _get_posted_vendor_invoice_lines(self):
        return self.filtered(
            lambda line: line.move_id.state == 'posted'
            and line.move_id.move_type in ('in_invoice', 'in_refund')
            and line.product_id
        )

    def _get_seller_contracts_from_analytic_distribution(self):
        analytic_ids = set()
        for line in self:
            for key in (line.analytic_distribution or {}):
                for analytic_id in str(key).split(','):
                    analytic_id = analytic_id.strip()
                    if analytic_id.isdigit():
                        analytic_ids.add(int(analytic_id))
        if not analytic_ids:
            return self.env['account.analytic.account']
        return self.env['account.analytic.account'].search(
            [
                ('id', 'in', list(analytic_ids)),
                ('is_plan_seller_contract', '=', True),
            ]
        )

    def _get_direct_analog_recompute_products(self):
        direct_products = self.env['product.product']
        for line in self:
            product = line.product_id
            if not product:
                continue
            original_product = line._get_analog_original_product_for_rollup()
            if original_product:
                direct_products |= original_product
                continue
            if not product._is_analog_rollup_child():
                direct_products |= product
                continue
            main_products = product._get_primary_analog_main_products()
            if len(main_products) == 1 or line.move_id.state == 'posted':
                direct_products |= main_products
        return direct_products

    def _get_kit_analog_recompute_boms(self, direct_products):
        kit_products = self.mapped('product_id') | direct_products
        if not kit_products:
            return self.env['mrp.bom']
        return self.env['mrp.bom'].search(
            [
                ('type', '=', 'phantom'),
                ('product_tmpl_id', 'in', kit_products.product_tmpl_id.ids),
            ]
        )

    def _get_analog_product_analytic_recompute_targets(self):
        lines = self._get_posted_vendor_invoice_lines()
        if not lines:
            return self.env['product.analytic']
        contracts = (
            lines.mapped('seller_contract_id')
            | lines._get_seller_contracts_from_analytic_distribution()
        )
        if 'seller_contract_id' in self.env['account.move']._fields:
            contracts |= lines.mapped('move_id.seller_contract_id')
        if not contracts:
            return self.env['product.analytic']

        direct_products = lines._get_direct_analog_recompute_products()
        kit_boms = lines._get_kit_analog_recompute_boms(direct_products)
        target_domain = [('sale_contract_id', 'in', contracts.ids)]
        if direct_products and kit_boms:
            target_domain += [
                '|',
                ('product_id', 'in', direct_products.ids),
                ('kit_bom_ids', 'in', kit_boms.ids),
            ]
        elif direct_products:
            target_domain.append(('product_id', 'in', direct_products.ids))
        elif kit_boms:
            target_domain.append(('kit_bom_ids', 'in', kit_boms.ids))
        else:
            return self.env['product.analytic']

        return self.env['product.analytic'].search(target_domain)

    def _recompute_analog_product_analytics(self):
        product_analytics = self._get_analog_product_analytic_recompute_targets()
        product_analytics._recompute_analog_invoice_fields()

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [
            self._prepare_analog_original_product_vals(vals)
            for vals in vals_list
        ]
        lines = super().create(vals_list)
        lines._sync_analog_original_product()
        lines._get_posted_vendor_invoice_lines()._ensure_analog_rollup_target_product_analytics()
        lines._get_posted_vendor_invoice_lines()._recompute_analog_product_analytics()
        return lines

    def write(self, vals):
        if self.env.context.get('product_alternatives_skip_analog_origin_sync'):
            return super().write(vals)
        should_recompute = bool(self.ANALOG_RECOMPUTE_FIELDS & set(vals))
        analytics = (
            self._get_analog_product_analytic_recompute_targets()
            if should_recompute
            else self.env['product.analytic']
        )
        current_selected_product = self.env['product.product']
        if self and all(
            line.analog_original_product_id
            == self[0].analog_original_product_id
            for line in self
        ):
            current_selected_product = self[0].analog_original_product_id
        write_vals = (
            self._prepare_analog_original_product_vals(
                vals,
                current_selected_product=current_selected_product,
            )
            if 'product_id' in vals
            else vals
        )
        res = super().write(write_vals)
        if {
            'product_id',
            'analog_original_product_id',
            'purchase_line_id',
            'purchase_line_ids',
        } & set(vals):
            self._sync_analog_original_product()
        if should_recompute:
            self._get_posted_vendor_invoice_lines()._ensure_analog_rollup_target_product_analytics()
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
        if not self.raw_material_production_id:
            raise UserError(
                _('Analog replacement is only available for manufacturing components.')
            )
        self.raw_material_production_id.check_access_rights('write')
        self.raw_material_production_id.check_access_rule('write')

        move = self.sudo()
        if move.raw_material_production_state in ('done', 'cancel'):
            raise UserError(
                _('You cannot replace components on a done or cancelled manufacturing order.')
            )

        analog_product = (
            self.env['product.product'].sudo().browse(analog_product_id).exists()
        )
        if not analog_product:
            raise UserError(_('The selected analog product no longer exists.'))
        if analog_product not in move.analog_product_ids:
            raise ValidationError(
                _('The selected product is not an allowed analog for this component.')
            )
        if analog_product.uom_id != move.product_id.uom_id:
            raise ValidationError(
                _('The selected analog must use the same unit of measure.')
            )

        should_reassign = move.state in {'confirmed', 'partially_available', 'assigned'}
        if move.move_line_ids:
            move._do_unreserve()
        move.write(
            {
                'product_id': analog_product.id,
                'product_uom': analog_product.uom_id.id,
            }
        )
        if should_reassign and move.state not in {'done', 'cancel'}:
            move._action_assign()
        move.invalidate_recordset(
            [
                'analog_marker',
                'analog_product_data',
                'analog_product_ids',
            ]
        )
        return {
            'product_id': move.product_id.id,
            'product_display_name': move.product_id.display_name,
            'product_uom_id': move.product_uom.id,
            'product_uom_name': move.product_uom.display_name,
            'analog_marker': move.analog_marker,
            'analog_product_data': move.analog_product_data,
        }
