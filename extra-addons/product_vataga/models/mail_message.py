from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.osv import expression

from .product_autolog import autolog_subtype_ids


class MailMessage(models.Model):
    _inherit = 'mail.message'

    # The 1.17 rule can still be loaded from a database that has not run -u.
    # Keep the old domain searchable without requiring its former SQL column.
    product_autolog_company_id = fields.Many2one(
        'res.company', compute='_compute_legacy_product_autolog_company',
        search='_search_legacy_product_autolog_company',
    )

    @api.depends('record_company_id')
    def _compute_legacy_product_autolog_company(self):
        for message in self:
            message.product_autolog_company_id = message.record_company_id

    def _search_legacy_product_autolog_company(self, operator, value):
        if operator == '=' and value is False:
            subtype_ids = list(filter(None, autolog_subtype_ids(self.env)))
            return [('subtype_id', 'not in', subtype_ids)]
        # Keep 1.17 history private until migration has transferred its company scope.
        return expression.FALSE_DOMAIN

    def write(self, vals):
        protected = {'record_company_id', 'product_autolog_company_id', 'subtype_id', 'is_internal', 'model', 'res_id'}
        if not self.env.su and protected & vals.keys():
            subtype_ids = list(filter(None, autolog_subtype_ids(self.env)))
            if self.sudo().filtered_domain([('subtype_id', 'in', subtype_ids)]):
                raise AccessError('Змінити доступ до автозапису товару неможливо.')
        return super().write(vals)
