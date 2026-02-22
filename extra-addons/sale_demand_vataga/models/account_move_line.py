from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    seller_contract_id = fields.Many2one(
        'account.analytic.account', compute='_compute_seller_contract_id', store=True
    )

    @api.depends('analytic_distribution')
    def _compute_seller_contract_id(self):
        for line in self:
            line.seller_contract_id = self.env['account.analytic.account']
            if line.analytic_distribution:
                account_analytics_ids = [
                    analytic_id
                    for key in line.analytic_distribution.keys()
                    for analytic_id in key.split(',')
                ]
                valid_analytic = line.env['account.analytic.account']._read_group(
                    [
                        ('is_plan_seller_contract', '=', True),
                        ('id', 'in', account_analytics_ids),
                    ],
                    ['id'],
                )
                if valid_analytic:
                    line.seller_contract_id = valid_analytic[0][0]
