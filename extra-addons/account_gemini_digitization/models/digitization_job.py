import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services import (
    DigitizationApplyService,
    GeminiClient,
    ProductMatcher,
    ResponseParser,
)


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
        ('partial_purchase', 'Partial Purchase Order Recognition'),
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
    attachment_name = fields.Char(
        string='Attachment File Name',
        related='attachment_id.name',
        readonly=True,
    )
    attachment_mimetype = fields.Char(
        string='Attachment MIME Type',
        related='attachment_id.mimetype',
        readonly=True,
    )
    attachment_preview_data = fields.Binary(
        string='Document Preview',
        related='attachment_id.datas',
        readonly=True,
    )
    attachment_is_pdf = fields.Boolean(
        compute='_compute_attachment_preview_type',
    )
    attachment_is_image = fields.Boolean(
        compute='_compute_attachment_preview_type',
    )
    attachment_preview_supported = fields.Boolean(
        compute='_compute_attachment_preview_type',
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
    document_price_tax_mode = fields.Selection(
        selection=[
            ('included', 'Prices Include Tax'),
            ('excluded', 'Prices Exclude Tax'),
            ('unknown', 'Unknown'),
        ],
        string='Document Price Tax Mode',
        default='unknown',
        copy=False,
    )
    line_ids = fields.One2many(
        comodel_name='account.gemini.digitization.line',
        inverse_name='job_id',
        string='Recognized Lines',
        copy=False,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            self._check_linked_document_values_access(values, 'write')
        return super().create(vals_list)

    def write(self, values):
        for job in self:
            job._check_linked_document_access('write')
        self._check_linked_document_values_access(values, 'write')
        return super().write(values)

    @api.depends('raw_request_json', 'raw_response_json')
    def _compute_raw_json_text(self):
        for job in self:
            job.raw_request_json_text = self._format_json_for_display(
                job.raw_request_json
            )
            job.raw_response_json_text = self._format_json_for_display(
                job.raw_response_json
            )

    @api.depends('attachment_id', 'attachment_id.mimetype')
    def _compute_attachment_preview_type(self):
        for job in self:
            mimetype = (job.attachment_mimetype or '').lower()
            job.attachment_is_pdf = mimetype == 'application/pdf'
            job.attachment_is_image = mimetype in (
                'image/png',
                'image/jpeg',
                'image/jpg',
            )
            job.attachment_preview_supported = (
                job.attachment_is_pdf or job.attachment_is_image
            )

    def action_open_source_attachment(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_('No source document is attached.'))
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=false' % self.attachment_id.id,
            'target': 'new',
        }

    def action_process(self):
        self.ensure_one()
        self._check_linked_document_access('write')
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

        return True

    def run_automatic_pipeline(self):
        self.ensure_one()
        self._check_linked_document_access('write')
        try:
            self.action_process()
        except Exception as error:
            _logger.exception('Gemini automatic digitization pipeline failed.')
            message = _('Не вдалося завершити оцифрування. Дані до документа не застосовано.')
            self._post_automatic_pipeline_message(message)
            return {
                'status': 'error',
                'message': message,
                'notification_type': 'danger',
                'sticky': True,
            }

        if self.state != 'review':
            message = self._get_automatic_manual_review_message()
            self._post_automatic_pipeline_message(message)
            return {
                'status': 'manual_review',
                'message': message,
                'notification_type': 'warning',
                'sticky': False,
            }

        blocker = self._get_automatic_apply_blocker()
        if blocker:
            self._set_automatic_review_message(blocker)
            message = self._get_automatic_manual_review_message(blocker)
            self._post_automatic_pipeline_message(message)
            return {
                'status': 'manual_review',
                'message': message,
                'notification_type': 'warning',
                'sticky': False,
            }

        try:
            apply_service = DigitizationApplyService(self.env, self)
            if self.mode not in ('full_bill', 'full_purchase'):
                apply_service.validate_for_automatic_apply()
            apply_result = apply_service.apply()
        except UserError as error:
            details = self._get_error_message(error)
            self._set_automatic_review_message(details)
            message = self._get_automatic_manual_review_message(details)
            self._post_automatic_pipeline_message(message)
            return {
                'status': 'manual_review',
                'message': message,
                'notification_type': 'warning',
                'sticky': False,
            }
        except Exception as error:
            _logger.exception('Gemini automatic apply failed.')
            self._set_automatic_review_message(str(error))
            message = self._get_automatic_manual_review_message()
            self._post_automatic_pipeline_message(message)
            return {
                'status': 'manual_review',
                'message': message,
                'notification_type': 'warning',
                'sticky': False,
            }

        message = self._get_automatic_success_message(apply_result)
        self._post_automatic_pipeline_message(message)
        notification_type = 'success'
        status = 'applied'
        if isinstance(apply_result, dict):
            notification_type = apply_result.get('gemini_notification_type', notification_type)
            status = apply_result.get('gemini_status', status)
        return {
            'status': status,
            'message': message,
            'notification_type': notification_type,
            'sticky': False,
        }

    def action_run_matching(self):
        self.ensure_one()
        self._check_linked_document_access('write')
        if self.state != 'review':
            raise UserError(_('Matching can be re-run only for jobs in Review state.'))
        if not self.line_ids:
            raise UserError(_('There are no recognized lines to match.'))

        ProductMatcher(self.env).match_job(self)
        self._update_matching_message_after_matching()
        return True

    def action_apply(self):
        self.ensure_one()
        self._check_linked_document_access('write')
        return DigitizationApplyService(self.env, self).apply()

    def action_cancel(self):
        for job in self:
            job._check_linked_document_access('write')
        self.write({'state': 'cancelled'})
        return True

    def action_reset_to_draft(self):
        for job in self:
            job._check_linked_document_access('write')
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

    def _get_automatic_apply_blocker(self):
        self.ensure_one()
        create_lines = self.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )
        non_create_lines = self.line_ids - create_lines
        if not self.line_ids:
            return _('There are no recognized lines to apply.')
        if self.mode in ('full_bill', 'full_purchase'):
            return False
        if non_create_lines:
            return _('Automatic apply is disabled when OCR lines use merge or skip actions.')

        if self.mode == 'partial_bill':
            used_move_line_ids = set()
            for line in create_lines:
                if line.match_status not in ('matched', 'manual') or not line.move_line_id:
                    article_message = self._get_unmatched_supplier_article_message(line)
                    if article_message:
                        return article_message
                    return _(
                        'Не вдалося однозначно зіставити рядок документа з товарним рядком рахунку: %(line)s.'
                    ) % {
                        'line': line._display_label(),
                    }
                if not self._is_positive_number(line.quantity):
                    return _('Some OCR lines do not have a positive quantity.')
                if not self._is_positive_number(line.price_unit):
                    return _('Some OCR lines do not have a positive unit price.')
                if line.move_line_id.move_id != self.move_id:
                    return _('Some OCR lines are linked to another vendor bill.')
                if not line.move_line_id.product_id:
                    return _('Some selected vendor bill lines do not have products.')
                if line.matched_product_id != line.move_line_id.product_id:
                    return _(
                        'Не вдалося однозначно зіставити рядок документа з товарним рядком рахунку: %(line)s.'
                    ) % {
                        'line': line._display_label(),
                    }
                if line.move_line_id.id in used_move_line_ids:
                    return _('One vendor bill line is assigned to several OCR lines.')
                used_move_line_ids.add(line.move_line_id.id)
            return False

        if self.mode == 'partial_purchase':
            problematic = create_lines.filtered(
                lambda line: line.match_status not in ('matched', 'manual')
                or not line.purchase_order_line_id
            )
            if problematic:
                article_message = self._get_unmatched_supplier_article_message(problematic[0])
                if article_message:
                    return article_message
                return _(
                    'Оцифрування завершено, але не вдалося однозначно зіставити %(count)s рядків із товарами замовлення. '
                    'Дані не були застосовані автоматично.'
                ) % {
                    'count': len(problematic),
                }
            used_order_line_ids = set()
            for line in create_lines:
                if not self._is_positive_number(line.quantity):
                    return _('Some OCR lines do not have a positive quantity.')
                if not self._is_positive_number(line.price_unit):
                    return _('Some OCR lines do not have a positive unit price.')
                if line.purchase_order_line_id.order_id != self.purchase_order_id:
                    return _('Some OCR lines are linked to another purchase order.')
                if not line.purchase_order_line_id.product_id:
                    return _('Some selected purchase order lines do not have products.')
                if line.matched_product_id != line.purchase_order_line_id.product_id:
                    return _(
                        'Оцифрування завершено, але не вдалося однозначно зіставити %(count)s рядків із товарами замовлення. '
                        'Дані не були застосовані автоматично.'
                    ) % {
                        'count': 1,
                    }
                if line.purchase_order_line_id.id in used_order_line_ids:
                    return _('One purchase order line is assigned to several OCR lines.')
                used_order_line_ids.add(line.purchase_order_line_id.id)
            return False

        for line in create_lines:
            if line.match_status not in ('matched', 'manual'):
                return _('Some OCR lines are not confidently matched.')
            if not self._is_positive_number(line.quantity):
                return _('Some OCR lines do not have a positive quantity.')
            if not self._is_positive_number(line.price_unit):
                return _('Some OCR lines do not have a positive unit price.')

        if self.mode in ('full_bill', 'full_purchase'):
            for line in create_lines:
                if not line.matched_product_id:
                    return _('Some OCR lines do not have matched products.')
                if len(line.candidate_product_ids) > 1:
                    return _('Some OCR lines have several product candidates.')
            return False

        return _('Unsupported Gemini digitization mode.')

    def _set_automatic_review_message(self, details=False):
        self.ensure_one()
        message = self._get_automatic_manual_review_message(details)
        values = {
            'matching_message': message,
        }
        if self.state not in ('done', 'error', 'cancelled'):
            values['state'] = 'review'
        self.write(values)

    def _get_automatic_manual_review_message(self, details=False):
        self.ensure_one()
        if details and self.mode in ('partial_bill', 'partial_purchase'):
            return details
        if self.mode == 'partial_bill':
            message = _(
                'Оцифрування завершено, але не вдалося однозначно зіставити всі рядки '
                'з товарами рахунку. Дані не були застосовані автоматично.'
            )
        elif self.mode == 'partial_purchase':
            message = _(
                'Оцифрування завершено, але не вдалося однозначно зіставити рядки '
                'з товарами замовлення. Дані не були застосовані автоматично.'
            )
        else:
            message = _(
                'Оцифрування завершено, але деякі рядки потребують перевірки. '
                'Дані не були застосовані автоматично.'
            )
        if details:
            return '%s\n%s' % (message, details)
        return message

    def _get_automatic_success_message(self, apply_result=False):
        self.ensure_one()
        if self.mode == 'partial_bill':
            return _('Оцифрування завершено. Кількість, ціни та податки в рядках рахунку оновлено.')
        if self.mode == 'partial_purchase':
            return _('Оцифрування завершено. Кількість, ціни та податки в рядках замовлення оновлено.')
        if self.mode == 'full_bill':
            if isinstance(apply_result, dict):
                applied = apply_result.get('gemini_applied_count', 0)
                skipped = apply_result.get('gemini_skipped_count', 0)
                if applied and skipped:
                    return _(
                        'Оцифрування завершено. Створено рядків: %(applied)s. '
                        'Не перенесено рядків: %(skipped)s, оскільки товари або дані '
                        'не вдалося однозначно визначити.'
                    ) % {
                        'applied': applied,
                        'skipped': skipped,
                    }
                if not applied:
                    return _(
                        'Оцифрування завершено, але жоден товар не вдалося безпечно '
                        'зіставити. Дані до рахунку не застосовано.'
                    )
            return _('Оцифрування завершено. Усі розпізнані рядки рахунку застосовано.')
        if self.mode == 'full_purchase':
            if isinstance(apply_result, dict):
                applied = apply_result.get('gemini_applied_count', 0)
                skipped = apply_result.get('gemini_skipped_count', 0)
                if applied and skipped:
                    return _(
                        'Gemini OCR completed. Purchase order lines created: %(applied)s. '
                        'Skipped lines: %(skipped)s because products or values were not safe to apply.'
                    ) % {
                        'applied': applied,
                        'skipped': skipped,
                    }
                if not applied:
                    return _(
                        'Gemini OCR completed, but no products could be safely matched. '
                        'No data was applied to the purchase order.'
                    )
                return _('Gemini OCR completed. All recognized purchase order lines were applied.')
            return _('Оцифрування завершено. Рядки замовлення на закупівлю створено.')
        return _('Оцифрування завершено. Дані з документа автоматично застосовано.')

    def _post_automatic_pipeline_message(self, message):
        self.ensure_one()
        _logger.info(
            'Gemini OCR result for %s/%s: %s',
            self.res_model,
            self.res_id,
            message,
        )
        return False

    def _unlink_temporary_job(self):
        jobs = self.sudo().exists()
        for job in jobs:
            job.line_ids.sudo().unlink()
        jobs.unlink()
        return True

    def _is_positive_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    def _check_linked_document_access(self, operation='read'):
        self.ensure_one()
        document = self.move_id or self.purchase_order_id
        if not document:
            return True
        document.check_access_rights(operation)
        document.check_access_rule(operation)
        return True

    def _check_linked_document_values_access(self, values, operation='read'):
        document_specs = (
            ('move_id', 'account.move'),
            ('purchase_order_id', 'purchase.order'),
        )
        for field_name, model_name in document_specs:
            document_id = values.get(field_name)
            if not document_id:
                continue
            document = self.env[model_name].browse(document_id).exists()
            if not document:
                continue
            document.check_access_rights(operation)
            document.check_access_rule(operation)
        return True

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
                lambda line: (line.apply_action or 'create_line') == 'create_line'
                and (
                    line.match_status not in ('matched', 'manual')
                    or not line.move_line_id
                )
            )
            if problematic:
                article_message = self._get_unmatched_supplier_article_message(problematic[0])
                if article_message:
                    return article_message
                return self._get_automatic_manual_review_message()
            return False

        if self.mode == 'partial_purchase':
            problematic = self.line_ids.filtered(
                lambda line: (line.apply_action or 'create_line') == 'create_line'
                and (
                    line.match_status not in ('matched', 'manual')
                    or not line.purchase_order_line_id
                )
            )
            if problematic:
                article_message = self._get_unmatched_supplier_article_message(problematic[0])
                if article_message:
                    return article_message
                return _(
                    'Оцифрування завершено, але не вдалося однозначно зіставити %(count)s рядків із товарами замовлення. '
                    'Дані не були застосовані автоматично.'
                ) % {
                    'count': len(problematic),
                }
            return False

        if self.mode == 'full_bill':
            problematic = self.line_ids.filtered(
                lambda line: (line.apply_action or 'create_line') == 'create_line'
                and (
                    line.match_status not in ('matched', 'manual')
                    or not line.matched_product_id
                )
            )
            if problematic:
                article_message = self._get_unmatched_supplier_article_message(problematic[0])
                if article_message:
                    return article_message
                return _('Some OCR lines require manual product review before Apply.')
            return False

        if self.mode == 'full_purchase':
            problematic = self.line_ids.filtered(
                lambda line: (line.apply_action or 'create_line') == 'create_line'
                and (
                    line.match_status not in ('matched', 'manual')
                    or not line.matched_product_id
                )
            )
            if problematic:
                article_message = self._get_unmatched_supplier_article_message(problematic[0])
                if article_message:
                    return article_message
                return _('Some OCR lines require manual product review before Apply.')
            return False

        return False

    def _get_unmatched_supplier_article_message(self, line):
        code = getattr(line, 'supplier_product_code', False)
        if not code:
            return False
        return _(
            'Не вдалося зіставити артикул постачальника «%(code)s» з товарами документа.'
        ) % {
            'code': code,
        }

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
