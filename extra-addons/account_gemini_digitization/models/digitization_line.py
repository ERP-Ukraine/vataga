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
    APPLY_ACTION_SELECTION = [
        ('create_line', 'Create Invoice Line'),
        ('merge_into', 'Merge Into Another Line'),
        ('skip', 'Skip'),
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
    price_unit_without_tax = fields.Float(
        string='Price Without Tax',
    )
    price_unit_with_tax = fields.Float(
        string='Price With Tax',
    )
    price_unit = fields.Float()
    tax_rate = fields.Float()
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string='Taxes',
    )
    line_subtotal_without_tax = fields.Monetary(
        string='Subtotal Without Tax',
        currency_field='currency_id',
    )
    line_tax_amount = fields.Monetary(
        string='Line Tax Amount',
        currency_field='currency_id',
    )
    line_total_with_tax = fields.Monetary(
        string='Total With Tax',
        currency_field='currency_id',
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
    candidate_product_ids = fields.Many2many(
        comodel_name='product.product',
        relation='account_gemini_digitization_line_product_candidate_rel',
        column1='line_id',
        column2='product_id',
        string='Product Candidates',
    )
    match_status = fields.Selection(
        selection=MATCH_STATUS_SELECTION,
        required=True,
        default='draft',
    )
    match_score = fields.Float()
    match_method = fields.Char()
    match_summary = fields.Char(
        string='Match Summary',
        copy=False,
    )
    move_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string='Vendor Bill Line',
        ondelete='set null',
    )
    candidate_move_line_ids = fields.Many2many(
        comodel_name='account.move.line',
        relation='account_gemini_digitization_line_move_line_candidate_rel',
        column1='line_id',
        column2='move_line_id',
        string='Vendor Bill Line Candidates',
    )
    apply_action = fields.Selection(
        selection=APPLY_ACTION_SELECTION,
        default='create_line',
        copy=False,
    )
    merge_target_line_id = fields.Many2one(
        comodel_name='account.gemini.digitization.line',
        string='Merge Target OCR Line',
        ondelete='set null',
        copy=False,
    )
    source_columns = fields.Text()
    note = fields.Text()
    match_note = fields.Text()
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
