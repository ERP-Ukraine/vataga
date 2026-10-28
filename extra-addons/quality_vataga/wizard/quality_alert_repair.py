from odoo import fields, models


class QualityAlertRepairWizard(models.TransientModel):
    _name = 'quality.alert.repair.wizard'
    _description = 'Підтвердження виконання ремонту за рішенням ТР'

    alert_id = fields.Many2one('quality.alert', required=True, readonly=True)
    sample_ids = fields.Many2many(
        related='alert_id.vataga_failure_sample_ids', readonly=True, compute_sudo=False)
    note = fields.Text(string='Фактично виконані роботи', required=True)

    def action_apply(self):
        self.ensure_one()
        self.alert_id.action_record_repair(self.note)
        return {'type': 'ir.actions.act_window_close'}
