"""QUNIT evidence at native WO boundaries. No stock or timer implementation."""
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .native_backorder import native_operation_allows


PARTIAL_WO_MESSAGE = (
    'Штатне завершення закриває WO цілком. Частина QUNIT утримується або вибрано '
    'не всю партію. Потрібна Enterprise-перевірка часткового виконання WO зі '
    'збереженням незавершеного залишку; складський випуск і Backorder тут не виконуються.'
)


class QualityNativeWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    quality_native_unit_ids = fields.Many2many(
        'quality.production.unit', 'quality_native_workorder_unit_rel',
        'workorder_id', 'unit_id', string='Вироби цієї операції', copy=False,
        help='За потреби оберіть конкретні QUNIT. Повний однозначний склад '
             'власної QC або попереднього виконання враховується автоматично.')

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

    def _quality_native_checks(self):
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
        latest = {}
        for check in checks.sorted('id', reverse=True):
            latest.setdefault(check.point_id.id, check.id)
        result = checks.browse(list(latest.values()))
        requirements = set(self.production_id._applicable_full_qcp(self, operation_only=True))
        requirements |= {tuple(row) for row in self.production_id.quality_full_required_qcp or []}
        required = {point for point, operation, workorder in requirements if workorder == self.id}
        if required - set(result.point_id.ids):
            raise UserError(_('Для цієї операції ще не створено всі обов’язкові QC.'))
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
            raise ValidationError(_('QUNIT повинні належати саме цьому MO, виробу та компанії.'))
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
            raise UserError(_('Оберіть конкретні QUNIT у картці робочого завдання.'))
        return universe

    def _quality_validate_native_checks(self, checks, units):
        for check in checks:
            check._lock_unit_admission_scope()
            if not units:
                self.production_id._check_matrix_native_completion(workorders=self)
                continue
            for unit in units:
                sample = check.sample_ids.filtered(lambda row: row.controlled_unit_id == unit)
                if len(sample) != 1:
                    raise UserError(_('Обов’язкова QC не ідентифікує вибраний QUNIT.'))
                error = check._get_unit_admission_error(sample)
                if error:
                    raise UserError(error)
                admissions = check.unit_admission_ids.filtered(
                    lambda row: row.controlled_unit_id == unit and row.state == 'confirmed')
                if not admissions or any(row._get_effectiveness_error() for row in admissions):
                    raise UserError(_('Для QUNIT немає чинного автоматично зафіксованого допуску QC.'))

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
                if source.state != 'done':
                    raise UserError(_(PARTIAL_WO_MESSAGE))
                if history and units - history.filtered(lambda row: row.state == 'processed').controlled_unit_id:
                    raise UserError(_('Не підтверджено виконання попередньої операції для цих QUNIT.'))
                source._quality_validate_native_checks(checks, units)

    def _check_quality_native_finish(self):
        if native_operation_allows(self.production_id):
            return
        self._check_quality_native_start()
        for workorder in self:
            if workorder.state == 'done':
                continue  # Never backfill a previously completed operation.
            checks = workorder._quality_native_checks()
            units = workorder._quality_native_units()
            if units:
                workorder._check_quality_execution_access()
                uom = self.env.ref('uom.product_uom_unit')
                if workorder.product_uom_id.category_id != uom.category_id:
                    raise ValidationError(_('QUNIT потребують кількості в одиницях виробів.'))
                quantity = workorder.product_uom_id._compute_quantity(
                    workorder.qty_producing or workorder.qty_production, uom, round=False)
                total = workorder.product_uom_id._compute_quantity(workorder.qty_production, uom, round=False)
                if (not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity)
                        or quantity != total or len(units) != quantity
                        or set(units.ids) != set(workorder.production_id._full_release_units().ids)
                        or workorder.qty_produced not in (0, workorder.qty_production)):
                    raise UserError(_(PARTIAL_WO_MESSAGE))
            workorder._quality_validate_native_checks(checks, units)

    def button_start(self, bypass=False):
        with self.env.cr.savepoint():
            self._check_quality_native_start()
            return super().button_start(bypass=bypass)

    def button_finish(self):
        with self.env.cr.savepoint():
            self._check_quality_native_finish()
            return super().button_finish()

    def button_done(self):
        with self.env.cr.savepoint():
            self._check_quality_native_finish()
            return super().button_done()

    def action_mark_as_done(self):
        with self.env.cr.savepoint():
            self._check_quality_native_finish()
            return super().action_mark_as_done()

    @api.model_create_multi
    def create(self, vals_list):
        with self.env.cr.savepoint():
            records = super().create(vals_list)
            for record in records:
                if native_operation_allows(record.production_id):
                    continue
                if record.quality_native_unit_ids:
                    record._quality_native_units()
                if (record.state in ('progress', 'done') or record.qty_produced) and record.production_id._full_release_units():
                    raise UserError(_('Створіть WO без виконання, потім використайте штатний запуск і завершення.'))
                if record.state in ('progress', 'done') or record.qty_produced:
                    record._check_quality_native_start()
                    checks = record._quality_native_checks()
                    record._quality_validate_native_checks(checks, record._quality_native_units())
            return records

    def write(self, vals):
        with self.env.cr.savepoint():
            if native_operation_allows(self.production_id):
                return super().write(vals)  # Existing private repair/release capability only.
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
                if history.filtered(lambda row: row.workorder_id == record) and protected.intersection(vals):
                    changing = any(
                        name in vals and vals[name] != record[name]
                        for name in ('state', 'qty_produced'))
                    if changing or protected.intersection(vals) - {'state', 'qty_produced'}:
                        raise UserError(_('Ідентичність та результат підтвердженої операції незмінні.'))
            completing = self.filtered(lambda row: row.state != 'done') if vals.get('state') == 'done' else self.browse()
            if completing:
                if sensitive.intersection(vals) - {'state', 'qty_produced'}:
                    raise UserError(_('Збережіть кількість, маршрут та вибір QUNIT перед завершенням WO.'))
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
            selections = {record.id: record._quality_native_units() for record in completing}
            result = super().write(vals)
            if 'quality_native_unit_ids' in vals:
                for record in self:
                    record._quality_native_units()
            for record in completing:
                if selections[record.id]:
                    self.env['quality.unit.execution']._record_native_completion(record, selections[record.id])
            return result
