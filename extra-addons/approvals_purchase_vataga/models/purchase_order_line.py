from odoo import models

from ..internal import INTERNAL


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def unlink(self):
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
