"""Read-only presentation and navigation for the existing incoming ledger.

No stock execution, acceptance policy or permission guard belongs in this file.
Buttons delegate to the original checks and confirmation wizard.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare


class IncomingHoldUI(models.Model):
    _inherit = 'quality.incoming.hold'

    ui_processing_state = fields.Selection([
        ('unconfigured', 'Карантин: немає плану'),
        ('pending', 'Очікує перевірки'),
        ('ready', 'Готово до приймання'),
        ('failure', 'Потрібне рішення щодо браку'),
        ('sorted', 'Суцільний контроль: є результати'),
        ('partial', 'Частково оброблено'),
        ('done', 'Обробку завершено'),
    ], string='Обробка партії', compute='_compute_ui_presentation')
    ui_can_check = fields.Boolean(compute='_compute_ui_presentation')
    ui_can_release = fields.Boolean(compute='_compute_ui_presentation')
    ui_can_return = fields.Boolean(compute='_compute_ui_presentation')
    ui_can_inspect = fields.Boolean(compute='_compute_ui_presentation')
    ui_can_report = fields.Boolean(compute='_compute_ui_presentation')

    @api.depends_context('uid')
    @api.depends('remaining_qty', 'good_qty', 'bad_qty', 'unchecked_qty',
                 'released_qty', 'returned_qty', 'inspection_ids', 'operation_ids',
                 'check_ids.quality_state', 'check_ids.can_pass_measurement_check',
                 'required_point_ids', 'failure_reported_at')
    def _compute_ui_presentation(self):
        quality_user = self.env.user.has_group('quality.group_quality_user')
        stock_user = self.env.user.has_group('stock.group_stock_user')
        for hold in self:
            positive = lambda qty: float_compare(qty, 0, precision_rounding=hold.uom_id.rounding) > 0
            remaining = positive(hold.remaining_qty)
            failed = hold._has_failure()
            passed = hold._sampling_passed()
            hold.ui_can_check = bool(quality_user and remaining and not hold.inspection_ids
                                     and not hold.operation_ids and not failed and not passed
                                     and (not hold.check_ids or any(check.quality_state == 'none' for check in hold.check_ids)))
            hold.ui_can_release = bool(quality_user and stock_user and remaining and (
                positive(hold.good_qty) or (not hold.inspection_ids and passed and positive(hold.unchecked_qty))))
            hold.ui_can_return = bool(stock_user and remaining and failed)
            hold.ui_can_inspect = bool(quality_user and remaining and failed and positive(hold.unchecked_qty))
            hold.ui_can_report = bool(quality_user and remaining and not failed)
            if not remaining:
                state = 'done'
            elif positive(hold.released_qty) or positive(hold.returned_qty):
                state = 'partial'
            elif hold.inspection_ids:
                state = 'sorted'
            elif failed:
                state = 'failure'
            elif passed:
                state = 'ready'
            else:
                state = 'pending' if hold.check_ids else 'unconfigured'
            hold.ui_processing_state = state

    def action_ui_check(self):
        self.ensure_one()
        if not self.ui_can_check:
            raise UserError(_('Перевірка недоступна для поточного стану або ваших прав.'))
        if not self.check_ids and not self.action_start_checks():
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
                'title': _('План вхідного контролю не налаштований'),
                'message': _('Налаштуйте в контрольній точці розмір вибірки та критерії приймання. Партія залишається в карантині.'),
                'type': 'warning', 'sticky': True}}
        checks = self.check_ids.filtered(lambda check: check.quality_state == 'none') or self.check_ids
        action = {'type': 'ir.actions.act_window', 'name': _('Перевірки вхідної партії'),
                  'res_model': 'quality.check', 'view_mode': 'tree,form',
                  'domain': [('id', 'in', checks.ids)]}
        if len(checks) == 1:
            action.update(view_mode='form', res_id=checks.id, target='new')
        return action

    def _ui_decision(self, kind, bucket='unchecked', quantity=0):
        self.ensure_one()
        action = self.action_open_decision()
        action['name'] = {'release': _('Прийняти на склад'), 'return': _('Повернути постачальнику'),
                          'inspect': _('Суцільний контроль'), 'report': _('Зафіксувати невідповідність')}[kind]
        # Pre-fill the transient itself, never default_quantity in the action
        # context: the existing executor propagates its context to Stock, where
        # that default also applies to automatically created move lines.
        wizard = self.env['quality.incoming.decision'].create({
            'hold_id': self.id, 'kind': kind, 'bucket': bucket, 'quantity': quantity,
            'reason': '',
        })
        action['res_id'] = wizard.id
        return action

    def action_ui_release(self):
        self.ensure_one()
        if not self.ui_can_release:
            raise UserError(_('Приймання недоступне для поточного стану або ваших прав.'))
        bucket = 'good' if self.good_qty > 0 else 'unchecked'
        return self._ui_decision('release', bucket, self.good_qty if bucket == 'good' else self.unchecked_qty)

    def action_ui_return(self):
        self.ensure_one()
        if not self.ui_can_return:
            raise UserError(_('Повернення недоступне для поточного стану або ваших прав.'))
        bucket = next(key for key, qty in [('bad', self.bad_qty), ('unchecked', self.unchecked_qty),
                                          ('good', self.good_qty)] if qty > 0)
        return self._ui_decision('return', bucket, getattr(self, bucket + '_qty'))

    def action_ui_inspect(self):
        self.ensure_one()
        if not self.ui_can_inspect:
            raise UserError(_('Суцільний контроль недоступний для поточного стану або ваших прав.'))
        return self._ui_decision('inspect')

    def action_ui_report(self):
        self.ensure_one()
        if not self.ui_can_report:
            raise UserError(_('Фіксація невідповідності недоступна для поточного стану або ваших прав.'))
        return self._ui_decision('report')

    def action_ui_details(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Вхідна партія: результати та історія'),
                'res_model': self._name, 'res_id': self.id, 'view_mode': 'form', 'target': 'new'}


class IncomingPickingUI(models.Model):
    _inherit = 'stock.picking'

    ui_incoming_hold_id = fields.Many2one('quality.incoming.hold', compute='_compute_ui_incoming_hold')
    ui_incoming_product_id = fields.Many2one(related='ui_incoming_hold_id.product_id', compute_sudo=False)
    ui_incoming_lot_id = fields.Many2one(related='ui_incoming_hold_id.lot_id', compute_sudo=False)
    ui_incoming_state = fields.Selection(related='ui_incoming_hold_id.ui_processing_state', compute_sudo=False)
    ui_incoming_quality_state = fields.Selection(related='ui_incoming_hold_id.sampling_state', compute_sudo=False)
    ui_incoming_received_qty = fields.Float(related='ui_incoming_hold_id.received_qty', compute_sudo=False)
    ui_incoming_released_qty = fields.Float(related='ui_incoming_hold_id.released_qty', compute_sudo=False)
    ui_incoming_returned_qty = fields.Float(related='ui_incoming_hold_id.returned_qty', compute_sudo=False)
    ui_incoming_remaining_qty = fields.Float(related='ui_incoming_hold_id.remaining_qty', compute_sudo=False)
    ui_incoming_can_check = fields.Boolean(related='ui_incoming_hold_id.ui_can_check', compute_sudo=False)
    ui_incoming_can_release = fields.Boolean(related='ui_incoming_hold_id.ui_can_release', compute_sudo=False)
    ui_incoming_can_return = fields.Boolean(related='ui_incoming_hold_id.ui_can_return', compute_sudo=False)
    ui_incoming_can_inspect = fields.Boolean(related='ui_incoming_hold_id.ui_can_inspect', compute_sudo=False)
    ui_incoming_can_report = fields.Boolean(related='ui_incoming_hold_id.ui_can_report', compute_sudo=False)

    @api.depends('quality_incoming_hold_ids')
    def _compute_ui_incoming_hold(self):
        for picking in self:
            holds = picking.quality_incoming_hold_ids
            picking.ui_incoming_hold_id = holds if len(holds) == 1 else False

    def _ui_incoming_hold(self):
        self.ensure_one()
        if not self.ui_incoming_hold_id:
            raise UserError(_('Оберіть конкретну партію надходження.'))
        return self.ui_incoming_hold_id

    def action_ui_incoming_check(self):
        return self._ui_incoming_hold().action_ui_check()

    def action_ui_incoming_release(self):
        return self._ui_incoming_hold().action_ui_release()

    def action_ui_incoming_return(self):
        return self._ui_incoming_hold().action_ui_return()

    def action_ui_incoming_inspect(self):
        return self._ui_incoming_hold().action_ui_inspect()

    def action_ui_incoming_report(self):
        return self._ui_incoming_hold().action_ui_report()

    def action_ui_incoming_details(self):
        return self._ui_incoming_hold().action_ui_details()


class IncomingDecisionUI(models.TransientModel):
    _inherit = 'quality.incoming.decision'

    ui_receipt_id = fields.Many2one(related='hold_id.receipt_id', compute_sudo=False)
    ui_product_id = fields.Many2one(related='hold_id.product_id', compute_sudo=False)
    ui_lot_id = fields.Many2one(related='hold_id.lot_id', compute_sudo=False)
