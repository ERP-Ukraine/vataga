from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountGeminiDigitizationReviewWizard(models.TransientModel):
    _name = 'account.gemini.digitization.review.wizard'
    _description = 'Gemini Digitization Review Wizard'

    job_id = fields.Many2one(
        comodel_name='account.gemini.digitization.job',
        required=True,
        readonly=True,
    )
    mode = fields.Selection(
        related='job_id.mode',
        readonly=True,
    )
    recognized_invoice_number = fields.Char(
        related='job_id.recognized_invoice_number',
        readonly=True,
    )
    recognized_invoice_date = fields.Date(
        related='job_id.recognized_invoice_date',
        readonly=True,
    )
    currency_id = fields.Many2one(
        related='job_id.currency_id',
        readonly=True,
    )
    recognized_amount_untaxed = fields.Monetary(
        related='job_id.recognized_amount_untaxed',
        currency_field='currency_id',
        readonly=True,
    )
    recognized_amount_tax = fields.Monetary(
        related='job_id.recognized_amount_tax',
        currency_field='currency_id',
        readonly=True,
    )
    recognized_amount_total = fields.Monetary(
        related='job_id.recognized_amount_total',
        currency_field='currency_id',
        readonly=True,
    )
    line_ids = fields.One2many(
        comodel_name='account.gemini.digitization.review.line.wizard',
        inverse_name='wizard_id',
        string='Review Lines',
    )
    note = fields.Text()

    def action_apply(self):
        raise UserError(_('Applying digitization results is not implemented yet.'))

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}


class AccountGeminiDigitizationReviewLineWizard(models.TransientModel):
    _name = 'account.gemini.digitization.review.line.wizard'
    _description = 'Gemini Digitization Review Line Wizard'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        comodel_name='account.gemini.digitization.review.wizard',
        required=True,
        ondelete='cascade',
    )
    job_line_id = fields.Many2one(
        comodel_name='account.gemini.digitization.line',
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    supplier_product_code = fields.Char()
    supplier_product_name = fields.Char()
    description = fields.Text()
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Product',
    )
    quantity = fields.Float()
    uom_name = fields.Char(string='UoM Name')
    price_unit = fields.Float()
    tax_rate = fields.Float()
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string='Taxes',
    )
    confidence = fields.Float()
    note = fields.Text()
