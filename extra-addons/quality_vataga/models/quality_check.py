from decimal import Decimal
from math import isclose

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import formatLang, get_lang

from .measurement_utils import (
    format_number,
    normalize_boolean_norm,
    parse_numeric_input,
)
from .quality_check_sample import AUTOMATIC_SAMPLE_INITIALIZATION
from .quality_point import CONTROL_STAGES


RECHECK_INITIALIZATION = object()


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    x_control_stage = fields.Selection(
        CONTROL_STAGES, string='Етап контролю', default='incoming',
        required=True, readonly=True, copy=False,
    )
    automatic_sample_count = fields.Integer(readonly=True, copy=False)
    automatic_sample_lot_id = fields.Many2one(
        'stock.lot', readonly=True, copy=False, check_company=True, ondelete='restrict',
    )
    quality_quarantine_deferred = fields.Boolean(
        compute='_compute_quality_quarantine_deferred', compute_sudo=True,
    )
    quality_recheck_of_id = fields.Many2one(
        'quality.check', string='Початкова перевірка', readonly=True, copy=False,
        check_company=True, ondelete='restrict',
    )
    quality_recheck_ids = fields.One2many(
        'quality.check', 'quality_recheck_of_id', readonly=True, copy=False,
    )

    _sql_constraints = [
        ('quality_single_recheck', 'unique(quality_recheck_of_id)',
         'Для перевірки вже створено повторний контроль.'),
    ]

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
        recheck = self.env.context.get('quality_vataga_recheck') is RECHECK_INITIALIZATION
        sanitized_vals_list = []
        default_point_id = False
        if any('point_id' not in vals for vals in vals_list):
            default_point_id = self.default_get(['point_id']).get('point_id')
        points = self.env['quality.point']
        for vals in vals_list:
            sanitized_vals = dict(vals)
            if 'quality_recheck_ids' in vals or ('quality_recheck_of_id' in vals and not recheck):
                raise AccessError(_('Повторний контроль створюється лише відповідною дією.'))
            sanitized_vals.setdefault('quality_recheck_of_id', False)
            sanitized_vals.pop('measurement_matrix_required', None)
            point = points.browse(vals.get('point_id', default_point_id))
            sanitized_vals['x_control_stage'] = 'in_process' if recheck else (point.x_control_stage or 'incoming')
            sanitized_vals['automatic_sample_count'] = 0
            sanitized_vals['automatic_sample_lot_id'] = False
            sanitized_vals_list.append(sanitized_vals)
        checks = super().create(sanitized_vals_list)
        if not recheck:
            checks._initialize_measurement_snapshot()
        checks.filtered(
            lambda check: (
                check.x_control_stage == 'in_process'
                and check.quality_state == 'pass'
            ),
        )._validate_measurement_can_pass()
        return checks

    def write(self, vals):
        if {'quality_recheck_of_id', 'quality_recheck_ids'} & vals.keys():
            raise AccessError(_('Зв’язок повторного контролю не можна змінювати вручну.'))
        if {'x_control_stage', 'automatic_sample_count', 'automatic_sample_lot_id'} & vals.keys():
            raise UserError(_(
                'Етап контролю та розмір автоматичної вибірки зберігаються '
                'в перевірці та не редагуються вручну.',
            ))
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

        protected = self.filtered(lambda check: check.x_control_stage == 'in_process')
        binding_fields = {'production_id', 'workorder_id', 'product_id', 'company_id', 'lot_id'}
        control_fields = {'test_type_id', 'measure_on', 'finished_product_sequence'}
        if protected and ({'quality_state'} | binding_fields | control_fields) & vals.keys():
            protected._lock_measurement_checks()
            for check in protected:
                if check.quality_state == 'fail' or check.quality_recheck_of_id:
                    if control_fields & vals.keys() or any(
                        check[field].id != (vals[field] or False)
                        for field in binding_fields & vals.keys()
                    ):
                        raise UserError(_('Не можна змінювати обсяг або призначення завершеної чи повторної перевірки.'))
                if (
                    'quality_state' in vals and vals['quality_state'] != check.quality_state
                    and (check.quality_state == 'fail'
                         or (check.quality_recheck_of_id and check.quality_state != 'none'))
                ):
                    raise UserError(_('Результат завершеного контролю зберігається в історії.'))
        if binding_fields & vals.keys():
            destination = self.env['mrp.production'].browse(vals.get('production_id'))
            for check in protected.sudo():
                production = check.production_id
                if not (
                    (production.picking_type_id.quality_quarantine_location_id
                     and (production.state != 'done' or production.quality_quarantine_point_ids))
                    or destination.sudo().picking_type_id.quality_quarantine_location_id
                ):
                    continue
                if (
                    (check.sample_ids or production.quality_quarantine_point_ids
                     or destination.sudo().quality_quarantine_point_ids)
                    and any(check[field].id != (vals[field] or False) for field in binding_fields & vals.keys())
                ):
                    raise UserError(_('Не можна змінювати партію або замовлення контрольованої перевірки.'))
        if vals.get('quality_state') == 'pass':
            protected._validate_measurement_can_pass()
        productions = self.env['mrp.production']
        if 'quality_state' in vals and protected:
            protected.check_access_rights('write')
            protected.check_access_rule('write')
            for check in protected.sudo():
                production = check.production_id
                if (
                    not production.picking_type_id.quality_quarantine_location_id
                    or (production.state == 'done' and not production.quality_quarantine_point_ids)
                ):
                    continue
                if vals['quality_state'] != check.quality_state and production.move_finished_ids.quality_release_move_ids.filtered(
                    lambda move: move.state == 'done',
                ):
                    raise UserError(_('Результат контролю вже використано для випуску партії.'))
                productions |= production
            # Serialize QC decisions with the production posting and each other.
            for production in productions.sudo().sorted('id'):
                production.write({'write_date': fields.Datetime.now()})
        result = super().write(vals)
        if 'point_id' in vals:
            self._initialize_measurement_snapshot()
        productions.sudo()._quality_release_if_passed()
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_except_quarantine_checks(self):
        if self.sudo().filtered(
            lambda check: check.x_control_stage == 'in_process'
            and (
                check.quality_recheck_of_id
                or check.production_id.quality_quarantine_point_ids
                or (check.quality_state != 'none'
                    and check.production_id.state != 'done'
                    and check.production_id.picking_type_id.quality_quarantine_location_id)
            ),
        ):
            raise UserError(_('Не можна видаляти перевірку контрольованої партії.'))

    def _lock_measurement_checks(self):
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.flush_recordset(['quality_state'])
        self.env.cr.execute(
            'SELECT id FROM quality_check WHERE id IN %s ORDER BY id FOR UPDATE',
            [tuple(self.ids)],
        )
        self.invalidate_recordset()

    def action_open_quality_recheck(self):
        self.ensure_one()
        if not self.env.user.has_group('quality.group_quality_user'):
            raise AccessError(_('Повторний контроль може створити інспектор якості.'))
        self._lock_measurement_checks()
        if (
            self.x_control_stage != 'in_process' or self.quality_state != 'fail'
            or not self.measurement_matrix_required or self.quality_recheck_of_id
        ):
            raise UserError(_('Повторний контроль доступний лише для початкової перевірки зі статусом FAIL.'))
        retry = self.sudo().quality_recheck_ids.sudo(False)
        if not retry:
            retry = self._create_quality_recheck()
        retry.check_access_rights('read')
        retry.check_access_rule('read')
        return {
            'type': 'ir.actions.act_window', 'name': _('Повторний контроль'),
            'res_model': 'quality.check', 'view_mode': 'form', 'res_id': retry.id,
        }

    def _create_quality_recheck(self):
        production = self.production_id
        lot = self.automatic_sample_lot_id or self.lot_id
        if (
            not production or not self.point_id or self.company_id != production.company_id
            or (self.product_id and self.product_id != production.product_id)
            or (production.product_tracking != 'none' and not lot)
            or not self.automatic_sample_count or len(self.sample_ids) != self.automatic_sample_count
        ):
            raise UserError(_('Початкова перевірка не містить однозначної партії та кількості зразків.'))
        self._validate_measurement_sample_structure()
        binding = {
            name: self[name].id
            for name in ('company_id', 'production_id', 'point_id', 'team_id', 'workorder_id')
        }
        retry = self.with_context({
            'allowed_company_ids': self.env.companies.ids,
            'quality_vataga_recheck': RECHECK_INITIALIZATION,
        }).create(dict(
            binding, quality_recheck_of_id=self.id, product_id=production.product_id.id,
            lot_id=lot.id, measure_on=self.measure_on, quality_state='none',
            test_type_id=self.test_type_id.id,
        ))
        super(QualityCheck, retry).write({
            'measurement_matrix_required': True,
            'automatic_sample_count': self.automatic_sample_count,
            'automatic_sample_lot_id': lot.id,
        })
        retry._copy_recheck_measurement_snapshot(self)
        retry._create_measurement_samples(1, self.automatic_sample_count, AUTOMATIC_SAMPLE_INITIALIZATION)
        retry.message_post(body=_('Повторний контроль після перевірки %(check)s.', check=self.display_name))
        return retry

    def _copy_recheck_measurement_snapshot(self, original):
        context = {'quality_vataga_snapshot_initialization': True}
        columns = self.env['quality.check.measurement.column'].sudo().with_context(**context)
        selections = self.env['quality.check.equipment.selection'].sudo().with_context(**context)
        columns.create([
            column.copy_data({
                'quality_check_id': self.id,
                'equipment_category_ids': [Command.set((column.equipment_category_ids or column.equipment_category_id).ids)],
            })[0] for column in original.measurement_column_ids
        ])
        selections.create([
            selection.copy_data({
                'quality_check_id': self.id, 'equipment_id': False,
                'equipment_ids': [Command.clear()],
                'preserve_completed_equipment_selection': True,
            })[0] for selection in original.equipment_selection_ids.filtered('requires_equipment_selection')
        ])

    @api.depends(
        'x_control_stage', 'measurement_matrix_required', 'test_type_id', 'quality_state',
        'production_id.picking_type_id.quality_quarantine_location_id', 'workorder_id',
    )
    def _compute_quality_quarantine_deferred(self):
        for check in self:
            production = check.production_id
            check.quality_quarantine_deferred = bool(
                check.quality_state == 'none'
                and production.state not in ('done', 'cancel')
                and check.x_control_stage == 'in_process' and check.measurement_matrix_required
                and check.test_type in ('passfail', 'measure')
                and check.workorder_id and check.workorder_id.production_id == production
                and not (check.picking_id or check.component_id or check.move_line_id)
                and (not check.product_id or check.product_id == production.product_id)
                and check.company_id == production.company_id
                and production.product_id.type == 'product'
                and production.product_uom_id.category_id == self.env.ref('uom.product_uom_unit').category_id
                and production._quality_quarantine_location()
            )

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
                and check.x_control_stage != 'in_process'
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
        if self.quality_recheck_of_id:
            return self.equipment_selection_ids.required_equipment_category_ids
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

    def _get_in_process_sample_quantity(self):
        self.ensure_one()
        production = self.production_id
        if not production or (
            self.product_id and self.product_id != production.product_id
        ):
            return 0, _(
                'Для автоматичної вибірки потрібне пов’язане виробниче '
                'замовлення на виріб, що перевіряється.',
            )

        unit = self.env.ref('uom.product_uom_unit')
        if production.product_uom_id.category_id != unit.category_id:
            return 0, _(
                'Автоматична вибірка потребує фактичної кількості у штуках.',
            )

        workorder = self.workorder_id if 'workorder_id' in self._fields else False
        lot = self.lot_id or production.lot_producing_id
        finished_moves = production.move_finished_ids.filtered(
            lambda move: move.product_id == production.product_id
            and move.state != 'cancel'
            and (move.picked or move.state == 'done'),
        )
        if workorder and production.product_tracking != 'none':
            quantity = sum(
                line.product_uom_id._compute_quantity(line.quantity, unit, round=False)
                for move in finished_moves
                for line in move.move_line_ids
                if lot and line.lot_id == lot and (line.picked or move.state == 'done')
            )
        else:
            quantity = sum(
                move.product_uom._compute_quantity(
                    move.quantity if move.state == 'done' else move._get_picked_quantity(),
                    unit,
                    round=False,
                )
                for move in finished_moves
            )
        if (
            production.state not in ('done', 'cancel')
            and workorder and workorder.production_id == production
            and {'qty_done', 'finished_product_sequence'} <= self._fields.keys()
            and (lot or production.product_tracking == 'none')
        ):
            registered_quantity = self._get_in_process_registration_quantity(
                workorder, lot,
                allow_shop_floor_quantity=not (quantity or production.qty_produced),
            )
            if registered_quantity:
                quantity = production.product_uom_id._compute_quantity(
                    registered_quantity, unit, round=False,
                )

        if quantity <= 0:
            return 0, _(
                'Фактичну кількість виробів ще не зареєстровано. '
                'Підтвердьте крок «Зареєструвати виробництво» для цієї '
                'партії або відкрийте матрицю після проведення випуску.',
            )
        count = round(quantity)
        if count < 1 or not isclose(quantity, count, rel_tol=0, abs_tol=1e-9):
            return 0, _(
                'Фактична кількість має бути цілим числом штук, '
                'щоб створити зразок на кожен виріб.',
            )
        return count, False

    def _get_in_process_registration_quantity(
        self, workorders, lot, *, allow_shop_floor_quantity,
    ):
        production = self.production_id
        domain = [
            ('production_id', '=', production.id),
            ('finished_product_sequence', '=', self.finished_product_sequence),
            ('test_type_id.technical_name', '=', 'register_production'),
            '|', ('lot_id', '=', lot.id or False), ('lot_id', '=', False),
        ]
        # Odoo links the route (or explicit dependencies) with blocked_by.
        # Prefer this operation, then look at its nearest predecessors.
        visited = self.env['mrp.workorder']
        has_registration_step = False
        while workorders:
            steps = self.search(
                domain + [('workorder_id', 'in', workorders.ids)],
                order='id desc',
            )
            registration = steps.filtered(
                lambda step: step.quality_state == 'pass'
                and step.lot_id == lot and step.qty_done > 0,
            )[:1]
            if registration:
                return registration.qty_done
            has_registration_step = has_registration_step or bool(steps)
            visited |= workorders
            workorders = workorders.blocked_by_workorder_ids.filtered(
                lambda wo: wo.production_id == production,
            ) - visited
        if allow_shop_floor_quantity and not has_registration_step:
            return self._get_shop_floor_quantity(visited, lot)
        return 0.0

    def _get_shop_floor_quantity(self, workorders, lot):
        production = self.production_id
        quantity = production.qty_producing
        rounding = production.product_uom_id.rounding
        if (
            production.state not in ('progress', 'to_close')
            or production.product_tracking == 'serial'
            or lot != production.lot_producing_id
            or self.workorder_id.state == 'cancel'
            or self.finished_product_sequence != self.workorder_id.qty_produced
            or not workorders.filtered(lambda wo: wo.state in ('progress', 'done'))
            or quantity <= 0
        ):
            return 0.0
        if quantity == production.quality_registered_quantity and lot == production.quality_registered_lot_id:
            return quantity
        # Preserve the existing fallback for partial quantities entered
        # before the Shop Floor confirmation was recorded on the MO.
        if (
            float_compare(quantity, production.product_qty, precision_rounding=rounding) >= 0
            or any(
                float_compare(quantity, wo.qty_remaining, precision_rounding=rounding) == 0
                for wo in workorders
                if wo.state != 'cancel'
            )
        ):
            return 0.0
        return quantity

    def _prepare_automatic_measurement_samples(self):
        self.ensure_one()
        if (
            self.x_control_stage != 'in_process'
            or self.automatic_sample_count
            or self.sample_ids
            or self.quality_state != 'none'
            or not self.measurement_matrix_required
        ):
            return False
        count, message = self._get_in_process_sample_quantity()
        if not count:
            return message
        try:
            self._ensure_measurement_editable()
        except AccessError:
            return _('Зразки будуть створені під час відкриття інспектором.')

        # Persist the count before creating rows. Concurrent loads update the
        # same check, so Odoo can retry and reuse the committed sample.
        super().write({
            'automatic_sample_count': count,
            'automatic_sample_lot_id': (self.lot_id or self.production_id.lot_producing_id).id,
        })
        self._create_measurement_samples(1, count, AUTOMATIC_SAMPLE_INITIALIZATION)
        return False

    def _ensure_manual_measurement_samples(self):
        if self.x_control_stage == 'in_process':
            raise UserError(_(
                'Зразки міжопераційного контролю формуються автоматично.',
            ))

    def _add_measurement_samples(self, count):
        self.ensure_one()
        self._ensure_measurement_editable()
        self._ensure_manual_measurement_samples()
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
        self._create_measurement_samples(first_number, count, True)

    def _create_measurement_samples(self, first_number, count, initialization):
        self.env['quality.check.sample'].sudo().with_context(
            quality_vataga_sample_initialization=initialization,
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
        self._ensure_manual_measurement_samples()
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
        automatic_sample_message = self._prepare_automatic_measurement_samples()

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
                'x_was_reworked': sample.x_was_reworked,
                'cells': cells,
            })

        return {
            'check_id': self.id,
            'editable': self.quality_state == 'none',
            'manual_samples': self.x_control_stage != 'in_process',
            'automatic_sample_message': automatic_sample_message,
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
            if check.x_control_stage == 'in_process' and not check.sample_ids:
                _count, message = check._get_in_process_sample_quantity()
                raise ValidationError(message or _(
                    'Відкрийте вкладку «Показники» для автоматичного '
                    'формування зразків.',
                ))
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
