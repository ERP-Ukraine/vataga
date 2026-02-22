from odoo import fields, models

CATEGORY_SELECTION = [
    ('required', 'Required'),
    ('optional', 'Optional'),
    ('no', 'None'),
]


class ApprovalCategory(models.Model):
    _inherit = 'approval.category'

    has_sale_contract = fields.Selection(
        CATEGORY_SELECTION, default='no', required=True
    )
