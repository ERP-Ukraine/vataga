"""Full-batch readiness around native MRP completion; no stock posting engine."""
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .quality_native_backorder import _read
from .native_backorder import NativeBackorderOperation, native_operation, native_operation_allows

FULL_RELEASE_FIELDS = {'quality_full_release_test_database', 'quality_full_required_qcp'}


class QualityFullReleaseProduction(models.Model):
    _inherit = 'mrp.production'

    quality_full_release_test_database = fields.Char(readonly=True, copy=False)
    quality_full_required_qcp = fields.Json(readonly=True, copy=False, default=lambda self: [])
    quality_full_release_test_enabled = fields.Boolean(compute='_compute_full_release_test_enabled')

    @api.depends('quality_full_release_test_database')
    def _compute_full_release_test_enabled(self):
        for production in self:
            production.quality_full_release_test_enabled = (
                production.quality_full_release_test_database == self.env.cr.dbname)

    @api.model_create_multi
    def create(self, vals_list):
        if any(FULL_RELEASE_FIELDS.intersection(values) for values in vals_list):
            raise UserError(_('Дозвіл тестового випуску та призначення QCP встановлює сервер.'))
        context = {key: value for key, value in self.env.context.items()
                   if key not in {'default_' + name for name in FULL_RELEASE_FIELDS}}
        return super(QualityFullReleaseProduction, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if FULL_RELEASE_FIELDS.intersection(vals):
            raise UserError(_('Службові дозволи випуску та призначення QCP незмінні через write.'))
        return super().write(vals)

    def _enable_required_unit_route(self):
        result = super()._enable_required_unit_route()
        for production in self:
            production._capture_full_required_qcp()
        return result

    def action_enable_quality_full_release_test(self):
        raise UserError(_('Окремий адміністративний дозвіл більше не потрібен. Використайте штатне завершення MO.'))

    def _check_full_release_test_access(self):
        raise UserError(_('Окремий майстер випуску закрито. Використайте штатну кнопку «Виготовити все» у MO.'))

    def button_mark_done(self):
        controlled = self.filtered(lambda production: production.quality_unit_route_required
                                   and not production.quality_transfer_controlled)
        # Existing private repair/legacy operations retain their own guards.
        if not controlled or native_operation_allows(controlled):
            return super().button_mark_done()
        with self.env.cr.savepoint():
            self.check_access_rights('write')
            self.check_access_rule('write')
            # Idempotent retries never repost or backfill already closed MOs.
            pending = self - controlled.filtered(lambda production: production.state == 'done')
            if not pending:
                return True
            pending._lock_quality_transfer_scope()
            batches = {}
            for production in controlled & pending:
                units = production._full_release_units()
                production._validate_quality_full_batch(units, standard=True)
                production._check_standard_release_quantity(units)
                if production.lot_producing_id:
                    production._validate_full_release_lot(units, production.lot_producing_id)
                batches[production.id] = tuple(units.ids)
            if not batches:
                return super(QualityFullReleaseProduction, pending).button_mark_done()
            first = controlled.browse(next(iter(batches)))
            operation = NativeBackorderOperation(first, len(batches[first.id]),
                                                standard_full=batches)
            with operation.activate():
                result = super(QualityFullReleaseProduction, pending).button_mark_done()
                for production in controlled & pending:
                    if production.state != 'done':
                        continue  # Native consumption/other dialog: no facts yet.
                    units = self.env['quality.production.unit'].browse(batches[production.id])
                    production._validate_quality_full_batch(units, standard=True, posted=True)
                    production._record_standard_stock_facts(units)
            return result

    def _check_standard_release_quantity(self, units):
        self.ensure_one()
        if self.qty_producing not in (0, self.product_qty) or len(units) != self.product_qty:
            raise UserError(_('Дозволено лише повний випуск усієї годної партії. Частковий випуск і Backorder закриті.'))
        if self.product_id.tracking == 'serial' and len(units) != 1:
            raise UserError(_('Підтримується повний випуск одного серійного виробу на MO. Випуск кількох serial потребує штатного розділення, яке в цій фазі закрите.'))

    def _post_inventory(self, cancel_backorder=False):
        operation = native_operation()
        if operation and operation.standard_full:
            for production in self.filtered(lambda row: row.id in operation.standard_full):
                units = self.env['quality.production.unit'].browse(operation.standard_full[production.id])
                production._validate_quality_full_batch(units, standard=True)
                production._check_standard_release_quantity(units)
                if production.qty_producing != production.product_qty:
                    raise UserError(_('Штатне проведення повинно охоплювати всю партію QUNIT.'))
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    def _record_standard_stock_facts(self, units):
        """Link identities to real posted lines; never create stock objects."""
        self.ensure_one()
        self._validate_full_release_lot(units, self.lot_producing_id)
        self.env['stock.move'].flush_model()
        self.env['stock.move.line'].flush_model()
        self.env.cr.execute('''SELECT l.id FROM stock_move_line l JOIN stock_move m ON m.id=l.move_id
            WHERE m.production_id=%s AND m.product_id=%s AND m.state='done'
                AND NOT COALESCE(m.scrapped, FALSE) ORDER BY l.id''', [self.id, self.product_id.id])
        lines = _read(self.env['stock.move.line'].browse([row[0] for row in self.env.cr.fetchall()]))
        pending = list(units.sorted('id'))
        values = []
        for line in lines:
            if (not math.isfinite(line.quantity) or line.quantity < 0 or line.quantity != int(line.quantity)
                    or line.product_uom_id != self.product_uom_id
                    or line.product_id != self.product_id or line.company_id != self.company_id
                    or line.location_dest_id != self.location_dest_id or line.location_id.usage != 'production'
                    or (self.product_id.tracking == 'none' and line.lot_id)
                    or (self.product_id.tracking != 'none' and line.lot_id != self.lot_producing_id)):
                raise ValidationError(_('Проведені складські строки не відповідають партії QUNIT.'))
            for index in range(int(line.quantity)):
                if not pending:
                    raise ValidationError(_('Фактичний складський випуск перевищує склад QUNIT.'))
                unit = pending.pop(0)
                values.append({'unit_id': unit.id, 'production_id': self.id,
                               'move_line_id': line.id, 'request_key': 'native-mo:%s' % self.id})
        if pending:
            raise ValidationError(_('Не всі QUNIT мають фактичну проведену складську строку.'))
        return self.env['quality.unit.stock.fact'].create(values)

    def _full_release_checks(self):
        self.ensure_one()
        self.env.flush_all()
        Check = self.env['quality.check']
        scope, params = ['q.production_id=%s', 'r.production_id=%s'], [self.id, self.id]
        if 'workorder_id' in Check._fields:
            scope.append('q.workorder_id IN (SELECT id FROM mrp_workorder WHERE production_id=%s)')
            params.append(self.id)
        self.env.cr.execute('SELECT q.id FROM quality_check q LEFT JOIN quality_production_run r ON r.id=q.operation_run_id WHERE ' + ' OR '.join(scope) + ' ORDER BY q.id', params)
        return _read(Check.browse([row[0] for row in self.env.cr.fetchall()]))

    def _full_release_workorders(self):
        self.ensure_one()
        self.env['mrp.workorder'].flush_model(['production_id'])
        self.env.cr.execute('SELECT id FROM mrp_workorder WHERE production_id=%s ORDER BY id', [self.id])
        return _read(self.env['mrp.workorder'].browse([row[0] for row in self.env.cr.fetchall()]))

    def _applicable_full_qcp(self, workorders, operation_only=False, control_stages=('in_process',)):
        """Resolve actual QCP assignments, not the absence of generated QC.

        Enterprise's routing relation is identified by its ORM comodel, rather
        than a guessed field name. Product, category, company and operation-type
        assignments are applied; the resulting requirements are sticky on MO.
        """
        Point = self.env['quality.point']
        domain = [('x_control_stage', 'in', list(control_stages))]
        if 'company_id' in Point._fields:
            domain.append(('company_id', 'in', [False, self.company_id.id]))
        operation_fields = [name for name, field in Point._fields.items()
                            if getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter'
                            and field.type in ('many2one', 'many2many')]
        if operation_only:
            # A WO boundary resolves only QCP assigned to that operation.
            # Global/unassigned QCP remain final MO/stock requirements.
            if not operation_fields or not workorders.operation_id:
                return set()
            domain += ['|'] * (len(operation_fields) - 1) + [
                (name, 'in', workorders.operation_id.ids) for name in operation_fields]
        ids = Point.sudo().search(domain).ids  # discover hidden requirements only
        points = _read(Point.browse(ids)) if ids else Point
        categories = self.product_id.categ_id
        parent = categories.parent_id
        while parent and parent not in categories:
            _read(parent)
            categories |= parent
            parent = parent.parent_id
        result = set()
        for point in points:
            operations = self.env['mrp.routing.workcenter']
            for name in operation_fields:
                operations |= _read(point[name])
            assigned_type = self.picking_type_id in point.picking_type_ids
            if not assigned_type and (point.picking_type_ids or not operations):
                continue
            restrictions, matches = False, False
            product_scopes = {'product.product': self.product_id,
                              'product.template': self.product_id.product_tmpl_id,
                              'product.category': categories}
            for name, field in Point._fields.items():
                model = getattr(field, 'comodel_name', None)
                if model in product_scopes and field.type in ('many2one', 'many2many') and point[name]:
                    restrictions = True
                    matches = matches or bool(_read(point[name]) & product_scopes[model])
            if restrictions and not matches:
                continue
            if operations:
                for workorder in workorders.filtered(lambda row: bool(row.operation_id) and row.operation_id in operations):
                    result.add((point.id, workorder.operation_id.id, workorder.id))
            else:
                result.add((point.id, 0, 0))
        return result

    def _capture_full_required_qcp(self):
        self.ensure_one()
        self._lock_quality_transfer_scope()
        workorders = self._full_release_workorders()
        requirements = {tuple(row) for row in (self.quality_full_required_qcp or [])}
        requirements |= self._applicable_full_qcp(workorders)
        for check in self._full_release_checks().filtered(
                lambda item: item.x_control_stage == 'in_process' and item.unit_admission_role == 'required'):
            workorder = check.operation_run_id.workorder_id
            if not workorder and 'workorder_id' in check._fields:
                workorder = check.workorder_id
            requirements.add((check.point_id.id, workorder.operation_id.id or 0, workorder.id or 0))
        values = [list(row) for row in sorted(requirements)]
        if values != (self.quality_full_required_qcp or []):
            super(QualityFullReleaseProduction, self).write({'quality_full_required_qcp': values})
        return requirements

    def _full_release_units(self):
        self.ensure_one()
        self.env['quality.production.unit'].flush_model(['production_id'])
        self.env['quality.unit.continuation'].flush_model(['production_id', 'unit_id'])
        self.env.cr.execute("""SELECT id FROM quality_production_unit WHERE production_id=%s
            UNION SELECT unit_id FROM quality_unit_continuation WHERE production_id=%s""", [self.id, self.id])
        units = _read(self.env['quality.production.unit'].browse([row[0] for row in self.env.cr.fetchall()]))
        return units.filtered(lambda unit: unit._quality_current_production() == self)

    def _check_full_release_alerts(self, units, checks):
        self.env['quality.alert'].flush_model()
        self.env['quality.check.sample'].flush_model(['controlled_unit_id'])
        Alert = self.env['quality.alert']
        scope = ['a.check_id IN %s', 'EXISTS (SELECT 1 FROM quality_alert_failure_sample_rel x JOIN quality_check_sample s ON s.id=x.sample_id WHERE x.alert_id=a.id AND s.controlled_unit_id IN %s)']
        params = [tuple(checks.ids) or (0,), tuple(units.ids)]
        if 'production_id' in Alert._fields:
            scope.append('a.production_id=%s')
            params.append(self.id)
        if 'workorder_id' in Alert._fields:
            scope.append('a.workorder_id IN (SELECT id FROM mrp_workorder WHERE production_id=%s)')
            params.append(self.id)
        self.env.cr.execute('SELECT a.id FROM quality_alert a WHERE ' + ' OR '.join(scope), params)
        alerts = _read(Alert.browse([row[0] for row in self.env.cr.fetchall()]))
        for alert in alerts:
            if alert._matrix_hold_is_open():
                samples = _read(alert.vataga_failure_sample_ids)
                if not samples or any(not row.controlled_unit_id for row in samples) or samples.controlled_unit_id & units:
                    raise UserError(_('Випуск утримує відкритий Alert %(alert)s.', alert=alert.display_name))

    def _validate_full_release_lot(self, units, lot):
        """Validate an actual stock lot, independently of QUNIT identity."""
        self.ensure_one()
        if self.product_id.tracking == 'none':
            if lot:
                raise ValidationError(_('Для товару без tracking партію готової продукції не задають.'))
            return self.env['stock.lot']
        if self.product_id.tracking == 'serial' and (len(units) != 1 or self.product_qty != 1):
            raise ValidationError(_('Повний серійний випуск підтримується лише для одного QUNIT на MO.'))
        if self.product_id.tracking not in ('lot', 'serial'):
            raise ValidationError(_('Невідомий спосіб відстеження готового виробу.'))
        lot = _read((lot or self.env['stock.lot']).exists())
        if len(lot) != 1:
            raise ValidationError(_('Вкажіть реальну партію / серійний номер у штатному полі готової продукції MO.'))
        if lot.product_id != self.product_id or (lot.company_id and lot.company_id != self.company_id):
            raise ValidationError(_('Партія належить іншому товару або компанії.'))
        if self.lot_producing_id and self.lot_producing_id != lot:
            raise ValidationError(_('Обрана партія не відповідає партії готової продукції, вже вказаній у MO.'))
        for unit in units:
            if unit.lot_id and unit.lot_id != lot:
                raise ValidationError(_('Партія не відповідає вже зафіксованій партії QUNIT %(unit)s.', unit=unit.display_name))
        lines = self.move_finished_ids.filtered(lambda move: move.product_id == self.product_id and move.state != 'cancel').move_line_ids
        if any(line.quantity > 0 and line.lot_id and line.lot_id != lot for line in lines):
            raise ValidationError(_('Готові складські строки вже містять іншу партію. Автоматичне перепризначення заборонене.'))
        return lot

    def _validate_quality_full_batch(self, units, standard=False, posted=False):
        """Full batches only. No requirement for a fictional QC on every WO."""
        self.ensure_one()
        self._lock_quality_transfer_scope()
        valid_state = self.state == 'done' and self.qty_produced == self.product_qty if posted else (
            self.state in ('confirmed', 'progress', 'to_close') and not self.qty_produced)
        if (not valid_state
                or not (self.quality_unit_route_required or self.quality_transfer_controlled)):
            raise ValidationError(_('Потрібен відкритий захищений MO без попереднього випуску.'))
        if standard and self.quality_transfer_controlled:
            raise UserError(_('Історичний маршрут передач не переводиться на штатний випуск автоматично.'))
        destination = _read(self.location_dest_id)
        if destination.usage != 'internal' or destination.scrap_location or (destination.company_id and destination.company_id != self.company_id):
            raise ValidationError(_('Готова партія повинна надходити у внутрішню складську локацію цієї компанії.'))
        quantity = self.product_qty
        if (not math.isfinite(quantity) or quantity <= 0 or quantity != int(quantity)
                or self.product_uom_id != self.env.ref('uom.product_uom_unit')
                or self.product_uom_id != self.product_id.uom_id):
            raise ValidationError(_('Потрібна однозначна ціла партія в одиницях «шт.».'))
        _read(units)
        actual = self._full_release_units()
        if not units or len(units) != quantity or set(actual.ids) != set(units.ids):
            raise ValidationError(_('Немає повного складу QUNIT: спочатку ідентифікуйте всі вироби у QRUN.'))
        if (self.company_id not in self.env.companies or any(
                unit.company_id != self.company_id or unit.product_id != self.product_id
                or unit._quality_current_production() != self for unit in units)):
            raise ValidationError(_('QUNIT належать іншому MO, товару або компанії.'))
        if standard:
            # Native MRP may already have consumed components. Do not turn
            # that normal stock history into a second consumption algorithm.
            self.env['quality.stock.posting'].flush_model(['production_id'])
            self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE production_id=%s LIMIT 1', [self.id])
            if self.env.cr.fetchone():
                raise UserError(_('MO має архівне власне складське проведення; автоматична міграція заборонена.'))
        else:
            self._check_named_stock_history()
        units._assert_not_stock_posted()
        workorders = self._full_release_workorders()
        if not workorders:
            raise ValidationError(_('Не визначені необхідні робочі операції MO.'))
        if workorders.time_ids.filtered(lambda timer: not timer.date_end):
            raise ValidationError(_('Спочатку закрийте всі робочі інтервали MO.'))
        for workorder in workorders:
            if not self.quality_transfer_controlled:
                if workorder.state != 'done' or workorder.qty_produced < quantity:
                    raise ValidationError(_('Не завершено операцію %(operation)s для всієї партії.', operation=workorder.display_name))
            else:
                for unit in units:
                    processed = self.env['quality.unit.execution'].search([
                        ('controlled_unit_id', '=', unit.id), ('workorder_id', '=', workorder.id), ('state', '=', 'processed')])
                    handed_off = self.env['quality.unit.transfer'].search([
                        ('controlled_unit_id', '=', unit.id), ('source_workorder_id', '=', workorder.id),
                        ('state', 'in', ('received', 'advanced'))])
                    if not processed and not handed_off and not (
                            workorder.state == 'done' and workorder.qty_produced >= quantity):
                        raise ValidationError(_('Не підтверджено виконання %(operation)s для %(unit)s.', operation=workorder.display_name, unit=unit.display_name))
        return self._validate_quality_batch_controls(units, workorders, standard=standard)

    def _validate_quality_batch_controls(self, units, workorders, standard=False):
        """Shared exact QCP/admission/Alert gates; no quantity/stock authority."""
        requirements = self._capture_full_required_qcp()
        checks = self._full_release_checks()
        required = checks.filtered(lambda check: check.x_control_stage == 'in_process' and check.unit_admission_role == 'required')
        other = checks.filtered(lambda check: check.x_control_stage != 'in_process' and check.unit_admission_role == 'required')
        if standard:
            for point_id, operation_id, workorder_id in self._applicable_full_qcp(
                    workorders, control_stages=('incoming', 'final')):
                scoped = other.filtered(lambda check: check.point_id.id == point_id and (
                    not workorder_id or (
                        check.operation_run_id.workorder_id.id if check.operation_run_id else
                        check.workorder_id.id if 'workorder_id' in check._fields else 0) == workorder_id))
                if not scoped:
                    raise ValidationError(_('Не створена обов’язкова QC іншої стадії для QCP ID %(point)s.', point=point_id))
        if any(check.quality_state != 'pass' for check in other):
            raise ValidationError(_('Усі обов’язкові QC цього MO повинні бути завершені з PASS.'))
        if other:
            other._validate_measurement_can_pass()
        if not requirements or not required:
            raise ValidationError(_('Не підтверджено передбачені QCP міжопераційні QC.'))
        for check in required:
            if not check.operation_run_id:
                raise ValidationError(_('QC %(check)s не має QRUN/QUNIT. Ідентифікація має передувати завершенню QC.', check=check.display_name))
        for point_id, operation_id, workorder_id in requirements:
            if workorder_id:
                expected_workorder = workorders.filtered(lambda row: row.id == workorder_id)
                if not expected_workorder or (expected_workorder.operation_id.id or 0) != operation_id:
                    raise ValidationError(_('Змінено або видалено обов’язкову операцію WO ID %(operation)s.', operation=workorder_id))
            scoped = required.filtered(lambda check: check.point_id.id == point_id and (
                not workorder_id or check.operation_run_id.workorder_id.id == workorder_id))
            if not scoped:
                raise ValidationError(_('Не створена обов’язкова QC для QCP ID %(point)s, операція ID %(operation)s.', point=point_id, operation=workorder_id))
            for unit in units:
                relevant = scoped.filtered(lambda check: unit in check.sample_ids.controlled_unit_id)
                if not relevant:
                    raise ValidationError(_('QCP ID %(point)s не перевірив %(unit)s.', point=point_id, unit=unit.display_name))
                latest = _read(relevant.operation_run_id).sorted('id')[-1:]
                for check in relevant.filtered(lambda item: item.operation_run_id == latest):
                    sample = check.sample_ids.filtered(lambda row: row.controlled_unit_id == unit)
                    # A mixed matrix may retain PASS for this unit while
                    # only its failed neighbour receives a later retest.
                    # A FAIL decision with all inputs now PASS still needs
                    # an explicit QC completion, as before.
                    if (check.quality_state not in ('pass', 'fail') or len(sample) != 1
                            or sample.sample_result != 'pass'
                            or (check.quality_state == 'fail' and not any(row.sample_result == 'fail' for row in check.sample_ids))
                            or check._get_unit_admission_error(sample)):
                        raise ValidationError(_('QC %(check)s / %(unit)s: потрібні завершений PASS, ЗВТ і відсутність утримань.', check=check.display_name, unit=unit.display_name))
                admissions = self.env['quality.unit.admission'].search([
                    ('operation_run_id', '=', latest.id), ('controlled_unit_id', '=', unit.id), ('state', '=', 'confirmed')])
                if not admissions or any(admission._get_effectiveness_error() for admission in admissions):
                    raise ValidationError(_('Немає чинного допуску інспектора для %(unit)s / QCP ID %(point)s.', unit=unit.display_name, point=point_id))
        self._check_full_release_alerts(units, checks)
        return True
