from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .measurement_utils import format_number


class QualityCheckMeasurementColumn(models.Model):
    _name = 'quality.check.measurement.column'
    _description = 'Snapshot колонки матриці показників'
    _order = 'sequence, id'

    quality_check_id = fields.Many2one(
        'quality.check',
        string='Перевірка якості',
        required=True,
        ondelete='cascade',
        index=True,
    )
    source_line_id = fields.Many2one(
        'quality.control.parameter.line',
        string='Початкове налаштування',
        ondelete='set null',
        readonly=True,
    )
    sequence = fields.Integer(
        string='Послідовність',
        default=10,
        readonly=True,
    )
    control_type = fields.Selection(
        selection=[
            ('instrumental', 'Інструментальний'),
            ('functional', 'Функціональний'),
        ],
        string='Вид контролю',
        required=True,
        readonly=True,
    )
    equipment_category_id = fields.Many2one(
        'maintenance.equipment.category',
        string='Категорія ЗВТ',
        required=True,
        ondelete='restrict',
        readonly=True,
    )
    equipment_category_name = fields.Char(
        string='Назва категорії ЗВТ',
        required=True,
        readonly=True,
    )
    parameter_id = fields.Many2one(
        'quality.equipment.parameter',
        string='Параметр',
        ondelete='set null',
        readonly=True,
    )
    parameter_name = fields.Char(
        string='Назва параметра',
        required=True,
        readonly=True,
    )
    parameter_type = fields.Selection(
        selection=[
            ('numeric', 'Числовий'),
            ('boolean', 'Булевий'),
            ('string', 'Рядковий'),
        ],
        string='Тип параметра',
        required=True,
        readonly=True,
    )
    parameter_unit = fields.Char(
        string='Одиниця вимірювання',
        readonly=True,
    )
    has_min_tolerance = fields.Boolean(
        string='Мінімальний допуск задано',
        readonly=True,
    )
    min_tolerance = fields.Float(
        string='Мінімальний допуск',
        readonly=True,
    )
    has_max_tolerance = fields.Boolean(
        string='Максимальний допуск задано',
        readonly=True,
    )
    max_tolerance = fields.Float(
        string='Максимальний допуск',
        readonly=True,
    )
    text_norm = fields.Char(
        string='Текстова норма',
        readonly=True,
    )
    boolean_expected = fields.Selection(
        selection=[
            ('yes', 'Так'),
            ('no', 'Ні'),
        ],
        string='Очікуване булеве значення',
        readonly=True,
    )

    _sql_constraints = [
        (
            'quality_check_source_line_uniq',
            'unique(quality_check_id, source_line_id)',
            'Одне налаштування QCP не може створити дві колонки перевірки.',
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(
            'quality_vataga_snapshot_initialization',
        ):
            raise UserError(_(
                'Колонки матриці створюються системою з налаштувань QCP.',
            ))
        columns = super().create(vals_list)
        value_model = self.env['quality.check.measurement.value']
        values_to_create = []
        for column in columns:
            existing_sample_ids = column.quality_check_id.sample_ids.ids
            existing_values = value_model.search([
                ('sample_id', 'in', existing_sample_ids),
                ('column_id', '=', column.id),
            ])
            valued_sample_ids = set(existing_values.sample_id.ids)
            values_to_create.extend({
                'quality_check_id': column.quality_check_id.id,
                'sample_id': sample.id,
                'column_id': column.id,
            } for sample in column.quality_check_id.sample_ids
                if sample.id not in valued_sample_ids)
        if values_to_create:
            value_model.with_context(
                quality_vataga_matrix_initialization=True,
            ).create(values_to_create)
        return columns

    def write(self, vals):
        raise UserError(_(
            'Snapshot-колонки перевірки не можна змінювати.',
        ))

    def unlink(self):
        raise UserError(_(
            'Snapshot-колонки перевірки не можна видаляти.',
        ))

    def _get_tolerance_label(self):
        self.ensure_one()
        if self.parameter_type != 'numeric':
            return False
        if self.has_min_tolerance and self.has_max_tolerance:
            return '%s – %s' % (
                format_number(self.min_tolerance),
                format_number(self.max_tolerance),
            )
        if self.has_min_tolerance:
            return '≥ %s' % format_number(self.min_tolerance)
        if self.has_max_tolerance:
            return '≤ %s' % format_number(self.max_tolerance)
        return False
