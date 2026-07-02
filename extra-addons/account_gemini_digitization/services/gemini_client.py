import base64
import copy
import json
import logging
import re

import requests

from odoo import _
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class GeminiClient:
    DEFAULT_MODEL = 'gemini-1.5-pro'
    DEFAULT_TIMEOUT = 60
    DEFAULT_MIN_CONFIDENCE = 0.90
    SUPPORTED_MIMETYPES = (
        'application/pdf',
        'image/png',
        'image/jpeg',
        'image/jpg',
    )

    def __init__(self, env):
        self.env = env
        self.last_request_payload = None
        self.last_raw_response = None
        self.last_raw_text = None

    def get_config(self):
        config = self.env['ir.config_parameter'].sudo()
        timeout = config.get_param(
            'account_gemini_digitization.gemini_request_timeout',
            self.DEFAULT_TIMEOUT,
        )
        min_confidence = config.get_param(
            'account_gemini_digitization.gemini_min_confidence',
            self.DEFAULT_MIN_CONFIDENCE,
        )
        return {
            'api_key': config.get_param('account_gemini_digitization.gemini_api_key'),
            'model': config.get_param(
                'account_gemini_digitization.gemini_model',
                self.DEFAULT_MODEL,
            ),
            'timeout': int(timeout or self.DEFAULT_TIMEOUT),
            'min_confidence': float(min_confidence or self.DEFAULT_MIN_CONFIDENCE),
        }

    def recognize(self, job):
        job.ensure_one()
        config = self.get_config()
        endpoint = self._build_endpoint(config)
        self.last_request_payload = self._build_minimal_request_metadata(
            job,
            config,
            endpoint,
            error='request_not_sent_yet',
        )
        self._save_job_raw_request(job)
        if not config['api_key']:
            self.last_request_payload = self._build_minimal_request_metadata(
                job,
                config,
                endpoint,
                error='missing_api_key',
            )
            self._save_job_raw_request(job)
            self._set_preflight_error(
                'missing_api_key',
                _('Не задано Gemini API key у налаштуваннях модуля.'),
            )
            self._save_job_raw_response(job)
            raise UserError(_('Не задано Gemini API key у налаштуваннях модуля.'))

        try:
            attachment = self._get_valid_attachment(job)
            file_content = self._decode_attachment(attachment)
        except UserError:
            self._save_job_raw_response(job)
            raise
        prompt = self._build_prompt(job, config)
        payload = self._build_request_payload(prompt, attachment.mimetype, file_content)
        self.last_request_payload = self._build_request_metadata(
            job,
            attachment,
            payload,
            config,
            endpoint,
            prompt,
            len(file_content),
        )
        self._save_job_raw_request(job)

        final_response = self._post_to_gemini(job, config, endpoint, payload)
        try:
            self.last_raw_text = self._extract_text(final_response)
            self._augment_last_raw_response({
                'extracted_text_for_json_parse': self.last_raw_text,
            })
            self._save_job_raw_response(job)
            return self._extract_json(self.last_raw_text)
        except UserError:
            self._save_job_raw_response(job)
            raise

    def _get_valid_attachment(self, job):
        attachment = job.attachment_id
        if not attachment:
            self._set_preflight_error(
                'missing_attachment',
                _('Не знайдено вкладення для обробки Gemini.'),
            )
            raise UserError(_('Не знайдено вкладення для обробки Gemini.'))
        if attachment.mimetype not in self.SUPPORTED_MIMETYPES:
            self._set_preflight_error(
                'unsupported_mimetype',
                _(
                    'Gemini підтримує тільки PDF, PNG або JPEG вкладення для цього процесу.'
                ),
                {'mimetype': attachment.mimetype},
            )
            raise UserError(_(
                'Gemini підтримує тільки PDF, PNG або JPEG вкладення для цього процесу.'
            ))
        return attachment

    def _decode_attachment(self, attachment):
        if not attachment.datas:
            self._set_preflight_error(
                'empty_attachment',
                _('Вкладення порожнє або недоступне для читання.'),
            )
            raise UserError(_('Вкладення порожнє або недоступне для читання.'))
        try:
            file_content = base64.b64decode(attachment.datas)
        except Exception as error:
            _logger.exception('Failed to decode Gemini digitization attachment.')
            self._set_preflight_error(
                'attachment_decode_error',
                _('Не вдалося прочитати файл вкладення: %s') % error,
            )
            raise UserError(_('Не вдалося прочитати файл вкладення: %s') % error)
        if not file_content:
            self._set_preflight_error(
                'empty_attachment',
                _('Вкладення порожнє або недоступне для читання.'),
            )
            raise UserError(_('Вкладення порожнє або недоступне для читання.'))
        return file_content

    def _build_request_payload(self, prompt, mimetype, file_content):
        encoded_file = base64.b64encode(file_content).decode('ascii')
        return {
            'contents': [{
                'parts': [
                    {'text': prompt},
                    {
                        'inline_data': {
                            'mime_type': mimetype,
                            'data': encoded_file,
                        },
                    },
                ],
            }],
            'generationConfig': {
                'temperature': 0,
                'responseMimeType': 'application/json',
                'responseSchema': self._build_response_schema(),
            },
        }

    def _build_request_metadata(
        self,
        job,
        attachment,
        payload,
        config,
        endpoint,
        prompt,
        file_size,
    ):
        generation_config = copy.deepcopy(payload.get('generationConfig') or {})
        return {
            'model': self._normalize_model_name(config['model']),
            'endpoint': endpoint,
            'timeout': config['timeout'],
            'min_confidence': config['min_confidence'],
            'mode': job.mode,
            'attachment': {
                'id': attachment.id,
                'name': attachment.name,
                'mimetype': attachment.mimetype,
                'size': file_size,
            },
            'generation_config': generation_config,
            'prompt_preview': self._shorten(prompt, limit=4000),
            'contains_file_base64': False,
        }

    def _build_minimal_request_metadata(self, job, config, endpoint, error=None):
        request_metadata = {
            'model': self._normalize_model_name(config['model']),
            'endpoint': endpoint,
            'timeout': config['timeout'],
            'min_confidence': config['min_confidence'],
            'mode': job.mode,
            'error': error,
        }
        attachment = job.attachment_id
        if attachment:
            request_metadata['attachment'] = {
                'id': attachment.id,
                'name': attachment.name,
                'mimetype': attachment.mimetype,
                'size': getattr(attachment, 'file_size', 0),
            }
        return request_metadata

    def _post_to_gemini(self, job, config, endpoint, payload):
        attempts = []
        first_response = self._execute_request(
            config,
            endpoint,
            payload,
            attempt='with_response_schema',
        )
        attempts.append(first_response)
        self._set_last_raw_response(attempts, first_response)
        self._save_job_raw_response(job)
        self._raise_transport_error(first_response)

        final_response = first_response
        if self._should_retry_without_schema(first_response, payload):
            fallback_payload = copy.deepcopy(payload)
            fallback_payload['generationConfig'].pop('responseSchema', None)
            reason = self._extract_error_message(first_response)
            self.last_request_payload['response_schema_fallback'] = {
                'triggered': True,
                'reason': self._shorten(reason),
                'generation_config': fallback_payload.get('generationConfig'),
            }
            self._save_job_raw_request(job)
            final_response = self._execute_request(
                config,
                endpoint,
                fallback_payload,
                attempt='without_response_schema',
            )
            attempts.append(final_response)
            self._set_last_raw_response(attempts, final_response)
            self._save_job_raw_response(job)
            self._raise_transport_error(final_response)

        if final_response.get('status_code', 0) >= 400:
            message = self._extract_error_message(final_response)
            raise UserError(
                _('Gemini API повернув HTTP %(status)s: %(message)s') % {
                    'status': final_response.get('status_code'),
                    'message': self._shorten(message),
                }
            )
        return final_response

    def _execute_request(self, config, endpoint, payload, attempt):
        try:
            response = requests.post(
                endpoint,
                params={'key': config['api_key']},
                json=payload,
                timeout=config['timeout'],
            )
        except requests.Timeout:
            return {
                'attempt': attempt,
                'status_code': None,
                'transport_error': {
                    'type': 'timeout',
                    'message': _('Час очікування відповіді Gemini API вичерпано.'),
                },
            }
        except requests.RequestException as error:
            return {
                'attempt': attempt,
                'status_code': None,
                'transport_error': {
                    'type': error.__class__.__name__,
                    'message': str(error),
                },
            }

        response_capture = {
            'attempt': attempt,
            'status_code': response.status_code,
            'headers': self._sanitize_response_headers(response.headers),
            'content_type': response.headers.get('Content-Type'),
        }
        if not response.content:
            response_capture['empty_response'] = True
            return response_capture
        try:
            response_capture['response_json'] = response.json()
        except ValueError:
            response_capture['response_text'] = response.text
        return response_capture

    def _set_last_raw_response(self, attempts, final_response):
        self.last_raw_response = {
            'attempts': attempts,
            'final_response': final_response,
        }

    def _raise_transport_error(self, response_capture):
        transport_error = response_capture.get('transport_error')
        if transport_error:
            raise UserError(_('Помилка запиту до Gemini API: %s') % (
                transport_error.get('message') or transport_error.get('type')
            ))

    def _extract_text(self, response_capture):
        response_json = response_capture.get('response_json')
        diagnostics = {
            'response_keys': sorted(response_json.keys()) if isinstance(response_json, dict) else [],
            'finish_reasons': [],
            'prompt_feedback': False,
            'non_text_parts': [],
            'text_part_count': 0,
        }
        if not response_json:
            diagnostics['empty_reason'] = 'response_is_not_json'
            diagnostics['status_code'] = response_capture.get('status_code')
            diagnostics['response_text_present'] = bool(response_capture.get('response_text'))
            self._augment_last_raw_response({
                'extracted_text_for_json_parse': '',
                'text_extraction': diagnostics,
            })
            raise UserError(_(
                'Gemini API не повернув JSON response. Деталі збережено у вкладці Raw JSON.'
            ))

        text_parts = []
        diagnostics['prompt_feedback'] = response_json.get('promptFeedback')
        candidates = response_json.get('candidates') or []
        if not candidates:
            diagnostics['empty_reason'] = 'no_candidates'
            self._augment_last_raw_response({
                'extracted_text_for_json_parse': '',
                'text_extraction': diagnostics,
            })
            raise UserError(_(
                'Gemini API не повернув candidates. Деталі збережено у вкладці Raw JSON.'
            ))

        for candidate_index, candidate in enumerate(candidates):
            diagnostics['finish_reasons'].append(candidate.get('finishReason'))
            content = candidate.get('content') or {}
            for part_index, part in enumerate(content.get('parts') or []):
                text = part.get('text')
                if text:
                    text_parts.append(text)
                    diagnostics['text_part_count'] += 1
                    continue
                diagnostics['non_text_parts'].append({
                    'candidate_index': candidate_index,
                    'part_index': part_index,
                    'keys': sorted(part.keys()),
                })

        text = '\n'.join(text_parts).strip()
        if not text:
            diagnostics['empty_reason'] = 'no_text_parts'
            self._augment_last_raw_response({
                'extracted_text_for_json_parse': '',
                'text_extraction': diagnostics,
            })
            raise UserError(_(
                'Gemini API не повернув текст для JSON parse. Деталі збережено у вкладці Raw JSON.'
            ))
        self._augment_last_raw_response({
            'text_extraction': diagnostics,
        })
        return text

    def _extract_json(self, text):
        json_text = self._extract_json_text(text)
        self._augment_last_raw_response({
            'json_text_candidate': json_text,
        })
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as error:
            self._augment_last_raw_response({
                'json_parse_error': {
                    'message': str(error),
                    'line': error.lineno,
                    'column': error.colno,
                    'position': error.pos,
                },
            })
            raise UserError(_(
                'Gemini повернув невалідний JSON. Деталі збережено у вкладці Raw JSON.'
            ))
        if not isinstance(data, dict):
            self._augment_last_raw_response({
                'json_parse_error': 'Parsed JSON root is not an object.',
            })
            raise UserError(_('Gemini JSON must be an object.'))
        return data

    def _extract_json_text(self, text):
        cleaned = (text or '').strip()
        if not cleaned:
            raise UserError(_('Gemini API повернув порожній текст.'))

        fenced = re.search(r'```(?:json)?\s*(.*?)```', cleaned, flags=re.I | re.S)
        if fenced:
            cleaned = fenced.group(1).strip()

        if not cleaned.startswith('{'):
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start >= 0 and end > start:
                cleaned = cleaned[start:end + 1].strip()
        return cleaned

    def _extract_error_message(self, raw_response):
        if isinstance(raw_response, dict):
            response_json = raw_response.get('response_json')
            if isinstance(response_json, dict):
                error = response_json.get('error')
                if isinstance(error, dict):
                    return error.get('message')
            error = raw_response.get('error')
            if isinstance(error, dict):
                return error.get('message')
            return raw_response.get('response_text') or raw_response.get('text')
        return False

    def _normalize_model_name(self, model):
        model = (model or self.DEFAULT_MODEL).strip()
        if model.startswith('models/'):
            return model
        return 'models/%s' % model

    def _build_endpoint(self, config):
        return (
            'https://generativelanguage.googleapis.com/v1beta/'
            '%s:generateContent'
        ) % self._normalize_model_name(config['model'])

    def _build_prompt(self, job, config):
        mode_text = self._get_mode_prompt(job.mode)
        return """
You are extracting data from a supplier invoice document for Odoo.

Return only valid JSON.
Return ONLY one valid JSON object.
Do not use markdown.
Do not wrap JSON in markdown.
Do not add explanations.
Do not include explanations.
The first character must be {.
The last character must be }.
Do not invent data.
If a field is not visible, return null.
If a field is unknown, use null.
If no invoice lines are found, return "lines": [].
Do not translate product names.
Do not improve, normalize, or correct product names.
Do not merge product lines.
Keep product lines in the same order as in the document.
Return numbers as JSON numbers without currency symbols.
Return dates in YYYY-MM-DD format.
Return VAT separately as tax_rate and tax_amount.
Return tax_rate as a percentage number, not as a fraction.
For 20%% VAT return 20, not 0.2.
For 7%% VAT return 7, not 0.07.
Determine the meaning of each table column from its header and surrounding totals.
If a column explicitly means price without VAT, return it as price_unit_without_tax.
If a column explicitly means price with VAT, return it as price_unit_with_tax.
If a column explicitly means line subtotal without VAT, return it as line_subtotal_without_tax.
If a column explicitly means line total with VAT, return it as line_total_with_tax.
If a line VAT amount is shown separately, return it as line_tax_amount.
If the VAT rate is shown only in the footer/header and it is clearly one rate for
the whole document, return that rate as tax_rate for each taxable line.
If multiple VAT rates are present or there is any doubt, return tax_rate as null
for the uncertain line.
Do not set tax_rate to 0 unless the document explicitly marks that line as zero-rated or VAT-exempt.
If VAT is not found anywhere in the document, return tax_rate as null.
Use source_columns to describe which document columns were used for the line amounts.
Keep legacy fields unit_price, line_total, and tax_amount when available, but use
the precise fields above as the primary representation.
For every line, return evidence when possible.
Use confidence values from 0 to 1. The downstream review threshold is %(min_confidence)s.

Mode:
%(mode_text)s

JSON schema:
{
  "invoice_number": null,
  "invoice_date": null,
  "vendor_name": null,
  "currency": null,
  "untaxed_amount": null,
  "tax_amount": null,
  "total_amount": null,
  "confidence": null,
  "lines": [
    {
      "supplier_product_code": null,
      "supplier_product_name": null,
      "description": null,
      "quantity": null,
      "uom": null,
      "price_unit_without_tax": null,
      "price_unit_with_tax": null,
      "line_subtotal_without_tax": null,
      "line_tax_amount": null,
      "line_total_with_tax": null,
      "unit_price": null,
      "tax_rate": null,
      "tax_amount": null,
      "line_total": null,
      "confidence": null,
      "evidence": null,
      "source_columns": null
    }
  ]
}
""" % {
            'mode_text': mode_text,
            'min_confidence': config['min_confidence'],
        }

    def _get_mode_prompt(self, mode):
        if mode == 'partial_bill':
            return """
partial_bill:
The document is a vendor bill created from a purchase order.
Products already exist in account.move.
Extract invoice number, invoice date, VAT, prices, totals, and document lines for later matching.
Do not match products.
Do not apply data to account.move.
Do not change or create invoice lines.
"""
        if mode == 'full_bill':
            return """
full_bill:
The document is a supplier/vendor bill uploaded to an empty vendor bill in Odoo.
Extract invoice number, invoice date, vendor, currency, products, quantities, prices, VAT, and totals.
Extract every product or service line needed to create vendor bill lines after human review.
Do not match products.
Do not create products.
Do not apply data to account.move.
Do not create invoice lines.
"""
        if mode == 'partial_purchase':
            return """
partial_purchase:
The document is a supplier invoice uploaded to a purchase order or RFQ that already has product lines in Odoo.
Products already exist in purchase.order.line.
Extract invoice number, invoice date, vendor, VAT, prices, totals, and document lines for later matching.
Do not match products.
Do not create products.
Do not apply data to purchase.order.
Do not create purchase order lines.
"""
        if mode == 'full_purchase':
            return """
full_purchase:
The document is a supplier invoice uploaded to an empty purchase order.
Extract invoice number, invoice date, vendor, products, quantities, prices, VAT, and totals.
Do not match products.
Do not create purchase order lines.
"""
        raise UserError(_('Невідомий режим розпізнавання Gemini: %s') % mode)

    def _shorten(self, value, limit=1000):
        value = value or ''
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        if len(value) <= limit:
            return value
        return '%s...' % value[:limit]

    def _build_response_schema(self):
        return {
            'type': 'object',
            'properties': {
                'invoice_number': {'type': 'string'},
                'invoice_date': {'type': 'string'},
                'vendor_name': {'type': 'string'},
                'currency': {'type': 'string'},
                'untaxed_amount': {'type': 'number'},
                'tax_amount': {'type': 'number'},
                'total_amount': {'type': 'number'},
                'confidence': {'type': 'number'},
                'lines': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'supplier_product_code': {'type': 'string'},
                            'supplier_product_name': {'type': 'string'},
                            'description': {'type': 'string'},
                            'quantity': {'type': 'number'},
                            'uom': {'type': 'string'},
                            'price_unit_without_tax': {'type': 'number'},
                            'price_unit_with_tax': {'type': 'number'},
                            'line_subtotal_without_tax': {'type': 'number'},
                            'line_tax_amount': {'type': 'number'},
                            'line_total_with_tax': {'type': 'number'},
                            'unit_price': {'type': 'number'},
                            'tax_rate': {'type': 'number'},
                            'tax_amount': {'type': 'number'},
                            'line_total': {'type': 'number'},
                            'confidence': {'type': 'number'},
                            'evidence': {'type': 'string'},
                            'source_columns': {'type': 'string'},
                        },
                    },
                },
            },
            'required': ['lines'],
        }

    def _should_retry_without_schema(self, response_capture, payload):
        generation_config = payload.get('generationConfig') or {}
        if 'responseSchema' not in generation_config:
            return False
        if response_capture.get('status_code') != 400:
            return False
        message = (self._extract_error_message(response_capture) or '').lower()
        return (
            'schema' in message
            or 'responseschema' in message
            or 'response_schema' in message
        )

    def _sanitize_response_headers(self, headers):
        sensitive_headers = {
            'authorization',
            'proxy-authorization',
            'set-cookie',
            'x-goog-api-key',
            'x-api-key',
            'cookie',
        }
        result = {}
        for key, value in dict(headers or {}).items():
            if key.lower() in sensitive_headers:
                result[key] = '<omitted>'
            else:
                result[key] = value
        return result

    def _augment_last_raw_response(self, values):
        if self.last_raw_response is None:
            self.last_raw_response = {}
        self.last_raw_response.update(values)

    def _save_job_raw_request(self, job):
        if self.last_request_payload is not None:
            job.write({'raw_request_json': self.last_request_payload})

    def _save_job_raw_response(self, job):
        if self.last_raw_response is not None:
            job.write({'raw_response_json': self.last_raw_response})

    def _set_preflight_error(self, code, message, details=None):
        self.last_raw_response = {
            'preflight_error': {
                'code': code,
                'message': message,
                'details': details or {},
            },
        }
