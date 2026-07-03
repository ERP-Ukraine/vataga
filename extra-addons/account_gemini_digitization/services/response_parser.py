import re
import logging
from datetime import datetime

from odoo import _
from odoo.exceptions import UserError

from .supplier_code import SupplierArticleNormalizer


_logger = logging.getLogger(__name__)


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
        header_tax_rate = self._compute_header_tax_rate(header, lines)
        legacy_line_total_kind = self._detect_legacy_line_total_kind(lines, header)
        return {
            'header': header,
            'lines': [
                self._parse_line(
                    line,
                    index,
                    header_tax_rate=header_tax_rate,
                    legacy_line_total_kind=legacy_line_total_kind,
                )
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
                'price_unit_without_tax': line['price_unit_without_tax'],
                'price_unit_with_tax': line['price_unit_with_tax'],
                'price_unit': line['price_unit'],
                'tax_rate': line['tax_rate'],
                'line_subtotal_without_tax': line['line_subtotal_without_tax'],
                'line_tax_amount': line['line_tax_amount'],
                'line_total_with_tax': line['line_total_with_tax'],
                'amount_untaxed': line['amount_untaxed'],
                'amount_tax': line['amount_tax'],
                'amount_total': line['amount_total'],
                'confidence': line['confidence'],
                'source_columns': line['source_columns'],
                'note': line['note'],
                'match_status': 'draft',
            })
        return parsed

    def _parse_line(
        self,
        line,
        index,
        header_tax_rate=False,
        legacy_line_total_kind=False,
    ):
        if not isinstance(line, dict):
            raise UserError(_('Gemini line %s must be a JSON object.') % index)
        warnings = []
        quantity = self._to_float(line.get('quantity'))
        source_columns = self._stringify_value(line.get('source_columns'))
        evidence = self._clean_string(line.get('evidence'))
        context_text = self._build_context_text(source_columns, evidence)

        price_unit_without_tax = self._to_float(line.get('price_unit_without_tax'))
        price_unit_with_tax = self._to_float(line.get('price_unit_with_tax'))
        legacy_price_unit = self._to_float(line.get('unit_price'))
        if not self._is_number(price_unit_without_tax) and not self._is_number(
            price_unit_with_tax
        ) and self._is_number(legacy_price_unit):
            if self._context_says_with_tax(context_text):
                price_unit_with_tax = legacy_price_unit
            else:
                price_unit_without_tax = legacy_price_unit

        line_subtotal_without_tax = self._to_float(
            line.get('line_subtotal_without_tax')
        )
        line_tax_amount = self._first_number(
            line.get('line_tax_amount'),
            line.get('tax_amount'),
        )
        line_total_with_tax = self._to_float(line.get('line_total_with_tax'))
        tax_rate, tax_rate_warning = self._normalize_tax_rate(
            self._to_float(line.get('tax_rate')),
            header_tax_rate=header_tax_rate,
            context_text=context_text,
            line_subtotal_without_tax=line_subtotal_without_tax,
            line_tax_amount=line_tax_amount,
        )
        if tax_rate_warning:
            warnings.append(tax_rate_warning)

        legacy_line_total = self._to_float(line.get('line_total'))
        if not self._is_number(line_subtotal_without_tax) and not self._is_number(
            line_total_with_tax
        ) and self._is_number(legacy_line_total):
            if self._context_says_without_tax(context_text):
                line_subtotal_without_tax = legacy_line_total
            elif self._context_says_with_tax(context_text):
                line_total_with_tax = legacy_line_total
            elif legacy_line_total_kind == 'without_tax':
                line_subtotal_without_tax = legacy_line_total
            elif legacy_line_total_kind == 'with_tax':
                line_total_with_tax = legacy_line_total
            else:
                warnings.append(
                    'Legacy line_total is ambiguous and was not used as a normalized amount.'
                )

        normalized = self._normalize_amounts(
            quantity=quantity,
            price_unit_without_tax=price_unit_without_tax,
            price_unit_with_tax=price_unit_with_tax,
            line_subtotal_without_tax=line_subtotal_without_tax,
            line_tax_amount=line_tax_amount,
            line_total_with_tax=line_total_with_tax,
            tax_rate=tax_rate,
        )
        if normalized['warning']:
            warnings.append(normalized['warning'])

        supplier_product_code = self._clean_string(
            self._first_string(
                line.get('supplier_product_code'),
                line.get('supplier_code'),
                line.get('product_code'),
                line.get('article'),
                line.get('sku'),
            )
        )
        if supplier_product_code:
            _logger.info(
                'OCR article raw=%s, normalized=%s',
                supplier_product_code,
                SupplierArticleNormalizer.normalize(supplier_product_code),
            )

        return {
            'sequence': index * 10,
            'supplier_product_code': supplier_product_code,
            'supplier_product_name': self._clean_string(
                line.get('supplier_product_name')
            ),
            'description': self._clean_string(line.get('description')),
            'quantity': quantity,
            'uom_name': self._clean_string(line.get('uom')),
            'price_unit_without_tax': normalized['price_unit_without_tax'],
            'price_unit_with_tax': normalized['price_unit_with_tax'],
            'price_unit': normalized['price_unit'],
            'tax_rate': tax_rate,
            'line_subtotal_without_tax': normalized['line_subtotal_without_tax'],
            'line_tax_amount': normalized['line_tax_amount'],
            'line_total_with_tax': normalized['line_total_with_tax'],
            'amount_untaxed': normalized['amount_untaxed'],
            'amount_tax': normalized['amount_tax'],
            'amount_total': normalized['amount_total'],
            'confidence': self._to_float(line.get('confidence')),
            'source_columns': source_columns,
            'note': self._build_note(evidence, warnings),
        }

    def _clean_string(self, value):
        if value is None or value is False:
            return False
        if isinstance(value, str):
            value = value.strip()
            return value or False
        return str(value)

    def _first_string(self, *values):
        for value in values:
            cleaned = self._clean_string(value)
            if cleaned:
                return cleaned
        return False

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

    def _first_number(self, *values):
        for value in values:
            number = self._to_float(value)
            if self._is_number(number):
                return number
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

    def _compute_header_tax_rate(self, header, lines):
        explicit_rates = {
            self._normalize_tax_rate_number(self._to_float(line.get('tax_rate')))
            for line in lines
            if isinstance(line, dict)
            and self._is_positive_number(self._to_float(line.get('tax_rate')))
        }
        if len(explicit_rates) > 1:
            return False

        untaxed_amount = header.get('untaxed_amount')
        tax_amount = header.get('tax_amount')
        if not self._is_positive_number(untaxed_amount):
            return False
        if not self._is_positive_number(tax_amount):
            return False
        return self._round_amount(tax_amount / untaxed_amount * 100)

    def _normalize_tax_rate(
        self,
        line_tax_rate,
        header_tax_rate=False,
        context_text=False,
        line_subtotal_without_tax=False,
        line_tax_amount=False,
    ):
        if self._is_number(line_tax_rate):
            if line_tax_rate == 0:
                if self._context_says_zero_tax(context_text):
                    return 0.0, False
                return False, 'Gemini returned tax_rate 0 without explicit zero-rated or VAT-exempt evidence.'
            if line_tax_rate > 1:
                return self._round_rate(line_tax_rate), False

            normalized = self._round_rate(line_tax_rate * 100)
            if self._fraction_rate_matches_amounts(
                line_tax_rate,
                line_subtotal_without_tax,
                line_tax_amount,
            ):
                return normalized, False
            if (
                self._is_positive_number(header_tax_rate)
                and self._rates_close(line_tax_rate, header_tax_rate / 100)
            ):
                return normalized, False
            return normalized, (
                'Gemini returned fractional tax_rate %.4g; interpreted as %.4g%%.'
                % (line_tax_rate, normalized)
            )

        if self._is_positive_number(header_tax_rate):
            return self._round_rate(header_tax_rate), False
        return False, False

    def _normalize_tax_rate_number(self, value):
        if not self._is_positive_number(value):
            return False
        if value <= 1:
            return self._round_rate(value * 100)
        return self._round_rate(value)

    def _fraction_rate_matches_amounts(self, fraction_rate, amount_untaxed, amount_tax):
        if not self._is_positive_number(amount_untaxed):
            return False
        if not self._is_number(amount_tax):
            return False
        actual_fraction = amount_tax / amount_untaxed
        return self._rates_close(actual_fraction, fraction_rate)

    def _rates_close(self, first, second, tolerance=0.0001):
        if not self._is_number(first) or not self._is_number(second):
            return False
        return abs(first - second) <= tolerance

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

    def _normalize_amounts(
        self,
        quantity=False,
        price_unit_without_tax=False,
        price_unit_with_tax=False,
        line_subtotal_without_tax=False,
        line_tax_amount=False,
        line_total_with_tax=False,
        tax_rate=False,
    ):
        warning = False

        if self._is_number(price_unit_with_tax) and not self._is_number(
            price_unit_without_tax
        ) and self._is_positive_number(tax_rate):
            price_unit_without_tax = self._round_amount(
                price_unit_with_tax / (1 + tax_rate / 100)
            )
        if self._is_number(price_unit_without_tax) and not self._is_number(
            price_unit_with_tax
        ) and self._is_positive_number(tax_rate):
            price_unit_with_tax = self._round_amount(
                price_unit_without_tax * (1 + tax_rate / 100)
            )

        if not self._is_number(line_subtotal_without_tax):
            if self._is_number(quantity) and self._is_number(price_unit_without_tax):
                line_subtotal_without_tax = self._round_amount(
                    quantity * price_unit_without_tax
                )
            elif self._is_number(line_total_with_tax) and self._is_positive_number(
                tax_rate
            ):
                line_subtotal_without_tax = self._round_amount(
                    line_total_with_tax / (1 + tax_rate / 100)
                )
            elif self._is_number(line_total_with_tax) and self._is_number(
                line_tax_amount
            ):
                line_subtotal_without_tax = self._round_amount(
                    line_total_with_tax - line_tax_amount
                )

        if not self._is_number(line_tax_amount):
            if self._is_number(line_subtotal_without_tax) and self._is_positive_number(
                tax_rate
            ):
                line_tax_amount = self._round_amount(
                    line_subtotal_without_tax * tax_rate / 100
                )
            elif self._is_number(line_total_with_tax) and self._is_number(
                line_subtotal_without_tax
            ):
                line_tax_amount = self._round_amount(
                    line_total_with_tax - line_subtotal_without_tax
                )

        if not self._is_number(line_total_with_tax):
            if self._is_number(line_subtotal_without_tax) and self._is_number(
                line_tax_amount
            ):
                line_total_with_tax = self._round_amount(
                    line_subtotal_without_tax + line_tax_amount
                )
            elif self._is_number(quantity) and self._is_number(price_unit_with_tax):
                line_total_with_tax = self._round_amount(quantity * price_unit_with_tax)

        if not self._is_number(line_total_with_tax) and self._is_number(
            line_subtotal_without_tax
        ) and not self._is_number(line_tax_amount):
            line_total_with_tax = line_subtotal_without_tax

        if self._is_number(line_subtotal_without_tax) and self._is_number(
            line_tax_amount
        ) and not self._is_number(line_total_with_tax):
            line_total_with_tax = self._round_amount(
                line_subtotal_without_tax + line_tax_amount
            )

        if not self._is_number(tax_rate) and not self._is_number(line_tax_amount):
            warning = 'Tax rate is not reliably determined.'

        return {
            'price_unit_without_tax': price_unit_without_tax,
            'price_unit_with_tax': price_unit_with_tax,
            'line_subtotal_without_tax': line_subtotal_without_tax,
            'line_tax_amount': line_tax_amount,
            'line_total_with_tax': line_total_with_tax,
            'price_unit': price_unit_without_tax,
            'amount_untaxed': line_subtotal_without_tax,
            'amount_tax': line_tax_amount,
            'amount_total': line_total_with_tax,
            'warning': warning,
        }

    def _detect_legacy_line_total_kind(self, lines, header):
        legacy_totals = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            if self._has_precise_amount_fields(line):
                continue
            value = self._to_float(line.get('line_total'))
            if self._is_number(value):
                legacy_totals.append(value)
        if not legacy_totals:
            return False

        legacy_sum = self._round_amount(sum(legacy_totals))
        if self._amounts_close(legacy_sum, header.get('untaxed_amount')):
            return 'without_tax'
        if self._amounts_close(legacy_sum, header.get('total_amount')):
            return 'with_tax'
        return False

    def _has_precise_amount_fields(self, line):
        return any(
            self._is_number(self._to_float(line.get(field_name)))
            for field_name in (
                'line_subtotal_without_tax',
                'line_tax_amount',
                'line_total_with_tax',
            )
        )

    def _build_context_text(self, source_columns=False, evidence=False):
        return ' '.join(
            value.lower()
            for value in (source_columns, evidence)
            if isinstance(value, str) and value
        )

    def _context_says_without_tax(self, context_text):
        return bool(re.search(
            r'(без\s*(пдв|ндс|vat|tax)|without\s*(vat|tax)|excl\.?\s*(vat|tax)|net)',
            context_text,
        ))

    def _context_says_with_tax(self, context_text):
        return bool(re.search(
            r'((з|із|с)\s*(пдв|ндс)|with\s*(vat|tax)|incl\.?\s*(vat|tax)|gross)',
            context_text,
        ))

    def _context_says_zero_tax(self, context_text):
        return bool(re.search(
            r'(\b0\s*%|zero[-\s]?rated|vat[-\s]?exempt|tax[-\s]?exempt|no\s*vat)',
            context_text or '',
        ))

    def _stringify_value(self, value):
        if value in (None, False, ''):
            return False
        if isinstance(value, str):
            return value.strip() or False
        if isinstance(value, (list, tuple, dict)):
            return str(value)
        return str(value)

    def _build_note(self, evidence=False, warnings=None):
        parts = []
        if evidence:
            parts.append(evidence)
        for warning in warnings or []:
            if warning:
                parts.append('Warning: %s' % warning)
        return '\n'.join(parts) or False

    def _amounts_close(self, first, second, tolerance=0.02):
        if not self._is_number(first) or not self._is_number(second):
            return False
        return abs(first - second) <= tolerance

    def _is_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _is_positive_number(self, value):
        return self._is_number(value) and value > 0

    def _round_amount(self, value):
        return round(value + 0.000000001, 2)

    def _round_rate(self, value):
        return round(value + 0.000000001, 4)
