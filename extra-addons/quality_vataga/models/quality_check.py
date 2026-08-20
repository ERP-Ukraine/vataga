from decimal import Decimal

from odoo import _, api, Command, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import formatLang, get_lang

from .measurement_utils import (
    format_number,
    normalize_boolean_norm,
    parse_numeric_input,
)


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    equipment_selection_ids = fields.One2many(
        'quality.check.equipment.selection',
        'quality_check_id',
        string='Використані ЗВТ',
        copy=False,
        domain=[('requires_equipment_selection', '=', True)],
    )
    measurement_column_ids = fields.One2many(
        'quality.check.measurement.column',
        'quality_check_id',
        string='Колонки матриці показників',
        copy=False,
    )
    sample_ids = fields.One2many(
        'quality.check.sample',
        'quality_check_id',
        string='Зразки',
        copy=False,
    )
    sample_count_to_add = fields.Integer(
        string='Кількість нових зразків',
        default=1,
        copy=False,
    )
    measurement_matrix_required = fields.Boolean(
        string='Потрібна матриця показників',
        default=False,
        readonly=True,
        copy=False,
    )
    measurement_matrix_complete = fields.Boolean(
        string='Матрицю показників заповнено',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    measurement_matrix_has_failure = fields.Boolean(
        string='Матриця містить невідповідності',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    equipment_selection_complete = fields.Boolean(
        string='Усі ЗВТ вибрано',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    can_pass_measurement_check = fields.Boolean(
        string='Перевірку можна успішно завершити',
        compute='_compute_measurement_matrix_state',
        store=True,
    )
    can_initialize_measurement_matrix = fields.Boolean(
        string='Матрицю можна ініціалізувати',
        compute='_compute_can_initialize_measurement_matrix',
    )
    measurement_matrix_data = fields.Json(
        string='Матриця показників',
        compute='_compute_measurement_matrix_data',
    )
    operation_product_quantity = fields.Float(
        string='Кількість товару',
        compute='_compute_operation_product_quantity',
        digits='Product Unit of Measure',
        readonly=True,
    )
    operation_product_uom_id = fields.Many2one(
        'uom.uom',
        string='Одиниця вимірювання кількості товару',
        compute='_compute_operation_product_quantity',
        readonly=True,
    )
    operation_product_quantity_label = fields.Char(
        string='Кількість товару',
        compute='_compute_operation_product_quantity',
        readonly=True,
    )
    has_operation_product_quantity = fields.Boolean(
        compute='_compute_operation_product_quantity',
    )
    arrival_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Склад',
        related='picking_id.picking_type_id.warehouse_id',
        store=True,
        readonly=True,
        index=True,
    )
    arrival_scheduled_date = fields.Datetime(
        string='Запланована дата надходження',
        related='picking_id.scheduled_date',
        store=True,
        readonly=True,
        index=True,
    )

    @api.depends(
        'product_id',
        'product_id.uom_id',
        'uom_id',
        'qty_line',
        'move_line_id',
        'move_line_id.product_id',
        'move_line_id.product_uom_id',
        'move_line_id.quantity',
        'picking_id',
        'picking_id.move_ids.product_id',
        'picking_id.move_ids.product_uom',
        'picking_id.move_ids.product_uom_qty',
        'picking_id.move_ids.quantity',
        'picking_id.move_ids.state',
    )
    def _compute_operation_product_quantity(self):
        for check in self:
            check.operation_product_quantity = 0.0
            check.operation_product_uom_id = False
            check.operation_product_quantity_label = False
            check.has_operation_product_quantity = False

            quantity_source = check._get_operation_product_quantity_source()
            if not quantity_source:
                continue

            quantity, source_uom, display_uom = quantity_source
            if source_uom != display_uom:
                quantity = source_uom._compute_quantity(
                    quantity,
                    display_uom,
                    round=False,
                )

            check.operation_product_quantity = quantity
            check.operation_product_uom_id = display_uom
            check.operation_product_quantity_label = (
                check._format_operation_product_quantity(
                    quantity,
                    display_uom,
                )
            )
            check.has_operation_product_quantity = True

    def _get_operation_product_quantity_source(self):
        """Return a reliable quantity source for this particular check.

        A product-level quality check has no direct ``stock.move`` link in
        Odoo 17.  In that case a move is reliable only when the current
        picking contains exactly one non-cancelled move for the checked
        product.  Looking only inside ``picking_id`` also keeps backorders
        isolated from their parent transfer.
        """
        self.ensure_one()
        if not self.product_id:
            return False

        display_uom = self.uom_id or self.product_id.uom_id
        move_line = self.move_line_id
        if move_line:
            if move_line.product_id != self.product_id:
                return False
            source_uom = move_line.product_uom_id
            return self._prepare_operation_quantity_source(
                move_line.quantity,
                source_uom,
                display_uom,
            )

        # quality_mrp computes the standard qty_line from
        # production_id.qty_producing.  Work-order checks carry the same
        # production_id, so depending on qty_line keeps this extension
        # compatible without making optional MRP modules hard dependencies.
        if 'production_id' in self._fields and self.production_id:
            return self._prepare_operation_quantity_source(
                self.qty_line,
                display_uom,
                display_uom,
            )

        if not self.picking_id:
            return False
        moves = self.picking_id.move_ids.filtered(
            lambda move: (
                move.product_id == self.product_id
                and move.state != 'cancel'
            ),
        )
        if len(moves) != 1:
            return False

        move = moves[0]
        quantity = move.quantity
        if not quantity and move.state != 'done':
            quantity = move.product_uom_qty
        return self._prepare_operation_quantity_source(
            quantity,
            move.product_uom,
            display_uom,
        )

    def _prepare_operation_quantity_source(
        self,
        quantity,
        source_uom,
        display_uom,
    ):
        self.ensure_one()
        if not source_uom or not display_uom:
            return False
        if source_uom.category_id != display_uom.category_id:
            # Do not fail an existing quality check because of inconsistent
            # historical UoM data.  The source UoM remains truthful.
            display_uom = source_uom
        return quantity, source_uom, display_uom

    def _format_operation_product_quantity(self, quantity, uom):
        self.ensure_one()
        rounding = Decimal(str(uom.rounding or 0.01)).normalize()
        digits = max(0, -rounding.as_tuple().exponent)
        formatted_quantity = formatLang(
            self.env,
            quantity,
            digits=digits,
        )
        decimal_point = get_lang(self.env).decimal_point
        if decimal_point in formatted_quantity:
            formatted_quantity = formatted_quantity.rstrip('0').rstrip(
                decimal_point,
            )
        return _(
            '%(quantity)s %(uom)s',
            quantity=formatted_quantity,
            uom=uom.display_name,
        )

    @api.model_create_multi
    def create(self, vals_list):
        sanitized_vals_list = []
        for vals in vals_list:
            sanitized_vals = dict(vals)
            sanitized_vals.pop('measurement_matrix_required', None)
            sanitized_vals_list.append(sanitized_vals)
        checks = super().create(sanitized_vals_list)
        checks._initialize_measurement_snapshot()
        return checks

    def write(self, vals):
        if 'measurement_matrix_required' in vals:
            raise UserError(_(
                'Ознака необхідності матриці є незмінним snapshot перевірки.',
            ))
        if 'point_id' in vals:
            new_point_id = vals.get('point_id') or False
            protected_checks = self.filtered(
                lambda check: (
                    (check.point_id.id or False) != new_point_id
                    and bool(
                        check.measurement_column_ids
                        or check.equipment_selection_ids
                        or check.sample_ids
                        or check.measurement_matrix_required
                    )
                ),
            )
            if protected_checks:
                raise UserError(_(
                    'Не можна змінити пункт контролю після формування '
                    'матриці показників.',
                ))

        result = super().write(vals)
        if 'point_id' in vals:
            self._initialize_measurement_snapshot()
        return result

    def _set_measurement_matrix_required(self):
        checks_to_update = self.filtered(
            lambda check: not check.measurement_matrix_required,
        )
        if checks_to_update:
            super(QualityCheck, checks_to_update).write({
                'measurement_matrix_required': True,
            })

    def _initialize_measurement_snapshot(self):
        column_model = self.env['quality.check.measurement.column']
        selection_model = self.env['quality.check.equipment.selection']
        for check in self:
            check.check_access_rights('write')
            check.check_access_rule('write')
            if (
                not check.point_id
                or check.measurement_matrix_required
                or check.measurement_column_ids
                or check.equipment_selection_ids
                or check.sample_ids
            ):
                continue
            source_lines = check.point_id.control_parameter_line_ids.sorted(
                key=lambda line: (line.sequence, line.id),
            )
            if (
                not source_lines
                and not check.point_id.visual_sample_control_required
            ):
                continue
            check._set_measurement_matrix_required()
            if not source_lines:
                continue

            column_values = []
            category_set_values = {}
            for line in source_lines:
                categories = (
                    line.equipment_category_ids
                    or line.equipment_category_id
                ).sorted('id')
                if not categories:
                    raise ValidationError(_(
                        'Неможливо створити матрицю: для параметра '
                        '«%(parameter)s» не вибрано категорії ЗВТ.',
                        parameter=line.parameter_id.display_name,
                    ))
                category_set_key = ','.join(
                    str(category_id)
                    for category_id in categories.ids
                )
                category_names = ', '.join(
                    categories.mapped('display_name'),
                )
                first_category = categories[0]
                boolean_expected = False
                if line.parameter_type == 'boolean':
                    boolean_expected = normalize_boolean_norm(line.text_norm)
                    if not boolean_expected:
                        raise ValidationError(_(
                            'Неможливо створити матрицю: текстова норма '
                            'булевого параметра «%(parameter)s» не означає '
                            'однозначно «Так» або «Ні».',
                            parameter=line.parameter_id.display_name,
                        ))
                column_values.append({
                    'quality_check_id': check.id,
                    'source_line_id': line.id,
                    'sequence': line.sequence,
                    'control_type': line.control_type,
                    'equipment_category_id':
                        first_category.id,
                    'equipment_category_name':
                        first_category.display_name,
                    'equipment_category_ids': [
                        Command.set(categories.ids),
                    ],
                    'equipment_category_names_snapshot':
                        category_names,
                    'category_set_key': category_set_key,
                    'parameter_id': line.parameter_id.id,
                    'parameter_name': line.parameter_id.name,
                    'parameter_type': line.parameter_type,
                    'parameter_unit': line.parameter_id.unit or False,
                    'has_min_tolerance': line.has_min_tolerance,
                    'min_tolerance': line.min_tolerance,
                    'has_max_tolerance': line.has_max_tolerance,
                    'max_tolerance': line.max_tolerance,
                    'text_norm': line.text_norm or False,
                    'boolean_expected': boolean_expected,
                })
                selection_categories = categories.filtered(
                    'requires_equipment_selection',
                )
                if selection_categories:
                    selection_category_set_key = ','.join(
                        str(category_id)
                        for category_id in selection_categories.ids
                    )
                    selection_category_names = ', '.join(
                        selection_categories.mapped('display_name'),
                    )
                    first_selection_category = selection_categories[0]
                    category_set_values.setdefault(
                        selection_category_set_key,
                        {
                            'quality_check_id': check.id,
                            'sequence': line.sequence,
                            'equipment_category_id':
                                first_selection_category.id,
                            'equipment_category_name':
                                first_selection_category.display_name,
                            'allowed_equipment_category_ids': [
                                Command.set(selection_categories.ids),
                            ],
                            'equipment_category_names_snapshot':
                                selection_category_names,
                            'category_set_key':
                                selection_category_set_key,
                        },
                    )

            snapshot_context = {
                'quality_vataga_snapshot_initialization': True,
            }
            if category_set_values:
                selection_model.sudo().with_context(
                    **snapshot_context
                ).create(list(category_set_values.values()))
            column_model.sudo().with_context(**snapshot_context).create(
                column_values,
            )

    @api.depends(
        'quality_state',
        'point_id',
        'point_id.control_parameter_line_ids',
        'point_id.visual_sample_control_required',
        'measurement_matrix_required',
        'measurement_column_ids',
        'equipment_selection_ids',
        'sample_ids',
    )
    def _compute_can_initialize_measurement_matrix(self):
        for check in self:
            check.can_initialize_measurement_matrix = bool(
                check.quality_state == 'none'
                and check.point_id
                and (
                    check.point_id.control_parameter_line_ids
                    or check.point_id.visual_sample_control_required
                )
                and not check.measurement_matrix_required
                and not check.measurement_column_ids
                and not check.equipment_selection_ids
                and not check.sample_ids
            )

    @api.depends(
        'measurement_matrix_required',
        'measurement_column_ids',
        'measurement_column_ids.equipment_category_ids.requires_equipment_selection',
        'measurement_column_ids.equipment_category_id.requires_equipment_selection',
        'sample_ids',
        'sample_ids.sample_result',
        'sample_ids.is_complete',
        'sample_ids.has_failure',
        'equipment_selection_ids',
        'equipment_selection_ids.requires_equipment_selection',
        'equipment_selection_ids.required_equipment_category_ids',
        'equipment_selection_ids.allowed_equipment_category_ids',
        'equipment_selection_ids.equipment_ids',
        'equipment_selection_ids.equipment_ids.category_id',
        'equipment_selection_ids.equipment_id',
        'equipment_selection_ids.equipment_id.category_id',
    )
    def _compute_measurement_matrix_state(self):
        for check in self:
            matrix_required = check.measurement_matrix_required
            required_categories = (
                check._get_required_measurement_equipment_categories()
            )
            selections = check.equipment_selection_ids.filtered(
                'requires_equipment_selection',
            )
            samples = check.sample_ids
            check.equipment_selection_complete = (
                not matrix_required
                or not required_categories
                or (
                    bool(selections)
                    and all(
                        selection._has_valid_equipment_selection()
                        for selection in selections
                    )
                )
            )
            check.measurement_matrix_complete = (
                not matrix_required
                or bool(samples)
                and all(samples.mapped('is_complete'))
            )
            check.measurement_matrix_has_failure = (
                matrix_required
                and any(samples.mapped('has_failure'))
            )
            check.can_pass_measurement_check = (
                not matrix_required
                or (
                    check.equipment_selection_complete
                    and check.measurement_matrix_complete
                    and not check.measurement_matrix_has_failure
                    and all(
                        result == 'pass'
                        for result in samples.mapped('sample_result')
                    )
                )
            )

    def _get_required_measurement_equipment_categories(self):
        self.ensure_one()
        categories = self.env['maintenance.equipment.category']
        for column in self.measurement_column_ids:
            categories |= (
                column.equipment_category_ids
                or column.equipment_category_id
            )
        return categories.filtered('requires_equipment_selection')

    @api.depends(
        'measurement_column_ids',
        'sample_ids',
        'sample_ids.visual_result',
        'sample_ids.sample_result',
        'sample_ids.measurement_value_ids.result',
        'sample_ids.measurement_value_ids.numeric_value',
        'sample_ids.measurement_value_ids.boolean_value',
        'sample_ids.measurement_value_ids.string_value',
        'sample_ids.measurement_value_ids.manual_result',
    )
    def _compute_measurement_matrix_data(self):
        for check in self:
            persisted_check_id = check._origin.id or False
            check.measurement_matrix_data = {
                'quality_check_id': persisted_check_id,
            }

    def _add_measurement_samples(self, count):
        self.ensure_one()
        self._ensure_measurement_editable()
        if not self.measurement_matrix_required:
            raise UserError(_(
                'Для цієї перевірки матриця показників не потрібна.',
            ))
        if (
            isinstance(count, bool)
            or not isinstance(count, (int, float))
            or not float(count).is_integer()
            or count < 1
        ):
            raise ValidationError(_(
                'Кількість нових зразків повинна бути цілим числом '
                'більшим за нуль.',
            ))
        count = int(count)

        # The row lock keeps sample numbering stable when two inspectors add
        # samples to the same check at the same time.
        self.env.cr.execute(
            'SELECT id FROM quality_check WHERE id = %s FOR UPDATE',
            [self.id],
        )
        last_sample = self.env['quality.check.sample'].search(
            [('quality_check_id', '=', self.id)],
            order='sample_number desc',
            limit=1,
        )
        first_number = (last_sample.sample_number or 0) + 1
        self.env['quality.check.sample'].sudo().with_context(
            quality_vataga_sample_initialization=True,
        ).create([
            {
                'quality_check_id': self.id,
                'sample_number': sample_number,
                'sequence': sample_number * 10,
            }
            for sample_number in range(
                first_number,
                first_number + count,
            )
        ])

    def add_measurement_samples(self, count):
        self.ensure_one()
        self._add_measurement_samples(count)
        return self.get_measurement_matrix_data()

    def _remove_measurement_samples(self, count):
        self.ensure_one()
        self._ensure_measurement_editable(
            completed_message=_(
                'Не можна видаляти зразки завершеної перевірки.',
            ),
        )
        if (
            isinstance(count, bool)
            or not isinstance(count, (int, float))
            or not float(count).is_integer()
            or count < 1
        ):
            raise ValidationError(_(
                'Кількість зразків для видалення повинна бути цілим '
                'числом більшим за нуль.',
            ))
        count = int(count)

        # Serialize sample additions and removals for the same check so the
        # tail and the next sample number cannot change during the operation.
        self.env.cr.execute(
            'SELECT id FROM quality_check WHERE id = %s FOR UPDATE',
            [self.id],
        )
        samples_to_remove = self.env['quality.check.sample'].search(
            [('quality_check_id', '=', self.id)],
            order='sample_number desc, id desc',
            limit=count,
        )
        if len(samples_to_remove) != count:
            raise ValidationError(_(
                'Неможливо прибрати %(count)s зразків: у перевірці '
                'наявно лише %(available)s.',
                count=count,
                available=len(samples_to_remove),
            ))

        sample_with_results = samples_to_remove.filtered(
            lambda sample: sample._has_entered_results(),
        )[:1]
        if sample_with_results:
            raise UserError(_(
                'Не можна прибрати останні %(count)s зразків, оскільки '
                'зразок №%(number)s вже містить введені результати.',
                count=count,
                number=sample_with_results.sample_number,
            ))

        # The technical sample model is intentionally read-only in ACLs.
        # Parent write rights/rules were checked above, and the user-scoped
        # search fixed the exact records before this controlled elevation.
        for sample in samples_to_remove.sudo():
            sample.unlink()

    def remove_measurement_samples(self, count):
        self.ensure_one()
        self._remove_measurement_samples(count)
        return self.get_measurement_matrix_data()

    def action_add_measurement_samples(self):
        self.ensure_one()
        self._add_measurement_samples(self.sample_count_to_add)
        self.sample_count_to_add = 1
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_initialize_measurement_matrix(self):
        self.ensure_one()
        if self.quality_state != 'none':
            raise UserError(_(
                'Матрицю можна ініціалізувати лише для незавершеної '
                'перевірки.',
            ))
        if not self.point_id or not (
            self.point_id.control_parameter_line_ids
            or self.point_id.visual_sample_control_required
        ):
            raise UserError(_(
                'У пункті контролю немає налаштувань для матриці.',
            ))
        if (
            self.measurement_matrix_required
            or self.measurement_column_ids
            or self.equipment_selection_ids
            or self.sample_ids
        ):
            raise UserError(_(
                'Матриця вже ініціалізована або має частково створену '
                'структуру. Автоматичне перегенерування заборонено.',
            ))
        self._initialize_measurement_snapshot()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def get_measurement_matrix_data(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')

        values_by_key = {
            (value.sample_id.id, value.column_id.id): value
            for value in self.sample_ids.measurement_value_ids
        }
        columns = [{
            'id': column.id,
            'control_type': column.control_type,
            'control_type_label': dict(
                column._fields['control_type'].selection,
            ).get(column.control_type),
            'parameter_name': column.parameter_name,
            'parameter_type': column.parameter_type,
            'parameter_unit': column.parameter_unit or '',
            'tolerance_label': column._get_tolerance_label() or '',
            'text_norm': column.text_norm or '',
        } for column in self.measurement_column_ids]

        samples = []
        for sample in self.sample_ids:
            cells = []
            for column in self.measurement_column_ids:
                value = values_by_key.get((sample.id, column.id))
                if not value:
                    continue
                cells.append({
                    'id': value.id,
                    'column_id': column.id,
                    'parameter_type': value.parameter_type,
                    'numeric_input': (
                        format_number(value.numeric_value)
                        if value.has_numeric_value
                        else ''
                    ),
                    'boolean_value': value.boolean_value or '',
                    'string_value': value.string_value or '',
                    'manual_result': value.manual_result or '',
                    'result': value.result,
                    'failure_reason': value.failure_reason or '',
                })
            samples.append({
                'id': sample.id,
                'sample_number': sample.sample_number,
                'display_name': sample.display_name,
                'visual_result': sample.visual_result or '',
                'sample_result': sample.sample_result,
                'cells': cells,
            })

        return {
            'check_id': self.id,
            'editable': self.quality_state == 'none',
            'columns': columns,
            'samples': samples,
            'can_pass': self.can_pass_measurement_check,
            'has_failure': self.measurement_matrix_has_failure,
            'is_complete': self.measurement_matrix_complete,
            'equipment_complete': self.equipment_selection_complete,
        }

    def update_measurement_visual_result(self, sample_id, visual_result):
        self.ensure_one()
        self._ensure_measurement_editable()
        sample = self.env['quality.check.sample'].browse(sample_id).exists()
        if not sample or sample.quality_check_id != self:
            raise ValidationError(_('Зразок не належить цій перевірці.'))
        if visual_result not in ('yes', 'no', False, ''):
            raise ValidationError(_(
                'Візуальний контроль повинен мати значення «Так» або «Ні».',
            ))
        sample.sudo().write({'visual_result': visual_result or False})
        return self.get_measurement_matrix_data()

    def update_measurement_value(self, value_id, payload):
        self.ensure_one()
        self._ensure_measurement_editable()
        value = self.env[
            'quality.check.measurement.value'
        ].browse(value_id).exists()
        if not value or value.quality_check_id != self:
            raise ValidationError(_('Комірка не належить цій перевірці.'))

        payload = payload or {}
        if value.parameter_type == 'numeric':
            has_value, numeric_value = parse_numeric_input(
                payload.get('numeric_input'),
                _('Значення показника'),
            )
            values = {
                'has_numeric_value': has_value,
                'numeric_value': numeric_value,
                'boolean_value': False,
                'string_value': False,
                'manual_result': False,
            }
        elif value.parameter_type == 'boolean':
            boolean_value = payload.get('boolean_value') or False
            if boolean_value not in ('yes', 'no', False):
                raise ValidationError(_(
                    'Булеве значення повинно бути «Так» або «Ні».',
                ))
            values = {
                'has_numeric_value': False,
                'numeric_value': 0.0,
                'boolean_value': boolean_value,
                'string_value': False,
                'manual_result': False,
            }
        else:
            manual_result = payload.get('manual_result') or False
            if manual_result not in ('pass', 'fail', False):
                raise ValidationError(_(
                    'Ручний результат повинен бути PASS або FAIL.',
                ))
            values = {
                'has_numeric_value': False,
                'numeric_value': 0.0,
                'boolean_value': False,
                'string_value': payload.get('string_value') or False,
                'manual_result': manual_result,
            }
        value.sudo().write(values)
        return self.get_measurement_matrix_data()

    def _ensure_measurement_editable(self, completed_message=None):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.quality_state != 'none':
            raise UserError(
                completed_message
                or _('Матрицю завершеної перевірки не можна редагувати.'),
            )

    def _validate_measurement_can_pass(self):
        for check in self.filtered('measurement_matrix_required'):
            invalid_boolean_column = check.measurement_column_ids.filtered(
                lambda column:
                    column.parameter_type == 'boolean'
                    and not column.boolean_expected,
            )[:1]
            if invalid_boolean_column:
                raise ValidationError(_(
                    'Текстова норма параметра «%(parameter)s» не означає '
                    'однозначно «Так» або «Ні». Виправте QCP; вже створена '
                    'перевірка зберігає початковий snapshot.',
                    parameter=invalid_boolean_column.parameter_name,
                ))
            required_categories = (
                check._get_required_measurement_equipment_categories()
            )
            selections = check.equipment_selection_ids.filtered(
                'requires_equipment_selection',
            )
            if required_categories and not selections:
                raise ValidationError(_(
                    'Оберіть щонайменше один допустимий прилад для '
                    'категорій «%(categories)s».',
                    categories=', '.join(
                        required_categories.mapped('display_name'),
                    ),
                ))
            missing_equipment = selections.filtered(
                lambda selection:
                    not selection._has_valid_equipment_selection(),
            )[:1]
            if missing_equipment:
                raise ValidationError(_(
                    'Оберіть щонайменше один допустимий прилад для '
                    'категорій «%(categories)s».',
                    categories=(
                        missing_equipment
                        .equipment_category_names_snapshot
                        or missing_equipment.equipment_category_name
                    ),
                ))
            if not check.sample_ids:
                raise ValidationError(_(
                    'Додайте щонайменше один зразок для перевірки.',
                ))
            check._validate_measurement_sample_structure()
            if check.measurement_matrix_has_failure:
                raise ValidationError(_(
                    'Перевірка містить значення поза допустимими межами або '
                    'негативний результат. Доступний лише результат '
                    '«Невдало».',
                ))
            if not check.measurement_matrix_complete:
                if check.measurement_column_ids:
                    raise ValidationError(_(
                        'Заповніть візуальний контроль і всі комірки матриці '
                        'показників.',
                    ))
                raise ValidationError(_(
                    'Заповніть візуальний контроль для всіх зразків.',
                ))

    def _validate_measurement_sample_structure(self):
        for check in self:
            expected_column_ids = set(check.measurement_column_ids.ids)
            for sample in check.sample_ids:
                values = sample.measurement_value_ids
                actual_column_ids = values.mapped('column_id').ids
                structure_complete = bool(
                    check.measurement_matrix_required
                    and len(values) == len(expected_column_ids)
                    and set(actual_column_ids) == expected_column_ids
                    and all(
                        value.quality_check_id == check
                        and value.sample_id == sample
                        and value.column_id.quality_check_id == check
                        for value in values
                    )
                )
                if not structure_complete:
                    raise ValidationError(_(
                        'Матриця зразка №%(number)s має неповний набір '
                        'комірок. Оновіть або відновіть структуру матриці.',
                        number=sample.sample_number,
                    ))

    def _snapshot_selected_equipment(self):
        for check in self:
            check.check_access_rights('write')
            check.check_access_rule('write')
            check.equipment_selection_ids.filtered(
                'requires_equipment_selection',
            )._snapshot_selected_equipment()

    def do_pass(self):
        self._validate_measurement_can_pass()
        self._snapshot_selected_equipment()
        return super().do_pass()

    def do_fail(self):
        self._snapshot_selected_equipment()
        return super().do_fail()
