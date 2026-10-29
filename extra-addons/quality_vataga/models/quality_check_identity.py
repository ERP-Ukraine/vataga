from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .quality_production_identity import IDENTIFICATION_CONTEXT, IDENTIFICATION_TOKEN


class QualityCheckIdentity(models.Model):
    _inherit = 'quality.check'

    operation_run_id = fields.Many2one(
        'quality.production.run', string='Запуск операції', readonly=True,
        copy=False, ondelete='restrict', index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any('operation_run_id' in vals for vals in vals_list):
            raise UserError(_('Запуск призначається лише майстром ідентифікації.'))
        context = dict(self.env.context)
        context.pop('default_operation_run_id', None)
        return super(QualityCheckIdentity, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'operation_run_id' in vals:
            raise UserError(_('Запуск призначається лише майстром ідентифікації.'))
        for check in self.filtered('operation_run_id'):
            for name in ('production_id', 'product_id', 'company_id', 'workorder_id'):
                if name in vals and name in check._fields and vals[name] != (check[name].id or False):
                    raise UserError(_('Не можна змінити джерело ідентифікованої перевірки.'))
        return super().write(vals)

    def unlink(self):
        if self.filtered('operation_run_id'):
            raise UserError(_('Ідентифіковані перевірки зберігають історію виробів та не видаляються.'))
        return super().unlink()

    def _identity_production(self):
        self.ensure_one()
        production = self.production_id
        workorder = self.workorder_id if 'workorder_id' in self._fields else False
        if workorder:
            if production and production != workorder.production_id:
                raise ValidationError(_('Робоче завдання належить іншому MO.'))
            production = workorder.production_id
        return production

    def _check_identification_access(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.env['quality.production.run'].check_access_rights('create')
        self.env['quality.production.unit'].check_access_rights('create')
        production = self._identity_production()
        if (
            self.x_control_stage != 'in_process' or not production
            or self.product_id != production.product_id
            or production.company_id not in self.env.companies
            or 'company_id' in self._fields and self.company_id and self.company_id != production.company_id
        ):
            raise ValidationError(_('Для ідентифікації потрібна однозначна міжопераційна перевірка цього MO, виробу та компанії.'))
        production.check_access_rights('read')
        production.check_access_rule('read')
        return production

    def action_open_identification_wizard(self):
        self._check_identification_access()
        if self.operation_run_id:
            self.operation_run_id.check_access_rights('read')
            self.operation_run_id.check_access_rule('read')
            return self.operation_run_id.get_formview_action()
        self._ensure_measurement_editable()
        error = self._get_in_process_batch_error(validate_current=True)
        if error:
            raise ValidationError(error)
        self._validate_measurement_sample_structure()
        return {
            'type': 'ir.actions.act_window', 'name': _('Ідентифікація виробів'),
            'res_model': 'quality.check.identification.wizard', 'view_mode': 'form',
            'target': 'new', 'context': {
                'default_check_id': self.id,
                'default_workorder_id': self.workorder_id.id if 'workorder_id' in self._fields else False,
            },
        }

    def _identify_samples(self, mode, mapping, run=None, workorder=None, confirmed=False):
        self.ensure_one()
        with self.env.cr.savepoint():
            return self._identify_samples_locked(mode, mapping, run, workorder, confirmed)

    def _identify_samples_locked(self, mode, mapping, run, workorder, confirmed):
        """One-time binding shared by QC initialization and explicit selection.

        Lock MO before QC so two wizards cannot register two batches for the
        same check. SQL uniqueness also protects the origin and unit numbers.
        """
        production = self._check_identification_access()
        if mode not in ('new', 'reuse', 'existing') or not confirmed:
            raise UserError(_('Оберіть спосіб і підтвердіть відповідність зразків виробам та наявним результатам.'))
        # A concurrent registration changes the release evidence population;
        # bump the shared MO version rather than keep an older RR snapshot.
        production._lock_quality_transfer_scope()
        self.env.cr.execute('SELECT id FROM quality_check WHERE id = %s FOR UPDATE', [self.id])
        production.invalidate_recordset(['product_id', 'company_id', 'qty_producing', 'product_uom_id', 'state'])
        self.invalidate_recordset([
            'operation_run_id', 'quality_state', 'sample_ids', 'in_process_sample_quantity',
            'production_id', 'product_id',
        ] + [name for name in ('company_id', 'workorder_id') if name in self._fields])
        if self._check_identification_access() != production:
            raise ValidationError(_('Джерело перевірки змінилося. Відкрийте майстер знову.'))
        samples = self.sample_ids.sorted('sample_number')
        samples.invalidate_recordset(['controlled_unit_id'])
        if len(mapping) != len(samples) or {row['sample_id'] for row in mapping} != set(samples.ids):
            raise ValidationError(_('Майстер повинен містити рівно один рядок для кожного зразка цієї перевірки.'))
        by_sample = {row['sample_id']: row for row in mapping}
        workorder = workorder or self.env['mrp.workorder']
        run = run or self.env['quality.production.run']
        if workorder:
            workorder.check_access_rights('read')
            workorder.check_access_rule('read')
            if workorder.production_id != production:
                raise ValidationError(_('Робоче завдання має належати цьому MO.'))
        if run:
            run.check_access_rights('read')
            run.check_access_rule('read')
        check_workorder = self.workorder_id if 'workorder_id' in self._fields else False
        selected_workorder = run.workorder_id if mode == 'existing' else workorder
        if check_workorder and check_workorder != selected_workorder:
            raise ValidationError(_('Запуск повинен відповідати робочому завданню перевірки.'))

        if self.operation_run_id:
            existing = self.operation_run_id
            same_run = (
                mode == 'existing' and run == existing
                or mode in ('new', 'reuse') and existing.origin_check_id == self
                and existing.registration_mode == mode and existing.workorder_id == workorder
            )
            same_units = all(
                sample.controlled_unit_id
                and (not by_sample[sample.id].get('lot_id') or by_sample[sample.id]['lot_id'] == sample.controlled_unit_id.lot_id.id)
                and (
                    mode == 'new' and not by_sample[sample.id].get('unit_id')
                    and (by_sample[sample.id].get('lot_id') or False) == (sample.controlled_unit_id.lot_id.id or False)
                    or mode != 'new' and by_sample[sample.id].get('unit_id') == sample.controlled_unit_id.id
                ) for sample in samples
            )
            if same_run and same_units:
                return existing
            raise UserError(_('Відповідність цієї перевірки вже зафіксована. Заміна виробів або запуску заборонена.'))

        self._ensure_measurement_editable()
        error = self._get_in_process_batch_error(validate_current=True)
        if error:
            raise ValidationError(error)
        self._validate_measurement_sample_structure()
        if samples.filtered('controlled_unit_id'):
            raise ValidationError(_('Перевірка має частково призначені ідентифікатори. Автоматичне виправлення заборонено.'))
        if mode == 'new' and self.env['quality.production.unit'].sudo().search_count([
            '|', ('production_id', '=', production.id),
            ('native_continuation_ids.production_id', '=', production.id),
        ], limit=1):
            raise ValidationError(_('Вироби цього MO вже зареєстровані. Оберіть наявні QUNIT у матриці.'))
        if mode == 'existing':
            if (
                not run or run.state != 'registered' or run.production_id != production
                or run.product_id != self.product_id or run.company_id != production.company_id
                or run.quantity != self.in_process_sample_quantity
            ):
                raise ValidationError(_('Оберіть сумісний зафіксований запуск з такою самою кількістю виробів.'))
        elif run:
            raise ValidationError(_('Для нового запуску не можна підставляти наявний запуск.'))

        units = self.env['quality.production.unit']
        if mode != 'new':
            unit_ids = [row.get('unit_id') for row in mapping]
            if not all(unit_ids) or len(set(unit_ids)) != len(samples):
                raise ValidationError(_('Явно оберіть різні вироби для всіх зразків.'))
            units = units.browse(unit_ids).exists()
            units.check_access_rights('read')
            units.check_access_rule('read')
            if len(units) != len(samples) or any(
                unit._quality_current_production() != production or unit.product_id != self.product_id
                or unit.company_id != production.company_id for unit in units
            ):
                raise ValidationError(_('Всі вироби мають належати цьому MO, виробу та компанії.'))
            if mode == 'existing' and set(units.ids) != set(run.unit_ids.ids):
                raise ValidationError(_('Відповідність повинна охоплювати весь склад вибраного запуску.'))
            if any(row.get('lot_id') and row['lot_id'] != units.browse(row['unit_id']).lot_id.id for row in mapping):
                raise ValidationError(_('Реальна партія зареєстрованого виробу незмінна.'))
        elif any(row.get('unit_id') for row in mapping):
            raise ValidationError(_('Для нових виробів не можна підставляти наявні ідентифікатори.'))

        registrar = self.env['quality.production.run'].with_context(**{IDENTIFICATION_CONTEXT: IDENTIFICATION_TOKEN})
        if mode != 'existing':
            run = registrar.create({
                'production_id': production.id, 'workorder_id': workorder.id or False,
                'quantity': self.in_process_sample_quantity, 'origin_check_id': self.id,
                'registration_mode': mode,
            })
            if mode == 'new':
                units = self.env['quality.production.unit'].with_context(**{IDENTIFICATION_CONTEXT: IDENTIFICATION_TOKEN}).create([
                    {'production_id': production.id, 'registration_run_id': run.id,
                     'unit_number': index, 'lot_id': by_sample[sample.id].get('lot_id') or False}
                    for index, sample in enumerate(samples, 1)
                ])
            run.write({'unit_ids': [Command.set(units.ids)], 'state': 'registered'})
        super(QualityCheckIdentity, self).write({'operation_run_id': run.id})
        if mode == 'new':
            mapping_units = dict(zip(samples.ids, units.sorted('unit_number')))
        else:
            mapping_units = {row['sample_id']: units.browse(row['unit_id']) for row in mapping}
        for sample in samples:
            sample._bind_controlled_unit(mapping_units[sample.id])
        return run

    def get_measurement_matrix_data(self):
        can_identify = all([
            self.check_access_rights('write', raise_exception=False),
            self.env['quality.production.run'].check_access_rights('create', raise_exception=False),
            self.env['quality.production.unit'].check_access_rights('create', raise_exception=False),
        ])
        if can_identify:
            try:
                self.check_access_rule('write')
            except AccessError:
                can_identify = False
        data = super().get_measurement_matrix_data()
        samples = {sample.id: sample for sample in self.sample_ids}
        for row in data['samples']:
            unit = samples[row['id']].controlled_unit_id
            if unit:
                unit.check_access_rights('read')
                unit.check_access_rule('read')
            row['controlled_unit_id'] = unit.id or False
            row['controlled_unit_name'] = unit.name or ''
        if self.x_control_stage == 'in_process':
            data['operation_run_id'] = self.operation_run_id.id or False
            data['can_initialize_identity'] = bool(can_identify and self.quality_state == 'none' and not self.operation_run_id)
        if self.x_control_stage == 'in_process' and self.quality_state == 'none' and not self.operation_run_id and can_identify:
            units = self._qc_existing_units()
            data['identity_choices'] = [{'id': unit.id, 'name': unit.name} for unit in units]
            data['identity_required'] = bool(self.sample_ids)
            data['identity_reason'] = _('Оберіть наявні QUNIT. Якщо операцію або фізичний склад партії не визначено, уточніть їх у стандартному робочому завданні; нові ідентифікатори не створюються навмання.')
        return data

    def initialize_qc_unit_identity(self):
        """Called automatically on a clean QC form, never by a read getter."""
        self.ensure_one()
        self._ensure_measurement_editable()
        with self.env.cr.savepoint():
            self._ensure_qc_unit_identity()
            return self.get_measurement_matrix_data()

    def _qc_existing_units(self):
        """Discover hidden identities too; never interpret ACL filtering as absence."""
        self.ensure_one()
        production = self._identity_production()
        if not production:
            return self.env['quality.production.unit']
        ids = self.env['quality.production.unit'].sudo().search([
            '|', ('production_id', '=', production.id),
            ('native_continuation_ids.production_id', '=', production.id),
        ]).ids
        units = self.env['quality.production.unit'].browse(ids)
        units.check_access_rights('read')
        units.check_access_rule('read')
        return units.filtered(lambda unit: unit._quality_current_production() == production)

    def _qc_identity_workorder(self):
        self.ensure_one()
        workorder = self.workorder_id if 'workorder_id' in self._fields else self.env['mrp.workorder']
        if not workorder:
            candidates = self._identity_production().workorder_ids
            if len(candidates) == 1:
                workorder = candidates
        return workorder

    def _ensure_qc_unit_identity(self):
        self.ensure_one()
        if (self.x_control_stage != 'in_process' or self.quality_state != 'none'
                or self.operation_run_id or not self.sample_ids
                or self._get_in_process_batch_error(validate_current=True)):
            return
        with self.env.cr.savepoint():
            production = self._check_identification_access()
            production._lock_quality_transfer_scope()
            self.invalidate_recordset(['operation_run_id', 'sample_ids'])
            if self.operation_run_id:
                return
            units = self._qc_existing_units()
            samples = self.sample_ids.sorted('sample_number')
            workorder = self._qc_identity_workorder()
            if not workorder and production.workorder_ids:
                return
            # An execution-backed historical QC already has explicit identities.
            explicit = self.quality_execution_ids.controlled_unit_id
            if explicit:
                return  # preserve the existing protected execution workflow
            if units:
                # Equal batch sizes do not prove which physical unit is row 1.
                if len(units) != 1 or len(samples) != 1:
                    return
                mode = 'reuse'
            else:
                # Do not re-register units moved to a continuation or already
                # produced. Initial registration must describe a fresh batch.
                if production.qty_produced or self.env['quality.production.run'].sudo().search_count([
                    ('production_id', '=', production.id),
                ], limit=1):
                    return
                mode = 'new'
            lot = production.lot_producing_id
            if mode == 'new' and self.product_id.tracking == 'serial':
                if len(samples) != 1 or not lot or self.env['quality.production.unit'].sudo().search_count([
                    ('serial_lot_id', '=', lot.id),
                ], limit=1):
                    return
            self._identify_samples(mode, [
                {'sample_id': sample.id, 'unit_id': units.id if units else False,
                 'lot_id': lot.id if mode == 'new' and self.product_id.tracking in ('lot', 'serial') else False}
                for sample in samples
            ], workorder=workorder, confirmed=True)

    def bind_measurement_units(self, mapping):
        """One atomic selection from the QC matrix; no new identities via RPC."""
        self.ensure_one()
        if not isinstance(mapping, list) or any(
                not isinstance(row, dict) or set(row) != {'sample_id', 'unit_id'}
                or type(row['sample_id']) is not int or type(row['unit_id']) is not int
                for row in mapping):
            raise ValidationError(_('Оберіть наявний виріб для кожного зразка.'))
        workorder = self._qc_identity_workorder()
        if not workorder and self._identity_production().workorder_ids:
            raise ValidationError(_('Уточніть робоче завдання QC перед прив’язкою виробів.'))
        with self.env.cr.savepoint():
            self._identify_samples('reuse', mapping, workorder=workorder, confirmed=True)
            return self.get_measurement_matrix_data()


def release_quantity_dependencies(model):
    dependencies = ['production_id', 'production_id.product_qty', 'production_id.product_uom_id']
    if 'workorder_id' in model._fields:
        dependencies += ['workorder_id', 'workorder_id.production_id.product_qty', 'workorder_id.production_id.product_uom_id']
    return dependencies


class QualityReleaseEvidenceCheck(models.Model):
    _inherit = 'quality.check'

    quality_release_mo_quantity_label = fields.Char(string='Кількість товару у виробничому замовленні', compute='_compute_release_mo_quantity_label')

    @api.depends(release_quantity_dependencies)
    def _compute_release_mo_quantity_label(self):
        for check in self:
            production = check._identity_production()
            check.quality_release_mo_quantity_label = check._format_operation_product_quantity(production.product_qty, production.product_uom_id) if production else False
