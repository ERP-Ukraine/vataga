import math

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.float_utils import float_compare


APPROVAL_GROUP = 'quality_vataga.group_quality_board_approval'
AUDIT_FIELDS = frozenset({'is_approved', 'approved_by_id', 'approved_at', 'vataga_closed_by_id', 'vataga_closed_at'})
NEW_STAGE_XMLID = 'quality_vataga.quality_alert_stage_new'
CLOSED_STAGE_XMLID = 'quality_vataga.quality_alert_stage_closed'
BOARD_STAGE_XMLIDS = (
    'quality_vataga.quality_alert_stage_board_analysis',
    'quality_vataga.quality_alert_stage_board_approval',
    'quality_vataga.quality_alert_stage_board_execution',
)
WORKFLOW_STAGE_XMLIDS = (NEW_STAGE_XMLID, *BOARD_STAGE_XMLIDS, CLOSED_STAGE_XMLID)


class QualityAlert(models.Model):
    _inherit = 'quality.alert'

    x_sorting_parameter = fields.Text(
        string='Параметри невідповідності', copy=False,
    )
    vataga_matrix_failure_snapshot = fields.Text(
        string='Невідповідності на момент створення', readonly=True, copy=False,
    )

    stage_id = fields.Many2one(
        default=lambda self: self.env.ref(NEW_STAGE_XMLID), copy=False,
        domain=lambda self: [('id', 'in', self._board_stages().ids)],
        group_expand='_expand_board_stages',
    )

    @api.model
    def _board_stages(self):
        return self.env['quality.alert.stage'].browse([
            self.env.ref(xmlid).id for xmlid in WORKFLOW_STAGE_XMLIDS
        ])

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if 'stage_id' in names:
            # Kanban/action defaults cannot bypass the initial workflow step.
            values['stage_id'] = self.env.ref(NEW_STAGE_XMLID).id
        return values

    @api.model
    def _expand_board_stages(self, stages, domain, order):
        return self.env['quality.alert.stage'].search(
            [('id', 'in', self._board_stages().ids)], order=order,
        )

    def _read_group_fill_results(self, domain, groupby, annoted_aggregates,
                                 read_group_result, read_group_order=None):
        result = super()._read_group_fill_results(
            domain, groupby, annoted_aggregates, read_group_result, read_group_order,
        )
        if groupby == 'stage_id':
            # Odoo appends populated legacy groups even when group_expand
            # excludes them. Keep historical cards untouched, but hide those
            # columns in this workflow (cards remain accessible in list views).
            board_ids = set(self._board_stages().ids)
            result = [row for row in result
                      if row[groupby] and row[groupby].id in board_ids]
        return result

    decision_type = fields.Selection([
        ('accept_as_is', 'Прийняти як є'),
        ('full_control', 'Суцільний контроль'),
        ('repair', 'Ремонт/Доопрацювання'),
        ('return_supplier', 'Повернути постачальнику'),
    ], string='Рішення Технічної ради', tracking=True, copy=False)
    approval_user_id = fields.Many2one(
        'res.users', string='Затверджуючий', tracking=True, copy=False,
        domain=lambda self: [('groups_id', 'in', self.env.ref(APPROVAL_GROUP).ids)],
    )
    is_approved = fields.Boolean(string='Затверджено', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Затвердив', readonly=True, copy=False,
    )
    approved_at = fields.Datetime(string='Дата затвердження', readonly=True, copy=False)
    vataga_closed_by_id = fields.Many2one('res.users', string='Закрив', readonly=True, copy=False, ondelete='restrict')
    vataga_closed_at = fields.Datetime(string='Дата закриття', readonly=True, copy=False)
    is_new_stage = fields.Boolean(compute='_compute_board_approval_stage')
    is_board_approval_stage = fields.Boolean(compute='_compute_board_approval_stage')
    is_board_analysis_stage = fields.Boolean(compute='_compute_board_approval_stage')
    is_board_execution_stage = fields.Boolean(compute='_compute_board_approval_stage')
    full_control_line_ids = fields.One2many(
        'quality.alert.full.control.line', 'alert_id', string='Суцільний контроль',
        copy=False,
    )
    full_control_checked_qty = fields.Float(
        string='Всього перевірено', compute='_compute_full_control_totals',
        digits='Product Unit of Measure',
    )
    full_control_good_qty = fields.Float(
        string='Всього придатних', compute='_compute_full_control_totals',
        digits='Product Unit of Measure',
    )
    full_control_bad_qty = fields.Float(
        string='Всього браку', compute='_compute_full_control_totals',
        digits='Product Unit of Measure',
    )

    @api.depends('stage_id')
    def _compute_board_approval_stage(self):
        stage = self.env.ref('quality_vataga.quality_alert_stage_board_approval')
        analysis = self.env.ref('quality_vataga.quality_alert_stage_board_analysis')
        new = self.env.ref(NEW_STAGE_XMLID)
        execution = self.env.ref(BOARD_STAGE_XMLIDS[2])
        for alert in self:
            alert.is_new_stage = alert.stage_id == new
            alert.is_board_approval_stage = bool(stage and alert.stage_id == stage)
            alert.is_board_analysis_stage = alert.stage_id == analysis
            alert.is_board_execution_stage = alert.stage_id == execution

    @api.depends('full_control_line_ids.checked_qty', 'full_control_line_ids.good_qty',
                 'full_control_line_ids.bad_qty')
    def _compute_full_control_totals(self):
        for alert in self:
            alert.full_control_checked_qty = sum(alert.full_control_line_ids.mapped('checked_qty'))
            alert.full_control_good_qty = sum(alert.full_control_line_ids.mapped('good_qty'))
            alert.full_control_bad_qty = sum(alert.full_control_line_ids.mapped('bad_qty'))

    @api.model_create_multi
    def create(self, vals_list):
        prepared = []
        new = self.env.ref(NEW_STAGE_XMLID)
        missing = [name for name in ('check_id', 'description', 'x_sorting_parameter')
                   if any(name not in vals for vals in vals_list)]
        defaults = self.default_get(missing) if missing else {}
        for vals in vals_list:
            if 'vataga_matrix_failure_snapshot' in vals:
                raise UserError(_('Snapshot невідповідностей формується лише системою.'))
            if any(vals.get(name) for name in AUDIT_FIELDS):
                raise AccessError(_('Поля аудиту встановлюють лише відповідні дії затвердження та закриття.'))
            # Normalize explicit stage_id as well as context defaults. Native
            # QC/team creation must succeed without skipping the initial step.
            values = dict(
                vals, is_approved=False, approved_by_id=False, approved_at=False,
                vataga_closed_by_id=False, vataga_closed_at=False, stage_id=new.id,
                vataga_matrix_failure_snapshot=False,
            )
            check_id = values.get('check_id', defaults.get('check_id'))
            if check_id:
                check = self.env['quality.check'].browse(check_id)
                in_process = check.x_control_stage == 'in_process'
                if in_process:
                    failure_data = check._get_current_matrix_failure_data()
                    values['vataga_matrix_failure_snapshot'] = check._format_matrix_failure_data(
                        failure_data,
                    ) or False
                    values['x_sorting_parameter'] = self._append_matrix_failure_text(
                        values.get('x_sorting_parameter', defaults.get('x_sorting_parameter')),
                        [check._format_matrix_failure_data([row]) for row in failure_data],
                    )
                if in_process or check.quality_state == 'fail':
                    failures = check._get_technical_board_failure_description()
                    description = values.get('description', defaults.get('description')) or ''
                    if failures and str(failures) not in description:
                        # Prepare before creation: create permission must not
                        # implicitly require write permission on the new alert.
                        values['description'] = Markup(description) + failures
            prepared.append(values)
        return super().create(prepared)

    @api.model
    def _append_matrix_failure_text(self, manual_text, failure_lines):
        text = str(manual_text) if manual_text is not False and manual_text is not None else ''
        for line in failure_lines:
            if line and line not in text:
                separator = '\n' if text and not text.endswith('\n') else ''
                text += separator + line
        return text or False

    def _lock_board_records(self):
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.flush_recordset()
        if self.ids:
            # Also version the row: a waiting REPEATABLE READ transaction
            # must retry instead of closing against stale full-control lines.
            # Values and audit dates are unchanged by this serialization lock.
            for alert_id in sorted(self.ids):
                self.env.cr.execute('UPDATE quality_alert SET stage_id=stage_id WHERE id=%s', [alert_id])
            self.invalidate_recordset()

    def write(self, vals):
        if 'vataga_matrix_failure_snapshot' in vals:
            raise UserError(_('Snapshot невідповідностей не можна змінювати вручну.'))
        if AUDIT_FIELDS.intersection(vals):
            raise AccessError(_('Поля аудиту не можна змінювати вручну.'))
        source_fields = {'check_id', 'production_id', 'workorder_id'}
        if ({'decision_type', 'approval_user_id', 'stage_id'} | source_fields).intersection(vals):
            self._lock_board_records()
            if 'stage_id' in vals and any(
                alert.stage_id.id != vals['stage_id'] for alert in self
            ):
                raise UserError(_(
                    'Стадію можна змінити лише відповідними діями процесу: '
                    'аналіз, затвердження рішення та закриття.',
                ))
            for alert in self:
                if alert.vataga_closed_at and any(name in vals and name in alert._fields
                        and alert[name].id != vals[name] for name in source_fields):
                    raise UserError(_('Вихідні документи закритого сповіщення не переприв’язують.'))
                if alert.is_approved and (
                    ('decision_type' in vals and vals['decision_type'] != alert.decision_type)
                    or ('approval_user_id' in vals and vals['approval_user_id'] != alert.approval_user_id.id)
                ):
                    raise UserError(_('Затверджене рішення та затверджуючого не можна змінювати.'))
        return super().write(vals)

    def unlink(self):
        if any(alert.vataga_closed_at for alert in self):
            raise UserError(_('Закриті сповіщення зберігають аудит та не видаляються.'))
        return super().unlink()

    def action_send_to_analysis(self):
        self.ensure_one()
        self._lock_board_records()
        if self.stage_id != self.env.ref(NEW_STAGE_XMLID) or self.is_approved:
            raise UserError(_('Передати на аналіз можна лише зі стадії «Нове».'))
        super(QualityAlert, self).write({'stage_id': self.env.ref(BOARD_STAGE_XMLIDS[0]).id})
        self.message_post(body=_('Сповіщення передано на аналіз та обговорення.'), subtype_xmlid='mail.mt_note')
        return True

    def _is_managed_closed(self):
        self.ensure_one()
        closed = self.env.ref(CLOSED_STAGE_XMLID, False)
        return bool(closed and self.stage_id == closed and self.vataga_closed_by_id and self.vataga_closed_at
                    and self._production_resolution_complete())

    def _production_resolution_complete(self):
        self.ensure_one()
        return not self._requires_production_resolution()

    def _validate_production_resolution(self):
        self.ensure_one()
        raise UserError(_('Міжопераційний Alert потребує підтвердженої процедури етапу 13.4. PASS або рішення ТР не замінюють виконання ремонту / списання.'))

    def _get_alert_closure_values(self):
        self.ensure_one()
        return {}

    def _requires_production_resolution(self):
        self.ensure_one()
        if (self.check_id.production_id or self.check_id.x_control_stage == 'in_process'
                or self.vataga_matrix_failure_snapshot or self.vataga_failure_sample_ids):
            return True
        for name, model in (('production_id', 'mrp.production'), ('workorder_id', 'mrp.workorder')):
            field = self._fields.get(name)
            if field and getattr(field, 'comodel_name', None) == model and self[name]:
                return True
        return False

    def _validate_alert_closure(self):
        self.ensure_one()
        if (self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2])
                or not self.is_approved or not self.approved_by_id or not self.approved_at):
            raise UserError(_('Закриття можливе лише після затвердження на стадії «Виконання рішення».'))
        if self._requires_production_resolution():
            self._validate_production_resolution()
            return
        if self.decision_type == 'full_control':
            lines = self.full_control_line_ids
            lines._check_quantities()
            expected = self.check_id.operation_product_quantity
            precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
            if (not lines or not math.isfinite(expected) or expected <= 0
                    or not all(math.isfinite(value) for value in (
                        self.full_control_checked_qty, self.full_control_good_qty, self.full_control_bad_qty))
                    or float_compare(self.full_control_checked_qty, expected, precision_digits=precision)
                    or self.full_control_bad_qty):
                raise UserError(_('Для закриття суцільного контролю потрібна повна визначена партія, коректні результати та відсутність неврегульованого браку.'))
        elif self.decision_type != 'accept_as_is':
            raise UserError(_('Для цього рішення ще немає перевірки фактичного виконання. Закриття заборонене, зокрема без підтвердженого повернення / списання.'))

    def action_close_alert(self):
        self.ensure_one()
        if not self.env.user.has_group('quality.group_quality_manager'):
            raise AccessError(_('Закрити сповіщення може лише керівник якості.'))
        self._lock_board_records()
        if self._is_managed_closed():
            return True
        self._validate_alert_closure()
        super(QualityAlert, self).write({
            **self._get_alert_closure_values(),
            'stage_id': self.env.ref(CLOSED_STAGE_XMLID).id,
            'vataga_closed_by_id': self.env.uid, 'vataga_closed_at': fields.Datetime.now(),
        })
        self.message_post(body=_('Сповіщення закрито користувачем %(user)s.', user=self.env.user.display_name), subtype_xmlid='mail.mt_note')
        return True

    def action_send_for_board_approval(self):
        self.ensure_one()
        self._lock_board_records()
        if self.is_approved or self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[0]):
            raise UserError(_('Передати на затвердження можна лише зі стадії «Аналіз та обговорення».'))
        if not self.decision_type:
            raise UserError(_('Оберіть рішення Технічної ради.'))
        if not self.approval_user_id:
            raise UserError(_('Оберіть користувача, який має затвердити рішення.'))
        # Bypass our public stage guard only; the ORM still enforces access.
        super(QualityAlert, self).write({
            'stage_id': self.env.ref(BOARD_STAGE_XMLIDS[1]).id,
        })
        return True

    def action_approve_technical_board(self):
        self.ensure_one()
        if not self.env.user.has_group(APPROVAL_GROUP):
            raise AccessError(_('У вас немає прав для затвердження рішення Технічної ради.'))
        self._lock_board_records()
        if not self.approval_user_id:
            raise UserError(_('Оберіть затверджуючого рішення Технічної ради.'))
        if self.approval_user_id != self.env.user:
            raise AccessError(_('Затвердити рішення може лише призначений затверджуючий.'))
        if self.is_approved:
            return True
        if not self.decision_type:
            raise UserError(_('Оберіть рішення Технічної ради.'))
        if self.stage_id != self.env.ref('quality_vataga.quality_alert_stage_board_approval'):
            raise UserError(_('Рішення можна затвердити лише на стадії «На затвердженні».'))
        execution = self.env.ref('quality_vataga.quality_alert_stage_board_execution')
        # Bypass only our public write guard, never ACLs or record rules.
        super(QualityAlert, self).write({
            'is_approved': True,
            'approved_by_id': self.env.uid,
            'approved_at': fields.Datetime.now(),
            'stage_id': execution.id,
        })
        self.message_post(body=_(
            'Рішення Технічної ради затверджено користувачем %(user)s.',
            user=self.env.user.display_name,
        ))
        return True
