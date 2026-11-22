"""Only fields needed by the unchanged guard; no Enterprise process is simulated."""
from odoo import fields, models


class CaseDeclaration(models.Model):
    _name = 'quality.alert'
    _description = 'Disposable case declaration, NOT Enterprise Quality Alert'

    name = fields.Char()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    production_case_mo_id = fields.Many2one('mrp.production')


class ProductionDeclaration(models.Model):
    _inherit = 'mrp.production'

    quality_production_case_enabled = fields.Boolean()
    quality_production_case_ids = fields.One2many('quality.alert', 'production_case_mo_id')
