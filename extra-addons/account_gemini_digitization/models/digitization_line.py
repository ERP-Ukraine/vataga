from odoo import fields, models


class AccountGeminiDigitizationLine(models.Model):
    _name = 'account.gemini.digitization.line'
    _description = 'Gemini Digitization Line'
    _order = 'job_id, sequence, id'

    MATCH_STATUS_SELECTION = [
        ('draft', 'Draft'),
        ('matched', 'Matched'),
        ('ambiguous', 'Ambiguous'),
        ('not_found', 'Not Found'),
        ('manual', 'Manual'),
        ('error', 'Error'),
    ]

    job_id = fields.Many2one(
        comodel_name='account.gemini.digitization.job',
        required=True,
        index=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(
        default=10,
    )
    supplier_product_code = fields.Char()
    supplier_product_name = fields.Char()
    description = fields.Text()
    quantity = fields.Float()
    uom_name = fields.Char(
        string='UoM Name',
    )
    price_unit = fields.Float()
    tax_rate = fields.Float()
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string='Taxes',
    )
    amount_untaxed = fields.Monetary(
        currency_field='currency_id',
    )
    amount_tax = fields.Monetary(
        currency_field='currency_id',
    )
    amount_total = fields.Monetary(
        currency_field='currency_id',
    )
    confidence = fields.Float()
    matched_product_id = fields.Many2one(
        comodel_name='product.product',
        string='Matched Product',
        ondelete='set null',
    )
    match_status = fields.Selection(
        selection=MATCH_STATUS_SELECTION,
        required=True,
        default='draft',
    )
    match_score = fields.Float()
    match_method = fields.Char()
    move_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string='Vendor Bill Line',
        ondelete='set null',
    )
    note = fields.Text()
    company_id = fields.Many2one(
        related='job_id.company_id',
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related='job_id.currency_id',
        store=True,
        readonly=True,
    )
