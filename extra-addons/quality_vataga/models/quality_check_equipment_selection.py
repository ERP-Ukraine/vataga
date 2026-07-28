from odoo import _, api, Command, fields, models
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
    allowed_equipment_category_ids = fields.Many2many(
        'maintenance.equipment.category',
        relation='quality_check_equipment_selection_category_rel',
        column1='selection_id',
        column2='equipment_category_id',
        string='Допустимі категорії ЗВТ',
        readonly=True,
    )
    equipment_category_names_snapshot = fields.Char(
        string='Допустимі категорії ЗВТ (snapshot)',
        required=True,
        readonly=True,
    )
    category_set_key = fields.Char(
        string='Ключ набору категорій',
        required=True,
        readonly=True,
        index=True,
    )
    equipment_id = fields.Many2one(
        'maintenance.equipment',
        string='Конкретне обладнання (legacy)',
        ondelete='set null',
        domain="[('category_id', '=', equipment_category_id)]",
    )
    equipment_ids = fields.Many2many(
        'maintenance.equipment',
        relation='quality_check_equipment_selection_equipment_rel',
        column1='selection_id',
        column2='equipment_id',
        string='Конкретне обладнання',
    )
    equipment_name_snapshot = fields.Char(
        string='Назва обладнання (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_serial_snapshot = fields.Char(
        string='Серійний номер (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_inventory_snapshot = fields.Char(
        string='Номер обладнання (legacy snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_number_snapshot = fields.Char(
        string='Номер обладнання (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_number_label_snapshot = fields.Char(
        string='Тип номера обладнання (snapshot)',
        readonly=True,
        copy=False,
    )
    equipment_display_snapshot = fields.Char(
        string='Обладнання (snapshot)',
        compute='_compute_equipment_display_snapshot',
        store=True,
        readonly=True,
        copy=False,
    )
    equipment_display_list_snapshot = fields.Text(
        string='Використане обладнання (snapshot)',
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            'quality_check_category_set_uniq',
            'unique(quality_check_id, category_set_key)',
            'Для одного набору категорій ЗВТ у перевірці може бути лише '
            'один рядок.',
        ),
    ]

    @api.onchange('equipment_ids')
    def _onchange_equipment_ids(self):
        for selection in self:
            first_equipment = min(
                selection.equipment_ids,
                key=self._get_persisted_record_id,
                default=self.env['maintenance.equipment'],
            )
            selection.equipment_id = first_equipment

    @api.model
    def _get_persisted_record_id(self, record):
        origin_id = record._origin.id if record._origin else False
        if isinstance(origin_id, int):
            return origin_id

        record_id = record.id
        if isinstance(record_id, int):
            return record_id

        return 0

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.context.get(
            'quality_vataga_snapshot_initialization',
        ):
            raise UserError(_(
                'Категорії ЗВТ створюються системою з налаштувань QCP.',
            ))
        prepared_vals_list = [
            self._prepare_snapshot_create_values(vals)
            for vals in vals_list
        ]
        return super().create(prepared_vals_list)

    @api.model
    def _prepare_snapshot_create_values(self, vals):
        prepared_vals = dict(vals)
        category_ids = self._command_ids(
            prepared_vals.get('allowed_equipment_category_ids'),
        )
        if not category_ids and prepared_vals.get('equipment_category_id'):
            category_ids = [prepared_vals['equipment_category_id']]
            prepared_vals['allowed_equipment_category_ids'] = [
                Command.set(category_ids),
            ]
        categories = self.env[
            'maintenance.equipment.category'
        ].browse(category_ids).exists().sorted('id')
        if not categories:
            raise ValidationError(_(
                'Для рядка вибору ЗВТ потрібна хоча б одна допустима '
                'категорія.',
            ))
        category_ids = categories.ids
        prepared_vals['equipment_category_id'] = category_ids[0]
        prepared_vals['equipment_category_name'] = (
            categories[0].display_name
        )
        prepared_vals['equipment_category_names_snapshot'] = (
            ', '.join(categories.mapped('display_name'))
        )
        prepared_vals['category_set_key'] = (
            self._make_category_set_key(category_ids)
        )

        equipment_ids = self._command_ids(
            prepared_vals.get('equipment_ids'),
        )
        if not equipment_ids and prepared_vals.get('equipment_id'):
            equipment_ids = [prepared_vals['equipment_id']]
            prepared_vals['equipment_ids'] = [
                Command.set(equipment_ids),
            ]
        if equipment_ids:
            prepared_vals['equipment_id'] = min(equipment_ids)
        return prepared_vals

    @api.model
    def _command_ids(self, commands):
        ids = set()
        for command in commands or []:
            if isinstance(command, int):
                ids.add(command)
                continue
            operation = command[0]
            if operation == Command.SET:
                ids = set(command[2])
            elif operation == Command.LINK:
                ids.add(command[1])
            elif operation == Command.UNLINK:
                ids.discard(command[1])
            elif operation == Command.CLEAR:
                ids.clear()
        return sorted(ids)

    @api.model
    def _make_category_set_key(self, category_ids):
        return ','.join(
            str(category_id)
            for category_id in sorted(category_ids)
        )

    def _get_allowed_equipment_categories(self):
        self.ensure_one()
        return (
            self.allowed_equipment_category_ids
            or self.equipment_category_id
        )

    def _get_selected_equipment(self):
        self.ensure_one()
        return self.equipment_ids or self.equipment_id

    def _has_valid_equipment_selection(self):
        self.ensure_one()
        allowed_categories = self._get_allowed_equipment_categories()
        selected_equipment = self._get_selected_equipment()
        return bool(
            selected_equipment
            and allowed_categories
            and all(
                equipment.category_id in allowed_categories
                for equipment in selected_equipment
            )
        )

    @api.constrains(
        'allowed_equipment_category_ids',
        'equipment_category_id',
        'category_set_key',
    )
    def _check_category_set_structure(self):
        for selection in self:
            categories = (
                selection._get_allowed_equipment_categories().sorted('id')
            )
            expected_key = self._make_category_set_key(categories.ids)
            if (
                not categories
                or selection.equipment_category_id != categories[0]
                or selection.category_set_key != expected_key
            ):
                raise ValidationError(_(
                    'Набір допустимих категорій ЗВТ пошкоджено. '
                    'Повторно ініціалізуйте структуру перевірки.',
                ))

    @api.constrains(
        'allowed_equipment_category_ids',
        'equipment_category_id',
        'equipment_ids',
        'equipment_id',
    )
    def _check_equipment_matches_categories(self):
        for selection in self:
            allowed_categories = (
                selection._get_allowed_equipment_categories()
            )
            invalid_equipment = (
                selection._get_selected_equipment().filtered(
                    lambda equipment:
                        equipment.category_id not in allowed_categories,
                )
            )
            if invalid_equipment:
                equipment_names = ', '.join(
                    invalid_equipment.with_context(
                        quality_vataga_equipment_selection=True,
                    ).mapped('display_name'),
                )
                category_names = (
                    selection.equipment_category_names_snapshot
                    or ', '.join(
                        allowed_categories.mapped('display_name'),
                    )
                )
                raise ValidationError(_(
                    'Обладнання «%(equipment)s» не належить до допустимих '
                    'категорій ЗВТ: «%(categories)s».',
                    equipment=equipment_names,
                    categories=category_names,
                ))

    def _snapshot_selected_equipment(self):
        for selection in self:
            selected_equipment = (
                selection._get_selected_equipment().sorted('id')
            )
            if not selected_equipment:
                continue
            display_lines = []
            for equipment in selected_equipment:
                number, _number_label = (
                    equipment._quality_vataga_get_equipment_number()
                )
                display_lines.append(
                    f'[{number}] {equipment.name}'
                    if number
                    else equipment.name
                )
            first_equipment = selected_equipment[0]
            number, number_label = (
                first_equipment._quality_vataga_get_equipment_number()
            )
            selection._write_equipment_snapshot({
                'equipment_name_snapshot':
                    first_equipment.name,
                'equipment_number_snapshot': number,
                'equipment_number_label_snapshot': number_label,
                'equipment_display_list_snapshot':
                    '\n'.join(display_lines),
            })

    @api.depends(
        'equipment_name_snapshot',
        'equipment_number_snapshot',
        'equipment_number_label_snapshot',
        'equipment_serial_snapshot',
        'equipment_inventory_snapshot',
    )
    def _compute_equipment_display_snapshot(self):
        for selection in self:
            number = (
                selection.equipment_number_snapshot
                or selection.equipment_serial_snapshot
                or selection.equipment_inventory_snapshot
            )
            name = selection.equipment_name_snapshot or ''
            selection.equipment_display_snapshot = (
                f'[{number}] {name}'
                if number and name
                else name
            )

    def _write_equipment_snapshot(self, vals):
        allowed_snapshot_fields = {
            'equipment_name_snapshot',
            'equipment_serial_snapshot',
            'equipment_inventory_snapshot',
            'equipment_number_snapshot',
            'equipment_number_label_snapshot',
            'equipment_display_list_snapshot',
        }
        if set(vals) - allowed_snapshot_fields:
            raise UserError(_(
                'Внутрішній snapshot може змінювати лише підпис '
                'обладнання.',
            ))
        for check in self.mapped('quality_check_id'):
            check.check_access_rights('write')
            check.check_access_rule('write')
        return super(
            QualityCheckEquipmentSelection,
            self.sudo(),
        ).write(vals)

    def write(self, vals):
        if set(vals) - {'equipment_ids'}:
            raise UserError(_(
                'У перевірці можна змінювати лише конкретне обладнання.',
            ))
        for check in self.mapped('quality_check_id'):
            check.check_access_rights('write')
            check.check_access_rule('write')
        if (
            'equipment_ids' in vals
            and any(
                selection.quality_check_id.quality_state != 'none'
                for selection in self
            )
        ):
            raise UserError(_(
                'Не можна змінювати ЗВТ завершеної перевірки.',
            ))
        result = super().write(vals)
        if 'equipment_ids' in vals:
            for selection in self:
                first_equipment = selection.equipment_ids.sorted('id')[:1]
                super(
                    QualityCheckEquipmentSelection,
                    selection,
                ).write({
                    'equipment_id': first_equipment.id or False,
                })
        return result

    @api.model
    def _migrate_legacy_equipment_data(self):
        for selection in self.sudo().search([]):
            values = {}
            if (
                not selection.allowed_equipment_category_ids
                and selection.equipment_category_id
            ):
                values['allowed_equipment_category_ids'] = [
                    Command.set(selection.equipment_category_id.ids),
                ]
            categories = (
                selection.allowed_equipment_category_ids
                or selection.equipment_category_id
            ).sorted('id')
            if categories:
                if selection.equipment_category_id != categories[0]:
                    values['equipment_category_id'] = categories[0].id
                if not selection.equipment_category_names_snapshot:
                    values['equipment_category_names_snapshot'] = (
                        ', '.join(categories.mapped('display_name'))
                    )
                if not selection.category_set_key:
                    values['category_set_key'] = (
                        self._make_category_set_key(categories.ids)
                    )
            if not selection.equipment_ids and selection.equipment_id:
                values['equipment_ids'] = [
                    Command.set(selection.equipment_id.ids),
                ]
            elif selection.equipment_ids:
                first_equipment = selection.equipment_ids.sorted('id')[0]
                if selection.equipment_id != first_equipment:
                    values['equipment_id'] = first_equipment.id
            if (
                selection.quality_check_id.quality_state != 'none'
                and not selection.equipment_display_list_snapshot
                and selection.equipment_display_snapshot
            ):
                values['equipment_display_list_snapshot'] = (
                    selection.equipment_display_snapshot
                )
            if values:
                super(
                    QualityCheckEquipmentSelection,
                    selection,
                ).write(values)

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
