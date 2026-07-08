import logging

from odoo import _, fields
from odoo.exceptions import UserError

from .amount_validator import AmountValidator
from .product_matcher import ProductMatcher
from .supplier_code import SupplierArticleNormalizer, TechnicalCodeNormalizer


_logger = logging.getLogger(__name__)


class DigitizationApplyService:
    """Apply reviewed persistent OCR lines to their linked business document."""

    def __init__(self, env, job):
        self.env = env
        self.job = job

    def apply(self):
        self.job.ensure_one()
        if self.job.mode == 'partial_bill':
            ProductMatcher(self.env).sync_partial_bill_move_lines(self.job)
            self.job._update_matching_message_after_matching()
        if self.job.mode == 'partial_purchase':
            ProductMatcher(self.env).sync_partial_purchase_order_lines(self.job)
            self.job._update_matching_message_after_matching()
        return _JobApplyContext(self.job).action_apply()

    def validate_for_automatic_apply(self):
        self.job.ensure_one()
        if self.job.mode == 'partial_bill':
            ProductMatcher(self.env).sync_partial_bill_move_lines(self.job)
            self.job._update_matching_message_after_matching()
        if self.job.mode == 'partial_purchase':
            ProductMatcher(self.env).sync_partial_purchase_order_lines(self.job)
            self.job._update_matching_message_after_matching()
        return _JobApplyContext(self.job).validate_for_automatic_apply()


