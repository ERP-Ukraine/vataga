from odoo import models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()

        if self.account_date:
            vals['date'] = self.account_date
        else:
            vals.pop('date', None)

        return vals
