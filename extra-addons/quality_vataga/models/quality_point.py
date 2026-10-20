from odoo import _, fields, models
from odoo.exceptions import UserError


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    x_control_stage = fields.Selection(
        selection=[
            ('incoming', 'Вхідний контроль'),
            ('in_process', 'Міжопераційний контроль'),
            ('final', 'Фінальний контроль'),
        ],
        string='Етап контролю',
        default='incoming',
        required=True,
    )

    def write(self, vals):
        if 'x_control_stage' in vals:
            changed = self.filtered(
                lambda point: point.x_control_stage != vals['x_control_stage'],
            )
            if changed and self.env['quality.check'].sudo().search_count([
                ('point_id', 'in', changed.ids),
            ], limit=1):
                raise UserError(_(
                    'Не можна змінити етап пункту, який вже має перевірки. '
                    'Створіть новий пункт контролю для іншого етапу.',
                ))
        return super().write(vals)

    visual_sample_control_required = fields.Boolean(
        string='Потрібен візуальний контроль зразків',
        default=False,
    )
    control_parameter_line_ids = fields.One2many(
        'quality.control.parameter.line',
        'quality_point_id',
        string='Налаштування контролю',
    )
