from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .measurement_utils import (
    format_number,
    normalize_boolean_norm,
    parse_numeric_input,
)


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    equipment_selection_ids = fields.One2many(
        'quality.check.equipment.selection',
        'quality_check_id',
        string='Використані ЗВТ',
        copy=False,
    )
    measurement_column_ids = fields.One2many(
        'quality.check.measurement.column',
        'quality_check_id',
        string='Колонки матриці показників',
        copy=False,
    )
    sample_ids = fields.One2many(
        'quality.check.sample',
        'quality_check_id',
        string='Зразки',
        copy=False,
    )
    sample_count_to_add = fields.Integer(
        string='Кількість нових зразків',
        default=1,
        copy=False,
    )
    measurement_matrix_complete = fields.Boolean(
        string='Матрицю показників заповнено',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    measurement_matrix_has_failure = fields.Boolean(
        string='Матриця містить невідповідності',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    equipment_selection_complete = fields.Boolean(
        string='Усі ЗВТ вибрано',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    can_pass_measurement_check = fields.Boolean(
        string='Перевірку можна успішно завершити',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    measurement_matrix_data = fields.Json(
        string='Матриця показників',
        compute='_compute_measurement_matrix_data',
    )

    @api.model_create_multi
    def create(self, vals_list):
        checks = super().create(vals_list)
        checks._initialize_measurement_snapshot()
        return checks

    def write(self, vals):
        result = super().write(vals)
        if 'point_id' in vals:
            self.filtered(
                lambda check:
                    check.point_id and not check.measurement_column_ids
            )._initialize_measurement_snapshot()
        return result

    def _initialize_measurement_snapshot(self):
        column_model = self.env['quality.check.measurement.column']
        selection_model = self.env['quality.check.equipment.selection']
        for check in self:
            if not check.point_id or check.measurement_column_ids:
                continue
            source_lines = check.point_id.control_parameter_line_ids.sorted(
                key=lambda line: (line.sequence, line.id),
            )
            if not source_lines:
                continue

            column_values = []
            category_values = {}
            for line in source_lines:
                boolean_expected = False
                if line.parameter_type == 'boolean':
                    boolean_expected = normalize_boolean_norm(line.text_norm)
                    if not boolean_expected:
                        raise ValidationError(_(
                            'Неможливо створити матрицю: текстова норма '
                            'булевого параметра «%(parameter)s» не означає '
                            'однозначно «Так» або «Ні».',
                            parameter=line.parameter_id.display_name,
                        ))
                column_values.append({
                    'quality_check_id': check.id,
                    'source_line_id': line.id,
                    'sequence': line.sequence,
                    'control_type': line.control_type,
                    'equipment_category_id':
                        line.equipment_category_id.id,
                    'equipment_category_name':
                        line.equipment_category_id.display_name,
                    'parameter_id': line.parameter_id.id,
                    'parameter_name': line.parameter_id.name,
                    'parameter_type': line.parameter_type,
                    'parameter_unit': line.parameter_id.unit or False,
                    'has_min_tolerance': line.has_min_tolerance,
                    'min_tolerance': line.min_tolerance,
                    'has_max_tolerance': line.has_max_tolerance,
                    'max_tolerance': line.max_tolerance,
                    'text_norm': line.text_norm or False,
                    'boolean_expected': boolean_expected,
                })
                category_values.setdefault(
                    line.equipment_category_id.id,
                    {
                        'quality_check_id': check.id,
                        'sequence': line.sequence,
                        'equipment_category_id':
                            line.equipment_category_id.id,
                        'equipment_category_name':
                            line.equipment_category_id.display_name,
                    },
                )

            snapshot_context = {
                'quality_vataga_snapshot_initialization': True,
            }
            selection_model.with_context(**snapshot_context).create(
                list(category_values.values()),
            )
            column_model.with_context(**snapshot_context).create(
                column_values,
            )

    @api.depends(
        'measurement_column_ids',
        'sample_ids',
        'sample_ids.sample_result',
        'sample_ids.is_complete',
        'sample_ids.has_failure',
        'equipment_selection_ids',
        'equipment_selection_ids.equipment_id',
    )
    def _compute_measurement_matrix_state(self):
        for check in self:
            matrix_required = bool(check.measurement_column_ids)
            selections = check.equipment_selection_ids
            samples = check.sample_ids
            check.equipment_selection_complete = (
                not matrix_required
                or bool(selections)
                and all(
                    selection.equipment_id
                    and selection.equipment_id.category_id
                    == selection.equipment_category_id
                    for selection in selections
                )
            )
            check.measurement_matrix_complete = (
                not matrix_required
                or bool(samples)
                and all(samples.mapped('is_complete'))
            )
            check.measurement_matrix_has_failure = (
                matrix_required
                and any(samples.mapped('has_failure'))
            )
            check.can_pass_measurement_check = (
                not matrix_required
                or (
                    check.equipment_selection_complete
                    and check.measurement_matrix_complete
                    and not check.measurement_matrix_has_failure
                    and all(
                        result == 'pass'
                        for result in samples.mapped('sample_result')
                    )
                )
            )

    @api.depends(
        'measurement_column_ids',
        'sample_ids',
        'sample_ids.visual_result',
        'sample_ids.sample_result',
        'sample_ids.measurement_value_ids.result',
        'sample_ids.measurement_value_ids.numeric_value',
        'sample_ids.measurement_value_ids.boolean_value',
        'sample_ids.measurement_value_ids.string_value',
        'sample_ids.measurement_value_ids.manual_result',
    )
    def _compute_measurement_matrix_data(self):
        for check in self:
            check.measurement_matrix_data = {
                'quality_check_id': check.id,
            }

    def action_add_measurement_samples(self):
        self.ensure_one()
        if not self.measurement_column_ids:
            raise UserError(_(
                'Для цієї перевірки немає налаштованих колонок показників.',
            ))
        if self.quality_state != 'none':
            raise UserError(_(
                'Не можна додавати зразки до вже завершеної перевірки.',
            ))
        if self.sample_count_to_add < 1:
            raise ValidationError(_(
                'Кількість нових зразків повинна бути більшою за нуль.',
            ))

        # The row lock keeps sample numbering stable when two inspectors add
        # samples to the same check at the same time.
        self.env.cr.execute(
            'SELECT id FROM quality_check WHERE id = %s FOR UPDATE',
            [self.id],
        )
        last_sample = self.env['quality.check.sample'].search(
            [('quality_check_id', '=', self.id)],
            order='sample_number desc',
            limit=1,
        )
        first_number = (last_sample.sample_number or 0) + 1
        self.env['quality.check.sample'].create([
            {
                'quality_check_id': self.id,
                'sample_number': sample_number,
                'sequence': sample_number * 10,
            }
            for sample_number in range(
                first_number,
                first_number + self.sample_count_to_add,
            )
        ])
        self.sample_count_to_add = 1
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def get_measurement_matrix_data(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')

        values_by_key = {
            (value.sample_id.id, value.column_id.id): value
            for value in self.sample_ids.measurement_value_ids
        }
        columns = [{
            'id': column.id,
            'control_type': column.control_type,
            'control_type_label': dict(
                column._fields['control_type'].selection,
            ).get(column.control_type),
            'parameter_name': column.parameter_name,
            'parameter_type': column.parameter_type,
            'parameter_unit': column.parameter_unit or '',
            'tolerance_label': column._get_tolerance_label() or '',
            'text_norm': column.text_norm or '',
        } for column in self.measurement_column_ids]

        samples = []
        for sample in self.sample_ids:
            cells = []
            for column in self.measurement_column_ids:
                value = values_by_key.get((sample.id, column.id))
                if not value:
                    continue
                cells.append({
                    'id': value.id,
                    'column_id': column.id,
                    'parameter_type': value.parameter_type,
                    'numeric_input': (
                        format_number(value.numeric_value)
                        if value.has_numeric_value
                        else ''
                    ),
                    'boolean_value': value.boolean_value or '',
                    'string_value': value.string_value or '',
                    'manual_result': value.manual_result or '',
                    'result': value.result,
                    'failure_reason': value.failure_reason or '',
                })
            samples.append({
                'id': sample.id,
                'sample_number': sample.sample_number,
                'display_name': sample.display_name,
                'visual_result': sample.visual_result or '',
                'sample_result': sample.sample_result,
                'cells': cells,
            })

        return {
            'check_id': self.id,
            'editable': self.quality_state == 'none',
            'columns': columns,
            'samples': samples,
            'can_pass': self.can_pass_measurement_check,
            'has_failure': self.measurement_matrix_has_failure,
            'is_complete': self.measurement_matrix_complete,
        }

    def update_measurement_visual_result(self, sample_id, visual_result):
        self.ensure_one()
        self._ensure_measurement_editable()
        sample = self.env['quality.check.sample'].browse(sample_id).exists()
        if not sample or sample.quality_check_id != self:
            raise ValidationError(_('Зразок не належить цій перевірці.'))
        if visual_result not in ('yes', 'no', False, ''):
            raise ValidationError(_(
                'Візуальний контроль повинен мати значення «Так» або «Ні».',
            ))
        sample.write({'visual_result': visual_result or False})
        return self.get_measurement_matrix_data()

    def update_measurement_value(self, value_id, payload):
        self.ensure_one()
        self._ensure_measurement_editable()
        value = self.env[
            'quality.check.measurement.value'
        ].browse(value_id).exists()
        if not value or value.quality_check_id != self:
            raise ValidationError(_('Комірка не належить цій перевірці.'))

        payload = payload or {}
        if value.parameter_type == 'numeric':
            has_value, numeric_value = parse_numeric_input(
                payload.get('numeric_input'),
                _('Значення показника'),
            )
            values = {
                'has_numeric_value': has_value,
                'numeric_value': numeric_value,
                'boolean_value': False,
                'string_value': False,
                'manual_result': False,
            }
        elif value.parameter_type == 'boolean':
            boolean_value = payload.get('boolean_value') or False
            if boolean_value not in ('yes', 'no', False):
                raise ValidationError(_(
                    'Булеве значення повинно бути «Так» або «Ні».',
                ))
            values = {
                'has_numeric_value': False,
                'numeric_value': 0.0,
                'boolean_value': boolean_value,
                'string_value': False,
                'manual_result': False,
            }
        else:
            manual_result = payload.get('manual_result') or False
            if manual_result not in ('pass', 'fail', False):
                raise ValidationError(_(
                    'Ручний результат повинен бути PASS або FAIL.',
                ))
            values = {
                'has_numeric_value': False,
                'numeric_value': 0.0,
                'boolean_value': False,
                'string_value': payload.get('string_value') or False,
                'manual_result': manual_result,
            }
        value.write(values)
        return self.get_measurement_matrix_data()

    def _ensure_measurement_editable(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.quality_state != 'none':
            raise UserError(_(
                'Матрицю завершеної перевірки не можна редагувати.',
            ))

    def _validate_measurement_can_pass(self):
        for check in self.filtered('measurement_column_ids'):
            invalid_boolean_column = check.measurement_column_ids.filtered(
                lambda column:
                    column.parameter_type == 'boolean'
                    and not column.boolean_expected,
            )[:1]
            if invalid_boolean_column:
                raise ValidationError(_(
                    'Текстова норма параметра «%(parameter)s» не означає '
                    'однозначно «Так» або «Ні». Виправте QCP; вже створена '
                    'перевірка зберігає початковий snapshot.',
                    parameter=invalid_boolean_column.parameter_name,
                ))
            missing_equipment = check.equipment_selection_ids.filtered(
                lambda selection:
                    not selection.equipment_id
                    or selection.equipment_id.category_id
                    != selection.equipment_category_id,
            )[:1]
            if missing_equipment:
                raise ValidationError(_(
                    'Оберіть конкретний прилад для категорії '
                    '«%(category)s».',
                    category=missing_equipment.equipment_category_name,
                ))
            if not check.sample_ids:
                raise ValidationError(_(
                    'Додайте щонайменше один зразок для перевірки.',
                ))
            if check.measurement_matrix_has_failure:
                raise ValidationError(_(
                    'Перевірка містить значення поза допустимими межами або '
                    'негативний результат. Доступний лише результат '
                    '«Невдало».',
                ))
            if not check.measurement_matrix_complete:
                raise ValidationError(_(
                    'Заповніть візуальний контроль і всі комірки матриці '
                    'показників.',
                ))

    def _snapshot_selected_equipment(self):
        self.mapped(
            'equipment_selection_ids',
        )._snapshot_selected_equipment()

    def do_pass(self):
        self._validate_measurement_can_pass()
        self._snapshot_selected_equipment()
        return super().do_pass()

    def do_fail(self):
        self._snapshot_selected_equipment()
        return super().do_fail()
