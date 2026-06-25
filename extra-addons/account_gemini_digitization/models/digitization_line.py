from odoo import _, api, fields, models


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
        ('create_line', 'Create Document Line'),
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
    purchase_order_line_id = fields.Many2one(
        comodel_name='purchase.order.line',
        string='Purchase Order Line',
        ondelete='set null',
        copy=False,
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

    @api.model_create_multi
    def create(self, vals_list):
        job_ids = {
            values.get('job_id')
            for values in vals_list
            if values.get('job_id')
        }
        for job in self.env['account.gemini.digitization.job'].browse(job_ids):
            job._check_linked_document_access('write')
        return super().create(vals_list)

    def write(self, values):
        for job in self.mapped('job_id'):
            job._check_linked_document_access('write')
        return super().write(values)

    @property
    def job_line_id(self):
        """Compatibility alias used by the shared Apply implementation."""
        return self

    @api.onchange('move_line_id')
    def _onchange_move_line_id(self):
        for line in self:
            if not line.move_line_id:
                continue
            line.matched_product_id = line.move_line_id.product_id
            line.match_status = 'manual'
            line.match_method = 'manual_move_line'
            line.match_score = 1.0
            line.match_summary = _('Рядок рахунку обрано вручну.')

    @api.onchange('matched_product_id')
    def _onchange_matched_product_id(self):
        for line in self:
            if not line.matched_product_id:
                continue
            if line.job_id.mode == 'partial_bill':
                if line.move_line_id:
                    line.matched_product_id = line.move_line_id.product_id
                    line.match_status = 'manual'
                    line.match_method = 'manual_move_line'
                    line.match_score = 1.0
                    line.match_summary = _('Vendor bill line selected manually.')
                else:
                    line.matched_product_id = False
                    line.match_summary = _('Select a vendor bill line for partial bill matching.')
                continue
            line.match_status = 'manual'
            line.match_method = 'manual_product'
            line.match_score = 1.0
            line.match_summary = _('Matched manually: %s') % (
                line.matched_product_id.display_name
            )

    @api.onchange('tax_ids')
    def _onchange_tax_ids(self):
        for line in self:
            if (
                line.tax_ids
                and line.match_summary
                and str(line.match_summary).startswith('Tax review required:')
            ):
                line.match_summary = _('Tax selected manually')

    @api.onchange('apply_action')
    def _onchange_apply_action(self):
        for line in self:
            if line.apply_action != 'merge_into':
                line.merge_target_line_id = False
            if line.apply_action == 'skip':
                line.match_status = 'manual'
                line.match_method = 'manual_skip'
                line.match_score = 1.0
                if line.job_id.mode == 'full_purchase':
                    line.match_summary = _(
                        'Skipped: will not create a purchase order line'
                    )
                else:
                    line.match_summary = _('Skipped: will not create an invoice line')
            elif line.apply_action == 'merge_into':
                line.match_status = 'manual'
                line.match_method = 'manual_merge'
                line.match_score = line.match_score or 1.0
                line.match_summary = (
                    _('Merged into: %s') % line.merge_target_line_id._display_label()
                    if line.merge_target_line_id
                    else _('Merge: select target OCR line')
                )
            elif (
                line.apply_action == 'create_line'
                and line.match_method in ('manual_skip', 'manual_merge')
            ):
                if line.move_line_id:
                    line.match_status = 'manual'
                    line.match_method = 'manual_move_line'
                    line.match_score = 1.0
                    line.match_summary = _('Matched manually: %s') % (
                        line.move_line_id.display_name
                    )
                elif line.job_id.mode == 'partial_bill':
                    line.matched_product_id = False
                    line.match_status = 'draft'
                    line.match_method = False
                    line.match_score = 0.0
                    line.match_summary = _('Select a vendor bill line for partial bill matching.')
                elif line.matched_product_id:
                    line.match_status = 'manual'
                    line.match_method = 'manual_product'
                    line.match_score = 1.0
                    line.match_summary = _('Matched manually: %s') % (
                        line.matched_product_id.display_name
                    )
                else:
                    line.match_status = 'draft'
                    line.match_method = False
                    line.match_score = 0.0
                    line.match_summary = _('Manual product selection required')

    @api.onchange('merge_target_line_id')
    def _onchange_merge_target_line_id(self):
        for line in self:
            if not line.merge_target_line_id:
                continue
            line.apply_action = 'merge_into'
            line.match_status = 'manual'
            line.match_method = 'manual_merge'
            line.match_score = line.match_score or 1.0
            line.match_summary = _('Merged into: %s') % (
                line.merge_target_line_id._display_label()
            )

    def _is_manual_selection(self):
        self.ensure_one()
        return bool(
            self.move_line_id
            and (
                self.match_status == 'manual'
                or self.match_method == 'manual_move_line'
            )
        )

    def _is_manual_product_selection(self):
        self.ensure_one()
        return bool(
            self.matched_product_id
            and (
                self.match_status == 'manual'
                or self.match_method == 'manual_product'
            )
        )

    def _display_label(self):
        self.ensure_one()
        return self.supplier_product_name or self.description or str(self.sequence)
