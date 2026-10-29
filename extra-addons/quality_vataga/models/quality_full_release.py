"""Full-batch readiness and explicit, per-MO staging authorization."""
import math

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .quality_native_backorder import _read, _require_roles

FULL_RELEASE_FIELDS = {'quality_full_release_test_database', 'quality_full_required_qcp'}


def require_full_release_roles(env):
    _require_roles(env)
    if not env.user.has_group('quality.group_quality_user'):
        raise AccessError(_('Потрібні права контролю якості, виробництва та складу.'))


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
        self.ensure_one()
        require_full_release_roles(self.env)
        if not (self.env.user.has_group('base.group_system')
                and self.env.user.has_group('quality.group_quality_manager')):
            raise AccessError(_('Тестовий випуск на окремому MO дозволяє адміністратор — керівник якості.'))
        with self.env.cr.savepoint():
            self.check_access_rights('write')
            self.check_access_rule('write')
            self._lock_quality_transfer_scope()
            if (self.state not in ('confirmed', 'progress', 'to_close') or self.qty_produced
                    or not (self.quality_unit_route_required or self.quality_transfer_controlled)):
                raise UserError(_('Потрібен відкритий захищений тестовий MO без попереднього випуску.'))
            self._capture_full_required_qcp()
            super(QualityFullReleaseProduction, self).write({
                'quality_full_release_test_database': self.env.cr.dbname})
            self.message_post(body=_('Адміністратор дозволив ручний staging-тест повного випуску цього MO у поточній базі. Частковий випуск не дозволено.'), subtype_xmlid='mail.mt_note')
        return True

    def _check_full_release_test_access(self):
        self.ensure_one()
        require_full_release_roles(self.env)
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.company_id not in self.env.companies:
            raise AccessError(_('MO належить іншій компанії.'))
        if self.quality_full_release_test_database != self.env.cr.dbname:
            raise UserError(_('Повний випуск закрито. Адміністратор має явно дозволити staging-тест саме цього нового MO кнопкою «Дозволити тест повного випуску».'))

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

    def _applicable_full_qcp(self, workorders):
        """Resolve actual QCP assignments, not the absence of generated QC.

        Enterprise's routing relation is identified by its ORM comodel, rather
        than a guessed field name. Product, category, company and operation-type
        assignments are applied; the resulting requirements are sticky on MO.
        """
        Point = self.env['quality.point']
        domain = [('x_control_stage', '=', 'in_process')]
        if 'company_id' in Point._fields:
            domain.append(('company_id', 'in', [False, self.company_id.id]))
        ids = Point.sudo().search(domain).ids  # discover hidden requirements only
        points = _read(Point.browse(ids))
        operation_fields = [name for name, field in Point._fields.items()
                            if getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter'
                            and field.type in ('many2one', 'many2many')]
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

    def _validate_quality_full_batch(self, units):
        """Full batches only. No requirement for a fictional QC on every WO."""
        self.ensure_one()
        self._lock_quality_transfer_scope()
        if (self.state not in ('confirmed', 'progress', 'to_close') or self.qty_produced
                or not (self.quality_unit_route_required or self.quality_transfer_controlled)):
            raise ValidationError(_('Потрібен відкритий захищений MO без попереднього випуску.'))
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
        requirements = self._capture_full_required_qcp()
        checks = self._full_release_checks()
        required = checks.filtered(lambda check: check.x_control_stage == 'in_process' and check.unit_admission_role == 'required')
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
                    if check.quality_state == 'none' or check._get_unit_admission_error(sample):
                        raise ValidationError(_('QC %(check)s / %(unit)s: потрібні завершений PASS, ЗВТ і відсутність утримань.', check=check.display_name, unit=unit.display_name))
                admissions = self.env['quality.unit.admission'].search([
                    ('operation_run_id', '=', latest.id), ('controlled_unit_id', '=', unit.id), ('state', '=', 'confirmed')])
                if not admissions or any(admission._get_effectiveness_error() for admission in admissions):
                    raise ValidationError(_('Немає чинного допуску інспектора для %(unit)s / QCP ID %(point)s.', unit=unit.display_name, point=point_id))
        self._check_full_release_alerts(units, checks)
        return True
