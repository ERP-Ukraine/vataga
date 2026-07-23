from odoo import _, api, fields, models
from odoo.exceptions import UserError


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
        'measurement_value_ids',
        'measurement_value_ids.result',
        'measurement_value_ids.is_filled',
    )
    def _compute_sample_state(self):
        for sample in self:
            values = sample.measurement_value_ids
            sample.has_failure = (
                sample.visual_result == 'no'
                or any(value.result == 'fail' for value in values)
            )
            sample.is_complete = bool(
                sample.visual_result
                and values
                and all(value.is_filled for value in values)
            )
            if sample.has_failure:
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
        check_ids = {
            vals.get('quality_check_id')
            for vals in vals_list
            if vals.get('quality_check_id')
        }
        if any(
            check.quality_state != 'none'
            for check in self.env['quality.check'].browse(check_ids)
        ):
            raise UserError(_(
                'Не можна додавати зразки до завершеної перевірки.',
            ))
        samples = super().create(vals_list)
        values_to_create = []
        for sample in samples:
            values_to_create.extend({
                'quality_check_id': sample.quality_check_id.id,
                'sample_id': sample.id,
                'column_id': column.id,
            } for column in sample.quality_check_id.measurement_column_ids)
        if values_to_create:
            self.env['quality.check.measurement.value'].create(
                values_to_create,
            )
        return samples

    def write(self, vals):
        if (
            {'visual_result', 'sample_number', 'sequence'} & set(vals)
            and any(
                sample.quality_check_id.quality_state != 'none'
                for sample in self
            )
        ):
            raise UserError(_(
                'Не можна змінювати зразки завершеної перевірки.',
            ))
        return super().write(vals)

    def unlink(self):
        if any(
            sample.quality_check_id.quality_state != 'none'
            for sample in self
        ):
            raise UserError(_(
                'Не можна видаляти зразки завершеної перевірки.',
            ))
        return super().unlink()
