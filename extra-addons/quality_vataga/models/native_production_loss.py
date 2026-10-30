"""Terminal equal allocation of actual costs; never manufactures the lost unit.

Native MRP consumes the full physical batch and closes via its ordinary warning
and no-backorder dialogs. Only the final price BEFORE SVL is allocated between
real good output and an immutable production loss ledger. Periodic valuation
does not create a general-ledger entry; that pending step is explicit.
"""
import json
import math

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .native_partial_release import PartialPosting, partial_operation, _read


class NativeLossRelease(models.Model):
    _inherit = 'quality.native.partial.release'

    disposition = fields.Selection([('incremental', 'Частковий випуск'),
                                    ('terminal_loss', 'Завершення з виробничими втратами')],
                                   default='incremental', required=True, readonly=True)
    loss_ids = fields.One2many('quality.native.production.loss', 'release_id', readonly=True)
    allocation = fields.Json(readonly=True)


class NativeProductionLoss(models.Model):
    _name = 'quality.native.production.loss'
    _inherit = 'quality.native.partial.access'
    _description = 'Підтверджена виробнича втрата невипущеного виробу'
    _rec_name = 'unit_key'

    release_id = fields.Many2one('quality.native.partial.release', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one(related='release_id.production_id', store=True, readonly=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one('res.currency', required=True, readonly=True)
    unit_key = fields.Char(required=True, readonly=True)
    quantity = fields.Integer(default=1, required=True, readonly=True)
    # Rounded explicitly using currency_id at allocation. A plain Float avoids
    # ORM Monetary conversion fetching a not-yet-created protected audit row.
    value = fields.Float(string='Вартість браку', required=True, readonly=True)
    accounting_status = fields.Selection([('manual_periodic_pending', 'Періодичний облік: проводку не створено')],
                                        default='manual_periodic_pending', required=True, readonly=True)
    _sql_constraints = [('unit_unique', 'unique(production_id, unit_key)', 'Втрату цього виробу вже підтверджено.'),
                        ('one_unit', 'check(quantity = 1 AND value >= 0)', 'Втрата має визначати один виріб та невід’ємну вартість.')]


class NativeLossProduction(models.Model):
    _inherit = 'mrp.production'

    def _native_loss_before_post(self, operation):
        """Quality extension must validate the exact good/lost identity partition."""

    def _native_loss_allocate(self, consumed_moves, operation):
        if partial_operation(self) is not operation or operation.phase != 'cost' or not operation.loss_keys:
            raise UserError(_('Розподіл втрат доступний лише всередині перевіреного проведення.'))
        finished = self.move_finished_ids.filtered(
            lambda m: m.state not in ('done', 'cancel') and m.product_id == self.product_id and m.quantity > 0)
        finished.ensure_one()
        raw = -sum(consumed_moves.stock_valuation_layer_ids.mapped('value'))
        work = sum(operation.costs.values())
        quantity = len(operation.keys)
        native_total = finished.price_unit * quantity
        expected = raw + work + self.extra_cost * quantity
        if (set(operation.costs) != set(self.workorder_ids.ids) or not math.isfinite(expected)
                or raw <= 0 or work < 0 or self.extra_cost < 0
                or not math.isclose(native_total, expected, rel_tol=1e-9, abs_tol=1e-7)):
            raise ValidationError(_('Штатна ціна не підтвердила повні фактичні компоненти та WO.'))
        currency = self.company_id.currency_id
        total = currency.round(raw + work + self.extra_cost * self.product_qty)
        good = currency.round(total * quantity / self.product_qty)
        if total <= 0 or not math.isfinite(total):
            raise ValidationError(_('Потрібна додатна підтверджена фактична вартість партії.'))
        operation.allocation = {'policy': 'equal_actual_terminal_v1', 'planned_quantity': self.product_qty,
            'good_quantity': quantity, 'loss_quantity': len(operation.loss_keys),
            'component_value': raw, 'work_value': work, 'extra_value': self.extra_cost * self.product_qty,
            'total_value': total, 'good_value': good, 'loss_value': currency.round(total - good),
            'raw_svl_ids': consumed_moves.stock_valuation_layer_ids.ids,
            'accounting_status': 'manual_periodic_pending'}
        # This is an explicit cost allocation before native valuation. No SVL
        # is fabricated or corrected afterwards and the lost product never moves.
        finished.price_unit = good / quantity

    def _native_loss_close(self, good_keys, loss_keys, request_key):
        self.ensure_one()
        self = self.with_context({'allowed_company_ids': self.env.companies.ids,
                                  'lang': self.env.lang, 'tz': self.env.user.tz}).with_company(self.company_id)
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not all(self.env.user.has_group(g) for g in ('mrp.group_mrp_user', 'stock.group_stock_user')):
            raise AccessError(_('Потрібні права виробництва та складу.'))
        for keys in (good_keys, loss_keys):
            if (not isinstance(keys, (tuple, list)) or not keys or len(set(keys)) != len(keys)
                    or any(not isinstance(k, str) or not k or len(k) > 80 for k in keys)):
                raise ValidationError(_('Потрібен точний неповторний склад годних та втрачених виробів.'))
        if set(good_keys) & set(loss_keys) or not isinstance(request_key, str) or not request_key or len(request_key) > 64:
            raise ValidationError(_('Склад годних та втрачених виробів перетинається або невірний ключ запиту.'))
        with self.env.cr.savepoint():
            self._native_partial_lock()
            history = self._native_partial_history()
            if history:
                if (len(history) == 1 and history.disposition == 'terminal_loss' and history.request_key == request_key
                        and set(history.unit_ids.mapped('unit_key')) == set(good_keys)
                        and set(history.loss_ids.mapped('unit_key')) == set(loss_keys)):
                    return history
                raise UserError(_('Попередній випуск або втрати вже капіталізовані. Перерозподіл заборонений.'))
            self._native_partial_validate_configuration()
            self._native_partial_lock_cost_inputs()
            if (self.qty_produced or len(good_keys) + len(loss_keys) != self.product_qty
                    or (self.move_raw_ids | self.move_finished_ids).filtered(lambda m: m.state == 'done')
                    or self.move_raw_ids.filtered(lambda m: m.state == 'cancel')
                    or self.consumption == 'strict' or self.picking_type_id.create_backorder == 'always'):
                raise ValidationError(_('Потрібен повний первинний розподіл партії без попереднього Stock; суворе споживання не підтримується.'))
            operation = PartialPosting(self, tuple(sorted(good_keys)), 0, (), (), {}, loss_keys=tuple(sorted(loss_keys)))
            self._native_loss_before_post(operation)
            for product in self.move_raw_ids.product_id:
                moves = self.move_raw_ids.filtered(lambda m: m.product_id == product and m.state != 'cancel')
                required = sum(m.product_uom._compute_quantity(m.quantity, product.uom_id) for m in moves)
                quants = _read(self.env['stock.quant'].search([
                    ('product_id', '=', product.id), ('company_id', '=', self.company_id.id),
                    ('location_id', 'child_of', self.location_src_id.id)]))
                if float_compare(sum(quants.mapped('quantity')), required, precision_rounding=product.uom_id.rounding) < 0:
                    raise ValidationError(_('Недостатній фактичний запас компонентів: оцінка від’ємного залишку не підтримується.'))
            bases = {wo.id: wo._native_partial_cost_basis() for wo in self.workorder_ids}
            for wo in self.workorder_ids:
                wo._native_partial_check_checkpoint(None)
            configuration = json.loads(json.dumps(self._native_partial_configuration()))
            with operation.activate():
                # Prepare the actual full physical batch with native Odoo,
                # then declare only its good output. Picked actuals must agree.
                for move in self.move_raw_ids.filtered(lambda m: m.state != 'cancel'):
                    if not move.picked or float_compare(move.quantity, self.product_qty * move.unit_factor,
                                     precision_rounding=move.product_uom.rounding):
                        raise ValidationError(_('Спочатку підтвердьте фактичне повне споживання фізичної партії з браком. Автоматична підміна факту заборонена.'))
                self.qty_producing = self.product_qty
                self._set_qty_producing()
                raw_quantities = {m.id: m.quantity for m in self.move_raw_ids}
                self.qty_producing = len(good_keys)
                operation.phase = 'post'
                result = self.button_mark_done()
                # Follow the actual standard dialogs; never set skip flags on
                # an unvalidated RPC call or reduce the actual consumption.
                for _index in range(2):
                    if not isinstance(result, dict):
                        break
                    model = result.get('res_model')
                    if model not in ('mrp.consumption.warning', 'mrp.production.backorder'):
                        raise ValidationError(_('Невідоме штатне підтвердження завершення MO.'))
                    wizard = self.env[model].with_context(result['context']).create({})
                    result = wizard.action_confirm() if model == 'mrp.consumption.warning' else wizard.action_close_mo()
                finished = self.move_finished_ids.filtered(lambda m: m.state == 'done' and m.quantity > 0)
                raw = self.move_raw_ids.filtered(lambda m: m.state == 'done' and m.quantity > 0)
                lines = finished.move_line_ids.sorted('id')
                allocation = operation.allocation
                if (result is not True or self.state != 'done' or self.product_qty != len(good_keys) + len(loss_keys)
                        or self.qty_produced != len(good_keys) or not self.date_finished or not self.is_locked
                        or self.workorder_ids.filtered(lambda wo: wo.state != 'done')
                        or len(finished) != 1
                        or any(line.quantity <= 0 or line.quantity != int(line.quantity)
                               or line.product_id != self.product_id or line.company_id != self.company_id
                               or line.product_uom_id != self.product_uom_id
                               or line.location_id.usage != 'production' or line.location_dest_id != self.location_dest_id
                               or line.lot_id != self.lot_producing_id for line in lines)
                        or {m.id: m.quantity for m in raw} != raw_quantities
                        or (raw | finished).stock_valuation_layer_ids.account_move_id
                        or not allocation or not self.company_id.currency_id.is_zero(
                            sum(finished.stock_valuation_layer_ids.mapped('value')) - allocation['good_value'])
                        or sum(lines.mapped('quantity')) != len(good_keys)
                        or any(wo._native_partial_cost_basis() != bases[wo.id] for wo in self.workorder_ids)
                        or (self.move_raw_ids | self.move_finished_ids).filtered(lambda m: m.state not in ('done', 'cancel'))
                        or any(set(line.consume_line_ids.ids) != set(raw.move_line_ids.ids) for line in lines)):
                    raise ValidationError(_('Штатний Stock/завершення не підтвердив точний випуск, витрати або незмінний час.'))
                operation.new_line_ids = tuple(lines.ids)
                self._native_partial_after_stock(operation)
                operation.phase = 'audit'
                release = self.env['quality.native.partial.release'].create({
                    'production_id': self.id, 'request_key': request_key, 'before_quantity': 0,
                    'quantity': len(good_keys), 'configuration': configuration,
                    'disposition': 'terminal_loss', 'allocation': allocation})
                operation.release_id = release.id
                pending = iter(operation.keys)
                self.env['quality.native.partial.unit'].create([
                    {'release_id': release.id, 'unit_key': next(pending), 'move_line_id': line.id}
                    for line in lines for _index in range(int(line.quantity))])
                currency = self.company_id.currency_id
                remaining = allocation['loss_value']
                values = []
                for index, key in enumerate(operation.loss_keys):
                    value = currency.round(allocation['loss_value'] / len(loss_keys)) if index < len(loss_keys) - 1 else remaining
                    remaining = currency.round(remaining - value)
                    values.append({'release_id': release.id, 'unit_key': key, 'value': value, 'currency_id': currency.id})
                self.env['quality.native.production.loss'].create(values)
                self.env['quality.native.partial.cost'].create([{
                    'release_id': release.id, 'workorder_id': wo.id, 'cumulative_cost': operation.costs[wo.id],
                    'basis': bases[wo.id], 'timer_ids': [(6, 0, wo.time_ids.ids)]} for wo in self.workorder_ids])
                operation.phase = 'facts'
                self._native_partial_after_post(release, operation)
                return release
