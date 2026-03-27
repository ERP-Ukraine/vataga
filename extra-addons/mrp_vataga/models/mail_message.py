from odoo import fields, models


class MailMessage(models.Model):
    _inherit = 'mail.message'

    is_bom_autolog = fields.Boolean(default=False, index=True, copy=False)
