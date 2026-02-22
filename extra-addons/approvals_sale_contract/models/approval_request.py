from odoo import fields, models


class ApprovalRequest(models.Model):
    _inherit = 'approval.request'

    sale_contract_id = fields.Many2one(
        comodel_name='l10n_ua.contract',
        domain="""[
            ('contract_type', '=', 'sale'),
        ]""",
        string='Sale contract',
    )
    has_sale_contract = fields.Selection(related='category_id.has_sale_contract')
