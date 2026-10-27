from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from ..models.quality_release_access import check_stock_release_roles


class QualityStockTimerBindingWizard(models.TransientModel):
    _name = 'quality.stock.timer.binding.wizard'
    _description = 'Явне підтвердження QUNIT спільного робочого часу'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True)
    timer_id = fields.Many2one('mrp.workcenter.productivity', string='Закритий спільний інтервал', required=True)
    unit_ids = fields.Many2many('quality.production.unit', string='Фактичні учасники інтервалу', required=True)
    reason = fields.Text(string='Підстава підтвердження', required=True)

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)

    def action_apply(self):
        self.ensure_one()
        self.production_id._check_unit_stock_release_access()
        if self.timer_id.workorder_id.production_id != self.production_id:
            raise ValidationError(_('Інтервал має належати вибраному MO.'))
        self.env['quality.stock.timer.binding']._bind_shared_timer(self.timer_id, self.unit_ids, self.reason or '')
        return {'type': 'ir.actions.act_window_close'}
