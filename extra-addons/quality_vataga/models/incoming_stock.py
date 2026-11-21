from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError

from .incoming_hold import (incoming_context, internal, INCOMING_DEFER_CONTEXT, INCOMING_DEFER_OPERATION)


class IncomingProduct(models.Model):
    _inherit = 'product.template'

    quality_incoming_controlled = fields.Boolean(string='Вхідний контроль CAB', copy=False)

    def _check_incoming_configuration_access(self):
        if not (self.env.user.has_group('stock.group_stock_manager')
                or self.env.user.has_group('quality.group_quality_manager')):
            raise AccessError(_('Вхідний контроль товару налаштовує керівник складу або якості.'))

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get('quality_incoming_controlled') for vals in vals_list):
            self._check_incoming_configuration_access()
        # Do not allow a forged default_* RPC context to opt a product in.
        return super().create([dict(vals, quality_incoming_controlled=bool(vals.get('quality_incoming_controlled')))
                               for vals in vals_list])

    def write(self, vals):
        if 'quality_incoming_controlled' in vals:
            self._check_incoming_configuration_access()
        return super().write(vals)


class IncomingWarehouse(models.Model):
    _inherit = 'stock.warehouse'

    quality_incoming_enabled = fields.Boolean(string='Вхідний карантин Vataga', copy=False)
    quality_incoming_location_id = fields.Many2one('stock.location', readonly=True, copy=False, check_company=True)
    quality_incoming_start_move_id = fields.Integer(readonly=True, copy=False)

    def write(self, vals):
        if not internal(self.env) and {'quality_incoming_enabled', 'quality_incoming_location_id',
                                      'quality_incoming_start_move_id'} & vals.keys():
            raise AccessError(_('Вхідний карантин налаштовується окремою дією керівника складу.'))
        if (not internal(self.env) and {'code', 'company_id', 'view_location_id'} & vals.keys()
                and self.filtered('quality_incoming_enabled')):
            raise UserError(_('Не змінюйте ідентичність складу з увімкненим вхідним карантином.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not internal(self.env) and any(
            vals.get('quality_incoming_enabled') or vals.get('quality_incoming_location_id')
            or vals.get('quality_incoming_start_move_id') for vals in vals_list
        ):
            raise AccessError(_('Спочатку створіть склад, потім налаштуйте карантин.'))
        if not internal(self.env):
            vals_list = [dict(vals, quality_incoming_enabled=False, quality_incoming_location_id=False,
                              quality_incoming_start_move_id=0) for vals in vals_list]
        return super().create(vals_list)

    def action_enable_incoming_quality(self):
        self.ensure_one()
        if not self.env.user.has_group('stock.group_stock_manager'):
            raise AccessError(_('Потрібні права керівника складу.'))
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.code != 'CAB' or self.company_id not in self.env.companies:
            raise UserError(_('Етап A доступний лише для складу CAB поточної компанії.'))
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM stock_warehouse WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        if self.quality_incoming_enabled:
            return True
        context = incoming_context(self.env)
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM stock_move')
        start_move_id = self.env.cr.fetchone()[0]
        location = self.env['stock.location'].with_context(context).create({
            'name': _('Вхідний карантин'), 'usage': 'internal',
            'location_id': self.view_location_id.id, 'company_id': self.company_id.id,
            'quality_quarantine': True,
        })
        self.with_context(context).write({
            'quality_incoming_enabled': True, 'quality_incoming_location_id': location.id,
            'quality_incoming_start_move_id': start_move_id,
        })
        return True


class IncomingMove(models.Model):
    _inherit = 'stock.move'

    quality_incoming_required = fields.Boolean(readonly=True, copy=False, index=True)
    quality_incoming_destination_id = fields.Many2one('stock.location', readonly=True, copy=False, check_company=True)
    quality_incoming_downstream_ids = fields.Many2many(
        'stock.move', 'quality_incoming_downstream_rel', 'receipt_id', 'downstream_id', readonly=True, copy=False)
    quality_incoming_hold_id = fields.Many2one('quality.incoming.hold', readonly=True, copy=False,
                                              check_company=True, ondelete='restrict', index=True)

    def _incoming_scope(self):
        self.ensure_one()
        warehouse = self.picking_type_id.warehouse_id
        return (self.picking_type_id.code == 'incoming' and warehouse.code == 'CAB'
                and warehouse.quality_incoming_enabled and self.location_id.usage == 'supplier'
                and self.product_id.type == 'product' and self.product_id.quality_incoming_controlled
                and self.id > warehouse.quality_incoming_start_move_id)

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'quality_incoming_required', 'quality_incoming_destination_id',
                     'quality_incoming_downstream_ids', 'quality_incoming_hold_id'}
        if not internal(self.env) and any(any(vals.get(key) for key in protected) for vals in vals_list):
            raise AccessError(_('Системні реквізити карантину не можна задавати вручну.'))
        if not internal(self.env):
            vals_list = [dict(vals, quality_incoming_required=False, quality_incoming_destination_id=False,
                              quality_incoming_downstream_ids=[], quality_incoming_hold_id=False) for vals in vals_list]
        moves = super().create(vals_list)
        for move in moves.filtered(lambda row: row._incoming_scope()):
            super(IncomingMove, move).write({'quality_incoming_required': True})
        return moves

    def write(self, vals):
        protected = {'quality_incoming_required', 'quality_incoming_destination_id',
                     'quality_incoming_downstream_ids', 'quality_incoming_hold_id'}
        if not internal(self.env):
            if protected & vals.keys():
                raise AccessError(_('Системні реквізити карантину незмінні.'))
            if self.filtered('quality_incoming_required') and {
                'picking_id', 'picking_type_id', 'company_id', 'location_id', 'product_id',
            } & vals.keys():
                if any(any(row[field].id != (vals[field] or False) for field in vals.keys() & {
                        'picking_id', 'picking_type_id', 'company_id', 'location_id', 'product_id'}) for row in self):
                    raise UserError(_('Не можна змінювати джерело контрольованого надходження.'))
        result = super().write(vals)
        for move in self.filtered(lambda row: not row.quality_incoming_required and row._incoming_scope()):
            super(IncomingMove, move).write({'quality_incoming_required': True})
        return result

    def _action_done(self, cancel_backorder=False):
        controlled = self.filtered(lambda row: row.state not in ('done', 'cancel') and (
            row.quality_incoming_required or row._incoming_scope()))
        if not controlled:
            return super()._action_done(cancel_backorder=cancel_backorder)
        controlled.check_access_rights('write')
        controlled.check_access_rule('write')
        context = incoming_context(self.env)
        for move in controlled:
            if not move._incoming_scope() or not move.picking_id:
                raise UserError(_('Контрольоване надходження має залишатися вхідним документом CAB.'))
            warehouse = move.picking_type_id.warehouse_id
            quarantine = warehouse.quality_incoming_location_id
            destination = move.location_dest_id
            if (not quarantine.active or not quarantine.quality_quarantine or quarantine.usage != 'internal'
                    or quarantine.company_id != move.company_id or quarantine.location_id != warehouse.view_location_id
                    or destination.quality_quarantine or destination.usage != 'internal'
                    or destination.warehouse_id != warehouse or destination.company_id != move.company_id
                    or move.move_line_ids.location_dest_id - destination
                    or any(line.location_id.usage != 'supplier' for line in move.move_line_ids)):
                raise UserError(_('Перевірте карантин CAB і локації вхідного надходження.'))
            if self.env['stock.rule'].sudo().search_count([
                    '|', ('location_src_id', '=', quarantine.id), ('location_dest_id', '=', quarantine.id)], limit=1):
                raise UserError(_('Карантин не повинен входити до автоматичних складських маршрутів.'))
            secured = move.with_context(context)
            secured.write({
                'quality_incoming_required': True, 'quality_incoming_destination_id': destination.id,
                'quality_incoming_downstream_ids': [Command.set(move.move_dest_ids.ids)],
                'move_dest_ids': [Command.clear()], 'location_dest_id': quarantine.id,
            })
            secured.move_line_ids.write({'location_dest_id': quarantine.id, 'picking_id': move.picking_id.id})
        done = super(IncomingMove, controlled.with_context(context))._action_done(cancel_backorder=cancel_backorder)
        done.invalidate_recordset(['move_line_ids', 'quantity'])
        for move in done.filtered('quality_incoming_required'):
            lines = self.env['stock.move.line'].search([('move_id', '=', move.id), ('quantity', '>', 0)])
            for line in lines:
                if line.location_id.usage != 'supplier' or line.location_dest_id != move.picking_type_id.warehouse_id.quality_incoming_location_id:
                    raise UserError(_('Рядок надходження проведено поза вхідним карантином.'))
                hold = self.env['quality.incoming.hold'].sudo().with_context(context).create({
                    'receipt_line_id': line.id, 'company_id': line.company_id.id,
                    'product_id': line.product_id.id, 'lot_id': line.lot_id.id,
                    'owner_id': line.owner_id.id, 'package_id': line.result_package_id.id,
                    'supplier_id': line.picking_id.partner_id.id,
                    'location_id': line.location_dest_id.id, 'destination_id': move.quality_incoming_destination_id.id,
                    'received_qty': line.quantity_product_uom,
                })
                hold._start_checks()
        others = self - controlled
        if others:
            done |= super(IncomingMove, others)._action_done(cancel_backorder=cancel_backorder)
        return done

    def _action_confirm(self, merge=True, merge_into=False):
        scoped = self.with_context(**{INCOMING_DEFER_CONTEXT: INCOMING_DEFER_OPERATION})
        return super(IncomingMove, scoped)._action_confirm(merge=merge, merge_into=merge_into)

    def _action_assign(self, force_qty=False):
        scoped = self.with_context(**{INCOMING_DEFER_CONTEXT: INCOMING_DEFER_OPERATION})
        return super(IncomingMove, scoped)._action_assign(force_qty=force_qty)

    def _prepare_move_split_vals(self, qty):
        vals = super()._prepare_move_split_vals(qty)
        if self.quality_incoming_required and self.quality_incoming_destination_id:
            # The posted part is quarantined; an unreceived backorder must
            # retain the original destination until its own validation.
            vals.update(location_dest_id=self.quality_incoming_destination_id.id,
                        move_dest_ids=[Command.set(self.quality_incoming_downstream_ids.ids)])
        return vals


