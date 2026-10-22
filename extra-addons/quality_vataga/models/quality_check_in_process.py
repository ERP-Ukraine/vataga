from collections import Counter
from decimal import Decimal

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


# A client-supplied RPC context cannot grant permission to create batch rows.
IN_PROCESS_SAMPLE_TOKEN = object()
RESULT_QUANTITY_FIELDS = frozenset({
    'x_passed_qty', 'x_failed_qty', 'x_pending_qty',
    'in_process_result_quantities_valid',
})


def _batch_dependencies(model):
    dependencies = [
        'x_control_stage', 'quality_state', 'in_process_sample_quantity', 'product_id',
        'product_id.uom_id', 'picking_id', 'move_line_id', 'production_id',
        'production_id.qty_producing', 'production_id.product_id',
        'production_id.product_uom_id', 'production_id.state',
    ]
    # quality_mrp_workorder is optional; quality_mrp supplies production_id.
    if 'workorder_id' in model._fields:
        dependencies.extend([
            'workorder_id', 'workorder_id.production_id',
            'workorder_id.qty_producing', 'workorder_id.product_uom_id',
            'workorder_id.production_id.product_id',
            'workorder_id.production_id.product_uom_id',
            'workorder_id.production_id.state',
        ])
    return dependencies


class QualityCheckInProcess(models.Model):
    _inherit = 'quality.check'

    x_control_stage = fields.Selection(
        related='point_id.x_control_stage',
        string='Етап контролю',
        readonly=True,
    )
    in_process_sample_quantity = fields.Integer(
        string='Зафіксована кількість виробів для контролю',
        readonly=True,
        copy=False,
    )
    x_passed_qty = fields.Integer(
        string='Придатних', compute='_compute_in_process_result_quantities',
        store=True, readonly=True, copy=False, compute_sudo=False,
    )
    x_failed_qty = fields.Integer(
        string='Брак', compute='_compute_in_process_result_quantities',
        store=True, readonly=True, copy=False, compute_sudo=False,
    )
    x_pending_qty = fields.Integer(
        string='Не перевірено', compute='_compute_in_process_result_quantities',
        store=True, readonly=True, copy=False, compute_sudo=False,
    )
    in_process_result_quantities_valid = fields.Boolean(
        string='Кількість зразків відповідає партії',
        compute='_compute_in_process_result_quantities',
        store=True, readonly=True, copy=False, compute_sudo=False,
        help='Підрахунок стосується зафіксованої матриці. Ця ознака не '
             'підтверджує завершення контролю, вибір ЗВТ або допуск виробів.',
    )

    @api.depends(
        'x_control_stage', 'measurement_matrix_required',
        'in_process_sample_quantity', 'sample_ids', 'sample_ids.sample_result',
    )
    def _compute_in_process_result_quantities(self):
        """Count this check's frozen samples, independently of the current MO.

        Validity describes the tally's coverage, including pending/FAIL rows;
        it is not a release decision. Existing PASS/ZVT validation still applies.
        """
        for check in self:
            in_process = check.x_control_stage == 'in_process'
            counts = (
                Counter(sample.sample_result for sample in check.sample_ids)
                if in_process else {}
            )
            check.x_passed_qty = counts.get('pass', 0)
            check.x_failed_qty = counts.get('fail', 0)
            check.x_pending_qty = counts.get('pending', 0)
            counted = check.x_passed_qty + check.x_failed_qty + check.x_pending_qty
            check.in_process_result_quantities_valid = bool(
                in_process and check.measurement_matrix_required
                and check.in_process_sample_quantity > 0
                and counted == len(check.sample_ids) == check.in_process_sample_quantity
            )

    @api.model_create_multi
    def create(self, vals_list):
        if any(RESULT_QUANTITY_FIELDS.intersection(vals) for vals in vals_list):
            raise UserError(_('Кількісні результати перевірки обчислюються автоматично.'))
        if any('x_control_stage' in vals for vals in vals_list):
            raise UserError(_('Етап перевірки визначається пунктом контролю.'))
        if any(vals.get('in_process_sample_quantity') for vals in vals_list):
            raise UserError(_('Кількість зразків партії встановлює система.'))
        # Context defaults must not turn readonly computed fields into inputs.
        context = dict(self.env.context)
        for name in RESULT_QUANTITY_FIELDS:
            context.pop('default_' + name, None)
        checks = super(QualityCheckInProcess, self.with_context(context)).create(vals_list)
        checks.filtered(lambda check: (
            check.x_control_stage == 'in_process'
            and check.quality_state == 'pass'
        ))._validate_measurement_can_pass()
        return checks

    def write(self, vals):
        if RESULT_QUANTITY_FIELDS.intersection(vals):
            raise UserError(_('Кількісні результати перевірки обчислюються автоматично.'))
        if 'x_control_stage' in vals:
            raise UserError(_('Етап перевірки визначається пунктом контролю.'))
        if 'in_process_sample_quantity' in vals:
            raise UserError(_('Кількість зразків партії встановлює система.'))
        source_fields = {'production_id', 'workorder_id', 'product_id'}
        for check in self.filtered('in_process_sample_quantity'):
            if any(
                field in vals and (check[field].id or False) != (vals[field] or False)
                for field in source_fields if field in check._fields
            ):
                raise UserError(_(
                    'Не можна змінити джерело сформованої партії. '
                    'Створіть нову перевірку.',
                ))
        if vals.get('quality_state') == 'pass':
            self.filtered(
                lambda check: check.x_control_stage == 'in_process',
            )._validate_measurement_can_pass()
        return super().write(vals)

    def _get_in_process_sample_quantity(self):
        """Return a whole number of units, or a readable reason to defer.

        Use the current production batch, never the planned product_qty or
        the picking fallback of operation_product_quantity. Standard Odoo 17
        workorder.qty_producing mirrors production.qty_producing.
        """
        self.ensure_one()
        production = self.production_id
        workorder = self.workorder_id if 'workorder_id' in self._fields else False
        if workorder:
            if production and production != workorder.production_id:
                return 0, _('Робоче завдання належить іншому виробничому замовленню.')
            production = workorder.production_id
        if (
            not production or self.picking_id or self.move_line_id
            or production.product_id != self.product_id
            or production.state == 'cancel'
        ):
            return 0, _(
                'Неможливо однозначно визначити виробничу партію. '
                'Перевірте виріб і виробниче замовлення / робоче завдання.',
            )
        unit = self.env.ref('uom.product_uom_unit')
        source_uom = production.product_uom_id
        if (
            not source_uom or source_uom.category_id != unit.category_id
            or self.product_id.uom_id.category_id != unit.category_id
            or workorder and workorder.product_uom_id != source_uom
        ):
            return 0, _(
                'Автоматична матриця потребує кількості в одиницях виробів. '
                'Перевірте одиницю вимірювання виробничої партії.',
            )
        quantity = production.qty_producing
        if not quantity:
            return 0, _(
                'Фактичну кількість виробів ще не встановлено або вона дорівнює '
                'нулю. Задайте кількість у виробництві та оновіть матрицю.',
            )
        quantity = Decimal(str(source_uom._compute_quantity(
            quantity, unit, round=False,
        )))
        if (
            not quantity.is_finite() or quantity <= 0
            or quantity != quantity.to_integral_value()
        ):
            return 0, _(
                'Кількість виробів має бути додатним цілим числом. '
                'Дробну кількість не можна автоматично округлювати.',
            )
        return int(quantity), False

    def _get_in_process_batch_error(self, validate_current=False):
        self.ensure_one()
        # A completed inspection describes its frozen batch even after the
        # production moves on. Every attempt to PASS still validates the
        # current source explicitly, including a direct quality_state write.
        quantity = self.in_process_sample_quantity
        if self.quality_state == 'none' or validate_current:
            quantity, error = self._get_in_process_sample_quantity()
            if error:
                return error
        if not self.measurement_matrix_required:
            return _('Матрицю не сформовано: налаштуйте параметри або візуальний контроль у пункті контролю.')
        if not self.in_process_sample_quantity:
            return _('Матрицю партії ще не сформовано. Оновіть матрицю.')
        if (
            quantity != self.in_process_sample_quantity
            or len(self.sample_ids) != self.in_process_sample_quantity
        ):
            return _(
                'Кількість виробів (%(current)s) або зразків (%(samples)s) '
                'не відповідає зафіксованій партії (%(batch)s). '
                'Результати збережено. Відновіть кількість партії або '
                'створіть нову перевірку; автоматична зміна зразків заборонена.',
                current=quantity, samples=len(self.sample_ids),
                batch=self.in_process_sample_quantity,
            )
        return False

    def _ensure_in_process_measurement_samples(self):
        for check in self.filtered(lambda check: (
            check.x_control_stage == 'in_process'
            and check.quality_state == 'none'
            and check.measurement_matrix_required
        )):
            check._ensure_measurement_editable()
            # Lock before checking the snapshot and rows; repeated opens and
            # concurrent inspectors must not generate the same batch twice.
            check.env.cr.execute(
                'SELECT id FROM quality_check WHERE id = %s FOR UPDATE',
                [check.id],
            )
            check.invalidate_recordset(['in_process_sample_quantity', 'sample_ids'])
            if check.in_process_sample_quantity or check.sample_ids:
                continue
            quantity, error = check._get_in_process_sample_quantity()
            if error:
                continue
            check.with_context(
                quality_vataga_in_process_initialization=IN_PROCESS_SAMPLE_TOKEN,
            )._create_measurement_samples(quantity)
            # Bypass only the immutable-field guard in this extension.
            super(QualityCheckInProcess, check).write({
                'in_process_sample_quantity': quantity,
            })

    def _initialize_measurement_snapshot(self):
        for check in self.filtered(
            lambda check: check.x_control_stage == 'in_process',
        ).sorted('id'):
            self.env.cr.execute(
                'SELECT id FROM quality_check WHERE id = %s FOR UPDATE',
                [check.id],
            )
            check.invalidate_recordset([
                'measurement_matrix_required', 'measurement_column_ids',
                'equipment_selection_ids', 'sample_ids',
            ])
        super()._initialize_measurement_snapshot()
        self._ensure_in_process_measurement_samples()

    def initialize_in_process_measurement_matrix(self):
        """Retry after qty_producing becomes available; preserve existing rows."""
        self.ensure_one()
        self._ensure_measurement_editable()
        if self.x_control_stage != 'in_process':
            raise UserError(_('Автогенерація доступна лише для міжопераційного контролю.'))
        self._ensure_in_process_measurement_samples()
        return self.get_measurement_matrix_data()

    def action_initialize_measurement_matrix(self):
        if self.x_control_stage != 'in_process':
            return super().action_initialize_measurement_matrix()
        self.ensure_one()
        self._ensure_measurement_editable()
        if not self.measurement_matrix_required:
            super().action_initialize_measurement_matrix()
        self._ensure_in_process_measurement_samples()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def _add_measurement_samples(self, count):
        if self.x_control_stage == 'in_process':
            raise UserError(_('Зразки міжопераційного контролю формуються автоматично. Ручне додавання заборонено.'))
        return super()._add_measurement_samples(count)

    def _remove_measurement_samples(self, count):
        if self.x_control_stage == 'in_process':
            raise UserError(_('Ручне видалення зразків міжопераційного контролю заборонено.'))
        return super()._remove_measurement_samples(count)

    @api.depends(_batch_dependencies)
    def _compute_measurement_matrix_state(self):
        super()._compute_measurement_matrix_state()
        for check in self.filtered(lambda check: check.x_control_stage == 'in_process'):
            if check._get_in_process_batch_error():
                check.measurement_matrix_complete = False
                check.can_pass_measurement_check = False

    def get_measurement_matrix_data(self):
        data = super().get_measurement_matrix_data()
        data['control_stage'] = self.x_control_stage or 'incoming'
        if self.x_control_stage == 'in_process':
            data['batch_quantity'] = self.in_process_sample_quantity
            data['batch_error'] = self._get_in_process_batch_error()
        return data

    def _validate_measurement_can_pass(self):
        in_process = self.filtered(lambda check: check.x_control_stage == 'in_process')
        for check in in_process:
            error = check._get_in_process_batch_error(validate_current=True)
            if error:
                raise ValidationError(error)
        result = super()._validate_measurement_can_pass()
        for check in in_process:
            if any(sample.sample_result != 'pass' for sample in check.sample_ids):
                raise ValidationError(_('Усі зразки міжопераційного контролю повинні мати результат PASS.'))
        return result
