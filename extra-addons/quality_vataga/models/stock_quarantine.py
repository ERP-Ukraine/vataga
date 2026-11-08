from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


QUARANTINE_CONTEXT = 'quality_vataga_quarantine'
QUARANTINE_OPERATION = object()
QUARANTINE_NAME = 'Очікує перевірку якості'


def quarantine_error():
    return UserError(_('Рух запасів у карантині дозволено лише процесу контролю якості.'))


class StockLocation(models.Model):
    _inherit = 'stock.location'

    quality_quarantine = fields.Boolean(readonly=True, copy=False, index=True)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION:
            if any('quality_quarantine' in vals for vals in vals_list):
                raise quarantine_error()
            for vals in vals_list:
                vals['quality_quarantine'] = False
        locations = super().create(vals_list)
        if locations.location_id.filtered('quality_quarantine'):
            raise quarantine_error()
        return locations

    def write(self, vals):
        if self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION:
            if 'quality_quarantine' in vals or (
                {'active', 'usage', 'location_id', 'company_id'} & vals.keys()
                and self.filtered('quality_quarantine')
            ):
                raise quarantine_error()
        if self.browse(vals.get('location_id')).quality_quarantine:
            raise quarantine_error()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quality_quarantine(self):
        if self.filtered('quality_quarantine'):
            raise quarantine_error()


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    quality_quarantine_location_id = fields.Many2one(
        'stock.location', readonly=True, copy=False, check_company=True, ondelete='restrict',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['quality_quarantine_location_id'] = False
        return super().create(vals_list)

    def write(self, vals):
        if 'quality_quarantine_location_id' in vals and (
            self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION
        ):
            raise quarantine_error()
        return super().write(vals)

    @api.model
    def _configure_cab_quality_quarantine(self):
        warehouses = self.env['stock.warehouse'].search([('code', '=', 'CAB')])
        candidates = self.search([
            ('warehouse_id', 'in', warehouses.ids), ('code', '=', 'mrp_operation'),
        ]).filtered(
            lambda operation: operation.default_location_src_id
            == operation.default_location_dest_id
            == operation.warehouse_id.lot_stock_id,
        )
        if not candidates:
            return
        if len(candidates) != 1:
            raise UserError(_('Не вдалося однозначно визначити тип виробництва складу CAB.'))
        operation = candidates
        if operation.quality_quarantine_location_id:
            return

        warehouse = operation.warehouse_id
        locations = self.env['stock.location'].with_context(
            **{QUARANTINE_CONTEXT: QUARANTINE_OPERATION},
        )
        quarantine = locations.search([
            ('location_id', '=', warehouse.view_location_id.id),
            ('company_id', '=', warehouse.company_id.id),
            ('quality_quarantine', '=', True),
        ])
        if not quarantine:
            quarantine = locations.create({
                'name': QUARANTINE_NAME, 'usage': 'internal', 'quality_quarantine': True,
                'location_id': warehouse.view_location_id.id,
                'company_id': warehouse.company_id.id,
            })
        if len(quarantine) != 1:
            raise UserError(_('Для складу CAB знайдено кілька карантинних локацій.'))
        operation.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).write({
            'quality_quarantine_location_id': quarantine.id,
        })


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _get_reserve_quantity(
        self, product_id, location_id, quantity, product_packaging_id=None,
        uom_id=None, lot_id=None, package_id=None, owner_id=None, strict=False,
    ):
        if location_id.quality_quarantine:
            return []
        # Exclude quarantined quants even when an ancestor is used as source.
        quants = self.with_context(quality_vataga_reservation=True)
        return super(StockQuant, quants)._get_reserve_quantity(
            product_id, location_id, quantity, product_packaging_id,
            uom_id, lot_id, package_id, owner_id, strict,
        )

    def _gather(self, product_id, location_id, lot_id=None, package_id=None,
                owner_id=None, strict=False, qty=0):
        quants = super()._gather(
            product_id, location_id, lot_id, package_id, owner_id, strict, qty,
        )
        if self.env.context.get('quality_vataga_reservation'):
            return quants.filtered(lambda quant: not quant.location_id.quality_quarantine)
        return quants

    def _get_gather_domain(self, product_id, location_id, lot_id=None, package_id=None,
                           owner_id=None, strict=False):
        domain = super()._get_gather_domain(
            product_id, location_id, lot_id, package_id, owner_id, strict,
        )
        if self.env.context.get('quality_vataga_reservation'):
            domain.append(('location_id.quality_quarantine', '=', False))
        return domain

    @api.model_create_multi
    def create(self, vals_list):
        quants = super().create(vals_list)
        quants._check_quality_quarantine()
        return quants

    def write(self, vals):
        self._check_quality_quarantine(self.env['stock.location'].browse(vals.get('location_id')))
        return super().write(vals)

    def _apply_inventory(self):
        self._check_quality_quarantine()
        return super()._apply_inventory()

    def _update_available_quantity(
        self, product_id, location_id, quantity=False, reserved_quantity=False,
        lot_id=None, package_id=None, owner_id=None, in_date=None,
    ):
        # A forced transfer from Stock must not issue a quarantined lot by
        # creating a negative quantity under its number in another location.
        consumes = bool(quantity and quantity < 0)
        reserves = bool(reserved_quantity and reserved_quantity > 0)
        if (
            (consumes or reserves) and lot_id and location_id.usage == 'internal'
            and not location_id.quality_quarantine
        ):
            warehouse = location_id.warehouse_id
            if warehouse and self.sudo().search_count([
                ('product_id', '=', product_id.id), ('lot_id', '=', lot_id.id),
                ('location_id.quality_quarantine', '=', True),
                ('location_id.location_id', '=', warehouse.view_location_id.id),
                ('quantity', '>', 0),
            ], limit=1):
                quants = self.sudo()._gather(
                    product_id, location_id, lot_id, package_id, owner_id, strict=True,
                ).filtered(lambda quant: quant.lot_id == lot_id)
                available = sum(quants.mapped('quantity'))
                requested = -quantity if consumes else reserved_quantity
                if reserves:
                    available -= sum(quants.mapped('reserved_quantity'))
                if float_compare(available, requested, precision_rounding=product_id.uom_id.rounding) < 0:
                    raise quarantine_error()
        return super()._update_available_quantity(
            product_id, location_id, quantity, reserved_quantity, lot_id,
            package_id, owner_id, in_date,
        )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quality_quarantine(self):
        self.filtered(lambda quant: quant.quantity or quant.reserved_quantity)._check_quality_quarantine()

    def _check_quality_quarantine(self, locations=None):
        if self.env.context.get(QUARANTINE_CONTEXT) is QUARANTINE_OPERATION:
            return
        if self.location_id.filtered('quality_quarantine') or (
            locations and locations.filtered('quality_quarantine')
        ):
            raise quarantine_error()


