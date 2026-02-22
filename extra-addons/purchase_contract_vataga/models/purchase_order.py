from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    sale_ua_contract_id = fields.Many2one(
        comodel_name='l10n_ua.contract',
        string='Sale contract',
        domain='''[
            ('contract_type', '=', 'sale'),
        ]''',
    )

    def _prepare_invoice(self):
        self.ensure_one()
        vals = super()._prepare_invoice()

        if self.sale_ua_contract_id:
            vals['sale_ua_contract_id'] = self.sale_ua_contract_id.id

        return vals
