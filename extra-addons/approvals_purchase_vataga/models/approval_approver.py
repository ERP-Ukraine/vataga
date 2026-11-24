from odoo import api, models, _
from odoo.exceptions import AccessError, UserError

from ..internal import WORKFLOW


class ApprovalApprover(models.Model):
    _inherit = 'approval.approver'

    def _vataga_guard_mutation(self, requests, vals, creating=False):
        purchases = requests.filtered(lambda r: r._vataga_is_purchase())
        if not purchases:
            return
        purchases._vataga_lock()
        if self.env.context.get('_vataga_workflow') is WORKFLOW:
            return
        purchases._vataga_check_access('write')
        if any(r._vataga_is_locked() for r in purchases):
            raise UserError(_('Змінювати погоджувачів надісланої або обробленої закупочної заявки заборонено.'))
        if vals.get('status', 'new') not in (False, 'new'):
            raise AccessError(_('Статус погоджувача змінюють лише стандартні дії погодження заявки.'))
        if not creating and set(vals).intersection({'category_approver', 'can_edit', 'can_edit_user_id'}):
            raise AccessError(_('Службові права погоджувача не можна змінювати вручну.'))

    @api.model_create_multi
    def create(self, vals_list):
        defaults = self.default_get(['request_id', 'status'])
        requests = self.env['approval.request'].browse(sorted({
            vals.get('request_id', defaults.get('request_id')) for vals in vals_list
            if vals.get('request_id', defaults.get('request_id'))
        }))
        requests.filtered(lambda r: r._vataga_is_purchase())._vataga_lock()
        prepared = []
        for vals in vals_list:
            vals = dict(vals)
            request = self.env['approval.request'].browse(vals.get('request_id', defaults.get('request_id')))
            effective = dict(defaults, **vals)
            self._vataga_guard_mutation(request, effective, creating=True)
            if request and request._vataga_is_purchase() and self.env.context.get('_vataga_workflow') is not WORKFLOW:
                vals['status'] = 'new'
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        requests = self.request_id
        if vals.get('request_id'):
            requests |= self.env['approval.request'].browse(vals['request_id'])
        self._vataga_guard_mutation(requests, vals)
        return super().write(vals)

    def unlink(self):
        self._vataga_guard_mutation(self.request_id, {})
        return super().unlink()
