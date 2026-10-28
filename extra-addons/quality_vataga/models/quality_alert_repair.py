"""Addressed repair evidence and repeat QC, without manufacturing movements."""

from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .quality_alert import BOARD_STAGE_XMLIDS


REPAIR_CHECK_TOKEN = object()
REPAIR_CONTEXT = 'quality_vataga_repair_check'
REPAIR_AUDIT = frozenset({
    'vataga_repair_done_by_id', 'vataga_repair_done_at', 'vataga_repair_note',
    'vataga_repair_check_ids', 'vataga_repair_closed_check_id',
})


class QualityAlertRepair(models.Model):
    _inherit = 'quality.alert'

    vataga_repair_done_by_id = fields.Many2one(
        'res.users', string='Доопрацювання підтвердив', readonly=True, copy=False,
        ondelete='restrict')
    vataga_repair_done_at = fields.Datetime(
        string='Доопрацювання виконано', readonly=True, copy=False)
    vataga_repair_note = fields.Text(
        string='Виконані роботи', readonly=True, copy=False)
    vataga_repair_check_ids = fields.One2many(
        'quality.check', 'vataga_repair_alert_id', string='Повторний контроль',
        readonly=True, copy=False)
    vataga_repair_closed_check_id = fields.Many2one(
        'quality.check', string='Контроль виконання рішення', readonly=True,
        copy=False, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if any(REPAIR_AUDIT.intersection(vals) for vals in vals_list):
            raise AccessError(_('Підтвердження ремонту встановлює лише захищена дія.'))
        context = {key: value for key, value in self.env.context.items()
                   if key not in {'default_' + name for name in REPAIR_AUDIT}}
        return super(QualityAlertRepair, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if REPAIR_AUDIT.intersection(vals):
            raise AccessError(_('Аудит ремонту та повторні перевірки не змінюють вручну.'))
        source_fields = ('check_id', 'production_id', 'workorder_id', 'product_id', 'company_id')
        if set(source_fields).intersection(vals):
            for alert in self.filtered('vataga_repair_done_at'):
                if any(name in vals and name in alert._fields and alert[name].id != vals[name]
                       for name in source_fields):
                    raise UserError(_('Джерело виконаного ремонту незмінне.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered('vataga_repair_done_at') or self._repair_checks():
            raise UserError(_('Історію виконаного ремонту та повторного контролю не видаляють.'))
        return super().unlink()

    def _repair_checks(self):
        """Include hidden attempts in existence checks, then enforce normal ACLs."""
        self.env['quality.check'].flush_model(['vataga_repair_alert_id'])
        self.env.cr.execute(
            'SELECT id FROM quality_check WHERE vataga_repair_alert_id IN %s ORDER BY id',
            [tuple(self.ids) or (0,)])
        checks = self.env['quality.check'].browse([row[0] for row in self.env.cr.fetchall()])
        checks.check_access_rights('read')
        checks.check_access_rule('read')
        return checks

    def _lock_board_records(self):
        # Same MO -> QRUN -> QC -> Alert order as measurements/admissions.
        # Version writes force Odoo's normal retry for concurrent RR snapshots.
        repairs = self.filtered(lambda alert: alert.decision_type == 'repair')
        if not repairs:
            return super()._lock_board_records()
        productions = self.env['mrp.production']
        for alert in repairs.filtered('check_id'):
            productions |= alert.check_id._identity_production()
        productions.sorted('id')._lock_quality_transfer_scope()
        checks = (repairs.check_id | repairs._repair_checks()) if repairs else self.env['quality.check']
        checks.check_access_rights('read')
        checks.check_access_rule('read')
        for check in checks.sorted('id'):
            check._lock_unit_admission_scope()
            self.env.cr.execute('SELECT id FROM quality_check WHERE id=%s FOR UPDATE', [check.id])
            check.invalidate_recordset()
        return super()._lock_board_records()

    def _repair_scope(self):
        return self._matrix_resolution_scope()

    def _matrix_resolution_scope(self):
        """The frozen Alert scope, not today's FAIL set or MO.qty_producing."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        check = self.check_id
        check.check_access_rights('read')
        check.check_access_rule('read')
        samples = self.vataga_failure_sample_ids
        samples.check_access_rights('read')
        samples.check_access_rule('read')
        scope_error = _(
            'Для виконання рішення ТР потрібна визначена сфера Alert: ідентифіковані QUNIT '
            'одного MO та зареєстрованого запуску відповідної операції. '
            'Для старого Alert спочатку уточніть проблемні зразки.')
        if not check or check.x_control_stage != 'in_process' or not samples:
            raise ValidationError(scope_error)
        run = check.operation_run_id
        run.check_access_rights('read')
        run.check_access_rule('read')
        production = check._identity_production()
        production.check_access_rights('read')
        production.check_access_rule('read')
        for name, expected in (('production_id', production), ('workorder_id', run.workorder_id),
                               ('product_id', check.product_id), ('company_id', production.company_id)):
            if name in self._fields and self[name] and self[name] != expected:
                raise ValidationError(scope_error)
        units = samples.controlled_unit_id
        units.check_access_rights('read')
        units.check_access_rule('read')
        if (check.x_control_stage != 'in_process' or not samples
                or samples.quality_check_id != check or len(units) != len(samples)
                or not run or run.state != 'registered' or not run.workorder_id
                or not production or production.company_id not in self.env.companies
                or run.production_id != production or run.product_id != check.product_id
                or run.company_id != production.company_id
                or ('company_id' in check._fields and check.company_id and check.company_id != production.company_id)
                or run.workorder_id.production_id != production
                or ('workorder_id' in check._fields and check.workorder_id
                    and check.workorder_id != run.workorder_id)
                or set(check.sample_ids.controlled_unit_id.ids) != set(run.unit_ids.ids)
                or len(check.sample_ids) != run.quantity
                or run.quantity != check.in_process_sample_quantity
                or units - run.unit_ids
                or any(unit.production_id != production or unit.product_id != check.product_id
                       or unit.company_id != production.company_id for unit in units)):
            raise ValidationError(scope_error)
        run.workorder_id.check_access_rights('read')
        run.workorder_id.check_access_rule('read')
        return samples.sorted('sample_number')

    def _check_repair_action(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.env.user.has_group('quality.group_quality_user'):
            raise AccessError(_('Виконання ремонту підтверджує користувач служби якості.'))
        if (self.decision_type != 'repair' or not self.is_approved
                or not self.approved_by_id or not self.approved_at
                or self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2])):
            raise UserError(_('Спочатку затвердьте рішення ТР «Ремонт/Доопрацювання».'))
        samples = self._repair_scope()
        production = self.check_id._identity_production()
        if production.state not in ('confirmed', 'progress') or self.check_id.operation_run_id.workorder_id.state in ('done', 'cancel'):
            raise UserError(_('Ремонт доступний для відкритого MO та незавершеної операції.'))
        return samples

    def action_open_repair_confirmation(self):
        self._check_repair_action()
        return {
            'type': 'ir.actions.act_window', 'name': _('Підтвердити доопрацювання'),
            'res_model': 'quality.alert.repair.wizard', 'view_mode': 'form', 'target': 'new',
            'views': [(self.env.ref('quality_vataga.quality_alert_repair_wizard_form').id, 'form')],
            'context': {'default_alert_id': self.id},
        }

    def action_record_repair(self, note):
        self.ensure_one()
        with self.env.cr.savepoint():
            self._lock_board_records()
            samples = self._check_repair_action()
            if not isinstance(note, str) or not note.strip():
                raise ValidationError(_('Опишіть фактично виконане доопрацювання всіх зазначених QUNIT.'))
            if self.vataga_repair_done_at:
                if note.strip() != self.vataga_repair_note:
                    raise UserError(_('Факт виконання вже зафіксовано; його не перезаписують.'))
                return True
            super(QualityAlertRepair, self).write({
                'vataga_repair_done_by_id': self.env.uid,
                'vataga_repair_done_at': fields.Datetime.now(), 'vataga_repair_note': note.strip(),
            })
            self.message_post(body=escape(_(
                'Доопрацювання за затвердженим рішенням ТР виконано для %(units)s. '
                'Виконавець підтвердження: %(user)s. Роботи: %(note)s',
                units=', '.join(samples.controlled_unit_id.mapped('name')),
                user=self.env.user.display_name, note=note.strip())), subtype_xmlid='mail.mt_note')
        return True

    def action_create_repair_check(self):
        self.ensure_one()
        with self.env.cr.savepoint():
            self._lock_board_records()
            samples = self._check_repair_action()
            if not self.vataga_repair_done_at or not self.vataga_repair_done_by_id:
                raise UserError(_('Спочатку зафіксуйте фактичне виконання доопрацювання.'))
            previous = self._repair_checks()[-1:]
            if previous and previous.quality_state != 'fail':
                return previous._repair_form_action()
            source = self.check_id
            values = {
                'vataga_repair_alert_id': self.id, 'point_id': source.point_id.id,
                'production_id': source._identity_production().id,
                'product_id': source.product_id.id, 'team_id': source.team_id.id,
                'test_type_id': source.test_type_id.id, 'measure_on': 'product',
                'quality_state': 'none', 'unit_admission_role': 'required',
            }
            for name in ('company_id', 'workorder_id'):
                if name in source._fields:
                    values[name] = (source.operation_run_id.workorder_id.id
                                    if name == 'workorder_id' else source[name].id)
            context = {key: value for key, value in self.env.context.items()
                       if not key.startswith('default_') and key != REPAIR_CONTEXT}
            check = self.env['quality.check'].with_context(
                context, **{REPAIR_CONTEXT: REPAIR_CHECK_TOKEN}).create(values).with_context(context)
            check._identify_samples('reuse', [
                {'sample_id': sample.id, 'unit_id': original.controlled_unit_id.id}
                for sample, original in zip(check.sample_ids.sorted('sample_number'), samples)
            ], workorder=source.operation_run_id.workorder_id, confirmed=True)
            # Creation and identification share the savepoint: no orphan QC or QRUN.
            check.message_post(body=escape(_(
                'Повторний контроль після доопрацювання за Alert %(alert)s. '
                'Початкові результати та snapshot збережено.', alert=self.display_name)),
                subtype_xmlid='mail.mt_note')
            self.message_post(body=escape(_(
                'Створено повторну перевірку %(check)s для %(units)s.',
                check=check.display_name, units=', '.join(samples.controlled_unit_id.mapped('name')))),
                subtype_xmlid='mail.mt_note')
            return check._repair_form_action()

    def _validate_repair_check(self, check):
        self.ensure_one()
        samples = self._repair_scope()
        check.check_access_rights('read')
        check.check_access_rule('read')
        run = check.operation_run_id
        if (not self.vataga_repair_done_at or not self.vataga_repair_done_by_id
                or not self.vataga_repair_note or self.decision_type != 'repair'
                or not self.is_approved or not self.approved_by_id or not self.approved_at
                or not check or check.vataga_repair_alert_id != self
                or check.quality_state != 'pass' or check.unit_admission_role != 'required'
                or check.x_control_stage != 'in_process' or check.point_id != self.check_id.point_id
                or check._identity_production() != self.check_id._identity_production()
                or not run or run == self.check_id.operation_run_id or run.state != 'registered'
                or run.workorder_id != self.check_id.operation_run_id.workorder_id
                or run.origin_check_id != check or run.registration_mode != 'reuse'
                or set(run.unit_ids.ids) != set(samples.controlled_unit_id.ids)
                or len(check.sample_ids) != len(samples)
                or set(check.sample_ids.controlled_unit_id.ids) != set(samples.controlled_unit_id.ids)):
            raise ValidationError(_('Потрібні підтверджений ремонт і завершений повторний PASS для всіх QUNIT сфери Alert.'))
        check._validate_measurement_can_pass()
        return check

    def _validate_production_resolution(self):
        if self.decision_type != 'repair':
            return super()._validate_production_resolution()
        self._validate_repair_check(self._repair_checks()[-1:])

    def _get_alert_closure_values(self):
        values = super()._get_alert_closure_values()
        if self.decision_type == 'repair':
            values['vataga_repair_closed_check_id'] = self._validate_repair_check(self._repair_checks()[-1:]).id
        return values

    def _production_resolution_complete(self):
        if self.decision_type != 'repair':
            return super()._production_resolution_complete()
        try:
            self._validate_repair_check(self.vataga_repair_closed_check_id)
        except (AccessError, UserError):
            return False
        return True


class QualityCheckRepair(models.Model):
    _inherit = 'quality.check'

    vataga_repair_alert_id = fields.Many2one(
        'quality.alert', string='Ремонт за рішенням ТР', readonly=True, copy=False,
        ondelete='restrict', index=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'vataga_repair_alert_id' in vals:
                if self.env.context.get(REPAIR_CONTEXT) is not REPAIR_CHECK_TOKEN:
                    raise AccessError(_('Повторний контроль ремонту створюють лише з відповідного Alert.'))
                self.env['quality.alert'].browse(vals['vataga_repair_alert_id'])._check_repair_action()
        context = dict(self.env.context)
        context.pop('default_vataga_repair_alert_id', None)
        return super(QualityCheckRepair, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'vataga_repair_alert_id' in vals:
            raise AccessError(_('Походження повторного контролю незмінне.'))
        if 'quality_state' in vals:
            # Take the shared lock before discovering repair facts. Otherwise
            # a waiting reset could miss a concurrently recorded repair in RR.
            identified = self.filtered('operation_run_id')
            identified.check_access_rights('write')
            identified.check_access_rule('write')
            for check in identified.sorted(lambda row: (row._identity_production().id, row.id)):
                check._lock_unit_admission_scope()
                check.invalidate_recordset(['quality_state'])
        if 'quality_state' in vals and self.ids:
            # Preserve a completed original QC as well as the repeat attempts.
            # Discover only IDs: a hidden repair must not permit rewriting history.
            self.env['quality.alert'].flush_model(['check_id', 'vataga_repair_done_at'])
            self.env.cr.execute('''SELECT DISTINCT check_id FROM quality_alert
                WHERE check_id IN %s AND vataga_repair_done_at IS NOT NULL''', [tuple(self.ids)])
            originals = self.browse([row[0] for row in self.env.cr.fetchall()])
            originals.check_access_rights('write')
            originals.check_access_rule('write')
            for check in originals.sorted('id'):
                if check.quality_state != 'none' and check.quality_state != vals['quality_state']:
                    raise UserError(_('Початкові результати завершеної QC ремонту зберігаються незмінними.'))
        for check in self.filtered('vataga_repair_alert_id'):
            if {'quality_state', 'unit_admission_role', 'point_id', 'production_id', 'workorder_id', 'product_id', 'company_id', 'quality_execution_ids'}.intersection(vals):
                check.check_access_rights('write')
                check.check_access_rule('write')
                check._lock_unit_admission_scope()
                check.invalidate_recordset(['quality_state'])
                if 'quality_state' in vals and check.quality_state != 'none' and vals['quality_state'] != check.quality_state:
                    raise UserError(_('Завершений повторний контроль не відкривають і не переписують. Створіть наступну перевірку з Alert.'))
                if vals.get('unit_admission_role', 'required') != 'required':
                    raise UserError(_('Повторний контроль ремонту є обов’язковим.'))
                for name in ('point_id', 'production_id', 'workorder_id', 'product_id', 'company_id'):
                    if name in vals and name in check._fields and check[name].id != vals[name]:
                        raise UserError(_('Джерело повторної перевірки незмінне.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered('vataga_repair_alert_id'):
            raise UserError(_('Повторні перевірки ремонту зберігаються в історії.'))
        return super().unlink()

    def _get_in_process_sample_quantity(self):
        self.ensure_one()
        if not self.vataga_repair_alert_id:
            return super()._get_in_process_sample_quantity()
        alert = self.vataga_repair_alert_id
        samples = alert._repair_scope()
        source = alert.check_id
        if (self.x_control_stage != 'in_process' or self.point_id != source.point_id
                or self._identity_production() != source._identity_production()
                or self.product_id != source.product_id or self.picking_id or self.move_line_id
                or ('workorder_id' in self._fields and self.workorder_id != source.operation_run_id.workorder_id)):
            return 0, _('Повторний контроль не відповідає адресній партії ремонту.')
        return len(samples), False

    def _is_repair_identification(self, mode, mapping, run, workorder):
        if not self.vataga_repair_alert_id:
            return super()._is_repair_identification(mode, mapping, run, workorder)
        alert = self.vataga_repair_alert_id
        samples = alert._check_repair_action()
        if (not alert.vataga_repair_done_at or mode != 'reuse' or run
                or workorder != alert.check_id.operation_run_id.workorder_id
                or {row.get('unit_id') for row in mapping} != set(samples.controlled_unit_id.ids)):
            raise ValidationError(_('Повторний контроль використовує лише ті самі QUNIT затвердженого ремонту.'))
        return True

    def _get_route_execution_evidence(self):
        self.ensure_one()
        alert = self.vataga_repair_alert_id
        if alert:
            if alert.vataga_repair_closed_check_id != self or not alert._is_managed_closed():
                raise UserError(_('Спочатку закрийте ремонтний Alert після повторного PASS.'))
            # Reuse the original processed evidence; never create a fictitious
            # execution, timer, transfer or production quantity for a repair.
            source = alert.check_id
            seen = {self.id}
            while source.vataga_repair_alert_id:
                source.check_access_rights('read')
                source.check_access_rule('read')
                if source.id in seen:
                    raise ValidationError(_('Некоректний цикл походження повторних перевірок.'))
                seen.add(source.id)
                source.vataga_repair_alert_id._repair_scope()
                source = source.vataga_repair_alert_id.check_id
            return source._get_route_execution_evidence().filtered(
                lambda row: row.controlled_unit_id in self.sample_ids.controlled_unit_id)
        return super()._get_route_execution_evidence()

    def _repair_form_action(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': self.display_name,
                'res_model': 'quality.check', 'res_id': self.id, 'view_mode': 'form',
                'views': [(False, 'form')], 'target': 'current'}
