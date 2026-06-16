import re
from datetime import datetime

from odoo import _
from odoo.exceptions import UserError


class ResponseParser:
    DATE_FORMATS = (
        '%Y-%m-%d',
        '%d.%m.%Y',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%Y/%m/%d',
    )

    def __init__(self, env=None):
        self.env = env

    def parse(self, response):
        if not isinstance(response, dict):
            raise UserError(_('Gemini response must be a JSON object.'))

        lines = response.get('lines')
        if not isinstance(lines, list):
            raise UserError(_('Gemini JSON не містить рядків lines.'))

        header = {
            'invoice_number': self._clean_string(response.get('invoice_number')),
            'invoice_date': self._to_date(response.get('invoice_date')),
            'vendor_name': self._clean_string(response.get('vendor_name')),
            'currency': self._clean_string(response.get('currency')),
            'untaxed_amount': self._to_float(response.get('untaxed_amount')),
            'tax_amount': self._to_float(response.get('tax_amount')),
            'total_amount': self._to_float(response.get('total_amount')),
            'confidence': self._to_float(response.get('confidence')),
        }
        header_tax_rate = self._compute_header_tax_rate(header)
        return {
            'header': header,
            'lines': [
                self._parse_line(line, index, header_tax_rate)
                for index, line in enumerate(lines, start=1)
            ],
            'raw': response,
        }

    def apply_to_job(self, job, response, raw_response=None):
        job.ensure_one()
        parsed = self.parse(response)
        header = parsed['header']

        job.line_ids.unlink()

        job.write({
            'raw_response_json': raw_response if raw_response is not None else response,
            'error_message': False,
            'confidence': header['confidence'],
            'recognized_invoice_number': header['invoice_number'],
            'recognized_invoice_date': header['invoice_date'],
            'recognized_amount_untaxed': header['untaxed_amount'],
            'recognized_amount_tax': header['tax_amount'],
            'recognized_amount_total': header['total_amount'],
            'state': 'review',
        })

        line_model = job.env['account.gemini.digitization.line']
        for line in parsed['lines']:
            line_model.create({
                'job_id': job.id,
                'sequence': line['sequence'],
                'supplier_product_code': line['supplier_product_code'],
                'supplier_product_name': line['supplier_product_name'],
                'description': line['description'],
                'quantity': line['quantity'],
                'uom_name': line['uom_name'],
                'price_unit': line['price_unit'],
                'tax_rate': line['tax_rate'],
                'amount_untaxed': line['amount_untaxed'],
                'amount_tax': line['amount_tax'],
                'amount_total': line['amount_total'],
                'confidence': line['confidence'],
                'note': line['note'],
                'match_status': 'draft',
            })
        return parsed

    def _parse_line(self, line, index, header_tax_rate=False):
        if not isinstance(line, dict):
            raise UserError(_('Gemini line %s must be a JSON object.') % index)
        quantity = self._to_float(line.get('quantity'))
        price_unit = self._to_float(line.get('unit_price'))
        amount_untaxed = self._to_float(line.get('line_total'))
        tax_rate = self._get_effective_tax_rate(
            self._to_float(line.get('tax_rate')),
            header_tax_rate,
        )
        amount_tax = self._compute_line_tax_amount(
            amount_untaxed,
            tax_rate,
            self._to_float(line.get('tax_amount')),
        )
        amount_total = self._compute_line_total(amount_untaxed, amount_tax)
        return {
            'sequence': index * 10,
            'supplier_product_code': self._clean_string(
                line.get('supplier_product_code')
            ),
            'supplier_product_name': self._clean_string(
                line.get('supplier_product_name')
            ),
            'description': self._clean_string(line.get('description')),
            'quantity': quantity,
            'uom_name': self._clean_string(line.get('uom')),
            'price_unit': price_unit,
            'tax_rate': tax_rate,
            'amount_untaxed': amount_untaxed,
            'amount_tax': amount_tax,
            'amount_total': amount_total,
            'confidence': self._to_float(line.get('confidence')),
            'note': self._clean_string(line.get('evidence')),
        }

    def _clean_string(self, value):
        if value is None or value is False:
            return False
        if isinstance(value, str):
            value = value.strip()
            return value or False
        return str(value)

    def _to_float(self, value):
        if value is None or value is False:
            return False
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return False

        value = value.strip()
        if not value:
            return False
        normalized = re.sub(r'[^\d,.\-]', '', value)
        if not normalized:
            return False

        if ',' in normalized and '.' in normalized:
            if normalized.rfind(',') > normalized.rfind('.'):
                normalized = normalized.replace('.', '').replace(',', '.')
            else:
                normalized = normalized.replace(',', '')
        elif ',' in normalized:
            normalized = normalized.replace(',', '.')

        try:
            return float(normalized)
        except ValueError:
            return False

    def _to_date(self, value):
        value = self._clean_string(value)
        if not value:
            return False
        for date_format in self.DATE_FORMATS:
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                continue
        return False

    def _compute_header_tax_rate(self, header):
        untaxed_amount = header.get('untaxed_amount')
        tax_amount = header.get('tax_amount')
        if not self._is_positive_number(untaxed_amount):
            return False
        if not self._is_positive_number(tax_amount):
            return False
        return self._round_amount(tax_amount / untaxed_amount * 100)

    def _get_effective_tax_rate(self, line_tax_rate, header_tax_rate=False):
        if self._is_positive_number(line_tax_rate):
            return line_tax_rate
        if self._is_positive_number(header_tax_rate):
            return header_tax_rate
        return False

    def _compute_line_tax_amount(
        self,
        amount_untaxed,
        tax_rate=False,
        explicit_tax_amount=False,
    ):
        if self._is_number(amount_untaxed) and self._is_positive_number(tax_rate):
            return self._round_amount(amount_untaxed * tax_rate / 100)
        if self._is_number(explicit_tax_amount):
            return explicit_tax_amount
        return False

    def _compute_line_total(self, amount_untaxed, amount_tax=False):
        if not self._is_number(amount_untaxed):
            return False
        if self._is_number(amount_tax):
            return self._round_amount(amount_untaxed + amount_tax)
        return amount_untaxed

    def _is_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _is_positive_number(self, value):
        return self._is_number(value) and value > 0

    def _round_amount(self, value):
        return round(value + 0.000000001, 2)
