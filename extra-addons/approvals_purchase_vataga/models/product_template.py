from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from ..policy import valid_product_url


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vataga_purchase_url = fields.Char(string='URL закупівлі', copy=False)

    @api.constrains('vataga_purchase_url')
    def _check_vataga_purchase_url(self):
        if any(p.vataga_purchase_url and not valid_product_url(p.vataga_purchase_url) for p in self):
            raise ValidationError(_('URL закупівлі має містити коректне посилання HTTP або HTTPS.'))