class _JobApplyContext:
    """Expose a job and persistent OCR lines to the shared Apply methods."""

    def __init__(self, job):
        self.job = job
        self.env = job.env

    @property
    def job_id(self):
        return self.job

    def ensure_one(self):
        self.job.ensure_one()
        return self

    def __getattr__(self, name):
        return getattr(self.job, name)

    def action_apply(self):
        self.ensure_one()
        if self.job_id.state == 'done':
            raise UserError(_('This Gemini job has already been applied.'))
        if self.mode == 'partial_bill':
            return self._apply_partial_bill()
        if self.mode == 'partial_purchase':
            return self._apply_partial_purchase()
        if self.mode == 'full_bill':
            return self._apply_full_bill()
        if self.mode == 'full_purchase':
            return self._apply_full_purchase()
        raise UserError(_('Unsupported Gemini review mode: %s') % self.mode)

    def validate_for_automatic_apply(self):
        self.ensure_one()
        if self.job.mode == 'partial_bill':
            move = self.job.move_id
            self._validate_partial_bill_apply(self.job, move)
            self._validate_review_lines()
            apply_plans = self._prepare_partial_bill_apply_plan(move)
            self._validate_partial_bill_apply_plans(apply_plans, move)
            return True
        if self.job.mode == 'partial_purchase':
            order = self.job.purchase_order_id
            self._validate_partial_purchase_apply(self.job, order)
            self._validate_partial_purchase_review_lines()
            apply_plans = self._prepare_partial_purchase_apply_plan(order)
            self._validate_partial_purchase_apply_plans(apply_plans, order)
            return True
        if self.job.mode == 'full_bill':
            move = self.job.move_id
            self._validate_full_bill_apply(self.job, move)
            return True
        if self.job.mode == 'full_purchase':
            order = self.job.purchase_order_id
            self._validate_full_purchase_apply(self.job, order)
            self._validate_full_purchase_review_lines()
            self._prepare_full_purchase_apply_plan(order)
            return True
        return False

    def _apply_partial_bill(self):
        self.ensure_one()
        if self.mode != 'partial_bill':
            raise UserError(_('Unsupported Gemini review mode: %s') % self.mode)

        job = self.job_id
        move = job.move_id
        self._validate_partial_bill_apply(job, move)
        self._validate_review_lines()
        apply_plans = self._prepare_partial_bill_apply_plan(move)
        self._validate_partial_bill_apply_plans(apply_plans, move)

        existing_line_ids = set(move.invoice_line_ids.ids)
        warnings = []

        header_values = {}
        if self.recognized_invoice_number:
            header_values['ref'] = self.recognized_invoice_number
        if self.recognized_invoice_date:
            header_values['invoice_date'] = self.recognized_invoice_date
        if header_values:
            move.write(header_values)

        for plan in apply_plans:
            line_warnings = self._apply_review_line(plan, move)
            warnings.extend(line_warnings)

        for skipped_line in self.line_ids.filtered(lambda line: line.apply_action == 'skip'):
            skipped_line.job_line_id.write({
                'apply_action': 'skip',
                'merge_target_line_id': False,
                'move_line_id': False,
                'match_status': 'manual',
                'match_score': skipped_line.match_score or 1.0,
                'match_method': 'manual_skip',
                'match_summary': _('Skipped: manually excluded from vendor bill update'),
                'note': self._append_text(
                    skipped_line.job_line_id.note,
                    _('Skipped during partial bill apply.'),
                ),
            })

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
            'name': _('Рахунок постачальника'),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _apply_partial_purchase(self):
        self.ensure_one()
        if self.mode != 'partial_purchase':
            raise UserError(_('Unsupported Gemini review mode: %s') % self.mode)

        job = self.job_id
        order = job.purchase_order_id
        self._validate_partial_purchase_apply(job, order)
        self._validate_partial_purchase_review_lines()
        apply_plans = self._prepare_partial_purchase_apply_plan(order)
        self._validate_partial_purchase_apply_plans(apply_plans, order)

        existing_line_ids = set(order.order_line.ids)
        for plan in apply_plans:
            self._apply_purchase_review_line(plan, order)

        if set(order.order_line.ids) != existing_line_ids:
            raise UserError(_('Apply must not create or delete purchase order lines.'))

        header_warnings = self._apply_purchase_order_header(order)

        job.write({
            'state': 'done',
            'error_message': self._format_warnings(header_warnings) if header_warnings else False,
            'matching_message': False,
        })

        action = self._get_purchase_order_form_action(order)
        if header_warnings:
            action.update({
                'gemini_header_warnings': header_warnings,
                'gemini_notification_type': 'warning',
            })
        return action

    def _apply_full_bill(self):
        job = self.job_id
        move = job.move_id
        self._validate_full_bill_apply(job, move)
        apply_plans, skipped_reasons = self._prepare_full_bill_partial_apply_plan(move)
        applied_source_line_ids = {
            source_line.id
            for plan in apply_plans
            for source_line in (plan['line'] | plan['merged_lines'])
        }
        skipped_count = len(self.line_ids.filtered(
            lambda line: line.id not in applied_source_line_ids
        ))
        applied_count = len(apply_plans)

        if not apply_plans:
            for skipped_line in self.line_ids:
                self._mark_full_bill_line_skipped(
                    skipped_line,
                    skipped_reasons.get(skipped_line.id) or _(
                        'No safe product match or apply data was found.'
                    ),
                )
            job.write({
                'state': 'review',
                'error_message': False,
                'matching_message': False,
            })
            action = self._get_move_form_action(move)
            action.update({
                'gemini_apply_result': {
                    'applied': 0,
                    'skipped': skipped_count,
                },
                'gemini_applied_count': 0,
                'gemini_skipped_count': skipped_count,
                'gemini_status': 'nothing_applied',
                'gemini_notification_type': 'warning',
            })
            return action

        warnings = []
        header_values = {}
        if self.recognized_invoice_number:
            header_values['ref'] = self.recognized_invoice_number
        if self.recognized_invoice_date:
            header_values['invoice_date'] = self.recognized_invoice_date
        if header_values:
            move.write(header_values)

        update_plans = [
            plan for plan in apply_plans if plan.get('existing_move_line')
        ]
        create_plans = [
            plan for plan in apply_plans if not plan.get('existing_move_line')
        ]
        commands = []
        existing_line_ids = set(move.invoice_line_ids.ids)
        for plan in update_plans:
            self._apply_existing_full_bill_line(plan, move)
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
            review_line = plan['line']
            tax_ids = plan['tax_ids']
            status = review_line.match_status
            method = review_line.match_method
            score = review_line.match_score
            if review_line._is_manual_product_selection() or status not in ('matched', 'manual'):
                status = 'manual'
                method = 'manual_product'
                score = score or 1.0
            note = self._append_text(
                review_line.job_line_id.note,
                _('Created vendor bill line %s.') % created_line.display_name,
            )
            if plan['merged_lines']:
                note = self._append_text(
                    note,
                    _('Merged OCR lines: %s.') % ', '.join(
                        merged_line._display_label() for merged_line in plan['merged_lines']
                    ),
                )
            review_line.job_line_id.write({
                'move_line_id': created_line.id,
                'matched_product_id': review_line.matched_product_id.id,
                'apply_action': 'create_line',
                'merge_target_line_id': False,
                'match_status': status,
                'match_score': score,
                'match_method': method,
                'match_summary': review_line.match_summary,
                'quantity': plan['quantity'],
                'price_unit': plan['price_unit'],
                'tax_rate': plan['tax_rate'],
                'price_tax_mode': plan.get('price_tax_mode') or review_line.price_tax_mode or 'unknown',
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
                    'merge_target_line_id': review_line.job_line_id.id,
                    'move_line_id': created_line.id,
                    'matched_product_id': review_line.matched_product_id.id,
                    'match_status': 'manual',
                    'match_score': merged_line.match_score or 1.0,
                    'match_method': 'manual_merge',
                    'match_summary': _('Merged into: %s') % review_line._display_label(),
                    'note': self._append_text(
                        merged_line.job_line_id.note,
                        _('Merged into vendor bill line %s.') % created_line.display_name,
                    ),
                })

        for skipped_line in self.line_ids.filtered(lambda line: line.apply_action == 'skip'):
            if skipped_line.id not in applied_source_line_ids:
                self._mark_full_bill_line_skipped(
                    skipped_line,
                    skipped_reasons.get(skipped_line.id) or _(
                        'Skipped during full bill apply.'
                    ),
                )

        for skipped_line_id, reason in skipped_reasons.items():
            if skipped_line_id in applied_source_line_ids:
                continue
            skipped_line = self.line_ids.filtered(lambda line: line.id == skipped_line_id)
            if skipped_line:
                self._mark_full_bill_line_skipped(skipped_line, reason)

        move.invalidate_recordset(['amount_untaxed', 'amount_tax', 'amount_total'])
        warnings.extend(AmountValidator(self.env).validate_move_totals(move, job))
        job.write({
            'state': 'done',
            'error_message': self._format_warnings(warnings) if warnings else False,
            'matching_message': False,
        })

        action = self._get_move_form_action(move)
        action.update({
            'gemini_apply_result': {
                'applied': applied_count,
                'skipped': skipped_count,
            },
            'gemini_applied_count': applied_count,
            'gemini_skipped_count': skipped_count,
            'gemini_status': 'partial_applied' if skipped_count else 'applied',
            'gemini_notification_type': 'warning' if skipped_count else 'success',
        })
        return action

    def _apply_full_purchase(self):
        self.ensure_one()
        job = self.job_id
        order = job.purchase_order_id
        self._validate_full_purchase_apply(job, order)
        apply_plans, skipped_reasons = self._prepare_full_purchase_partial_apply_plan(order)
        applied_source_line_ids = {
            source_line.id
            for plan in apply_plans
            for source_line in (plan['line'] | plan['merged_lines'])
        }
        skipped_count = len(self.line_ids.filtered(
            lambda line: line.id not in applied_source_line_ids
        ))
        applied_count = len(apply_plans)

        if not apply_plans:
            for skipped_line in self.line_ids:
                self._mark_full_purchase_line_skipped(
                    skipped_line,
                    skipped_reasons.get(skipped_line.id) or _(
                        'No safe product match or apply data was found.'
                    ),
                )
            job.write({
                'state': 'review',
                'error_message': False,
                'matching_message': False,
            })
            action = self._get_purchase_order_form_action(order)
            action.update({
                'gemini_apply_result': {
                    'applied': 0,
                    'skipped': skipped_count,
                },
                'gemini_applied_count': 0,
                'gemini_skipped_count': skipped_count,
                'gemini_status': 'nothing_applied',
                'gemini_notification_type': 'warning',
            })
            return action

        update_plans = [
            plan for plan in apply_plans if plan.get('existing_purchase_order_line')
        ]
        create_plans = [
            plan for plan in apply_plans if not plan.get('existing_purchase_order_line')
        ]

        existing_line_ids = set(order.order_line.ids)
        for plan in update_plans:
            self._apply_existing_full_purchase_line(plan, order)

        commands = [
            (0, 0, self._prepare_full_purchase_order_line_values(plan, order))
            for plan in create_plans
        ]
        if commands:
            order.write({'order_line': commands})

        created_lines = self.env['purchase.order.line'].search([
            ('order_id', '=', order.id),
            ('id', 'not in', list(existing_line_ids)),
        ], order='id')
        if len(created_lines) != len(create_plans):
            raise UserError(_(
                'Gemini full purchase apply could not safely identify created purchase order lines.'
            ))

        for created_line, plan in zip(created_lines, create_plans):
            review_line = plan['line']
            tax_ids = plan['tax_ids']
            status = review_line.match_status
            method = review_line.match_method
            score = review_line.match_score
            if review_line._is_manual_product_selection() or status not in ('matched', 'manual'):
                status = 'manual'
                method = 'manual_product'
                score = score or 1.0

            note = self._append_text(
                review_line.job_line_id.note,
                _('Created purchase order line %s.') % created_line.display_name,
            )
            if plan['merged_lines']:
                note = self._append_text(
                    note,
                    _('Merged OCR lines: %s.') % ', '.join(
                        merged_line._display_label() for merged_line in plan['merged_lines']
                    ),
                )

            review_line.job_line_id.write({
                'purchase_order_line_id': created_line.id,
                'move_line_id': False,
                'matched_product_id': review_line.matched_product_id.id,
                'apply_action': 'create_line',
                'merge_target_line_id': False,
                'match_status': status,
                'match_score': score,
                'match_method': method,
                'match_summary': review_line.match_summary,
                'quantity': plan['quantity'],
                'price_unit': plan['price_unit'],
                'tax_rate': plan['tax_rate'],
                'price_tax_mode': plan.get('price_tax_mode') or review_line.price_tax_mode or 'unknown',
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
                    'purchase_order_line_id': created_line.id,
                    'move_line_id': False,
                    'matched_product_id': review_line.matched_product_id.id,
                    'apply_action': 'merge_into',
                    'merge_target_line_id': review_line.job_line_id.id,
                    'match_status': 'manual',
                    'match_score': merged_line.match_score or 1.0,
                    'match_method': 'manual_merge',
                    'match_summary': _('Merged into: %s') % review_line._display_label(),
                    'note': self._append_text(
                        merged_line.job_line_id.note,
                        _('Merged into purchase order line %s.') % created_line.display_name,
                    ),
                })

        for skipped_line in self.line_ids.filtered(lambda line: line.apply_action == 'skip'):
            skipped_line.job_line_id.write({
                'purchase_order_line_id': False,
                'move_line_id': False,
                'apply_action': 'skip',
                'merge_target_line_id': False,
                'match_status': 'manual',
                'match_score': skipped_line.match_score or 1.0,
                'match_method': 'manual_skip',
                'match_summary': _('Skipped: manually excluded from purchase order line creation'),
                'note': self._append_text(
                    skipped_line.job_line_id.note,
                    _('Skipped during full purchase apply.'),
                ),
            })

        header_warnings = self._apply_purchase_order_header(order)
        job.write({
            'state': 'done',
            'error_message': self._format_warnings(header_warnings) if header_warnings else False,
            'matching_message': False,
        })
        action = self._get_purchase_order_form_action(order)
        action.update({
            'gemini_apply_result': {
                'applied': applied_count,
                'skipped': skipped_count,
            },
            'gemini_applied_count': applied_count,
            'gemini_skipped_count': skipped_count,
            'gemini_status': 'partial_applied' if skipped_count else 'applied',
            'gemini_notification_type': 'warning' if skipped_count or header_warnings else 'success',
            'gemini_header_warnings': header_warnings,
        })
        return action

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
        existing_product_lines = self._get_move_product_lines(move)
        manual_product_lines = existing_product_lines.filtered(
            lambda line: not getattr(line, 'gemini_digitization_auto_created', False)
        )
        if manual_product_lines:
            raise UserError(_(
                'Vendor bill already contains non-Gemini product lines. '
                'Use partial bill recognition for existing lines.'
            ))

    def _validate_full_purchase_apply(self, job, order):
        if not job or job.state != 'review':
            raise UserError(_('Gemini job must be in Review state before apply.'))
        if not order:
            raise UserError(_('Gemini job is not linked to a purchase order.'))
        if order.state not in ('draft', 'sent'):
            raise UserError(_(
                'Gemini full purchase apply is allowed only for draft or sent purchase orders.'
            ))
        if not (order.partner_id or job.partner_id):
            raise UserError(_(
                'Спочатку оберіть постачальника в замовленні на закупівлю.'
            ))
        if any(job_line.purchase_order_line_id for job_line in job.line_ids):
            raise UserError(_(
                'This Gemini full purchase job already has created purchase order lines and cannot be applied again.'
            ))
        existing_product_lines = self._get_purchase_product_lines(order)
        manual_product_lines = existing_product_lines.filtered(
            lambda line: not getattr(line, 'gemini_digitization_auto_created', False)
        )
        if manual_product_lines:
            raise UserError(_(
                'Purchase order already contains non-Gemini product lines. '
                'Use partial purchase recognition for existing lines.'
            ))

    def _validate_partial_purchase_apply(self, job, order):
        if not job or job.state != 'review':
            raise UserError(_('Gemini job must be in Review state before apply.'))
        if not order:
            raise UserError(_('Gemini job is not linked to a purchase order.'))
        if order.state not in ('draft', 'sent'):
            raise UserError(_(
                'Gemini partial purchase apply is allowed only for draft or sent purchase orders.'
            ))
        if not (order.partner_id or job.partner_id):
            raise UserError(_(
                'Спочатку оберіть постачальника в замовленні на закупівлю.'
            ))
        if not self._get_purchase_product_lines(order):
            raise UserError(_(
                'Purchase order has no product lines. Use full purchase recognition for empty orders.'
            ))

    def _validate_review_lines(self):
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to apply.'))

        incomplete = []
        invalid = []
        invalid_action = []
        missing_merge_target = []
        invalid_merge_target = []
        missing_price = []
        missing_quantity = []
        duplicate_move_lines = []
        used_move_line_ids = {}
        create_lines = self.line_ids.filtered(lambda line: (line.apply_action or 'create_line') == 'create_line')

        if not create_lines:
            raise UserError(_('At least one OCR line must update a vendor bill line.'))

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
                    or not self._is_line_in_apply_context(line.merge_target_line_id)
                    or line.merge_target_line_id.apply_action != 'create_line'
                ):
                    invalid_merge_target.append(label)
                continue
            if not line.move_line_id:
                incomplete.append(label)
                continue
            if line.match_status == 'error':
                invalid.append(label)
                continue
            if line.match_status not in ('matched', 'manual') and not line._is_manual_selection():
                incomplete.append(label)
            if line.move_line_id.id in used_move_line_ids:
                duplicate_move_lines.append(label)
            else:
                used_move_line_ids[line.move_line_id.id] = line
            if not self._is_positive_number(line.quantity):
                missing_quantity.append(label)
            if not self._is_positive_number(line.price_unit):
                missing_price.append(label)

        if invalid_action:
            raise UserError(_('Some OCR lines have unsupported apply actions. Lines: %s') % ', '.join(invalid_action))
        if incomplete:
            raise UserError(_(
                'Не всі рядки зіставлено з рядками рахунку. '
                'Перевірте Review. Рядки: %s'
            ) % ', '.join(incomplete))
        if missing_merge_target:
            raise UserError(_('Some OCR lines are marked as merge_into but have no target line. Lines: %s') % ', '.join(missing_merge_target))
        if invalid_merge_target:
            raise UserError(_('Some OCR lines have invalid merge targets. Target must be a create_line in the same Review. Lines: %s') % ', '.join(invalid_merge_target))
        if invalid:
            raise UserError(_(
                'Lines with matching errors cannot be applied. Lines: %s'
            ) % ', '.join(invalid))
        if duplicate_move_lines:
            raise UserError(_(
                'One vendor bill line cannot be assigned to several OCR lines. Lines: %s'
            ) % ', '.join(duplicate_move_lines))
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

    def _prepare_partial_bill_apply_plan(self, move):
        errors = []
        apply_plans = []
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )
        for line in create_lines.sorted('sequence'):
            try:
                merged_lines = self.line_ids.filtered(
                    lambda child: child.apply_action == 'merge_into'
                    and child.merge_target_line_id == line
                ).sorted('sequence')
                final_tax_rate = self._get_full_bill_plan_tax_rate(line, merged_lines)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(line, merged_lines)
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                )
                quantity, price_unit = self._convert_partial_bill_plan_values(
                    line,
                    line.move_line_id,
                    quantity,
                    price_unit,
                    amount_untaxed,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    move,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                apply_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
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
        return apply_plans

    def _validate_partial_bill_apply_plans(self, apply_plans, move):
        errors = []
        used_move_line_ids = {}
        for plan in apply_plans:
            line = plan['line']
            move_line = line.move_line_id
            label = line._display_label()
            if not move_line or move_line.move_id != move:
                errors.append(_(
                    'Вибраний рядок рахунку не належить до рахунку, що обробляється: %s'
                ) % label)
                continue
            if not move_line.product_id:
                errors.append(_(
                    'У вибраному рядку рахунку не вказано товар: %s'
                ) % label)
                continue
            if (
                line.matched_product_id
                and line.matched_product_id != move_line.product_id
            ):
                errors.append(_(
                    'Зіставлений товар не відповідає товару у вибраному рядку рахунку: %s'
                ) % label)

            if move_line.id in used_move_line_ids:
                errors.append(_(
                    'One vendor bill line cannot be assigned to several OCR lines: %s'
                ) % label)
            else:
                used_move_line_ids[move_line.id] = line

            if not self._is_positive_number(plan['quantity']):
                errors.append(_(
                    '%s: OCR quantity must be greater than zero before Apply.'
                ) % label)

            for source_line in (line | plan['merged_lines']).sorted('sequence'):
                if not self._is_partial_uom_compatible(source_line, move_line):
                    errors.append(_(
                        'Товари рахунку зіставлено, але кількість неможливо безпечно перенести: '
                        'одиниця виміру в документі «%(ocr_uom)s», а в рядку рахунку «%(line_uom)s». '
                        'Перевірте налаштування одиниць виміру або упаковки товару.'
                    ) % {
                        'ocr_uom': source_line.uom_name or '',
                        'line_uom': self._get_move_line_uom_name(move_line) or '',
                    }
                    )
                    break

            # Quantity mismatch is allowed in partial_bill; OCR quantity is applied after validation.
            source_lines = []
            for source_line in source_lines:
                recognized_quantity = self._to_float(source_line.quantity)
                if not self._is_number(recognized_quantity):
                    continue
                if not self._numbers_close(
                    recognized_quantity,
                    move_line.quantity,
                    tolerance=0.0001,
                ):
                    errors.append(_(
                        'Кількість у розпізнаному документі не збігається '
                        'з кількістю у рядку рахунку. Перевірте відповідність '
                        'товару перед застосуванням. Рядок: %s'
                    ) % source_line._display_label())

        if errors:
            raise UserError('\n'.join(errors))
        return True

    def _validate_partial_purchase_apply_plans(self, apply_plans, order):
        errors = []
        used_order_line_ids = {}
        for plan in apply_plans:
            line = plan['line']
            order_line = line.purchase_order_line_id
            label = line._display_label()
            if not order_line or order_line.order_id != order:
                errors.append(_(
                    'Selected purchase order line does not belong to the reviewed order: %s'
                ) % label)
                continue
            if not order_line.product_id:
                errors.append(_(
                    'Selected purchase order line has no product: %s'
                ) % label)
                continue
            if (
                line.matched_product_id
                and line.matched_product_id != order_line.product_id
            ):
                errors.append(_(
                    'Matched product does not match selected purchase order line product: %s'
                ) % label)

            if order_line.id in used_order_line_ids:
                errors.append(_(
                    'One purchase order line cannot be assigned to several OCR lines: %s'
                ) % label)
            else:
                used_order_line_ids[order_line.id] = line

            if not self._is_positive_number(plan['quantity']):
                errors.append(_(
                    '%s: OCR quantity must be greater than zero before Apply.'
                ) % label)

            if not self._is_partial_purchase_uom_compatible(line, order_line):
                errors.append(_(
                    'Товари замовлення зіставлено, але кількість неможливо безпечно перенести: '
                    'одиниця виміру в документі «%(ocr_uom)s», а в рядку замовлення «%(line_uom)s». '
                    'Перевірте налаштування одиниць виміру або упаковки товару.'
                ) % {
                    'ocr_uom': line.uom_name or '',
                    'line_uom': self._get_purchase_line_uom_name(order_line) or '',
                })

        if errors:
            raise UserError('\n'.join(errors))
        return True

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
                    or not self._is_line_in_apply_context(line.merge_target_line_id)
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

    def _validate_partial_purchase_review_lines(self):
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to apply.'))

        incomplete = []
        invalid = []
        missing_price = []
        missing_quantity = []
        duplicate_order_lines = []
        invalid_action = []
        used_order_line_ids = {}
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )

        if not create_lines:
            raise UserError(_('At least one OCR line must update a purchase order line.'))

        for line in self.line_ids:
            label = line._display_label()
            action = line.apply_action or 'create_line'
            if action != 'create_line':
                invalid_action.append(label)
                continue
            if not line.purchase_order_line_id:
                incomplete.append(label)
                continue
            if line.match_status == 'error':
                invalid.append(label)
                continue
            if line.match_status not in ('matched', 'manual'):
                incomplete.append(label)
            if line.purchase_order_line_id.id in used_order_line_ids:
                duplicate_order_lines.append(label)
            else:
                used_order_line_ids[line.purchase_order_line_id.id] = line
            if not self._is_positive_number(line.quantity):
                missing_quantity.append(label)
            if not self._is_positive_number(line.price_unit):
                missing_price.append(label)

        if invalid_action:
            raise UserError(_('Automatic apply is disabled when OCR lines use merge or skip actions.'))
        if incomplete:
            raise UserError(_(
                'Оцифрування завершено, але не вдалося однозначно зіставити %(count)s рядків із товарами замовлення. '
                'Дані не були застосовані автоматично.'
            ) % {
                'count': len(set(incomplete)),
            })
        if invalid:
            raise UserError(_('Lines with matching errors cannot be applied. Lines: %s') % ', '.join(invalid))
        if duplicate_order_lines:
            raise UserError(_(
                'One purchase order line cannot be assigned to several OCR lines. Lines: %s'
            ) % ', '.join(duplicate_order_lines))
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

    def _validate_full_purchase_review_lines(self):
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to apply.'))

        missing_product = []
        invalid = []
        missing_quantity = []
        missing_price = []
        invalid_action = []
        missing_merge_target = []
        invalid_merge_target = []
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )
        if not create_lines:
            raise UserError(_('At least one OCR line must create a purchase order line.'))

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
                    or not self._is_line_in_apply_context(line.merge_target_line_id)
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
            raise UserError(_(
                'Some OCR lines have unsupported apply actions. Lines: %s'
            ) % ', '.join(invalid_action))
        if invalid:
            raise UserError(_(
                'Lines with matching errors cannot be applied. Lines: %s'
            ) % ', '.join(invalid))
        if missing_merge_target:
            raise UserError(_(
                'Some OCR lines are marked as merge_into but have no target line. Lines: %s'
            ) % ', '.join(missing_merge_target))
        if invalid_merge_target:
            raise UserError(_(
                'Some OCR lines have invalid merge targets. Target must be a create_line in the same Review. Lines: %s'
            ) % ', '.join(invalid_merge_target))
        if missing_product:
            raise UserError(_(
                'Not all purchase order lines have an Odoo product selected. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_product))
        if missing_quantity:
            raise UserError(_(
                'Not all purchase order lines have a positive quantity. '
                'Please check Review. Lines: %s'
            ) % ', '.join(missing_quantity))
        if missing_price:
            raise UserError(_(
                'Not all purchase order lines have a positive unit price. '
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
                final_tax_rate = self._get_full_bill_plan_tax_rate(line, merged_lines)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(line, merged_lines)
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                )
                quantity, price_unit = self._convert_full_document_plan_values(
                    line,
                    line.matched_product_id,
                    quantity,
                    price_unit,
                    amount_untaxed,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    move,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                create_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
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

    def _prepare_full_bill_partial_apply_plan(self, move):
        create_plans = []
        skipped_reasons = {}
        applied_source_line_ids = set()
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )

        for line in create_lines.sorted('sequence'):
            merged_lines = self.line_ids.filtered(
                lambda child: child.apply_action == 'merge_into'
                and child.merge_target_line_id == line
            ).sorted('sequence')
            source_lines = line | merged_lines
            try:
                self._validate_full_bill_create_candidate(line)
                final_tax_rate = self._get_full_bill_plan_tax_rate(line, merged_lines)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(line, merged_lines)
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                )
                quantity, price_unit = self._convert_full_document_plan_values(
                    line,
                    line.matched_product_id,
                    quantity,
                    price_unit,
                    amount_untaxed,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    move,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                create_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
                    'quantity': quantity,
                    'price_unit': price_unit,
                    'amount_untaxed': amount_untaxed,
                    'amount_tax': amount_tax,
                    'amount_total': amount_total,
                    'existing_move_line': self._find_existing_gemini_move_line(
                        move,
                        line,
                        line.matched_product_id,
                    ),
                })
                applied_source_line_ids.update(source_lines.ids)
            except UserError as error:
                reason = self._get_error_message(error)
                for source_line in source_lines:
                    skipped_reasons[source_line.id] = reason

        for line in self.line_ids:
            if line.id in applied_source_line_ids or line.id in skipped_reasons:
                continue
            action = line.apply_action or 'create_line'
            if action == 'skip':
                skipped_reasons[line.id] = _('OCR line was skipped.')
            elif action == 'merge_into':
                skipped_reasons[line.id] = _(
                    'Merge target was not applied or is invalid.'
                )
            else:
                skipped_reasons[line.id] = _(
                    'OCR line was not eligible for automatic apply.'
                )

        return create_plans, skipped_reasons

    def _prepare_partial_purchase_apply_plan(self, order):
        errors = []
        apply_plans = []
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )
        for line in create_lines.sorted('sequence'):
            try:
                final_tax_rate = self._normalize_tax_rate(line.tax_rate)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(
                    line,
                    self.env['account.gemini.digitization.line'],
                )
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    self.env['account.gemini.digitization.line'],
                )
                quantity, price_unit = self._convert_partial_purchase_plan_values(
                    line,
                    line.purchase_order_line_id,
                    quantity,
                    price_unit,
                    amount_untaxed,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    order,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                apply_plans.append({
                    'line': line,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
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
        return apply_plans

    def _validate_full_bill_create_candidate(self, line):
        action = line.apply_action or 'create_line'
        if action != 'create_line':
            raise UserError(_('%s: OCR line is not marked to create a line.') % (
                line._display_label(),
            ))
        if line.match_status == 'error':
            raise UserError(_('%s: product matching failed.') % line._display_label())
        if line.match_status not in ('matched', 'manual'):
            raise UserError(_('%s: product was not confidently matched.') % (
                line._display_label(),
            ))
        if not line.matched_product_id:
            raise UserError(_('%s: no Odoo product was selected.') % (
                line._display_label(),
            ))
        if not self._is_positive_number(line.quantity):
            raise UserError(_('%s: OCR quantity must be greater than zero.') % (
                line._display_label(),
            ))
        if not self._is_positive_number(line.price_unit):
            raise UserError(_('%s: OCR unit price must be greater than zero.') % (
                line._display_label(),
            ))
        product = line.matched_product_id
        uom = getattr(product, 'uom_po_id', False) or getattr(product, 'uom_id', False)
        if not uom:
            raise UserError(_('%s: matched product has no unit of measure.') % (
                line._display_label(),
            ))
        return True

    def _prepare_full_purchase_apply_plan(self, order):
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
                final_tax_rate = self._get_full_bill_plan_tax_rate(line, merged_lines)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(
                    line,
                    merged_lines,
                )
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                    quantity_error_message=_(
                        'Обʼєднані рядки мають різну кількість. '
                        'Відредагуйте кількість і ціну цільового рядка вручну '
                        'або не обʼєднуйте їх автоматично.'
                    ),
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    order,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                create_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
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

    def _prepare_full_purchase_partial_apply_plan(self, order):
        create_plans = []
        skipped_reasons = {}
        applied_source_line_ids = set()
        used_existing_purchase_order_line_ids = set()
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )

        for line in create_lines.sorted('sequence'):
            merged_lines = self.line_ids.filtered(
                lambda child: child.apply_action == 'merge_into'
                and child.merge_target_line_id == line
            ).sorted('sequence')
            source_lines = line | merged_lines
            try:
                self._validate_full_bill_create_candidate(line)
                final_tax_rate = self._get_full_bill_plan_tax_rate(line, merged_lines)
                final_price_tax_mode = self._get_full_bill_plan_price_tax_mode(
                    line,
                    merged_lines,
                )
                quantity, price_unit, amount_untaxed = self._get_full_bill_plan_values(
                    line,
                    merged_lines,
                    quantity_error_message=_(
                        'Merged OCR lines have different quantities. '
                        'Edit target quantity and price manually or do not merge them automatically.'
                    ),
                )
                quantity, price_unit = self._convert_full_document_plan_values(
                    line,
                    line.matched_product_id,
                    quantity,
                    price_unit,
                    amount_untaxed,
                )
                tax_ids, _tax_warning = self._get_line_taxes(
                    line,
                    order,
                    strict=True,
                    tax_rate_override=final_tax_rate,
                    price_tax_mode_override=final_price_tax_mode,
                )
                amount_tax, amount_total = self._get_full_bill_plan_tax_amounts(
                    amount_untaxed,
                    final_tax_rate,
                )
                existing_purchase_order_line = self._find_existing_gemini_purchase_line(
                    order,
                    line,
                    line.matched_product_id,
                )
                if (
                    existing_purchase_order_line
                    and existing_purchase_order_line.id in used_existing_purchase_order_line_ids
                ):
                    raise UserError(_(
                        '%s: existing Gemini purchase order line is already assigned to another OCR line.'
                    ) % line._display_label())
                if existing_purchase_order_line:
                    used_existing_purchase_order_line_ids.add(existing_purchase_order_line.id)
                create_plans.append({
                    'line': line,
                    'merged_lines': merged_lines,
                    'tax_ids': tax_ids,
                    'tax_rate': final_tax_rate,
                    'price_tax_mode': final_price_tax_mode,
                    'quantity': quantity,
                    'price_unit': price_unit,
                    'amount_untaxed': amount_untaxed,
                    'amount_tax': amount_tax,
                    'amount_total': amount_total,
                    'existing_purchase_order_line': existing_purchase_order_line,
                })
                applied_source_line_ids.update(source_lines.ids)
            except UserError as error:
                reason = self._get_error_message(error)
                for source_line in source_lines:
                    skipped_reasons[source_line.id] = reason

        for line in self.line_ids:
            if line.id in applied_source_line_ids or line.id in skipped_reasons:
                continue
            action = line.apply_action or 'create_line'
            if action == 'skip':
                skipped_reasons[line.id] = _('OCR line was skipped.')
            elif action == 'merge_into':
                skipped_reasons[line.id] = _(
                    'Merge target was not applied or is invalid.'
                )
            else:
                skipped_reasons[line.id] = _(
                    'OCR line was not eligible for automatic apply.'
                )

        return create_plans, skipped_reasons

    def _get_full_bill_plan_values(
        self,
        line,
        merged_lines,
        quantity_error_message=False,
    ):
        if not merged_lines:
            return (
                line.quantity,
                self._line_price_unit(line),
                self._line_subtotal(line) or line.quantity * self._line_price_unit(line),
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
                    self._line_price_unit(line),
                    self._line_subtotal(line) or line.quantity * self._line_price_unit(line),
                )
            if quantity_error_message:
                raise UserError(quantity_error_message)
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

    def _get_full_bill_plan_tax_rate(self, line, merged_lines):
        target_rate = self._normalize_tax_rate(line.tax_rate)
        child_rates = [
            self._normalize_tax_rate(merged_line.tax_rate)
            for merged_line in merged_lines
            if self._is_number(self._normalize_tax_rate(merged_line.tax_rate))
        ]
        if not child_rates:
            return target_rate

        unique_child_rates = []
        for child_rate in child_rates:
            if not any(
                self._numbers_close(child_rate, known_rate, tolerance=0.0001)
                for known_rate in unique_child_rates
            ):
                unique_child_rates.append(child_rate)

        if len(unique_child_rates) > 1:
            raise UserError(_(
                '%s: merged OCR lines have different tax rates. '
                'Split them or correct tax rates before Apply.'
            ) % line._display_label())

        child_rate = unique_child_rates[0]
        if not self._is_number(target_rate):
            return child_rate
        if not self._numbers_close(target_rate, child_rate, tolerance=0.0001):
            raise UserError(_(
                '%(line)s: merged OCR lines use %(child_rate).4g%% VAT, '
                'but target line uses %(target_rate).4g%% VAT.'
            ) % {
                'line': line._display_label(),
                'child_rate': child_rate,
                'target_rate': target_rate,
            })
        return target_rate

    def _get_full_bill_plan_price_tax_mode(self, line, merged_lines):
        target_mode = self._line_price_tax_mode(line)
        if target_mode == 'unknown':
            target_mode = self._job_document_price_tax_mode()
        child_modes = []
        for merged_line in merged_lines:
            child_mode = self._line_price_tax_mode(merged_line)
            if child_mode == 'unknown':
                child_mode = self._job_document_price_tax_mode()
            if child_mode != 'unknown' and child_mode not in child_modes:
                child_modes.append(child_mode)

        if len(child_modes) > 1:
            raise UserError(_(
                '%s: merged OCR lines have different price VAT modes. '
                'Split them or correct OCR result before Apply.'
            ) % line._display_label())

        if not child_modes:
            return target_mode

        child_mode = child_modes[0]
        if target_mode == 'unknown':
            return child_mode
        if target_mode != child_mode:
            raise UserError(_(
                '%(line)s: merged OCR lines use %(child_mode)s price VAT mode, '
                'but target line uses %(target_mode)s price VAT mode.'
            ) % {
                'line': line._display_label(),
                'child_mode': child_mode,
                'target_mode': target_mode,
            })
        return target_mode

    def _job_document_price_tax_mode(self):
        mode = getattr(self.job_id, 'document_price_tax_mode', False) or 'unknown'
        if mode in ('included', 'excluded'):
            return mode
        return 'unknown'

    def _validate_merge_tax_rates(self, line, merged_lines):
        target_rate = self._normalize_tax_rate(line.tax_rate)
        if not self._is_number(target_rate):
            self._get_full_bill_plan_tax_rate(line, merged_lines)
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
        if self._line_price_tax_mode(line) == 'included':
            return self._first_number(
                line.price_unit_with_tax,
                line.price_unit,
            )
        return self._first_number(
            line.price_unit_without_tax,
            line.price_unit,
        )

    def _line_subtotal(self, line):
        subtotal = self._first_number(
            line.amount_untaxed,
            line.line_subtotal_without_tax,
        )
        if self._is_number(subtotal):
            return subtotal
        if self._line_price_tax_mode(line) != 'included':
            return subtotal
        tax_rate = self._normalize_tax_rate(line.tax_rate)
        if not self._is_positive_number(tax_rate):
            return subtotal
        total = self._first_number(
            line.amount_total,
            line.line_total_with_tax,
        )
        if self._is_number(total):
            return total / (1 + tax_rate / 100.0)
        quantity = self._to_float(line.quantity)
        price_unit = self._line_price_unit(line)
        if self._is_number(quantity) and self._is_number(price_unit):
            return quantity * price_unit / (1 + tax_rate / 100.0)
        return subtotal

    def _prepare_full_bill_invoice_line_values(self, plan, move):
        line = plan['line']
        product = line.matched_product_id
        self._log_price_mode_apply(plan, plan['tax_ids'], move)
        values = {
            'product_id': product.id,
            'name': self._get_full_bill_line_name(line, product, plan['merged_lines']),
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
        }
        values.update(self._gemini_source_values(line))
        account = self._get_product_expense_account(product, move)
        if account:
            values['account_id'] = account.id
        uom = getattr(product, 'uom_po_id', False) or getattr(product, 'uom_id', False)
        if uom:
            values['product_uom_id'] = uom.id
        values['tax_ids'] = [(6, 0, plan['tax_ids'].ids)]
        return values

    def _mark_full_bill_line_skipped(self, line, reason):
        summary_reason = reason or _('Not eligible for automatic apply.')
        if len(summary_reason) > 160:
            summary_reason = '%s...' % summary_reason[:157]
        line.job_line_id.write({
            'move_line_id': False,
            'match_summary': _('Skipped: %s') % summary_reason,
            'note': self._append_text(
                line.job_line_id.note,
                _('Skipped during automatic full bill apply: %s') % (reason or ''),
            ),
        })

    def _get_move_form_action(self, move):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Рахунок постачальника'),
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _get_purchase_order_form_action(self, order):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Замовлення на закупівлю'),
            'res_model': 'purchase.order',
            'res_id': order.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _apply_purchase_order_header(self, order):
        self.ensure_one()
        values = {}
        warnings = []

        if self.recognized_invoice_number:
            current_ref = (order.partner_ref or '').strip()
            recognized_ref = str(self.recognized_invoice_number).strip()
            if not current_ref:
                values['partner_ref'] = recognized_ref
            elif current_ref != recognized_ref:
                warnings.append(_(
                    'Референс постачальника вже заповнений іншим значенням: %(old)s. '
                    'Розпізнаний номер документа: %(new)s.'
                ) % {
                    'old': current_ref,
                    'new': recognized_ref,
                })

        if self.recognized_invoice_date:
            date_field_name = self._get_purchase_order_supplier_document_date_field(order)
            if date_field_name:
                date_warning = self._prepare_purchase_order_date_header_value(
                    order,
                    date_field_name,
                    values,
                )
                if date_warning:
                    warnings.append(date_warning)
            else:
                warnings.append(_(
                    'Розпізнану дату документа постачальника не перенесено, '
                    'бо в замовленні на закупівлю немає відповідного поля.'
                ))

        if values:
            order.write(values)
        for warning in warnings:
            _logger.info(
                'Gemini OCR purchase order header warning for purchase.order %s: %s',
                order.id,
                warning,
            )
        return warnings

    def _get_purchase_order_supplier_document_date_field(self, order):
        candidate_fields = (
            'supplier_invoice_date',
            'supplier_document_date',
            'vendor_invoice_date',
            'vendor_document_date',
            'partner_invoice_date',
            'partner_document_date',
            'document_date',
            'invoice_date',
            'date_order',
        )
        for field_name in candidate_fields:
            field = order._fields.get(field_name)
            if field and field.type in ('date', 'datetime'):
                return field_name
        return False

    def _prepare_purchase_order_date_header_value(self, order, field_name, values):
        recognized_date = fields.Date.to_date(self.recognized_invoice_date)
        if not recognized_date:
            return False

        field = order._fields[field_name]
        current_value = order[field_name]
        current_date = self._to_header_date(current_value)
        if not current_date:
            values[field_name] = self._get_purchase_order_header_date_write_value(
                recognized_date,
                field,
            )
            return False
        if current_date == recognized_date:
            return False
        if self._can_replace_purchase_order_header_date(order, field_name, current_date):
            values[field_name] = self._get_purchase_order_header_date_write_value(
                recognized_date,
                field,
            )
            return False
        return _(
            'Дата документа постачальника вже заповнена іншим значенням: %(old)s. '
            'Розпізнана дата документа: %(new)s.'
        ) % {
            'old': fields.Date.to_string(current_date),
            'new': fields.Date.to_string(recognized_date),
        }

    def _get_purchase_order_header_date_write_value(self, recognized_date, field):
        if field.type == 'datetime':
            return fields.Datetime.to_datetime(recognized_date)
        return recognized_date

    def _to_header_date(self, value):
        if not value:
            return False
        if hasattr(value, 'date'):
            return value.date()
        return fields.Date.to_date(value)

    def _can_replace_purchase_order_header_date(self, order, field_name, current_date):
        if field_name != 'date_order':
            return False
        create_date = self._to_header_date(order.create_date)
        return bool(create_date and current_date == create_date)

    def _prepare_full_purchase_order_line_values(self, plan, order):
        line = plan['line']
        product = line.matched_product_id
        uom = getattr(product, 'uom_po_id', False) or getattr(product, 'uom_id', False)
        if not uom:
            raise UserError(_(
                '%s: selected product does not have a purchase unit of measure.'
            ) % line._display_label())
        self._log_price_mode_apply(plan, plan['tax_ids'], order)
        values = {
            'order_id': order.id,
            'product_id': product.id,
            'name': self._get_full_bill_line_name(
                line,
                product,
                plan['merged_lines'],
            ),
            'product_qty': plan['quantity'],
            'product_uom': uom.id,
            'price_unit': plan['price_unit'],
            'taxes_id': [(6, 0, plan['tax_ids'].ids)],
            'date_planned': order.date_order or fields.Datetime.now(),
        }
        values.update(self._gemini_source_values(line))
        return values

    def _apply_existing_full_bill_line(self, plan, move):
        move_line = plan['existing_move_line']
        if move_line.move_id != move:
            raise UserError(_('Existing Gemini vendor bill line belongs to another bill.'))
        if move_line.product_id != plan['line'].matched_product_id:
            raise UserError(_('Existing Gemini vendor bill line product no longer matches OCR product.'))
        values = {
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
            'tax_ids': [(6, 0, plan['tax_ids'].ids)],
        }
        values.update(self._gemini_source_values(plan['line']))
        self._log_price_mode_apply(plan, plan['tax_ids'], move)
        move_line.write(values)
        self._write_full_bill_plan_result(move_line, plan, created=False)
        return True

    def _apply_existing_full_purchase_line(self, plan, order):
        order_line = plan['existing_purchase_order_line']
        if order_line.order_id != order:
            raise UserError(_('Existing Gemini purchase order line belongs to another order.'))
        if order_line.product_id != plan['line'].matched_product_id:
            raise UserError(_('Existing Gemini purchase order line product no longer matches OCR product.'))
        values = {
            'product_qty': plan['quantity'],
            'price_unit': plan['price_unit'],
            'taxes_id': [(6, 0, plan['tax_ids'].ids)],
        }
        values.update(self._gemini_source_values(plan['line']))
        self._log_price_mode_apply(plan, plan['tax_ids'], order)
        order_line.write(values)
        self._write_full_purchase_plan_result(order_line, plan, created=False)
        return True

    def _write_full_bill_plan_result(self, move_line, plan, created=True):
        review_line = plan['line']
        tax_ids = plan['tax_ids']
        status, method, score = self._applied_match_values(review_line)
        message = (
            _('Created vendor bill line %s.')
            if created
            else _('Updated existing Gemini vendor bill line %s.')
        ) % move_line.display_name
        note = self._append_text(review_line.job_line_id.note, message)
        if plan['merged_lines']:
            note = self._append_text(
                note,
                _('Merged OCR lines: %s.') % ', '.join(
                    merged_line._display_label() for merged_line in plan['merged_lines']
                ),
            )
        review_line.job_line_id.write({
            'move_line_id': move_line.id,
            'matched_product_id': review_line.matched_product_id.id,
            'apply_action': 'create_line',
            'merge_target_line_id': False,
            'match_status': status,
            'match_score': score,
            'match_method': method,
            'match_summary': review_line.match_summary,
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
            'tax_rate': plan['tax_rate'],
            'price_tax_mode': plan.get('price_tax_mode') or review_line.price_tax_mode or 'unknown',
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
                'merge_target_line_id': review_line.job_line_id.id,
                'move_line_id': move_line.id,
                'matched_product_id': review_line.matched_product_id.id,
                'match_status': 'manual',
                'match_score': merged_line.match_score or 1.0,
                'match_method': 'manual_merge',
                'match_summary': _('Merged into: %s') % review_line._display_label(),
                'note': self._append_text(
                    merged_line.job_line_id.note,
                    _('Merged into vendor bill line %s.') % move_line.display_name,
                ),
            })
        return True

    def _write_full_purchase_plan_result(self, order_line, plan, created=True):
        review_line = plan['line']
        tax_ids = plan['tax_ids']
        status, method, score = self._applied_match_values(review_line)
        message = (
            _('Created purchase order line %s.')
            if created
            else _('Updated existing Gemini purchase order line %s.')
        ) % order_line.display_name
        note = self._append_text(review_line.job_line_id.note, message)
        if plan['merged_lines']:
            note = self._append_text(
                note,
                _('Merged OCR lines: %s.') % ', '.join(
                    merged_line._display_label() for merged_line in plan['merged_lines']
                ),
            )
        review_line.job_line_id.write({
            'purchase_order_line_id': order_line.id,
            'move_line_id': False,
            'matched_product_id': review_line.matched_product_id.id,
            'apply_action': 'create_line',
            'merge_target_line_id': False,
            'match_status': status,
            'match_score': score,
            'match_method': method,
            'match_summary': review_line.match_summary,
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
            'tax_rate': plan['tax_rate'],
            'price_tax_mode': plan.get('price_tax_mode') or review_line.price_tax_mode or 'unknown',
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
                'purchase_order_line_id': order_line.id,
                'move_line_id': False,
                'matched_product_id': review_line.matched_product_id.id,
                'apply_action': 'merge_into',
                'merge_target_line_id': review_line.job_line_id.id,
                'match_status': 'manual',
                'match_score': merged_line.match_score or 1.0,
                'match_method': 'manual_merge',
                'match_summary': _('Merged into: %s') % review_line._display_label(),
                'note': self._append_text(
                    merged_line.job_line_id.note,
                    _('Merged into purchase order line %s.') % order_line.display_name,
                ),
            })
        return True

    def _applied_match_values(self, line):
        status = line.match_status
        method = line.match_method
        score = line.match_score
        if line._is_manual_product_selection() or status not in ('matched', 'manual'):
            status = 'manual'
            method = method or 'manual_product'
            score = score or 1.0
        return status, method, score

    def _mark_full_purchase_line_skipped(self, line, reason):
        summary_reason = reason or _('Not eligible for automatic apply.')
        if len(summary_reason) > 160:
            summary_reason = '%s...' % summary_reason[:157]
        line.job_line_id.write({
            'purchase_order_line_id': False,
            'move_line_id': False,
            'match_summary': _('Skipped: %s') % summary_reason,
            'note': self._append_text(
                line.job_line_id.note,
                _('Skipped during automatic full purchase apply: %s') % (reason or ''),
            ),
        })

    def _gemini_source_values(self, line):
        return {
            'gemini_digitization_auto_created': True,
            'gemini_digitization_source_article': self._line_supplier_article(line) or False,
            'gemini_digitization_technical_code': self._line_technical_code(line) or False,
        }

    def _line_supplier_article(self, line):
        articles = ProductMatcher(self.env)._line_supplier_articles(line)
        return articles[0] if articles else False

    def _line_technical_code(self, line):
        profile = ProductMatcher(self.env)._line_technical_profile(line)
        codes = profile.get('full_codes') if profile else []
        return codes[0] if codes else False

    def _find_existing_gemini_move_line(self, move, line, product):
        candidates = self._get_move_product_lines(move).filtered(
            lambda move_line: getattr(move_line, 'gemini_digitization_auto_created', False)
            and move_line.product_id == product
        )
        return self._find_existing_gemini_business_line(candidates, line)

    def _find_existing_gemini_purchase_line(self, order, line, product):
        candidates = self._get_purchase_product_lines(order).filtered(
            lambda order_line: getattr(order_line, 'gemini_digitization_auto_created', False)
            and order_line.product_id == product
        )
        return self._find_existing_gemini_business_line(candidates, line)

    def _find_existing_gemini_business_line(self, candidates, line):
        if not candidates:
            return candidates

        supplier_article = self._line_supplier_article(line)
        if supplier_article:
            article_matches = candidates.filtered(
                lambda business_line: SupplierArticleNormalizer.equals(
                    supplier_article,
                    getattr(business_line, 'gemini_digitization_source_article', False),
                )
            )
            if len(article_matches) == 1:
                return article_matches
            return self.env[candidates._name]

        technical_code = self._line_technical_code(line)
        if technical_code:
            technical_matches = candidates.filtered(
                lambda business_line: TechnicalCodeNormalizer.equals(
                    technical_code,
                    getattr(business_line, 'gemini_digitization_technical_code', False),
                )
            )
            if len(technical_matches) == 1:
                return technical_matches
            return self.env[candidates._name]

        if len(candidates) == 1:
            return candidates
        return self.env[candidates._name]

    def _convert_full_document_plan_values(
        self,
        line,
        product,
        quantity,
        price_unit,
        amount_untaxed,
    ):
        converted_quantity = self._convert_full_document_quantity(line, product, quantity)
        if not (
            self._is_number(converted_quantity)
            and self._is_number(quantity)
            and self._is_positive_number(converted_quantity)
            and not self._numbers_close(converted_quantity, quantity, tolerance=0.0001)
        ):
            return quantity, price_unit

        if self._is_number(price_unit) and self._is_positive_number(quantity):
            if self._line_uses_price_with_tax(line):
                return converted_quantity, (price_unit * quantity) / converted_quantity
        if self._is_number(amount_untaxed):
            return converted_quantity, amount_untaxed / converted_quantity
        if self._is_number(price_unit) and self._is_positive_number(quantity):
            return converted_quantity, (price_unit * quantity) / converted_quantity
        return converted_quantity, price_unit

    def _convert_full_document_quantity(self, line, product, quantity):
        if not self._is_number(quantity):
            return quantity
        source_uom_name = getattr(line, 'uom_name', False)
        normalized_source_uom = self._normalize_partial_uom(source_uom_name)
        if not normalized_source_uom:
            return quantity

        target_uom = (
            getattr(product, 'uom_po_id', False)
            or getattr(product, 'uom_id', False)
        )
        if not target_uom:
            return quantity

        source_uom = self._find_partial_ocr_uom(source_uom_name, target_uom)
        if not source_uom:
            if self._partial_uom_names_match(source_uom_name, target_uom):
                return quantity
            raise UserError(self._full_document_uom_error_message(line, target_uom))

        if source_uom.category_id != target_uom.category_id:
            raise UserError(self._full_document_uom_error_message(line, target_uom))
        if source_uom == target_uom:
            return quantity
        return source_uom._compute_quantity(quantity, target_uom)

    def _full_document_uom_error_message(self, line, target_uom):
        return _(
            '%(line)s: OCR unit of measure "%(ocr_uom)s" is not compatible with product unit "%(product_uom)s".'
        ) % {
            'line': line._display_label(),
            'ocr_uom': getattr(line, 'uom_name', False) or '',
            'product_uom': getattr(target_uom, 'display_name', False) or getattr(target_uom, 'name', False) or '',
        }

    def _log_price_mode_apply(self, plan, taxes, document):
        line = plan['line']
        tax = taxes[:1] if taxes else False
        price_tax_mode = plan.get('price_tax_mode') or self._line_price_tax_mode(line)
        _logger.info(
            'Gemini OCR price mode: document_model=%s document_id=%s '
            'document_mode=%s line_mode=%s ocr_price=%s selected_tax=%s/%s '
            'selected_tax_price_include=%s final_price_unit=%s',
            getattr(document, '_name', False),
            getattr(document, 'id', False),
            self._job_document_price_tax_mode(),
            price_tax_mode,
            self._line_price_unit(line),
            tax.display_name if tax else False,
            tax.id if tax else False,
            self._tax_is_price_included(tax) if tax else False,
            plan.get('price_unit'),
        )
        if self.mode in ('full_bill', 'full_purchase'):
            _logger.info(
                'Gemini OCR full match: mode=%s supplier_article=%s technical_code=%s '
                'match_method=%s match_score=%s selected_product_id=%s '
                'price_tax_mode=%s selected_tax_price_include=%s',
                self.mode,
                self._line_supplier_article(line) or 'none',
                self._line_technical_code(line) or 'none',
                line.match_method or 'none',
                line.match_score or 0.0,
                line.matched_product_id.id if line.matched_product_id else False,
                price_tax_mode,
                self._tax_is_price_included(tax) if tax else False,
            )

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

    def _get_purchase_product_lines(self, order):
        return order.order_line.filtered(lambda line: self._is_purchase_product_line(line))

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

    def _is_purchase_product_line(self, line):
        if not line.product_id:
            return False
        display_type = getattr(line, 'display_type', False)
        if display_type:
            return False
        return True

    def _apply_review_line(self, plan, move):
        warnings = []
        line = plan['line']
        merged_lines = plan.get('merged_lines') or self.env['account.gemini.digitization.line']
        tax_ids = plan['tax_ids']
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

        values = {
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
        }
        if tax_ids:
            values['tax_ids'] = [(6, 0, tax_ids.ids)]
        self._log_price_mode_apply(plan, tax_ids, move)
        move_line.write(values)

        status = line.match_status
        method = line.match_method
        if line._is_manual_selection() or status not in ('matched', 'manual'):
            status = 'manual'
            method = method or 'manual_move_line'
        match_score = line.match_score
        if status == 'manual' and not match_score:
            match_score = 1.0
        note = self._append_text(
            line.job_line_id.note,
            _('Applied to vendor bill line %s.') % move_line.display_name,
        )
        if merged_lines:
            note = self._append_text(
                note,
                _('Merged OCR lines: %s.') % ', '.join(
                    merged_line._display_label() for merged_line in merged_lines
                ),
            )

        line.job_line_id.write({
            'move_line_id': move_line.id,
            'matched_product_id': matched_product.id,
            'apply_action': 'create_line',
            'merge_target_line_id': False,
            'match_status': status,
            'match_score': match_score,
            'match_method': method,
            'match_summary': line.match_summary,
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
            'tax_rate': plan['tax_rate'],
            'price_tax_mode': plan.get('price_tax_mode') or line.price_tax_mode or 'unknown',
            'tax_ids': [(6, 0, tax_ids.ids)] if tax_ids else [(6, 0, [])],
            'amount_untaxed': plan['amount_untaxed'],
            'amount_tax': plan['amount_tax'],
            'amount_total': plan['amount_total'],
            'line_subtotal_without_tax': plan['amount_untaxed'],
            'line_tax_amount': plan['amount_tax'],
            'line_total_with_tax': plan['amount_total'],
            'note': note,
        })
        for merged_line in merged_lines:
            merged_line.job_line_id.write({
                'apply_action': 'merge_into',
                'merge_target_line_id': line.job_line_id.id,
                'move_line_id': move_line.id,
                'matched_product_id': matched_product.id,
                'match_status': 'manual',
                'match_score': merged_line.match_score or 1.0,
                'match_method': 'manual_merge',
                'match_summary': _('Merged into: %s') % line._display_label(),
                'note': self._append_text(
                    merged_line.job_line_id.note,
                    _('Merged into vendor bill line %s.') % move_line.display_name,
                ),
            })
        return warnings

    def _apply_purchase_review_line(self, plan, order):
        line = plan['line']
        tax_ids = plan['tax_ids']
        order_line = line.purchase_order_line_id
        if order_line.order_id != order:
            raise UserError(_(
                'Selected purchase order line does not belong to the reviewed order: %s'
            ) % line._display_label())

        matched_product = order_line.product_id
        values = {
            'product_qty': plan['quantity'],
            'price_unit': plan['price_unit'],
        }
        if tax_ids:
            values['taxes_id'] = [(6, 0, tax_ids.ids)]
        self._log_price_mode_apply(plan, tax_ids, order)
        order_line.write(values)

        status = line.match_status
        method = line.match_method
        if status not in ('matched', 'manual'):
            status = 'manual'
            method = method or 'manual_purchase_order_line'
        match_score = line.match_score
        if status == 'manual' and not match_score:
            match_score = 1.0

        note = self._append_text(
            line.job_line_id.note,
            _('Applied to purchase order line %s.') % order_line.display_name,
        )
        line.job_line_id.write({
            'purchase_order_line_id': order_line.id,
            'move_line_id': False,
            'matched_product_id': matched_product.id,
            'apply_action': 'create_line',
            'merge_target_line_id': False,
            'match_status': status,
            'match_score': match_score,
            'match_method': method,
            'match_summary': line.match_summary,
            'quantity': plan['quantity'],
            'price_unit': plan['price_unit'],
            'tax_rate': plan['tax_rate'],
            'price_tax_mode': plan.get('price_tax_mode') or line.price_tax_mode or 'unknown',
            'tax_ids': [(6, 0, tax_ids.ids)] if tax_ids else [(6, 0, [])],
            'amount_untaxed': plan['amount_untaxed'],
            'amount_tax': plan['amount_tax'],
            'amount_total': plan['amount_total'],
            'line_subtotal_without_tax': plan['amount_untaxed'],
            'line_tax_amount': plan['amount_tax'],
            'line_total_with_tax': plan['amount_total'],
            'note': note,
        })
        return True

    def _get_line_taxes(
        self,
        line,
        move,
        strict=False,
        tax_rate_override=None,
        price_tax_mode_override=None,
    ):
        tax_rate = self._normalize_tax_rate(
            line.tax_rate if tax_rate_override is None else tax_rate_override
        )
        price_tax_mode = price_tax_mode_override or self._line_price_tax_mode(line)
        if line.tax_ids:
            if strict:
                self._validate_selected_taxes(
                    line,
                    line.tax_ids,
                    tax_rate,
                    price_tax_mode_override=price_tax_mode,
                )
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

        taxes, warning = self._find_purchase_taxes(
            move.company_id,
            tax_rate,
            line=line,
            price_tax_mode=price_tax_mode,
        )
        if taxes:
            try:
                self._validate_selected_taxes(
                    line,
                    taxes,
                    tax_rate,
                    price_tax_mode_override=price_tax_mode,
                )
            except UserError as error:
                if strict:
                    raise
                return self.env['account.tax'], self._get_error_message(error)
            line.tax_ids = [(6, 0, taxes.ids)]
            return taxes, False

        if strict:
            if tax_rate > 0:
                if price_tax_mode == 'included' and warning:
                    raise UserError(warning)
                raise UserError(_(
                    'Для рядка "%(line)s" оберіть податок ПДВ %(rate).4g%% у Review перед Apply. %(details)s'
                ) % {
                    'line': line._display_label(),
                    'rate': tax_rate,
                    'details': warning or '',
                })
            raise UserError(warning)
        return self.env['account.tax'], warning

    def _find_purchase_taxes(self, company, tax_rate, line=False, price_tax_mode=False):
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
            price_tax_mode=price_tax_mode,
        )
        if selected_taxes:
            return selected_taxes, False

        price_basis_warning = ''
        if price_tax_mode == 'included':
            return self.env['account.tax'], _(
                '%(line)s: Не знайдено однозначний податок придбання зі ставкою %(rate).4g%%, '
                'включений у ціну. Налаштуйте податок «Придбання в т. ч. ПДВ». %(candidates)s'
            ) % {
                'line': line._display_label() if line else _('Line'),
                'rate': tax_rate,
                'candidates': self._format_tax_candidates(matching_taxes),
            }
        if price_tax_mode == 'excluded' or self._line_uses_price_without_tax(line):
            price_basis_warning = _(
                ' Preferred non-price-included purchase VAT tax was not selected automatically.'
            )

        return self.env['account.tax'], _(
            '%(line)s: several purchase taxes for %(rate).4g%% were found. '
            'Please select the correct tax manually before Apply.%(price_basis_warning)s %(candidates)s'
        ) % {
            'line': line._display_label() if line else _('Line'),
            'rate': tax_rate,
            'price_basis_warning': price_basis_warning,
            'candidates': self._format_tax_candidates(matching_taxes),
        }

    def _select_best_purchase_tax(
        self,
        taxes,
        company,
        tax_rate,
        line=False,
        price_tax_mode=False,
    ):
        if not price_tax_mode:
            price_tax_mode = self._line_price_tax_mode(line)
        price_include = self._price_mode_to_tax_price_include(price_tax_mode)
        configured_tax = self._get_configured_purchase_vat_tax(
            tax_rate,
            company,
            price_include=price_include,
        )
        if configured_tax and configured_tax.id in taxes.ids:
            return configured_tax

        product_tax = self._get_product_supplier_tax(
            line,
            taxes,
            tax_rate,
            price_tax_mode=price_tax_mode,
        )
        if product_tax:
            return product_tax

        purchase_taxes = taxes.filtered(lambda tax: tax.type_tax_use == 'purchase')
        if purchase_taxes:
            taxes = purchase_taxes

        company_taxes = taxes.filtered(lambda tax: tax.company_id == company)
        if company_taxes:
            taxes = company_taxes

        price_basis_taxes = self._filter_taxes_for_line_price_basis(
            taxes,
            line,
            price_tax_mode=price_tax_mode,
        )
        if price_tax_mode == 'included':
            if len(price_basis_taxes) == 1:
                return price_basis_taxes
            return self.env['account.tax']
        if len(price_basis_taxes) == 1:
            return price_basis_taxes
        if price_basis_taxes:
            taxes = price_basis_taxes
        elif price_tax_mode in ('included', 'excluded') or self._line_uses_price_without_tax(line):
            return self.env['account.tax']

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

    def _get_configured_purchase_vat_tax(self, tax_rate, company, price_include=None):
        if abs((tax_rate or 0.0) - 20.0) > 0.0001:
            return self.env['account.tax']
        parameter_key = (
            'account_gemini_digitization.default_purchase_vat_20_included_tax_id'
            if price_include is True
            else 'account_gemini_digitization.default_purchase_vat_20_tax_id'
        )
        tax_id = self.env['ir.config_parameter'].sudo().get_param(
            parameter_key
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
        if price_include is not None and self._tax_is_price_included(tax) != price_include:
            return self.env['account.tax']
        return tax

    def _get_product_supplier_tax(self, line, taxes, tax_rate, price_tax_mode=False):
        product = getattr(line, 'matched_product_id', False) if line else False
        if not product:
            return self.env['account.tax']
        price_include = self._price_mode_to_tax_price_include(price_tax_mode)
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
            and not (
                price_include is False
                and self._tax_is_price_included(tax)
            )
            and not (
                price_include is True
                and not self._tax_is_price_included(tax)
            )
        )
        if len(matching_product_taxes) == 1:
            return matching_product_taxes
        return self.env['account.tax']

    def _filter_taxes_for_line_price_basis(self, taxes, line, price_tax_mode=False):
        if not taxes or not line:
            return taxes
        if not price_tax_mode:
            price_tax_mode = self._line_price_tax_mode(line)
        price_include = self._price_mode_to_tax_price_include(price_tax_mode)
        if price_include is True:
            return taxes.filtered(lambda tax: self._tax_is_price_included(tax))
        if price_include is False:
            return taxes.filtered(lambda tax: not self._tax_is_price_included(tax))
        return taxes

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

    def _tax_is_price_included(self, tax):
        if getattr(tax, 'price_include', False):
            return True
        name = self._normalize_text(
            '%s %s' % (
                getattr(tax, 'name', False) or '',
                getattr(tax, 'display_name', False) or '',
            )
        )
        return any(
            marker in name
            for marker in (
                'в т. ч.',
                'в т.ч.',
                'в т ч',
                'в тч',
                'у т. ч.',
                'у т.ч.',
                'у т ч',
                'у тч',
                'включ',
                'included',
                'include',
                'including',
            )
        )

    def _price_mode_to_tax_price_include(self, price_tax_mode):
        if price_tax_mode == 'included':
            return True
        if price_tax_mode == 'excluded':
            return False
        return None

    def _line_price_tax_mode(self, line):
        if not line:
            return 'unknown'
        mode = getattr(line, 'price_tax_mode', False) or 'unknown'
        if mode in ('included', 'excluded'):
            return mode

        text = self._normalize_text(' '.join(
            str(value)
            for value in (
                getattr(line, 'source_columns', False),
                getattr(line, 'note', False),
                getattr(line, 'description', False),
                getattr(line, 'supplier_product_name', False),
            )
            if value
        ))
        if self._text_says_price_without_tax(text):
            return 'excluded'
        if self._text_says_price_with_tax(text):
            return 'included'
        return 'unknown'

    def _line_uses_price_with_tax(self, line):
        return self._line_price_tax_mode(line) == 'included'

    def _line_uses_price_without_tax(self, line):
        if not line:
            return False
        mode = self._line_price_tax_mode(line)
        if mode == 'included':
            return False
        if mode == 'excluded':
            return True
        if self._is_positive_number(self._to_float(getattr(line, 'price_unit_without_tax', False))):
            return True
        if self._is_positive_number(self._to_float(getattr(line, 'line_subtotal_without_tax', False))):
            return True
        text = self._normalize_text(' '.join(
            str(value)
            for value in (
                getattr(line, 'source_columns', False),
                getattr(line, 'note', False),
                getattr(line, 'description', False),
                getattr(line, 'supplier_product_name', False),
            )
            if value
        ))
        return self._text_says_price_without_tax(text)

    def _text_says_price_with_tax(self, text):
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                'ціна з пдв',
                'цiна з пдв',
                'цена с ндс',
                'price with vat',
                'price with tax',
                'with vat',
                'with tax',
                'сума з пдв',
                'сумма с ндс',
                'total with vat',
                'total with tax',
            )
        )

    def _text_says_price_without_tax(self, text):
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                'ціна без пдв',
                'цiна без пдв',
                'цена без ндс',
                'price without vat',
                'price without tax',
                'without vat',
                'without tax',
                'сума без пдв',
                'сумма без ндс',
                'subtotal without vat',
                'subtotal without tax',
            )
        )

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
                '[id=%s %s; amount=%s; type=%s; price_include=%s; company_id=%s; active=%s]'
                % (
                    tax.id,
                    tax.display_name or tax.name,
                    tax.amount,
                    tax.type_tax_use,
                    self._tax_is_price_included(tax),
                    company.id if company else False,
                    getattr(tax, 'active', True),
                )
            )
        return _('Tax candidates: %s') % ', '.join(parts)

    def _get_tax_review_summary(self, line, warning):
        tax_rate = self._normalize_tax_rate(line.tax_rate)
        if self._is_number(tax_rate):
            if 'включений у ціну' in str(warning) or 'included in price' in str(warning):
                return _(
                    'Tax review required: select price-included %.4g%% purchase tax'
                ) % tax_rate
            if 'several purchase taxes' in str(warning):
                if 'non-price-included' in str(warning):
                    return _(
                        'Tax review required: several %.4g%% purchase taxes found; choose non-price-included VAT'
                    ) % tax_rate
                return _('Tax review required: several %.4g%% purchase taxes found') % tax_rate
            if 'was not found' in str(warning):
                return _('Tax review required: no %.4g%% purchase tax found') % tax_rate
            if tax_rate == 0:
                return _('Tax review required: confirm 0%% VAT tax')
            return _('Tax review required: select %.4g%% purchase tax') % tax_rate
        return _('Tax review required: select purchase tax')

    def _validate_selected_taxes(
        self,
        line,
        taxes,
        tax_rate,
        price_tax_mode_override=None,
    ):
        if not self._is_number(tax_rate):
            return True
        price_tax_mode = price_tax_mode_override or self._line_price_tax_mode(line)
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
            if price_tax_mode == 'included':
                not_included_taxes = matching_taxes.filtered(
                    lambda tax: not self._tax_is_price_included(tax)
                )
                if not_included_taxes:
                    raise UserError(_(
                        'Для рядка "%(line)s" OCR розпізнав ціну з ПДВ, '
                        'але вибраний податок не включений у ціну. '
                        'Оберіть податок придбання зі ставкою %(rate).4g%%, включений у ціну.'
                    ) % {
                        'line': line._display_label(),
                        'rate': tax_rate,
                    })
            elif price_tax_mode == 'excluded' or self._line_uses_price_without_tax(line):
                price_included_taxes = matching_taxes.filtered(
                    lambda tax: self._tax_is_price_included(tax)
                )
                if price_included_taxes:
                    raise UserError(_(
                        'Для рядка "%(line)s" вибраний податок включено в ціну, '
                        'але OCR розпізнав ціну без ПДВ. Оберіть податок без включення в ціну.'
                    ) % {
                        'line': line._display_label(),
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

    def _convert_partial_bill_plan_values(
        self,
        line,
        move_line,
        quantity,
        price_unit,
        amount_untaxed,
    ):
        converted_quantity = self._convert_partial_bill_quantity(
            line,
            move_line,
            quantity,
        )
        if not (
            self._is_number(converted_quantity)
            and self._is_number(quantity)
            and self._is_positive_number(converted_quantity)
            and not self._numbers_close(converted_quantity, quantity, tolerance=0.0001)
        ):
            return quantity, price_unit

        if self._is_number(price_unit) and self._is_positive_number(quantity):
            if self._line_uses_price_with_tax(line):
                return converted_quantity, (price_unit * quantity) / converted_quantity
        if self._is_number(amount_untaxed):
            return converted_quantity, amount_untaxed / converted_quantity
        if self._is_number(price_unit) and self._is_positive_number(quantity):
            return converted_quantity, (price_unit * quantity) / converted_quantity
        return converted_quantity, price_unit

    def _convert_partial_purchase_plan_values(
        self,
        line,
        order_line,
        quantity,
        price_unit,
        amount_untaxed,
    ):
        converted_quantity = self._convert_partial_purchase_quantity(
            line,
            order_line,
            quantity,
        )
        if not (
            self._is_number(converted_quantity)
            and self._is_number(quantity)
            and self._is_positive_number(converted_quantity)
            and not self._numbers_close(converted_quantity, quantity, tolerance=0.0001)
        ):
            return quantity, price_unit

        if self._is_number(price_unit) and self._is_positive_number(quantity):
            if self._line_uses_price_with_tax(line):
                return converted_quantity, (price_unit * quantity) / converted_quantity
        if self._is_number(amount_untaxed):
            return converted_quantity, amount_untaxed / converted_quantity
        if self._is_number(price_unit) and self._is_positive_number(quantity):
            return converted_quantity, (price_unit * quantity) / converted_quantity
        return converted_quantity, price_unit

    def _convert_partial_bill_quantity(self, line, move_line, quantity):
        if not self._is_number(quantity):
            return quantity
        source_uom_name = getattr(line, 'uom_name', False)
        normalized_source_uom = self._normalize_partial_uom(source_uom_name)
        if not normalized_source_uom:
            return quantity

        target_uom = self._get_move_line_uom(move_line)
        if not target_uom:
            return quantity

        source_uom = self._find_partial_ocr_uom(source_uom_name, target_uom)
        if not source_uom:
            if self._partial_uom_names_match(source_uom_name, target_uom):
                return quantity
            raise UserError(self._partial_uom_error_message(line, move_line))

        if source_uom.category_id != target_uom.category_id:
            raise UserError(self._partial_uom_error_message(line, move_line))
        if source_uom == target_uom:
            return quantity
        return source_uom._compute_quantity(quantity, target_uom)

    def _convert_partial_purchase_quantity(self, line, order_line, quantity):
        if not self._is_number(quantity):
            return quantity
        source_uom_name = getattr(line, 'uom_name', False)
        normalized_source_uom = self._normalize_partial_uom(source_uom_name)
        if not normalized_source_uom:
            return quantity

        target_uom = self._get_purchase_line_uom(order_line)
        if not target_uom:
            return quantity

        source_uom = self._find_partial_ocr_uom(source_uom_name, target_uom)
        if not source_uom:
            if self._partial_uom_names_match(source_uom_name, target_uom):
                return quantity
            raise UserError(self._partial_purchase_uom_error_message(line, order_line))

        if source_uom.category_id != target_uom.category_id:
            raise UserError(self._partial_purchase_uom_error_message(line, order_line))
        if source_uom == target_uom:
            return quantity
        return source_uom._compute_quantity(quantity, target_uom)

    def _is_partial_uom_compatible(self, line, move_line):
        quantity = self._to_float(getattr(line, 'quantity', False))
        if not self._is_number(quantity):
            return True
        try:
            self._convert_partial_bill_quantity(line, move_line, quantity)
        except UserError:
            return False
        return True

    def _is_partial_purchase_uom_compatible(self, line, order_line):
        quantity = self._to_float(getattr(line, 'quantity', False))
        if not self._is_number(quantity):
            return True
        try:
            self._convert_partial_purchase_quantity(line, order_line, quantity)
        except UserError:
            return False
        return True

    def _get_move_line_uom(self, move_line):
        return (
            getattr(move_line, 'product_uom_id', False)
            or getattr(move_line, 'product_uom', False)
        )

    def _get_move_line_uom_name(self, move_line):
        move_uom = self._get_move_line_uom(move_line)
        return (
            getattr(move_uom, 'display_name', False)
            or getattr(move_uom, 'name', False)
            or False
        )

    def _get_purchase_line_uom(self, order_line):
        return getattr(order_line, 'product_uom', False)

    def _get_purchase_line_uom_name(self, order_line):
        purchase_uom = self._get_purchase_line_uom(order_line)
        return (
            getattr(purchase_uom, 'display_name', False)
            or getattr(purchase_uom, 'name', False)
            or False
        )

    def _find_partial_ocr_uom(self, value, target_uom=False):
        normalized = self._normalize_partial_uom(value)
        if not normalized:
            return False
        if target_uom and normalized == self._normalize_partial_uom(target_uom.name):
            return target_uom

        candidates = self.env['uom.uom'].search([])
        matching_uoms = candidates.filtered(
            lambda uom: normalized in (
                self._normalize_partial_uom(uom.name),
                self._normalize_partial_uom(uom.display_name),
            )
        )
        if target_uom:
            category_matches = matching_uoms.filtered(
                lambda uom: uom.category_id == target_uom.category_id
            )
            if len(category_matches) == 1:
                return category_matches
            if target_uom in category_matches:
                return target_uom
        if len(matching_uoms) == 1:
            return matching_uoms
        return False

    def _partial_uom_names_match(self, value, target_uom):
        normalized = self._normalize_partial_uom(value)
        if not normalized or not target_uom:
            return False
        return normalized in (
            self._normalize_partial_uom(target_uom.name),
            self._normalize_partial_uom(target_uom.display_name),
        )

    def _partial_uom_error_message(self, line, move_line):
        return _(
            'Товари рахунку зіставлено, але кількість неможливо безпечно перенести: '
            'одиниця виміру в документі «%(ocr_uom)s», а в рядку рахунку «%(line_uom)s». '
            'Перевірте налаштування одиниць виміру або упаковки товару.'
        ) % {
            'ocr_uom': getattr(line, 'uom_name', False) or '',
            'line_uom': self._get_move_line_uom_name(move_line) or '',
        }

    def _partial_purchase_uom_error_message(self, line, order_line):
        return _(
            'Товари замовлення зіставлено, але кількість неможливо безпечно перенести: '
            'одиниця виміру в документі «%(ocr_uom)s», а в рядку замовлення «%(line_uom)s». '
            'Перевірте налаштування одиниць виміру або упаковки товару.'
        ) % {
            'ocr_uom': getattr(line, 'uom_name', False) or '',
            'line_uom': self._get_purchase_line_uom_name(order_line) or '',
        }

    def _normalize_partial_uom(self, value):
        if not value:
            return False
        normalized = self._normalize_text(value)
        normalized = normalized.strip(' .,:;')
        normalized = normalized.replace('.', '')
        unit_aliases = {
            'шт',
            'штука',
            'штуки',
            'штук',
            'од',
            'одиниця',
            'одиниці',
            'одиниць',
            'pc',
            'pcs',
            'piece',
            'pieces',
            'unit',
            'units',
        }
        service_aliases = {
            'послуга',
            'послуги',
            'service',
            'services',
        }
        package_aliases = {
            'компл',
            'комплект',
            'комплекти',
            'уп',
            'упак',
            'упаковка',
            'package',
            'pack',
            'set',
            'kit',
        }
        if normalized in unit_aliases:
            return 'unit'
        if normalized in service_aliases:
            return 'service'
        if normalized in package_aliases:
            return 'set'
        return normalized

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
            [_('Apply completed with warnings:')] + [str(warning) for warning in warnings]
        )

    def _is_line_in_apply_context(self, line):
        return bool(line and line.job_id == self.job)

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
