from odoo import fields, models


class SupplierReliabilityRating(models.Model):
    _name = "supplier.reliability.rating"
    _description = "Supplier Reliability Rating"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    css_class = fields.Char(required=True)
    marker = fields.Char(required=True)
