from odoo import fields, models


class MaintenanceEquipmentCategory(models.Model):
    _inherit = 'maintenance.equipment.category'

    applicable_parameter_ids = fields.Many2many(
        'quality.equipment.parameter',
        'quality_equipment_category_parameter_rel',
        'category_id',
        'parameter_id',
        string='Застосовні параметри',
        domain=[('active', '=', True)],
    )
