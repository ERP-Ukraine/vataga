from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .measurement_utils import format_number


class QualityCheckMeasurementValue(models.Model):
    _name = 'quality.check.measurement.value'
    _description = 'Значення комірки матриці показників'
    _order = 'sample_id, column_id'

    quality_check_id = fields.Many2one(
        'quality.check',
        string='Перевірка якості',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sample_id = fields.Many2one(
        'quality.check.sample',
        string='Зразок',
        required=True,
        ondelete='cascade',
        index=True,
    )
    column_id = fields.Many2one(
        'quality.check.measurement.column',
        string='Колонка',
        required=True,
        ondelete='cascade',
        index=True,
    )
    parameter_type = fields.Selection(
        related='column_id.parameter_type',
        string='Тип параметра',
        store=True,
        readonly=True,
    )
    has_numeric_value = fields.Boolean(
        string='Числове значення задано',
        default=False,
    )
    numeric_value = fields.Float(string='Числове значення')
    boolean_value = fields.Selection(
        selection=[
            ('yes', 'Так'),
            ('no', 'Ні'),
        ],
        string='Булеве значення',
    )
    string_value = fields.Char(string='Текстове значення')
    manual_result = fields.Selection(
        selection=[
            ('pass', 'PASS'),
            ('fail', 'FAIL'),
        ],
        string='Ручний результат',
    )
    result = fields.Selection(
        selection=[
            ('pass', 'PASS'),
            ('fail', 'FAIL'),
            ('pending', 'Не заповнено'),
        ],
        string='Результат',
        compute='_compute_result',
        store=True,
    )
    is_filled = fields.Boolean(
        string='Заповнено',
        compute='_compute_result',
        store=True,
    )
    failure_reason = fields.Char(
        string='Причина невідповідності',
        compute='_compute_result',
        store=True,
    )

    _sql_constraints = [
        (
            'sample_column_uniq',
            'unique(sample_id, column_id)',
            'Для одного зразка та колонки може існувати лише одна комірка.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(
            'quality_vataga_matrix_initialization',
        ):
            raise UserError(_(
                'Комірки матриці формуються системою та не можуть '
                'створюватися вручну.',
            ))
        return super().create(vals_list)

    @api.depends(
        'parameter_type',
        'has_numeric_value',
        'numeric_value',
        'boolean_value',
        'string_value',
        'manual_result',
        'column_id.has_min_tolerance',
        'column_id.min_tolerance',
        'column_id.has_max_tolerance',
        'column_id.max_tolerance',
        'column_id.boolean_expected',
    )
    def _compute_result(self):
        for value in self:
            value.is_filled = False
            value.result = 'pending'
            value.failure_reason = False

            if value.parameter_type == 'numeric':
                value._compute_numeric_result()
            elif value.parameter_type == 'boolean':
                value._compute_boolean_result()
            elif value.parameter_type == 'string':
                value._compute_string_result()

    def _compute_numeric_result(self):
        self.ensure_one()
        if not self.has_numeric_value:
            return
        self.is_filled = True
        if (
            self.column_id.has_min_tolerance
            and self.numeric_value < self.column_id.min_tolerance
        ):
            self.result = 'fail'
            self.failure_reason = _(
                'Значення %(value)s нижче мінімального допуску %(limit)s.',
                value=format_number(self.numeric_value),
                limit=format_number(self.column_id.min_tolerance),
            )
            return
        if (
            self.column_id.has_max_tolerance
            and self.numeric_value > self.column_id.max_tolerance
        ):
            self.result = 'fail'
            self.failure_reason = _(
                'Значення %(value)s вище максимального допуску %(limit)s.',
                value=format_number(self.numeric_value),
                limit=format_number(self.column_id.max_tolerance),
            )
            return
        self.result = 'pass'

    def _compute_boolean_result(self):
        self.ensure_one()
        if not self.boolean_value:
            return
        self.is_filled = True
        if not self.column_id.boolean_expected:
            self.failure_reason = _(
                'Текстову норму неможливо однозначно інтерпретувати як '
                'булеве значення.',
            )
            return
        self.result = (
            'pass'
            if self.boolean_value == self.column_id.boolean_expected
            else 'fail'
        )
        if self.result == 'fail':
            self.failure_reason = _(
                'Фактичне булеве значення не відповідає текстовій нормі.',
            )

    def _compute_string_result(self):
        self.ensure_one()
        if not (self.string_value or '').strip() or not self.manual_result:
            return
        self.is_filled = True
        self.result = self.manual_result
        if self.result == 'fail':
            self.failure_reason = _(
                'Інспектор позначив текстове значення як невідповідне.',
            )

    @api.constrains(
        'quality_check_id',
        'sample_id',
        'column_id',
        'parameter_type',
        'has_numeric_value',
        'boolean_value',
        'string_value',
        'manual_result',
    )
    def _check_value_consistency(self):
        for value in self:
            if (
                value.sample_id.quality_check_id != value.quality_check_id
                or value.column_id.quality_check_id != value.quality_check_id
            ):
                raise ValidationError(_(
                    'Зразок, колонка та значення повинні належати одній '
                    'перевірці якості.',
                ))
            if value.parameter_type != 'numeric' and value.has_numeric_value:
                raise ValidationError(_(
                    'Числове значення можна задавати лише для числової '
                    'колонки.',
                ))
            if value.parameter_type != 'boolean' and value.boolean_value:
                raise ValidationError(_(
                    'Булеве значення можна задавати лише для булевої колонки.',
                ))
            if value.parameter_type != 'string' and (
                value.string_value or value.manual_result
            ):
                raise ValidationError(_(
                    'Текст і ручний результат можна задавати лише для '
                    'рядкової колонки.',
                ))

    def write(self, vals):
        input_fields = {
            'has_numeric_value',
            'numeric_value',
            'boolean_value',
            'string_value',
            'manual_result',
        }
        structural_fields = {
            'quality_check_id',
            'sample_id',
            'column_id',
            'parameter_type',
        }
        if structural_fields & set(vals):
            raise UserError(_(
                'Не можна змінювати перевірку, зразок, колонку або тип '
                'параметра існуючої комірки.',
            ))
        if set(vals) - input_fields:
            raise UserError(_(
                'У комірці можна змінювати лише фактичне значення та '
                'ручний результат.',
            ))
        if (
            input_fields & set(vals)
            and any(
                value.quality_check_id.quality_state != 'none'
                for value in self
            )
        ):
            raise UserError(_(
                'Не можна змінювати показники завершеної перевірки.',
            ))
        if input_fields & set(vals):
            self.mapped(
                'quality_check_id',
            )._ensure_ready_for_inspection()
        return super().write(vals)

    def unlink(self):
        raise UserError(_(
            'Комірки матриці формуються системою та не можуть видалятися '
            'вручну.',
        ))
