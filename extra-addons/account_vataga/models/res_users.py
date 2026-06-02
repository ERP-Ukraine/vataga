from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        return super(
            ResUsers,
            self.with_context(skip_counterparty_name_validation=True),
        ).create(vals_list)

    def write(self, vals):
        return super(
            ResUsers,
            self.with_context(skip_counterparty_name_validation=True),
        ).write(vals)
