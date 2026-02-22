from odoo import api, models


class Contract(models.Model):
    _inherit = 'l10n_ua.contract'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        ctx = self._context
        if 'order_display' in ctx:
            order = ctx['order_display']
        return super()._search(
            domain, offset=offset, limit=limit, order=order, access_rights_uid=None
        )
