from odoo import _, api, fields, models
from odoo.exceptions import UserError


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

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quarantine_points(self):
        if self.env['mrp.production'].sudo().search_count([
            ('quality_quarantine_point_ids', 'in', self.ids),
        ], limit=1):
            raise UserError(_('Не можна видаляти пункт контролю контрольованої партії.'))
