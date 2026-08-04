from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    _QUALITY_ARRIVAL_FIELDS = frozenset({
        'quality_arrival_confirmed',
        'quality_arrival_confirmed_at',
        'quality_arrival_confirmed_by_id',
    })

    quality_arrival_confirmed = fields.Boolean(
        string='Фактичне надходження підтверджено',
        readonly=True,
        copy=False,
    )
    quality_arrival_confirmed_at = fields.Datetime(
        string='Дата фактичного надходження',
        readonly=True,
        copy=False,
    )
    quality_arrival_confirmed_by_id = fields.Many2one(
        'res.users',
        string='Фактичне надходження підтвердив',
        readonly=True,
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any(self._QUALITY_ARRIVAL_FIELDS & set(vals) for vals in vals_list):
            raise UserError(_(
                'Дані фактичного надходження встановлюються лише дією '
                '«Товар фактично надійшов».',
            ))
        return super().create(vals_list)

    def write(self, vals):
        if self._QUALITY_ARRIVAL_FIELDS & set(vals):
            raise UserError(_(
                'Дані фактичного надходження встановлюються лише дією '
                '«Товар фактично надійшов».',
            ))
        return super().write(vals)

    def _quality_arrival_notification(self, message, notification_type):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Фактичне надходження'),
                'message': message,
                'type': notification_type,
                'sticky': False,
            },
        }

    def _schedule_quality_arrival_activity(self, check, recipient):
        self.ensure_one()
        activity_type = self.env.ref(
            'quality_vataga.mail_activity_type_quality_arrival_inspection',
        )
        activity_model = self.env['mail.activity']
        quality_check_model = self.env['ir.model']._get('quality.check')
        existing_activity = activity_model.sudo().search([
            ('res_model_id', '=', quality_check_model.id),
            ('res_id', '=', check.id),
            ('activity_type_id', '=', activity_type.id),
            ('user_id', '=', recipient.id),
        ], limit=1)
        if existing_activity:
            return existing_activity

        # The caller has write access to the fixed picking and the check is
        # reached only through picking.check_ids. Elevation is limited to
        # creating this dedicated activity for the resolved inspector.
        return activity_model.sudo().create({
            'res_model_id': quality_check_model.id,
            'res_id': check.id,
            'activity_type_id': activity_type.id,
            'user_id': recipient.id,
            'automated': True,
            'date_deadline': fields.Date.context_today(check),
            'summary': _('Провести перевірку якості'),
            'note': _(
                'Товар фактично надійшов. Перевірка якості готова до '
                'виконання.',
            ),
        })

    def action_confirm_quality_arrival(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')

        self.env.cr.execute(
            'SELECT id FROM stock_picking WHERE id = %s FOR UPDATE',
            [self.id],
        )
        self.invalidate_recordset([
            'quality_arrival_confirmed',
            'quality_arrival_confirmed_at',
            'quality_arrival_confirmed_by_id',
            'picking_type_id',
            'picking_type_code',
            'state',
        ])

        if self.picking_type_id.code != 'incoming':
            raise UserError(_(
                'Фактичне надходження можна підтвердити лише для вхідного '
                'складського переміщення.',
            ))
        if self.state in ('done', 'cancel'):
            raise UserError(_(
                'Фактичне надходження не можна підтвердити для завершеного '
                'або скасованого складського переміщення.',
            ))
        if self.quality_arrival_confirmed:
            return self._quality_arrival_notification(
                _('Фактичне надходження вже було підтверджено.'),
                'info',
            )

        confirmed_at = fields.Datetime.now()
        # This is the only write path for the protected arrival fields.
        super(StockPicking, self).write({
            'quality_arrival_confirmed': True,
            'quality_arrival_confirmed_at': confirmed_at,
            'quality_arrival_confirmed_by_id': self.env.user.id,
        })

        open_checks = self.check_ids.filtered(
            lambda check: check.quality_state == 'none',
        )
        author_id = self.env.user.partner_id.id
        self.message_post(body=_(
            'Фактичне надходження товару підтверджено користувачем '
            '%(user)s. Пов’язані перевірки якості готові до виконання.',
            user=self.env.user.display_name,
        ))

        checks_without_recipient = self.env['quality.check']
        for check in open_checks:
            # The records are fixed by the standard picking/check relation.
            # sudo is limited to chatter and the dedicated activity because a
            # warehouse user may not have quality-check write ACLs.
            check.sudo().message_post(
                author_id=author_id,
                body=_(
                    'Товар фактично надійшов. Перевірка готова до '
                    'виконання.',
                ),
            )
            recipient = check.sudo()._get_quality_arrival_inspector()
            if recipient:
                self._schedule_quality_arrival_activity(check, recipient)
                continue

            checks_without_recipient |= check
            check.sudo().message_post(
                author_id=author_id,
                body=_(
                    'Фактичне надходження підтверджено, але '
                    'відповідального інспектора не визначено.',
                ),
            )

        if checks_without_recipient:
            self.message_post(body=_(
                'Фактичне надходження підтверджено, але для %(count)s '
                'перевірок відповідального інспектора не визначено.',
                count=len(checks_without_recipient),
            ))
            return self._quality_arrival_notification(
                _(
                    'Надходження підтверджено, але для %(count)s перевірок '
                    'не визначено відповідального інспектора.',
                    count=len(checks_without_recipient),
                ),
                'warning',
            )

        return self._quality_arrival_notification(
            _('Надходження підтверджено. Перевірки якості готові.'),
            'success',
        )
