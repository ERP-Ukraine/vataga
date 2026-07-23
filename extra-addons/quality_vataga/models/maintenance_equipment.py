from odoo import api, fields, models


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    applicable_parameter_ids = fields.Many2many(
        'quality.equipment.parameter',
        compute='_compute_applicable_parameter_ids',
        string='Параметри',
        readonly=True,
    )

    @api.depends('category_id', 'category_id.applicable_parameter_ids')
    def _compute_applicable_parameter_ids(self):
        for equipment in self:
            equipment.applicable_parameter_ids = (
                equipment.category_id.applicable_parameter_ids
            )

    @api.depends('name', 'serial_no')
    @api.depends_context('quality_vataga_equipment_selection')
    def _compute_display_name(self):
        if not self.env.context.get('quality_vataga_equipment_selection'):
            return super()._compute_display_name()
        for equipment in self:
            equipment.display_name = (
                f'[{equipment.serial_no}] {equipment.name}'
                if equipment.serial_no
                else equipment.name
            )
