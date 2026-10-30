"""Retired dialog schema retained for history; native MO completion replaces it."""

from odoo import fields, models
from ..models.quality_archive import RetiredDialog


class QualityFullReleaseWizard(RetiredDialog, models.TransientModel):
    _name = 'quality.full.release.wizard'
    _description = 'Підтвердження повного випуску QUNIT'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True)
    unit_ids = fields.Many2many('quality.production.unit', readonly=True)
    product_id = fields.Many2one(related='production_id.product_id')
    company_id = fields.Many2one(related='production_id.company_id')
    tracking = fields.Selection(related='production_id.product_id.tracking')
    lot_id = fields.Many2one('stock.lot', string='Партія готової продукції', check_company=True)
    request_key = fields.Char(required=True, readonly=True)
    confirmed = fields.Boolean(string='Підтверджую фактичний склад всієї партії та реальний складський випуск', default=False)

    def action_confirm(self):
        return self.action_apply()
