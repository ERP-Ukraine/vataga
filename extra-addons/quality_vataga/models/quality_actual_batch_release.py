"""Close an identified actual batch through native MRP and its backorder wizard.

This is separate from incremental posting on an open production. No identities
exist for the unmanufactured remainder, and no historical executions are added.
"""
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

from .native_backorder import NativeBackorderOperation, native_operation
from .quality_native_backorder import _read, _require_roles


class QualityActualBatchProduction(models.Model):
    _inherit = 'mrp.production'

    quality_actual_batch_available = fields.Boolean(compute='_compute_quality_actual_batch_available')

    @api.depends('state', 'qty_producing', 'product_qty', 'quality_unit_route_required',
                 'quality_transfer_controlled', 'workorder_ids.quality_execution_ids.state')
    def _compute_quality_actual_batch_available(self):
        for production in self:
            production.quality_actual_batch_available = bool(
                production.state in ('confirmed', 'progress', 'to_close')
                and not production.quality_unit_route_required and not production.quality_transfer_controlled
                and 0 < production.qty_producing < production.product_qty
                and production.workorder_ids.quality_execution_ids)

    def _validate_quality_actual_batch(self, units, quantity):
        """Re-run at the stock boundary, including after native splitting."""
        self.ensure_one()
        if (self.quality_unit_route_required or self.quality_transfer_controlled
                or self.company_id not in self.env.companies
                or self.state not in ('confirmed', 'progress', 'to_close') or self.qty_produced
                or not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity)
                or quantity > self.product_qty or self.qty_producing != quantity
                or self.product_uom_id != self.env.ref('uom.product_uom_unit')
                or self.product_id.uom_id != self.product_uom_id
                or self.product_id.tracking != 'none'):
            raise ValidationError(_('Випуск фактичної партії дозволено лише для відкритого замовлення без попереднього випуску, обов’язкового поштучного маршруту, історичних передач та партійного або серійного відстеження. Кількість має точно відповідати виготовленим виробам у штуках.'))
        _read(units)
        if (not units or len(units) != quantity or set(units.ids) != set(self._full_release_units().ids)
                or any(unit._quality_current_production() != self or unit.company_id != self.company_id
                       or unit.product_id != self.product_id for unit in units)):
            raise ValidationError(_('Перелік повинен містити саме всі фактично виготовлені вироби цього замовлення, товару та компанії.'))
        units._assert_not_stock_posted()
        registration = _read(units.registration_run_id)
        if (len(registration) != 1 or registration.state != 'registered'
                or registration.quantity != quantity or registration.production_id != self
                or set(registration.unit_ids.ids) != set(units.ids)):
            raise ValidationError(_('Потрібна підтверджена реєстрація саме фактично виготовленої партії.'))
        destination = _read(self.location_dest_id)
        if (destination.usage != 'internal' or destination.scrap_location
                or destination.company_id and destination.company_id != self.company_id):
            raise ValidationError(_('Готові вироби повинні надходити у внутрішню складську локацію своєї компанії.'))
        self._check_named_stock_history()
        workorders = self._full_release_workorders().sorted(
            key=lambda row: (row.operation_id.sequence if row.operation_id else 0, row.id))
        if (not workorders or registration.workorder_id != workorders[-1:]
                or self.allow_workorder_dependencies):
            raise ValidationError(_('Випуск фактичної партії потребує послідовного маршруту без залежностей та реєстрації виробів на останній операції.'))
        if workorders.time_ids.filtered(lambda timer: not timer.date_end):
            raise ValidationError(_('Спочатку підтвердьте обробку та закрийте всі робочі інтервали.'))
        for workorder in workorders:
            history = workorder._quality_native_history()
            processed = history.filtered(lambda row: row.state == 'processed')
            if (workorder.qty_produced != quantity
                    or (workorder != workorders[-1:] and workorder.state != 'done')
                    or (workorder == workorders[-1:] and workorder.state not in ('progress', 'done'))):
                raise ValidationError(_('Не підтверджено фактичне виконання операції %(operation)s для всієї виготовленої партії.', operation=workorder.display_name))
            if history or workorder == workorders[-1:]:
                if (len(processed) != quantity or len(history) != len(processed)
                        or set(processed.controlled_unit_id.ids) != set(units.ids)):
                    raise ValidationError(_('Поштучні факти операції %(operation)s не відповідають виготовленій партії.', operation=workorder.display_name))
            # A completed earlier operation without identities is real evidence;
            # never create executions or checks retroactively for that operation.
        self._validate_quality_batch_controls(units, workorders, standard=True)
        return True

    def action_release_quality_actual_batch(self):
        """Explicit user confirmation; all stock/backorder/facts share a savepoint."""
        self.ensure_one()
        _require_roles(self.env)
        self.check_access_rights('write')
        self.check_access_rule('write')
        # Ignore all client defaults/skip/backorder contexts at this entry.
        production = self.with_context({'allowed_company_ids': self.env.companies.ids,
                                       'lang': self.env.lang, 'tz': self.env.user.tz})
        with self.env.cr.savepoint():
            production._lock_quality_transfer_scope()
            production.invalidate_recordset()
            if production.state == 'done':
                raise UserError(_('Це виробниче замовлення вже завершено. Повторний випуск заборонено.'))
            units = production._full_release_units()
            quantity = len(units)
            if not 0 < quantity < production.product_qty:
                raise ValidationError(_('Для випуску фактичної партії потрібна виготовлена частина планової кількості; решту буде замовлено окремо.'))
            production._validate_quality_actual_batch(units, quantity)
            production = production.with_company(production.company_id)
            raw_quantities = {
                move.id: float_round(quantity * move.unit_factor,
                                     precision_rounding=move.product_uom.rounding)
                for move in production.move_raw_ids.filtered(lambda row: row.state != 'cancel')}
            operation = NativeBackorderOperation(production, quantity, tuple(units.ids), actual_batch=True)
            with operation.activate():
                children = production._native_quality_complete()
                production._check_quality_actual_batch_stock(operation, raw_quantities)
                production._validate_quality_batch_controls(units, production._full_release_workorders(), standard=True)
                production._record_standard_stock_facts(units)
                if (children.state in ('draft', 'done', 'cancel') or children.qty_produced
                        or children._full_release_units() or children.quality_transfer_controlled
                        or children.quality_unit_route_required):
                    raise ValidationError(_('Замовлення на залишок не повинно успадковувати виготовлені вироби або інший режим маршруту.'))
            production.message_post(body=_(
                'Фактичну партію з %(quantity)s виробів випущено на склад. Замовлення на залишок: %(order)s.',
                quantity=quantity, order=children.display_name), subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.act_window', 'res_model': 'mrp.production',
                'view_mode': 'form', 'res_id': production.id, 'target': 'current'}

    def _post_inventory(self, cancel_backorder=False):
        operation = native_operation()
        for production in self:
            if (operation and operation.actual_batch and production == operation.production
                    and production.env.cr is operation.production.env.cr):
                units = self.env['quality.production.unit'].browse(operation.unit_ids)
                production._validate_quality_actual_batch(units, operation.quantity)
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    def _check_quality_actual_batch_stock(self, operation, raw_quantities):
        """Audit real native consumption; costing/valuation stay owned by MRP."""
        self.ensure_one()
        if (self.state != 'done' or self.product_qty != operation.quantity
                or self.qty_produced != operation.quantity
                or self.workorder_ids.filtered(lambda row: row.state != 'done')):
            raise ValidationError(_('Штатне завершення не підтвердило фактичну партію.'))
        moves = self.move_raw_ids.filtered(lambda row: row.state != 'cancel')
        if set(moves.ids) != set(raw_quantities):
            raise ValidationError(_('Під час випуску змінився склад компонентів. Увесь випуск скасовано.'))
        # Snapshot before posting: native unit_factor depends on qty_produced
        # and changes once the finished move is done. It is not a booked ratio.
        for move in moves:
            if (move.state != 'done' or float_compare(move.quantity, raw_quantities[move.id],
                    precision_rounding=move.product_uom.rounding)
                    or any(line.location_dest_id.usage != 'production' for line in move.move_line_ids)):
                raise ValidationError(_('Фактичне списання компонентів не відповідає виготовленій партії. Увесь випуск скасовано.'))
