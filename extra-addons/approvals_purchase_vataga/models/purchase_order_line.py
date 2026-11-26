from odoo import api, models

from ..internal import INTERNAL
from ..rfq_scope import current_scope


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        if current_scope(self.env.cr) is None:
            return super().create(vals_list)
        # Fail closed if an extension bypasses native PO searches via browse.
        order_ids = [vals.get('order_id') or self.env.context.get('default_order_id')
                     for vals in vals_list]
        self.env['purchase.order'].browse([oid for oid in order_ids if oid])._vataga_assert_new_order_scope()
        lines = super().create(vals_list)
        lines.order_id._vataga_assert_new_order_scope()
        return lines

    def write(self, vals):
        if current_scope(self.env.cr) is None:
            return super().write(vals)
        self.order_id._vataga_assert_new_order_scope()
        if vals.get('order_id'):
            self.env['purchase.order'].browse(vals['order_id'])._vataga_assert_new_order_scope()
        result = super().write(vals)
        self.order_id._vataga_assert_new_order_scope()
        return result

    def unlink(self):
        if current_scope(self.env.cr) is not None:
            self.order_id._vataga_assert_new_order_scope()
        self.check_access_rights('unlink')
        self.check_access_rule('unlink')
        # Limited elevation for a denial/audit invariant only. This neither
        # exposes a request nor approves it. Legacy links must leave a durable
        # tombstone BEFORE their FK is cleared by deletion of a draft RFQ.
        lines = self.env['approval.product.line'].sudo().search([
            ('purchase_order_line_id', 'in', self.ids),
            ('approval_request_id.approval_type', '=', 'purchase'),
        ])
        requests = lines.approval_request_id
        if requests:
            requests._vataga_lock()
            requests.filtered(lambda r: not r.vataga_rfq_created).with_context(
                _vataga_internal=INTERNAL).write({'vataga_rfq_created': True})
        return super().unlink()
