from odoo import fields, models


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    visual_sample_control_required = fields.Boolean(
        string='Потрібен візуальний контроль зразків',
        default=False,
    )
    control_parameter_line_ids = fields.One2many(
        'quality.control.parameter.line',
        'quality_point_id',
        string='Налаштування контролю',
    )
