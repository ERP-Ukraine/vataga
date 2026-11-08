from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .measurement_utils import format_number

# An RPC context cannot provide this server-side token.
AUTOMATIC_SAMPLE_INITIALIZATION = object()


class QualityCheckSample(models.Model):
    _name = 'quality.check.sample'
    _description = 'Зразок матриці показників'
    _order = 'sequence, sample_number, id'

    quality_check_id = fields.Many2one(
        'quality.check',
        string='Перевірка якості',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Послідовність', default=10)
    sample_number = fields.Integer(string='Номер зразка', required=True)
    x_was_reworked = fields.Boolean(string='Доопрацьовано', readonly=True, copy=False)
    initial_failures = fields.Json(readonly=True, copy=False)
    rework_pending = fields.Boolean(readonly=True, copy=False)
    visual_result = fields.Selection(
        selection=[
            ('yes', 'Так'),
            ('no', 'Ні'),
        ],
        string='Візуальний контроль',
    )
    measurement_value_ids = fields.One2many(
        'quality.check.measurement.value',
        'sample_id',
        string='Значення показників',
        copy=False,
    )
    sample_result = fields.Selection(
        selection=[
            ('pass', 'PASS'),
            ('fail', 'FAIL'),
            ('pending', 'Не заповнено'),
        ],
        string='Висновок по зразку',
        compute='_compute_sample_state',
        store=True,
    )
    is_complete = fields.Boolean(
        string='Зразок заповнено',
        compute='_compute_sample_state',
        store=True,
    )
    has_failure = fields.Boolean(
        string='Зразок має невідповідність',
        compute='_compute_sample_state',
        store=True,
    )

    _sql_constraints = [
        (
            'quality_check_sample_number_uniq',
            'unique(quality_check_id, sample_number)',
            'Номер зразка повинен бути унікальним у межах перевірки.',
        ),
        (
            'positive_sample_number',
            'check(sample_number > 0)',
            'Номер зразка повинен бути більшим за нуль.',
        ),
    ]

    @api.depends(
        'visual_result',
        'quality_check_id.measurement_matrix_required',
        'quality_check_id.measurement_column_ids',
        'measurement_value_ids',
        'measurement_value_ids.column_id',
        'measurement_value_ids.quality_check_id',
        'measurement_value_ids.result',
        'measurement_value_ids.is_filled',
    )
    def _compute_sample_state(self):
        for sample in self:
            values = sample.measurement_value_ids
            expected_column_ids = set(
                sample.quality_check_id.measurement_column_ids.ids,
            )
            actual_column_ids = values.mapped('column_id').ids
            structure_complete = bool(
                sample.quality_check_id.measurement_matrix_required
                and len(values) == len(expected_column_ids)
                and set(actual_column_ids) == expected_column_ids
                and all(
                    value.quality_check_id == sample.quality_check_id
                    and value.column_id.quality_check_id
                    == sample.quality_check_id
                    for value in values
                )
            )
            sample.has_failure = (
                sample.visual_result == 'no'
                or any(value.result == 'fail' for value in values)
            )
            sample.is_complete = bool(
                sample.visual_result
                and structure_complete
                and all(value.is_filled for value in values)
            )
            if not structure_complete:
                sample.sample_result = 'pending'
            elif sample.has_failure:
                sample.sample_result = 'fail'
            elif not sample.is_complete:
                sample.sample_result = 'pending'
            else:
                sample.sample_result = 'pass'

    @api.depends('sample_number')
    def _compute_display_name(self):
        for sample in self:
            sample.display_name = (
                f'Зразок №{sample.sample_number}'
                if sample.sample_number
                else 'Новий зразок'
            )

    @api.model_create_multi
    def create(self, vals_list):
        initialization = self.env.context.get(
            'quality_vataga_sample_initialization',
        )
        if not initialization:
            raise UserError(_(
                'Зразки матриці створюються лише дією «Додати зразки».',
            ))
        default_check_id = False
        if any('quality_check_id' not in vals for vals in vals_list):
            default_check_id = self.default_get(['quality_check_id']).get(
                'quality_check_id',
            )
        check_ids = {
            vals.get('quality_check_id', default_check_id)
            for vals in vals_list
        }
        checks = self.env['quality.check'].browse(check_ids - {False, None})
        if initialization is not AUTOMATIC_SAMPLE_INITIALIZATION:
            for check in checks:
                check._ensure_manual_measurement_samples()
        if any(
            check.quality_state != 'none'
            for check in checks
        ):
            raise UserError(_(
                'Не можна додавати зразки до завершеної перевірки.',
            ))
        samples = super().create([
            dict(vals, x_was_reworked=False, initial_failures=False, rework_pending=False)
            for vals in vals_list
        ])
        values_to_create = []
        for sample in samples:
            values_to_create.extend({
                'quality_check_id': sample.quality_check_id.id,
                'sample_id': sample.id,
                'column_id': column.id,
            } for column in sample.quality_check_id.measurement_column_ids)
        if values_to_create:
            self.env[
                'quality.check.measurement.value'
            ].sudo().with_context(
                quality_vataga_matrix_initialization=True,
            ).create(values_to_create)
        return samples

    def write(self, vals):
        if set(vals) - {'visual_result'}:
            raise UserError(_(
                'У зразку можна змінювати лише результат візуального '
                'контролю.',
            ))
        previous = self._before_measurement_change()
        if any(
            sample.quality_check_id.quality_state != 'none'
            for sample in self
        ):
            raise UserError(_(
                'Не можна змінювати зразки завершеної перевірки.',
            ))
        result = super().write(vals)
        self._after_measurement_change(previous)
        return result

    def _measurement_snapshot(self):
        self.ensure_one()
        snapshot = {
            'visual': {
                'name': _('візуальний контроль'), 'value': self.visual_result or '',
                'display': {'yes': _('Так'), 'no': _('Ні')}.get(self.visual_result, ''),
                'reason': _('Візуальний контроль не пройдено.') if self.visual_result == 'no' else '',
            },
        }
        for cell in self.measurement_value_ids:
            if cell.parameter_type == 'numeric':
                value = cell.numeric_value if cell.has_numeric_value else None
                display = (
                    f'{format_number(value)} {cell.column_id.parameter_unit or ""}'.strip()
                    if value is not None else ''
                )
            elif cell.parameter_type == 'boolean':
                value = cell.boolean_value or ''
                display = {'yes': _('Так'), 'no': _('Ні')}.get(value, '')
            else:
                value = [cell.string_value or '', cell.manual_result or '']
                display = ' / '.join(part for part in value if part)
            snapshot[str(cell.column_id.id)] = {
                'name': cell.column_id.parameter_name, 'value': value, 'display': display,
                'reason': (cell.failure_reason or _('Параметр не відповідає нормі.'))
                if cell.result == 'fail' else '',
            }
        return snapshot

    def _before_measurement_change(self):
        samples = self.filtered(lambda sample: sample.quality_check_id.x_control_stage == 'in_process')
        if not samples:
            return {}
        samples.quality_check_id.sudo(False)._lock_measurement_checks()
        samples.invalidate_recordset()
        samples.measurement_value_ids.invalidate_recordset()
        return {
            sample.id: (sample.sample_result, sample._measurement_snapshot())
            for sample in samples if sample.quality_check_id.quality_state == 'none'
        }

    def _after_measurement_change(self, previous):
        for sample in self.filtered(lambda row: row.id in previous):
            old_result, old = previous[sample.id]
            current = sample._measurement_snapshot()
            changed = [key for key in old if old[key]['value'] != current[key]['value']]
            if not changed:
                continue
            failures = dict(sample.initial_failures or {})
            for snapshot in (old, current):
                for key, detail in snapshot.items():
                    if detail['reason']:
                        failures.setdefault(key, detail)
            rework = sample.rework_pending or 'fail' in (old_result, sample.sample_result)
            repaired = rework and sample.sample_result == 'pass'
            pending = rework and not repaired
            if (
                failures != (sample.initial_failures or {}) or pending != sample.rework_pending
                or (repaired and not sample.x_was_reworked)
            ):
                super(QualityCheckSample, sample).write({
                    'initial_failures': failures,
                    'x_was_reworked': sample.x_was_reworked or repaired,
                    'rework_pending': pending,
                })
            if old_result == 'fail' or repaired:
                heading = (
                    _('Статус зразка №%(number)s змінено з FAIL на PASS після доопрацювання.',
                      number=sample.sample_number)
                    if repaired else _('Виправлення показників зразка №%(number)s.', number=sample.sample_number)
                )
                edits = [
                    _('%(name)s: «%(before)s» → «%(after)s». %(reason)s',
                      name=old[key]['name'], before=old[key]['display'], after=current[key]['display'],
                      reason=old[key]['reason'])
                    for key in changed
                ]
                sample.quality_check_id.sudo(False).message_post(
                    body='\n'.join([heading, *edits]), subtype_xmlid='mail.mt_note',
                )

    def _has_entered_results(self):
        self.ensure_one()
        return bool(
            self.visual_result
            or any(
                value.has_numeric_value
                or value.boolean_value
                or value.string_value
                or value.manual_result
                for value in self.measurement_value_ids
            )
        )

    def unlink(self):
        for check in self.quality_check_id:
            check._ensure_manual_measurement_samples()
        if len(self) != 1:
            raise UserError(_(
                'Можна видалити лише один останній порожній зразок.',
            ))
        if self.quality_check_id.quality_state != 'none':
            raise UserError(_(
                'Не можна видаляти зразки завершеної перевірки.',
            ))
        if self._has_entered_results():
            raise UserError(_(
                'Не можна видалити зразок №%(number)s, оскільки в ньому '
                'вже є введені результати.',
                number=self.sample_number,
            ))
        last_sample = self.env['quality.check.sample'].search(
            [('quality_check_id', '=', self.quality_check_id.id)],
            order='sample_number desc, id desc',
            limit=1,
        )
        if self != last_sample:
            raise UserError(_(
                'Можна видалити лише останній порожній зразок перевірки.',
            ))
        return super().unlink()
