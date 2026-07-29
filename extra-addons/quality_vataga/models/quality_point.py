from odoo import fields, models


class QualityPoint(models.Model):
    _inherit = 'quality.point'

    control_parameter_line_ids = fields.One2many(
        'quality.control.parameter.line',
        'quality_point_id',
        string='Налаштування контролю',
    )
