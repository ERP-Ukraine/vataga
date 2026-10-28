from odoo import fields, models


class QualityMatrixFailureScopeWizard(models.TransientModel):
    _name = 'quality.matrix.failure.scope.wizard'
    _description = 'Уточнення зразків історичного Quality Alert'

    alert_id = fields.Many2one('quality.alert', required=True, readonly=True)
    check_id = fields.Many2one(related='alert_id.check_id', readonly=True)
    sample_ids = fields.Many2many('quality.check.sample', string='Проблемні зразки', required=True)
    reason = fields.Text(string='Підстава уточнення', required=True)

    def action_apply(self):
        self.ensure_one()
        self.alert_id._bind_matrix_failure_scope(self.sample_ids, self.reason or '')
        return {'type': 'ir.actions.act_window_close'}
