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
            number, _number_label = (
                equipment._quality_vataga_get_equipment_number()
            )
            equipment.display_name = (
                f'[{number}] {equipment.name}'
                if number
                else equipment.name
            )

    def _quality_vataga_get_equipment_number(self):
        self.ensure_one()
        field_name = self.env['ir.config_parameter'].sudo().get_param(
            'quality_vataga.equipment_inventory_field',
        )
        field_name = (field_name or '').strip()
        if field_name and field_name in self._fields:
            inventory_number = self[field_name]
            if (
                inventory_number is not False
                and inventory_number is not None
                and inventory_number != ''
            ):
                return str(inventory_number), 'Інвентарний номер'
        if self.serial_no:
            return self.serial_no, 'Серійний номер'
        return False, False
