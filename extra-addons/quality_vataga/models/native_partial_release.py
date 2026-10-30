"""Incremental native MRP posting. Odoo owns consumption, price and SVL.

No RPC/context unlock exists. This kernel is also exercised unchanged in the
disposable Community addon before it is enabled by the QUNIT integration.
"""
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools import float_compare
from odoo.addons.mrp.models.mrp_workorder import MrpWorkorder as CoreWorkorder
from odoo.addons.mrp.models.mrp_production import MrpProduction as CoreProduction
from odoo.addons.mrp_account.models.mrp_production import MrpProduction as AccountProduction

from .native_cost_provider import describe_method, method_definitions, provider_name, reviewed_subcontracting_methods


_CORE_COST = CoreWorkorder._cal_cost
_CORE_PRICES = (CoreProduction._cal_price, AccountProduction._cal_price)
_PARTIAL = ContextVar('quality_native_partial_posting', default=None)


def _read(records):
    records.check_access_rights('read')
    records.check_access_rule('read')
    return records


def partial_operation(production=None):
    operation = _PARTIAL.get()
    if production is not None and (not operation or len(production) != 1
            or production.env.cr is not operation.production.env.cr
            or production.id != operation.production.id):
        return None
    return operation


def partial_operation_allows(productions):
    return bool(productions and all(partial_operation(row) for row in productions))


@dataclass
class PartialPosting:
    production: object
    keys: tuple
    before: int
    old_finished_ids: tuple
    old_raw_ids: tuple
    previous_costs: dict
    phase: str = 'prepare'
    costs: dict = field(default_factory=dict)
    unit_ids: tuple = ()
    new_line_ids: tuple = ()
    release_id: int = 0
    loss_keys: tuple = ()
    loss_approvals: dict = field(default_factory=dict)
    allocation: dict = field(default_factory=dict)

    @contextmanager
    def activate(self):
        token = _PARTIAL.set(self)
        try:
            yield self
        finally:
            _PARTIAL.reset(token)


class NativePartialAuditAccess(models.AbstractModel):
    _name = 'quality.native.partial.access'
    _description = 'Native partial posting evidence access'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        return super()._search(expression.AND([domain, [
            ('production_id', 'in', self.env['mrp.production']._search([]))]]),
            offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['production_id'])
            self.env.cr.execute('SELECT DISTINCT production_id FROM ' + self._table + ' WHERE id IN %s', [tuple(self.ids)])
            _read(self.env['mrp.production'].browse([r[0] for r in self.env.cr.fetchall()]))
        return result

    @api.model_create_multi
    def create(self, vals_list):
        operation = partial_operation()
        if not operation or operation.phase != 'audit':
            raise UserError(_('Докази часткового випуску створює лише сервер після Stock.'))
        for values in vals_list:
            production = values.get('production_id') if self._name == 'quality.native.partial.release' else (
                self.env['quality.native.partial.release'].browse(values.get('release_id')).production_id.id)
            if production != operation.production.id:
                raise ValidationError(_('Доказ часткового випуску належить іншому MO.'))
        clean = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(NativePartialAuditAccess, self.with_context(clean)).create(vals_list)

    def write(self, vals):
        raise UserError(_('Докази часткового випуску та checkpoint незмінні.'))

    def unlink(self):
        raise UserError(_('Докази часткового випуску та checkpoint не видаляють.'))


class NativePartialRelease(models.Model):
    _name = 'quality.native.partial.release'
    _inherit = 'quality.native.partial.access'
    _description = 'Confirmed native incremental manufacturing posting'
    _order = 'id'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True)
    before_quantity = fields.Integer(required=True, readonly=True)
    quantity = fields.Integer(required=True, readonly=True)
    unit_ids = fields.One2many('quality.native.partial.unit', 'release_id', readonly=True)
    cost_ids = fields.One2many('quality.native.partial.cost', 'release_id', readonly=True)
    configuration = fields.Json(required=True, readonly=True)
    _sql_constraints = [
        ('request_unique', 'unique(production_id, request_key)', 'Запит вже проведений.'),
        ('positive_quantity', 'check(quantity > 0 AND before_quantity >= 0)', 'Потрібна додатна кількість.'),
    ]


