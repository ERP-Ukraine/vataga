from odoo import fields, models


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    applicable_parameter_ids = fields.Many2many(
        related='category_id.applicable_parameter_ids',
        string='Параметри',
        readonly=True,
    )
