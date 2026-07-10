import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class QualityEquipmentParameter(models.Model):
    _name = 'quality.equipment.parameter'
    _description = 'Параметр обладнання'
    _order = 'sequence, name, id'

    name = fields.Char(string='Назва', required=True)
    parameter_type = fields.Selection(
        selection=[
            ('numeric', 'Числовий'),
            ('boolean', 'Булевий'),
            ('string', 'Рядковий'),
        ],
        string='Тип',
        required=True,
        default='numeric',
    )
    unit = fields.Char(string='Одиниця вимірювання')
    active = fields.Boolean(string='Активний', default=True)
    sequence = fields.Integer(string='Послідовність', default=10)
    description = fields.Text(string='Опис')
    normalized_name = fields.Char(
        string='Нормалізована назва',
        compute='_compute_normalized_name',
        store=True,
        index=True,
    )

    _sql_constraints = [
        (
            'normalized_name_uniq',
            'unique(normalized_name)',
            'Параметр обладнання з такою назвою вже існує. Перевірте зайві пробіли в назві.',
        ),
    ]

    @api.depends('name')
    def _compute_normalized_name(self):
        for parameter in self:
            parameter.normalized_name = self._normalize_name(parameter.name)

    @api.constrains('name')
    def _check_normalized_name(self):
        for parameter in self:
            if not self._normalize_name(parameter.name):
                raise ValidationError(_('Назва параметра не може складатися лише з пробілів.'))

    @api.model
    def _normalize_name(self, name):
        return re.sub(r'\s+', ' ', (name or '').strip())
