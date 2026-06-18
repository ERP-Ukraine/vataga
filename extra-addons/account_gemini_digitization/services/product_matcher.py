import difflib
import logging
import re

from odoo import _


_logger = logging.getLogger(__name__)


class ProductMatcher:
    MATCHED_THRESHOLD = 0.90
    CANDIDATE_THRESHOLD = 0.70
    BEST_GAP_MATCH_THRESHOLD = 0.05
    LOW_VALUE_CODE_PATTERNS = (
        re.compile(r'^ip\d{2,3}$', flags=re.I),
    )
    LOW_VALUE_CODE_TOKENS = {
        'abs',
        'led',
        'mm',
        'pc',
        'pvc',
    }
    GENERIC_NAME_TOKENS = {
        'aluminium',
        'box',
        'cover',
        'plastic',
        'алюмінієвий',
        'алюминиевый',
        'бокс',
        'коробка',
        'корпус',
        'кришка',
        'кріплення',
        'пластик',
        'пластиковий',
        'универсальний',
        'універсальний',
        'герметичний',
    }
    INTERNAL_PRODUCT_TERMS = (
        'модифікований',
        'модифицированный',
        'під блок',
        'под блок',
        'наземний робот',
        'наземный робот',
        'центрального',
    )

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
        self._repair_job_partner(job)
        if job.mode == 'partial_bill':
            self._match_partial_bill(job)
        elif job.mode == 'full_bill':
            self._match_full_bill(job)
        elif job.mode == 'full_purchase':
            self._match_full_purchase(job)
        else:
            self._mark_job_lines_error(job, _('Unknown Gemini matching mode: %s') % job.mode)
        return True

    def _match_partial_bill(self, job):
        line_source = self._get_partial_bill_line_source(job)
        move_lines = line_source['product_lines']
        partner = self._get_job_partner(job)
        for line in job.line_ids:
            try:
                candidates = [
                    self._score_move_line(line, move_line, partner)
                    for move_line in move_lines
                ]
                diagnostics = self._build_partial_diagnostics(
                    line,
                    job,
                    line_source,
                    candidates,
                )
                self._write_match_result(
                    line,
                    candidates,
                    include_move_lines=True,
                    diagnostics=diagnostics,
                )
            except Exception as error:
                _logger.exception('Gemini partial bill matching failed.')
                self._write_line_error(line, error)

    def _match_full_purchase(self, job):
        partner = self._get_job_partner(job)
        for line in job.line_ids:
            try:
                products = self._find_full_purchase_products(line, partner)
                candidates = [
                    self._score_product(line, product, partner)
                    for product in products
                ]
                self._write_match_result(line, candidates, include_move_lines=False)
            except Exception as error:
                _logger.exception('Gemini full purchase matching failed.')
                self._write_line_error(line, error)

    def _match_full_bill(self, job):
        partner = self._get_job_partner(job)
        for line in job.line_ids:
            try:
                products = self._find_full_bill_products(line, partner)
                candidates = [
                    self._score_product(
                        line,
                        product,
                        partner,
                        strict_code_profile=True,
                    )
                    for product in products
                ]
                diagnostics = self._build_product_diagnostics(
                    line,
                    job,
                    products,
                    candidates,
                    mode_label='Full vendor bill',
                )
                self._write_match_result(
                    line,
                    candidates,
                    include_move_lines=False,
                    diagnostics=diagnostics,
                    allow_best_gap_match=True,
                )
            except Exception as error:
                _logger.exception('Gemini full bill matching failed.')
                self._write_line_error(line, error)

    def _get_move_product_lines(self, job):
        return self._get_partial_bill_line_source(job)['product_lines']

    def _get_partial_bill_line_source(self, job):
        move = job.move_id
        if not move:
            return {
                'source': 'none',
                'product_lines': [],
                'invoice_lines': [],
                'invoice_product_lines': [],
                'line_ids': [],
                'line_product_lines': [],
            }

        invoice_lines = list(getattr(move, 'invoice_line_ids', []))
        invoice_product_lines = [
            line
            for line in invoice_lines
            if self._is_move_product_line(line)
        ]
        line_ids = list(getattr(move, 'line_ids', []))
        line_product_lines = [
            line
            for line in line_ids
            if self._is_move_product_line(line)
        ]

        if invoice_product_lines:
            source = 'invoice_line_ids'
            product_lines = invoice_product_lines
        else:
            source = 'line_ids fallback'
            product_lines = line_product_lines

        return {
            'source': source,
            'product_lines': product_lines,
            'invoice_lines': invoice_lines,
            'invoice_product_lines': invoice_product_lines,
            'line_ids': line_ids,
            'line_product_lines': line_product_lines,
        }

    def _is_move_product_line(self, line):
        if not getattr(line, 'product_id', False):
            return False

        display_type = getattr(line, 'display_type', False)
        if display_type and display_type != 'product':
            return False

        account = getattr(line, 'account_id', False)
        account_type = getattr(account, 'account_type', False) if account else False
        if account_type and self._is_receivable_or_payable_account_type(account_type):
            return False
        return True

    def _is_receivable_or_payable_account_type(self, account_type):
        account_type = str(account_type or '').lower()
        return 'receivable' in account_type or 'payable' in account_type

    def _get_job_partner(self, job):
        return (
            getattr(job, 'partner_id', False)
            or getattr(getattr(job, 'move_id', False), 'partner_id', False)
            or getattr(getattr(job, 'purchase_order_id', False), 'partner_id', False)
            or False
        )

    def _repair_job_partner(self, job):
        partner = self._get_job_partner(job)
        if partner and not getattr(job, 'partner_id', False):
            job.write({'partner_id': partner.id})
        return partner

    def _get_move_invoice_lines(self, job):
        move = job.move_id
        if not move:
            return []
        return list(getattr(move, 'invoice_line_ids', []))

    def _get_move_line_ids(self, job):
        move = job.move_id
        if not move:
            return []
        return list(getattr(move, 'line_ids', []))

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

    def _find_full_bill_products(self, line, partner):
        products = []
        profile = self._line_code_profile(line)
        search_codes = profile['primary_codes'] or profile['secondary_codes']

        for seller in self._find_supplierinfos_by_codes(
            profile['primary_codes'],
            partner,
        ):
            self._append_unique(products, self._seller_product(seller))

        for code in search_codes:
            if self._is_low_value_code(code):
                continue
            for product in self._search_products_exact('default_code', code):
                self._append_unique(products, product)
            for product in self._search_products_exact('barcode', code):
                self._append_unique(products, product)
            for product in self._search_products_code_like(code):
                self._append_unique(products, product)
            for seller in self._search_supplierinfos_code_like(code, partner):
                self._append_unique(products, self._seller_product(seller))

        for term in self._full_bill_search_terms(line, profile):
            for product in self._search_products_like(term):
                self._append_unique(products, product)
            for seller in self._search_supplierinfos_like(term, partner):
                self._append_unique(products, self._seller_product(seller))
        return products

    def _score_move_line(self, line, move_line, partner):
        product = move_line.product_id
        score, method, notes = self._score_product_identity(line, product, partner)
        score, method, notes = self._score_partial_code_match(
            line,
            move_line,
            partner,
            score,
            method,
            notes,
        )
        score, method = self._score_move_line_text(line, move_line, score, method)
        score, notes = self._apply_partial_consistency(line, move_line, score, notes)
        had_code_or_text_match = score >= 0.92
        score, method, notes, numeric_strong = self._apply_partial_numeric_fallback(
            line,
            move_line,
            score,
            method,
            notes,
        )
        if had_code_or_text_match and numeric_strong:
            score = max(score, 0.97)
            notes.append('Code/name match plus quantity/price/subtotal consistency.')
        return {
            'product': product,
            'move_line': move_line,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
            'numeric_strong': numeric_strong,
            'extracted_codes': self._line_codes(line),
            'candidate_codes': self._candidate_codes(product, move_line, partner),
        }

    def _score_product(self, line, product, partner, strict_code_profile=False):
        if strict_code_profile:
            return self._score_product_strict(line, product, partner)

        score, method, notes = self._score_product_identity(line, product, partner)
        score, method, notes = self._score_product_code_match(
            line,
            product,
            partner,
            score,
            method,
            notes,
        )
        return {
            'product': product,
            'move_line': False,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
            'extracted_codes': self._line_codes(line),
            'candidate_codes': self._product_candidate_codes(product, partner),
        }

    def _score_product_strict(self, line, product, partner):
        profile = self._line_code_profile(line)
        primary_codes = profile['primary_codes']
        secondary_codes = profile['secondary_codes']
        ignored_low_value_tokens = profile['ignored_low_value_tokens']
        candidate_codes = self._product_candidate_codes(product, partner)
        notes = []
        boosts = []
        penalties = []
        score = 0.0
        method = False
        supplierinfo_signal = 'none'

        for token in ignored_low_value_tokens:
            notes.append('Ignored low-value token %s for exact matching.' % token)

        for code in primary_codes:
            if self._supplier_code_matches(product, partner, code):
                score = 1.0
                method = 'supplierinfo_code_exact'
                supplierinfo_signal = 'supplierinfo_code_exact:%s' % code
                notes.append('Exact vendor supplierinfo code match: %s.' % code)
                break
            if self._code_equals(code, getattr(product, 'default_code', False)):
                score = 1.0
                method = 'default_code_exact'
                notes.append('Exact product default_code match: %s.' % code)
                break
            if self._code_equals(code, getattr(product, 'barcode', False)):
                score = 1.0
                method = 'barcode_exact'
                notes.append('Exact product barcode match: %s.' % code)
                break

        if score < 1.0:
            supplierinfo_signal = self._supplierinfo_signal(
                line,
                product,
                partner,
                primary_codes,
            )
            score, method, notes = self._score_primary_product_codes(
                product,
                partner,
                primary_codes,
                candidate_codes,
                score,
                method,
                notes,
            )

        base_score = score
        similarity_score = self._best_product_similarity(line, product)
        if not score and similarity_score >= self.CANDIDATE_THRESHOLD:
            score = similarity_score
            method = 'product_name_similarity'
            notes.append('Name similarity used as base score %.2f.' % similarity_score)
        elif score and similarity_score >= self.CANDIDATE_THRESHOLD:
            boost = min(0.04, (similarity_score - self.CANDIDATE_THRESHOLD) * 0.2)
            if boost > 0:
                score += boost
                boosts.append('name_similarity +%.2f' % boost)

        secondary_boost = self._secondary_token_boost(
            product,
            partner,
            secondary_codes,
        )
        if secondary_boost:
            score += secondary_boost
            boosts.append('meaningful_secondary_tokens +%.2f' % secondary_boost)

        meaningful_boost = self._meaningful_token_overlap_boost(line, product)
        if meaningful_boost:
            score += meaningful_boost
            boosts.append('meaningful_text_overlap +%.2f' % meaningful_boost)

        brand_boost = self._brand_boost(line, product)
        if brand_boost:
            score += brand_boost
            boosts.append('brand_match +%.2f' % brand_boost)

        dimension_boost = self._dimension_boost(line, product)
        if dimension_boost:
            score += dimension_boost
            boosts.append('dimension_match +%.2f' % dimension_boost)

        supplierinfo_boost = self._supplierinfo_presence_boost(product, partner)
        if supplierinfo_boost:
            score += supplierinfo_boost
            boosts.append('vendor_supplierinfo_present +%.2f' % supplierinfo_boost)
            if supplierinfo_signal == 'none':
                supplierinfo_signal = 'vendor_supplierinfo_present'

        authoritative_exact = method in (
            'supplierinfo_code_exact',
            'default_code_exact',
            'barcode_exact',
        )
        internal_penalty = (
            0.0
            if authoritative_exact
            else self._internal_product_penalty(line, product)
        )
        if internal_penalty:
            score -= internal_penalty
            penalties.append(
                'internal/modified product terms not present in OCR text -%.2f'
                % internal_penalty
            )

        if not score:
            method = method or False
        notes.extend('Boost: %s.' % boost for boost in boosts)
        notes.extend('Penalty: %s.' % penalty for penalty in penalties)
        if base_score:
            notes.append('Base score %.2f by %s.' % (base_score, method or 'unknown'))
        notes.append('Similarity score %.2f.' % similarity_score)

        return {
            'product': product,
            'move_line': False,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
            'extracted_codes': self._line_codes(line),
            'candidate_codes': candidate_codes,
            'primary_codes': primary_codes,
            'secondary_codes': secondary_codes,
            'ignored_low_value_tokens': ignored_low_value_tokens,
            'supplierinfo_signal': supplierinfo_signal,
            'base_score': self._clamp_score(base_score),
            'similarity_score': self._clamp_score(similarity_score),
            'boosts': boosts,
            'penalties': penalties,
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

    def _score_primary_product_codes(
        self,
        product,
        partner,
        primary_codes,
        candidate_codes,
        score,
        method,
        notes,
    ):
        for code in primary_codes:
            exact_token = self._find_code_exact_token(code, candidate_codes)
            if exact_token:
                score, method = self._choose_score(
                    score,
                    method,
                    0.89,
                    'product_primary_code_token_exact',
                )
                notes.append(
                    'Primary code-token match: %s equals product token %s.'
                    % (code, exact_token)
                )
                continue

            prefix_token = self._find_code_prefix_token(code, candidate_codes)
            if prefix_token:
                score, method = self._choose_score(
                    score,
                    method,
                    0.88,
                    'product_primary_code_prefix',
                )
                notes.append(
                    'Primary code prefix/substring match: %s found in product token %s.'
                    % (code, prefix_token)
                )
                continue

            for target, target_method in self._product_code_targets(product, partner):
                if not self._code_in_text(code, target):
                    continue
                score, method = self._choose_score(
                    score,
                    method,
                    0.86,
                    'primary_%s' % target_method,
                )
                notes.append(
                    'Primary code match: %s found in %s.' % (code, target_method)
                )
        return score, method, notes

    def _supplierinfo_signal(self, line, product, partner, primary_codes):
        if not partner:
            return 'no_vendor_partner'
        sellers = self._product_sellers(product, partner)
        if not sellers:
            return 'no_vendor_supplierinfo'
        for code in primary_codes:
            for seller in sellers:
                if self._code_equals(code, getattr(seller, 'product_code', False)):
                    return 'supplierinfo_code_exact:%s' % code
        supplier_name = getattr(line, 'supplier_product_name', False)
        if supplier_name:
            for seller_name in self._product_supplier_names(product, partner):
                if self._normalize_text(supplier_name) == self._normalize_text(seller_name):
                    return 'supplierinfo_name_exact'
        return 'vendor_supplierinfo_present'

    def _best_product_similarity(self, line, product):
        best = 0.0
        for query in self._line_name_terms(line):
            for target, _target_method in self._product_name_targets(product):
                best = max(best, self._score_text_match(query, target))
        return best

    def _secondary_token_boost(self, product, partner, secondary_codes):
        if not secondary_codes:
            return 0.0
        matched = 0
        candidate_codes = self._product_candidate_codes(product, partner)
        for token in secondary_codes:
            if self._is_low_value_code(token):
                continue
            if self._find_code_exact_token(token, candidate_codes):
                matched += 1
                continue
            if self._find_code_prefix_token(token, candidate_codes):
                matched += 1
                continue
            if any(self._code_in_text(token, target) for target, _method in self._product_code_targets(product, partner)):
                matched += 1
        return min(0.04, matched * 0.02)

    def _meaningful_token_overlap_boost(self, line, product):
        line_tokens = self._meaningful_tokens(self._ocr_line_text(line))
        product_tokens = self._meaningful_tokens(self._product_text(product))
        if not line_tokens or not product_tokens:
            return 0.0
        overlap = line_tokens & product_tokens
        if not overlap:
            return 0.0
        ratio = len(overlap) / len(line_tokens)
        if ratio >= 0.50:
            return 0.05
        if ratio >= 0.30:
            return 0.03
        if len(overlap) >= 2:
            return 0.02
        return 0.0

    def _brand_boost(self, line, product):
        line_text = self._normalize_text(self._ocr_line_text(line))
        product_text = self._normalize_text(self._product_text(product))
        for brand in ('gainta',):
            if brand in line_text and brand in product_text:
                return 0.04
        return 0.0

    def _dimension_boost(self, line, product):
        line_dimensions = self._extract_dimension_numbers(self._ocr_line_text(line))
        product_dimensions = self._extract_dimension_numbers(self._product_text(product))
        if len(line_dimensions) < 2 or len(product_dimensions) < 2:
            return 0.0
        unmatched = list(product_dimensions)
        matches = 0
        for line_dimension in line_dimensions:
            for index, product_dimension in enumerate(unmatched):
                if abs(line_dimension - product_dimension) <= 1.0:
                    matches += 1
                    unmatched.pop(index)
                    break
        if matches >= min(3, len(line_dimensions), len(product_dimensions)):
            return 0.05
        if matches >= 2:
            return 0.03
        return 0.0

    def _supplierinfo_presence_boost(self, product, partner):
        if not partner:
            return 0.0
        return 0.03 if self._product_sellers(product, partner) else 0.0

    def _internal_product_penalty(self, line, product):
        product_text = self._normalize_text(
            '%s %s %s' % (
                getattr(product, 'default_code', False) or '',
                getattr(product, 'display_name', False) or '',
                getattr(product, 'name', False) or '',
            )
        )
        line_text = self._normalize_text(self._ocr_line_text(line))
        penalty = 0.0
        default_code = (getattr(product, 'default_code', False) or '').upper()
        if default_code.startswith('SUB-'):
            penalty += 0.08
        for term in self.INTERNAL_PRODUCT_TERMS:
            term_normalized = self._normalize_text(term)
            if term_normalized and term_normalized in product_text and term_normalized not in line_text:
                penalty += 0.05
        return min(0.15, penalty)

    def _score_product_code_match(self, line, product, partner, score, method, notes):
        candidate_codes = self._product_candidate_codes(product, partner)
        for code in self._line_codes(line):
            exact_token = self._find_code_exact_token(code, candidate_codes)
            if exact_token:
                score, method = self._choose_score(
                    score,
                    method,
                    1.0,
                    'product_code_token_exact',
                )
                notes.append(
                    'Exact code-token match: %s equals product token %s.'
                    % (code, exact_token)
                )
                continue

            prefix_token = self._find_code_prefix_token(code, candidate_codes)
            if prefix_token:
                score, method = self._choose_score(
                    score,
                    method,
                    0.92,
                    'product_code_prefix',
                )
                notes.append(
                    'Code prefix/substring match: %s found in product token %s.'
                    % (code, prefix_token)
                )
                continue

            for target, target_method in self._product_code_targets(product, partner):
                if not self._code_in_text(code, target):
                    continue
                score, method = self._choose_score(
                    score,
                    method,
                    0.92,
                    target_method,
                )
                notes.append(
                    'Product code match: %s found in %s.' % (code, target_method)
                )
        return score, method, notes

    def _score_partial_code_match(self, line, move_line, partner, score, method, notes):
        product = move_line.product_id
        candidate_codes = self._candidate_codes(product, move_line, partner)
        for code in self._line_codes(line):
            exact_token = self._find_code_exact_token(code, candidate_codes)
            if exact_token:
                score, method = self._choose_score(
                    score,
                    method,
                    1.0,
                    'candidate_code_token_exact',
                )
                notes.append(
                    'Exact code-token match: %s equals candidate token %s.'
                    % (code, exact_token)
                )
                continue

            prefix_token = self._find_code_prefix_token(code, candidate_codes)
            if prefix_token:
                score, method = self._choose_score(
                    score,
                    method,
                    0.92,
                    'candidate_code_prefix',
                )
                notes.append(
                    'Code prefix/substring match: %s found in candidate token %s.'
                    % (code, prefix_token)
                )
                continue

            for target, target_method in self._partial_code_targets(product, move_line, partner):
                if not self._code_in_text(code, target):
                    continue
                score, method = self._choose_score(
                    score,
                    method,
                    0.92,
                    target_method,
                )
                notes.append(
                    'Partial code match: %s found in %s.'
                    % (code, target_method)
                )
        return score, method, notes

    def _partial_code_targets(self, product, move_line, partner):
        targets = []
        for value, method in (
            (getattr(product, 'default_code', False), 'default_code_partial'),
            (getattr(product, 'barcode', False), 'barcode_partial'),
            (getattr(product, 'display_name', False), 'product_display_name_partial'),
            (getattr(product, 'name', False), 'product_name_partial'),
            (getattr(move_line, 'name', False), 'move_line_name_partial'),
        ):
            if value:
                targets.append((value, method))
        for seller in self._product_sellers(product, partner):
            for field_name, method in (
                ('product_code', 'supplier_code_partial'),
                ('product_name', 'supplier_name_partial'),
                ('name', 'supplierinfo_name_partial'),
            ):
                value = getattr(seller, field_name, False)
                if value:
                    targets.append((value, method))
        return targets

    def _product_code_targets(self, product, partner):
        targets = []
        for value, method in (
            (getattr(product, 'default_code', False), 'default_code_partial'),
            (getattr(product, 'barcode', False), 'barcode_partial'),
            (getattr(product, 'display_name', False), 'product_display_name_partial'),
            (getattr(product, 'name', False), 'product_name_partial'),
        ):
            if value:
                targets.append((value, method))
        for seller in self._product_sellers(product, partner):
            for field_name, method in (
                ('product_code', 'supplier_code_partial'),
                ('product_name', 'supplier_name_partial'),
                ('name', 'supplierinfo_name_partial'),
            ):
                value = getattr(seller, field_name, False)
                if value:
                    targets.append((value, method))
        return targets

    def _candidate_codes(self, product, move_line, partner):
        codes = []
        for target, _method in self._partial_code_targets(product, move_line, partner):
            codes.extend(self._extract_codes_from_text(target))
        return self._unique_normalized_codes(codes)

    def _product_candidate_codes(self, product, partner):
        codes = []
        for target, _method in self._product_code_targets(product, partner):
            codes.extend(self._extract_codes_from_text(target))
        return self._unique_normalized_codes(codes)

    def _find_code_exact_token(self, code, candidate_codes):
        for candidate_code in candidate_codes:
            if self._code_equals(code, candidate_code):
                return candidate_code
        return False

    def _find_code_prefix_token(self, code, candidate_codes):
        code_normalized = self._normalize_code(code)
        if len(code_normalized) < 4:
            return False
        for candidate_code in candidate_codes:
            candidate_normalized = self._normalize_code(candidate_code)
            if not candidate_normalized:
                continue
            if candidate_normalized.startswith(code_normalized):
                return candidate_code
            if code_normalized in candidate_normalized:
                return candidate_code
        return False

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
            ('price_unit', self._recognized_price_unit(line), getattr(move_line, 'price_unit', False)),
            ('amount_untaxed', self._recognized_subtotal(line), getattr(move_line, 'price_subtotal', False)),
            ('amount_total', self._recognized_total(line), getattr(move_line, 'price_total', False)),
        ]
        currency = getattr(line, 'currency_id', False)
        for label, recognized_value, move_value in comparisons:
            recognized_value = self._to_float(recognized_value)
            move_value = self._to_float(move_value)
            if not self._is_number(recognized_value) or not self._is_number(move_value):
                continue
            if self._amounts_close(recognized_value, move_value, currency=currency):
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

    def _apply_partial_numeric_fallback(self, line, move_line, score, method, notes):
        checks = self._get_partial_numeric_checks(line, move_line)
        numeric_strong = False

        if checks['quantity_match'] and checks['price_match'] and checks['subtotal_match']:
            numeric_strong = True
            fallback_score = 0.93
            fallback_method = 'quantity_price_subtotal'
            notes.append('Numeric fallback: quantity, price_unit, and subtotal match.')
        elif checks['quantity_match'] and checks['price_match']:
            numeric_strong = True
            fallback_score = 0.91
            fallback_method = 'quantity_price'
            notes.append('Numeric fallback: quantity and price_unit match.')
        elif checks['quantity_match'] and checks['amount_match_count']:
            fallback_score = 0.74
            fallback_method = 'quantity_amount'
            notes.append(
                'Numeric fallback: quantity and %s match.'
                % checks['best_amount_label']
            )
        elif checks['quantity_match']:
            fallback_score = 0.72
            fallback_method = 'quantity_only'
            notes.append('Numeric fallback: quantity matches, amount/price did not match.')
        elif checks['amount_match_count']:
            fallback_score = 0.66
            fallback_method = checks['best_amount_method']
            notes.append(
                'Numeric fallback: %s matches but quantity did not match.'
                % checks['best_amount_label']
            )
        else:
            return score, method, notes, numeric_strong

        if fallback_score > (score or 0.0):
            score = fallback_score
            method = fallback_method
        return score, method, notes, numeric_strong

    def _get_partial_numeric_checks(self, line, move_line):
        quantity = self._to_float(getattr(line, 'quantity', False))
        move_quantity = self._to_float(getattr(move_line, 'quantity', False))
        currency = getattr(line, 'currency_id', False)
        quantity_match = (
            self._is_number(quantity)
            and self._is_number(move_quantity)
            and self._numbers_close(quantity, move_quantity, tolerance=0.0001)
        )

        amount_checks = []
        matched_by_label = {}
        for label, method, recognized_value, move_value in (
            (
                'price_unit',
                'quantity_price',
                self._recognized_price_unit(line),
                getattr(move_line, 'price_unit', False),
            ),
            (
                'subtotal',
                'quantity_subtotal',
                self._recognized_subtotal(line),
                getattr(move_line, 'price_subtotal', False),
            ),
            (
                'total',
                'quantity_total',
                self._recognized_total(line),
                getattr(move_line, 'price_total', False),
            ),
        ):
            recognized_value = self._to_float(recognized_value)
            move_value = self._to_float(move_value)
            matched = (
                self._is_number(recognized_value)
                and self._is_number(move_value)
                and self._amounts_close(recognized_value, move_value, currency=currency)
            )
            amount_checks.append((label, method, matched))
            matched_by_label[label] = matched

        matched_amounts = [
            (label, method)
            for label, method, matched in amount_checks
            if matched
        ]
        best_amount_label, best_amount_method = (
            matched_amounts[0] if matched_amounts else (False, False)
        )
        return {
            'quantity_match': quantity_match,
            'amount_match_count': len(matched_amounts),
            'best_amount_label': best_amount_label,
            'best_amount_method': best_amount_method,
            'price_match': matched_by_label.get('price_unit', False),
            'subtotal_match': matched_by_label.get('subtotal', False),
            'total_match': matched_by_label.get('total', False),
        }

    def _recognized_price_unit(self, line):
        return self._first_number(
            getattr(line, 'price_unit', False),
            getattr(line, 'price_unit_without_tax', False),
        )

    def _recognized_subtotal(self, line):
        return self._first_number(
            getattr(line, 'amount_untaxed', False),
            getattr(line, 'line_subtotal_without_tax', False),
        )

    def _recognized_total(self, line):
        return self._first_number(
            getattr(line, 'amount_total', False),
            getattr(line, 'line_total_with_tax', False),
        )

    def _write_match_result(
        self,
        line,
        candidates,
        include_move_lines=False,
        diagnostics=False,
        allow_best_gap_match=False,
    ):
        if include_move_lines:
            unique_numeric_match = self._promote_unique_partial_numeric_match(candidates)
        else:
            unique_numeric_match = False

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
        }
        winner_candidate = False

        if unique_numeric_match:
            winner = unique_numeric_match
            winner_candidate = winner
            values.update({
                'match_status': 'matched',
                'matched_product_id': winner['product'].id,
                'match_score': winner['score'],
                'match_method': winner['method'],
            })
            if winner.get('move_line'):
                values['move_line_id'] = winner['move_line'].id
        elif (
            len(visible_candidates) == 1
            and visible_candidates[0]['score'] >= self.MATCHED_THRESHOLD
        ):
            winner = visible_candidates[0]
            winner_candidate = winner
            values.update({
                'match_status': 'matched',
                'matched_product_id': winner['product'].id,
            })
            if include_move_lines and winner.get('move_line'):
                values['move_line_id'] = winner['move_line'].id
        elif self._can_match_best_gap(visible_candidates, allow_best_gap_match):
            winner = visible_candidates[0]
            winner_candidate = winner
            values.update({
                'match_status': 'matched',
                'matched_product_id': winner['product'].id,
                'match_score': winner['score'],
                'match_method': winner['method'],
            })
            winner.setdefault('notes', []).append(
                'Best candidate selected because score gap is at least %.2f.'
                % self.BEST_GAP_MATCH_THRESHOLD
            )
        elif visible_candidates:
            values['match_status'] = 'ambiguous'
        else:
            values['match_status'] = 'not_found'
        values['match_note'] = self._build_match_note(
            candidates,
            visible_candidates,
            diagnostics=diagnostics,
            status=values['match_status'],
        )
        values['match_summary'] = self._build_match_summary(
            line,
            values,
            winner_candidate,
            best,
            visible_candidates,
        )
        line.write(values)

    def _can_match_best_gap(self, visible_candidates, allow_best_gap_match):
        if not allow_best_gap_match or not visible_candidates:
            return False
        best = visible_candidates[0]
        if best['score'] < self.MATCHED_THRESHOLD:
            return False
        if len(visible_candidates) == 1:
            return True
        second = visible_candidates[1]
        return (best['score'] - second['score']) >= self.BEST_GAP_MATCH_THRESHOLD

    def _promote_unique_partial_numeric_match(self, candidates):
        strong_candidates = [
            candidate
            for candidate in candidates
            if candidate.get('numeric_strong') and candidate.get('move_line')
        ]
        if len(strong_candidates) != 1:
            return False

        winner = strong_candidates[0]
        winner['score'] = max(winner.get('score') or 0.0, 0.95)
        if winner.get('method'):
            if not winner['method'].endswith('_unique'):
                winner['method'] = '%s_unique' % winner['method']
        else:
            winner['method'] = 'quantity_price_unique'
        winner.setdefault('notes', []).append(
            'Unique partial bill numeric match among checked invoice lines.'
        )
        return winner

    def _write_line_error(self, line, error):
        line.write({
            'match_status': 'error',
            'match_score': 0.0,
            'match_method': False,
            'matched_product_id': False,
            'move_line_id': False,
            'candidate_product_ids': [(6, 0, [])],
            'candidate_move_line_ids': [(6, 0, [])],
            'match_summary': _('Error: product matching failed.'),
            'match_note': _('Matching error: %s') % error,
        })

    def _mark_job_lines_error(self, job, message):
        for line in job.line_ids:
            line.write({
                'match_status': 'error',
                'match_summary': _('Error: product matching failed.'),
                'match_note': message,
            })

    def _build_match_summary(
        self,
        line,
        values,
        winner_candidate,
        best_candidate,
        visible_candidates,
    ):
        status = values.get('match_status')
        score = values.get('match_score') or 0.0
        method = values.get('match_method') or 'unknown'
        code = self._summary_line_code(line)

        if status == 'matched':
            product = winner_candidate.get('product') if winner_candidate else False
            product_name = self._short_product_name(product)
            return _('Matched: %(product)s by %(method)s %(code)s, score %(score).2f') % {
                'product': product_name,
                'method': method,
                'code': code,
                'score': score,
            }
        if status == 'ambiguous':
            product = best_candidate.get('product') if best_candidate else False
            product_name = self._short_product_name(product)
            return _('Ambiguous: %(count)s candidates, best %(product)s, score %(score).2f') % {
                'count': len(visible_candidates),
                'product': product_name,
                'score': score,
            }
        if status == 'not_found':
            return _('Not found: no product matched %(code)s') % {'code': code or _('recognized line')}
        if status == 'error':
            return _('Error: product matching failed.')
        if status == 'manual':
            return _('Manual: selected by user')
        return _('Draft: not matched yet')

    def _summary_line_code(self, line):
        profile = self._line_code_profile(line)
        if profile['primary_codes']:
            return profile['primary_codes'][0]
        code = getattr(line, 'supplier_product_code', False)
        if code:
            return code
        return getattr(line, 'supplier_product_name', False) or getattr(line, 'description', False) or ''

    def _short_product_name(self, product, limit=90):
        if not product:
            return _('no product')
        name = getattr(product, 'display_name', False) or getattr(product, 'name', False) or str(product.id)
        if len(name) <= limit:
            return name
        return '%s...' % name[:limit - 3]

    def _build_match_note(
        self,
        candidates,
        visible_candidates,
        diagnostics=False,
        status=False,
    ):
        lines = list(diagnostics or [])
        if status == 'not_found':
            if candidates:
                lines.append(
                    'Status reason: best score is below %.2f candidate threshold.'
                    % self.CANDIDATE_THRESHOLD
                )
            else:
                lines.append('Status reason: no scored candidates were found.')
        elif status == 'ambiguous':
            lines.append('Status reason: several candidates require manual review.')
        elif status == 'matched':
            lines.append('Status reason: one confident candidate was selected.')

        if not candidates:
            lines.append(_('No product candidates found.'))
            return '\n'.join(lines)

        lines.append('Top candidates:')
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
        if status == 'ambiguous' and visible_candidates and len(visible_candidates) > 1:
            lines.insert(0, _('Several product candidates require review.'))
        return '\n'.join(lines)

    def _build_partial_diagnostics(
        self,
        line,
        job,
        line_source,
        candidates,
    ):
        invoice_lines = line_source['invoice_lines']
        invoice_product_lines = line_source['invoice_product_lines']
        line_ids = line_source['line_ids']
        line_product_lines = line_source['line_product_lines']
        move_lines = line_source['product_lines']
        extracted_codes = self._line_codes(line)
        diagnostics = [
            'Partial bill matching diagnostics:',
            'Job mode: %s.' % job.mode,
            'Move ID: %s.' % (job.move_id.id if job.move_id else 'none'),
            'Extracted supplier/internal codes: %s.' % (
                ', '.join(extracted_codes) if extracted_codes else 'none'
            ),
            'invoice_line_ids total: %s.' % len(invoice_lines),
            'invoice_line_ids product lines: %s.' % len(invoice_product_lines),
            'line_ids total: %s.' % len(line_ids),
            'line_ids product lines: %s.' % len(line_product_lines),
            'Line source used: %s.' % line_source['source'],
            self._format_line_source_reason(line_source),
            'Recognized values: supplier_product_code=%s; supplier_product_name=%s; quantity=%s; price_unit=%s; amount_untaxed=%s.'
            % (
                getattr(line, 'supplier_product_code', False) or 'none',
                getattr(line, 'supplier_product_name', False) or 'none',
                getattr(line, 'quantity', False) or 'none',
                self._recognized_price_unit(line) or 'none',
                self._recognized_subtotal(line) or 'none',
            ),
            'Methods tried: supplier_product_code, supplier_product_name, default_code, barcode, product name, move_line.name, quantity, price, subtotal, total.',
            'Fields compared: quantity, price_unit, amount_untaxed/subtotal, amount_total, product/default/barcode/display names, supplierinfo code/name.',
        ]
        if not move_lines:
            diagnostics.append(
                'No product invoice lines are available on the vendor bill after checking invoice_line_ids and line_ids fallback.'
            )
            return diagnostics

        best = max(candidates, key=lambda candidate: candidate.get('score') or 0.0, default=False)
        if best and best.get('score'):
            product = best.get('product')
            product_name = (
                getattr(product, 'display_name', False)
                or getattr(product, 'name', False)
                or getattr(product, 'id', False)
            )
            move_line = best.get('move_line')
            diagnostics.append(
                'Best score before threshold: %.2f by %s%s for %s.'
                % (
                    best.get('score') or 0.0,
                    best.get('method') or 'unknown',
                    ' on move_line_id=%s' % move_line.id if move_line else '',
                    product_name,
                )
            )
        else:
            diagnostics.append('Best score before threshold: 0.00.')

        diagnostics.append('Checked invoice line details:')
        for candidate in candidates:
            diagnostics.extend(self._format_partial_candidate_diagnostics(candidate))
        return diagnostics

    def _format_line_source_reason(self, line_source):
        if line_source['source'] == 'invoice_line_ids':
            return (
                'Used invoice_line_ids with %s product lines.'
                % len(line_source['invoice_product_lines'])
            )
        if line_source['source'] == 'line_ids fallback':
            return (
                'invoice_line_ids returned %s product lines, used line_ids fallback with %s product lines.'
                % (
                    len(line_source['invoice_product_lines']),
                    len(line_source['line_product_lines']),
                )
            )
        return 'No move line source was available.'

    def _format_partial_candidate_diagnostics(self, candidate):
        move_line = candidate.get('move_line')
        product = candidate.get('product')
        if not move_line or not product:
            return ['- Empty candidate.']

        score = candidate.get('score') or 0.0
        if score >= self.MATCHED_THRESHOLD:
            decision = 'accepted/confident'
        elif score >= self.CANDIDATE_THRESHOLD:
            decision = 'candidate/manual review'
        else:
            decision = 'rejected below threshold'

        values = [
            '- move_line_id=%s; product_id=%s; score=%.2f; method=%s; decision=%s.'
            % (
                move_line.id,
                product.id,
                score,
                candidate.get('method') or 'none',
                decision,
            ),
            '  product_display_name=%s' % (
                getattr(product, 'display_name', False)
                or getattr(product, 'name', False)
                or ''
            ),
            '  product_default_code=%s; barcode=%s'
            % (
                getattr(product, 'default_code', False) or '',
                getattr(product, 'barcode', False) or '',
            ),
            '  move_line.name=%s' % (getattr(move_line, 'name', False) or ''),
            '  display_type=%s; account_id=%s; account_type=%s'
            % (
                getattr(move_line, 'display_type', False) or '',
                getattr(getattr(move_line, 'account_id', False), 'id', False) or '',
                getattr(getattr(move_line, 'account_id', False), 'account_type', False) or '',
            ),
            '  quantity=%s; price_unit=%s; price_subtotal=%s'
            % (
                getattr(move_line, 'quantity', False),
                getattr(move_line, 'price_unit', False),
                getattr(move_line, 'price_subtotal', False),
            ),
            '  extracted candidate tokens/codes=%s'
            % (
                ', '.join(candidate.get('candidate_codes') or [])
                if candidate.get('candidate_codes')
                else 'none'
            ),
            '  primary_codes=%s; secondary_tokens=%s; ignored_low_value_tokens=%s'
            % (
                ', '.join(candidate.get('primary_codes') or []) or 'none',
                ', '.join(candidate.get('secondary_codes') or []) or 'none',
                ', '.join(candidate.get('ignored_low_value_tokens') or []) or 'none',
            ),
            '  supplierinfo_signal=%s; base_score=%.2f; similarity_score=%.2f'
            % (
                candidate.get('supplierinfo_signal') or 'none',
                candidate.get('base_score') or 0.0,
                candidate.get('similarity_score') or 0.0,
            ),
        ]
        if candidate.get('boosts'):
            values.append('  boosts: %s' % '; '.join(candidate['boosts']))
        if candidate.get('penalties'):
            values.append('  penalties: %s' % '; '.join(candidate['penalties']))
        if candidate.get('notes'):
            values.append('  why: %s' % '; '.join(candidate['notes']))
        return values

    def _build_product_diagnostics(
        self,
        line,
        job,
        products,
        candidates,
        mode_label='Product',
    ):
        partner = self._get_job_partner(job)
        profile = self._line_code_profile(line)
        supplierinfo_candidates = self._find_supplierinfos_by_codes(
            profile['primary_codes'],
            partner,
        )
        supplierinfo_exact = [
            seller
            for seller in supplierinfo_candidates
            if any(
                self._code_equals(code, getattr(seller, 'product_code', False))
                for code in profile['primary_codes']
            )
        ]
        diagnostics = [
            '%s matching diagnostics:' % mode_label,
            'Job mode: %s.' % job.mode,
            'Move ID: %s.' % (job.move_id.id if getattr(job, 'move_id', False) else 'none'),
            'Vendor partner used: %s.' % (partner.id if partner else 'none'),
            'Primary codes: %s.' % (
                ', '.join(profile['primary_codes']) if profile['primary_codes'] else 'none'
            ),
            'Secondary tokens: %s.' % (
                ', '.join(profile['secondary_codes']) if profile['secondary_codes'] else 'none'
            ),
            'Ignored low-value tokens: %s.' % (
                ', '.join(profile['ignored_low_value_tokens'])
                if profile['ignored_low_value_tokens']
                else 'none'
            ),
            'Supplierinfo candidates count: %s.' % len(supplierinfo_candidates),
            'Supplierinfo exact matches: %s.' % len(supplierinfo_exact),
            'Product candidates found: %s.' % len(products),
            'Recognized values: supplier_product_code=%s; supplier_product_name=%s; quantity=%s; price_unit=%s; amount_untaxed=%s.'
            % (
                getattr(line, 'supplier_product_code', False) or 'none',
                getattr(line, 'supplier_product_name', False) or 'none',
                getattr(line, 'quantity', False) or 'none',
                self._recognized_price_unit(line) or 'none',
                self._recognized_subtotal(line) or 'none',
            ),
            'Methods tried: supplierinfo code/name, default_code, barcode, primary code-token, meaningful secondary token boost, brand/dimension boosts, internal product penalties, fuzzy/token name similarity.',
        ]
        if not products:
            diagnostics.append('No product.product candidates were found.')
            return diagnostics

        best = max(candidates, key=lambda candidate: candidate.get('score') or 0.0, default=False)
        if best and best.get('score'):
            product = best.get('product')
            product_name = (
                getattr(product, 'display_name', False)
                or getattr(product, 'name', False)
                or getattr(product, 'id', False)
            )
            diagnostics.append(
                'Best score before threshold: %.2f by %s for %s.'
                % (
                    best.get('score') or 0.0,
                    best.get('method') or 'unknown',
                    product_name,
                )
            )
        else:
            diagnostics.append('Best score before threshold: 0.00.')

        diagnostics.append('Checked product details:')
        for candidate in candidates:
            diagnostics.extend(self._format_product_candidate_diagnostics(candidate))
        return diagnostics

    def _format_product_candidate_diagnostics(self, candidate):
        product = candidate.get('product')
        if not product:
            return ['- Empty product candidate.']

        score = candidate.get('score') or 0.0
        if score >= self.MATCHED_THRESHOLD:
            decision = 'accepted/confident'
        elif score >= self.CANDIDATE_THRESHOLD:
            decision = 'candidate/manual review'
        else:
            decision = 'rejected below threshold'

        values = [
            '- product_id=%s; score=%.2f; method=%s; decision=%s.'
            % (
                product.id,
                score,
                candidate.get('method') or 'none',
                decision,
            ),
            '  product_display_name=%s' % (
                getattr(product, 'display_name', False)
                or getattr(product, 'name', False)
                or ''
            ),
            '  product_default_code=%s; barcode=%s'
            % (
                getattr(product, 'default_code', False) or '',
                getattr(product, 'barcode', False) or '',
            ),
            '  extracted candidate tokens/codes=%s'
            % (
                ', '.join(candidate.get('candidate_codes') or [])
                if candidate.get('candidate_codes')
                else 'none'
            ),
        ]
        if candidate.get('notes'):
            values.append('  why: %s' % '; '.join(candidate['notes']))
        return values

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

    def _find_supplierinfos_by_codes(self, codes, partner):
        sellers = []
        for code in codes:
            if self._is_low_value_code(code):
                continue
            for seller in self._search_supplierinfos_exact(partner, product_code=code):
                self._append_unique(sellers, seller)
            for seller in self._search_supplierinfos_code_like(code, partner):
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
            '|',
            ('default_code', 'ilike', normalized_code),
            ('barcode', 'ilike', normalized_code),
            ('name', 'ilike', code),
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
        explicit_code = getattr(line, 'supplier_product_code', False)
        if explicit_code:
            codes.append(explicit_code)
        for value in (
            getattr(line, 'supplier_product_name', False),
            getattr(line, 'description', False),
            getattr(line, 'note', False),
            getattr(line, 'source_columns', False),
        ):
            if not value:
                continue
            codes.extend(self._extract_codes_from_text(value))
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

    def _full_bill_search_terms(self, line, profile):
        terms = list(profile['primary_codes'])
        for value in self._line_name_terms(line):
            for token in self._meaningful_tokens(value):
                if token in self.GENERIC_NAME_TOKENS:
                    continue
                if self._is_low_value_code(token):
                    continue
                terms.append(token)
        if not terms:
            terms.extend(profile['secondary_codes'])
        return list(dict.fromkeys(term for term in terms if term))

    def _line_code_profile(self, line):
        primary_codes = []
        ignored_low_value_tokens = []

        explicit_code = getattr(line, 'supplier_product_code', False)
        if explicit_code:
            for code in self._expand_code_tokens([explicit_code]):
                self._add_profile_code(code, primary_codes, ignored_low_value_tokens)

        if not primary_codes:
            for value in (
                getattr(line, 'supplier_product_name', False),
                getattr(line, 'description', False),
                getattr(line, 'note', False),
                getattr(line, 'source_columns', False),
            ):
                leading_codes = self._extract_leading_codes(value)
                if not leading_codes:
                    continue
                for code in self._expand_code_tokens(leading_codes):
                    self._add_profile_code(code, primary_codes, ignored_low_value_tokens)
                if primary_codes:
                    break

        primary_codes = self._unique_normalized_codes(primary_codes)
        primary_normalized = {
            self._normalize_code(code)
            for code in primary_codes
            if self._normalize_code(code)
        }
        secondary_codes = []
        for code in self._line_codes(line):
            normalized = self._normalize_code(code)
            if not normalized or normalized in primary_normalized:
                continue
            if self._is_low_value_code(code):
                ignored_low_value_tokens.append(code)
                continue
            secondary_codes.append(code)

        return {
            'primary_codes': primary_codes,
            'secondary_codes': self._unique_normalized_codes(secondary_codes),
            'ignored_low_value_tokens': self._unique_normalized_codes(ignored_low_value_tokens),
        }

    def _add_profile_code(self, code, codes, ignored_low_value_tokens):
        if not code:
            return
        if self._is_low_value_code(code):
            ignored_low_value_tokens.append(code)
            return
        codes.append(code)

    def _is_low_value_code(self, code):
        normalized = self._normalize_code(code)
        if not normalized:
            return True
        if len(normalized) < 4:
            return True
        if normalized.isdigit():
            return True
        if normalized in self.LOW_VALUE_CODE_TOKENS:
            return True
        for pattern in self.LOW_VALUE_CODE_PATTERNS:
            if pattern.match(normalized):
                return True
        text_normalized = self._normalize_text(code)
        return text_normalized in self.GENERIC_NAME_TOKENS

    def _meaningful_tokens(self, value):
        tokens = set()
        for token in self._normalize_text(value).split():
            if len(token) < 3:
                continue
            if token.isdigit():
                continue
            if token in self.GENERIC_NAME_TOKENS:
                continue
            if self._is_low_value_code(token):
                continue
            tokens.add(token)
        return tokens

    def _ocr_line_text(self, line):
        return ' '.join(
            str(value)
            for value in (
                getattr(line, 'supplier_product_code', False),
                getattr(line, 'supplier_product_name', False),
                getattr(line, 'description', False),
                getattr(line, 'note', False),
                getattr(line, 'source_columns', False),
            )
            if value
        )

    def _product_text(self, product):
        return ' '.join(
            str(value)
            for value in (
                getattr(product, 'default_code', False),
                getattr(product, 'barcode', False),
                getattr(product, 'display_name', False),
                getattr(product, 'name', False),
            )
            if value
        )

    def _extract_dimension_numbers(self, value):
        value = str(value or '').lower()
        numbers = []
        dimension_blocks = re.findall(
            r'\d+(?:[,.]\d+)?(?:\s*[xх×]\s*\d+(?:[,.]\d+)?){1,3}\s*(?:мм|mm)?',
            value,
            flags=re.U,
        )
        for block in dimension_blocks:
            numbers.extend(self._numbers_from_text(block))
        numbers.extend(
            self._numbers_from_text(match)
            for match in re.findall(r'\d+(?:[,.]\d+)?\s*(?:мм|mm)', value, flags=re.U)
        )
        flattened = []
        for number in numbers:
            if isinstance(number, list):
                flattened.extend(number)
            else:
                flattened.append(number)
        return list(dict.fromkeys(
            round(number, 2)
            for number in flattened
            if self._is_number(number) and number > 0
        ))

    def _numbers_from_text(self, value):
        result = []
        for match in re.findall(r'\d+(?:[,.]\d+)?', str(value or '')):
            try:
                result.append(float(match.replace(',', '.')))
            except ValueError:
                continue
        return result

    def _extract_bracket_codes(self, value):
        return [
            match.strip()
            for match in re.findall(r'\[([^\]]+)\]', value or '')
            if match.strip()
        ]

    def _extract_codes_from_text(self, value):
        value = value or ''
        codes = []
        codes.extend(self._extract_bracket_codes(value))
        codes.extend(self._extract_leading_codes(value))
        codes.extend(self._extract_embedded_codes(value))
        return self._expand_code_tokens(codes)

    def _extract_leading_codes(self, value):
        value = (value or '').strip()
        if not value:
            return []
        bracket_match = re.match(r'^\[([^\]]+)\]', value)
        if bracket_match:
            return [bracket_match.group(1).strip()]
        match = re.match(r'^([^\W_][\w./-]{1,40})', value, flags=re.U)
        if not match:
            return []
        code = match.group(1).strip(':-.,;')
        if self._looks_like_code(code):
            return [code]
        return []

    def _extract_embedded_codes(self, value):
        codes = []
        patterns = (
            r'\b[^\W_\d]{1,12}\d[\w/-]{1,30}\b',
            r'\b\d[\w/-]*[-/]\d[\w/-]*\b',
        )
        for pattern in patterns:
            for match in re.findall(pattern, value or '', flags=re.U):
                code = str(match).strip(':-.,;')
                if self._looks_like_code(code):
                    codes.append(code)
        return codes

    def _expand_code_tokens(self, codes):
        expanded = []
        for code in codes:
            if not code:
                continue
            expanded.append(code)
            for part in re.split(r'[-/\s.]+', code):
                part = part.strip()
                if part and self._looks_like_code(part):
                    expanded.append(part)
        return expanded

    def _looks_like_code(self, value):
        normalized = self._normalize_code(value)
        if len(normalized) < 3:
            return False
        has_digit = any(char.isdigit() for char in value)
        if not has_digit:
            return False
        has_alpha = any(char.isalpha() for char in value)
        has_separator = any(char in value for char in ('-', '/', '.'))
        return has_alpha or has_separator or len(normalized) >= 4

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

    def _code_in_text(self, code, text):
        code_normalized = self._normalize_code(code)
        text_normalized = self._normalize_code(text)
        if not code_normalized or not text_normalized:
            return False
        if len(code_normalized) < 4:
            return False
        return code_normalized in text_normalized

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

    def _is_number(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _numbers_close(self, first, second, tolerance=0.01):
        if not self._is_number(first) or not self._is_number(second):
            return False
        return abs(first - second) <= tolerance

    def _amounts_close(self, first, second, currency=False):
        if not self._is_number(first) or not self._is_number(second):
            return False
        rounding = getattr(currency, 'rounding', False) if currency else False
        tolerance = max(rounding or 0.01, 0.01)
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
