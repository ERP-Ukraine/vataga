from odoo import fields, models
from odoo.exceptions import AccessError


class MailMessage(models.Model):
    _inherit = 'mail.message'

    product_autolog_company_id = fields.Many2one(
        'res.company', readonly=True, copy=False, index='btree_not_null', ondelete='cascade',
    )

    def write(self, vals):
        if 'product_autolog_company_id' in vals and not self.env.su:
            raise AccessError('Змінити компанію автозапису товару неможливо.')
        return super().write(vals)