class StockMove(models.Model):
    _inherit = 'stock.move'

    quality_held_move_id = fields.Many2one(
        'stock.move', readonly=True, copy=False, check_company=True, ondelete='restrict',
    )
    quality_release_move_ids = fields.One2many('stock.move', 'quality_held_move_id')
    quality_board_accepted = fields.Boolean(readonly=True, copy=False)

    _sql_constraints = [
        ('quality_single_release', 'unique(quality_held_move_id)',
         'Для складського руху вже існує переміщення з карантину.'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION:
            vals_list = [dict(vals, quality_board_accepted=False) for vals in vals_list]
        moves = super().create(vals_list)
        moves._check_quality_quarantine()
        return moves

    def write(self, vals):
        if 'quality_board_accepted' in vals and self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION:
            raise quarantine_error()
        if {
            'location_id', 'location_dest_id', 'state', 'quantity', 'product_uom_qty',
            'product_id', 'product_uom', 'company_id', 'picked', 'production_id',
            'quality_held_move_id', 'move_line_ids', 'picking_type_id', 'picking_id',
        } & vals.keys():
            locations = self.env['stock.location'].browse([
                vals[key] for key in ('location_id', 'location_dest_id') if vals.get(key)
            ])
            self._check_quality_quarantine(locations)
            if 'quality_held_move_id' in vals and (
                self.env.context.get(QUARANTINE_CONTEXT) is not QUARANTINE_OPERATION
            ):
                raise quarantine_error()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quality_quarantine(self):
        self._check_quality_quarantine()

    def _action_done(self, cancel_backorder=False):
        self.filtered(lambda move: move.state not in ('done', 'cancel'))._check_quality_quarantine()
        return super()._action_done(cancel_backorder=cancel_backorder)

    def _check_quality_quarantine(self, locations=None):
        if self.env.context.get(QUARANTINE_CONTEXT) is QUARANTINE_OPERATION:
            return
        if self.quality_held_move_id or (
            self.location_id | self.location_dest_id | (locations or self.env['stock.location'])
        ).filtered('quality_quarantine'):
            raise quarantine_error()


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._check_quality_quarantine()
        return lines

    def write(self, vals):
        locations = self.env['stock.location'].browse([
            vals[key] for key in ('location_id', 'location_dest_id') if vals.get(key)
        ])
        self._check_quality_quarantine(locations)
        if vals.get('move_id'):
            self.env['stock.move'].browse(vals['move_id'])._check_quality_quarantine()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quality_quarantine(self):
        self._check_quality_quarantine()

    def _action_done(self):
        self._check_quality_quarantine()
        return super()._action_done()

    def _check_quality_quarantine(self, locations=None):
        self.move_id._check_quality_quarantine(
            self.location_id | self.location_dest_id | (locations or self.env['stock.location']),
        )
