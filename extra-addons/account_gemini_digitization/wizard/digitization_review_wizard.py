from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import AmountValidator


MATCH_STATUS_SELECTION = [
    ('draft', 'Draft'),
    ('matched', 'Matched'),
    ('ambiguous', 'Ambiguous'),
    ('not_found', 'Not Found'),
    ('manual', 'Manual'),
    ('error', 'Error'),
]


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
    move_id = fields.Many2one(
        related='job_id.move_id',
        readonly=True,
    )
    partner_id = fields.Many2one(
        related='job_id.partner_id',
        readonly=True,
    )
    company_id = fields.Many2one(
        related='job_id.company_id',
        readonly=True,
    )
    attachment_id = fields.Many2one(
        related='job_id.attachment_id',
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
    confidence = fields.Float(
        related='job_id.confidence',
        readonly=True,
    )
    line_ids = fields.One2many(
        comodel_name='account.gemini.digitization.review.line.wizard',
        inverse_name='wizard_id',
        string='Review Lines',
    )
    note = fields.Text()

    def action_apply(self):
        self.ensure_one()
        if self.job_id.state == 'done':
            raise UserError(_('This Gemini job has already been applied.'))
        if self.mode == 'partial_bill':
            return self._apply_partial_bill()
        if self.mode == 'full_bill':
            return self._apply_full_bill()
        if self.mode == 'full_purchase':
            raise UserError(_('Застосування для full_purchase буде реалізовано окремо.'))
        raise UserError(_('Unsupported Gemini review mode: %s') % self.mode)

    def _apply_partial_bill(self):
        self.ensure_one()
        if self.mode == 'full_purchase':
            raise UserError(_('Застосування для full_purchase буде реалізовано окремо.'))
        if self.mode != 'partial_bill':
            raise UserError(_('Unsupported Gemini review mode: %s') % self.mode)

        job = self.job_id
        move = job.move_id
        self._validate_partial_bill_apply(job, move)
        self._validate_review_lines()

        existing_line_ids = set(move.invoice_line_ids.ids)
        warnings = []

        header_values = {}
        if self.recognized_invoice_number:
            header_values['ref'] = self.recognized_invoice_number
        if self.recognized_invoice_date:
            header_values['invoice_date'] = self.recognized_invoice_date
        if header_values:
            move.write(header_values)

        for line in self.line_ids.sorted('sequence'):
            line_warnings = self._apply_review_line(line, move)
            warnings.extend(line_warnings)

        if set(move.invoice_line_ids.ids) != existing_line_ids:
            raise UserError(_('Apply must not create or delete vendor bill lines.'))

        move.invalidate_recordset(['amount_untaxed', 'amount_tax', 'amount_total'])
        warnings.extend(AmountValidator(self.env).validate_move_totals(move, job))

        if warnings:
            job.write({
                'state': 'review',
                'error_message': self._format_warnings(warnings),
            })
        else:
            job.write({
                'state': 'done',
                'error_message': False,
            })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Bill'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': move.id,
            'target': 'current',
        }

    def _apply_full_bill(self):
        job = self.job_id
        move = job.move_id
        self._validate_full_bill_apply(job, move)
        self._validate_full_bill_review_lines()

        warnings = []
        header_values = {}
        if self.recognized_invoice_number:
            header_values['ref'] = self.recognized_invoice_number
        if self.recognized_invoice_date:
            header_values['invoice_date'] = self.recognized_invoice_date
        if header_values:
            move.write(header_values)

        planned_lines = []
        commands = []
        existing_line_ids = set(move.invoice_line_ids.ids)
        for line in self.line_ids.sorted('sequence'):
            tax_ids, tax_warning = self._get_line_taxes(line, move)
            if tax_warning:
                warnings.append(tax_warning)
            planned_lines.append((line, tax_ids))
            commands.append((0, 0, self._prepare_full_bill_invoice_line_values(
                line,
                move,
                tax_ids,
            )))

        if commands:
            move.write({'invoice_line_ids': commands})

        created_lines = self.env['account.move.line'].search([
            ('move_id', '=', move.id),
            ('id', 'not in', list(existing_line_ids)),
            ('product_id', '!=', False),
        ], order='id')
        if len(created_lines) != len(planned_lines):
            raise UserError(_(
                'Gemini full bill apply could not safely identify created vendor bill lines.'
            ))

        for created_line, (wizard_line, tax_ids) in zip(created_lines, planned_lines):
            status = wizard_line.match_status
            method = wizard_line.match_method
            score = wizard_line.match_score
            if wizard_line._is_manual_product_selection() or status not in ('matched', 'manual'):
                status = 'manual'
                method = 'manual_product'
                score = score or 1.0
            wizard_line.job_line_id.write({
                'move_line_id': created_line.id,
                'matched_product_id': wizard_line.matched_product_id.id,
                'match_status': status,
                'match_score': score,
                'match_method': method,
                'quantity': wizard_line.quantity,
                'price_unit': wizard_line.price_unit,
                'tax_rate': wizard_line.tax_rate,
                'tax_ids': [(6, 0, tax_ids.ids)] if tax_ids else [(6, 0, [])],
                'amount_untaxed': wizard_line.amount_untaxed,
                'amount_tax': wizard_line.amount_tax,
                'amount_total': wizard_line.amount_total,
                'line_subtotal_without_tax': wizard_line.line_subtotal_without_tax,
                'line_tax_amount': wizard_line.line_tax_amount,
                'line_total_with_tax': wizard_line.line_total_with_tax,
                'note': self._append_text(
                    wizard_line.job_line_id.note,
                    _('Created vendor bill line %s.') % created_line.display_name,
                ),
            })

        move.invalidate_recordset(['amount_untaxed', 'amount_tax', 'amount_total'])
        warnings.extend(AmountValidator(self.env).validate_move_totals(move, job))
        job.write({
            'state': 'done',
            'error_message': self._format_warnings(warnings) if warnings else False,
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Vendor Bill'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': move.id,
            'target': 'current',
        }

    def action_close(self):
        return {'type': 'ir.actions.act_window_close'}

    def _validate_partial_bill_apply(self, job, move):
        if not job or job.state != 'review':
            raise UserError(_('Gemini job must be in Review state before apply.'))
        if not move:
            raise UserError(_('Gemini job is not linked to a vendor bill.'))
        if move.move_type != 'in_invoice':
            raise UserError(_('Gemini partial apply is allowed only for vendor bills.'))
        if move.state != 'draft':
            raise UserError(_('Gemini partial apply is allowed only for draft vendor bills.'))

    def _validate_full_bill_apply(self, job, move):
        if not job or job.state != 'review':
            raise UserError(_('Gemini job must be in Review state before apply.'))
        if not move:
            raise UserError(_('Gemini job is not linked to a vendor bill.'))
        if move.move_type != 'in_invoice':
            raise UserError(_('Gemini full bill apply is allowed only for vendor bills.'))
        if move.state != 'draft':
            raise UserError(_('Gemini full bill apply is allowed only for draft vendor bills.'))
        if not (move.partner_id or job.partner_id):
            raise UserError(_('Спочатку оберіть постачальника в рахунку.'))
        if any(job_line.move_line_id for job_line in job.line_ids):
            raise UserError(_(
                'This Gemini full bill job already has created vendor bill lines and cannot be applied again.'
            ))
        if self._get_move_product_lines(move):
            raise UserError(_(
                'Vendor bill already contains product lines. Use partial bill recognition for existing lines.'
            ))

    def _validate_review_lines(self):
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to apply.'))

        incomplete = []
        invalid = []
        missing_price = []
        for line in self.line_ids:
            label = line._display_label()
            if not line.move_line_id:
                incomplete.append(label)
                continue
            if line.match_status == 'error':
                invalid.append(label)
                continue
            if line.match_status not in ('matched', 'manual') and not line._is_manual_selection():
                incomplete.append(label)
            if not self._is_positive_number(line.price_unit):
                missing_price.append(label)

        if incomplete:
            raise UserError(_(
                'Не всі рядки зіставлено з рядками рахунку. '
                'Перевірте Review. Рядки: %s'
            ) % ', '.join(incomplete))
        if invalid:
            raise UserError(_(
                'Lines with matching errors cannot be applied. Lines: %s'
            ) % ', '.join(invalid))
        if missing_price:
            raise UserError(_(
                'Not all recognized lines have a positive unit price. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_price))

    def _validate_full_bill_review_lines(self):
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to apply.'))

        missing_product = []
        invalid = []
        missing_quantity = []
        missing_price = []
        for line in self.line_ids:
            label = line._display_label()
            if line.match_status == 'error':
                invalid.append(label)
                continue
            if not line.matched_product_id:
                missing_product.append(label)
            if not self._is_positive_number(line.quantity):
                missing_quantity.append(label)
            if not self._is_positive_number(line.price_unit):
                missing_price.append(label)

        if invalid:
            raise UserError(_('Lines with matching errors cannot be applied. Lines: %s') % ', '.join(invalid))
        if missing_product:
            raise UserError(_(
                'Not all recognized lines have an Odoo product selected. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_product))
        if missing_quantity:
            raise UserError(_(
                'Not all recognized lines have a positive quantity. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_quantity))
        if missing_price:
            raise UserError(_(
                'Not all recognized lines have a positive unit price. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_price))

    def _prepare_full_bill_invoice_line_values(self, line, move, tax_ids):
        product = line.matched_product_id
        values = {
            'product_id': product.id,
            'name': self._get_full_bill_line_name(line, product),
            'quantity': line.quantity,
            'price_unit': line.price_unit,
        }
        account = self._get_product_expense_account(product, move)
        if account:
            values['account_id'] = account.id
        uom = getattr(product, 'uom_po_id', False) or getattr(product, 'uom_id', False)
        if uom:
            values['product_uom_id'] = uom.id
        if tax_ids:
            values['tax_ids'] = [(6, 0, tax_ids.ids)]
        return values

    def _get_full_bill_line_name(self, line, product):
        return (
            line.description
            or line.supplier_product_name
            or getattr(product, 'display_name', False)
            or getattr(product, 'name', False)
        )

    def _get_product_expense_account(self, product, move):
        account = (
            getattr(product, 'property_account_expense_id', False)
            or getattr(getattr(product, 'categ_id', False), 'property_account_expense_categ_id', False)
        )
        fiscal_position = getattr(move, 'fiscal_position_id', False)
        if account and fiscal_position and hasattr(fiscal_position, 'map_account'):
            account = fiscal_position.map_account(account)
        return account

    def _get_move_product_lines(self, move):
        invoice_lines = move.invoice_line_ids.filtered(
            lambda line: self._is_move_product_line(line)
        )
        if invoice_lines:
            return invoice_lines
        return move.line_ids.filtered(lambda line: self._is_move_product_line(line))

    def _is_move_product_line(self, line):
        if not line.product_id:
            return False
        display_type = getattr(line, 'display_type', False)
        if display_type and display_type != 'product':
            return False
        account = getattr(line, 'account_id', False)
        account_type = getattr(account, 'account_type', False) if account else False
        if account_type:
            account_type = str(account_type).lower()
            if 'receivable' in account_type or 'payable' in account_type:
                return False
        return True

    def _apply_review_line(self, line, move):
        warnings = []
        move_line = line.move_line_id
        if move_line.move_id != move:
            raise UserError(_(
                'Selected vendor bill line does not belong to the reviewed bill: %s'
            ) % line._display_label())

        matched_product = move_line.product_id
        if line.matched_product_id and line.matched_product_id != matched_product:
            warnings.append(_(
                '%s: selected product differs from the vendor bill line product. '
                'The existing vendor bill product was kept.'
            ) % line._display_label())

        tax_ids, tax_warning = self._get_line_taxes(line, move)
        if tax_warning:
            warnings.append(tax_warning)

        values = {
            'price_unit': line.price_unit,
        }
        if tax_ids:
            values['tax_ids'] = [(6, 0, tax_ids.ids)]
        move_line.write(values)

        status = line.match_status
        method = line.match_method
        if line._is_manual_selection() or status not in ('matched', 'manual'):
            status = 'manual'
            method = method or 'manual_move_line'
        match_score = line.match_score
        if status == 'manual' and not match_score:
            match_score = 1.0

        line.job_line_id.write({
            'move_line_id': move_line.id,
            'matched_product_id': matched_product.id,
            'match_status': status,
            'match_score': match_score,
            'match_method': method,
            'price_unit': line.price_unit,
            'tax_rate': line.tax_rate,
            'tax_ids': [(6, 0, tax_ids.ids)] if tax_ids else [(6, 0, [])],
            'amount_untaxed': line.amount_untaxed,
            'amount_tax': line.amount_tax,
            'amount_total': line.amount_total,
            'line_subtotal_without_tax': line.line_subtotal_without_tax,
            'line_tax_amount': line.line_tax_amount,
            'line_total_with_tax': line.line_total_with_tax,
            'note': self._append_text(
                line.job_line_id.note,
                _('Applied to vendor bill line %s.') % move_line.display_name,
            ),
        })
        return warnings

    def _get_line_taxes(self, line, move):
        if line.tax_ids:
            return line.tax_ids, False
        if not self._is_positive_number(line.tax_rate):
            return self.env['account.tax'], False

        taxes = self._find_purchase_taxes(move.company_id, line.tax_rate)
        if len(taxes) == 1:
            return taxes, False
        if not taxes:
            return self.env['account.tax'], _(
                '%s: purchase tax %.4g%% was not found and was not applied.'
            ) % (line._display_label(), line.tax_rate)
        return self.env['account.tax'], _(
            '%s: several purchase taxes for %.4g%% were found; tax was not applied automatically.'
        ) % (line._display_label(), line.tax_rate)

    def _find_purchase_taxes(self, company, tax_rate):
        taxes = self.env['account.tax'].search([
            ('amount_type', '=', 'percent'),
            ('type_tax_use', 'in', ('purchase', 'none')),
            '|',
            ('company_id', '=', company.id),
            ('company_id', '=', False),
        ])
        return taxes.filtered(lambda tax: abs((tax.amount or 0.0) - tax_rate) <= 0.0001)

    def _format_warnings(self, warnings):
        return '\n'.join(
            ['Apply completed with warnings:'] + [str(warning) for warning in warnings]
        )

    def _append_text(self, existing_text, message):
        if existing_text:
            return '%s\n%s' % (existing_text, message)
        return message

    def _is_positive_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


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
    quantity = fields.Float()
    uom_name = fields.Char(string='UoM Name')
    price_unit_without_tax = fields.Float(
        string='Price Without Tax',
        readonly=True,
    )
    price_unit_with_tax = fields.Float(
        string='Price With Tax',
        readonly=True,
    )
    line_subtotal_without_tax = fields.Monetary(
        string='Subtotal Without Tax',
        currency_field='currency_id',
        readonly=True,
    )
    line_tax_amount = fields.Monetary(
        string='Line Tax Amount',
        currency_field='currency_id',
        readonly=True,
    )
    line_total_with_tax = fields.Monetary(
        string='Total With Tax',
        currency_field='currency_id',
        readonly=True,
    )
    matched_product_id = fields.Many2one(
        comodel_name='product.product',
        string='Matched Product',
    )
    price_unit = fields.Float()
    tax_rate = fields.Float()
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string='Taxes',
    )
    amount_untaxed = fields.Monetary(
        currency_field='currency_id',
        readonly=True,
    )
    amount_tax = fields.Monetary(
        currency_field='currency_id',
        readonly=True,
    )
    amount_total = fields.Monetary(
        currency_field='currency_id',
        readonly=True,
    )
    move_line_id = fields.Many2one(
        comodel_name='account.move.line',
        string='Vendor Bill Line',
    )
    candidate_product_ids = fields.Many2many(
        comodel_name='product.product',
        relation='account_gemini_digitization_review_line_product_candidate_rel',
        column1='wizard_line_id',
        column2='product_id',
        string='Product Candidates',
    )
    candidate_move_line_ids = fields.Many2many(
        comodel_name='account.move.line',
        relation='account_gemini_digitization_review_line_move_line_candidate_rel',
        column1='wizard_line_id',
        column2='move_line_id',
        string='Vendor Bill Line Candidates',
    )
    match_status = fields.Selection(
        selection=MATCH_STATUS_SELECTION,
        default='draft',
    )
    match_score = fields.Float()
    match_method = fields.Char()
    confidence = fields.Float()
    source_columns = fields.Text()
    note = fields.Text()
    match_note = fields.Text()
    currency_id = fields.Many2one(
        related='wizard_id.currency_id',
        readonly=True,
    )

    @api.onchange('move_line_id')
    def _onchange_move_line_id(self):
        for line in self:
            if not line.move_line_id:
                continue
            line.matched_product_id = line.move_line_id.product_id
            if line._is_manual_selection() or line.match_status not in ('matched', 'manual'):
                line.match_status = 'manual'
                line.match_method = 'manual_move_line'
                line.match_score = 1.0

    @api.onchange('matched_product_id')
    def _onchange_matched_product_id(self):
        for line in self:
            if line.wizard_id.mode != 'full_bill':
                continue
            if not line.matched_product_id:
                continue
            if line._is_manual_product_selection() or line.match_status not in ('matched', 'manual'):
                line.match_status = 'manual'
                line.match_method = 'manual_product'
                line.match_score = 1.0

    def _is_manual_selection(self):
        self.ensure_one()
        if not self.move_line_id:
            return False
        original_move_line = self.job_line_id.move_line_id
        return not original_move_line or original_move_line != self.move_line_id or self.match_status == 'manual'

    def _is_manual_product_selection(self):
        self.ensure_one()
        if not self.matched_product_id:
            return False
        original_product = self.job_line_id.matched_product_id
        return not original_product or original_product != self.matched_product_id or self.match_status == 'manual'

    def _display_label(self):
        self.ensure_one()
        return self.supplier_product_name or self.description or str(self.sequence)
