from odoo import fields, models


CONTROL_STAGES = [
    ('incoming', 'Вхідний контроль'),
    ('in_process', 'Міжопераційний контроль'),
    ('final', 'Фінальний контроль'),
]


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    x_control_stage = fields.Selection(
        CONTROL_STAGES, string='Етап контролю', default='incoming',
        required=True,
    )
    visual_sample_control_required = fields.Boolean(
        string='Потрібен візуальний контроль зразків',
        default=False,
    )
    control_parameter_line_ids = fields.One2many(
        'quality.control.parameter.line',
        'quality_point_id',
        string='Налаштування контролю',
    )
