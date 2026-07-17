from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class QualityControlParameterLine(models.Model):
    _name = 'quality.control.parameter.line'
    _description = 'Рядок налаштування контролю'
    _order = 'sequence, id'

    quality_point_id = fields.Many2one(
        'quality.point',
        string='Пункт контролю якості',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Послідовність', default=10)
    control_type = fields.Selection(
        selection=[
            ('instrumental', 'Інструментальний'),
            ('functional', 'Функціональний'),
        ],
        string='Вид контролю',
        required=True,
        default='instrumental',
    )
    equipment_category_id = fields.Many2one(
        'maintenance.equipment.category',
        string='Категорія ЗВТ',
        required=True,
    )
    available_parameter_ids = fields.Many2many(
        'quality.equipment.parameter',
        compute='_compute_available_parameter_ids',
        string='Доступні параметри',
    )
    parameter_id = fields.Many2one(
        'quality.equipment.parameter',
        string='Параметр',
        required=True,
        domain="[('id', 'in', available_parameter_ids)]",
    )
    has_min_tolerance = fields.Boolean(string='Мін. допуск задано')
    min_tolerance = fields.Float(string='Мінімальний допуск')
    has_max_tolerance = fields.Boolean(string='Макс. допуск задано')
    max_tolerance = fields.Float(string='Максимальний допуск')
    text_norm = fields.Char(string='Текстова норма')

    @api.depends(
        'equipment_category_id',
        'equipment_category_id.applicable_parameter_ids',
    )
    def _compute_available_parameter_ids(self):
        for line in self:
            line.available_parameter_ids = (
                line.equipment_category_id.applicable_parameter_ids
            )

    @api.onchange('equipment_category_id')
    def _onchange_equipment_category_id(self):
        for line in self:
            applicable_parameters = (
                line.equipment_category_id.applicable_parameter_ids
            )
            if (
                line.parameter_id
                and line.parameter_id not in applicable_parameters
            ):
                line.parameter_id = False

    @api.onchange('parameter_id')
    def _onchange_parameter_id(self):
        for line in self:
            if line.parameter_id.parameter_type in ('boolean', 'string'):
                line.has_min_tolerance = False
                line.min_tolerance = 0.0
                line.has_max_tolerance = False
                line.max_tolerance = 0.0

    @api.onchange('has_min_tolerance')
    def _onchange_has_min_tolerance(self):
        for line in self:
            if not line.has_min_tolerance:
                line.min_tolerance = 0.0

    @api.onchange('has_max_tolerance')
    def _onchange_has_max_tolerance(self):
        for line in self:
            if not line.has_max_tolerance:
                line.max_tolerance = 0.0

    @api.constrains('equipment_category_id', 'parameter_id')
    def _check_parameter_matches_category(self):
        for line in self:
            if not line.equipment_category_id or not line.parameter_id:
                continue
            applicable_parameters = line.equipment_category_id.with_context(
                active_test=False,
            ).applicable_parameter_ids
            if line.parameter_id not in applicable_parameters:
                raise ValidationError(_(
                    'Параметр «%(parameter)s» не належить до категорії ЗВТ '
                    '«%(category)s».',
                    parameter=line.parameter_id.display_name,
                    category=line.equipment_category_id.display_name,
                ))

    @api.constrains(
        'parameter_id',
        'has_min_tolerance',
        'min_tolerance',
        'has_max_tolerance',
        'max_tolerance',
    )
    def _check_tolerances(self):
        for line in self:
            if not line.parameter_id:
                continue
            if line.parameter_id.parameter_type != 'numeric':
                if (
                    line.has_min_tolerance
                    or line.has_max_tolerance
                    or line.min_tolerance
                    or line.max_tolerance
                ):
                    raise ValidationError(_(
                        'Числові допуски можна задавати лише для числових '
                        'параметрів.',
                    ))
                continue
            if not line.has_min_tolerance and line.min_tolerance:
                raise ValidationError(_(
                    'Щоб використовувати мінімальний допуск, позначте поле '
                    '«Мін. допуск задано».',
                ))
            if not line.has_max_tolerance and line.max_tolerance:
                raise ValidationError(_(
                    'Щоб використовувати максимальний допуск, позначте поле '
                    '«Макс. допуск задано».',
                ))
            if (
                line.has_min_tolerance
                and line.has_max_tolerance
                and line.min_tolerance > line.max_tolerance
            ):
                raise ValidationError(_(
                    'Мінімальний допуск не може бути більшим за максимальний.',
                ))
