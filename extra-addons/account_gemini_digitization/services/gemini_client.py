import base64
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
        attachment = self._get_valid_attachment(job)
        config = self.get_config()
        if not config['api_key']:
            raise UserError(_('Не задано Gemini API key у налаштуваннях модуля.'))

        file_content = self._decode_attachment(attachment)
        prompt = self._build_prompt(job, config)
        payload = self._build_request_payload(prompt, attachment.mimetype, file_content)
        self.last_request_payload = self._sanitize_request_payload(
            job,
            attachment,
            payload,
            config,
            len(file_content),
        )

        raw_response = self._post_to_gemini(config, payload)
        self.last_raw_response = raw_response
        self.last_raw_text = self._extract_text(raw_response)
        return self._extract_json(self.last_raw_text)

    def _get_valid_attachment(self, job):
        attachment = job.attachment_id
        if not attachment:
            raise UserError(_('Не знайдено вкладення для обробки Gemini.'))
        if attachment.mimetype not in self.SUPPORTED_MIMETYPES:
            raise UserError(_(
                'Gemini підтримує тільки PDF, PNG або JPEG вкладення для цього процесу.'
            ))
        return attachment

    def _decode_attachment(self, attachment):
        if not attachment.datas:
            raise UserError(_('Вкладення порожнє або недоступне для читання.'))
        try:
            file_content = base64.b64decode(attachment.datas)
        except Exception as error:
            _logger.exception('Failed to decode Gemini digitization attachment.')
            raise UserError(_('Не вдалося прочитати файл вкладення: %s') % error)
        if not file_content:
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
                'response_mime_type': 'application/json',
            },
        }

    def _sanitize_request_payload(self, job, attachment, payload, config, file_size):
        safe_payload = json.loads(json.dumps(payload))
        safe_payload['contents'][0]['parts'][1]['inline_data']['data'] = (
            '<base64 file content omitted>'
        )
        return {
            'model': self._normalize_model_name(config['model']),
            'timeout': config['timeout'],
            'min_confidence': config['min_confidence'],
            'mode': job.mode,
            'attachment': {
                'id': attachment.id,
                'name': attachment.name,
                'mimetype': attachment.mimetype,
                'file_size': file_size,
            },
            'payload': safe_payload,
        }

    def _post_to_gemini(self, config, payload):
        model = self._normalize_model_name(config['model'])
        url = (
            'https://generativelanguage.googleapis.com/v1beta/'
            '%s:generateContent'
        ) % model
        try:
            response = requests.post(
                url,
                params={'key': config['api_key']},
                json=payload,
                timeout=config['timeout'],
            )
        except requests.Timeout:
            raise UserError(_('Час очікування відповіді Gemini API вичерпано.'))
        except requests.RequestException as error:
            raise UserError(_('Помилка запиту до Gemini API: %s') % error)

        if not response.content:
            raise UserError(_('Gemini API повернув порожню відповідь.'))

        raw_response = self._response_to_json(response)
        if response.status_code >= 400:
            self.last_raw_response = raw_response
            message = self._extract_error_message(raw_response) or response.text
            raise UserError(
                _('Gemini API повернув HTTP %(status)s: %(message)s') % {
                    'status': response.status_code,
                    'message': self._shorten(message),
                }
            )
        return raw_response

    def _response_to_json(self, response):
        try:
            return response.json()
        except ValueError:
            return {
                'status_code': response.status_code,
                'text': response.text,
            }

    def _extract_text(self, raw_response):
        if not raw_response:
            raise UserError(_('Gemini API повернув порожню відповідь.'))

        text_parts = []
        for candidate in raw_response.get('candidates', []):
            content = candidate.get('content') or {}
            for part in content.get('parts', []):
                text = part.get('text')
                if text:
                    text_parts.append(text)

        text = '\n'.join(text_parts).strip()
        if not text:
            raise UserError(_('Gemini API не повернув текст з JSON.'))
        return text

    def _extract_json(self, text):
        json_text = self._extract_json_text(text)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            raise UserError(_('Gemini повернув текст замість валідного JSON.'))
        if not isinstance(data, dict):
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
            error = raw_response.get('error')
            if isinstance(error, dict):
                return error.get('message')
            return raw_response.get('text')
        return False

    def _normalize_model_name(self, model):
        model = (model or self.DEFAULT_MODEL).strip()
        if model.startswith('models/'):
            return model
        return 'models/%s' % model

    def _build_prompt(self, job, config):
        mode_text = self._get_mode_prompt(job.mode)
        return """
You are extracting data from a supplier invoice document for Odoo.

Return only valid JSON.
Do not use markdown.
Do not add explanations.
Do not invent data.
If a field is not visible, return null.
Do not translate product names.
Do not improve, normalize, or correct product names.
Do not merge product lines.
Keep product lines in the same order as in the document.
Return numbers as JSON numbers without currency symbols.
Return dates in YYYY-MM-DD format.
Return VAT separately as tax_rate and tax_amount.
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
      "unit_price": null,
      "tax_rate": null,
      "tax_amount": null,
      "line_total": null,
      "confidence": null,
      "evidence": null
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
