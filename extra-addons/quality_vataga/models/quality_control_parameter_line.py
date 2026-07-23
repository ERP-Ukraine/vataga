import math

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
    min_tolerance = fields.Float(string='Мінімальний допуск (технічний)')
    min_tolerance_input = fields.Char(
        string='Мінімальний допуск',
        compute='_compute_min_tolerance_input',
        inverse='_inverse_min_tolerance_input',
    )
    has_max_tolerance = fields.Boolean(string='Макс. допуск задано')
    max_tolerance = fields.Float(string='Максимальний допуск (технічний)')
    max_tolerance_input = fields.Char(
        string='Максимальний допуск',
        compute='_compute_max_tolerance_input',
        inverse='_inverse_max_tolerance_input',
    )
    text_norm = fields.Char(string='Текстова норма')

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for vals in vals_list:
            prepared_vals = dict(vals)
            self._synchronize_tolerance_flags(prepared_vals)
            prepared_vals_list.append(prepared_vals)
        return super().create(prepared_vals_list)

    def write(self, vals):
        prepared_vals = dict(vals)
        self._synchronize_tolerance_flags(prepared_vals)
        return super().write(prepared_vals)

    @api.model
    def _synchronize_tolerance_flags(self, vals):
        for prefix in ('min', 'max'):
            tolerance_field = f'{prefix}_tolerance'
            flag_field = f'has_{prefix}_tolerance'
            if tolerance_field in vals and flag_field not in vals:
                value = vals[tolerance_field]
                vals[flag_field] = (
                    value is not False
                    and value is not None
                    and value != ''
                )

    @api.depends(
        'equipment_category_id',
        'equipment_category_id.applicable_parameter_ids',
    )
    def _compute_available_parameter_ids(self):
        for line in self:
            line.available_parameter_ids = (
                line.equipment_category_id.applicable_parameter_ids
            )

    @api.depends('has_min_tolerance', 'min_tolerance')
    def _compute_min_tolerance_input(self):
        for line in self:
            line.min_tolerance_input = (
                self._format_tolerance(line.min_tolerance)
                if line.has_min_tolerance
                else False
            )

    @api.depends('has_max_tolerance', 'max_tolerance')
    def _compute_max_tolerance_input(self):
        for line in self:
            line.max_tolerance_input = (
                self._format_tolerance(line.max_tolerance)
                if line.has_max_tolerance
                else False
            )

    def _inverse_min_tolerance_input(self):
        for line in self:
            has_tolerance, tolerance = self._parse_tolerance_input(
                line.min_tolerance_input,
                _('мінімального допуску'),
            )
            line.write({
                'has_min_tolerance': has_tolerance,
                'min_tolerance': tolerance,
            })

    def _inverse_max_tolerance_input(self):
        for line in self:
            has_tolerance, tolerance = self._parse_tolerance_input(
                line.max_tolerance_input,
                _('максимального допуску'),
            )
            line.write({
                'has_max_tolerance': has_tolerance,
                'max_tolerance': tolerance,
            })

    @api.model
    def _format_tolerance(self, value):
        return format(value, '.15g')

    @api.model
    def _parse_tolerance_input(self, raw_value, field_label):
        value = (raw_value or '').strip()
        if not value:
            return False, 0.0

        normalized_value = (
            value
            .replace('\N{NO-BREAK SPACE}', '')
            .replace(' ', '')
            .replace(',', '.')
        )
        try:
            parsed_value = float(normalized_value)
        except (TypeError, ValueError) as error:
            raise ValidationError(_(
                'Значення %(field)s «%(value)s» не є коректним числом.',
                field=field_label,
                value=value,
            )) from error
        if not math.isfinite(parsed_value):
            raise ValidationError(_(
                'Значення %(field)s повинно бути скінченним числом.',
                field=field_label,
            ))
        return True, parsed_value

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
        'min_tolerance_input',
        'has_max_tolerance',
        'max_tolerance',
        'max_tolerance_input',
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
            if (
                line.has_min_tolerance
                and line.has_max_tolerance
                and line.min_tolerance > line.max_tolerance
            ):
                raise ValidationError(_(
                    'Мінімальний допуск не може бути більшим за максимальний.',
                ))
