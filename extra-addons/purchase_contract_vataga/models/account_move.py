from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    sale_ua_contract_id = fields.Many2one(
        comodel_name='l10n_ua.contract',
        domain='''[
            ('contract_type', '=', 'sale'),
        ]''',
        string='Sale contract'
    )
    