class IncomingPicking(models.Model):
    _inherit = 'stock.picking'

    quality_incoming_hold_ids = fields.One2many('quality.incoming.hold', 'receipt_id', readonly=True)

    def action_confirm(self):
        scoped = self.with_context(**{INCOMING_DEFER_CONTEXT: INCOMING_DEFER_OPERATION})
        return super(IncomingPicking, scoped).action_confirm()

    def write(self, vals):
        if not internal(self.env) and {'picking_type_id', 'company_id', 'location_id'} & vals.keys():
            if self.move_ids.filtered('quality_incoming_required'):
                for picking in self:
                    if any(picking[field].id != (vals[field] or False) for field in vals.keys() & {
                            'picking_type_id', 'company_id', 'location_id'}):
                        raise UserError(_('Не можна змінювати джерело контрольованого надходження.'))
        return super().write(vals)

    def action_open_incoming_holds(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        return {'type': 'ir.actions.act_window', 'name': _('Вхідний карантин'),
                'res_model': 'quality.incoming.hold', 'view_mode': 'tree,form',
                'domain': [('receipt_id', '=', self.id)]}


class IncomingQuant(models.Model):
    _inherit = 'stock.quant'

    def _update_available_quantity(self, product_id, location_id, quantity=False, reserved_quantity=False,
                                   lot_id=None, package_id=None, owner_id=None, in_date=None):
        # Extend the existing tracked-lot guard to untracked incoming goods.
        # A forced negative quant in CAB/Stock cannot stand in for held stock.
        consumes = bool(quantity and quantity < 0)
        reserves = bool(reserved_quantity and reserved_quantity > 0)
        warehouse = location_id.warehouse_id
        if ((consumes or reserves) and location_id.usage == 'internal' and not location_id.quality_quarantine
                and warehouse.code == 'CAB' and warehouse.quality_incoming_enabled
                and self.sudo().search_count([
                    ('product_id', '=', product_id.id), ('lot_id', '=', lot_id.id if lot_id else False),
                    ('location_id', '=', warehouse.quality_incoming_location_id.id), ('quantity', '>', 0),
                ], limit=1)):
            from odoo.tools.float_utils import float_compare
            quants = self.sudo()._gather(product_id, location_id, lot_id, package_id, owner_id, strict=True)
            available = sum(quants.mapped('quantity'))
            requested = -quantity if consumes else reserved_quantity
            if reserves:
                available -= sum(quants.mapped('reserved_quantity'))
            if float_compare(available, requested, precision_rounding=product_id.uom_id.rounding) < 0:
                raise UserError(_('Товар утримується у вхідному карантині; від’ємний рух заборонено.'))
        return super()._update_available_quantity(product_id, location_id, quantity, reserved_quantity,
                                                  lot_id, package_id, owner_id, in_date)
