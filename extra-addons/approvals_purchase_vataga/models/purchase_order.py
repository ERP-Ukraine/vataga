from copy import deepcopy

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from ..rfq_scope import current_scope
from ..internal import PO_APPROVAL


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    vataga_rfq_origin_key = fields.Char(readonly=True, copy=False, index=True)
    vataga_origin_approval_ids = fields.Many2many(
        'approval.request', 'vataga_po_approval_rel', 'order_id', 'request_id',
        string='Джерела погодження', readonly=True, copy=False)
    vataga_approval_request_ids = fields.Many2many(
        'approval.request', compute='_compute_vataga_approvals', compute_sudo=False,
        string='Документ погодження')
    vataga_approval_is_copy = fields.Boolean(readonly=True, copy=False)
    vataga_can_duplicate_with_approval = fields.Boolean(compute='_compute_vataga_approvals')

    @api.depends('vataga_origin_approval_ids', 'order_line')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_vataga_approvals(self):
        requests = self.env['approval.request']
        lines = self.env['approval.product.line'].with_context(vataga_purchase_registry=False)
        can_read = (requests.check_access_rights('read', raise_exception=False)
                    and lines.check_access_rights('read', raise_exception=False))
        can_copy = (self.env.user.has_group('purchase.group_purchase_user')
                    and self.check_access_rights('create', raise_exception=False)
                    and self.env['purchase.order.line'].check_access_rights('create', raise_exception=False))
        for order in self:
            ids = set(order.vataga_origin_approval_ids.ids)
            if can_read and order.id:
                ids.update(lines.search([('purchase_order_line_id.order_id', '=', order.id)]).approval_request_id.ids)
            visible = (requests.search([('id', 'in', list(ids)), ('company_id', 'in', self.env.companies.ids)])
                       if can_read and ids else requests)
            order.vataga_approval_request_ids = visible
            order.vataga_can_duplicate_with_approval = bool(
                order.id and visible and can_copy and order.company_id in self.env.companies)

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        if (view_type == 'form' and not arch.get('js_class')
                and arch.xpath(".//field[@name='vataga_can_duplicate_with_approval']")):
            arch = deepcopy(arch)
            arch.set('js_class', 'vataga_purchase_order_form')
        return arch, view

    def _vataga_check_approval_access(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.company_id not in self.env.companies:
            raise AccessError(_('Закупка належить недоступній компанії.'))

    def action_vataga_open_approval(self, request_id):
        self._vataga_check_approval_access()
        self.invalidate_recordset(['vataga_approval_request_ids'])
        request = self.vataga_approval_request_ids.filtered(lambda r: r.id == request_id)
        if not request:
            raise AccessError(_('Документ погодження недоступний для цієї закупки.'))
        request._vataga_check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': 'approval.request',
                'res_id': request.id, 'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}

    def action_vataga_duplicate_with_approval(self):
        self._vataga_check_approval_access()
        self.invalidate_recordset(['vataga_approval_request_ids', 'vataga_can_duplicate_with_approval'])
        if not self.vataga_can_duplicate_with_approval:
            raise AccessError(_('Недостатньо прав або відсутній доступний документ погодження.'))
        # Native copy handles draft state, numbering and operational relations.
        # Only this explicit action carries the reference, never native line FKs.
        duplicate = self.with_context(_vataga_po_approval=PO_APPROVAL).copy({
            'state': 'draft', 'vataga_origin_approval_ids': [fields.Command.set(self.vataga_approval_request_ids.ids)],
            'vataga_approval_is_copy': True,
        })
        return {'type': 'ir.actions.act_window', 'res_model': 'purchase.order', 'res_id': duplicate.id,
                'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current'}

    @api.model_create_multi
    def create(self, vals_list):
        scope = current_scope(self.env.cr)
        derived = {'vataga_approval_request_ids', 'vataga_can_duplicate_with_approval'}
        if any(derived.intersection(vals) for vals in vals_list):
            raise AccessError(_('Похідні реквізити погодження встановлює лише сервер.'))
        protected = {'vataga_origin_approval_ids', 'vataga_approval_is_copy'}
        if self.env.context.get('_vataga_po_approval') is not PO_APPROVAL and (
                any(protected.intersection(vals) for vals in vals_list)
                or any(self.env.context.get('default_' + name) for name in protected)):
            raise AccessError(_('Зв’язок із погодженням встановлює лише сервер.'))
        if any('vataga_rfq_origin_key' in vals for vals in vals_list) or self.env.context.get('default_vataga_rfq_origin_key'):
            raise AccessError(_('Технічний ключ окремого ЗНКП встановлює лише сервер.'))
        if scope is not None:
            vals_list = [dict(vals, vataga_rfq_origin_key=scope.key,
                             vataga_origin_approval_ids=[fields.Command.set(scope.approval_ids)]) for vals in vals_list]
        orders = super().create(vals_list)
        if scope is not None:
            scope.order_ids.update(orders.ids)
        return orders

    def _vataga_assert_new_order_scope(self):
        scope = current_scope(self.env.cr)
        # The key is already in the inserted parent row while native create()
        # processes order_line commands, before create() returns its IDs.
        if scope is not None and any(order.vataga_rfq_origin_key != scope.key for order in self):
            raise UserError(_('Існуючий ЗНКП не можна змінювати під час формування нової закупки із заявки.'))

    def write(self, vals):
        if {'vataga_origin_approval_ids', 'vataga_approval_is_copy',
                'vataga_approval_request_ids', 'vataga_can_duplicate_with_approval'}.intersection(vals):
            raise AccessError(_('Зв’язок із погодженням змінювати заборонено.'))
        if 'vataga_rfq_origin_key' in vals:
            raise AccessError(_('Технічний ключ окремого ЗНКП змінювати заборонено.'))
        self._vataga_assert_new_order_scope()
        return super().write(vals)

    def unlink(self):
        self._vataga_assert_new_order_scope()
        return super().unlink()
