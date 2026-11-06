from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError

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
    equipment_category_ids = fields.Many2many(
        'maintenance.equipment.category',
        relation='quality_check_measurement_column_category_rel',
        column1='measurement_column_id',
        column2='equipment_category_id',
        string='Допустимі категорії ЗВТ (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_category_names_snapshot = fields.Char(
        string='Допустимі категорії ЗВТ (snapshot)',
        readonly=True,
        copy=False,
    )
    category_set_key = fields.Char(
        string='Ключ набору категорій',
        readonly=True,
        copy=False,
        index=True,
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
        prepared_vals_list = [
            self._prepare_snapshot_create_values(vals)
            for vals in vals_list
        ]
        columns = super().create(prepared_vals_list)
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
            value_model.sudo().with_context(
                quality_vataga_matrix_initialization=True,
            ).create(values_to_create)
        return columns

    @api.model
    def _prepare_snapshot_create_values(self, vals):
        prepared_vals = dict(vals)
        category_ids = self._command_ids(
            prepared_vals.get('equipment_category_ids'),
        )
        if not category_ids and 'equipment_category_ids' not in prepared_vals:
            legacy_category_id = prepared_vals.get('equipment_category_id')
            if legacy_category_id:
                category_ids = [legacy_category_id]

        categories = self.env[
            'maintenance.equipment.category'
        ].browse(category_ids).exists().sorted('id')
        if not categories:
            raise ValidationError(_(
                'Для snapshot-колонки потрібна хоча б одна категорія ЗВТ.',
            ))

        prepared_vals.update({
            'equipment_category_id': categories[0].id,
            'equipment_category_name': categories[0].display_name,
            'equipment_category_ids': [
                Command.set(categories.ids),
            ],
            'equipment_category_names_snapshot': ', '.join(
                categories.mapped('display_name'),
            ),
            'category_set_key': self._make_category_set_key(categories.ids),
        })
        return prepared_vals

    @api.model
    def _command_ids(self, commands):
        ids = set()
        for command in commands or []:
            if isinstance(command, int):
                ids.add(command)
                continue
            operation = command[0]
            if operation == Command.SET:
                ids = set(command[2])
            elif operation == Command.LINK:
                ids.add(command[1])
            elif operation == Command.UNLINK:
                ids.discard(command[1])
            elif operation == Command.CLEAR:
                ids.clear()
        return sorted(ids)

    @api.model
    def _make_category_set_key(self, category_ids):
        return ','.join(
            str(category_id)
            for category_id in sorted(category_ids)
        )

    @api.constrains(
        'equipment_category_ids',
        'equipment_category_id',
        'equipment_category_name',
        'equipment_category_names_snapshot',
        'category_set_key',
    )
    def _check_category_set_structure(self):
        for column in self:
            categories = column.equipment_category_ids.sorted('id')
            expected_key = self._make_category_set_key(categories.ids)
            if (
                not categories
                or column.equipment_category_id != categories[0]
                or column.category_set_key != expected_key
                or not (column.equipment_category_name or '').strip()
                or not (
                    column.equipment_category_names_snapshot or ''
                ).strip()
            ):
                raise ValidationError(_(
                    'Набір категорій ЗВТ snapshot-колонки пошкоджено.',
                ))

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
