from odoo import models, _
from odoo.exceptions import UserError


class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    def write(self, vals):
        if set(vals).intersection({'approval_type', 'automated_sequence'}):
            changing = self.filtered(lambda c: any(
                key in vals and c[key] != vals[key] for key in ('approval_type', 'automated_sequence')))
            # Deliberately conservative: an inaccessible request must not allow
            # a category administrator to remove purchase protections.
            if changing and self.env['approval.request'].sudo().search_count([
                ('category_id', 'in', changing.ids),
                '|', ('approval_type', '=', 'purchase'),
                ('vataga_purchase_managed', '=', True),
            ]):
                raise UserError(_('Не можна змінити тип або спосіб нумерації категорії, що містить закупочні заявки.'))
        return super().write(vals)
