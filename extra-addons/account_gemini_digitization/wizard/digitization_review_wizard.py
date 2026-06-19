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
APPLY_ACTION_SELECTION = [
    ('create_line', 'Create Invoice Line'),
    ('merge_into', 'Merge Into Another Line'),
    ('skip', 'Skip'),
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
                'matching_message': False,
            })
        else:
            job.write({
                'state': 'done',
                'error_message': False,
                'matching_message': False,
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
        create_plans = self._prepare_full_bill_apply_plan(move)

        warnings = []
        header_values = {}
        if self.recognized_invoice_number:
            header_values['ref'] = self.recognized_invoice_number
        if self.recognized_invoice_date:
            header_values['invoice_date'] = self.recognized_invoice_date
        if header_values:
            move.write(header_values)

        commands = []
        existing_line_ids = set(move.invoice_line_ids.ids)
        for plan in create_plans:
            commands.append((0, 0, self._prepare_full_bill_invoice_line_values(plan, move)))

        if commands:
            move.write({'invoice_line_ids': commands})

        created_lines = self.env['account.move.line'].search([
            ('move_id', '=', move.id),
            ('id', 'not in', list(existing_line_ids)),
            ('product_id', '!=', False),
        ], order='id')
        if len(created_lines) != len(create_plans):
            raise UserError(_(
                'Gemini full bill apply could not safely identify created vendor bill lines.'
            ))

        for created_line, plan in zip(created_lines, create_plans):
            wizard_line = plan['line']
            tax_ids = plan['tax_ids']
            status = wizard_line.match_status
            method = wizard_line.match_method
            score = wizard_line.match_score
            if wizard_line._is_manual_product_selection() or status not in ('matched', 'manual'):
                status = 'manual'
                method = 'manual_product'
                score = score or 1.0
            note = self._append_text(
                wizard_line.job_line_id.note,
                _('Created vendor bill line %s.') % created_line.display_name,
            )
            if plan['merged_lines']:
                note = self._append_text(
                    note,
                    _('Merged OCR lines: %s.') % ', '.join(
                        merged_line._display_label() for merged_line in plan['merged_lines']
                    ),
                )
            wizard_line.job_line_id.write({
                'move_line_id': created_line.id,
                'matched_product_id': wizard_line.matched_product_id.id,
                'apply_action': 'create_line',
                'merge_target_line_id': False,
                'match_status': status,
                'match_score': score,
                'match_method': method,
                'match_summary': wizard_line.match_summary,
                'quantity': plan['quantity'],
                'price_unit': plan['price_unit'],
                'tax_rate': wizard_line.tax_rate,
                'tax_ids': [(6, 0, tax_ids.ids)] if tax_ids else [(6, 0, [])],
                'amount_untaxed': plan['amount_untaxed'],
                'amount_tax': plan['amount_tax'],
                'amount_total': plan['amount_total'],
                'line_subtotal_without_tax': plan['amount_untaxed'],
                'line_tax_amount': plan['amount_tax'],
                'line_total_with_tax': plan['amount_total'],
                'note': note,
            })
            for merged_line in plan['merged_lines']:
                merged_line.job_line_id.write({
                    'apply_action': 'merge_into',
                    'merge_target_line_id': wizard_line.job_line_id.id,
                    'move_line_id': False,
                    'match_status': 'manual',
                    'match_score': merged_line.match_score or 1.0,
                    'match_method': 'manual_merge',
                    'match_summary': _('Merged into: %s') % wizard_line._display_label(),
                    'note': self._append_text(
                        merged_line.job_line_id.note,
                        _('Merged into vendor bill line %s.') % created_line.display_name,
                    ),
                })

        for skipped_line in self.line_ids.filtered(lambda line: line.apply_action == 'skip'):
            skipped_line.job_line_id.write({
                'apply_action': 'skip',
                'merge_target_line_id': False,
                'move_line_id': False,
                'match_status': 'manual',
                'match_score': skipped_line.match_score or 1.0,
                'match_method': 'manual_skip',
                'match_summary': _('Skipped: manually excluded from invoice line creation'),
                'note': self._append_text(
                    skipped_line.job_line_id.note,
                    _('Skipped during full bill apply.'),
                ),
            })

        move.invalidate_recordset(['amount_untaxed', 'amount_tax', 'amount_total'])
        warnings.extend(AmountValidator(self.env).validate_move_totals(move, job))
        job.write({
            'state': 'done',
            'error_message': self._format_warnings(warnings) if warnings else False,
            'matching_message': False,
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

    def _autofill_line_taxes(self):
        for wizard in self:
            warnings = []
            move = wizard.move_id
            if not move:
                continue
            for line in wizard.line_ids:
                if line.tax_ids:
                    continue
                tax_ids, tax_warning = wizard._get_line_taxes(
                    line,
                    move,
                    strict=False,
                )
                if tax_ids:
                    line.tax_ids = [(6, 0, tax_ids.ids)]
                if tax_warning:
                    line.note = wizard._append_text(line.note, tax_warning)
                    if not tax_ids:
                        line.match_summary = wizard._get_tax_review_summary(line, tax_warning)
                    warnings.append(tax_warning)
            if warnings:
                wizard.note = wizard._append_text(
                    wizard.note,
                    '\n'.join(str(warning) for warning in warnings),
                )
        return True

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
        invalid_action = []
        missing_merge_target = []
        invalid_merge_target = []
        create_lines = self.line_ids.filtered(lambda line: (line.apply_action or 'create_line') == 'create_line')

        if not create_lines:
            raise UserError(_('At least one OCR line must create a vendor bill line.'))

        for line in self.line_ids:
            label = line._display_label()
            action = line.apply_action or 'create_line'
            if action not in ('create_line', 'merge_into', 'skip'):
                invalid_action.append(label)
                continue
            if action == 'skip':
                continue
            if action == 'merge_into':
                if not line.merge_target_line_id:
                    missing_merge_target.append(label)
                    continue
                if (
                    line.merge_target_line_id == line
                    or line.merge_target_line_id.wizard_id != self
                    or line.merge_target_line_id.apply_action != 'create_line'
                ):
                    invalid_merge_target.append(label)
                continue
            if line.match_status == 'error':
                invalid.append(label)
                continue
            if not line.matched_product_id:
                missing_product.append(label)
            if not self._is_positive_number(line.quantity):
                missing_quantity.append(label)
            if not self._is_positive_number(line.price_unit):
                missing_price.append(label)

        if invalid_action:
            raise UserError(_('Some OCR lines have unsupported apply actions. Lines: %s') % ', '.join(invalid_action))
        if invalid:
            raise UserError(_('Lines with matching errors cannot be applied. Lines: %s') % ', '.join(invalid))
        if missing_merge_target:
            raise UserError(_('Some OCR lines are marked as merge_into but have no target line. Lines: %s') % ', '.join(missing_merge_target))
        if invalid_merge_target:
            raise UserError(_('Some OCR lines have invalid merge targets. Target must be a create_line in the same Review. Lines: %s') % ', '.join(invalid_merge_target))
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

    def _prepare_full_bill_apply_plan(self, move):
        errors = []
        create_plans = []
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )
        for line in create_lines.sorted('sequence'):
            try:
                merged_lines = self.line_ids.filtered(
                    lambda child: child.apply_action == 'merge_into'
                    and child.merge_target_line_id == line
                ).sorted('sequence')
                self._validate_merge_tax_rates(line, merged_lines)
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    move,
                    strict=True,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    line.tax_rate,
                )
                create_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'quantity': quantity,
                    'price_unit': price_unit,
                    'amount_untaxed': amount_untaxed,
                    'amount_tax': amount_tax,
                    'amount_total': amount_total,
                })
            except UserError as error:
                errors.append(self._get_error_message(error))

        if errors:
            raise UserError('\n'.join(errors))
        return create_plans

    def _get_full_bill_plan_values(self, line, merged_lines):
        if not merged_lines:
            return (
                line.quantity,
                line.price_unit,
                self._line_subtotal(line) or line.quantity * line.price_unit,
            )

        group_lines = (line | merged_lines).sorted('sequence')
        quantities = [self._to_float(merged_line.quantity) for merged_line in group_lines]
        if all(self._is_number(quantity) for quantity in quantities):
            first_quantity = quantities[0]
            same_quantity = all(
                self._numbers_close(quantity, first_quantity, tolerance=0.0001)
                for quantity in quantities
            )
        else:
            first_quantity = False
            same_quantity = False

        if not same_quantity:
            if self._has_manual_merge_values(line):
                return (
                    line.quantity,
                    line.price_unit,
                    self._line_subtotal(line) or line.quantity * line.price_unit,
                )
            raise UserError(_(
                '%s: merged OCR lines have different quantities. '
                'Set target quantity and price manually before Apply.'
            ) % line._display_label())

        price_values = [self._line_price_unit(merged_line) for merged_line in group_lines]
        subtotal_values = [self._line_subtotal(merged_line) for merged_line in group_lines]
        if all(self._is_number(price) for price in price_values):
            price_unit = sum(price_values)
            amount_untaxed = sum(
                subtotal if self._is_number(subtotal) else first_quantity * price
                for subtotal, price in zip(subtotal_values, price_values)
            )
        elif (
            all(self._is_number(subtotal) for subtotal in subtotal_values)
            and self._is_positive_number(first_quantity)
        ):
            price_unit = sum(subtotal_values) / first_quantity
            amount_untaxed = sum(subtotal_values)
        else:
            raise UserError(_(
                '%s: merged OCR lines do not have enough price/subtotal data for automatic calculation.'
            ) % line._display_label())

        return first_quantity, price_unit, amount_untaxed

    def _has_manual_merge_values(self, line):
        original = line.job_line_id
        if not original:
            return True
        original_quantity = self._to_float(original.quantity)
        original_price = self._to_float(original.price_unit)
        current_quantity = self._to_float(line.quantity)
        current_price = self._to_float(line.price_unit)
        quantity_changed = (
            self._is_number(original_quantity)
            and self._is_number(current_quantity)
            and not self._numbers_close(original_quantity, current_quantity, tolerance=0.0001)
        )
        price_changed = (
            self._is_number(original_price)
            and self._is_number(current_price)
            and not self._numbers_close(original_price, current_price, tolerance=0.01)
        )
        return quantity_changed or price_changed

    def _validate_merge_tax_rates(self, line, merged_lines):
        target_rate = self._normalize_tax_rate(line.tax_rate)
        if not self._is_number(target_rate):
            return True
        for merged_line in merged_lines:
            merged_rate = self._normalize_tax_rate(merged_line.tax_rate)
            if not self._is_number(merged_rate):
                continue
            if not self._numbers_close(target_rate, merged_rate, tolerance=0.0001):
                raise UserError(_(
                    '%(line)s: merged OCR line "%(merged)s" has a different tax rate. '
                    'Split it or correct tax rates before Apply.'
                ) % {
                    'line': line._display_label(),
                    'merged': merged_line._display_label(),
                })
        return True

    def _get_full_bill_plan_tax_amounts(self, amount_untaxed, tax_rate):
        tax_rate = self._normalize_tax_rate(tax_rate)
        if not self._is_number(amount_untaxed) or not self._is_number(tax_rate):
            return False, False
        amount_tax = amount_untaxed * tax_rate / 100.0
        return amount_tax, amount_untaxed + amount_tax

    def _line_price_unit(self, line):
        return self._first_number(
            line.price_unit,
            line.price_unit_without_tax,
        )

    def _line_subtotal(self, line):
        return self._first_number(
            line.amount_untaxed,
            line.line_subtotal_without_tax,
        )

    def _prepare_full_bill_invoice_line_values(self, plan, move):
        line = plan['line']
        product = line.matched_product_id
        values = {
            'product_id': product.id,
            'name': self._get_full_bill_line_name(line, product, plan['merged_lines']),
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
        }
        account = self._get_product_expense_account(product, move)
        if account:
            values['account_id'] = account.id
        uom = getattr(product, 'uom_po_id', False) or getattr(product, 'uom_id', False)
        if uom:
            values['product_uom_id'] = uom.id
        values['tax_ids'] = [(6, 0, plan['tax_ids'].ids)]
        return values

    def _get_full_bill_line_name(self, line, product, merged_lines=False):
        name = (
            line.description
            or line.supplier_product_name
            or getattr(product, 'display_name', False)
            or getattr(product, 'name', False)
        )
        merged_lines = merged_lines or []
        if merged_lines:
            merged_names = [
                merged_line.description
                or merged_line.supplier_product_name
                or merged_line._display_label()
                for merged_line in merged_lines
            ]
            if merged_names:
                name = '%s\n%s: %s' % (
                    name,
                    _('Includes OCR lines'),
                    '; '.join(merged_names),
                )
        return name

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
            'match_summary': line.match_summary,
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

    def _get_line_taxes(self, line, move, strict=False):
        tax_rate = self._normalize_tax_rate(line.tax_rate)
        if line.tax_ids:
            if strict:
                self._validate_selected_taxes(line, line.tax_ids, tax_rate)
            return line.tax_ids, False

        if not self._is_number(tax_rate):
            return self.env['account.tax'], False

        if tax_rate == 0:
            if not self._line_allows_zero_tax(line):
                if strict:
                    raise UserError(_(
                        '%s: OCR tax rate is 0%%, but there is no explicit zero-rated/VAT-exempt evidence. '
                        'Please select taxes manually or check OCR result.'
                    ) % line._display_label())
                return self.env['account.tax'], _(
                    '%s: zero tax was not applied automatically because OCR did not provide explicit VAT-exempt evidence.'
                ) % line._display_label()
        elif tax_rate < 0:
            if strict:
                raise UserError(_('%s: invalid negative tax rate %.4g%%.') % (
                    line._display_label(),
                    tax_rate,
                ))
            return self.env['account.tax'], _('%s: invalid negative tax rate %.4g%%.') % (
                line._display_label(),
                tax_rate,
            )

        taxes, warning = self._find_purchase_taxes(move.company_id, tax_rate, line=line)
        if taxes:
            line.tax_ids = [(6, 0, taxes.ids)]
            return taxes, False

        if strict:
            if tax_rate > 0:
                raise UserError(_(
                    'Для рядка "%(line)s" оберіть податок ПДВ %(rate).4g%% у Review перед Apply. %(details)s'
                ) % {
                    'line': line._display_label(),
                    'rate': tax_rate,
                    'details': warning or '',
                })
            raise UserError(warning)
        return self.env['account.tax'], warning

    def _find_purchase_taxes(self, company, tax_rate, line=False):
        tax_rate = self._normalize_tax_rate(tax_rate)
        if not self._is_number(tax_rate):
            return self.env['account.tax'], False

        company_domain = []
        if company:
            company_domain = [
                '|',
                ('company_id', '=', company.id),
                ('company_id', '=', False),
            ]
        taxes = self.env['account.tax'].search([
            ('active', '=', True),
            ('amount_type', '=', 'percent'),
            ('type_tax_use', 'in', ('purchase', 'none')),
        ] + company_domain)
        matching_taxes = taxes.filtered(
            lambda tax: abs((tax.amount or 0.0) - tax_rate) <= 0.0001
        )
        if not matching_taxes:
            return self.env['account.tax'], _(
                '%s: purchase tax %.4g%% was not found. Please select the correct tax before Apply.'
            ) % (line._display_label() if line else _('Line'), tax_rate)

        selected_taxes = self._select_best_purchase_tax(
            matching_taxes,
            company,
            tax_rate,
            line=line,
        )
        if selected_taxes:
            return selected_taxes, False

        return self.env['account.tax'], _(
            '%(line)s: several purchase taxes for %(rate).4g%% were found. '
            'Please select the correct tax manually before Apply. %(candidates)s'
        ) % {
            'line': line._display_label() if line else _('Line'),
            'rate': tax_rate,
            'candidates': self._format_tax_candidates(matching_taxes),
        }

    def _select_best_purchase_tax(self, taxes, company, tax_rate, line=False):
        configured_tax = self._get_configured_purchase_vat_tax(tax_rate, company)
        if configured_tax and configured_tax.id in taxes.ids:
            return configured_tax

        product_tax = self._get_product_supplier_tax(line, taxes, tax_rate)
        if product_tax:
            return product_tax

        purchase_taxes = taxes.filtered(lambda tax: tax.type_tax_use == 'purchase')
        if purchase_taxes:
            taxes = purchase_taxes

        company_taxes = taxes.filtered(lambda tax: tax.company_id == company)
        if company_taxes:
            taxes = company_taxes

        preferred_by_name = taxes.filtered(
            lambda tax: self._tax_name_matches_rate(tax, tax_rate)
        )
        if len(preferred_by_name) == 1:
            return preferred_by_name
        if preferred_by_name:
            taxes = preferred_by_name

        if len(taxes) == 1:
            return taxes
        return self.env['account.tax']

    def _get_configured_purchase_vat_tax(self, tax_rate, company):
        if abs((tax_rate or 0.0) - 20.0) > 0.0001:
            return self.env['account.tax']
        tax_id = self.env['ir.config_parameter'].sudo().get_param(
            'account_gemini_digitization.default_purchase_vat_20_tax_id'
        )
        if not tax_id:
            return self.env['account.tax']
        try:
            tax_id = int(tax_id)
        except (TypeError, ValueError):
            return self.env['account.tax']
        tax = self.env['account.tax'].browse(tax_id).exists()
        if not tax:
            return self.env['account.tax']
        if not self._tax_matches_rate_and_scope(tax, tax_rate, company):
            return self.env['account.tax']
        return tax

    def _get_product_supplier_tax(self, line, taxes, tax_rate):
        product = getattr(line, 'matched_product_id', False) if line else False
        if not product:
            return self.env['account.tax']
        product_taxes = (
            getattr(product, 'supplier_taxes_id', False)
            or getattr(getattr(product, 'product_tmpl_id', False), 'supplier_taxes_id', False)
        )
        if not product_taxes:
            return self.env['account.tax']
        matching_product_taxes = product_taxes.filtered(
            lambda tax: tax.id in taxes.ids
            and tax.amount_type == 'percent'
            and abs((tax.amount or 0.0) - tax_rate) <= 0.0001
            and tax.type_tax_use in ('purchase', 'none')
            and getattr(tax, 'active', True)
        )
        if len(matching_product_taxes) == 1:
            return matching_product_taxes
        return self.env['account.tax']

    def _tax_name_matches_rate(self, tax, tax_rate):
        name = self._normalize_text(
            '%s %s' % (
                getattr(tax, 'name', False) or '',
                getattr(tax, 'display_name', False) or '',
            )
        )
        if not name:
            return False
        rate_text = str(int(tax_rate)) if abs(tax_rate - int(tax_rate)) <= 0.0001 else str(tax_rate)
        has_rate = rate_text in name
        if tax_rate == 0:
            return has_rate or 'без пдв' in name or 'без ндс' in name or 'no vat' in name
        has_vat_word = any(word in name for word in ('пдв', 'ндс', 'vat'))
        excluded_words = (
            'імпорт',
            'импорт',
            'кориг',
            'умов',
            'услов',
            'зворот',
            'возврат',
            'компенс',
        )
        has_excluded_word = any(word in name for word in excluded_words)
        has_purchase_word = 'придбання' in name or 'приобрет' in name or 'purchase' in name
        return has_rate and has_vat_word and has_purchase_word and not has_excluded_word

    def _tax_matches_rate_and_scope(self, tax, tax_rate, company):
        return (
            getattr(tax, 'active', True)
            and tax.amount_type == 'percent'
            and tax.type_tax_use in ('purchase', 'none')
            and abs((tax.amount or 0.0) - tax_rate) <= 0.0001
            and (not tax.company_id or tax.company_id == company)
        )

    def _format_tax_candidates(self, taxes):
        if not taxes:
            return ''
        parts = []
        for tax in taxes[:10]:
            company = getattr(tax, 'company_id', False)
            parts.append(
                '[id=%s %s; amount=%s; type=%s; company_id=%s; active=%s]'
                % (
                    tax.id,
                    tax.display_name or tax.name,
                    tax.amount,
                    tax.type_tax_use,
                    company.id if company else False,
                    getattr(tax, 'active', True),
                )
            )
        return _('Tax candidates: %s') % ', '.join(parts)

    def _get_tax_review_summary(self, line, warning):
        tax_rate = self._normalize_tax_rate(line.tax_rate)
        if self._is_number(tax_rate):
            if 'several purchase taxes' in str(warning):
                return _('Tax review required: several %.4g%% purchase taxes found') % tax_rate
            if 'was not found' in str(warning):
                return _('Tax review required: no %.4g%% purchase tax found') % tax_rate
            if tax_rate == 0:
                return _('Tax review required: confirm 0%% VAT tax')
            return _('Tax review required: select %.4g%% purchase tax') % tax_rate
        return _('Tax review required: select purchase tax')

    def _validate_selected_taxes(self, line, taxes, tax_rate):
        if not self._is_number(tax_rate):
            return True
        if tax_rate > 0:
            matching_taxes = taxes.filtered(
                lambda tax: tax.amount_type == 'percent'
                and abs((tax.amount or 0.0) - tax_rate) <= 0.0001
                and tax.type_tax_use in ('purchase', 'none')
                and getattr(tax, 'active', True)
            )
            if not matching_taxes:
                raise UserError(_(
                    'Для рядка "%(line)s" вибраний податок не відповідає розпізнаній ставці %(rate).4g%%.'
                ) % {
                    'line': line._display_label(),
                    'rate': tax_rate,
                })
        if tax_rate == 0:
            matching_zero_taxes = taxes.filtered(
                lambda tax: tax.amount_type == 'percent'
                and abs(tax.amount or 0.0) <= 0.0001
                and tax.type_tax_use in ('purchase', 'none')
                and getattr(tax, 'active', True)
            )
            if not matching_zero_taxes:
                raise UserError(_(
                    '%s: selected tax is not a 0%% tax, but OCR tax rate is 0%%.'
                ) % line._display_label())
        return True

    def _normalize_tax_rate(self, tax_rate):
        if not self._is_number(tax_rate):
            return False
        if 0 < tax_rate <= 1:
            return tax_rate * 100
        return tax_rate

    def _line_allows_zero_tax(self, line):
        text = self._normalize_text(' '.join(
            str(value)
            for value in (
                line.supplier_product_name,
                line.description,
                line.source_columns,
                line.note,
            )
            if value
        ))
        return any(
            phrase in text
            for phrase in (
                'без пдв',
                'без ндс',
                'vat exempt',
                'zero rated',
                '0 пдв',
                '0 ндс',
                '0 vat',
            )
        )

    def _normalize_text(self, value):
        value = str(value or '').lower()
        value = value.replace('%', ' ')
        return ' '.join(value.split())

    def _get_error_message(self, error):
        if getattr(error, 'args', None):
            return error.args[0]
        return str(error)

    def _format_warnings(self, warnings):
        return '\n'.join(
            ['Apply completed with warnings:'] + [str(warning) for warning in warnings]
        )

    def _append_text(self, existing_text, message):
        if existing_text:
            return '%s\n%s' % (existing_text, message)
        return message

    def _is_tax_review_summary(self, summary):
        return bool(summary and str(summary).startswith('Tax review required:'))

    def _is_positive_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    def _is_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _to_float(self, value):
        if value in (None, False, ''):
            return False
        try:
            return float(value)
        except (TypeError, ValueError):
            return False

    def _first_number(self, *values):
        for value in values:
            number = self._to_float(value)
            if self._is_number(number):
                return number
        return False

    def _numbers_close(self, first, second, tolerance=0.01):
        if not self._is_number(first) or not self._is_number(second):
            return False
        return abs(first - second) <= tolerance


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
    match_summary = fields.Char(
        readonly=True,
    )
    apply_action = fields.Selection(
        selection=APPLY_ACTION_SELECTION,
        required=True,
        default='create_line',
        string='Apply Action',
    )
    merge_target_line_id = fields.Many2one(
        comodel_name='account.gemini.digitization.review.line.wizard',
        string='Merge Target',
    )
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
                line.match_summary = _('Manual: selected vendor bill line %s') % (
                    line.move_line_id.display_name
                )

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
                line.match_summary = _('Manual: selected product %s') % (
                    line.matched_product_id.display_name
                )

    @api.onchange('tax_ids')
    def _onchange_tax_ids(self):
        for line in self:
            if not line.tax_ids:
                continue
            if line.wizard_id._is_tax_review_summary(line.match_summary):
                line.match_summary = (
                    line.job_line_id.match_summary
                    or _('Tax selected manually')
                )

    @api.onchange('apply_action')
    def _onchange_apply_action(self):
        for line in self:
            if line.apply_action != 'merge_into':
                line.merge_target_line_id = False
            if line.apply_action == 'skip':
                line.match_status = 'manual'
                line.match_method = 'manual_skip'
                line.match_score = 1.0
                line.match_summary = _('Skipped: will not create an invoice line')
            elif line.apply_action == 'merge_into':
                line.match_status = 'manual'
                line.match_method = 'manual_merge'
                line.match_score = line.match_score or 1.0
                if line.merge_target_line_id:
                    line.match_summary = _('Merged into: %s') % line.merge_target_line_id._display_label()
                else:
                    line.match_summary = _('Merge: select target OCR line')
            elif line.apply_action == 'create_line' and line.job_line_id:
                line.match_summary = line.job_line_id.match_summary
                line.match_status = line.job_line_id.match_status
                line.match_method = line.job_line_id.match_method
                line.match_score = line.job_line_id.match_score

    @api.onchange('merge_target_line_id')
    def _onchange_merge_target_line_id(self):
        for line in self:
            if not line.merge_target_line_id:
                continue
            line.apply_action = 'merge_into'
            line.match_status = 'manual'
            line.match_method = 'manual_merge'
            line.match_score = line.match_score or 1.0
            line.match_summary = _('Merged into: %s') % line.merge_target_line_id._display_label()

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