class NativePartialUnit(models.Model):
    _name = 'quality.native.partial.unit'
    _inherit = 'quality.native.partial.access'
    _description = 'Identity key linked to a real native finished line'

    release_id = fields.Many2one('quality.native.partial.release', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one(related='release_id.production_id', store=True, readonly=True, index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    unit_key = fields.Char(required=True, readonly=True)
    move_line_id = fields.Many2one('stock.move.line', required=True, readonly=True, ondelete='restrict', index=True)
    _sql_constraints = [('unit_unique', 'unique(production_id, unit_key)', 'Цей виріб вже випущений.')]


class NativePartialCost(models.Model):
    _name = 'quality.native.partial.cost'
    _inherit = 'quality.native.partial.access'
    _description = 'Append-only native WO cost checkpoint'
    _order = 'id'

    release_id = fields.Many2one('quality.native.partial.release', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one(related='release_id.production_id', store=True, readonly=True, index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    workorder_id = fields.Many2one('mrp.workorder', required=True, readonly=True, ondelete='restrict', index=True)
    cumulative_cost = fields.Float(required=True, readonly=True)
    basis = fields.Json(required=True, readonly=True)
    timer_ids = fields.Many2many('mrp.workcenter.productivity', 'quality_partial_cost_timer_rel',
                                'cost_id', 'timer_id', readonly=True)
    _sql_constraints = [('release_wo_unique', 'unique(release_id, workorder_id)', 'Checkpoint WO вже створений.')]


class NativePartialProduction(models.Model):
    _inherit = 'mrp.production'

    quality_native_partial_revision = fields.Integer(readonly=True, copy=False, default=0)

    @api.model
    def _native_partial_cost_provider_report(self, expected_database, include_source=False):
        """Read-only registry evidence, callable from Odoo shell/Scheduled Action."""
        if self.env.cr.dbname != expected_database or not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Потрібні системний адміністратор і точна назва тестової staging-бази.'))
        production_allowed = {NativePartialProduction._cal_price, *_CORE_PRICES, *reviewed_subcontracting_methods(self)}
        workorder_allowed = {NativePartialWorkorder._cal_cost, _CORE_COST}
        report = {'database': self.env.cr.dbname, 'mode': 'READ ONLY',
                  'order': 'registry MRO definitions; super calls require source review',
                  'mrp.production._cal_price': describe_method(
                      self.env['mrp.production'], '_cal_price', production_allowed, include_source),
                  'mrp.workorder._cal_cost': describe_method(
                      self.env['mrp.workorder'], '_cal_cost', workorder_allowed, include_source),
                  'fields': {}, 'computed_methods': {}}
        for model in ('mrp.production', 'mrp.workorder', 'mrp.workcenter',
                      'mrp.workcenter.productivity', 'hr.employee'):
            if model not in self.env:
                continue
            records = self.env[model]
            for name, field_definition in records._fields.items():
                if not any(word in name for word in ('cost', 'employee', 'duration')):
                    continue
                compute = field_definition.compute
                report['fields'][model + '.' + name] = {
                    'type': field_definition.type, 'store': bool(field_definition.store),
                    'related': field_definition.related,
                    'compute': compute if isinstance(compute, str) else repr(compute),
                    'depends': self.env.registry.field_depends.get(field_definition, ())}
                if isinstance(compute, str):
                    report['computed_methods'][model + '.' + compute] = describe_method(
                        records, compute, include_source=include_source)
        return report

    @api.model_create_multi
    def create(self, vals_list):
        if any('quality_native_partial_revision' in v for v in vals_list):
            raise UserError(_('Блокування часткового випуску встановлює сервер.'))
        context = dict(self.env.context)
        context.pop('default_quality_native_partial_revision', None)
        return super(NativePartialProduction, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'quality_native_partial_revision' in vals:
            raise UserError(_('Блокування часткового випуску захищене.'))
        if self.ids and not partial_operation_allows(self) and {
                'product_qty', 'product_id', 'product_uom_id', 'company_id', 'bom_id',
                'lot_producing_id', 'move_finished_ids', 'move_raw_ids', 'workorder_ids',
                'location_src_id', 'location_dest_id', 'state', 'extra_cost', 'qty_produced'}.intersection(vals):
            if self._native_partial_history():
                raise UserError(_('Після часткового випуску конфігурацію та стан MO вручну не змінюють.'))
        return super().write(vals)

    def _native_partial_history(self):
        if not self.ids:
            return self.env['quality.native.partial.release']
        self.env['quality.native.partial.release'].flush_model()
        self.env.cr.execute('SELECT id FROM quality_native_partial_release WHERE production_id IN %s ORDER BY id', [tuple(self.ids)])
        records = self.env['quality.native.partial.release'].browse([r[0] for r in self.env.cr.fetchall()])
        # Ordinary stock workers need no new audit ACL for an ordinary MO.
        # Existing evidence still fails closed when hidden from this caller.
        return _read(records) if records else records

    def _native_partial_lock(self):
        self.ensure_one()
        if 'quality_transfer_revision' in self._fields:
            self._lock_quality_transfer_scope()
        self.flush_recordset(['quality_native_partial_revision'])
        # A revision UPDATE, not SELECT FOR UPDATE: a waiting REPEATABLE READ
        # caller must serialize/retry instead of using its stale snapshot.
        self.env.cr.execute('UPDATE mrp_production SET quality_native_partial_revision = '
                            'COALESCE(quality_native_partial_revision,0)+1 WHERE id=%s', [self.id])
        self.invalidate_recordset()
        self.modified(['quality_native_partial_revision'])

    def _native_partial_configuration(self):
        self.ensure_one()
        return {'product': self.product_id.id, 'quantity': self.product_qty,
                'uom': self.product_uom_id.id, 'company': self.company_id.id,
                'bom': self.bom_id.id, 'lot': self.lot_producing_id.id,
                'source': self.location_src_id.id, 'destination': self.location_dest_id.id,
                'extra_cost': self.extra_cost, 'cost_method': self.product_id.cost_method,
                'valuation': self.product_id.valuation,
                'route': [(wo.id, wo.operation_id.id, wo.workcenter_id.id)
                          for wo in self.workorder_ids.sorted('id')]}

    def _native_partial_lock_cost_inputs(self):
        """Serialize rate/timer edits before a new immutable checkpoint.

        Employee -> workcenter -> timer order also avoids holding a timer
        while waiting for an HR write which will recompute that same timer.
        A concurrent RR update must serialize/retry, never mix two cost bases.
        """
        self.ensure_one()
        timers = _read(self.workorder_ids.time_ids)
        centers = _read(self.workorder_ids.workcenter_id)
        sources = []
        if 'employee_id' in timers._fields:
            employees = _read(timers.employee_id)
            sources.append(('hr_employee', employees))
        sources.extend([('mrp_workcenter', centers), ('mrp_workcenter_productivity', timers)])
        for table, records in sources:
            if records:
                records.flush_recordset()
                self.env.cr.execute('SELECT id FROM ' + table + ' WHERE id IN %s ORDER BY id FOR UPDATE',
                                    [tuple(records.ids)])
                records.invalidate_recordset()

    def _native_partial_validate_configuration(self):
        self.ensure_one()
        # A hidden WO, component or timer must reject the whole operation,
        # never reduce the batch/cost scope visible to this caller.
        self.env.flush_all()
        for model, query in [
                ('mrp.workorder', 'SELECT id FROM mrp_workorder WHERE production_id=%s'),
                ('stock.move', 'SELECT id FROM stock_move WHERE production_id=%s OR raw_material_production_id=%s')]:
            self.env.cr.execute(query, [self.id, self.id] if model == 'stock.move' else [self.id])
            _read(self.env[model].browse([r[0] for r in self.env.cr.fetchall()]))
        if (self.company_id not in self.env.companies or self.state not in ('confirmed', 'progress', 'to_close')
                or self.product_id.type != 'product' or self.product_id.tracking not in ('none', 'lot')
                or self.product_uom_id != self.env.ref('uom.product_uom_unit')
                or self.product_uom_id != self.product_id.uom_id
                or not math.isfinite(self.product_qty) or self.product_qty <= 0
                or self.product_qty != int(self.product_qty) or self.move_byproduct_ids
                or self.product_id.cost_method != 'fifo' or self.product_id.valuation != 'manual_periodic'
                or self.move_raw_ids.filtered(lambda m: m.manual_consumption or m.product_id.tracking != 'none'
                    or m.product_id.type != 'product' or m.product_id.cost_method != 'fifo'
                    or m.product_id.valuation != 'manual_periodic')
                or self.location_src_id.usage != 'internal' or self.location_dest_id.usage != 'internal'
                or self.location_dest_id.scrap_location or not self.workorder_ids
                or self.warehouse_id.manufacture_steps != 'mrp_one_step'):
            raise ValidationError(_('Частковий випуск підтримує лише перевірений FIFO/periodic MO: один товар, '
                                    'цілі штуки, автоматичне споживання без serial/побічної продукції.'))
        if self.product_id.tracking == 'lot':
            lot = _read(self.lot_producing_id)
            if not lot or lot.product_id != self.product_id or (lot.company_id and lot.company_id != self.company_id):
                raise ValidationError(_('Потрібна реальна партія товару та компанії у MO.'))
        elif self.lot_producing_id:
            raise ValidationError(_('Товар без tracking не потребує партії.'))
        # The reviewed subcontracting override is a no-op only for this exact
        # non-subcontract branch. Its receipt-price helper is not authorized.
        downstream = _read(self.move_finished_ids.move_dest_ids)
        if (self.bom_id.type == 'subcontract'
                or ('is_subcontract' in downstream._fields and downstream.filtered('is_subcontract'))):
            raise ValidationError(_('Субпідрядний розрахунок ціни не підтримується цим адаптером.'))
        for method, allowed in [('_cal_price', {NativePartialProduction._cal_price, *_CORE_PRICES,
                                              *reviewed_subcontracting_methods(self)})]:
            unknown = [provider_name(owner, provider) for owner, provider in method_definitions(self, method)
                       if provider not in allowed]
            if unknown:
                raise ValidationError(_('Неперевірений постачальник розрахунку ціни MRP.\n%s') % '\n'.join(unknown))
        for workorder in self.workorder_ids:
            workorder._native_partial_cost_basis()
        for move in self.move_raw_ids | self.move_finished_ids:
            _read(move)
            if move.company_id != self.company_id or move.scrapped or (
                    move.production_id and (move.product_id != self.product_id
                                           or move.location_dest_id != self.location_dest_id)):
                raise ValidationError(_('Рухи не відповідають товару, призначенню або компанії MO.'))

    def _native_partial_before_post(self, operation):
        """QUNIT integration validates identities before capability activation."""

    def _native_partial_after_post(self, release, operation):
        """QUNIT integration records only identities in this confirmed posting."""

    def _native_partial_after_stock(self, operation):
        """Transaction boundary for integration verification, before checkpoint."""

    def _native_partial_release(self, unit_keys, request_key):
        self.ensure_one()
        self = self.with_context({'allowed_company_ids': self.env.companies.ids,
                                  'lang': self.env.lang, 'tz': self.env.user.tz}).with_company(self.company_id)
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not (self.env.user.has_group('mrp.group_mrp_user') and self.env.user.has_group('stock.group_stock_user')):
            raise AccessError(_('Потрібні права виробництва та складу.'))
        if (not isinstance(unit_keys, (tuple, list)) or not unit_keys
                or any(not isinstance(k, str) or not k or len(k) > 80 for k in unit_keys)
                or len(set(unit_keys)) != len(unit_keys)
                or not isinstance(request_key, str) or not request_key or len(request_key) > 64):
            raise ValidationError(_('Потрібен однозначний склад виробів і ключ запиту.'))
        keys = tuple(sorted(unit_keys))
        with self.env.cr.savepoint():
            self._native_partial_lock()
            history = self._native_partial_history()
            if 'disposition' in history._fields and history.filtered(lambda r: r.disposition == 'terminal_loss'):
                raise UserError(_('MO вже закрито з виробничими втратами. Новий випуск заборонений.'))
            previous = history.filtered(lambda r: r.request_key == request_key)
            if previous:
                if set(previous.unit_ids.mapped('unit_key')) != set(keys):
                    raise ValidationError(_('Ключ запиту вже використаний для інших виробів.'))
                return previous
            if set(history.unit_ids.mapped('unit_key')).intersection(keys):
                raise ValidationError(_('Серед вибраних виробів є вже випущені.'))
            self._native_partial_validate_configuration()
            self._native_partial_lock_cost_inputs()
            configuration = self._native_partial_configuration()
            # JSON roundtrip normalizes tuples in the persisted route.
            import json
            configuration = json.loads(json.dumps(configuration))
            if history and history[-1].configuration != configuration:
                raise ValidationError(_('Змінено конфігурацію раніше підтвердженого випуску.'))
            before = len(history.unit_ids)
            if self.qty_produced != before or before + len(keys) > self.product_qty:
                raise ValidationError(_('Фактичний випуск MO не відповідає захищеній історії виробів.'))
            checkpoints = {row.workorder_id.id: row for row in history.cost_ids.sorted('id')}
            for workorder in self.workorder_ids:
                workorder._native_partial_check_checkpoint(checkpoints.get(workorder.id))
            old_finished = self.move_finished_ids.filtered(lambda m: m.state == 'done')
            old_raw = self.move_raw_ids.filtered(lambda m: m.state == 'done')
            if not history and (old_finished or old_raw):
                raise ValidationError(_('Є попереднє проведення поза перевіреною історією часткового випуску.'))
            old_lines = (old_finished | old_raw).move_line_ids
            line_fields = ['quantity', 'lot_id', 'move_id', 'consume_line_ids', 'location_id', 'location_dest_id']
            line_snapshot = old_lines.read(line_fields)
            old_svl = (old_finished | old_raw).stock_valuation_layer_ids
            svl_fields = ['quantity', 'value', 'remaining_qty', 'remaining_value', 'stock_move_id']
            svl_snapshot = old_svl.read(svl_fields)
            timers = self.workorder_ids.time_ids
            timer_fields = ['date_start', 'date_end', 'workorder_id', 'workcenter_id', 'loss_id', 'user_id']
            timer_snapshot = timers.read(timer_fields)
            operation = PartialPosting(self, keys, before, tuple(old_finished.move_line_ids.ids),
                                       tuple(old_raw.ids), checkpoints)
            self._native_partial_before_post(operation)
            with operation.activate():
                self.write({'qty_producing': before + len(keys)})
                # Manual consumption is unsupported. Respect any picked actual
                # quantity instead of silently replacing it with a smaller one.
                for move in self.move_raw_ids.filtered(lambda m: m.state not in ('done', 'cancel') and m.picked):
                    if float_compare(move.quantity, len(keys) * move.unit_factor,
                                     precision_rounding=move.product_uom.rounding):
                        raise ValidationError(_('Зафіксоване споживання не відповідає поточному випуску.'))
                self._set_qty_producing()
                operation.phase = 'post'
                self._post_inventory(cancel_backorder=False)
                finished = self.move_finished_ids.filtered(lambda m: m.state == 'done') - old_finished
                raw = self.move_raw_ids.filtered(lambda m: m.state == 'done') - old_raw
                lines = finished.move_line_ids.sorted('id')
                if (self.qty_produced != before + len(keys) or sum(lines.mapped('quantity')) != len(keys)
                        or any(l.quantity <= 0 or l.quantity != int(l.quantity)
                               or l.product_uom_id != self.product_uom_id or l.company_id != self.company_id
                               or l.product_id != self.product_id or l.location_dest_id != self.location_dest_id
                               or l.location_id.usage != 'production'
                               or l.lot_id != self.lot_producing_id for l in lines)
                        or old_lines.read(line_fields) != line_snapshot or old_svl.read(svl_fields) != svl_snapshot
                        or self.workorder_ids.time_ids != timers or timers.read(timer_fields) != timer_snapshot
                        or set(operation.costs) != set(self.workorder_ids.ids)
                        or any(set(l.consume_line_ids.ids) != set(raw.move_line_ids.ids) for l in lines)):
                    raise ValidationError(_('Stock не підтвердив точний новий випуск або змінив його історію.'))
                operation.new_line_ids = tuple(lines.ids)
                self._native_partial_after_stock(operation)
                operation.phase = 'audit'
                values = []
                pending = iter(keys)
                for line in lines:
                    values.extend({'unit_key': next(pending), 'move_line_id': line.id}
                                  for _index in range(int(line.quantity)))
                release = self.env['quality.native.partial.release'].create({
                    'production_id': self.id, 'request_key': request_key,
                    'before_quantity': before, 'quantity': len(keys), 'configuration': configuration})
                operation.release_id = release.id
                self.env['quality.native.partial.unit'].create([dict(v, release_id=release.id) for v in values])
                self.env['quality.native.partial.cost'].create([{
                    'release_id': release.id, 'workorder_id': wo.id,
                    'cumulative_cost': operation.costs[wo.id],
                    'basis': wo._native_partial_cost_basis(), 'timer_ids': [(6, 0, wo.time_ids.ids)],
                } for wo in self.workorder_ids])
                operation.phase = 'facts'
                self._native_partial_after_post(release, operation)
                if self.qty_produced == self.product_qty:
                    self._native_partial_finalize(operation)
                return release

    def _native_partial_finalize(self, operation):
        """Run native closure, acknowledging Stock already posted exactly once."""
        self.ensure_one()
        if partial_operation(self) is not operation or operation.phase != 'facts':
            raise UserError(_('Завершення доступне лише всередині підтвердженого випуску.'))
        moves = self.move_finished_ids | self.move_raw_ids
        if (self.qty_produced != self.product_qty or len(self._native_partial_history().unit_ids) != self.product_qty
                or moves.filtered(lambda m: m.state not in ('done', 'cancel'))):
            raise ValidationError(_('Для завершення потрібен повний вже проведений складський факт.'))
        lines = moves.move_line_ids
        layers = moves.stock_valuation_layer_ids
        line_fields = ['quantity', 'lot_id', 'move_id', 'consume_line_ids', 'location_id', 'location_dest_id']
        layer_fields = ['quantity', 'value', 'remaining_qty', 'remaining_value', 'stock_move_id']
        timer_fields = ['date_start', 'date_end', 'workorder_id', 'workcenter_id', 'loss_id', 'user_id']
        timers = self.workorder_ids.time_ids
        before = (lines.read(line_fields), layers.read(layer_fields), timers.read(timer_fields))
        operation.phase = 'finalize'
        # The native button closes WOs, records date/lock/priority, and triggers
        # downstream assignment. Its one redundant inventory call is handled
        # below only in this exact private phase, after complete Stock proof.
        result = self.button_mark_done()
        if (result is not True or self.state != 'done' or not self.date_finished or not self.is_locked
                or self.workorder_ids.filtered(lambda wo: wo.state != 'done')
                or moves != (self.move_finished_ids | self.move_raw_ids)
                or lines != moves.move_line_ids or layers != moves.stock_valuation_layer_ids
                or timers != self.workorder_ids.time_ids
                or before != (lines.read(line_fields), layers.read(layer_fields), timers.read(timer_fields))):
            raise ValidationError(_('Штатне завершення не підтверджене або повторно змінило Stock/час.'))

    def _cal_price(self, consumed_moves):
        operation = partial_operation(self)
        if not operation:
            return super()._cal_price(consumed_moves)
        if operation.phase != 'post':
            raise UserError(_('Розрахунок ціни поза фазою складського проведення заборонений.'))
        operation.phase = 'cost'
        try:
            result = super()._cal_price(consumed_moves)
            if operation.loss_keys:
                self._native_loss_allocate(consumed_moves, operation)
            return result
        finally:
            operation.phase = 'post'

    def _post_inventory(self, cancel_backorder=False):
        operation = partial_operation(self)
        if operation and operation.phase == 'finalize':
            if (not cancel_backorder or self.qty_produced != self.product_qty
                    or len(self._native_partial_history().unit_ids) != self.product_qty
                    or (self.move_raw_ids | self.move_finished_ids).filtered(
                        lambda m: m.state not in ('done', 'cancel'))):
                raise ValidationError(_('Не підтверджено попереднє повне складське проведення.'))
            return True  # The native closing button must not post Stock twice.
        if self._native_partial_history() and not partial_operation_allows(self):
            raise UserError(_('MO має частковий випуск: використайте захищену операцію продовження.'))
        if partial_operation_allows(self) and (any(partial_operation(p).phase != 'post' for p in self)
                or any(bool(cancel_backorder) != bool(partial_operation(p).loss_keys) for p in self)):
            raise UserError(_('Невірна фаза часткового проведення або скасування залишку.'))
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    def _split_productions(self, amounts=False, cancel_remaining_qty=False, set_consumed_qty=False):
        if partial_operation_allows(self) or self._native_partial_history():
            raise UserError(_('Перевірений частковий випуск не створює Backorder.'))
        return super()._split_productions(amounts=amounts, cancel_remaining_qty=cancel_remaining_qty,
                                          set_consumed_qty=set_consumed_qty)

    def button_mark_done(self):
        operation = partial_operation(self)
        if operation and operation.loss_keys and operation.phase == 'post':
            return super().button_mark_done()
        if operation and operation.phase == 'finalize':
            return super().button_mark_done()
        if partial_operation_allows(self) or self._native_partial_history():
            if all(p.state == 'done' for p in self) and not partial_operation_allows(self):
                return True
            raise UserError(_('Завершіть залишок через перевірений частковий випуск.'))
        return super().button_mark_done()


class NativePartialWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    def _native_partial_cost_basis(self):
        self.ensure_one()
        allowed = {NativePartialWorkorder._cal_cost, _CORE_COST}
        unknown = [provider_name(owner, provider) for owner, provider in method_definitions(self, '_cal_cost')
                   if provider not in allowed]
        if unknown:
            raise ValidationError(_('Неперевірений постачальник вартості WO.\n%s') % '\n'.join(unknown))
        self.env['mrp.workcenter.productivity'].flush_model()
        self.env.cr.execute('SELECT id FROM mrp_workcenter_productivity WHERE workorder_id=%s ORDER BY id', [self.id])
        timers = _read(self.env['mrp.workcenter.productivity'].browse([r[0] for r in self.env.cr.fetchall()]))
        if timers.filtered(lambda t: not t.date_end or t.date_end < t.date_start):
            raise ValidationError(_('Спочатку закрийте робочі інтервали.'))
        if not timers:
            raise ValidationError(_('Потрібні фактичні закриті інтервали кожної WO; розрахунковий час не створюється.'))
        if not math.isfinite(self.workcenter_id.costs_hour) or self.workcenter_id.costs_hour < 0:
            raise ValidationError(_('Ставка робочого центру має бути скінченною та невід’ємною.'))
        basis = {'workcenter': self.workcenter_id.id, 'company': self.company_id.id,
                'rate': self.workcenter_id.costs_hour,
                'timers': {str(t.id): [fields.Datetime.to_string(t.date_start), fields.Datetime.to_string(t.date_end),
                                     t.loss_id.id, t.workcenter_id.id, t.user_id.id] for t in timers}}
        if 'employee_cost' in timers._fields:
            # Fields and formulas supplied by the staging diagnostic. This
            # snapshots their inputs; it does NOT authorize an unknown _cal_cost.
            if (not {'employee_id', 'total_cost', 'duration'}.issubset(timers._fields)
                    or timers._fields['employee_id'].comodel_name != 'hr.employee'
                    or 'employee_costs_hour' not in self.workcenter_id._fields
                    or 'hourly_cost' not in self.env['hr.employee']._fields):
                raise ValidationError(_('Невідомий контракт вартості працівника.'))
            _read(timers.employee_id)
            amounts = ([e.hourly_cost for e in timers.employee_id] + [self.workcenter_id.employee_costs_hour]
                       + [value for t in timers for value in (t.employee_cost, t.duration, t.total_cost)])
            if any(not math.isfinite(value) or value < 0 for value in amounts):
                raise ValidationError(_('Ставки, час та вартість працівника мають бути скінченними та невід’ємними.'))
            basis['employee_rates'] = {str(e.id): e.hourly_cost for e in timers.employee_id}
            basis['employee_fallback_rate'] = self.workcenter_id.employee_costs_hour
            basis['employee_timers'] = {str(t.id): [t.employee_id.id, t.employee_cost, t.duration,
                                                   t.total_cost, t.loss_type, t.company_id.id] for t in timers}
        return basis

    def _native_partial_check_checkpoint(self, checkpoint):
        basis = self._native_partial_cost_basis()
        if checkpoint:
            old = checkpoint.basis
            if (any(basis[k] != old[k] for k in ('workcenter', 'company', 'rate'))
                    or basis.get('employee_fallback_rate') != old.get('employee_fallback_rate')
                    or any(basis.get('employee_rates', {}).get(k) != v for k, v in old.get('employee_rates', {}).items())
                    or bool(basis.get('employee_timers')) != bool(old.get('employee_timers'))
                    or any(basis.get('employee_timers', {}).get(k) != v for k, v in old.get('employee_timers', {}).items())
                    or any(basis['timers'].get(k) != v for k, v in old['timers'].items())):
                raise ValidationError(_('Змінено вже капіталізовані інтервали або ставку WO.'))
            cutoff = max(value[1] for value in old['timers'].values())
            if any(value[0] < cutoff for key, value in basis['timers'].items() if key not in old['timers']):
                raise ValidationError(_('Новий інтервал перекриває вже капіталізовану роботу.'))
        cumulative = self._cal_cost()
        booked = checkpoint.cumulative_cost if checkpoint else 0
        if not math.isfinite(cumulative) or not math.isfinite(booked) or cumulative < booked:
            raise ValidationError(_('Вартість WO має від’ємну або нечислову різницю.'))
        return cumulative

    def _cal_cost(self):
        cumulative = super()._cal_cost()
        operation = partial_operation(self.production_id) if len(self) == 1 else None
        if not operation or operation.phase != 'cost':
            return cumulative
        checkpoint = operation.previous_costs.get(self.id)
        booked = checkpoint.cumulative_cost if checkpoint else 0
        if not math.isfinite(cumulative) or cumulative < booked or self.id in operation.costs:
            raise ValidationError(_('Некоректний або повторний розрахунок вартості WO.'))
        operation.costs[self.id] = cumulative
        return cumulative - booked

    def write(self, vals):
        if {'production_id', 'operation_id', 'workcenter_id', 'product_uom_id',
                'blocked_by_workorder_ids', 'needed_by_workorder_ids'}.intersection(vals):
            if self.production_id._native_partial_history():
                raise UserError(_('Ідентичність маршруту частково випущеного MO незмінна.'))
        return super().write(vals)


class NativePartialMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _native_partial_protected(self):
        if not self.ids:
            return False
        self.env['quality.native.partial.unit'].flush_model()
        self.env.cr.execute('''SELECT 1 FROM quality_native_partial_unit u
            WHERE u.move_line_id IN %s OR EXISTS (
              SELECT 1 FROM stock_move_line_consume_rel r
              WHERE r.consume_line_id=u.move_line_id AND r.produce_line_id IN %s) LIMIT 1''',
            [tuple(self.ids), tuple(self.ids)])
        return bool(self.env.cr.fetchone())

    def write(self, vals):
        operation = partial_operation()
        old = self.browse(operation.old_finished_ids) & self if operation else self.browse()
        if old and set(vals) == {'consume_line_ids'} and operation.phase == 'post':
            raw = operation.production.move_raw_ids.filtered(
                lambda m: m.state == 'done' and m.id not in operation.old_raw_ids).move_line_ids
            commands = vals['consume_line_ids']
            if (len(commands) != 1 or commands[0][0] != 6 or commands[0][1] != 0
                    or set(commands[0][2]) != set(raw.ids)
                    or any(l.move_id.production_id != operation.production or l.move_id.state != 'done' for l in self)):
                raise ValidationError(_('Неочікувана спроба переприв’язати старий складський випуск.'))
            remaining = self - old
            return super(NativePartialMoveLine, remaining).write(vals) if remaining else True
        if {'quantity', 'picked', 'lot_id', 'product_id', 'move_id', 'product_uom_id',
                'company_id', 'location_id', 'location_dest_id'}.intersection(vals):
            for line in self.filtered(lambda l: l.move_id.production_id):
                production = line.move_id.production_id
                if production._native_partial_history() and not partial_operation(production):
                    raise UserError(_('Готові строки залишку змінює лише перевірена операція випуску.'))
        if {'quantity', 'product_id', 'product_uom_id', 'company_id', 'move_id', 'lot_id', 'picked',
                'location_id', 'location_dest_id', 'consume_line_ids', 'produce_line_ids'}.intersection(vals):
            if self._native_partial_protected():
                raise UserError(_('Проведені строки часткового випуску та споживання незмінні.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        moves = self.env['stock.move'].browse([v['move_id'] for v in vals_list if v.get('move_id')])
        if moves.move_line_ids._native_partial_protected():
            raise UserError(_('До проведених рухів часткового випуску не додають строки.'))
        for production in moves.production_id:
            if production._native_partial_history() and not partial_operation(production):
                raise UserError(_('Нові готові строки часткового MO створює лише серверний випуск.'))
        return super().create(vals_list)

    def unlink(self):
        if self._native_partial_protected():
            raise UserError(_('Проведені строки часткового випуску не видаляють.'))
        return super().unlink()


class NativePartialMove(models.Model):
    _inherit = 'stock.move'

    def _native_partial_check_movement(self):
        for production in self.production_id | self.raw_material_production_id:
            if production._native_partial_history() and not partial_operation(production):
                raise UserError(_('Проведення залишку можливе лише через перевірений частковий випуск.'))

    def _action_done(self, cancel_backorder=False):
        self._native_partial_check_movement()
        return super()._action_done(cancel_backorder=cancel_backorder)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            parents = self.env['mrp.production'].browse([
                values[n] for n in ('production_id', 'raw_material_production_id') if values.get(n)])
            for production in parents:
                if production._native_partial_history() and not partial_operation(production):
                    raise UserError(_('Структура рухів частково випущеного MO незмінна.'))
        return super().create(vals_list)

    def write(self, vals):
        if (vals.get('state') in ('done', 'cancel') or {'production_id', 'raw_material_production_id',
                'product_id', 'product_uom', 'company_id', 'location_id', 'location_dest_id', 'product_uom_qty'}.intersection(vals)
                or ({'quantity', 'picked'}.intersection(vals) and self.production_id)):
            self._native_partial_check_movement()
            for name in ('production_id', 'raw_material_production_id'):
                if vals.get(name):
                    production = self.env['mrp.production'].browse(vals[name])
                    if production._native_partial_history() and not partial_operation(production):
                        raise UserError(_('Не можна приєднати сторонній рух до часткового MO.'))
        if {'quantity', 'product_uom_qty', 'price_unit', 'product_id', 'product_uom', 'company_id', 'picked',
                'production_id', 'raw_material_production_id', 'move_line_ids', 'state',
                'location_id', 'location_dest_id'}.intersection(vals) and self.move_line_ids._native_partial_protected():
            raise UserError(_('Рухи часткового випуску та споживання незмінні.'))
        return super().write(vals)

    def unlink(self):
        if self.move_line_ids._native_partial_protected():
            raise UserError(_('Рухи часткового випуску не видаляють.'))
        return super().unlink()


class NativePartialTimer(models.Model):
    _inherit = 'mrp.workcenter.productivity'

    def _native_partial_capitalized(self):
        if not self.ids:
            return False
        self.env['quality.native.partial.cost'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_partial_cost_timer_rel WHERE timer_id IN %s LIMIT 1', [tuple(self.ids)])
        return bool(self.env.cr.fetchone())

    def write(self, vals):
        if {'date_start', 'date_end', 'workorder_id', 'workcenter_id', 'user_id', 'loss_id', 'duration',
                'employee_id', 'employee_cost', 'total_cost', 'loss_type', 'company_id'}.intersection(vals):
            if self._native_partial_capitalized():
                raise UserError(_('Вже капіталізовані інтервали незмінні.'))
        return super().write(vals)

    def _compute_field_value(self, field):
        # Stored recomputations bypass Model.write(). Guard them separately so
        # changing hr.employee.hourly_cost cannot silently rewrite booked labor.
        if (field.store and field.name in {'employee_cost', 'employee_id', 'duration'}
                and self._native_partial_capitalized()):
            raise UserError(_('Перерахунок вже капіталізованого часу або ставки працівника заборонений.'))
        return super()._compute_field_value(field)

    def unlink(self):
        if self._native_partial_capitalized():
            raise UserError(_('Вже капіталізовані інтервали не видаляють.'))
        return super().unlink()
