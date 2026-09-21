from odoo import fields, models


class MrpEcoBomChange(models.Model):
    _inherit = 'mrp.eco.bom.change'

    vataga_bom_id = fields.Many2one(
        comodel_name='mrp.bom',
        string='Специфікація / виріб',
        related='eco_id.bom_id',
        store=True,
        readonly=True,
        compute_sudo=False,
        index=True,
    )
