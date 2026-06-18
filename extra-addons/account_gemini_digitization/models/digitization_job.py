import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import GeminiClient, ProductMatcher, ResponseParser


_logger = logging.getLogger(__name__)


class AccountGeminiDigitizationJob(models.Model):
    _name = 'account.gemini.digitization.job'
    _description = 'Gemini Digitization Job'
    _order = 'create_date desc, id desc'

    STATE_SELECTION = [
        ('draft', 'Draft'),
        ('processing', 'Processing'),
        ('review', 'Review'),
        ('done', 'Done'),
        ('error', 'Error'),
        ('cancelled', 'Cancelled'),
    ]

    MODE_SELECTION = [
        ('partial_bill', 'Partial Vendor Bill Recognition'),
        ('full_bill', 'Full Vendor Bill Recognition'),
        ('full_purchase', 'Full Purchase Order Recognition'),
    ]

    name = fields.Char(
        required=True,
        default=lambda self: _('New Digitization Job'),
    )
    state = fields.Selection(
        selection=STATE_SELECTION,
        required=True,
        default='draft',
        copy=False,
    )
    mode = fields.Selection(
        selection=MODE_SELECTION,
        required=True,
        default='partial_bill',
    )
    res_model = fields.Char(
    )
    res_id = fields.Integer(
    )
    move_id = fields.Many2one(
        comodel_name='account.move',
        string='Vendor Bill',
        ondelete='set null',
    )
    purchase_order_id = fields.Many2one(
        comodel_name='purchase.order',
        string='Purchase Order',
        ondelete='set null',
    )
    partner_id = fields.Many2one(
        comodel_name='res.partner',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        comodel_name='res.currency',
        default=lambda self: self.env.company.currency_id,
    )
    attachment_id = fields.Many2one(
        comodel_name='ir.attachment',
        string='Source Attachment',
        ondelete='set null',
    )
    raw_request_json = fields.Json(
        string='Raw Request JSON',
        copy=False,
    )
    raw_response_json = fields.Json(
        string='Raw Response JSON',
        copy=False,
    )
    raw_request_json_text = fields.Text(
        string='Raw Request JSON',
        compute='_compute_raw_json_text',
        readonly=True,
    )
    raw_response_json_text = fields.Text(
        string='Raw Response JSON',
        compute='_compute_raw_json_text',
        readonly=True,
    )
    error_message = fields.Text(
        copy=False,
    )
    matching_message = fields.Text(
        copy=False,
    )
    confidence = fields.Float(
        copy=False,
    )
    recognized_invoice_number = fields.Char(
        copy=False,
    )
    recognized_invoice_date = fields.Date(
        copy=False,
    )
    recognized_amount_untaxed = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    recognized_amount_tax = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    recognized_amount_total = fields.Monetary(
        currency_field='currency_id',
        copy=False,
    )
    line_ids = fields.One2many(
        comodel_name='account.gemini.digitization.line',
        inverse_name='job_id',
        string='Recognized Lines',
        copy=False,
    )

    @api.depends('raw_request_json', 'raw_response_json')
    def _compute_raw_json_text(self):
        for job in self:
            job.raw_request_json_text = self._format_json_for_display(
                job.raw_request_json
            )
            job.raw_response_json_text = self._format_json_for_display(
                job.raw_response_json
            )

    def action_process(self):
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_('Cannot process a done or cancelled digitization job.'))

        client = GeminiClient(self.env)
        try:
            self.write({
                'state': 'processing',
                'error_message': False,
                'matching_message': False,
            })
            response = client.recognize(self)
            self.write({
                'raw_request_json': client.last_request_payload,
            })
            ResponseParser(self.env).apply_to_job(
                self,
                response,
                raw_response=client.last_raw_response,
            )
            self._run_product_matching()
        except UserError as error:
            self._save_processing_error(error, client)
            raise
        except Exception as error:
            _logger.exception('Unexpected Gemini digitization processing error.')
            user_error = UserError(_('Помилка обробки Gemini: %s') % error)
            self._save_processing_error(user_error, client)
            raise user_error

        return self._get_job_form_action()

    def action_open_review_wizard(self):
        self.ensure_one()
        if self.state != 'review':
            raise UserError(_('Gemini review can be opened only for jobs in Review state.'))

        wizard = self.env['account.gemini.digitization.review.wizard'].create({
            'job_id': self.id,
            'line_ids': [
                (0, 0, self._prepare_review_wizard_line_values(line))
                for line in self.line_ids.sorted('sequence')
            ],
        })
        form_view = self.env.ref(
            'account_gemini_digitization.view_account_gemini_digitization_review_wizard_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Review Gemini Digitization'),
            'res_model': 'account.gemini.digitization.review.wizard',
            'view_mode': 'form',
            'views': [(form_view.id, 'form')],
            'res_id': wizard.id,
            'target': 'new',
        }

    def action_run_matching(self):
        self.ensure_one()
        if self.state != 'review':
            raise UserError(_('Matching can be re-run only for jobs in Review state.'))
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to match.'))

        ProductMatcher(self.env).match_job(self)
        self._update_matching_message_after_matching()
        return self._get_job_form_action()

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_reset_to_draft(self):
        self.write({
            'state': 'draft',
            'error_message': False,
            'matching_message': False,
        })
        return True

    def _prepare_request_payload(self):
        raise UserError(_('Gemini request payload preparation is not implemented yet.'))

    def _create_lines_from_response(self):
        raise UserError(_('Creating digitization lines from Gemini response is not implemented yet.'))

    def _prepare_review_wizard_line_values(self, line):
        return {
            'job_line_id': line.id,
            'sequence': line.sequence,
            'supplier_product_code': line.supplier_product_code,
            'supplier_product_name': line.supplier_product_name,
            'description': line.description,
            'quantity': line.quantity,
            'uom_name': line.uom_name,
            'price_unit_without_tax': line.price_unit_without_tax,
            'price_unit_with_tax': line.price_unit_with_tax,
            'line_subtotal_without_tax': line.line_subtotal_without_tax,
            'line_tax_amount': line.line_tax_amount,
            'line_total_with_tax': line.line_total_with_tax,
            'price_unit': line.price_unit,
            'tax_rate': line.tax_rate,
            'tax_ids': [(6, 0, line.tax_ids.ids)],
            'amount_untaxed': line.amount_untaxed,
            'amount_tax': line.amount_tax,
            'amount_total': line.amount_total,
            'matched_product_id': line.matched_product_id.id,
            'move_line_id': line.move_line_id.id,
            'candidate_product_ids': [(6, 0, line.candidate_product_ids.ids)],
            'candidate_move_line_ids': [(6, 0, line.candidate_move_line_ids.ids)],
            'match_status': line.match_status,
            'match_score': line.match_score,
            'match_method': line.match_method,
            'match_summary': line.match_summary,
            'match_note': line.match_note,
            'confidence': line.confidence,
            'source_columns': line.source_columns,
            'note': line.note,
        }

    def _run_product_matching(self):
        self.ensure_one()
        try:
            ProductMatcher(self.env).match_job(self)
            self._update_matching_message_after_matching()
        except Exception as error:
            _logger.exception('Gemini digitization product matching failed.')
            warning = _('Product matching warning: %s') % error
            self.write({
                'state': 'review',
                'matching_message': warning,
            })

    def _get_job_form_action(self):
        self.ensure_one()
        form_view = self.env.ref(
            'account_gemini_digitization.view_account_gemini_digitization_job_form'
        )
        return {
            'type': 'ir.actions.act_window',
            'name': _('Gemini Digitization Job'),
            'res_model': 'account.gemini.digitization.job',
            'view_mode': 'form',
            'views': [(form_view.id, 'form')],
            'res_id': self.id,
            'target': 'current',
        }

    def _save_processing_error(self, error, client):
        self.ensure_one()
        error_message = self._get_error_message(error)
        values = {
            'state': 'error',
            'error_message': error_message,
        }
        if getattr(client, 'last_request_payload', None):
            values['raw_request_json'] = client.last_request_payload
        if getattr(client, 'last_raw_response', None) is not None:
            values['raw_response_json'] = client.last_raw_response
        elif getattr(client, 'last_raw_text', None):
            values['raw_response_json'] = {'text': client.last_raw_text}
        self.write(values)
        # Keep the diagnostic state visible even though the button raises UserError.
        self.env.cr.commit()

    def _get_error_message(self, error):
        if getattr(error, 'args', None):
            return error.args[0]
        return str(error)

    def _append_error_message(self, message):
        if self.error_message:
            return '%s\n%s' % (self.error_message, message)
        return message

    def _update_matching_message_after_matching(self):
        self.ensure_one()
        self.write({
            'matching_message': self._compute_matching_message(),
            'error_message': self._clean_matching_warning_from_error_message(),
        })

    def _compute_matching_message(self):
        self.ensure_one()
        if not self.line_ids:
            return False

        if self.mode == 'partial_bill':
            problematic = self.line_ids.filtered(
                lambda line: line.match_status not in ('matched', 'manual')
                or not line.move_line_id
            )
            if problematic:
                return _('Some OCR lines require manual vendor bill line review before Apply.')
            return False

        if self.mode == 'full_bill':
            problematic = self.line_ids.filtered(
                lambda line: line.match_status not in ('matched', 'manual')
                or not line.matched_product_id
            )
            if problematic:
                return _('Some OCR lines require manual product review before Apply.')
            return False

        if self.mode == 'full_purchase':
            problematic = self.line_ids.filtered(
                lambda line: line.match_status not in ('matched', 'manual')
                or not line.matched_product_id
            )
            if problematic:
                return _('Some OCR lines require manual product review before Apply.')
            return False

        return False

    def _clean_matching_warning_from_error_message(self):
        self.ensure_one()
        if not self.error_message:
            return False

        matching_fragments = (
            'Several product candidates require review',
            'Some OCR lines require manual',
            'Product matching warning:',
        )
        kept_lines = [
            line
            for line in str(self.error_message).splitlines()
            if not any(fragment in line for fragment in matching_fragments)
        ]
        return '\n'.join(kept_lines).strip() or False

    def _format_json_for_display(self, value):
        if value in (False, None, ''):
            return False
        try:
            if isinstance(value, str):
                value = json.loads(value)
            return json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
        except Exception:
            return str(value)
