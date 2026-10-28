from odoo import api, fields, models
from ..models.quality_archive import RetiredDialog, archive_disabled
from ..models.quality_release_access import check_stock_release_roles


class QualityStockTimerBindingWizard(RetiredDialog, models.TransientModel):
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
        archive_disabled()
