from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

from ..rfq_scope import current_scope


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    vataga_rfq_origin_key = fields.Char(readonly=True, copy=False, index=True)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        scope = current_scope(self.env.cr)
        if scope is not None:
            # Initially empty; subsequent searches may reuse only orders just
            # created for this request, preserving native within-request grouping.
            domain = expression.AND([domain, [('vataga_rfq_origin_key', '=', scope.key)]])
        return super()._search(domain, offset=offset, limit=limit, order=order,
                               access_rights_uid=access_rights_uid)

    @api.model_create_multi
    def create(self, vals_list):
        scope = current_scope(self.env.cr)
        if any('vataga_rfq_origin_key' in vals for vals in vals_list) or self.env.context.get('default_vataga_rfq_origin_key'):
            raise AccessError(_('Технічний ключ окремого ЗНКП встановлює лише сервер.'))
        if scope is not None:
            vals_list = [dict(vals, vataga_rfq_origin_key=scope.key) for vals in vals_list]
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
        if 'vataga_rfq_origin_key' in vals:
            raise AccessError(_('Технічний ключ окремого ЗНКП змінювати заборонено.'))
        self._vataga_assert_new_order_scope()
        return super().write(vals)

    def unlink(self):
        self._vataga_assert_new_order_scope()
        return super().unlink()
