from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


APPROVAL_GROUP = 'quality_vataga.group_quality_board_approval'
AUDIT_FIELDS = frozenset({'is_approved', 'approved_by_id', 'approved_at'})
BOARD_STAGE_XMLIDS = (
    'quality_vataga.quality_alert_stage_board_analysis',
    'quality_vataga.quality_alert_stage_board_approval',
    'quality_vataga.quality_alert_stage_board_execution',
)


class QualityAlert(models.Model):
    _inherit = 'quality.alert'

    stage_id = fields.Many2one(
        readonly=True,
        default=lambda self: self.env.ref(BOARD_STAGE_XMLIDS[0]),
        domain=lambda self: [('id', 'in', self._board_stages().ids)],
        group_expand='_expand_board_stages',
    )

    @api.model
    def _board_stages(self):
        return self.env['quality.alert.stage'].browse([
            self.env.ref(xmlid).id for xmlid in BOARD_STAGE_XMLIDS
        ])

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
    is_board_approval_stage = fields.Boolean(compute='_compute_board_approval_stage')
    is_board_analysis_stage = fields.Boolean(compute='_compute_board_approval_stage')
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
        for alert in self:
            alert.is_board_approval_stage = bool(stage and alert.stage_id == stage)
            alert.is_board_analysis_stage = alert.stage_id == analysis

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
        execution = self.env.ref('quality_vataga.quality_alert_stage_board_execution', False)
        approval = self.env.ref('quality_vataga.quality_alert_stage_board_approval', False)
        missing = [name for name in ('stage_id', 'check_id', 'description')
                   if any(name not in vals for vals in vals_list)]
        defaults = self.default_get(missing) if missing else {}
        for vals in vals_list:
            if any(vals.get(name) for name in AUDIT_FIELDS):
                raise AccessError(_('Затвердження можливе лише кнопкою «Затвердити рішення».'))
            values = dict(vals, is_approved=False, approved_by_id=False, approved_at=False)
            stage_id = values.get('stage_id', defaults.get('stage_id'))
            if stage_id and stage_id in (execution.id if execution else False,
                                         approval.id if approval else False):
                raise UserError(_('Створіть сповіщення на стадії «Аналіз та обговорення».'))
            check_id = values.get('check_id', defaults.get('check_id'))
            if check_id:
                check = self.env['quality.check'].browse(check_id)
                if check.quality_state == 'fail':
                    failures = check._get_technical_board_failure_description()
                    description = values.get('description', defaults.get('description')) or ''
                    if failures and str(failures) not in description:
                        # Prepare before creation: create permission must not
                        # implicitly require write permission on the new alert.
                        values['description'] = Markup(description) + failures
            prepared.append(values)
        return super().create(prepared)

    def _lock_board_records(self):
        self.check_access_rights('write')
        self.check_access_rule('write')
        self.flush_recordset()
        if self.ids:
            self.env.cr.execute(
                'SELECT id FROM quality_alert WHERE id IN %s ORDER BY id FOR UPDATE',
                [tuple(self.ids)],
            )
            self.invalidate_recordset()

    def write(self, vals):
        if AUDIT_FIELDS.intersection(vals):
            raise AccessError(_('Поля затвердження не можна змінювати вручну.'))
        if {'decision_type', 'approval_user_id', 'stage_id'}.intersection(vals):
            self._lock_board_records()
            if 'stage_id' in vals and any(
                alert.stage_id.id != vals['stage_id'] for alert in self
            ):
                raise UserError(_(
                    'Стадію можна змінити лише діями «Передати на затвердження» '
                    'та «Затвердити рішення».',
                ))
            for alert in self:
                if alert.is_approved and (
                    ('decision_type' in vals and vals['decision_type'] != alert.decision_type)
                    or ('approval_user_id' in vals and vals['approval_user_id'] != alert.approval_user_id.id)
                ):
                    raise UserError(_('Затверджене рішення та затверджуючого не можна змінювати.'))
        return super().write(vals)

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
