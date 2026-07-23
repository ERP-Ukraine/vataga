from odoo import api, fields, models


class MaintenanceEquipment(models.Model):
    _inherit = 'maintenance.equipment'

    _quality_vataga_equipment_number_field_types = {
        'char',
        'text',
        'integer',
    }

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

    @api.depends('name', 'serial_no', 'write_date')
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
        field_name = self._quality_vataga_get_inventory_field_name()
        if field_name:
            inventory_number = self[field_name]
            if inventory_number is not False and inventory_number is not None:
                inventory_number = str(inventory_number).strip()
                if inventory_number:
                    return inventory_number, 'Інвентарний номер'
        if self.serial_no:
            return self.serial_no, 'Серійний номер'
        return False, False

    def _quality_vataga_get_inventory_field_name(self):
        self.ensure_one()
        configured_field_name = (
            self.env['ir.config_parameter'].sudo().get_param(
                'quality_vataga.equipment_inventory_field',
            )
            or ''
        ).strip()
        if configured_field_name:
            return (
                configured_field_name
                if self._quality_vataga_is_inventory_number_field(
                    configured_field_name,
                )
                else False
            )

        candidates = [
            field_name
            for field_name, field in self._fields.items()
            if self._quality_vataga_is_inventory_number_field(field_name)
            and (
                'inventory' in field_name.lower()
                or 'inventory' in str(field.string or '').lower()
                or 'інвентар' in str(field.string or '').lower()
            )
        ]
        return candidates[0] if len(candidates) == 1 else False

    def _quality_vataga_is_inventory_number_field(self, field_name):
        self.ensure_one()
        inventory_field = self._fields.get(field_name)
        return bool(
            field_name
            and field_name != 'serial_no'
            and inventory_field
            and inventory_field.type
            in self._quality_vataga_equipment_number_field_types
        )
