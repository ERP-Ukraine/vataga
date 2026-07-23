from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class QualityCheckEquipmentSelection(models.Model):
    _name = 'quality.check.equipment.selection'
    _description = 'Вибір ЗВТ для перевірки якості'
    _order = 'sequence, id'

    quality_check_id = fields.Many2one(
        'quality.check',
        string='Перевірка якості',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sequence = fields.Integer(string='Послідовність', default=10)
    equipment_category_id = fields.Many2one(
        'maintenance.equipment.category',
        string='Категорія ЗВТ',
        required=True,
        ondelete='restrict',
    )
    equipment_category_name = fields.Char(
        string='Назва категорії ЗВТ (snapshot)',
        required=True,
        readonly=True,
    )
    equipment_id = fields.Many2one(
        'maintenance.equipment',
        string='Конкретне обладнання',
        ondelete='set null',
        domain="[('category_id', '=', equipment_category_id)]",
    )
    equipment_name_snapshot = fields.Char(
        string='Назва обладнання (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_inventory_snapshot = fields.Char(
        string='Інвентарний номер (snapshot)',
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            'quality_check_category_uniq',
            'unique(quality_check_id, equipment_category_id)',
            'Для однієї категорії ЗВТ у перевірці може бути лише один рядок.',
        ),
    ]

    @api.onchange('equipment_category_id')
    def _onchange_equipment_category_id(self):
        for selection in self:
            if (
                selection.equipment_id
                and selection.equipment_id.category_id
                != selection.equipment_category_id
            ):
                selection.equipment_id = False

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(
            'quality_vataga_snapshot_initialization',
        ):
            raise UserError(_(
                'Категорії ЗВТ створюються системою з налаштувань QCP.',
            ))
        return super().create(vals_list)

    @api.constrains('equipment_category_id', 'equipment_id')
    def _check_equipment_matches_category(self):
        for selection in self:
            if (
                selection.equipment_id
                and selection.equipment_id.category_id
                != selection.equipment_category_id
            ):
                raise ValidationError(_(
                    'Обладнання «%(equipment)s» не належить до категорії '
                    'ЗВТ «%(category)s».',
                    equipment=selection.equipment_id.display_name,
                    category=selection.equipment_category_name
                    or selection.equipment_category_id.display_name,
                ))

    def _snapshot_selected_equipment(self):
        for selection in self.filtered('equipment_id'):
            selection.with_context(
                quality_vataga_equipment_snapshot=True,
            ).write({
                'equipment_name_snapshot': selection.equipment_id.name,
                'equipment_inventory_snapshot':
                    selection.equipment_id.serial_no or False,
            })

    def write(self, vals):
        if self.env.context.get('quality_vataga_equipment_snapshot'):
            return super().write(vals)
        if set(vals) - {'equipment_id'}:
            raise UserError(_(
                'У перевірці можна змінювати лише конкретне обладнання.',
            ))
        if (
            'equipment_id' in vals
            and any(
                selection.quality_check_id.quality_state != 'none'
                for selection in self
            )
        ):
            raise UserError(_(
                'Не можна змінювати ЗВТ завершеної перевірки.',
            ))
        return super().write(vals)

    def unlink(self):
        if any(
            selection.quality_check_id.quality_state != 'none'
            for selection in self
        ):
            raise UserError(_(
                'Не можна видаляти ЗВТ завершеної перевірки.',
            ))
        raise UserError(_(
            'Категорії ЗВТ формуються системою та не видаляються вручну.',
        ))
