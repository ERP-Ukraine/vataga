from markupsafe import escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .quality_unit_admission import ADMISSION_CONTEXT, ADMISSION_TOKEN


class QualityProductionRunAdmission(models.Model):
    _inherit = 'quality.production.run'

    unit_admission_revision = fields.Integer(default=0, readonly=True, copy=False)
    unit_admission_ids = fields.One2many('quality.unit.admission', 'operation_run_id', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if any({'unit_admission_revision', 'unit_admission_ids'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Службові дані допуску запуску встановлює система.'))
        context = dict(self.env.context)
        context.pop('default_unit_admission_revision', None)
        context.pop('default_unit_admission_ids', None)
        return super(QualityProductionRunAdmission, self.with_context(context)).create(vals_list)


class QualityCheckUnitAdmission(models.Model):
    _inherit = 'quality.check'

    unit_admission_role = fields.Selection(
        [('required', 'Обов’язкова'), ('optional', 'Необов’язкова')],
        string='Роль перевірки для допуску', default='required', required=True, copy=False,
        help='У межах одного явно вибраного запуску міжопераційні перевірки '
             'обов’язкові за замовчуванням. Необов’язкову роль до допуску '
             'може встановити лише керівник якості. Інші запуски та етапи незалежні.',
    )
    unit_admission_ids = fields.One2many('quality.unit.admission', 'check_id', readonly=True, copy=False)
    x_admitted_qty = fields.Integer(string='Допущено', compute='_compute_unit_admission_quantities', copy=False)
    x_held_qty = fields.Integer(string='Утримано', compute='_compute_unit_admission_quantities', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        for vals in vals_list:
            if {'x_admitted_qty', 'x_held_qty', 'unit_admission_ids'}.intersection(vals):
                raise UserError(_('Кількість допусків та їх записи встановлює система.'))
            role = vals.get('unit_admission_role', 'required')
            if role != 'required' and not self.env.user.has_group('quality.group_quality_manager'):
                raise AccessError(_('Лише керівник якості визначає необов’язкові перевірки.'))
            prepared.append(dict(vals, unit_admission_role=role, unit_admission_ids=[]))
        context = dict(self.env.context)
        for name in ('x_admitted_qty', 'x_held_qty', 'unit_admission_ids', 'unit_admission_role'):
            context.pop('default_' + name, None)
        return super(QualityCheckUnitAdmission, self.with_context(context)).create(prepared)

    def write(self, vals):
        if {'x_admitted_qty', 'x_held_qty', 'unit_admission_ids'}.intersection(vals):
            raise UserError(_('Кількість допусків та їх записи встановлює система.'))
        if 'unit_admission_role' in vals:
            if not self.env.user.has_group('quality.group_quality_manager'):
                raise AccessError(_('Лише керівник якості визначає роль перевірки.'))
            self.check_access_rights('write')
            self.check_access_rule('write')
            for check in self:
                check._lock_unit_admission_scope()
                if check.operation_run_id and check._active_run_admissions():
                    raise UserError(_('Спочатку скасуйте допуски запуску перед зміною обов’язкових перевірок.'))
        old_roles = {check.id: check.unit_admission_role for check in self} if 'unit_admission_role' in vals else {}
        result = super().write(vals)
        for check in self:
            if check.id in old_roles and old_roles[check.id] != check.unit_admission_role:
                check.message_post(body=escape(_('Роль перевірки для допуску змінено: %(role)s.', role=check.unit_admission_role)), subtype_xmlid='mail.mt_note')
        return result

    def _check_unit_admission_access(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.env['quality.unit.admission'].check_access_rights('create')
        if self.x_control_stage != 'in_process':
            raise ValidationError(_('Допуск виробів доступний лише для міжопераційного контролю.'))
        production = self._identity_production()
        if not production or production.company_id not in self.env.companies:
            raise ValidationError(_('Потрібне доступне однозначне MO.'))
        production.check_access_rights('read')
        production.check_access_rule('read')
        return production

    def _lock_unit_admission_scope(self):
        """Shared with evidence edits and registration, including sibling QCs.

        Updating the run revision is intentional: SELECT FOR UPDATE alone
        cannot refresh a PostgreSQL REPEATABLE READ snapshot. A concurrent
        mutation then causes the normal Odoo serialization retry.
        """
        self.ensure_one()
        production = self._identity_production()
        if not production:
            return
        # Share the MO version with native Backorder release as well as the
        # run version. A completed revocation must invalidate a release's
        # older RR snapshot even when the release has not read that run yet.
        production._lock_quality_transfer_scope()
        self.invalidate_recordset(['operation_run_id', 'unit_admission_role'])
        if self.operation_run_id:
            self.env.cr.execute(
                'UPDATE quality_production_run SET unit_admission_revision = COALESCE(unit_admission_revision, 0) + 1 WHERE id = %s',
                [self.operation_run_id.id],
            )
            self.operation_run_id.invalidate_recordset(['unit_admission_revision'], flush=False)

    def _active_run_admissions(self):
        self.ensure_one()
        # Only discover IDs with elevated read access. Hidden approvals must
        # still lock evidence; authorization never follows from this search.
        return self.env['quality.unit.admission'].sudo().search([
            ('operation_run_id', '=', self.operation_run_id.id), ('state', '=', 'confirmed'),
        ])

    def _unit_admission_required_checks(self):
        self.ensure_one()
        ids = self.env['quality.check'].sudo().search([
            ('operation_run_id', '=', self.operation_run_id.id),
            ('x_control_stage', '=', 'in_process'), ('unit_admission_role', '=', 'required'),
        ]).ids
        checks = self.env['quality.check'].browse(ids) | self
        checks.check_access_rights('read')
        checks.check_access_rule('read')
        return checks

    def _get_unit_admission_structure_error(self):
        self.ensure_one()
        if self.x_control_stage != 'in_process':
            return _('Потрібен міжопераційний контроль.')
        run = self.operation_run_id
        if not run or run.state != 'registered' or not run.workorder_id:
            return _('Ідентифікуйте вироби та оберіть однозначне робоче завдання зареєстрованого запуску.')
        run.check_access_rights('read')
        run.check_access_rule('read')
        production = self._identity_production()
        if (
            run.production_id != production or run.workorder_id.production_id != production
            or run.product_id != self.product_id or run.company_id != production.company_id
            or production.company_id not in self.env.companies
            or 'workorder_id' in self._fields and self.workorder_id and self.workorder_id != run.workorder_id
            or 'company_id' in self._fields and self.company_id and self.company_id != run.company_id
        ):
            return _('MO, робоче завдання, виріб або компанія не відповідають запуску.')
        error = self._get_in_process_batch_error()
        if error:
            return error
        if run.quantity != self.in_process_sample_quantity or len(run.unit_ids) != run.quantity:
            return _('Склад запуску не відповідає зафіксованій партії.')
        units = self.sample_ids.controlled_unit_id
        units.check_access_rights('read')
        units.check_access_rule('read')
        if len(units) != run.quantity or set(units.ids) != set(run.unit_ids.ids):
            return _('Кожен зразок повинен відповідати окремому виробу цього запуску.')
        if any(not unit._quality_matches_production(production) or unit.product_id != run.product_id or unit.company_id != run.company_id for unit in units):
            return _('Ідентифіковані вироби не відповідають MO, продукту або компанії.')
        try:
            self._validate_measurement_sample_structure()
        except (ValidationError, UserError) as error:
            return str(error)
        if not self.equipment_selection_complete:
            return _('Не виконано вимоги щодо вибору ЗВТ.')
        if self.measurement_column_ids.filtered(lambda column: column.parameter_type == 'boolean' and not column.boolean_expected):
            return _('Snapshot містить неоднозначну булеву норму. Допуск заборонено.')
        selections = self.equipment_selection_ids.filtered('requires_equipment_selection')
        if (self._get_required_measurement_equipment_categories() and not selections) or any(
            not selection._has_valid_equipment_selection() for selection in selections
        ):
            return _('Не виконано вимоги щодо вибору допустимих ЗВТ.')
        return False

    def _get_unit_admission_error(self, sample):
        self.ensure_one()
        if not sample or sample.quality_check_id != self:
            return _('Зразок не належить поточній перевірці.')
        try:
            error = self._get_unit_admission_structure_error()
            if error:
                return error
            if sample.sample_result != 'pass':
                return _('Допуск можливий лише для зразка PASS; FAIL та pending утримуються.')
            for check in self._unit_admission_required_checks():
                error = check._get_unit_admission_structure_error()
                if error:
                    return _('%(check)s: %(reason)s', check=check.display_name, reason=error)
                related = check.sample_ids.filtered(lambda item: item.controlled_unit_id == sample.controlled_unit_id)
                if len(related) != 1 or related.sample_result != 'pass':
                    return _('Обов’язкова перевірка %(check)s не підтвердила PASS цього виробу.', check=check.display_name)
        except AccessError:
            return _('Немає доступу до всіх обов’язкових перевірок, виробів або запуску. Допуск заборонено.')
        except (UserError, ValidationError) as error:
            return str(error)
        return False

    def _guard_unit_admission_evidence_edit(self, unit_ids=None):
        for check in self.filtered('operation_run_id').sorted('id'):
            check.check_access_rights('write')
            check.check_access_rule('write')
            check._lock_unit_admission_scope()
            admissions = check._active_run_admissions()
            if check.unit_admission_role != 'required':
                admissions = admissions.filtered(lambda record: record.check_id.id == check.id)
            if unit_ids is not None:
                admissions = admissions.filtered(lambda record: record.controlled_unit_id.id in unit_ids)
            if admissions:
                raise UserError(_('Спочатку скасуйте допуск відповідних виробів. Вимірювання та ЗВТ, на яких ґрунтується чинний допуск, не редагуються.'))

    def _identify_samples_locked(self, *args, **kwargs):
        already_bound = bool(self.operation_run_id)
        run = super()._identify_samples_locked(*args, **kwargs)
        if not already_bound:
            self._lock_unit_admission_scope()
            if self._active_run_admissions():
                raise UserError(_('Перед додаванням перевірки до запуску скасуйте його допуски та підтвердьте їх за повним набором перевірок.'))
        return run

    def action_open_unit_admission_wizard(self):
        return self._open_unit_admission_wizard('confirm')

    def action_open_unit_admission_revocation_wizard(self):
        return self._open_unit_admission_wizard('revoke')

    def _open_unit_admission_wizard(self, mode):
        self._check_unit_admission_access()
        return {
            'type': 'ir.actions.act_window', 'name': _('Допуск виробів'),
            'res_model': 'quality.unit.admission.wizard', 'view_mode': 'form', 'target': 'new',
            'views': [(self.env.ref('quality_vataga.quality_unit_admission_wizard_form').id, 'form')],
            'context': {'default_check_id': self.id, 'default_mode': mode},
        }

    def _execute_unit_admission_action(self, sample_ids, mode, request_key, reason='', expected_by_sample=None):
        self.ensure_one()
        self._check_unit_admission_access()
        if mode not in ('confirm', 'revoke') or not sample_ids or len(set(sample_ids)) != len(sample_ids):
            raise ValidationError(_('Явно оберіть вироби для підтвердження або скасування.'))
        if mode == 'revoke' and not reason.strip():
            raise ValidationError(_('Вкажіть причину скасування допуску.'))
        with self.env.cr.savepoint():
            self._lock_unit_admission_scope()
            self.invalidate_recordset(['sample_ids', 'in_process_sample_quantity', 'equipment_selection_complete'])
            samples = self.sample_ids.filtered(lambda sample: sample.id in sample_ids)
            samples.invalidate_recordset(['sample_result', 'controlled_unit_id'])
            if set(samples.ids) != set(sample_ids):
                raise ValidationError(_('Усі обрані зразки мають належати цій перевірці.'))
            if not self.operation_run_id:
                raise ValidationError(_('Спочатку ідентифікуйте вироби.'))
            registrar = self.env['quality.unit.admission'].with_context(**{ADMISSION_CONTEXT: ADMISSION_TOKEN})
            for sample in samples:
                active = self._active_run_admissions().filtered(lambda record: record.controlled_unit_id.id == sample.controlled_unit_id.id)
                if mode == 'revoke':
                    expected = (expected_by_sample or {}).get(sample.id)
                    if active and active.id != expected:
                        raise UserError(_('Допуск змінився після відкриття майстра. Відкрийте майстер знову.'))
                    if active:
                        active.with_user(self.env.user).sudo(False)._revoke(reason.strip())
                    continue
                error = self._get_unit_admission_error(sample)
                if error:
                    raise ValidationError(error)
                previous = self.env['quality.unit.admission'].sudo().search([
                    ('request_key', '=', request_key), ('controlled_unit_id', '=', sample.controlled_unit_id.id),
                ])
                if previous and previous.state == 'revoked':
                    raise UserError(_('Це підтвердження вже скасовано. Відкрийте новий майстер для нового рішення.'))
                if active:
                    continue
                admission = registrar.create({'check_id': self.id, 'sample_id': sample.id, 'request_key': request_key})
                self.message_post(body=escape(_(
                    'Інспектор підтвердив допуск виробу %(unit)s (зразок №%(number)s) у запуску %(run)s. Переміщення виробу не виконується.',
                    unit=sample.controlled_unit_id.name, number=sample.sample_number, run=admission.operation_run_id.name,
                )), subtype_xmlid='mail.mt_note')
        return True

    @api.depends(
        'x_control_stage', 'in_process_sample_quantity', 'sample_ids.controlled_unit_id',
        'sample_ids.sample_result', 'equipment_selection_complete',
        'production_id.qty_producing', 'operation_run_id.unit_admission_ids.state',
        'operation_run_id.production_id.qty_producing', 'operation_run_id.workorder_id.production_id',
        'operation_run_id.check_ids.sample_ids.sample_result',
        'operation_run_id.check_ids.equipment_selection_complete',
        'operation_run_id.check_ids.unit_admission_role',
    )
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_unit_admission_quantities(self):
        for check in self:
            count = 0
            if check.x_control_stage == 'in_process' and check.operation_run_id:
                active_units = check._active_run_admissions().filtered(
                    lambda record: not record._get_effectiveness_error(),
                ).controlled_unit_id.ids
                count = sum(
                    sample.controlled_unit_id.id in active_units
                    for sample in check.sample_ids
                )
            check.x_admitted_qty = count
            check.x_held_qty = max(check.in_process_sample_quantity - count, 0) if check.x_control_stage == 'in_process' else 0

    def get_measurement_matrix_data(self):
        data = super().get_measurement_matrix_data()
        data.update(admitted_quantity=self.x_admitted_qty, held_quantity=self.x_held_qty)
        if self.x_control_stage == 'in_process' and self.operation_run_id:
            samples = {sample.id: sample for sample in self.sample_ids}
            active = self._active_run_admissions()
            for row in data['samples']:
                sample = samples[row['id']]
                approved = active.filtered(lambda record: record.controlled_unit_id.id == sample.controlled_unit_id.id)
                error = approved._get_effectiveness_error() if approved else False
                row['unit_admission_label'] = _('Допуск призупинено') if error else (_('Допущено') if approved else _('Утримано'))
                row['unit_admission_reason'] = error or ''
        return data


class QualityCheckSampleAdmission(models.Model):
    _inherit = 'quality.check.sample'

    def _prepare_rework_tracking(self):
        for check in self.quality_check_id.filtered('operation_run_id').sudo(False).sorted('id'):
            units = self.filtered(lambda sample: sample.quality_check_id.id == check.id).controlled_unit_id.ids
            check._guard_unit_admission_evidence_edit(units)
        return super()._prepare_rework_tracking()


class QualityCheckEquipmentAdmission(models.Model):
    _inherit = 'quality.check.equipment.selection'

    def write(self, vals):
        if 'equipment_ids' in vals:
            self.quality_check_id.sudo(False)._guard_unit_admission_evidence_edit()
        return super().write(vals)
