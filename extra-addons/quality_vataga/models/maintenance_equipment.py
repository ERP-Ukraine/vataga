from odoo import fields, models


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    parameter_ids = fields.Many2many(
        'quality.equipment.parameter',
        'quality_equipment_parameter_rel',
        'equipment_id',
        'parameter_id',
        string='Параметри обладнання',
    )
