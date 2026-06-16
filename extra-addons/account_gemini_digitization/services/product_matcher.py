import difflib
import logging
import re

from odoo import _


_logger = logging.getLogger(__name__)


class ProductMatcher:
    MATCHED_THRESHOLD = 0.90
    CANDIDATE_THRESHOLD = 0.70

    CYRILLIC_LATIN_LOOKALIKES = str.maketrans({
        'А': 'A', 'а': 'a',
        'В': 'B', 'в': 'b',
        'Е': 'E', 'е': 'e',
        'К': 'K', 'к': 'k',
        'М': 'M', 'м': 'm',
        'Н': 'H', 'н': 'h',
        'О': 'O', 'о': 'o',
        'Р': 'P', 'р': 'p',
        'С': 'C', 'с': 'c',
        'Т': 'T', 'т': 't',
        'Х': 'X', 'х': 'x',
    })

    def __init__(self, env):
        self.env = env

    def match_job(self, job):
        job.ensure_one()
        if job.mode == 'partial_bill':
            self._match_partial_bill(job)
        elif job.mode == 'full_purchase':
            self._match_full_purchase(job)
        else:
            self._mark_job_lines_error(job, _('Unknown Gemini matching mode: %s') % job.mode)
        return True

    def _match_partial_bill(self, job):
        move_lines = self._get_move_product_lines(job)
        for line in job.line_ids:
            try:
                candidates = [
                    self._score_move_line(line, move_line, job.partner_id)
                    for move_line in move_lines
                ]
                self._write_match_result(line, candidates, include_move_lines=True)
            except Exception as error:
                _logger.exception('Gemini partial bill matching failed.')
                self._write_line_error(line, error)

    def _match_full_purchase(self, job):
        for line in job.line_ids:
            try:
                products = self._find_full_purchase_products(line, job.partner_id)
                candidates = [
                    self._score_product(line, product, job.partner_id)
                    for product in products
                ]
                self._write_match_result(line, candidates, include_move_lines=False)
            except Exception as error:
                _logger.exception('Gemini full purchase matching failed.')
                self._write_line_error(line, error)

    def _get_move_product_lines(self, job):
        move = job.move_id
        if not move:
            return []
        return [
            line
            for line in move.invoice_line_ids
            if getattr(line, 'product_id', False)
            and not getattr(line, 'display_type', False)
        ]

    def _find_full_purchase_products(self, line, partner):
        products = []
        for seller in self._find_supplierinfos(line, partner):
            self._append_unique(products, self._seller_product(seller))

        for code in self._line_codes(line):
            for product in self._search_products_exact('default_code', code):
                self._append_unique(products, product)
            for product in self._search_products_exact('barcode', code):
                self._append_unique(products, product)
            for product in self._search_products_code_like(code):
                self._append_unique(products, product)
            for seller in self._search_supplierinfos_code_like(code, partner):
                self._append_unique(products, self._seller_product(seller))

        for term in self._line_search_terms(line):
            for product in self._search_products_like(term):
                self._append_unique(products, product)
            for seller in self._search_supplierinfos_like(term, partner):
                self._append_unique(products, self._seller_product(seller))
        return products

    def _score_move_line(self, line, move_line, partner):
        product = move_line.product_id
        score, method, notes = self._score_product_identity(line, product, partner)
        score, method = self._score_move_line_text(line, move_line, score, method)
        score, notes = self._apply_partial_consistency(line, move_line, score, notes)
        return {
            'product': product,
            'move_line': move_line,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
        }

    def _score_product(self, line, product, partner):
        score, method, notes = self._score_product_identity(line, product, partner)
        return {
            'product': product,
            'move_line': False,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
        }

    def _score_product_identity(self, line, product, partner):
        score = 0.0
        method = False
        notes = []

        for code in self._line_codes(line):
            if self._supplier_code_matches(product, partner, code):
                return 1.0, 'supplier_code_exact', notes
            if self._code_equals(code, getattr(product, 'default_code', False)):
                return 1.0, 'default_code_exact', notes
            if self._code_equals(code, getattr(product, 'barcode', False)):
                return 1.0, 'barcode_exact', notes

        supplier_name = getattr(line, 'supplier_product_name', False)
        if supplier_name:
            for seller_name in self._product_supplier_names(product, partner):
                candidate_score = self._score_text_match(supplier_name, seller_name)
                score, method = self._choose_score(
                    score,
                    method,
                    candidate_score,
                    'supplier_name_match',
                )

        for query in self._line_name_terms(line):
            for target, target_method in self._product_name_targets(product):
                candidate_score = self._score_text_match(query, target)
                score, method = self._choose_score(
                    score,
                    method,
                    candidate_score,
                    target_method,
                )

        if score:
            notes.append('Identity score %.2f by %s.' % (score, method))
        return score, method, notes

    def _score_move_line_text(self, line, move_line, score, method):
        for query in self._line_name_terms(line):
            candidate_score = self._score_text_match(query, getattr(move_line, 'name', False))
            score, method = self._choose_score(
                score,
                method,
                candidate_score,
                'move_line_name_match',
            )
        return score, method

    def _apply_partial_consistency(self, line, move_line, score, notes):
        if not score:
            return score, notes

        quantity = self._to_float(getattr(line, 'quantity', False))
        move_quantity = self._to_float(getattr(move_line, 'quantity', False))
        if self._is_number(quantity) and self._is_number(move_quantity):
            if self._numbers_close(quantity, move_quantity, tolerance=0.01):
                score += 0.03
                notes.append('Quantity matches.')
            else:
                score -= 0.08
                notes.append('Quantity differs: recognized=%s document=%s.' % (
                    quantity,
                    move_quantity,
                ))

        comparisons = [
            ('price_unit', getattr(line, 'price_unit', False), getattr(move_line, 'price_unit', False)),
            ('amount_untaxed', getattr(line, 'amount_untaxed', False), getattr(move_line, 'price_subtotal', False)),
            ('amount_total', getattr(line, 'amount_total', False), getattr(move_line, 'price_total', False)),
        ]
        for label, recognized_value, move_value in comparisons:
            recognized_value = self._to_float(recognized_value)
            move_value = self._to_float(move_value)
            if not self._is_number(recognized_value) or not self._is_number(move_value):
                continue
            if self._amounts_close(recognized_value, move_value):
                score += 0.02
                notes.append('%s matches.' % label)
            else:
                score -= 0.04
                notes.append('%s differs: recognized=%s document=%s.' % (
                    label,
                    recognized_value,
                    move_value,
                ))
        return score, notes

    def _write_match_result(self, line, candidates, include_move_lines=False):
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get('product') and candidate.get('score', 0.0) > 0.0
        ]
        candidates.sort(key=lambda candidate: candidate['score'], reverse=True)
        visible_candidates = [
            candidate
            for candidate in candidates
            if candidate['score'] >= self.CANDIDATE_THRESHOLD
        ]
        best = candidates[0] if candidates else False

        values = {
            'matched_product_id': False,
            'move_line_id': False,
            'candidate_product_ids': [(6, 0, self._candidate_product_ids(visible_candidates))],
            'candidate_move_line_ids': [(6, 0, self._candidate_move_line_ids(visible_candidates))],
            'match_score': best['score'] if best else 0.0,
            'match_method': best['method'] if best else False,
            'match_note': self._build_match_note(candidates, visible_candidates),
        }

        if (
            len(visible_candidates) == 1
            and visible_candidates[0]['score'] >= self.MATCHED_THRESHOLD
        ):
            winner = visible_candidates[0]
            values.update({
                'match_status': 'matched',
                'matched_product_id': winner['product'].id,
            })
            if include_move_lines and winner.get('move_line'):
                values['move_line_id'] = winner['move_line'].id
        elif visible_candidates:
            values['match_status'] = 'ambiguous'
        else:
            values['match_status'] = 'not_found'
        line.write(values)

    def _write_line_error(self, line, error):
        line.write({
            'match_status': 'error',
            'match_score': 0.0,
            'match_method': False,
            'matched_product_id': False,
            'move_line_id': False,
            'candidate_product_ids': [(6, 0, [])],
            'candidate_move_line_ids': [(6, 0, [])],
            'match_note': _('Matching error: %s') % error,
        })

    def _mark_job_lines_error(self, job, message):
        for line in job.line_ids:
            line.write({
                'match_status': 'error',
                'match_note': message,
            })

    def _build_match_note(self, candidates, visible_candidates):
        if not candidates:
            return _('No product candidates found.')
        lines = []
        for candidate in candidates[:5]:
            product = candidate['product']
            product_name = getattr(product, 'display_name', False) or getattr(product, 'name', False)
            parts = [
                '%s: %.2f by %s' % (
                    product_name or product.id,
                    candidate['score'],
                    candidate['method'] or 'unknown',
                )
            ]
            if candidate.get('move_line'):
                parts.append('move_line_id=%s' % candidate['move_line'].id)
            if candidate.get('notes'):
                parts.append('; '.join(candidate['notes']))
            lines.append(' | '.join(parts))
        if visible_candidates and len(visible_candidates) > 1:
            lines.insert(0, _('Several product candidates require review.'))
        return '\n'.join(lines)

    def _candidate_product_ids(self, candidates):
        return self._unique_ids(candidate['product'] for candidate in candidates)

    def _candidate_move_line_ids(self, candidates):
        return self._unique_ids(
            candidate.get('move_line')
            for candidate in candidates
            if candidate.get('move_line')
        )

    def _unique_ids(self, records):
        ids = []
        seen = set()
        for record in records:
            record_id = getattr(record, 'id', False)
            if record_id and record_id not in seen:
                seen.add(record_id)
                ids.append(record_id)
        return ids

    def _find_supplierinfos(self, line, partner):
        sellers = []
        for code in self._line_codes(line):
            for seller in self._search_supplierinfos_exact(partner, product_code=code):
                self._append_unique(sellers, seller)
        supplier_name = getattr(line, 'supplier_product_name', False)
        if supplier_name:
            for seller in self._search_supplierinfos_exact(
                partner,
                product_name=supplier_name,
            ):
                self._append_unique(sellers, seller)
        return sellers

    def _search_supplierinfos_exact(self, partner, product_code=False, product_name=False):
        if not partner or (not product_code and not product_name):
            return []
        domain = [('partner_id', '=', partner.id)]
        if product_code and product_name:
            domain.extend(['|', ('product_code', '=', product_code), ('product_name', '=', product_name)])
        elif product_code:
            domain.append(('product_code', '=', product_code))
        elif product_name:
            domain.append(('product_name', '=', product_name))
        return list(self.env['product.supplierinfo'].search(domain, limit=100))

    def _search_supplierinfos_like(self, term, partner):
        if not term or not partner:
            return []
        return list(self.env['product.supplierinfo'].search([
            ('partner_id', '=', partner.id),
            ('product_name', 'ilike', term),
        ], limit=100))

    def _search_supplierinfos_code_like(self, code, partner):
        normalized_code = self._normalize_code(code)
        if not normalized_code or not partner:
            return []
        return list(self.env['product.supplierinfo'].search([
            ('partner_id', '=', partner.id),
            ('product_code', 'ilike', normalized_code),
        ], limit=100))

    def _search_products_exact(self, field_name, value):
        if not value:
            return []
        return list(self.env['product.product'].search([
            (field_name, '=', value),
        ], limit=100))

    def _search_products_like(self, term):
        if not term:
            return []
        return list(self.env['product.product'].search([
            '|',
            ('name', 'ilike', term),
            ('default_code', 'ilike', term),
        ], limit=100))

    def _search_products_code_like(self, code):
        normalized_code = self._normalize_code(code)
        if not normalized_code:
            return []
        return list(self.env['product.product'].search([
            '|',
            ('default_code', 'ilike', normalized_code),
            ('barcode', 'ilike', normalized_code),
        ], limit=100))

    def _supplier_code_matches(self, product, partner, code):
        for seller in self._product_sellers(product, partner):
            if self._code_equals(code, getattr(seller, 'product_code', False)):
                return True
        return False

    def _product_supplier_names(self, product, partner):
        names = []
        for seller in self._product_sellers(product, partner):
            for field_name in ('product_name', 'name'):
                value = getattr(seller, field_name, False)
                if value:
                    names.append(value)
        return names

    def _product_sellers(self, product, partner=False):
        sellers = []
        for source in (
            getattr(product, 'seller_ids', False),
            getattr(getattr(product, 'product_tmpl_id', False), 'seller_ids', False),
        ):
            if not source:
                continue
            for seller in source:
                if not partner or getattr(getattr(seller, 'partner_id', False), 'id', False) == partner.id:
                    self._append_unique(sellers, seller)
        return sellers

    def _seller_product(self, seller):
        product = getattr(seller, 'product_id', False)
        if product:
            return product
        template = getattr(seller, 'product_tmpl_id', False)
        if not template:
            return False
        return getattr(template, 'product_variant_id', False)

    def _product_name_targets(self, product):
        return [
            (getattr(product, 'display_name', False), 'product_display_name_match'),
            (getattr(product, 'name', False), 'product_name_match'),
        ]

    def _line_codes(self, line):
        codes = []
        for value in (
            getattr(line, 'supplier_product_code', False),
            getattr(line, 'supplier_product_name', False),
            getattr(line, 'description', False),
        ):
            if not value:
                continue
            if value == getattr(line, 'supplier_product_code', False):
                codes.append(value)
            codes.extend(self._extract_bracket_codes(value))
        return [code for code in self._unique_normalized_codes(codes) if code]

    def _line_name_terms(self, line):
        return [
            value
            for value in (
                getattr(line, 'supplier_product_name', False),
                getattr(line, 'description', False),
            )
            if value
        ]

    def _line_search_terms(self, line):
        terms = []
        for value in self._line_name_terms(line):
            normalized = self._normalize_text(value)
            if not normalized:
                continue
            tokens = [
                token
                for token in normalized.split()
                if len(token) >= 3 and not token.isdigit()
            ]
            if tokens:
                terms.append(' '.join(tokens[:3]))
                terms.extend(tokens[:3])
        terms.extend(self._line_codes(line))
        return list(dict.fromkeys(terms))

    def _extract_bracket_codes(self, value):
        return [
            match.strip()
            for match in re.findall(r'\[([^\]]+)\]', value or '')
            if match.strip()
        ]

    def _unique_normalized_codes(self, codes):
        result = []
        seen = set()
        for code in codes:
            normalized = self._normalize_code(code)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(code)
        return result

    def _score_text_match(self, query, target):
        query_normalized = self._normalize_text(query)
        target_normalized = self._normalize_text(target)
        if not query_normalized or not target_normalized:
            return 0.0
        if query_normalized == target_normalized:
            return 0.95

        similarity = max(
            difflib.SequenceMatcher(None, query_normalized, target_normalized).ratio(),
            self._token_similarity(query_normalized, target_normalized),
        )
        if similarity >= 0.90:
            return min(0.89, 0.84 + (similarity - 0.90) * 0.5)
        if similarity >= 0.80:
            return 0.80 + (similarity - 0.80) * 0.4
        if similarity >= 0.70:
            return 0.70 + (similarity - 0.70) * 0.3
        return similarity * 0.8

    def _token_similarity(self, left, right):
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _choose_score(self, current_score, current_method, candidate_score, method):
        if candidate_score > current_score:
            return candidate_score, method
        return current_score, current_method

    def _normalize_text(self, value):
        if not value:
            return ''
        value = str(value)
        value = re.sub(r'\[[^\]]+\]', ' ', value)
        value = value.translate(self.CYRILLIC_LATIN_LOOKALIKES)
        value = value.lower().strip()
        value = re.sub(r'([^\W\d_]+)(\d+)', r'\1 \2', value, flags=re.U)
        value = re.sub(r'(\d+)([^\W\d_]+)', r'\1 \2', value, flags=re.U)
        value = re.sub(r'[^\w\s]', ' ', value, flags=re.U)
        value = value.replace('_', ' ')
        return re.sub(r'\s+', ' ', value).strip()

    def _normalize_code(self, value):
        if not value:
            return ''
        value = str(value).translate(self.CYRILLIC_LATIN_LOOKALIKES).lower()
        return re.sub(r'[\W_]+', '', value, flags=re.U)

    def _code_equals(self, left, right):
        left_normalized = self._normalize_code(left)
        right_normalized = self._normalize_code(right)
        return bool(left_normalized and right_normalized and left_normalized == right_normalized)

    def _to_float(self, value):
        if value in (None, False, ''):
            return False
        try:
            return float(value)
        except (TypeError, ValueError):
            return False

    def _is_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _numbers_close(self, first, second, tolerance=0.01):
        if not self._is_number(first) or not self._is_number(second):
            return False
        return abs(first - second) <= tolerance

    def _amounts_close(self, first, second):
        if not self._is_number(first) or not self._is_number(second):
            return False
        tolerance = max(0.05, abs(second) * 0.01)
        return abs(first - second) <= tolerance

    def _clamp_score(self, score):
        return round(max(0.0, min(1.0, score or 0.0)), 4)

    def _append_unique(self, records, record):
        if not record:
            return
        record_id = getattr(record, 'id', False)
        if not record_id:
            return
        if record_id not in {getattr(existing, 'id', False) for existing in records}:
            records.append(record)
