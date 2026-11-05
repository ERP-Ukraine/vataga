"""QUNIT evidence at native WO boundaries. No stock or timer implementation."""
import math
import uuid
from contextvars import ContextVar

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_backorder import native_operation_allows

_PARTIAL_EXECUTION = ContextVar('quality_partial_workorder', default=None)
_PHYSICAL_FINISH = ContextVar('quality_physical_workorder_finish', default=None)


def physical_finish_scope(workorder):
    """Native WO completion only; confers no MO/stock or QC permission."""
    scope = _PHYSICAL_FINISH.get()
    return scope if (scope and len(workorder) == 1 and workorder.env.cr is scope[0]
                     and workorder.id == scope[1]) else None


def partial_execution_allows(workorder, units=None):
    scope = _PARTIAL_EXECUTION.get()
    return bool(scope and len(workorder) == 1 and workorder.env.cr is scope[0]
                and workorder.id == scope[1]
                and (units is None or set(units.ids).issubset(scope[2])))


PARTIAL_WO_MESSAGE = (
    'Не підтверджено обробку або допуск потрібних виробів. Завершіть фактичну обробку штатною кнопкою робочого завдання. Невиготовлений залишок зберігається у плані до окремого складського випуску з замовленням на залишок.'
)


class QualityNativeWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    quality_native_unit_ids = fields.Many2many(
        'quality.production.unit', 'quality_native_workorder_unit_rel',
        'workorder_id', 'unit_id', string='Вироби цієї операції', copy=False,
        help='У сучасному поштучному маршруті система фіксує дозволений склад при штатному запуску робочого завдання. Поле зберігається для перегляду та аудиту.')
    quality_native_managed = fields.Boolean(compute='_compute_quality_native_managed')
    quality_native_status = fields.Text(string='Результат операції', compute='_compute_quality_native_status')
    quality_native_hold_reason = fields.Text(string='Що утримує вироби', compute='_compute_quality_native_status')
    quality_native_identity_pending = fields.Boolean(
        string='Виготовлення очікує ідентифікації', readonly=True, copy=False,
        help='Фактичну роботу підтверджено. Відповідність зразків виробам потребує уточнення до складського випуску.')

    @api.depends('production_id.quality_unit_route_required', 'quality_transfer_controlled')
    def _compute_quality_native_managed(self):
        for workorder in self:
            workorder.quality_native_managed = workorder._quality_native_automatic()

    def _quality_native_automatic(self):
        self.ensure_one()
        return self.production_id.quality_unit_route_required and not self.quality_transfer_controlled

    def _quality_native_available_units(self, universe):
        """Read-only eligibility; mutation callers still lock and revalidate."""
        self.ensure_one()
        available = universe
        reasons = {}
        for source in self._quality_native_predecessors():
            checks = source._quality_native_checks()  # Missing QCP/ACL is never hidden.
            history = source._quality_native_history()
            processed = history.filtered(lambda row: row.state == 'processed').controlled_unit_id
            for unit in available:
                reason = False
                if (source.state != 'done' and unit not in processed) or (history and unit not in processed):
                    reason = _('Не підтверджено обробку попередньої операції %(wo)s.', wo=source.display_name)
                else:
                    try:
                        source._quality_validate_native_checks(checks, unit, lock=False)
                    except AccessError:
                        raise
                    except UserError as error:
                        reason = str(error)
                if reason:
                    reasons[unit.id] = reason
                    available -= unit
        return available, reasons

    @api.depends('state', 'qty_produced', 'qty_production', 'quality_execution_ids.state',
                 'production_id.quality_transfer_revision', 'production_id.quality_unit_route_required',
                 'quality_native_identity_pending')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_quality_native_status(self):
        # No registration, admission, revision updates or timers from a read.
        for workorder in self:
            workorder.quality_native_status = False
            workorder.quality_native_hold_reason = False
            if not workorder._quality_native_automatic():
                continue
            physical = (_('Обробку завершено (%(actual)s з %(planned)s).',
                          actual=workorder.qty_produced, planned=workorder.qty_production)
                        if workorder.state == 'done' else '')
            if workorder.quality_native_identity_pending:
                workorder.quality_native_status = physical + ' ' + _('Виготовлено: %s. Уточніть відповідність зразків виробам у перевірці якості; складський випуск заблоковано.') % workorder.qty_produced
                continue
            universe = workorder.production_id._full_release_units()
            if not universe:
                workorder.quality_native_status = physical or _('Звичайна робота робочого завдання. Ідентифікація готується при підтвердженні виготовлення.')
                continue
            try:
                available, reasons = workorder._quality_native_available_units(universe)
                checks = workorder._quality_native_checks()
            except AccessError:
                raise
            except UserError as error:
                workorder.quality_native_status = physical or _('Продовження утримується.')
                workorder.quality_native_hold_reason = str(error)
                continue
            processed = workorder._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id
            passed = failed = pending = 0
            for unit in universe:
                latest = {}
                for check in checks.sorted('id', reverse=True):
                    if not check.operation_run_id or unit in check.operation_run_id.unit_ids:
                        latest.setdefault(check.point_id.id, check)
                samples = [check.sample_ids.filtered(lambda row: row.controlled_unit_id == unit)
                           for check in latest.values()]
                if any(sample.sample_result == 'fail' for sample in samples if len(sample) == 1):
                    failed += 1
                elif samples and all(len(sample) == 1 and sample.sample_result == 'pass' for sample in samples):
                    passed += 1
                elif checks:
                    pending += 1
                if checks and unit in available:
                    try:
                        workorder._quality_validate_native_checks(checks, unit, lock=False)
                    except AccessError:
                        raise
                    except UserError as error:
                        reasons[unit.id] = str(error)
            workorder.quality_native_status = physical + ' ' + _(
                'Оброблено: %(processed)s / %(total)s. До обробки дозволено: %(ready)s. Перевірка якості: придатних — %(passed)s, непридатних — %(failed)s, очікують перевірки — %(pending)s. Дозволено продовжити: %(admitted)s.',
                processed=workorder.qty_produced if workorder.state == 'done' and not processed else len(processed),
                total=len(universe), ready=0 if workorder.state == 'done' else len(available - processed),
                passed=passed, failed=failed, pending=pending, admitted=len(universe) - len(reasons))
            workorder.quality_native_hold_reason = '\n'.join(
                '%s: %s' % (unit.display_name, reasons[unit.id]) for unit in universe if unit.id in reasons)

    def _quality_native_history(self):
        Execution = self.env['quality.unit.execution']
        if not self:
            return Execution
        Execution.flush_model(['workorder_id'])
        self.env.cr.execute('SELECT id FROM quality_unit_execution WHERE workorder_id IN %s ORDER BY id',
                            [tuple(self.ids)])
        records = Execution.browse([row[0] for row in self.env.cr.fetchall()])
        if records:
            records.check_access_rights('read')
            records.check_access_rule('read')
        return records

    def _quality_native_is_final(self):
        self.ensure_one()
        route = self.production_id._full_release_workorders()
        if self.production_id.allow_workorder_dependencies:
            return not any(self in row._quality_native_predecessors() for row in route - self)
        route = route.sorted(key=lambda row: (row.operation_id.sequence if row.operation_id else 0, row.id))
        return self == route[-1:]

    def _prepare_quality_native_checks(self):
        """Prepare existing QC at the physical boundary, never decide its result.

        The operator authorizes manufacturing, not inspection. Elevation is
        limited to draft snapshot/identity initialization on the exact readable
        checks of this WO; it grants no PASS, admission or release permission.
        Ambiguous existing evidence is preserved for explicit QC mapping.
        """
        self.ensure_one()
        self._check_quality_execution_access()
        checks = self.production_id._full_release_checks().filtered(lambda check:
            check.x_control_stage == 'in_process' and check.quality_state == 'none'
            and (check.workorder_id == self if 'workorder_id' in check._fields
                 else check.operation_run_id.workorder_id == self))
        for check in checks.sorted('id'):
            if (check._identity_production() != self.production_id or check.product_id != self.product_id
                    or check.company_id and check.company_id != self.company_id):
                raise ValidationError(_('Перевірка якості належить іншому замовленню, товару або компанії.'))
            # Configuration errors must not erase an operator's physical fact.
            # The ordinary QC/release gates will still reject incomplete QC.
            try:
                with self.env.cr.savepoint():
                    draft = check.sudo()
                    draft._initialize_measurement_snapshot()
                    draft._ensure_in_process_measurement_samples(manufactured=True)
                    if draft.operation_run_id or not any(sample._has_entered_results() for sample in draft.sample_ids):
                        draft._ensure_qc_unit_identity()
            except AccessError:
                raise
            except UserError as error:
                self.production_id.message_post(body=_('Виготовлення підтверджується; підготовка перевірки якості потребує уточнення: %s') % error)

    def _quality_native_predecessors(self):
        self.ensure_one()
        if self.production_id.allow_workorder_dependencies:
            previous = self.blocked_by_workorder_ids
            frontier = previous
            while frontier:
                frontier = frontier.blocked_by_workorder_ids - previous
                previous |= frontier
            previous.check_access_rights('read')
            previous.check_access_rule('read')
            if self in previous or any(row.production_id != self.production_id for row in previous):
                raise ValidationError(_('Некоректні залежності робочих завдань.'))
            return previous
        route = self.production_id._full_release_workorders().sorted(
            key=lambda row: (row.operation_id.sequence if row.operation_id else 0, row.id))
        return route[:list(route.ids).index(self.id)]

    def _quality_native_checks(self, require_all=True):
        """Discover hidden evidence, then apply ordinary ACLs; never sudo it."""
        self.ensure_one()
        Check = self.env['quality.check']
        Check.flush_model()
        self.env['quality.point'].flush_model(['x_control_stage'])
        self.env['quality.production.run'].flush_model(['workorder_id'])
        scope = 'r.workorder_id=%s'
        params = [self.id]
        if 'workorder_id' in Check._fields:
            scope += ' OR q.workorder_id=%s'
            params.append(self.id)
        self.env.cr.execute("SELECT q.id FROM quality_check q LEFT JOIN quality_production_run r "
                            "ON r.id=q.operation_run_id JOIN quality_point p ON p.id=q.point_id "
                            "WHERE p.x_control_stage='in_process' "
                            "AND (" + scope + ') ORDER BY q.id DESC', params)
        checks = Check.browse([row[0] for row in self.env.cr.fetchall()])
        if checks:
            checks.check_access_rights('read')
            checks.check_access_rule('read')
        result = checks
        requirements = set(self.production_id._applicable_full_qcp(self, operation_only=True))
        requirements |= {tuple(row) for row in self.production_id.quality_full_required_qcp or []}
        required = {point for point, operation, workorder in requirements if workorder == self.id}
        if require_all and required - set(result.point_id.ids):
            raise UserError(_('Для цієї операції ще не створено всі обов’язкові перевірки якості.'))
        return result

    def _quality_native_units(self):
        """Use an already identified full batch, never infer identities by count."""
        self.ensure_one()
        universe = self.production_id._full_release_units()
        selected = self.quality_native_unit_ids
        selected.check_access_rights('read')
        selected.check_access_rule('read')
        if selected - universe or any(
                unit._quality_current_production() != self.production_id
                or unit.company_id != self.company_id or unit.product_id != self.product_id
                for unit in selected):
            raise ValidationError(_('Вироби повинні належати саме цьому виробничому замовленню, виробу та компанії.'))
        if universe and self._quality_native_automatic():
            if selected and self.time_ids.filtered(lambda timer: not timer.date_end):
                return selected  # Composition frozen by the native start.
            available, reasons = self._quality_native_available_units(universe)
            if not available:
                raise UserError(_('Немає виробів, дозволених до обробки.\n%s') % '\n'.join(sorted(set(reasons.values()))))
            return available
        if selected:
            return selected
        # Only an explicit QC identity or immutable preceding execution is a
        # source of automatic selection. Equal quantities alone prove nothing.
        own = self._quality_native_checks().operation_run_id.unit_ids
        if own and set(own.ids) == set(universe.ids):
            return own
        previous = self._quality_native_predecessors()._quality_native_history().filtered(
            lambda row: row.state == 'processed').controlled_unit_id
        if previous and set(previous.ids) == set(universe.ids):
            return previous
        if universe:
            raise UserError(_('Оберіть конкретні вироби у картці робочого завдання.'))
        return universe

    def _quality_native_completion_units(self):
        units = self._quality_native_units()
        if self._quality_native_automatic():
            units |= self._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id
        return units

    def _quality_validate_native_checks(self, checks, units, lock=True):
        if units and checks:
            # A repeat QC for one unit supersedes its QCP for that unit only.
            for unit in units:
                latest = {}
                for check in sorted(checks, key=lambda row: row.id, reverse=True):
                    if not check.operation_run_id or unit in check.operation_run_id.unit_ids:
                        latest.setdefault(check.point_id.id, check)
                if {check.point_id.id for check in checks} - set(latest):
                    raise UserError(_('Обов’язкова перевірка якості не ідентифікує вибраний виріб.'))
                self._quality_validate_native_unit_checks(list(latest.values()), unit, lock=lock)
            return
        self._quality_validate_native_unit_checks(checks, None, lock=lock)

    def _quality_validate_native_unit_checks(self, checks, unit, lock=True):
        for check in checks:
            if lock:
                check._lock_unit_admission_scope()
            if unit is None:
                self.production_id._check_matrix_native_completion(workorders=self)
                continue
            for unit in [unit]:
                sample = check.sample_ids.filtered(lambda row: row.controlled_unit_id == unit)
                if len(sample) != 1:
                    raise UserError(_('Обов’язкова перевірка якості не ідентифікує вибраний виріб.'))
                error = check._get_unit_admission_error(sample)
                if error:
                    raise UserError(error)
                admissions = check.unit_admission_ids.filtered(
                    lambda row: row.controlled_unit_id == unit and row.state == 'confirmed')
                if not admissions or any(row._get_effectiveness_error() for row in admissions):
                    raise UserError(_('Для цього виробу відсутнє чинне підтвердження допуску за результатами перевірки якості.'))

    def _check_quality_native_start(self):
        if native_operation_allows(self.production_id):
            return
        self.production_id._check_quality_transfer_boundary()
        for workorder in self:
            previous = workorder._quality_native_predecessors()
            # Starting this operation must not wait for its own or future QC.
            # button_start(bypass=True) from Enterprise QC uses this same gate.
            for source in previous:
                checks = source._quality_native_checks()
                history = source._quality_native_history()
                if not checks and not history and not workorder.production_id._full_release_units():
                    continue
                units = workorder._quality_native_units()
                processed = history.filtered(lambda row: row.state == 'processed').controlled_unit_id
                if source.state != 'done' and (not units or units - processed):
                    raise UserError(_(PARTIAL_WO_MESSAGE))
                if history and units - history.filtered(lambda row: row.state == 'processed').controlled_unit_id:
                    raise UserError(_('Не підтверджено виконання попередньої операції для цих виробів.'))
                source._quality_validate_native_checks(checks, units)

    def action_start_quality_native_units(self):
        self.ensure_one()
        self._check_quality_execution_access()
        with self.env.cr.savepoint():
            self.production_id._check_quality_transfer_boundary()
            self.invalidate_recordset()
            self._check_quality_native_selection()
            if self.production_id.state not in ('confirmed', 'progress'):
                raise UserError(_('Поштучну обробку дозволено лише у відкритому виробничому замовленні.'))
            if not self.quality_native_unit_ids and not self._quality_native_automatic():
                raise UserError(_('Явно оберіть вироби для запуску цієї операції.'))
            self._quality_native_units()._assert_not_stock_posted()
            # Enterprise argument is forwarded, never a quality bypass.
            return self.button_start(bypass=True)

    def _check_quality_native_selection(self):
        expected = self.env.context.get('quality_expected_unit_ids')
        if expected is not None and set(expected) != set(self.quality_native_unit_ids.ids):
            raise UserError(_('Інший оператор змінив вибір виробів. Оновіть картку та перевірте склад.'))

    def action_confirm_quality_native_units(self):
        self.ensure_one()
        self._check_quality_execution_access()
        with self.env.cr.savepoint():
            self.production_id._check_quality_transfer_boundary()
            self.invalidate_recordset()
            self._check_quality_native_selection()
            self._check_quality_native_start()
            # This resolver accepts explicit selections or exact, registered
            # QC/predecessor identities. It never infers identities by count.
            units = self._quality_native_units()
            if not units:
                raise UserError(_('Спочатку ідентифікуйте вироби через перевірку якості і оберіть оброблені вироби.'))
            history = self._quality_native_history()
            recorded = history.filtered(lambda row: row.state == 'processed').controlled_unit_id
            reconciling = self.quality_native_identity_pending and self.qty_produced == len(units)
            if self.qty_produced != len(recorded) and not reconciling:
                raise UserError(_('Кількість робочого завдання не відповідає наявним поштучним фактам. Автоматичне доповнення історії заборонене.'))
            if reconciling and not self._quality_native_checks(require_all=False).filtered(
                    lambda check: check.operation_run_id
                    and set(check.operation_run_id.unit_ids.ids) == set(units.ids)
                    and len(check.sample_ids) == len(units)
                    and set(check.sample_ids.controlled_unit_id.ids) == set(units.ids)):
                raise UserError(_('Явно підтвердьте відповідність зразків саме обробленим виробам у перевірці цієї операції. Самої рівності кількостей недостатньо.'))
            missing = units - recorded
            if self.production_id._native_partial_history():
                missing._assert_not_stock_posted()
            else:
                units._assert_not_stock_posted()
            if not missing:
                return True
            if self.state not in (('progress', 'done') if reconciling else ('progress',)) or (not reconciling and not self.time_ids.filtered(
                    lambda timer: timer.user_id == self.env.user and not timer.date_end)):
                raise UserError(_('Спочатку почніть штатну роботу над робочим завданням під своїм користувачем.'))
            if self.state == 'done' and not self.time_ids.filtered(lambda timer: timer.user_id == self.env.user):
                raise UserError(_('Відповідність завершеної обробки підтверджує оператор її робочого інтервалу.'))
            scope = _PARTIAL_EXECUTION.set((self.env.cr, self.id, frozenset(missing.ids)))
            try:
                if self.state == 'done':
                    self.env['quality.unit.execution']._record_native_completion(self, missing)
                else:
                    self.env['quality.unit.execution']._record_partial_completion(self, missing, str(uuid.uuid4()))
                quantity = len(self._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id)
                if (self.product_uom_id != self.env.ref('uom.product_uom_unit')
                        or not math.isfinite(self.qty_producing) or quantity > self.qty_producing
                        or quantity > self.qty_production):
                    raise UserError(_('Кількість підтверджених виробів не відповідає робочому завданню.'))
                self.write({'qty_produced': quantity, 'quality_native_identity_pending': False})
            finally:
                _PARTIAL_EXECUTION.reset(scope)
        return True

    def action_open_quality_native_checks(self):
        self.ensure_one()
        self._check_quality_execution_access()
        return {'type': 'ir.actions.act_window', 'name': _('Контроль якості операції'),
                'res_model': 'quality.check', 'view_mode': 'tree,form',
                'domain': [('workorder_id', '=', self.id)],
                'context': {'default_workorder_id': self.id, 'default_production_id': self.production_id.id,
                            'default_product_id': self.product_id.id}}

    def _check_quality_native_finish(self):
        if native_operation_allows(self.production_id):
            return
        self._check_quality_native_start()
        for workorder in self:
            if workorder.state == 'done':
                continue  # Never backfill a previously completed operation.
            if workorder._quality_native_automatic():
                scope = physical_finish_scope(workorder)
                if (not scope or workorder.qty_producing != scope[2]
                        or workorder.qty_produced not in (0, scope[2])):
                    raise UserError(_('Підтвердьте фактичну обробку штатною кнопкою завершення робочого завдання.'))
                continue  # Own QC gates advancement/release, not physical closure.
            if workorder.quality_native_identity_pending:
                raise UserError(_('Підтверджене виготовлення потребує відповідності зразків виробам перед закриттям операції.'))
            checks = workorder._quality_native_checks(require_all=not workorder._quality_native_is_final())
            units = workorder._quality_native_completion_units()
            if units:
                workorder._check_quality_execution_access()
                uom = self.env.ref('uom.product_uom_unit')
                if workorder.product_uom_id.category_id != uom.category_id:
                    raise ValidationError(_('Вироби потребують кількості в одиницях виробів.'))
                quantity = workorder.product_uom_id._compute_quantity(
                    workorder.qty_producing or workorder.qty_production, uom, round=False)
                total = workorder.product_uom_id._compute_quantity(workorder.qty_production, uom, round=False)
                if (not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity)
                        or quantity != total or len(units) != quantity
                        or set(units.ids) != set(workorder.production_id._full_release_units().ids)
                        or workorder.qty_produced not in (0, workorder.qty_production)):
                    raise UserError(_(PARTIAL_WO_MESSAGE))
            if not workorder._quality_native_is_final():
                workorder._quality_validate_native_checks(checks, units)

    def button_start(self, bypass=False):
        with self.env.cr.savepoint():
            starting = self
            for workorder in self:
                if not native_operation_allows(workorder.production_id) and workorder._quality_native_automatic():
                    workorder.production_id._check_quality_transfer_boundary()
                    workorder.invalidate_recordset()
                    if workorder.state == 'done':
                        if bypass:
                            starting -= workorder  # Enterprise QC callback, no work/timer.
                            continue
                        raise UserError(_('Обробку цього робочого завдання вже завершено. Повторний запуск заборонено.'))
                    workorder._check_quality_execution_access()
                    if workorder.time_ids.filtered(lambda timer: not timer.date_end and timer.user_id != self.env.user):
                        raise UserError(_('Цю обробку вже виконує інший оператор.'))
                if (not native_operation_allows(workorder.production_id) and workorder._quality_native_automatic()
                        and workorder.state not in ('done', 'cancel')
                        and not workorder.time_ids.filtered(lambda timer: not timer.date_end)):
                    workorder.production_id._check_quality_transfer_boundary()
                    universe = workorder.production_id._full_release_units()
                    if universe:
                        available, reasons = workorder._quality_native_available_units(universe)
                        processed = workorder._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id
                        pending = available - processed
                        if not pending:
                            if bypass:
                                starting -= workorder  # QC callback: no repeated work/timer.
                                continue
                            raise UserError(_('Нових виробів для обробки немає. Вже підтверджена фактична обробка не запускається повторно. Завершіть контроль якості та випустіть повну або фактичну партію з виробничого замовлення.'))
                        workorder.write({'quality_native_unit_ids': [(6, 0, pending.ids)]})
            starting._check_quality_native_start()
            # Enterprise normally waits for all predecessors. The same
            # server gate above permits only individually eligible QUNIT.
            if any(workorder._quality_native_automatic()
                   and workorder.production_id._full_release_units()
                   and any(source.state != 'done' for source in workorder._quality_native_predecessors())
                   for workorder in self):
                bypass = True
            return super(QualityNativeWorkorder, starting).button_start(bypass=bypass) if starting else True

    def _quality_confirm_on_native_finish(self):
        """Prepare exact physical facts; native completion closes the WO next."""
        self.ensure_one()
        if (native_operation_allows(self.production_id) or self.quality_transfer_controlled
                or self.state in ('done', 'cancel')):
            return False
        self.production_id._check_quality_transfer_boundary()
        self.invalidate_recordset()
        if self.state in ('done', 'cancel'):
            return True
        self._check_quality_native_start()
        final = self._quality_native_is_final()
        automatic = self._quality_native_automatic()
        if automatic:
            self._check_quality_execution_access()
            self._prepare_quality_native_checks()
        universe = self.production_id._full_release_units()
        history = self._quality_native_history()
        if not universe and not final and not self._quality_native_checks(require_all=False):
            return False  # Ordinary earlier operation, before any unit identity.
        if not universe and not self._quality_native_automatic():
            return False
        uom = self.env.ref('uom.product_uom_unit')
        planned = self.product_uom_id._compute_quantity(self.qty_production, uom, round=False)
        current = self.product_uom_id._compute_quantity(self.qty_producing, uom, round=False)
        if automatic and (
                self.product_uom_id != uom or not math.isfinite(current)
                or current <= 0 or current != int(current) or current > planned):
            raise UserError(_('Задайте фактичну цілу кількість виготовлених виробів у межах плану.'))
        if (not universe or (self._quality_native_automatic() and len(universe) > current
                             and not history)):
            # A missing/ambiguous QC mapping does not invalidate physical work.
            # Retain its quantity, but never infer per-unit history from a count.
            if not self.quality_native_identity_pending:
                self._check_quality_execution_access()
                if (self.qty_produced or self.state != 'progress' or not self.time_ids.filtered(
                        lambda timer: timer.user_id == self.env.user and not timer.date_end)):
                    raise UserError(_('Спочатку почніть роботу та підтвердьте фактичну кількість.'))
                scope = _PARTIAL_EXECUTION.set((self.env.cr, self.id, frozenset(), current))
                try:
                    self.write({'qty_produced': current, 'quality_native_identity_pending': True})
                finally:
                    _PARTIAL_EXECUTION.reset(scope)
            elif self.qty_produced != current:
                raise UserError(_('Відновіть підтверджену фактичну кількість перед ідентифікацією.'))
            if not automatic:
                self.button_pending()
                return True
            return False  # Native done retains qty_produced and the pending marker.
        if not self._quality_native_automatic() and len(universe) == planned == current:
            return False  # Preserve ordinary full-batch completion.
        self.action_confirm_quality_native_units()
        if not automatic or self.qty_produced < current:
            # A subset of an already registered manufactured batch may still
            # await another unit (legacy incremental/retest flow). It is not
            # the completed actual batch selected by the operator.
            self.button_pending()
            return True
        return False

    def _finish_quality_native_work(self, method):
        """One protected physical lifecycle for all three native finish aliases."""
        with self.env.cr.savepoint():
            if not any(workorder._quality_native_automatic() for workorder in self):
                remaining = self.filtered(lambda workorder: not workorder._quality_confirm_on_native_finish())
                if not remaining:
                    return True
                remaining._check_quality_native_finish()
                return getattr(super(QualityNativeWorkorder, remaining), method)()
            for workorder in self:
                if workorder._quality_native_automatic() and not native_operation_allows(workorder.production_id):
                    workorder.production_id._check_quality_transfer_boundary()
                    workorder.invalidate_recordset()
                if workorder.state == 'done' and workorder.quality_native_identity_pending:
                    if len(workorder.production_id._full_release_units()) == workorder.qty_produced:
                        workorder.action_confirm_quality_native_units()
                if workorder.state in ('done', 'cancel'):
                    continue
                if workorder._quality_native_automatic() and not native_operation_allows(workorder.production_id):
                    workorder._check_quality_execution_access()
                    quantity = workorder.qty_producing
                    timers = workorder.time_ids.filtered(lambda timer: not timer.date_end)
                    if (workorder.product_uom_id != self.env.ref('uom.product_uom_unit')
                            or not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity)
                            or quantity > workorder.qty_production):
                        raise ValidationError(_('Задайте фактичну цілу кількість виготовлених виробів у межах плану.'))
                    if (workorder.state != 'progress'
                            or timers.filtered(lambda timer: timer.user_id != self.env.user)
                            or not workorder.qty_produced and not timers):
                        raise UserError(_('Завершити обробку має оператор, який почав штатну роботу.'))
                if workorder._quality_confirm_on_native_finish():
                    continue
                if not workorder._quality_native_automatic() or native_operation_allows(workorder.production_id):
                    getattr(super(QualityNativeWorkorder, workorder), method)()
                    continue
                quantity = workorder.qty_producing
                production = workorder.production_id
                before = (production.product_qty, production.qty_produced,
                          set((production.move_raw_ids | production.move_finished_ids).filtered(
                              lambda move: move.state == 'done').ids))
                token = _PHYSICAL_FINISH.set((self.env.cr, workorder.id, quantity))
                try:
                    # Odoo's canonical finish closes all timers and uses the
                    # already recorded qty_produced, without posting stock.
                    super(QualityNativeWorkorder, workorder).button_finish()
                    if (workorder.state != 'done' or workorder.qty_produced != quantity
                            or workorder.qty_producing != quantity
                            or workorder.time_ids.filtered(lambda timer: not timer.date_end)):
                        raise ValidationError(_('Штатне завершення не підтвердило фактичну обробку та закриття робочих інтервалів.'))
                    after = (production.product_qty, production.qty_produced,
                             set((production.move_raw_ids | production.move_finished_ids).filtered(
                                 lambda move: move.state == 'done').ids))
                    if after != before:
                        raise ValidationError(_('Завершення обробки не повинно змінювати план або проводити склад. Усі зміни скасовано.'))
                finally:
                    _PHYSICAL_FINISH.reset(token)
        return True

    def button_finish(self):
        return self._finish_quality_native_work('button_finish')

    def button_done(self):
        return self._finish_quality_native_work('button_done')

    def action_mark_as_done(self):
        return self._finish_quality_native_work('action_mark_as_done')

    @api.model_create_multi
    def create(self, vals_list):
        if any('quality_native_identity_pending' in vals for vals in vals_list):
            raise UserError(_('Стан ідентифікації встановлює підтвердження виготовлення.'))
        self = self.with_context({key: value for key, value in self.env.context.items()
                                  if key != 'default_quality_native_identity_pending'})
        with self.env.cr.savepoint():
            records = super().create(vals_list)
            for record in records:
                if native_operation_allows(record.production_id):
                    continue
                if record.quality_native_unit_ids:
                    record._quality_native_units()
                if (record.state in ('progress', 'done') or record.qty_produced) and record.production_id._full_release_units():
                    raise UserError(_('Створіть робоче завдання без виконання, потім використайте штатний запуск і завершення.'))
                if record.state in ('progress', 'done') or record.qty_produced:
                    record._check_quality_native_start()
                    checks = record._quality_native_checks()
                    record._quality_validate_native_checks(checks, record._quality_native_units())
            return records

    def write(self, vals):
        with self.env.cr.savepoint():
            if partial_execution_allows(self):
                scope = _PARTIAL_EXECUTION.get()
                count = scope[3] if len(scope) > 3 else len(self._quality_native_history().filtered(lambda row: row.state == 'processed').controlled_unit_id)
                if (set(vals) != {'qty_produced', 'quality_native_identity_pending'}
                        or vals['qty_produced'] != count
                        or not (self.state == 'progress' or self.state == 'done' and self.quality_native_identity_pending)
                        or vals['quality_native_identity_pending'] != (len(scope) > 3)):
                    raise UserError(_('Часткове підтвердження може змінювати лише фактичну кількість оброблених виробів.'))
                return super().write(vals)
            if 'quality_native_identity_pending' in vals:
                raise UserError(_('Стан ідентифікації встановлює підтвердження виготовлення.'))
            if native_operation_allows(self.production_id):
                return super().write(vals)  # Existing private repair/release capability only.
            scope = physical_finish_scope(self)
            if scope:
                if ({'production_id', 'operation_id', 'workcenter_id', 'product_uom_id',
                     'blocked_by_workorder_ids', 'needed_by_workorder_ids', 'quality_native_unit_ids',
                     'qty_producing', 'qty_reported_from_previous_wo'}.intersection(vals)
                        or 'qty_produced' in vals and vals['qty_produced'] != scope[2]
                        or 'state' in vals and vals['state'] != 'done'):
                    raise UserError(_('Штатне завершення може підтвердити лише зафіксовану фактичну обробку.'))
                if vals.get('state') == 'done' and self.time_ids.filtered(lambda timer: not timer.date_end):
                    raise UserError(_('Штатне завершення повинно спочатку закрити всі робочі інтервали.'))
                return super().write(vals)
            sensitive = {'production_id', 'operation_id', 'workcenter_id', 'product_uom_id',
                         'blocked_by_workorder_ids', 'needed_by_workorder_ids',
                         'quality_native_unit_ids', 'qty_produced', 'qty_producing',
                         'qty_reported_from_previous_wo', 'state'}
            if not sensitive.intersection(vals):
                return super().write(vals)
            self.production_id._lock_quality_transfer_scope()
            protected = {'production_id', 'operation_id', 'workcenter_id', 'product_uom_id',
                         'blocked_by_workorder_ids', 'needed_by_workorder_ids',
                         'quality_native_unit_ids', 'qty_produced', 'state'}
            history = self._quality_native_history()
            for record in self:
                if record._quality_native_automatic() and record.state == 'done' and (
                        protected.intersection(vals) - {'state', 'qty_produced'}
                        or any(name in vals and vals[name] != record[name] for name in ('state', 'qty_produced'))):
                    raise UserError(_('Фактичний результат завершеної операції незмінний.'))
                if record.quality_native_identity_pending and protected.intersection(vals) - {'quality_native_unit_ids'}:
                    if (protected.intersection(vals) - {'state', 'qty_produced', 'quality_native_unit_ids'}
                            or any(name in vals and vals[name] != record[name] for name in ('state', 'qty_produced'))):
                        raise UserError(_('Підтверджена фактична кількість та її виробниче джерело незмінні. Спочатку уточніть ідентифікацію.'))
                if ('quality_native_unit_ids' in vals and record._quality_native_automatic()
                        and record.time_ids.filtered(lambda timer: not timer.date_end)):
                    raise UserError(_('Склад активної роботи зафіксовано при запуску. Спочатку підтвердьте або призупиніть поточну обробку.'))
                if set(vals) == {'quality_native_unit_ids'} and record.state not in ('done', 'cancel'):
                    continue  # Draft selection is not immutable execution history.
                if vals.get('state') == 'done' and record.state == 'progress' and not (
                        protected.intersection(vals) - {'state', 'qty_produced'}):
                    continue  # The full completion gate below still validates every unit.
                if history.filtered(lambda row: row.workorder_id == record) and protected.intersection(vals):
                    changing = any(
                        name in vals and vals[name] != record[name]
                        for name in ('state', 'qty_produced'))
                    if changing or protected.intersection(vals) - {'state', 'qty_produced'}:
                        raise UserError(_('Ідентичність та результат підтвердженої операції незмінні.'))
            completing = self.filtered(lambda row: row.state != 'done') if vals.get('state') == 'done' else self.browse()
            if completing:
                if sensitive.intersection(vals) - {'state', 'qty_produced'}:
                    raise UserError(_('Збережіть кількість, маршрут та вибір виробів перед завершенням робочого завдання.'))
                completing._check_quality_native_finish()
            elif vals.get('state') == 'progress':
                self._check_quality_native_start()
            if 'qty_produced' in vals:
                for record in self:
                    if record.production_id._full_release_units() and vals['qty_produced'] != record.qty_produced:
                        if record not in completing or vals['qty_produced'] != record.qty_production:
                            raise UserError(_(PARTIAL_WO_MESSAGE))
            if 'qty_reported_from_previous_wo' in vals:
                for record in self:
                    if record.production_id._full_release_units() and vals['qty_reported_from_previous_wo'] != record.qty_reported_from_previous_wo:
                        raise UserError(_(PARTIAL_WO_MESSAGE))
            selections = {record.id: record._quality_native_completion_units() for record in completing}
            result = super().write(vals)
            if 'quality_native_unit_ids' in vals:
                for record in self:
                    record._quality_native_units()
            for record in completing:
                if selections[record.id]:
                    self.env['quality.unit.execution']._record_native_completion(record, selections[record.id])
            return result


class QualityPartialWorkorderAlert(models.Model):
    _inherit = 'quality.alert'

    def action_close_alert(self):
        with self.env.cr.savepoint():
            result = super().action_close_alert()
            # Closing the hold is a quality decision. Use the existing QC
            # confirmation/ACL checks, never mint permissions from WO start.
            for check in self.check_id.filtered('operation_run_id'):
                production = check._identity_production()
                if (production.quality_unit_route_required and not production.quality_transfer_controlled
                        and production.state in ('confirmed', 'progress', 'to_close')):
                    partial = check.operation_run_id.workorder_id._quality_native_history().filtered(
                        lambda execution: execution.batch_id.request_key.startswith('partial:'))
                    if partial:
                        check._confirm_completed_qc_units()
            return result
