"""QUNIT identity continuation and actual native stock facts, without costing."""
from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression

from .native_backorder import NativeBackorderOperation, native_operation


def _read(records):
    records.check_access_rights('read')
    records.check_access_rule('read')
    return records


def _require_roles(env):
    if not (env.user.has_group('mrp.group_mrp_user') and env.user.has_group('stock.group_stock_user')):
        raise AccessError(_('Потрібні одночасно ролі оператора виробництва та працівника складу.'))


class QualityUnitContinuation(models.Model):
    _name = 'quality.unit.continuation'
    _description = 'Преемність фізичного QUNIT у штатному Backorder'
    _order = 'id'
    _check_company_auto = True

    unit_id = fields.Many2one('quality.production.unit', required=True, readonly=True, ondelete='restrict', index=True)
    source_production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    _sql_constraints = [('unit_source_unique', 'unique(unit_id, source_production_id)', 'QUNIT вже перенесено до штатного Backorder.')]

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = [('unit_id', 'in', self.env['quality.production.unit']._search([])),
                 ('production_id', 'in', self.env['mrp.production']._search([])),
                 ('source_production_id', 'in', self.env['mrp.production']._search([]))]
        return super()._search(expression.AND([domain, scope]), offset=offset, limit=limit,
                               order=order, access_rights_uid=access_rights_uid)

    @api.model_create_multi
    def create(self, vals_list):
        operation = native_operation()
        _require_roles(self.env)
        for values in vals_list:
            if (not operation or set(values) != {'unit_id', 'source_production_id', 'production_id'}
                    or values['unit_id'] not in operation.remaining_ids
                    or values['source_production_id'] != operation.production.id
                    or values['production_id'] not in operation.children):
                raise UserError(_('Преемність QUNIT фіксують лише після реального штатного Backorder.'))
            child = _read(self.env['mrp.production'].browse(values['production_id']))
            unit = _read(self.env['quality.production.unit'].browse(values['unit_id']))
            if (child.quality_native_parent_id != operation.production
                    or not child.quality_transfer_controlled
                    or unit._quality_current_production() != operation.production):
                raise ValidationError(_('Преемність не відповідає незавершеному QUNIT та захищеному Backorder.'))
        return super(QualityUnitContinuation, self.with_context({
            key: value for key, value in self.env.context.items() if not key.startswith('default_')})).create(vals_list)

    def write(self, vals):
        raise UserError(_('Фактична преемність QUNIT незмінна.'))

    def unlink(self):
        raise UserError(_('Історію преемності QUNIT не видаляють.'))

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['unit_id', 'source_production_id', 'production_id'])
            self.env.cr.execute('SELECT unit_id, source_production_id, production_id FROM quality_unit_continuation WHERE id IN %s', [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            _read(self.env['quality.production.unit'].browse({row[0] for row in rows}))
            _read(self.env['mrp.production'].browse({row[index] for row in rows for index in (1, 2)}))
        return result


class QualityUnitStockFact(models.Model):
    _name = 'quality.unit.stock.fact'
    _description = 'Підтверджений QUNIT у штатному складському випуску'
    _order = 'id'

    unit_id = fields.Many2one('quality.production.unit', required=True, readonly=True, ondelete='restrict', index=True)
    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', index=True)
    move_line_id = fields.Many2one('stock.move.line', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True, index=True)
    _sql_constraints = [('unit_unique', 'unique(unit_id)', 'Фізичний QUNIT вже випущено.')]

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = [('unit_id', 'in', self.env['quality.production.unit']._search([])),
                 ('production_id', 'in', self.env['mrp.production']._search([])),
                 ('move_line_id', 'in', self.env['stock.move.line']._search([]))]
        return super()._search(expression.AND([domain, scope]), offset=offset, limit=limit,
                               order=order, access_rights_uid=access_rights_uid)

    @api.model_create_multi
    def create(self, vals_list):
        operation = native_operation()
        _require_roles(self.env)
        for values in vals_list:
            if (not operation or set(values) != {'unit_id', 'production_id', 'move_line_id', 'request_key'}
                    or values['unit_id'] not in operation.unit_ids
                    or values['production_id'] != operation.production.id or not values['request_key']):
                raise UserError(_('Складський факт QUNIT створюють лише після штатного проведення.'))
            unit = _read(self.env['quality.production.unit'].browse(values['unit_id']))
            line = _read(self.env['stock.move.line'].browse(values['move_line_id']))
            production = operation.production
            if (production.state != 'done' or line.move_id.state != 'done'
                    or line.move_id.production_id != production or line.move_id.scrapped
                    or line.product_id != unit.product_id or line.company_id != unit.company_id
                    or line.location_dest_id != production.location_dest_id
                    or unit._quality_current_production() != production
                    or line.product_uom_id != unit.product_id.uom_id
                    or unit.product_id.tracking not in ('none', 'lot') or line.quantity <= 0):
                raise ValidationError(_('Немає відповідного фактичного складського випуску цього QUNIT.'))
            if unit.product_id.tracking == 'lot':
                lot = _read(line.lot_id)
                if (operation.quantity != production.product_qty or operation.remaining_ids
                        or not lot or lot != production.lot_producing_id
                        or lot.product_id != unit.product_id
                        or (lot.company_id and lot.company_id != unit.company_id)
                        or (unit.lot_id and unit.lot_id != lot)):
                    raise ValidationError(_('Складський факт lot-QUNIT потребує повного випуску та правильної фактичної партії у проведеній строкі.'))
        records = super(QualityUnitStockFact, self.with_context({
            key: value for key, value in self.env.context.items() if not key.startswith('default_')})).create(vals_list)
        for line in records.move_line_id:
            self.flush_model(['move_line_id'])
            self.env.cr.execute('SELECT count(*) FROM quality_unit_stock_fact WHERE move_line_id=%s', [line.id])
            if self.env.cr.fetchone()[0] != line.quantity:
                raise ValidationError(_('Складська строка повинна точно відповідати переліку фізично підтверджених QUNIT.'))
        return records

    def write(self, vals):
        raise UserError(_('Фактичні складські зв’язки QUNIT незмінні.'))

    def unlink(self):
        raise UserError(_('Фактичні складські зв’язки QUNIT не видаляють.'))

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.flush_recordset(['unit_id', 'production_id', 'move_line_id'])
            self.env.cr.execute('SELECT unit_id, production_id, move_line_id FROM quality_unit_stock_fact WHERE id IN %s', [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            _read(self.env['quality.production.unit'].browse({row[0] for row in rows}))
            _read(self.env['mrp.production'].browse({row[1] for row in rows}))
            _read(self.env['stock.move.line'].browse({row[2] for row in rows}))
        return result


class NativeQualityUnit(models.Model):
    _inherit = 'quality.production.unit'

    native_continuation_ids = fields.One2many('quality.unit.continuation', 'unit_id', readonly=True)
    native_stock_fact_ids = fields.One2many('quality.unit.stock.fact', 'unit_id', readonly=True)

    def _quality_continuations(self):
        self.ensure_one()
        self.env['quality.unit.continuation'].flush_model(['unit_id'])
        self.env.cr.execute('SELECT id FROM quality_unit_continuation WHERE unit_id=%s ORDER BY id', [self.id])
        return _read(self.env['quality.unit.continuation'].browse([row[0] for row in self.env.cr.fetchall()]))

    def _quality_current_production(self):
        self.ensure_one()
        rows = self._quality_continuations()
        return _read(rows[-1:].production_id or self.production_id)

    def _quality_matches_production(self, production):
        self.ensure_one()
        return production == self.production_id or production in self._quality_continuations().production_id

    def _stock_posted(self):
        self.env['quality.unit.stock.fact'].flush_model(['unit_id'])
        self.env.cr.execute('SELECT 1 FROM quality_unit_stock_fact WHERE unit_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone()) or super()._stock_posted()


class NativeQualityProduction(models.Model):
    _inherit = 'mrp.production'

    native_continuation_ids = fields.One2many(
        'quality.unit.continuation', 'production_id', string='Успадковані QUNIT', readonly=True, copy=False)
    native_stock_fact_ids = fields.One2many(
        'quality.unit.stock.fact', 'production_id', string='Фактичний поштучний випуск', readonly=True, copy=False)

    def _quality_equivalent_workorder(self, original):
        """Use the actual native copy lineage; never operation names/sequence."""
        self.ensure_one()
        candidates = self.workorder_ids
        for candidate in candidates:
            ancestor = candidate
            seen = set()
            while ancestor:
                _read(ancestor)
                if ancestor == original:
                    return candidate
                if ancestor.id in seen:
                    raise ValidationError(_('Цикл преемності робочих операцій.'))
                seen.add(ancestor.id)
                ancestor = ancestor.quality_native_parent_workorder_id
        raise ValidationError(_('Немає підтвердженої преемності робочої операції у цьому MO.'))

    def action_open_quality_native_release(self):
        self.ensure_one()
        self._check_full_release_test_access()
        if self.state == 'done':
            return {'type': 'ir.actions.act_window', 'res_model': 'quality.unit.stock.fact',
                    'name': _('Фактичний випуск QUNIT'), 'view_mode': 'tree,form',
                    'domain': [('production_id', '=', self.id)]}
        with self.env.cr.savepoint():
            units = self._full_release_units()
            self._validate_quality_full_batch(units)
            wizard = self.env['quality.full.release.wizard'].create({'production_id': self.id})
            return {'type': 'ir.actions.act_window', 'res_model': wizard._name,
                    'name': _('Підтвердити повний випуск QUNIT'), 'view_mode': 'form',
                    'res_id': wizard.id, 'target': 'new'}

    def _quality_native_release(self, units, request_key, confirmed=False, full_only=False, finished_lot=None):
        """Private ORM integration entry, deliberately not an RPC action.

        Public staging confirmation uses full_only; the legacy experimental
        posting engine and partial public release remain closed.
        """
        self.ensure_one()
        _require_roles(self.env)
        if full_only:
            self._check_full_release_test_access()
        self.check_access_rights('write')
        self.check_access_rule('write')
        _read(units)
        if (not confirmed or len(set(units.ids)) != len(units)
                or not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 64):
            raise ValidationError(_('Потрібне явне підтвердження фізичного складу QUNIT і випуску з унікальним запитом.'))
        with self.env.cr.savepoint():
            self._lock_quality_transfer_scope()
            selected_lot = None
            if self.product_id.tracking == 'lot':
                selected_lot = self._validate_full_release_lot(
                    units, finished_lot if finished_lot is not None else self.lot_producing_id)
            elif finished_lot:
                self._validate_full_release_lot(units, finished_lot)
            self.env['quality.unit.stock.fact'].flush_model()
            self.env.cr.execute('SELECT id FROM quality_unit_stock_fact WHERE production_id=%s AND request_key=%s ORDER BY id', [self.id, request_key])
            previous = _read(self.env['quality.unit.stock.fact'].browse([row[0] for row in self.env.cr.fetchall()]))
            if previous:
                if set(previous.unit_id.ids) != set(units.ids):
                    raise ValidationError(_('Цей запит вже використано для іншого складу виробів.'))
                if selected_lot and set(previous.move_line_id.lot_id.ids) != set(selected_lot.ids):
                    raise ValidationError(_('Цей випуск вже підтверджено з іншою складською партією.'))
                return previous
            all_units = self._quality_native_units()
            remaining = all_units - units
            if full_only and (remaining or len(units) != self.product_qty):
                raise ValidationError(_('Публічна дія дозволяє лише повний випуск всієї партії. Частковий випуск закрито.'))
            self._validate_quality_native_units(units, remaining)
            context = {'allowed_company_ids': self.env.companies.ids, 'lang': self.env.lang, 'tz': self.env.user.tz}
            production = self.with_context(context).with_company(self.company_id)
            operation = NativeBackorderOperation(production, len(units), tuple(units.ids), tuple(remaining.ids))
            before_checks = self._full_release_checks().filtered(
                lambda check: check.x_control_stage == 'in_process' and check.unit_admission_role == 'required') if not remaining else self.env['quality.check']
            with operation.activate():
                if selected_lot and production.lot_producing_id != selected_lot:
                    production.write({'lot_producing_id': selected_lot.id})
                children = production._native_quality_complete()
                if not remaining:
                    after_checks = self._full_release_checks().filtered(
                        lambda check: check.x_control_stage == 'in_process' and check.unit_admission_role == 'required')
                    if set(before_checks.ids) != set(after_checks.ids):
                        raise ValidationError(_('Під час штатного проведення змінився склад обов’язкових QC. Випуск скасовано; завершіть нові перевірки.'))
                if children:
                    self.env['quality.unit.continuation'].create([
                        {'unit_id': unit.id, 'source_production_id': self.id, 'production_id': children.id}
                        for unit in remaining])
                lines = production.move_finished_ids.filtered(
                    lambda move: move.state == 'done' and move.product_id == self.product_id and not move.scrapped).move_line_ids.sorted('id')
                pending = list(units.sorted('id'))
                values = []
                for line in lines:
                    if line.quantity != int(line.quantity) or line.product_uom_id != self.product_id.uom_id:
                        raise ValidationError(_('Неоднозначна одиниця або кількість фактичного випуску.'))
                    for _index in range(int(line.quantity)):
                        if not pending:
                            raise ValidationError(_('Штатний складський випуск перевищує перелік QUNIT.'))
                        unit = pending.pop(0)
                        values.append({'unit_id': unit.id, 'production_id': self.id,
                                       'move_line_id': line.id, 'request_key': request_key})
                if pending:
                    raise ValidationError(_('Не всі QUNIT мають фактичну складську строку.'))
                facts = self.env['quality.unit.stock.fact'].create(values)
            self.message_post(body=escape(_(
                'Штатний складський випуск QUNIT: %(units)s; Backorder: %(backorder)s.',
                units=', '.join(units.mapped('name')), backorder=children.display_name or '—')), subtype_xmlid='mail.mt_note')
            return facts

    def _quality_native_units(self):
        self.ensure_one()
        units = self.env['quality.production.unit'].search([('production_id', '=', self.id)])
        units |= self.env['quality.unit.continuation'].search([('production_id', '=', self.id)]).unit_id
        return units.filtered(lambda unit: unit._quality_current_production() == self)

    def _validate_quality_native_units(self, units, remaining):
        self.ensure_one()
        if not remaining:
            return self._validate_quality_full_batch(units)
        if (not units or self.company_id not in self.env.companies
                or not self.quality_transfer_controlled or len(units | remaining) != self.product_qty
                or self.product_uom_id != self.product_id.uom_id
                or self.product_uom_id != self.env.ref('uom.product_uom_unit')
                or any(unit.product_id != self.product_id or unit.company_id != self.company_id
                       or unit._quality_current_production() != self for unit in units | remaining)):
            raise ValidationError(_('Потрібна однозначна захищена партія MO та унікальні QUNIT цієї компанії.'))
        self._check_named_stock_history()
        (units | remaining)._assert_not_stock_posted()
        self.env['quality.check'].flush_model(['production_id', 'point_id', 'operation_run_id', 'unit_admission_role'])
        self.env.cr.execute('''SELECT q.id FROM quality_check q JOIN quality_point p ON p.id=q.point_id
            WHERE q.production_id=%s AND p.x_control_stage='in_process'
                AND q.unit_admission_role='required' AND q.operation_run_id IS NULL LIMIT 1''', [self.id])
        if self.env.cr.fetchone():
            raise ValidationError(_('Є обов’язкова міжопераційна QC без визначеної партії QRUN/QUNIT. Спочатку явно визначте її сферу або роль; випуск заборонений.'))
        if self.workorder_ids.time_ids.filtered(lambda timer: not timer.date_end):
            raise ValidationError(_('Спочатку коректно завершіть усі робочі інтервали перед штатним розділенням.'))
        # Discover the actual route, including operations hidden by a rule.
        # Missing read access must reject release, not shorten that route.
        self.env['mrp.workorder'].flush_model(['production_id'])
        self.env.cr.execute('SELECT id FROM mrp_workorder WHERE production_id=%s ORDER BY id', [self.id])
        workorders = _read(self.env['mrp.workorder'].browse([row[0] for row in self.env.cr.fetchall()]))
        first = workorders.filtered(lambda row: not row.blocked_by_workorder_ids)
        if len(first) != 1:
            raise ValidationError(_('Підтримується лише однозначний лінійний виробничий маршрут.'))
        route, current = [], first
        while current:
            if current in route or len(current) != 1:
                raise ValidationError(_('Неоднозначний або циклічний маршрут.'))
            route.append(current)
            current = current.needed_by_workorder_ids
        if len(route) != len(workorders) or len(route) < 2:
            raise ValidationError(_('Потрібен повний лінійний маршрут щонайменше з двома операціями.'))
        # The first controlled operation is evidenced by its inspected handoff;
        # every later operation requires actual processed execution plus closed QC.
        for unit in units:
            for index, workorder in enumerate(route):
                self.env['quality.check'].flush_model(['operation_run_id', 'unit_admission_role'])
                self.env['quality.check.sample'].flush_model(['quality_check_id', 'controlled_unit_id'])
                self.env.cr.execute('''SELECT DISTINCT q.id FROM quality_check q
                    JOIN quality_check_sample s ON s.quality_check_id=q.id
                    JOIN quality_production_run r ON r.id=q.operation_run_id
                    WHERE s.controlled_unit_id=%s AND q.unit_admission_role='required'
                        AND r.workorder_id=%s ORDER BY q.id''', [unit.id, workorder.id])
                checks = _read(self.env['quality.check'].browse([row[0] for row in self.env.cr.fetchall()]))
                if not checks:
                    raise ValidationError(_('Немає обов’язкового контролю виробу на кожній операції.'))
                latest = _read(checks.operation_run_id).sorted('id')[-1:]
                required = checks.filtered(lambda row: row.operation_run_id == latest)
                for check in required:
                    _read(check)
                    sample = check.sample_ids.filtered(lambda row: row.controlled_unit_id == unit)
                    if check.quality_state == 'none' or check._get_unit_admission_error(sample):
                        raise ValidationError(_('Потрібні завершений QC, PASS, ЗВТ, відсутність Alert і допуск кожної операції.'))
                admissions = self.env['quality.unit.admission'].search([
                    ('controlled_unit_id', '=', unit.id), ('operation_run_id', '=', latest.id), ('state', '=', 'confirmed')])
                if not admissions:
                    raise ValidationError(_('Немає підтвердженого допуску інспектора на кожній операції.'))
                if index:
                    executions = self.env['quality.unit.execution'].search([
                        ('controlled_unit_id', '=', unit.id), ('workorder_id', '=', workorder.id), ('state', '=', 'processed')])
                    if len(executions) != 1:
                        raise ValidationError(_('Не підтверджено фактичне виконання всіх кінцевих операцій.'))
                else:
                    sent = self.env['quality.unit.transfer'].search([
                        ('controlled_unit_id', '=', unit.id), ('source_workorder_id', '=', workorder.id), ('state', 'in', ('received', 'advanced'))])
                    if not sent:
                        raise ValidationError(_('Немає прийнятої поштучної передачі після першої операції.'))
        # Minimal supported continuation: a held unit before its first handoff.
        # Do not silently relocate an existing receipt/execution to another WO.
        if remaining:
            self.env['quality.unit.transfer'].flush_model()
            self.env.cr.execute("SELECT 1 FROM quality_unit_transfer WHERE controlled_unit_id IN %s AND state IN ('sent','received','advanced') LIMIT 1", [tuple(remaining.ids)])
            if self.env.cr.fetchone():
                raise ValidationError(_('Залишок вже має передачі між операціями. Перенесення такого незавершеного маршруту ще не перевірено; розділення заборонене.'))


class NativeFactMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _quality_native_fact_protected(self):
        self.env['quality.unit.stock.fact'].flush_model(['move_line_id'])
        self.flush_model(['consume_line_ids'])
        self.env.cr.execute('''SELECT 1 FROM quality_unit_stock_fact f WHERE
            f.move_line_id IN %s OR EXISTS (SELECT 1 FROM stock_move_line_consume_rel r
                WHERE r.consume_line_id=f.move_line_id AND r.produce_line_id IN %s) LIMIT 1''',
            [tuple(self.ids) or (0,), tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone())

    @api.model_create_multi
    def create(self, vals_list):
        moves = self.env['stock.move'].browse([values['move_id'] for values in vals_list if values.get('move_id')])
        if moves.move_line_ids._quality_native_fact_protected():
            raise UserError(_('До підтвердженого складського випуску не додають інші строки.'))
        return super().create(vals_list)

    def write(self, vals):
        if {'quantity', 'product_id', 'product_uom_id', 'company_id', 'move_id', 'lot_id',
                'location_id', 'location_dest_id', 'consume_line_ids', 'produce_line_ids'}.intersection(vals):
            if self._quality_native_fact_protected():
                raise UserError(_('Фактичні строки випуску та споживання QUNIT незмінні.'))
        return super().write(vals)

    def unlink(self):
        if self._quality_native_fact_protected():
            raise UserError(_('Фактичні строки випуску та споживання QUNIT не видаляють.'))
        return super().unlink()


class NativeFactMove(models.Model):
    _inherit = 'stock.move'

    def write(self, vals):
        if {'quantity', 'product_uom_qty', 'product_id', 'product_uom', 'company_id',
                'production_id', 'raw_material_production_id', 'move_line_ids', 'state',
                'location_id', 'location_dest_id'}.intersection(vals):
            if self.move_line_ids._quality_native_fact_protected():
                raise UserError(_('Підтверджені рухи випуску та споживання QUNIT незмінні.'))
        return super().write(vals)

    def unlink(self):
        if self.move_line_ids._quality_native_fact_protected():
            raise UserError(_('Підтверджені рухи QUNIT не видаляють.'))
        return super().unlink()
