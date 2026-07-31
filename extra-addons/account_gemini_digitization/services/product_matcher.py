import difflib
import logging
import re

from odoo import _

from .supplier_code import SupplierArticleNormalizer, TechnicalCodeNormalizer


_logger = logging.getLogger(__name__)


class ProductMatcher:
    MATCHED_THRESHOLD = 0.90
    CANDIDATE_THRESHOLD = 0.70
    BEST_GAP_MATCH_THRESHOLD = 0.05
    PARTIAL_SAFE_ASSIGNMENT_THRESHOLD = 0.80
    PARTIAL_AUTO_ASSIGNMENT_THRESHOLD = 0.90
    PARTIAL_ASSIGNMENT_TIE_TOLERANCE = 0.02
    PARTIAL_MAX_ASSIGNMENT_LINES = 8
    PARTIAL_MAX_ASSIGNMENT_PAIRS = 80
    LOCKED_EXACT_METHODS = {
        'supplierinfo_code_exact',
        'supplierinfo_code_separatorless_unique',
        'default_code_exact',
        'barcode_exact',
        'technical_code_exact',
        'dotted_technical_code_exact',
        'historical_technical_code_exact',
        'historical_dotted_technical_code_exact',
    }
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
    TECHNICAL_COLOR_TOKENS = {
        'black',
        'bk',
        'rd',
        'red',
        'white',
    }
    TECHNICAL_MODEL_TOKEN_ALLOWLIST = {
        'cm4',
        'i0',
        'io',
        'm12',
        'pm03d',
        'rpi',
    }
    GENERIC_NAME_TOKENS = {
        'aluminium',
        'box',
        'cover',
        'plastic',
        '\u043a\u043b\u0435\u043c\u0430',
        '\u043a\u043b\u0435\u043c\u043c\u0430',
        '\u043a\u043e\u0440\u043f\u0443\u0441',
        '\u043a\u043e\u0440\u043e\u0431\u043a\u0430',
        '\u043a\u0440\u0438\u0448\u043a\u0430',
        '\u043a\u0440\u044b\u0448\u043a\u0430',
        '\u0431\u043e\u043a\u0441',
        '\u0433\u0435\u0440\u043c\u0435\u0442\u0438\u0447\u043d\u0438\u0439',
        '\u043a\u0440\u0456\u043f\u043b\u0435\u043d\u043d\u044f',
        '\u043f\u043b\u0430\u0441\u0442\u0438\u043a',
        '\u0443\u043d\u0456\u0432\u0435\u0440\u0441\u0430\u043b\u044c\u043d\u0438\u0439',
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
        'adapter',
        'black',
        'board',
        'cable',
        'camera',
        'analog',
        'module',
        'fpv',
        'output',
        'plate',
        'product',
        'pro',
        'service',
        'ac',
        'dc',
        'to',
        'with',
        'white',
        'адаптер',
        'кабель',
        'камера',
        'клема',
        'модуль',
        'напруга',
        'напруги',
        'напряжение',
        'напряжения',
        'перетворювач',
        'плата',
        'послуга',
        'товар',
        'шт',
    }
    NAME_TOKEN_CANONICALS = {
        'camera': 'camera',
        'kamepa': 'camera',
        'камера': 'camera',
        'підвищувальний': 'boost_converter',
        'підвищуючий': 'boost_converter',
        'повышающий': 'boost_converter',
        'понижувальний': 'buck_converter',
        'понижуючий': 'buck_converter',
        'понижающий': 'buck_converter',
        '\u043a\u043b\u0435\u043c\u043c\u0430': '\u043a\u043b\u0435\u043c\u0430',
        '\u043a\u0440\u044b\u0448\u043a\u0430': '\u043a\u0440\u0438\u0448\u043a\u0430',
        '\u0447\u0435\u0440\u0432\u043e\u043d\u0430': 'color_red',
        '\u0447\u0435\u0440\u0432\u043e\u043d\u0438\u0439': 'color_red',
        '\u043a\u0440\u0430\u0441\u043d\u0430\u044f': 'color_red',
        '\u043a\u0440\u0430\u0441\u043d\u044b\u0439': 'color_red',
        'red': 'color_red',
        '\u0447\u043e\u0440\u043d\u0430': 'color_black',
        '\u0447\u043e\u0440\u043d\u0438\u0439': 'color_black',
        '\u0447\u0435\u0440\u043d\u0430\u044f': 'color_black',
        '\u0447\u0435\u0440\u043d\u044b\u0439': 'color_black',
        'black': 'color_black',
    }
    NAME_COLOR_TOKENS = {'color_black', 'color_red'}
    NAME_GENERIC_WEIGHT = 0.08
    NAME_CHARACTERISTIC_WEIGHT = 0.26
    NAME_MEANINGFUL_WEIGHT_THRESHOLD = 0.70
    INTERNAL_PRODUCT_TERMS = (
        'модифікований',
        'модифицированный',
        'під блок',
        'под блок',
        'наземний робот',
        'наземный робот',
        'центрального',
    )
    DELIVERY_SERVICE_TERMS = (
        'доставка',
        'послуга доставки',
        'нова пошта',
        'новою поштою',
        'логістика',
        'перевезення',
        'shipping',
        'delivery',
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

    CYRILLIC_LATIN_LOOKALIKES.update(str.maketrans({
        '\u0406': 'I', '\u0456': 'i',
        '\u0407': 'I', '\u0457': 'i',
        '\u0404': 'E', '\u0454': 'e',
        '\u0410': 'A', '\u0430': 'a',
        '\u0412': 'B', '\u0432': 'b',
        '\u0415': 'E', '\u0435': 'e',
        '\u041a': 'K', '\u043a': 'k',
        '\u041c': 'M', '\u043c': 'm',
        '\u041d': 'H', '\u043d': 'h',
        '\u041e': 'O', '\u043e': 'o',
        '\u0420': 'P', '\u0440': 'p',
        '\u0421': 'C', '\u0441': 'c',
        '\u0422': 'T', '\u0442': 't',
        '\u0425': 'X', '\u0445': 'x',
    }))
    DASH_TRANSLATION = str.maketrans({
        '\u2010': '-',
        '\u2011': '-',
        '\u2012': '-',
        '\u2013': '-',
        '\u2014': '-',
        '\u2212': '-',
    })

    def __init__(self, env):
        self.env = env
        self._supplier_separatorless_cache = {}

    def match_job(self, job):
        job.ensure_one()
        self._repair_job_partner(job)
        if job.mode == 'partial_bill':
            self._match_partial_bill(job)
        elif job.mode == 'partial_purchase':
            self._match_partial_purchase(job)
        elif job.mode == 'full_bill':
            self._match_full_bill(job)
        elif job.mode == 'full_purchase':
            self._match_full_purchase(job)
        else:
            self._mark_job_lines_error(job, _('Unknown Gemini matching mode: %s') % job.mode)
        return True

    def _match_partial_bill(self, job):
        line_source = self._get_partial_bill_line_source(job)
        partner = self._get_job_partner(job)
        self._assign_partial_bill_move_lines(job, line_source, partner)

    def _match_partial_purchase(self, job):
        line_source = self._get_partial_purchase_line_source(job)
        partner = self._get_job_partner(job)
        self._assign_partial_bill_move_lines(job, line_source, partner)

    def sync_partial_bill_move_lines(self, job):
        """Fill concrete vendor bill lines for already matched partial OCR rows."""
        job.ensure_one()
        if job.mode != 'partial_bill':
            return True
        line_source = self._get_partial_bill_line_source(job)
        partner = self._get_job_partner(job)
        self._assign_partial_bill_move_lines(job, line_source, partner)
        return True

    def sync_partial_purchase_order_lines(self, job):
        """Fill concrete purchase order lines for already matched partial OCR rows."""
        job.ensure_one()
        if job.mode != 'partial_purchase':
            return True
        line_source = self._get_partial_purchase_line_source(job)
        partner = self._get_job_partner(job)
        self._assign_partial_bill_move_lines(job, line_source, partner)
        return True

    def _match_full_purchase(self, job):
        self._match_full_document_products(job, mode_label='Full purchase order')

    def _match_full_bill(self, job):
        self._match_full_document_products(job, mode_label='Full vendor bill')

    def _match_full_document_products(self, job, mode_label='Full document'):
        partner = self._get_job_partner(job)
        line_results = {}
        for line in job.line_ids:
            try:
                products = self._find_full_bill_products(line, partner)
                name_token_frequencies = self._product_name_token_frequencies(products)
                candidates = [
                    self._score_product(
                        line,
                        product,
                        partner,
                        strict_code_profile=True,
                        name_token_frequencies=name_token_frequencies,
                    )
                    for product in products
                ]
                diagnostics = self._build_product_diagnostics(
                    line,
                    job,
                    products,
                    candidates,
                    mode_label=mode_label,
                )
                line_results[line.id] = {
                    'line': line,
                    'candidates': candidates,
                    'diagnostics': diagnostics,
                }
            except Exception as error:
                _logger.exception('Gemini full document matching failed.')
                self._write_line_error(line, error)
        self._write_full_document_assignment_results(line_results)

    def _get_move_product_lines(self, job):
        return self._get_partial_bill_line_source(job)['product_lines']

    def _partial_create_lines(self, job):
        return job.line_ids.filtered(
            lambda line: (line.apply_action or 'create_line') == 'create_line'
        )

    def _is_partial_create_line(self, line):
        return (getattr(line, 'apply_action', False) or 'create_line') == 'create_line'

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
                'line_field': 'move_line_id',
                'document_label': 'vendor bill',
                'business_line_label': 'vendor bill line',
                'document_id_label': 'Move ID',
                'document_id': 'none',
                'source_total_label': 'invoice_line_ids total',
                'source_product_label': 'invoice_line_ids product lines',
                'fallback_total_label': 'line_ids total',
                'fallback_product_label': 'line_ids product lines',
                'candidate_scope_label': 'current vendor bill',
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
            'line_field': 'move_line_id',
            'document_label': 'vendor bill',
            'business_line_label': 'vendor bill line',
            'document_id_label': 'Move ID',
            'document_id': move.id,
            'source_total_label': 'invoice_line_ids total',
            'source_product_label': 'invoice_line_ids product lines',
            'fallback_total_label': 'line_ids total',
            'fallback_product_label': 'line_ids product lines',
            'candidate_scope_label': 'current vendor bill',
        }

    def _get_partial_purchase_line_source(self, job):
        order = job.purchase_order_id
        if not order:
            return {
                'source': 'none',
                'product_lines': [],
                'invoice_lines': [],
                'invoice_product_lines': [],
                'line_ids': [],
                'line_product_lines': [],
                'line_field': 'purchase_order_line_id',
                'document_label': 'purchase order',
                'business_line_label': 'purchase order line',
                'document_id_label': 'Purchase Order ID',
                'document_id': 'none',
                'source_total_label': 'order_line total',
                'source_product_label': 'order_line product lines',
                'fallback_total_label': 'fallback lines total',
                'fallback_product_label': 'fallback product lines',
                'candidate_scope_label': 'current purchase order/RFQ',
            }

        order_lines = list(getattr(order, 'order_line', []))
        order_product_lines = [
            line
            for line in order_lines
            if self._is_purchase_product_line(line)
        ]
        return {
            'source': 'order_line',
            'product_lines': order_product_lines,
            'invoice_lines': order_lines,
            'invoice_product_lines': order_product_lines,
            'line_ids': [],
            'line_product_lines': [],
            'line_field': 'purchase_order_line_id',
            'document_label': 'purchase order',
            'business_line_label': 'purchase order line',
            'document_id_label': 'Purchase Order ID',
            'document_id': order.id,
            'source_total_label': 'order_line total',
            'source_product_label': 'order_line product lines',
            'fallback_total_label': 'fallback lines total',
            'fallback_product_label': 'fallback product lines',
            'candidate_scope_label': 'current purchase order/RFQ',
        }

    def _is_purchase_product_line(self, line):
        if not getattr(line, 'product_id', False):
            return False
        display_type = getattr(line, 'display_type', False)
        if display_type:
            return False
        return True

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

    def _apply_single_line_partial_fallback(
        self,
        line,
        line_source,
        candidates,
        create_line_count,
    ):
        if not self._is_partial_create_line(line):
            return False
        move_lines = line_source['product_lines']
        if create_line_count != 1 or len(move_lines) != 1:
            return False

        move_line = move_lines[0]
        candidate = self._candidate_for_move_line(candidates, move_line)
        if not candidate:
            return False

        candidate['score'] = 1.0
        candidate['method'] = self._partial_method(
            line_source,
            'single_line_partial_bill',
            'single_line_partial_purchase',
        )
        candidate.setdefault('notes', []).append(
            'Single-line partial fallback: one create_line OCR row and one product line on the document.'
        )
        candidate['notes'].append(
            'Quantity, price, and taxes will be applied to the existing line after validation.'
        )
        return True

    def _candidate_for_move_line(self, candidates, move_line):
        for candidate in candidates:
            if candidate.get('move_line') == move_line:
                return candidate
        return False

    def _write_partial_single_line_result(
        self,
        line,
        line_source,
        diagnostics,
        create_line_count,
        candidate,
    ):
        move_line = candidate['move_line']
        product = move_line.product_id
        note = self._append_text(
            '\n'.join(diagnostics or []),
            self._partial_sync_diagnostic_text(
                line,
                line_source,
                create_line_count,
                'Єдиний OCR-рядок зіставлено з єдиним рядком рахунку.',
                move_line=move_line,
            ),
        )
        values = self._partial_business_line_match_values(
            line_source,
            move_line,
            product,
        )
        values.update({
            'match_status': 'matched',
            'match_score': 1.0,
            'match_method': self._partial_method(
                line_source,
                'single_line_partial_bill',
                'single_line_partial_purchase',
            ),
            'match_summary': _(
                'Єдиний OCR-рядок зіставлено з єдиним рядком документа.'
            ),
            'match_note': note,
        })
        line.write(values)
        return True

    def _sync_partial_line_product_from_move_line(
        self,
        line,
        line_source,
        create_line_count,
        from_matching=False,
    ):
        move_line = self._get_partial_business_line(line)
        if not move_line:
            return False
        product = move_line.product_id
        if not product:
            return False
        values = {
            'candidate_product_ids': [(6, 0, [])],
        }
        if line.matched_product_id != product:
            values['matched_product_id'] = product.id
        if line.match_status not in ('matched', 'manual'):
            values.update({
                'match_status': 'matched',
                'match_method': line.match_method or 'move_line_product_sync',
                'match_score': max(line.match_score or 0.0, 0.95),
                'match_summary': _(
                    'Matched: document line %(line)s by move_line_product_sync, score %(score).2f'
                ) % {
                    'line': move_line.display_name,
                    'score': max(line.match_score or 0.0, 0.95),
                },
            })
        if values:
            values['match_note'] = self._append_text(
                line.match_note,
                self._partial_sync_diagnostic_text(
                    line,
                    line_source,
                    create_line_count,
                    'Synchronized matched product from selected vendor bill line.',
                    move_line=move_line,
                ),
            )
            line.write(values)
        elif not from_matching:
            self._append_partial_sync_diagnostics(
                line,
                line_source,
                create_line_count,
                'Selected vendor bill line already matches the OCR line product.',
                move_line=move_line,
            )
        return True

    def _append_partial_sync_diagnostics(
        self,
        line,
        line_source,
        create_line_count,
        reason,
        move_line=False,
        from_matching=False,
    ):
        if from_matching:
            return False
        line.write({
            'match_note': self._append_text(
                line.match_note,
                self._partial_sync_diagnostic_text(
                    line,
                    line_source,
                    create_line_count,
                    reason,
                    move_line=move_line,
                ),
            ),
        })
        return True

    def _partial_sync_diagnostic_text(
        self,
        line,
        line_source,
        create_line_count,
        reason,
        move_line=False,
    ):
        product_lines = line_source['product_lines']
        selected_product = getattr(move_line, 'product_id', False) if move_line else False
        line_field = line_source.get('line_field') or 'move_line_id'
        return '\n'.join([
            'Partial document line sync:',
            'Document: %s.' % line_source.get('document_label', 'document'),
            'OCR create_line rows: %s.' % create_line_count,
            'Document product lines: %s.' % len(product_lines),
            'Single-line fallback applicable: %s.' % (
                'yes' if create_line_count == 1 and len(product_lines) == 1 else 'no'
            ),
            'Resolved %s: %s.' % (line_field, move_line.id if move_line else 'none'),
            'Business line product: %s.' % (
                getattr(selected_product, 'display_name', False)
                or getattr(selected_product, 'name', False)
                or 'none'
            ),
            'Reason: %s' % reason,
        ])

    def _assign_partial_bill_move_lines(self, job, line_source, partner):
        move_lines = line_source['product_lines']
        create_lines = self._partial_create_lines(job).sorted('sequence')
        create_line_count = len(create_lines)
        name_token_frequencies = self._move_line_name_token_frequencies(move_lines)
        candidate_map = {}
        diagnostics_map = {}

        for line in create_lines:
            try:
                candidates = [
                    self._score_move_line(
                        line,
                        move_line,
                        partner,
                        name_token_frequencies=name_token_frequencies,
                    )
                    for move_line in move_lines
                ]
                self._apply_single_line_partial_fallback(
                    line,
                    line_source,
                    candidates,
                    create_line_count,
                )
                candidate_map[line.id] = candidates
                diagnostics_map[line.id] = self._build_partial_diagnostics(
                    line,
                    job,
                    line_source,
                    candidates,
                )
            except Exception as error:
                _logger.exception('Gemini partial bill matching failed.')
                self._write_line_error(line, error)

        if create_line_count == 1 and len(move_lines) == 1:
            line = create_lines[0]
            if line.id in candidate_map and not self._is_manual_partial_mapping(line):
                candidate = self._candidate_for_move_line(candidate_map[line.id], move_lines[0])
                if candidate:
                    self._write_partial_single_line_result(
                        line,
                        line_source,
                        diagnostics_map.get(line.id),
                        create_line_count,
                        candidate,
                    )
                    return

        locked_move_line_ids = {
            self._get_partial_business_line(line).id
            for line in create_lines
            if self._is_manual_partial_mapping(line)
        }
        assignment = self._compute_partial_global_assignment(
            create_lines,
            candidate_map,
            line_source,
            locked_move_line_ids,
        )
        self._apply_partial_remaining_line_fallback(create_lines, line_source, assignment)

        for line in create_lines:
            if line.id not in candidate_map:
                continue
            if self._is_manual_partial_mapping(line):
                self._write_partial_locked_result(
                    line,
                    line_source,
                    diagnostics_map.get(line.id),
                    create_line_count,
                )
                continue
            self._write_partial_assignment_result(
                line,
                candidate_map.get(line.id) or [],
                diagnostics_map.get(line.id),
                assignment,
                line_source,
            )

    def _is_manual_partial_mapping(self, line):
        method = str(getattr(line, 'match_method', '') or '')
        return bool(
            self._get_partial_business_line(line)
            and (
                line.match_status == 'manual'
                or method.startswith('manual_')
            )
        )

    def _compute_partial_global_assignment(
        self,
        create_lines,
        candidate_map,
        line_source,
        locked_move_line_ids,
    ):
        result = {
            'assigned': {},
            'ambiguous_line_ids': set(),
            'conflict_message': False,
            'global_method': 'global_one_to_one_assignment',
            'global_applied': False,
        }
        unlocked_lines = [
            line
            for line in create_lines
            if not self._is_manual_partial_mapping(line)
            and line.id in candidate_map
        ]
        safe_candidates = {}
        total_pairs = 0
        for line in unlocked_lines:
            candidates = [
                candidate
                for candidate in candidate_map.get(line.id, [])
                if candidate.get('move_line')
                and candidate['move_line'].id not in locked_move_line_ids
                and (candidate.get('score') or 0.0) >= self.PARTIAL_SAFE_ASSIGNMENT_THRESHOLD
            ]
            candidates.sort(key=lambda candidate: candidate.get('score') or 0.0, reverse=True)
            safe_candidates[line.id] = candidates
            total_pairs += len(candidates)

        assignment_lines = [
            line for line in unlocked_lines if safe_candidates.get(line.id)
        ]
        if not assignment_lines:
            return result

        if (
            len(assignment_lines) > self.PARTIAL_MAX_ASSIGNMENT_LINES
            or total_pairs > self.PARTIAL_MAX_ASSIGNMENT_PAIRS
        ):
            result['ambiguous_line_ids'] = {line.id for line in assignment_lines}
            result['conflict_message'] = (
                'Global one-to-one assignment skipped because the candidate group is too large.'
            )
            return result

        best_assignments = self._find_best_partial_assignments(
            assignment_lines,
            safe_candidates,
        )
        if not best_assignments:
            result['ambiguous_line_ids'] = {line.id for line in assignment_lines}
            result['conflict_message'] = (
                'No complete one-to-one assignment is possible for the current candidates.'
            )
            return result

        best_total, best_assignment = best_assignments[0]
        if len(best_assignments) > 1:
            second_total = best_assignments[1][0]
            if abs(best_total - second_total) <= self.PARTIAL_ASSIGNMENT_TIE_TOLERANCE:
                result['ambiguous_line_ids'] = {line.id for line in assignment_lines}
                result['conflict_message'] = (
                    'Several one-to-one assignments have almost the same score.'
                )
                return result

        product_line_count = len(line_source['product_lines'])
        equal_count_safe_fallback = (
            len(create_lines) == product_line_count
            and len(best_assignment) == len(create_lines)
            and all(
                candidate_map.get(line.id)
                and any(
                    candidate.get('move_line')
                    and (candidate.get('score') or 0.0) >= self.PARTIAL_SAFE_ASSIGNMENT_THRESHOLD
                    for candidate in candidate_map[line.id]
                )
                for line in create_lines
            )
        )
        if equal_count_safe_fallback:
            result['global_method'] = 'global_one_to_one_equal_count_fallback'

        for line in assignment_lines:
            candidate = best_assignment.get(line.id)
            if not candidate:
                continue
            score = candidate.get('score') or 0.0
            if score >= self.PARTIAL_AUTO_ASSIGNMENT_THRESHOLD or equal_count_safe_fallback:
                result['assigned'][line.id] = candidate
            else:
                result['ambiguous_line_ids'].add(line.id)
                result['conflict_message'] = (
                    'Best one-to-one candidate is below automatic assignment threshold.'
                )

        if result['assigned']:
            result['global_applied'] = True
        return result

    def _apply_partial_remaining_line_fallback(self, create_lines, line_source, assignment):
        move_lines = line_source['product_lines']
        if len(create_lines) != len(move_lines):
            return False
        if any((line.apply_action or 'create_line') != 'create_line' for line in create_lines):
            return False

        assigned_move_line_ids = []
        unmatched_lines = []
        for line in create_lines:
            if self._is_manual_partial_mapping(line):
                business_line = self._get_partial_business_line(line)
                if not business_line:
                    return False
                assigned_move_line_ids.append(business_line.id)
                continue
            assigned = assignment['assigned'].get(line.id)
            if assigned and assigned.get('move_line'):
                assigned_move_line_ids.append(assigned['move_line'].id)
                continue
            unmatched_lines.append(line)

        if len(assigned_move_line_ids) != len(set(assigned_move_line_ids)):
            return False
        unused_move_lines = [
            move_line
            for move_line in move_lines
            if move_line.id not in set(assigned_move_line_ids)
        ]
        if len(unmatched_lines) != 1 or len(unused_move_lines) != 1:
            return False

        line = unmatched_lines[0]
        move_line = unused_move_lines[0]
        assignment['assigned'][line.id] = {
            'product': move_line.product_id,
            'move_line': move_line,
            'score': 0.90,
            'method': self._partial_method(
                line_source,
                'remaining_line_partial_bill',
                'remaining_line_partial_purchase',
            ),
            'notes': [
                'Safe residual partial fallback: all other OCR rows and document product lines are already uniquely assigned.'
            ],
        }
        assignment['global_applied'] = True
        assignment['global_method'] = self._partial_method(
            line_source,
            'remaining_line_partial_bill',
            'remaining_line_partial_purchase',
        )
        return True

    def _find_best_partial_assignments(self, assignment_lines, safe_candidates):
        ordered_lines = sorted(
            assignment_lines,
            key=lambda line: (len(safe_candidates.get(line.id, [])), line.sequence, line.id),
        )
        complete_assignments = []

        def _walk(index, used_move_line_ids, selected, total_score):
            if index >= len(ordered_lines):
                complete_assignments.append((total_score, dict(selected)))
                return
            line = ordered_lines[index]
            for candidate in safe_candidates.get(line.id, []):
                move_line = candidate.get('move_line')
                if not move_line or move_line.id in used_move_line_ids:
                    continue
                selected[line.id] = candidate
                used_move_line_ids.add(move_line.id)
                _walk(
                    index + 1,
                    used_move_line_ids,
                    selected,
                    total_score + (candidate.get('score') or 0.0),
                )
                used_move_line_ids.remove(move_line.id)
                selected.pop(line.id, None)

        _walk(0, set(), {}, 0.0)
        complete_assignments.sort(key=lambda item: item[0], reverse=True)
        return complete_assignments[:2]

    def _write_partial_locked_result(
        self,
        line,
        line_source,
        diagnostics,
        create_line_count,
    ):
        move_line = self._get_partial_business_line(line)
        product = move_line.product_id
        values = self._partial_business_line_match_values(
            line_source,
            move_line,
            product,
        )
        values.update({
            'match_status': 'manual',
            'match_score': line.match_score or 1.0,
            'match_method': line.match_method or 'manual_move_line',
            'match_summary': _('Рядок документа обрано вручну.'),
            'match_note': self._append_text(
                '\n'.join(diagnostics or []),
                self._partial_sync_diagnostic_text(
                    line,
                    line_source,
                    create_line_count,
                    'Manual move_line_id is locked and was not changed automatically.',
                    move_line=move_line,
                ),
            ),
        })
        line.write(values)

    def _write_partial_assignment_result(
        self,
        line,
        candidates,
        diagnostics,
        assignment,
        line_source,
    ):
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
        assigned = assignment['assigned'].get(line.id)
        values = {
            'move_line_id': False,
            'purchase_order_line_id': False,
            'matched_product_id': False,
            'candidate_product_ids': [(6, 0, [])],
            'candidate_move_line_ids': [
                (6, 0, self._candidate_move_line_ids(visible_candidates))
                if self._is_partial_bill_source(line_source)
                else (6, 0, [])
            ],
            'match_score': best['score'] if best else 0.0,
            'match_method': best['method'] if best else False,
        }

        if assigned:
            move_line = assigned['move_line']
            assigned.setdefault('notes', []).append(
                'Global one-to-one assignment applied by %s.'
                % assignment['global_method']
            )
            values.update(self._partial_business_line_match_values(
                line_source,
                move_line,
                move_line.product_id,
            ))
            values.update({
                'match_status': 'matched',
                'match_score': assigned.get('score') or 0.0,
                'match_method': assigned.get('method') or assignment['global_method'],
            })
            winner_candidate = assigned
        elif line.id in assignment['ambiguous_line_ids'] or visible_candidates:
            values['match_status'] = 'ambiguous'
            winner_candidate = False
        else:
            values['match_status'] = 'not_found'
            winner_candidate = False

        match_note = self._build_match_note(
            candidates,
            visible_candidates,
            diagnostics=diagnostics,
            status=values['match_status'],
        )
        if assignment.get('conflict_message') and (
            line.id in assignment['ambiguous_line_ids']
            or not assigned
        ):
            match_note = self._append_text(match_note, assignment['conflict_message'])
        if assigned:
            match_note = self._append_text(
                match_note,
                self._partial_assignment_diagnostic_text(line, assignment, assigned),
            )
        if values['match_status'] == 'not_found':
            technical_message = self._unmatched_technical_code_message(line)
            if technical_message:
                match_note = self._append_text(match_note, technical_message)
        values['match_note'] = match_note
        values['match_summary'] = self._build_match_summary(
            line,
            values,
            winner_candidate,
            best,
            visible_candidates,
        )
        if assigned:
            values['match_summary'] = _(
                'Зіставлено з рядком документа %(line)s за методом %(method)s.'
            ) % {
                'line': assigned['move_line'].display_name,
                'method': values['match_method'],
            }
        elif values['match_status'] == 'ambiguous':
            values['match_summary'] = _(
                'Не вдалося однозначно визначити рядок документа.'
            )
        elif values['match_status'] == 'not_found' and self._unmatched_technical_code_message(line):
            values['match_summary'] = self._unmatched_technical_code_message(line)
        elif values['match_status'] == 'not_found':
            values['match_summary'] = _(
                'Не знайдено відповідний товарний рядок документа.'
            )
        line.write(values)
        if values['match_status'] in ('ambiguous', 'not_found'):
            self._log_technical_match(line, candidates)
            self._log_name_model_match(line, candidates, values['match_status'])
        if values['match_status'] == 'not_found':
            self._log_unmatched_supplier_article(line, candidates)

    def _is_partial_bill_source(self, line_source):
        return (line_source.get('line_field') or 'move_line_id') == 'move_line_id'

    def _partial_method(self, line_source, bill_method, purchase_method):
        return bill_method if self._is_partial_bill_source(line_source) else purchase_method

    def _get_partial_business_line(self, line):
        if getattr(line.job_id, 'mode', False) == 'partial_purchase':
            return line.purchase_order_line_id
        return line.move_line_id

    def _partial_business_line_match_values(self, line_source, business_line, product):
        values = {
            'matched_product_id': product.id,
            'candidate_product_ids': [(6, 0, [])],
        }
        if self._is_partial_bill_source(line_source):
            values.update({
                'move_line_id': business_line.id,
                'purchase_order_line_id': False,
                'candidate_move_line_ids': [(6, 0, [business_line.id])],
            })
        else:
            values.update({
                'move_line_id': False,
                'purchase_order_line_id': business_line.id,
                'candidate_move_line_ids': [(6, 0, [])],
            })
        return values

    def _partial_assignment_diagnostic_text(self, line, assignment, candidate):
        move_line = candidate.get('move_line')
        return '\n'.join([
            'Partial bill global assignment:',
            'OCR line: %s.' % line._display_label(),
            'Global one-to-one applied: %s.' % ('yes' if assignment.get('global_applied') else 'no'),
            'Assignment method: %s.' % assignment.get('global_method'),
            'Selected move_line_id: %s.' % (move_line.id if move_line else 'none'),
            'Score: %.2f.' % (candidate.get('score') or 0.0),
            'Candidate method: %s.' % (candidate.get('method') or 'unknown'),
        ])

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
            for search_code in self._code_search_variants(code):
                for product in self._search_products_exact('default_code', search_code):
                    self._append_unique(products, product)
                for product in self._search_products_exact('barcode', search_code):
                    self._append_unique(products, product)
                for product in self._search_products_code_like(search_code):
                    self._append_unique(products, product)
                for seller in self._search_supplierinfos_code_like(search_code, partner):
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

        for seller in self._find_supplierinfos_by_articles(
            self._line_supplier_articles(line),
            partner,
        ):
            self._append_unique(products, self._seller_product(seller))

        for seller in self._find_supplierinfos_by_codes(
            profile['primary_codes'],
            partner,
            allow_low_value=True,
        ):
            self._append_unique(products, self._seller_product(seller))

        for code in search_codes:
            if self._is_low_value_code(code):
                continue
            for search_code in self._code_search_variants(code):
                for product in self._search_products_exact('default_code', search_code):
                    self._append_unique(products, product)
                for product in self._search_products_exact('barcode', search_code):
                    self._append_unique(products, product)
                for product in self._search_products_code_like(search_code):
                    self._append_unique(products, product)
                for seller in self._search_supplierinfos_code_like(search_code, partner):
                    self._append_unique(products, self._seller_product(seller))

        for term in self._full_bill_search_terms(line, profile):
            for product in self._search_products_like(term):
                self._append_unique(products, product)
            for seller in self._search_supplierinfos_like(term, partner):
                self._append_unique(products, self._seller_product(seller))

        for product in self._find_full_bill_name_products(line):
            self._append_unique(products, product)

        for history_line in self._find_historical_name_move_lines(line, partner):
            self._append_unique(products, getattr(history_line, 'product_id', False))

        for history_line in self._find_historical_move_lines(line, partner):
            self._append_unique(products, getattr(history_line, 'product_id', False))
        return products

    def _score_move_line(self, line, move_line, partner, name_token_frequencies=False):
        product = move_line.product_id
        score, method, notes = self._score_product_identity(line, product, partner)
        score, method, notes = self._score_supplierinfo_article_exact(
            line,
            product,
            partner,
            score,
            method,
            notes,
        )
        score, method, notes = self._score_partial_delivery_service_match(
            line,
            move_line,
            score,
            method,
            notes,
        )
        score, method, notes = self._score_partial_code_match(
            line,
            move_line,
            partner,
            score,
            method,
            notes,
        )
        technical_score, technical_method, technical_notes, technical_details = (
            self._score_technical_move_line_match(line, move_line, partner)
        )
        if technical_score > score or (
            technical_score == score == 1.0
            and technical_method == 'technical_code_exact'
        ):
            score = technical_score
            method = technical_method
        elif score and technical_score >= 0.80:
            score += min(0.03, (technical_score - 0.80) * 0.15)
        notes.extend(technical_notes)

        name_model_score, name_model_method, name_model_notes, name_model_details = (
            self._score_move_line_name_model_match(
                line,
                move_line,
                product,
                name_token_frequencies=name_token_frequencies,
            )
        )
        if name_model_score > score:
            score = name_model_score
            method = name_model_method
        elif score and name_model_score >= 0.80:
            score += min(0.04, (name_model_score - 0.80) * 0.20)
        notes.extend(name_model_notes)

        score, method = self._score_move_line_text(line, move_line, score, method)
        notes.append(
            'Partial bill candidate is limited to the current vendor bill line; '
            'quantity, price, and subtotal are not used as matching requirements.'
        )
        return {
            'product': product,
            'move_line': move_line,
            'score': self._clamp_score(score),
            'method': method,
            'notes': notes,
            'extracted_codes': self._line_codes(line),
            'candidate_codes': self._candidate_codes(product, move_line, partner),
            'technical_details': technical_details,
            'name_model_details': name_model_details,
        }

    def _score_partial_delivery_service_match(self, line, move_line, score, method, notes):
        line_text = self._ocr_line_text(line)
        product = move_line.product_id
        candidate_text = ' '.join(
            str(value)
            for value in (
                getattr(move_line, 'name', False),
                getattr(product, 'display_name', False),
                getattr(product, 'name', False),
            )
            if value
        )
        if not (
            self._is_delivery_service_text(line_text)
            and self._is_delivery_service_text(candidate_text)
        ):
            return score, method, notes
        score, method = self._choose_score(
            score,
            method,
            0.96,
            'delivery_service_match',
        )
        notes.append(
            'Delivery/service semantic match: OCR line and vendor bill line both contain logistics delivery signals.'
        )
        return score, method, notes

    def _is_delivery_service_text(self, value):
        text = self._normalize_text(value)
        if not text:
            return False
        return any(
            self._normalize_text(term) in text
            for term in self.DELIVERY_SERVICE_TERMS
        )

    def _score_supplierinfo_article_exact(self, line, product, partner, score, method, notes):
        for code in self._line_supplier_articles(line):
            match_info = self._supplier_code_match_info(product, partner, code)
            if not match_info:
                continue
            score, method = self._choose_score(
                score,
                method,
                1.0,
                match_info['method'],
            )
            notes.append(
                'Exact vendor supplierinfo article match: raw=%s normalized=%s method=%s.'
                % (
                    code,
                    SupplierArticleNormalizer.normalize(code),
                    match_info['method'],
                )
            )
            break
        return score, method, notes

    def _score_product(
        self,
        line,
        product,
        partner,
        strict_code_profile=False,
        name_token_frequencies=False,
    ):
        if strict_code_profile:
            return self._score_product_strict(
                line,
                product,
                partner,
                name_token_frequencies=name_token_frequencies,
            )

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

    def _score_product_strict(
        self,
        line,
        product,
        partner,
        name_token_frequencies=False,
    ):
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
            supplier_code_match = self._supplier_code_match_info(product, partner, code)
            if supplier_code_match:
                score = 1.0
                method = supplier_code_match['method']
                supplierinfo_signal = 'supplierinfo_code_exact:%s' % code
                notes.append(
                    'Exact vendor supplierinfo code match: %s (%s).'
                    % (code, supplier_code_match['method'])
                )
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

        technical_score, technical_method, technical_notes, technical_details = (
            self._score_technical_product_match(line, product, partner)
        )
        if technical_score:
            if technical_score > score:
                score = technical_score
                method = technical_method
            elif score and technical_score >= 0.80:
                boost = min(0.03, (technical_score - 0.80) * 0.15)
                if boost > 0:
                    score += boost
                    boosts.append('technical_segments +%.2f' % boost)
            notes.extend(technical_notes)

        name_score, name_method, name_notes, name_details = self._score_plain_product_name_match(
            line,
            product,
        )
        if name_score:
            if name_score > score:
                score = name_score
                method = name_method
            elif score and name_score >= 0.80:
                boost = min(0.03, (name_score - 0.80) * 0.15)
                if boost > 0:
                    score += boost
                    boosts.append('plain_name +%.2f' % boost)
            notes.extend(name_notes)

        name_model_score, name_model_method, name_model_notes, name_model_details = (
            self._score_product_name_model_match(
                line,
                product,
                name_token_frequencies=name_token_frequencies,
            )
        )
        if name_model_score:
            if name_model_score > score:
                score = name_model_score
                method = name_model_method
            elif score and name_model_score >= 0.80:
                boost = min(0.04, (name_model_score - 0.80) * 0.20)
                if boost > 0:
                    score += boost
                    boosts.append('name_model +%.2f' % boost)
            notes.extend(name_model_notes)

        history_name_score, history_name_method, history_name_notes, history_name_details = (
            self._score_historical_name_product_match(line, product, partner)
        )
        if history_name_score:
            if history_name_score > score:
                score = history_name_score
                method = history_name_method
            elif score and history_name_score >= 0.80:
                boost = min(0.03, (history_name_score - 0.80) * 0.15)
                if boost > 0:
                    score += boost
                    boosts.append('historical_plain_name +%.2f' % boost)
            notes.extend(history_name_notes)

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

        if self._has_conflicting_full_technical_codes(technical_details, method):
            score = min(score, 0.69)
            penalties.append(
                'conflicting full technical code prevents automatic match'
            )

        if not score:
            method = method or False
        bundle_note = self._bundle_product_note(line, product)
        if bundle_note:
            notes.append(bundle_note)
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
            'technical_details': technical_details,
            'name_details': name_details,
            'name_model_details': name_model_details,
            'history_name_details': history_name_details,
        }

    def _score_product_identity(self, line, product, partner):
        score = 0.0
        method = False
        notes = []

        for code in self._line_codes(line):
            supplier_code_match = self._supplier_code_match_info(product, partner, code)
            if supplier_code_match:
                notes.append(
                    'Exact vendor supplierinfo code match: %s (%s).'
                    % (code, supplier_code_match['method'])
                )
                return 1.0, supplier_code_match['method'], notes
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
                    0.92,
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
                    0.90,
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
                    0.89,
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
                if SupplierArticleNormalizer.equals(code, getattr(seller, 'product_code', False)):
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

    def _score_plain_product_name_match(self, line, product):
        notes = []
        details = {
            'line_names': self._line_normalized_plain_names(line),
            'product_names': self._product_normalized_plain_names(product),
            'match_type': 'none',
        }
        if not details['line_names'] or not details['product_names']:
            return 0.0, False, notes, details

        for line_name in details['line_names']:
            for product_name in details['product_names']:
                if not line_name or not product_name:
                    continue
                if line_name == product_name:
                    if not self._is_safe_plain_name_for_exact(line_name):
                        notes.append(
                            'Ignored exact normalized product name match because the name is too generic: "%s".'
                            % line_name
                        )
                        continue
                    details['match_type'] = 'exact'
                    notes.append(
                        'Exact normalized product name match: "%s".' % line_name
                    )
                    return 0.97, 'name_exact_or_near_exact', notes, details

        best_near_exact = False
        best_near_exact_score = 0.0
        for line_name in details['line_names']:
            if not self._is_safe_plain_name_for_exact(line_name):
                continue
            for product_name in details['product_names']:
                if not product_name:
                    continue
                similarity = difflib.SequenceMatcher(None, line_name, product_name).ratio()
                token_similarity = self._weighted_name_similarity(line_name, product_name)
                candidate_score = max(similarity, token_similarity)
                if candidate_score > best_near_exact_score:
                    best_near_exact_score = candidate_score
                    best_near_exact = (line_name, product_name)

        if best_near_exact and best_near_exact_score >= 0.96:
            details['match_type'] = 'near_exact'
            notes.append(
                'Near-exact normalized product name match: "%s" ~= "%s"; similarity %.2f.'
                % (best_near_exact[0], best_near_exact[1], best_near_exact_score)
            )
            return 0.97, 'name_exact_or_near_exact', notes, details

        best_substring = False
        for line_name in details['line_names']:
            for product_name in details['product_names']:
                if not self._is_safe_plain_name_for_substring(line_name):
                    continue
                if line_name in product_name or product_name in line_name:
                    best_substring = (line_name, product_name)
                    break
            if best_substring:
                break

        if best_substring:
            details['match_type'] = 'substring'
            notes.append(
                'Normalized product name substring match: "%s" ~= "%s".'
                % best_substring
            )
            return 0.90, 'product_name_substring', notes, details

        return 0.0, False, notes, details

    def _score_product_name_model_match(self, line, product, name_token_frequencies=False):
        return self._score_name_model_match(
            self._line_name_model_values(line),
            self._product_name_model_values(product),
            source_label='product',
            name_token_frequencies=name_token_frequencies,
        )

    def _score_move_line_name_model_match(
        self,
        line,
        move_line,
        product,
        name_token_frequencies=False,
    ):
        return self._score_name_model_match(
            self._line_name_model_values(line),
            self._move_line_name_model_values(move_line, product),
            source_label='document line/product',
            name_token_frequencies=name_token_frequencies,
        )

    def _score_name_model_match(
        self,
        line_values,
        candidate_values,
        source_label,
        name_token_frequencies=False,
    ):
        notes = []
        line_profile = self._name_model_token_profile(
            line_values,
            name_token_frequencies=name_token_frequencies,
        )
        candidate_profile = self._name_model_token_profile(
            candidate_values,
            name_token_frequencies=name_token_frequencies,
        )
        details = {
            'source': source_label,
            'ocr_tokens': line_profile['tokens'],
            'candidate_tokens': candidate_profile['tokens'],
            'ocr_meaningful_tokens': line_profile['meaningful_tokens'],
            'candidate_meaningful_tokens': candidate_profile['meaningful_tokens'],
            'token_weights': line_profile['weights'],
            'matched_tokens': [],
            'matched_phrases': [],
            'generic_matches': [],
            'conflicting_tokens': [],
            'missing_meaningful_tokens': [],
            'token_weight_score': 0.0,
            'phrase_score': 0.0,
            'conflict_penalty': 0.0,
        }
        if not line_profile['tokens'] or not candidate_profile['tokens']:
            return 0.0, False, notes, details

        candidate_tokens = set(candidate_profile['tokens'])
        overlap = set(line_profile['tokens']) & candidate_tokens
        matched_meaningful = [
            token
            for token in line_profile['meaningful_tokens']
            if token in overlap
        ]
        generic_matches = [
            token
            for token in overlap
            if token not in matched_meaningful
        ]
        matched_weight = sum(
            line_profile['weights'].get(token, 0.0)
            for token in matched_meaningful
        )
        total_meaningful_weight = sum(
            line_profile['weights'].get(token, 0.0)
            for token in line_profile['meaningful_tokens']
        )
        high_value_matches = [
            token
            for token in matched_meaningful
            if line_profile['weights'].get(token, 0.0) >= 0.55
        ]
        matched_phrases = self._matching_name_model_phrases(
            line_profile,
            candidate_profile,
        )
        phrase_score = self._name_model_phrase_score(
            matched_phrases,
            line_profile['weights'],
        )
        conflicting_tokens = self._conflicting_name_model_tokens(
            line_profile,
            candidate_profile,
            matched_meaningful,
        )
        conflict_penalty = self._name_model_conflict_penalty(conflicting_tokens)
        missing_meaningful = [
            token
            for token in line_profile['meaningful_tokens']
            if token not in overlap
        ]
        details.update({
            'matched_tokens': matched_meaningful,
            'matched_phrases': matched_phrases,
            'generic_matches': sorted(generic_matches),
            'conflicting_tokens': conflicting_tokens,
            'missing_meaningful_tokens': missing_meaningful,
            'token_weight_score': self._clamp_score(matched_weight),
            'phrase_score': self._clamp_score(phrase_score),
            'conflict_penalty': self._clamp_score(conflict_penalty),
        })

        if not matched_meaningful:
            if generic_matches:
                notes.append(
                    'Name-only match rejected in %s: only generic tokens matched: %s.'
                    % (source_label, ', '.join(sorted(generic_matches)))
                )
            return 0.0, False, notes, details

        coverage = (
            matched_weight / total_meaningful_weight
            if total_meaningful_weight
            else 0.0
        )
        score = 0.0
        method = False
        if (
            matched_weight >= self.NAME_MEANINGFUL_WEIGHT_THRESHOLD
            and high_value_matches
        ) or (
            matched_weight >= self.NAME_MEANINGFUL_WEIGHT_THRESHOLD + 0.35
            and len(matched_meaningful) >= 2
        ) or matched_phrases:
            score = 0.58 + min(0.26, matched_weight * 0.11) + min(0.14, coverage * 0.16)
            score += phrase_score
            score -= conflict_penalty
            method = 'name_weighted_match'
            if high_value_matches:
                method = 'name_weighted_model_match'
        else:
            score = min(0.69, 0.36 + matched_weight * 0.10 + phrase_score)
            score -= min(conflict_penalty, 0.12)
            method = 'weak_name_weighted_overlap'

        if score:
            notes.append(
                'Weighted name match in %s: OCR tokens=%s; candidate tokens=%s; '
                'matched tokens=%s; matched phrases=%s; generic matches=%s; '
                'conflicting tokens=%s; matched_weight=%.2f; coverage=%.2f; '
                'phrase_score=%.2f; conflict_penalty=%.2f; score=%.2f.'
                % (
                    source_label,
                    ', '.join(line_profile['tokens']) or 'none',
                    ', '.join(candidate_profile['tokens']) or 'none',
                    ', '.join(matched_meaningful) or 'none',
                    ', '.join(matched_phrases) or 'none',
                    ', '.join(sorted(generic_matches)) or 'none',
                    ', '.join(conflicting_tokens) or 'none',
                    matched_weight,
                    coverage,
                    phrase_score,
                    conflict_penalty,
                    score,
                )
            )
        return self._clamp_score(score), method, notes, details

    def _score_historical_name_product_match(self, line, product, partner):
        notes = []
        details = {
            'line_names': self._line_normalized_plain_names(line),
            'historical_lines_checked': 0,
            'historical_line_ids': [],
            'match_type': 'none',
        }
        if not details['line_names']:
            return 0.0, False, notes, details

        history_lines = self._find_historical_name_move_lines(line, partner, product=product)
        details['historical_lines_checked'] = len(history_lines)
        best_score = 0.0
        best_line = False
        best_match_type = False
        for history_line in history_lines[:10]:
            history_name = self._normalize_plain_name(getattr(history_line, 'name', False))
            if not history_name:
                continue
            for line_name in details['line_names']:
                if line_name == history_name:
                    score = 0.90
                    match_type = 'exact'
                elif (
                    self._is_safe_plain_name_for_substring(line_name)
                    and (line_name in history_name or history_name in line_name)
                ):
                    score = 0.88
                    match_type = 'substring'
                else:
                    continue
                if self._historical_line_partner_matches(history_line, partner):
                    score += 0.02
                if score > best_score:
                    best_score = score
                    best_line = history_line
                    best_match_type = match_type

        if best_line:
            details['match_type'] = best_match_type
            details['historical_line_ids'] = [getattr(best_line, 'id', False)]
            notes.append(
                'Matched by historical bill line name: line_id=%s; name=%s.'
                % (
                    getattr(best_line, 'id', False) or 'unknown',
                    getattr(best_line, 'name', False) or '',
                )
            )
            return (
                self._clamp_score(best_score),
                'historical_line_name_exact'
                if best_match_type == 'exact'
                else 'historical_line_name_substring',
                notes,
                details,
            )
        return 0.0, False, notes, details

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
        for brand in ('gainta', 'holybro'):
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

    def _score_technical_move_line_match(self, line, move_line, partner):
        product = move_line.product_id
        line_profile = self._line_technical_profile(line)
        candidate_profile = self._move_line_technical_profile(move_line, product, partner)
        return self._score_technical_profiles(
            line_profile,
            candidate_profile,
            'document line/product/supplier text',
        )

    def _score_technical_product_match(self, line, product, partner):
        line_profile = self._line_technical_profile(line)
        product_profile = self._product_technical_profile(product, partner)
        product_score, product_method, product_notes, product_details = (
            self._score_technical_profiles(
                line_profile,
                product_profile,
                'product',
            )
        )

        history_best = {
            'score': 0.0,
            'method': False,
            'notes': [],
            'details': {},
        }
        history_lines = self._find_historical_move_lines(line, partner, product=product)
        for history_line in history_lines[:10]:
            history_profile = self._technical_profile_from_values([
                getattr(history_line, 'name', False),
            ])
            history_score, history_method, history_notes, history_details = (
                self._score_technical_profiles(
                    line_profile,
                    history_profile,
                    'historical account.move.line.name',
                    historical=True,
                )
            )
            if history_score:
                same_partner = self._historical_line_partner_matches(history_line, partner)
                if same_partner:
                    history_score += 0.03
                    history_notes.append('Historical line belongs to the same vendor partner.')
                history_notes.append(
                    'Historical account.move.line match: line_id=%s; name=%s.'
                    % (
                        getattr(history_line, 'id', False) or 'unknown',
                        getattr(history_line, 'name', False) or '',
                    )
                )
            if history_score > history_best['score']:
                history_best = {
                    'score': history_score,
                    'method': history_method,
                    'notes': history_notes,
                    'details': history_details,
                }

        product_conflict_note = self._technical_model_conflict_note(
            line_profile,
            product_profile,
        )
        if product_conflict_note and history_best['score']:
            history_best['notes'].append(
                '%s Historical supplier text is the reason this product is still proposed.'
                % product_conflict_note
            )

        if history_best['score'] > product_score:
            notes = history_best['notes']
            details = history_best['details']
            details['historical_lines_checked'] = len(history_lines)
            return (
                self._clamp_score(history_best['score']),
                history_best['method'],
                notes,
                details,
            )

        if product_conflict_note and product_score:
            product_notes.append(product_conflict_note)

        product_details['historical_lines_checked'] = len(history_lines)
        return (
            self._clamp_score(product_score),
            product_method,
            product_notes,
            product_details,
        )

    def _score_technical_profiles(
        self,
        line_profile,
        candidate_profile,
        source_label,
        historical=False,
    ):
        notes = []
        details = {
            'source': source_label,
            'line_full_codes': line_profile['full_codes'],
            'line_segments': line_profile['segments'],
            'candidate_full_codes': candidate_profile['full_codes'],
            'candidate_segments': candidate_profile['segments'],
            'matched_segments': [],
            'unmatched_segments': [],
        }
        if not line_profile['segments'] and not line_profile['full_codes']:
            return 0.0, False, notes, details
        if not candidate_profile['segments'] and not candidate_profile['full_codes']:
            return 0.0, False, notes, details

        full_exact = self._find_matching_technical_value(
            line_profile['full_codes'],
            candidate_profile['full_codes'],
        )
        if full_exact:
            score = 1.0
            has_dotted_code = any(
                '.' in str(value)
                for value in full_exact
            )
            if historical:
                method = (
                    'historical_dotted_technical_code_exact'
                    if has_dotted_code
                    else 'historical_technical_code_exact'
                )
            else:
                method = (
                    'dotted_technical_code_exact'
                    if has_dotted_code
                    else 'technical_code_exact'
                )
            details['matched_full_code'] = full_exact
            details['exact_matches'] = [full_exact]
            notes.append(
                'Exact full technical code match in %s: %s == %s.'
                % (source_label, full_exact[0], full_exact[1])
            )
            return score, method, notes, details

        if line_profile['full_codes'] and candidate_profile['full_codes']:
            details['unmatched_segments'] = list(line_profile['important_segments'])
            notes.append(
                'Full technical codes differ; prefix and segment-only matches are rejected to preserve final model suffixes.'
            )
            return 0.0, False, notes, details

        full_prefix = self._find_contained_technical_value(
            line_profile['full_codes'],
            candidate_profile['full_codes'],
        )
        if full_prefix:
            score = 0.93
            method = 'historical_technical_full_code_prefix' if historical else 'technical_full_code_prefix'
            details['matched_full_code'] = full_prefix
            notes.append(
                'Full technical code prefix/contained match in %s: %s ~= %s.'
                % (source_label, full_prefix[0], full_prefix[1])
            )
            score, notes = self._apply_technical_color_penalty(
                line_profile,
                candidate_profile,
                score,
                notes,
            )
            return score, method, notes, details

        matched_segments = self._matching_technical_segments(
            line_profile['segments'],
            candidate_profile['segments'],
        )
        details['matched_segments'] = [match[0] for match in matched_segments]
        details['unmatched_segments'] = [
            segment
            for segment in line_profile['important_segments']
            if not any(self._technical_values_match(segment, match[0]) for match in matched_segments)
        ]
        base_match = self._find_matching_technical_value(
            line_profile['base_models'],
            candidate_profile['base_models'],
        )
        base_conflict = bool(
            line_profile['base_models']
            and candidate_profile['base_models']
            and not base_match
        )
        important_matches = [
            match
            for match in matched_segments
            if match[0] in line_profile['important_segments']
            and self._normalize_code(match[0]) not in {
                self._normalize_code(base)
                for base in line_profile['base_models']
            }
        ]

        score = 0.0
        method = False
        if base_match and len(important_matches) >= 2:
            score = 0.93
            method = 'technical_base_segments'
            notes.append(
                'Technical base model and multiple segments match in %s: base=%s; segments=%s.'
                % (
                    source_label,
                    base_match[0],
                    ', '.join(match[0] for match in important_matches[:5]),
                )
            )
        elif base_match and important_matches:
            score = 0.90
            method = 'technical_base_segment'
            notes.append(
                'Technical base model and segment match in %s: base=%s; segment=%s.'
                % (source_label, base_match[0], important_matches[0][0])
            )
        elif base_match:
            score = 0.78
            method = 'technical_base_model'
            notes.append('Technical base model matches in %s: %s.' % (source_label, base_match[0]))
        elif len(important_matches) >= 2:
            score = 0.82 if not historical else 0.86
            method = 'historical_technical_segments' if historical else 'technical_segments'
            notes.append(
                'Multiple technical segments match in %s: %s.'
                % (source_label, ', '.join(match[0] for match in important_matches[:5]))
            )
        elif len(important_matches) == 1:
            score = 0.55
            method = 'technical_single_segment'
            notes.append(
                'Only one technical segment matches in %s: %s; not enough for confident match.'
                % (source_label, important_matches[0][0])
            )

        if base_conflict and score and not historical:
            score = min(score, 0.68)
            notes.append(
                'Warning: OCR base model %s differs from product base model %s; product fields alone are not confident.'
                % (
                    ', '.join(line_profile['base_models']),
                    ', '.join(candidate_profile['base_models']),
                )
            )
        elif base_conflict and historical:
            notes.append(
                'Warning: OCR base model differs from product base model, but historical supplier line matched.'
            )

        score, notes = self._apply_technical_color_penalty(
            line_profile,
            candidate_profile,
            score,
            notes,
        )
        return score, method, notes, details

    def _apply_technical_color_penalty(self, line_profile, candidate_profile, score, notes):
        if not score:
            return score, notes
        line_colors = set(line_profile['colors'])
        candidate_colors = set(candidate_profile['colors'])
        if line_colors and candidate_colors and not line_colors & candidate_colors:
            score -= 0.03
            notes.append(
                'Small penalty: OCR color/variant %s differs from candidate %s.'
                % (', '.join(sorted(line_colors)), ', '.join(sorted(candidate_colors)))
            )
        return score, notes

    def _technical_model_conflict_note(self, line_profile, product_profile):
        if not line_profile['base_models'] or not product_profile['base_models']:
            return False
        if self._find_matching_technical_value(
            line_profile['base_models'],
            product_profile['base_models'],
        ):
            return False
        return (
            'Warning: OCR base model %s differs from product base model %s.'
            % (
                ', '.join(line_profile['base_models']),
                ', '.join(product_profile['base_models']),
            )
        )

    def _has_conflicting_full_technical_codes(self, technical_details, method=False):
        if method in (
            'technical_code_exact',
            'dotted_technical_code_exact',
            'historical_technical_code_exact',
            'historical_dotted_technical_code_exact',
        ):
            return False
        if method and str(method).startswith('historical_technical'):
            return False
        line_codes = technical_details.get('line_full_codes') or []
        candidate_codes = technical_details.get('candidate_full_codes') or []
        if not line_codes or not candidate_codes:
            return False
        if technical_details.get('exact_matches'):
            return False
        matched_full = technical_details.get('matched_full_code')
        if matched_full and self._technical_values_match(matched_full[0], matched_full[1]):
            return False
        return True

    def _line_technical_profile(self, line):
        return self._technical_profile_from_values([
            getattr(line, 'supplier_product_code', False),
            getattr(line, 'supplier_product_name', False),
            getattr(line, 'description', False),
            getattr(line, 'note', False),
            getattr(line, 'source_columns', False),
        ])

    def _product_technical_profile(self, product, partner):
        values = [
            getattr(product, 'default_code', False),
            getattr(product, 'barcode', False),
            getattr(product, 'display_name', False),
            getattr(product, 'name', False),
        ]
        for seller in self._product_sellers(product, partner):
            values.extend([
                getattr(seller, 'product_code', False),
                getattr(seller, 'product_name', False),
                getattr(seller, 'name', False),
            ])
        return self._technical_profile_from_values(values)

    def _move_line_technical_profile(self, move_line, product, partner):
        values = [
            getattr(move_line, 'name', False),
            getattr(product, 'default_code', False),
            getattr(product, 'barcode', False),
            getattr(product, 'display_name', False),
            getattr(product, 'name', False),
        ]
        for seller in self._product_sellers(product, partner):
            values.extend([
                getattr(seller, 'product_code', False),
                getattr(seller, 'product_name', False),
                getattr(seller, 'name', False),
            ])
        return self._technical_profile_from_values(values)

    def _technical_profile_from_values(self, values):
        full_codes = []
        segments = []
        ignored = []
        first_segments = []
        for value in values:
            prepared = self._prepare_code_text(value)
            if not prepared:
                continue
            extracted_full_codes = self._extract_full_technical_codes(prepared)
            full_codes.extend(extracted_full_codes)
            for full_code in extracted_full_codes:
                parts = [part for part in re.split(r'[-/\s.]+', full_code) if part]
                if parts:
                    first_segments.append(parts[0])
                for part in parts:
                    self._add_technical_segment(
                        part,
                        segments,
                        ignored,
                        allow_numeric_variant=True,
                    )
            for token in self._extract_technical_tokens(prepared):
                self._add_technical_segment(token, segments, ignored)

        full_codes = self._unique_technical_values(full_codes)
        segments = self._unique_technical_values(segments)
        ignored = self._unique_technical_values(ignored)
        colors = [
            segment
            for segment in segments
            if self._normalize_code(segment) in self.TECHNICAL_COLOR_TOKENS
        ]
        important_segments = [
            segment
            for segment in segments
            if self._normalize_code(segment) not in self.TECHNICAL_COLOR_TOKENS
        ]
        base_models = self._unique_technical_values(
            segment
            for segment in first_segments + segments
            if self._looks_like_base_model_segment(segment)
        )
        return {
            'full_codes': full_codes,
            'segments': segments,
            'important_segments': important_segments,
            'base_models': base_models,
            'colors': colors,
            'ignored': ignored,
            'variant_keys': {
                value: sorted(self._technical_variant_keys(value))
                for value in full_codes + segments
            },
        }

    def _extract_full_technical_codes(self, prepared_text):
        codes = []
        for code in TechnicalCodeNormalizer.extract(prepared_text):
            if self._looks_like_code(code):
                codes.append(code)
        return codes

    def _extract_technical_tokens(self, prepared_text):
        tokens = []
        patterns = (
            r'\b[A-Z]{1,12}\d[A-Z0-9]*\b',
            r'\b(?:RPI|CM4|PM03D|BLACK|WHITE|I0|IO|M12)\b',
        )
        for pattern in patterns:
            for match in re.findall(pattern, prepared_text or ''):
                token = match.strip(':-.,;')
                if token:
                    tokens.append(token)
        return tokens

    def _add_technical_segment(
        self,
        segment,
        segments,
        ignored,
        allow_numeric_variant=False,
    ):
        if not segment:
            return
        normalized = self._normalize_code(segment)
        if (
            normalized in ('10', 'i0', 'io')
            and allow_numeric_variant
        ):
            segments.append(segment)
            return
        if self._is_low_value_technical_segment(segment):
            ignored.append(segment)
            return
        segments.append(segment)

    def _is_low_value_technical_segment(self, segment):
        normalized = self._normalize_code(segment)
        if not normalized:
            return True
        if normalized in self.TECHNICAL_MODEL_TOKEN_ALLOWLIST:
            return False
        if normalized in self.TECHNICAL_COLOR_TOKENS:
            return False
        if normalized in self.LOW_VALUE_CODE_TOKENS:
            return True
        if normalized.isdigit():
            return True
        for pattern in self.LOW_VALUE_CODE_PATTERNS:
            if pattern.match(normalized):
                return True
        has_alpha = any(char.isalpha() for char in normalized)
        has_digit = any(char.isdigit() for char in normalized)
        if has_alpha and has_digit and len(normalized) >= 3:
            return False
        return len(normalized) < 4

    def _looks_like_base_model_segment(self, segment):
        normalized = self._normalize_code(segment).upper()
        if len(normalized) < 5:
            return False
        if not any(char.isalpha() for char in normalized):
            return False
        if not any(char.isdigit() for char in normalized):
            return False
        if normalized in {'ADF16KM', 'ADF28K', 'PM03D'}:
            return False
        if normalized.startswith(('ADF', 'PM')):
            return False
        return True

    def _prepare_code_text(self, value):
        if not value:
            return ''
        return TechnicalCodeNormalizer.normalize(value)

    def _unique_technical_values(self, values):
        result = []
        seen = set()
        for value in values:
            for key in self._technical_variant_keys(value):
                normalized = key
                break
            else:
                normalized = self._normalize_code(value).upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(value)
        return result

    def _technical_variant_keys(self, value):
        normalized = TechnicalCodeNormalizer.key(value)
        if not normalized:
            return set()
        variants = {normalized}
        changed = True
        while changed:
            changed = False
            for variant in list(variants):
                for source, target in (
                    ('I0', '10'),
                    ('IO', '10'),
                    ('10', 'I0'),
                    ('10', 'IO'),
                ):
                    if source in variant:
                        new_variant = variant.replace(source, target)
                        if new_variant not in variants:
                            variants.add(new_variant)
                            changed = True
        return variants

    def _technical_values_match(self, left, right):
        return bool(self._technical_variant_keys(left) & self._technical_variant_keys(right))

    def _find_matching_technical_value(self, left_values, right_values):
        for left in left_values:
            for right in right_values:
                if self._technical_values_match(left, right):
                    return left, right
        return False

    def _find_contained_technical_value(self, left_values, right_values):
        for left in left_values:
            left_keys = self._technical_variant_keys(left)
            for right in right_values:
                right_keys = self._technical_variant_keys(right)
                if any(
                    left_key in right_key or right_key in left_key
                    for left_key in left_keys
                    for right_key in right_keys
                    if len(left_key) >= 5 and len(right_key) >= 5
                ):
                    return left, right
        return False

    def _matching_technical_segments(self, line_segments, candidate_segments):
        matches = []
        for line_segment in line_segments:
            for candidate_segment in candidate_segments:
                if self._technical_values_match(line_segment, candidate_segment):
                    matches.append((line_segment, candidate_segment))
                    break
        return matches

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

    def _bundle_product_note(self, line, product):
        raw_product_text = ' '.join(
            str(value)
            for value in (
                getattr(product, 'display_name', False),
                getattr(product, 'name', False),
            )
            if value
        )
        product_text = self._normalize_text(raw_product_text)
        if not (
            '+' in raw_product_text
            or 'комплект' in product_text
            or 'набір' in product_text
            or 'набор' in product_text
            or 'bundle' in product_text
            or 'kit' in product_text
        ):
            return False

        line_tokens = self._meaningful_tokens(self._ocr_line_text(line))
        line_tokens.update(
            self._normalize_code(code)
            for code in self._line_codes(line)
            if self._normalize_code(code) and not self._is_low_value_code(code)
        )
        product_tokens = self._meaningful_tokens(raw_product_text)
        product_tokens.update(
            self._normalize_code(code)
            for code in self._product_candidate_codes(product, partner=False)
            if self._normalize_code(code) and not self._is_low_value_code(code)
        )
        overlap = sorted(token for token in line_tokens & product_tokens if token)
        if not overlap:
            return False
        return 'Possible bundle product found; shared tokens: %s. Review merge action manually.' % (
            ', '.join(overlap[:6])
        )

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
        supplier_article_codes = {
            SupplierArticleNormalizer.normalize(article)
            for article in self._line_supplier_articles(line)
            if SupplierArticleNormalizer.normalize(article)
        }
        for code in self._line_codes(line):
            normalized_article = SupplierArticleNormalizer.normalize(code)
            if self._is_low_value_code(code) and normalized_article not in supplier_article_codes:
                notes.append(
                    'Ignored low-value OCR code fragment %s for generic code-token matching; '
                    'exact vendor supplierinfo article matching is handled separately.'
                    % code
                )
                continue

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

        if (
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
            if include_move_lines and winner.get('move_line'):
                values['move_line_id'] = winner['move_line'].id
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
        if values['match_status'] == 'not_found':
            technical_message = self._unmatched_technical_code_message(line)
            if technical_message:
                values['match_note'] = self._append_text(values['match_note'], technical_message)
        values['match_summary'] = self._build_match_summary(
            line,
            values,
            winner_candidate,
            best,
            visible_candidates,
        )
        line.write(values)
        if values['match_status'] in ('ambiguous', 'not_found'):
            self._log_technical_match(line, candidates)
            self._log_name_model_match(line, candidates, values['match_status'])
        if values['match_status'] == 'not_found':
            self._log_unmatched_supplier_article(line, candidates)

    def _write_full_document_assignment_results(self, line_results):
        assigned = {}
        used_product_ids = set()

        ordered_results = sorted(
            line_results.values(),
            key=lambda item: (item['line'].sequence, item['line'].id),
        )
        for item in ordered_results:
            candidates = self._sorted_scored_candidates(item['candidates'])
            visible_candidates = self._visible_candidates(candidates)
            locked_candidates = [
                candidate
                for candidate in visible_candidates
                if self._is_locked_exact_candidate(candidate)
            ]
            locked_product_ids = {
                candidate['product'].id
                for candidate in locked_candidates
                if candidate.get('product')
            }
            if len(locked_product_ids) == 1:
                winner = locked_candidates[0]
                assigned[item['line'].id] = {
                    'winner': winner,
                    'reason': 'Locked exact article/code match selected before fuzzy assignment.',
                }
                used_product_ids.add(winner['product'].id)
            elif locked_candidates:
                assigned[item['line'].id] = {
                    'status': 'ambiguous',
                    'reason': 'Several locked exact article/code candidates were found.',
                }

        remaining = [
            item for item in ordered_results if item['line'].id not in assigned
        ]
        remaining.sort(
            key=lambda item: (
                -(self._sorted_scored_candidates(item['candidates'])[0]['score']
                  if self._sorted_scored_candidates(item['candidates']) else 0.0),
                item['line'].sequence,
                item['line'].id,
            )
        )
        for item in remaining:
            candidates = self._sorted_scored_candidates(item['candidates'])
            visible_candidates = self._visible_candidates(candidates)
            available_visible = [
                candidate
                for candidate in visible_candidates
                if candidate['product'].id not in used_product_ids
            ]
            if (
                available_visible
                and (
                    len(available_visible) == 1
                    and available_visible[0]['score'] >= self.MATCHED_THRESHOLD
                    or self._can_match_best_gap(available_visible, True)
                )
            ):
                winner = available_visible[0]
                assigned[item['line'].id] = {
                    'winner': winner,
                    'reason': 'Global one-to-one fuzzy assignment selected this product.',
                }
                used_product_ids.add(winner['product'].id)
            elif visible_candidates:
                assigned[item['line'].id] = {
                    'status': 'ambiguous',
                    'reason': (
                        'No safe one-to-one product assignment was possible; '
                        'the best product may already be used by another OCR row.'
                    ),
                }
            else:
                assigned[item['line'].id] = {
                    'status': 'not_found',
                    'reason': 'No candidate reached the product candidate threshold.',
                }

        for item in ordered_results:
            assignment = assigned.get(item['line'].id, {})
            self._write_full_assignment_result(
                item['line'],
                item['candidates'],
                diagnostics=item.get('diagnostics'),
                winner_candidate=assignment.get('winner'),
                forced_status=assignment.get('status'),
                assignment_reason=assignment.get('reason'),
            )

    def _sorted_scored_candidates(self, candidates):
        candidates = [
            candidate
            for candidate in candidates
            if candidate.get('product') and candidate.get('score', 0.0) > 0.0
        ]
        candidates.sort(key=lambda candidate: candidate['score'], reverse=True)
        return candidates

    def _visible_candidates(self, candidates):
        return [
            candidate
            for candidate in candidates
            if candidate['score'] >= self.CANDIDATE_THRESHOLD
        ]

    def _is_locked_exact_candidate(self, candidate):
        return (
            candidate.get('product')
            and (candidate.get('score') or 0.0) >= 0.999
            and candidate.get('method') in self.LOCKED_EXACT_METHODS
        )

    def _write_full_assignment_result(
        self,
        line,
        candidates,
        diagnostics=False,
        winner_candidate=False,
        forced_status=False,
        assignment_reason=False,
    ):
        candidates = self._sorted_scored_candidates(candidates)
        visible_candidates = self._visible_candidates(candidates)
        best = candidates[0] if candidates else False
        values = {
            'matched_product_id': False,
            'move_line_id': False,
            'candidate_product_ids': [(6, 0, self._candidate_product_ids(visible_candidates))],
            'candidate_move_line_ids': [(6, 0, [])],
            'match_score': best['score'] if best else 0.0,
            'match_method': best['method'] if best else False,
        }

        if winner_candidate:
            values.update({
                'match_status': 'matched',
                'matched_product_id': winner_candidate['product'].id,
                'match_score': winner_candidate['score'],
                'match_method': winner_candidate['method'],
            })
            winner_candidate.setdefault('notes', []).append(assignment_reason or '')
        elif forced_status:
            values['match_status'] = forced_status
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
        if assignment_reason:
            values['match_note'] = self._append_text(
                values['match_note'],
                'Global assignment: %s' % assignment_reason,
            )
        if values['match_status'] == 'not_found':
            technical_message = self._unmatched_technical_code_message(line)
            if technical_message:
                values['match_note'] = self._append_text(values['match_note'], technical_message)
        values['match_summary'] = self._build_match_summary(
            line,
            values,
            winner_candidate,
            best,
            visible_candidates,
        )
        line.write(values)
        if values['match_status'] in ('ambiguous', 'not_found'):
            self._log_technical_match(line, candidates)
            self._log_name_model_match(line, candidates, values['match_status'])
        if values['match_status'] == 'not_found':
            self._log_unmatched_supplier_article(line, candidates)

    def _log_unmatched_supplier_article(self, line, candidates):
        articles = self._line_supplier_articles(line)
        if not articles:
            return False
        partner = self._get_job_partner(line.job_id)
        candidate_codes = []
        for candidate in candidates or []:
            candidate_codes.extend(candidate.get('candidate_codes') or [])
            product = candidate.get('product')
            if product:
                for seller in self._product_sellers(product, partner):
                    code = getattr(seller, 'product_code', False)
                    if code:
                        candidate_codes.append(code)
        candidate_codes = list(dict.fromkeys(str(code) for code in candidate_codes if code))
        for article in articles:
            _logger.info(
                'OCR supplier code raw=%s normalized=%s supplier=%s candidate supplier codes=%s mode=%s',
                article,
                SupplierArticleNormalizer.normalize(article),
                getattr(partner, 'display_name', False) or getattr(partner, 'name', False) or 'none',
                ', '.join(candidate_codes[:30]) if candidate_codes else 'none',
                getattr(line.job_id, 'mode', False) or 'unknown',
            )
        return True

    def _log_technical_match(self, line, candidates):
        line_profile = self._line_technical_profile(line)
        ocr_codes = line_profile.get('full_codes') or []
        if not ocr_codes:
            return False
        candidate_codes = []
        exact_matches = []
        for candidate in candidates or []:
            details = candidate.get('technical_details') or {}
            candidate_codes.extend(details.get('candidate_full_codes') or [])
            exact_matches.extend(
                '%s=%s' % (left, right)
                for left, right in (details.get('exact_matches') or [])
            )
            matched_full = details.get('matched_full_code')
            if matched_full and matched_full not in (details.get('exact_matches') or []):
                exact_matches.append('%s~%s' % (matched_full[0], matched_full[1]))
        _logger.info(
            'Gemini OCR technical match: ocr_codes=%s candidate_codes=%s exact_matches=%s mode=%s',
            ', '.join(dict.fromkeys(ocr_codes)) or 'none',
            ', '.join(dict.fromkeys(candidate_codes)) or 'none',
            ', '.join(dict.fromkeys(exact_matches)) or 'none',
            getattr(line.job_id, 'mode', False) or 'unknown',
        )
        return True

    def _log_name_model_match(self, line, candidates, decision):
        line_values = self._line_name_model_values(line)
        if not line_values:
            return False
        ocr_profile = self._name_model_token_profile(line_values)
        if not ocr_profile['tokens']:
            return False
        technical_profile = self._line_technical_profile(line)
        scored_candidates = sorted(
            [
                candidate
                for candidate in candidates or []
                if candidate.get('product')
            ],
            key=lambda candidate: candidate.get('score') or 0.0,
            reverse=True,
        )
        second_score = (
            scored_candidates[1].get('score') or 0.0
            if len(scored_candidates) > 1
            else 0.0
        )
        for candidate in scored_candidates[:10]:
            product = candidate.get('product')
            details = candidate.get('name_model_details') or {}
            if not product or not details:
                continue
            technical_details = candidate.get('technical_details') or {}
            _logger.info(
                'Gemini OCR name match: mode=%s ocr_name="%s" '
                'ocr_technical_codes=%s ocr_tokens=%s ocr_meaningful_tokens=%s '
                'candidate_count=%s candidate=%s/%s candidate_codes=%s '
                'candidate_technical_codes=%s candidate_tokens=%s '
                'matched_tokens=%s matched_phrases=%s generic_matches=%s '
                'conflicting_tokens=%s token_weight_score=%.2f phrase_score=%.2f '
                'final_score=%.2f second_score=%.2f method=%s decision=%s reason=%s',
                getattr(line.job_id, 'mode', False) or 'unknown',
                ' | '.join(str(value) for value in line_values if value),
                ', '.join(technical_profile.get('full_codes') or []) or 'none',
                ', '.join(ocr_profile['tokens']) or 'none',
                ', '.join(ocr_profile['meaningful_tokens']) or 'none',
                len(scored_candidates),
                getattr(product, 'id', False) or 'unknown',
                getattr(product, 'display_name', False)
                or getattr(product, 'name', False)
                or '',
                ', '.join(candidate.get('candidate_codes') or []) or 'none',
                ', '.join(technical_details.get('candidate_full_codes') or []) or 'none',
                ', '.join(details.get('candidate_tokens') or []) or 'none',
                ', '.join(details.get('matched_tokens') or []) or 'none',
                ', '.join(details.get('matched_phrases') or []) or 'none',
                ', '.join(details.get('generic_matches') or []) or 'none',
                ', '.join(details.get('conflicting_tokens') or []) or 'none',
                details.get('token_weight_score') or 0.0,
                details.get('phrase_score') or 0.0,
                candidate.get('score') or 0.0,
                second_score,
                candidate.get('method') or 'none',
                decision,
                '; '.join(candidate.get('notes') or []) or 'none',
            )
        return True

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
            technical_message = self._unmatched_technical_code_message(line)
            if technical_message:
                return technical_message
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

    def _unmatched_technical_code_message(self, line):
        code = self._primary_technical_code(line)
        if not code:
            return False
        return _('Не вдалося зіставити товар за технічним кодом «%(code)s».') % {
            'code': code,
        }

    def _primary_technical_code(self, line):
        profile = self._line_technical_profile(line)
        if profile['full_codes']:
            return profile['full_codes'][0]
        return False

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
        technical_profile = self._line_technical_profile(line)
        supplier_articles = self._line_supplier_articles(line)
        create_line_count = len(self._partial_create_lines(job))
        diagnostics = [
            'Partial document matching diagnostics:',
            'Job mode: %s.' % job.mode,
            '%s: %s.' % (
                line_source.get('document_id_label', 'Document ID'),
                line_source.get('document_id', 'none'),
            ),
            'OCR create_line rows: %s.' % create_line_count,
            'Supplier articles raw/normalized: %s.' % (
                ', '.join(
                    '%s -> %s' % (article, SupplierArticleNormalizer.normalize(article))
                    for article in supplier_articles
                ) if supplier_articles else 'none'
            ),
            'Extracted supplier/internal codes: %s.' % (
                ', '.join(extracted_codes) if extracted_codes else 'none'
            ),
            'Technical full codes: %s.' % (
                ', '.join(technical_profile['full_codes'])
                if technical_profile['full_codes']
                else 'none'
            ),
            '%s: %s.' % (line_source.get('source_total_label'), len(invoice_lines)),
            '%s: %s.' % (line_source.get('source_product_label'), len(invoice_product_lines)),
            '%s: %s.' % (line_source.get('fallback_total_label'), len(line_ids)),
            '%s: %s.' % (line_source.get('fallback_product_label'), len(line_product_lines)),
            'Line source used: %s.' % line_source['source'],
            'Single-line fallback available: %s.' % (
                'yes' if create_line_count == 1 and len(move_lines) == 1 else 'no'
            ),
            self._format_line_source_reason(line_source),
            'Recognized values: supplier_product_code=%s; supplier_product_name=%s; quantity=%s; price_unit=%s; amount_untaxed=%s.'
            % (
                getattr(line, 'supplier_product_code', False) or 'none',
                getattr(line, 'supplier_product_name', False) or 'none',
                getattr(line, 'quantity', False) or 'none',
                self._recognized_price_unit(line) or 'none',
                self._recognized_subtotal(line) or 'none',
            ),
            'Candidate scope: candidates are limited to product lines of the %s.' % (
                line_source.get('candidate_scope_label', 'current document')
            ),
            'Methods tried: supplier_product_code, supplier_product_name, default_code, barcode, product name, business line name, supplierinfo of candidate products.',
            'Fields compared for matching: product/default/barcode/display names, business line name, supplierinfo code/name of products already on this document.',
            'Quantity, price_unit, subtotal, and total are ignored for line selection because OCR will overwrite them after Apply.',
        ]
        if not move_lines:
            diagnostics.append(
                'No product lines are available on the current document for partial matching.'
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
                    ' on %s=%s' % (
                        line_source.get('line_field', 'line_id'),
                        move_line.id,
                    ) if move_line else '',
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
        if line_source['source'] == 'order_line':
            return (
                'Used purchase order/RFQ order_line with %s product lines.'
                % len(line_source['invoice_product_lines'])
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
            '- business_line_id=%s; product_id=%s; score=%.2f; method=%s; decision=%s.'
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
            '  business_line.name=%s' % (getattr(move_line, 'name', False) or ''),
            '  display_type=%s; account_id=%s; account_type=%s'
            % (
                getattr(move_line, 'display_type', False) or '',
                getattr(getattr(move_line, 'account_id', False), 'id', False) or '',
                getattr(getattr(move_line, 'account_id', False), 'account_type', False) or '',
            ),
            '  quantity=%s; price_unit=%s; price_subtotal=%s'
            % (
                getattr(move_line, 'quantity', False) or getattr(move_line, 'product_qty', False),
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
        technical_details = candidate.get('technical_details') or {}
        if technical_details:
            values.extend([
                '  OCR technical full codes=%s; candidate technical full codes=%s'
                % (
                    ', '.join(technical_details.get('line_full_codes') or []) or 'none',
                    ', '.join(technical_details.get('candidate_full_codes') or []) or 'none',
                ),
                '  matched technical segments=%s; unmatched OCR segments=%s'
                % (
                    ', '.join(technical_details.get('matched_segments') or []) or 'none',
                    ', '.join(technical_details.get('unmatched_segments') or []) or 'none',
                ),
            ])
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
        technical_profile = self._line_technical_profile(line)
        historical_lines = self._find_historical_move_lines(line, partner)
        name_search = self._product_name_search_snapshot(line)
        historical_name_lines = self._find_historical_name_move_lines(line, partner)
        supplierinfo_candidates = self._find_supplierinfos_by_codes(
            profile['primary_codes'],
            partner,
            allow_low_value=True,
        )
        supplier_articles = self._line_supplier_articles(line)
        supplierinfo_exact = [
            seller
            for seller in supplierinfo_candidates
            if any(
                SupplierArticleNormalizer.equals(code, getattr(seller, 'product_code', False))
                for code in supplier_articles
            )
        ]
        diagnostics = [
            '%s matching diagnostics:' % mode_label,
            'Job mode: %s.' % job.mode,
            'Move ID: %s.' % (job.move_id.id if getattr(job, 'move_id', False) else 'none'),
            'Vendor partner used: %s.' % (partner.id if partner else 'none'),
            'Supplier articles raw/normalized: %s.' % (
                ', '.join(
                    '%s -> %s' % (article, SupplierArticleNormalizer.normalize(article))
                    for article in supplier_articles
                ) if supplier_articles else 'none'
            ),
            'Primary codes: %s.' % (
                ', '.join(profile['primary_codes']) if profile['primary_codes'] else 'none'
            ),
            'Technical full codes: %s.' % (
                ', '.join(technical_profile['full_codes'])
                if technical_profile['full_codes']
                else 'none'
            ),
            'Technical segments: %s.' % (
                ', '.join(technical_profile['segments'])
                if technical_profile['segments']
                else 'none'
            ),
            'Technical base models: %s.' % (
                ', '.join(technical_profile['base_models'])
                if technical_profile['base_models']
                else 'none'
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
            'Historical account.move.line candidates count: %s.' % len(historical_lines),
            'Historical account.move.line name candidates count: %s.' % len(historical_name_lines),
            'Product candidates found: %s.' % len(products),
            'Product name fallback normalized names: %s.' % (
                ', '.join(name_search['normalized_names'])
                if name_search['normalized_names']
                else 'none'
            ),
            'Tried exact normalized product name search: yes.',
            'Product name search purchase_ok=True candidates: %s.' % name_search['purchase_ok_count'],
            'Product name search fallback without purchase_ok candidates: %s.' % name_search['fallback_count'],
            'Exact normalized product name candidates: %s.' % (
                ', '.join(name_search['exact_candidate_names'])
                if name_search['exact_candidate_names']
                else 'none'
            ),
            'Recognized values: supplier_product_code=%s; supplier_product_name=%s; quantity=%s; price_unit=%s; amount_untaxed=%s.'
            % (
                getattr(line, 'supplier_product_code', False) or 'none',
                getattr(line, 'supplier_product_name', False) or 'none',
                getattr(line, 'quantity', False) or 'none',
                self._recognized_price_unit(line) or 'none',
                self._recognized_subtotal(line) or 'none',
            ),
            'Methods tried: supplierinfo code/name, default_code, barcode, primary code-token, technical full code/segment combinations, historical account.move.line.name, meaningful secondary token boost, brand/dimension boosts, internal product penalties, fuzzy/token name similarity.',
        ]
        if (
            not profile['primary_codes']
            and not profile['secondary_codes']
            and not technical_profile['full_codes']
            and not technical_profile['segments']
        ):
            diagnostics.append(
                'No supplier code/default_code/barcode/technical code found; plain product-name fallback is the main matching signal.'
            )
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
        technical_details = candidate.get('technical_details') or {}
        if technical_details:
            values.extend([
                '  technical_source=%s; historical_lines_checked=%s'
                % (
                    technical_details.get('source') or 'none',
                    technical_details.get('historical_lines_checked') or 0,
                ),
                '  OCR technical full codes=%s; OCR segments=%s'
                % (
                    ', '.join(technical_details.get('line_full_codes') or []) or 'none',
                    ', '.join(technical_details.get('line_segments') or []) or 'none',
                ),
                '  candidate technical full codes=%s; candidate segments=%s'
                % (
                    ', '.join(technical_details.get('candidate_full_codes') or []) or 'none',
                    ', '.join(technical_details.get('candidate_segments') or []) or 'none',
                ),
                '  matched technical segments=%s; unmatched OCR segments=%s'
                % (
                    ', '.join(technical_details.get('matched_segments') or []) or 'none',
                    ', '.join(technical_details.get('unmatched_segments') or []) or 'none',
                ),
            ])
        name_details = candidate.get('name_details') or {}
        if name_details:
            values.append(
                '  plain_name_match=%s; OCR names=%s; product names=%s'
                % (
                    name_details.get('match_type') or 'none',
                    ', '.join(name_details.get('line_names') or []) or 'none',
                    ', '.join(name_details.get('product_names') or []) or 'none',
                )
            )
        name_model_details = candidate.get('name_model_details') or {}
        if name_model_details:
            values.extend([
                '  name_model_source=%s; OCR tokens=%s; meaningful OCR tokens=%s; candidate tokens=%s'
                % (
                    name_model_details.get('source') or 'none',
                    ', '.join(name_model_details.get('ocr_tokens') or []) or 'none',
                    ', '.join(name_model_details.get('ocr_meaningful_tokens') or []) or 'none',
                    ', '.join(name_model_details.get('candidate_tokens') or []) or 'none',
                ),
                '  weighted name match: matched=%s; phrases=%s; generic=%s; conflicts=%s; token_score=%.2f; phrase_score=%.2f; conflict_penalty=%.2f'
                % (
                    ', '.join(name_model_details.get('matched_tokens') or []) or 'none',
                    ', '.join(name_model_details.get('matched_phrases') or []) or 'none',
                    ', '.join(name_model_details.get('generic_matches') or []) or 'none',
                    ', '.join(name_model_details.get('conflicting_tokens') or []) or 'none',
                    name_model_details.get('token_weight_score') or 0.0,
                    name_model_details.get('phrase_score') or 0.0,
                    name_model_details.get('conflict_penalty') or 0.0,
                ),
            ])
        history_name_details = candidate.get('history_name_details') or {}
        if history_name_details:
            values.append(
                '  historical_name_match=%s; historical_lines_checked=%s; historical_line_ids=%s'
                % (
                    history_name_details.get('match_type') or 'none',
                    history_name_details.get('historical_lines_checked') or 0,
                    ', '.join(str(line_id) for line_id in history_name_details.get('historical_line_ids') or []) or 'none',
                )
            )
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

    def _find_full_bill_name_products(self, line):
        terms = self._plain_name_search_terms(line)
        if not terms:
            return []

        purchase_products = []
        for term in terms:
            for product in self._search_products_name_like(term, purchase_ok=True):
                self._append_unique(purchase_products, product)
        if purchase_products:
            return purchase_products

        fallback_products = []
        for term in terms:
            for product in self._search_products_name_like(term, purchase_ok=False):
                self._append_unique(fallback_products, product)
        return fallback_products

    def _plain_name_search_terms(self, line):
        terms = []
        for name in self._line_plain_names(line):
            normalized = self._normalize_plain_name(name)
            if not self._is_safe_plain_name_for_search(normalized):
                continue
            terms.append(name)
            terms.append(normalized)
        return list(dict.fromkeys(term for term in terms if term))

    def _product_name_search_snapshot(self, line):
        terms = self._plain_name_search_terms(line)
        purchase_products = []
        fallback_products = []
        for term in terms:
            for product in self._search_products_name_like(term, purchase_ok=True):
                self._append_unique(purchase_products, product)
            for product in self._search_products_name_like(term, purchase_ok=False):
                self._append_unique(fallback_products, product)

        exact_products = []
        for product in purchase_products or fallback_products:
            score, _method, _notes, details = self._score_plain_product_name_match(line, product)
            if score >= 0.95 and details.get('match_type') == 'exact':
                self._append_unique(exact_products, product)

        return {
            'normalized_names': self._line_normalized_plain_names(line),
            'purchase_ok_count': len(purchase_products),
            'fallback_count': len(fallback_products),
            'exact_candidate_names': [
                self._short_product_name(product)
                for product in exact_products[:10]
            ],
        }

    def _find_historical_name_move_lines(self, line, partner, product=False):
        terms = self._plain_name_search_terms(line)
        if not terms:
            return []

        history_lines = []
        for term in terms:
            for history_line in self._search_historical_move_lines(
                term,
                partner=partner,
                product=product,
                normalize_as_name=True,
            ):
                self._append_unique(history_lines, history_line)
            if partner:
                for history_line in self._search_historical_move_lines(
                    term,
                    partner=False,
                    product=product,
                    normalize_as_name=True,
                ):
                    self._append_unique(history_lines, history_line)
        return history_lines[:100]

    def _find_historical_move_lines(self, line, partner, product=False):
        terms = self._historical_search_terms(line)
        if not terms:
            return []

        history_lines = []
        for term in terms:
            for search_term in self._code_search_variants(term):
                for history_line in self._search_historical_move_lines(
                    search_term,
                    partner=partner,
                    product=product,
                ):
                    self._append_unique(history_lines, history_line)
                if partner:
                    for history_line in self._search_historical_move_lines(
                        search_term,
                        partner=False,
                        product=product,
                    ):
                        self._append_unique(history_lines, history_line)
        return history_lines[:100]

    def _historical_search_terms(self, line):
        profile = self._line_code_profile(line)
        technical_profile = self._line_technical_profile(line)
        terms = []
        terms.extend(technical_profile['full_codes'])
        terms.extend(profile['primary_codes'])
        terms.extend(
            segment
            for segment in technical_profile['important_segments']
            if not self._is_low_value_technical_segment(segment)
        )
        terms.extend(
            code
            for code in profile['secondary_codes']
            if not self._is_low_value_code(code)
        )
        return self._unique_normalized_codes(terms)[:12]

    def _search_historical_move_lines(
        self,
        term,
        partner=False,
        product=False,
        normalize_as_name=False,
    ):
        if not term:
            return []
        if normalize_as_name:
            if not self._normalize_plain_name(term):
                return []
        elif not self._normalize_code(term):
            return []
        domain = [
            ('product_id', '!=', False),
            ('move_id.move_type', 'in', ('in_invoice', 'in_refund')),
            ('name', 'ilike', term),
        ]
        if partner:
            domain.append(('partner_id', '=', partner.id))
        if product:
            domain.append(('product_id', '=', product.id))
        return list(self.env['account.move.line'].search(domain, limit=50))

    def _historical_line_partner_matches(self, history_line, partner):
        if not partner:
            return False
        line_partner = (
            getattr(history_line, 'partner_id', False)
            or getattr(getattr(history_line, 'move_id', False), 'partner_id', False)
        )
        return bool(
            line_partner
            and getattr(line_partner, 'id', False) == getattr(partner, 'id', False)
        )

    def _find_supplierinfos(self, line, partner):
        sellers = []
        for seller in self._find_supplierinfos_by_articles(
            self._line_supplier_articles(line),
            partner,
        ):
            self._append_unique(sellers, seller)
        supplier_name = getattr(line, 'supplier_product_name', False)
        if supplier_name:
            for seller in self._search_supplierinfos_exact(
                partner,
                product_name=supplier_name,
            ):
                self._append_unique(sellers, seller)
        return sellers

    def _find_supplierinfos_by_articles(self, codes, partner):
        sellers = []
        for code in codes:
            for seller in self._search_supplierinfos_article_exact(partner, code):
                self._append_unique(sellers, seller)
        return sellers

    def _find_supplierinfos_by_codes(self, codes, partner, allow_low_value=False):
        sellers = []
        for code in codes:
            if self._is_low_value_code(code) and not allow_low_value:
                continue
            for search_code in self._code_search_variants(code):
                for seller in self._search_supplierinfos_exact(partner, product_code=search_code):
                    self._append_unique(sellers, seller)
                for seller in self._search_supplierinfos_code_like(search_code, partner):
                    self._append_unique(sellers, seller)
        return sellers

    def _search_supplierinfos_exact(self, partner, product_code=False, product_name=False):
        if not partner or (not product_code and not product_name):
            return []
        if product_code and not product_name:
            return self._search_supplierinfos_article_exact(partner, product_code)
        commercial_partner = self._commercial_partner(partner)
        domain = [('partner_id', '=', commercial_partner.id)]
        if product_code and product_name:
            domain.extend(['|', ('product_code', '=', product_code), ('product_name', '=', product_name)])
        elif product_code:
            domain.append(('product_code', '=', product_code))
        elif product_name:
            domain.append(('product_name', '=', product_name))
        return list(self.env['product.supplierinfo'].search(domain, limit=100))

    def _search_supplierinfos_article_exact(self, partner, product_code):
        if not partner or not product_code:
            return []
        commercial_partner = self._commercial_partner(partner)
        normalized = SupplierArticleNormalizer.normalize(product_code)
        if not normalized:
            return []
        variants = list(dict.fromkeys([
            product_code,
            normalized,
        ] + self._code_search_variants(product_code)))
        sellers = self.env['product.supplierinfo'].search([
            ('partner_id', '=', commercial_partner.id),
            ('product_code', 'in', variants),
        ], limit=100)
        result = [
            seller
            for seller in sellers
            if SupplierArticleNormalizer.equals(product_code, seller.product_code)
        ]
        if result:
            return result

        supplier_sellers = self._supplierinfos_for_partner(commercial_partner)
        return [
            seller
            for seller in supplier_sellers
            if SupplierArticleNormalizer.equals(product_code, seller.product_code)
        ][:100]

    def _search_supplierinfos_like(self, term, partner):
        if not term or not partner:
            return []
        commercial_partner = self._commercial_partner(partner)
        return list(self.env['product.supplierinfo'].search([
            ('partner_id', '=', commercial_partner.id),
            ('product_name', 'ilike', term),
        ], limit=100))

    def _search_supplierinfos_code_like(self, code, partner):
        code = str(code or '').strip()
        if not self._normalize_code(code) or not partner:
            return []
        commercial_partner = self._commercial_partner(partner)
        return list(self.env['product.supplierinfo'].search([
            ('partner_id', '=', commercial_partner.id),
            ('product_code', 'ilike', code),
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

    def _search_products_name_like(self, term, purchase_ok=True):
        if not term:
            return []
        domain = [('name', 'ilike', term)]
        if purchase_ok is True:
            domain.insert(0, ('purchase_ok', '=', True))
        return list(self.env['product.product'].search(domain, limit=100))

    def _search_products_code_like(self, code):
        code = str(code or '').strip()
        if not self._normalize_code(code):
            return []
        return list(self.env['product.product'].search([
            '|',
            '|',
            ('default_code', 'ilike', code),
            ('barcode', 'ilike', code),
            ('name', 'ilike', code),
        ], limit=100))

    def _supplier_code_matches(self, product, partner, code):
        return bool(self._supplier_code_match_info(product, partner, code))

    def _supplier_code_match_info(self, product, partner, code):
        if not product or not partner or not code:
            return False
        for seller in self._product_sellers(product, partner):
            if SupplierArticleNormalizer.equals(code, getattr(seller, 'product_code', False)):
                return {
                    'method': 'supplierinfo_code_exact',
                    'seller': seller,
                }

        separatorless = SupplierArticleNormalizer.separatorless(code)
        if not separatorless:
            return False
        unique_seller = self._unique_separatorless_supplierinfo(partner, separatorless)
        if unique_seller and self._seller_matches_product(unique_seller, product):
            return {
                'method': 'supplierinfo_code_separatorless_unique',
                'seller': unique_seller,
            }
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
        commercial_partner = self._commercial_partner(partner) if partner else False
        for source in (
            getattr(product, 'seller_ids', False),
            getattr(getattr(product, 'product_tmpl_id', False), 'seller_ids', False),
        ):
            if not source:
                continue
            for seller in source:
                seller_partner = self._commercial_partner(getattr(seller, 'partner_id', False))
                if not commercial_partner or (
                    seller_partner
                    and seller_partner.id == commercial_partner.id
                ):
                    self._append_unique(sellers, seller)
        return sellers

    def _commercial_partner(self, partner):
        return getattr(partner, 'commercial_partner_id', False) or partner

    def _supplierinfos_for_partner(self, partner):
        commercial_partner = self._commercial_partner(partner)
        if not commercial_partner:
            return self.env['product.supplierinfo']
        return self.env['product.supplierinfo'].search([
            ('partner_id', '=', commercial_partner.id),
            ('product_code', '!=', False),
        ], limit=1000)

    def _unique_separatorless_supplierinfo(self, partner, separatorless_code):
        commercial_partner = self._commercial_partner(partner)
        cache_key = (
            getattr(commercial_partner, 'id', False),
            separatorless_code,
        )
        if cache_key in self._supplier_separatorless_cache:
            return self._supplier_separatorless_cache[cache_key]
        matches = [
            seller
            for seller in self._supplierinfos_for_partner(commercial_partner)
            if SupplierArticleNormalizer.separatorless(seller.product_code) == separatorless_code
        ]
        product_keys = {
            self._seller_product_key(seller)
            for seller in matches
            if self._seller_product_key(seller)
        }
        result = matches[0] if len(product_keys) == 1 and matches else False
        self._supplier_separatorless_cache[cache_key] = result
        return result

    def _seller_matches_product(self, seller, product):
        seller_product = getattr(seller, 'product_id', False)
        if seller_product:
            return seller_product == product
        seller_template = getattr(seller, 'product_tmpl_id', False)
        product_template = getattr(product, 'product_tmpl_id', False)
        return bool(seller_template and product_template and seller_template == product_template)

    def _seller_product_key(self, seller):
        product = getattr(seller, 'product_id', False)
        if product:
            return ('product', product.id)
        template = getattr(seller, 'product_tmpl_id', False)
        if template:
            return ('template', template.id)
        return False

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

    def _line_supplier_articles(self, line):
        articles = []
        explicit_code = getattr(line, 'supplier_product_code', False)
        if explicit_code:
            articles.append(explicit_code)
        for code in self._line_codes(line):
            if not code:
                continue
            normalized = SupplierArticleNormalizer.normalize(code)
            if normalized and normalized not in {
                SupplierArticleNormalizer.normalize(article)
                for article in articles
            }:
                articles.append(code)
        return articles

    def _line_name_terms(self, line):
        return [
            value
            for value in (
                getattr(line, 'supplier_product_name', False),
                getattr(line, 'description', False),
            )
            if value
        ]

    def _line_plain_names(self, line):
        names = []
        for value in (
            getattr(line, 'supplier_product_name', False),
            getattr(line, 'description', False),
        ):
            if value:
                names.append(value)
        return list(dict.fromkeys(names))

    def _line_normalized_plain_names(self, line):
        return list(dict.fromkeys(
            name
            for name in (
                self._normalize_plain_name(value)
                for value in self._line_plain_names(line)
            )
            if name
        ))

    def _product_normalized_plain_names(self, product):
        names = []
        for value in (
            getattr(product, 'name', False),
            getattr(product, 'display_name', False),
        ):
            normalized = self._normalize_plain_name(value)
            if normalized:
                names.append(normalized)
        return list(dict.fromkeys(names))

    def _line_name_model_values(self, line):
        return [
            value
            for value in (
                getattr(line, 'supplier_product_name', False),
                getattr(line, 'description', False),
                getattr(line, 'note', False),
                getattr(line, 'source_columns', False),
            )
            if value
        ]

    def _product_name_model_values(self, product):
        return [
            value
            for value in (
                getattr(product, 'display_name', False),
                getattr(product, 'name', False),
            )
            if value
        ]

    def _move_line_name_model_values(self, move_line, product):
        values = self._product_name_model_values(product)
        line_name = getattr(move_line, 'name', False)
        if line_name:
            values.append(line_name)
        return values

    def _name_model_token_profile(self, values, name_token_frequencies=False):
        tokens = []
        for value in values or []:
            for token in self._extract_name_model_tokens(value):
                if token not in tokens:
                    tokens.append(token)
        weights = {
            token: self._name_model_token_weight(
                token,
                frequency=(name_token_frequencies or {}).get(token, 1),
            )
            for token in tokens
        }
        meaningful_tokens = [
            token
            for token in tokens
            if weights.get(token, 0.0) >= 0.18
        ]
        phrases = self._name_model_phrases(tokens, weights)
        return {
            'tokens': tokens,
            'meaningful_tokens': meaningful_tokens,
            'weights': weights,
            'phrases': phrases,
        }

    def _extract_name_model_tokens(self, value):
        text = self._prepare_name_model_text(value)
        if not text:
            return []
        tokens = []
        for token in re.findall(
            r'[a-z0-9\u0400-\u04ff]+(?:[-/.][a-z0-9\u0400-\u04ff]+)*',
            text,
            flags=re.I | re.U,
        ):
            for expanded in self._expand_name_model_token(token):
                if expanded and expanded not in tokens:
                    tokens.append(expanded)
        return tokens

    def _prepare_name_model_text(self, value):
        if not value:
            return ''
        value = str(value).translate(self.DASH_TRANSLATION)
        value = value.replace('\u0401', '\u0415').replace('\u0451', '\u0435')
        value = re.sub(r'(?<=\d)\s*[*×хx]\s*(?=\d)', 'x', value, flags=re.U)
        value = re.sub(r'(?<=[\u0400-\u04ff])(?=[A-Za-z])', ' ', value, flags=re.U)
        value = re.sub(r'(?<=[A-Za-z])(?=[\u0400-\u04ff])', ' ', value, flags=re.U)
        value = value.lower()
        value = re.sub(r'[^\w\s/.-]', ' ', value, flags=re.U)
        value = value.replace('_', ' ')
        return re.sub(r'\s+', ' ', value).strip()

    def _expand_name_model_token(self, token):
        token = (token or '').strip(' ./-')
        if not token:
            return []
        canonical = self.NAME_TOKEN_CANONICALS.get(token, token)
        if canonical.endswith('fpv') and len(canonical) > 3:
            return [canonical[:-3], 'fpv']
        tokens = [canonical]
        compact = re.sub(r'[-/.\s]+', '', canonical)
        if compact and compact != canonical and self._looks_like_name_model_token(compact):
            tokens.append(compact)
        for part in re.split(r'[-/.]+', canonical):
            part = part.strip()
            if part and self._looks_like_name_model_token(part):
                tokens.append(part)
        return list(dict.fromkeys(tokens))

    def _looks_like_name_model_token(self, token):
        if not token:
            return False
        if token in self.GENERIC_NAME_TOKENS:
            return False
        if token.isdigit() or len(token) < 3:
            return False
        if token in self.TECHNICAL_MODEL_TOKEN_ALLOWLIST:
            return True
        if '-' in token or '/' in token or '.' in token:
            return any(char.isdigit() for char in token) and any(char.isalpha() for char in token)
        return any(char.isdigit() for char in token) and any(char.isalpha() for char in token)

    def _name_model_token_weight(self, token, frequency=1):
        if not token:
            return 0.0
        if token.isdigit() or len(token) < 2:
            return 0.0
        if token in self.GENERIC_NAME_TOKENS:
            return self.NAME_GENERIC_WEIGHT
        if token in self.NAME_COLOR_TOKENS:
            return 0.10

        base = 0.34
        if self._is_characteristic_name_token(token):
            base = self.NAME_CHARACTERISTIC_WEIGHT
        if self._looks_like_name_model_token(token):
            base = 0.68
        elif len(token) >= 6:
            base += 0.18
        elif len(token) >= 5:
            base += 0.14
        elif len(token) >= 4:
            base += 0.08

        if '-' in token or '/' in token or '.' in token:
            base += 0.18
        if any(char.isdigit() for char in token) and any(char.isalpha() for char in token):
            base += 0.18
        if len(token) >= 8:
            base += 0.08

        base *= self._name_token_frequency_factor(frequency)
        if self._is_characteristic_name_token(token):
            base = min(base, 0.42)
        return self._clamp_score(base)

    def _name_token_frequency_factor(self, frequency):
        frequency = frequency or 1
        if frequency <= 1:
            return 1.18
        if frequency <= 3:
            return 1.0
        if frequency <= 6:
            return 0.78
        return 0.55

    def _is_characteristic_name_token(self, token):
        return bool(re.match(
            r'^(?:\d+(?:[.,]\d+)?(?:v|a|w|tvl|mah|mm|cm|mp|s)|\d+(?:x\d+){1,3})$',
            token or '',
            flags=re.I,
        ))

    def _name_model_phrases(self, tokens, weights):
        phrases = []
        for size in (2, 3):
            for index in range(0, max(len(tokens) - size + 1, 0)):
                phrase_tokens = tokens[index:index + size]
                phrase_weight = sum(weights.get(token, 0.0) for token in phrase_tokens)
                if phrase_weight < 0.55:
                    continue
                if all(weights.get(token, 0.0) <= 0.12 for token in phrase_tokens):
                    continue
                phrases.append(' '.join(phrase_tokens))
        return phrases

    def _matching_name_model_phrases(self, line_profile, candidate_profile):
        candidate_phrases = set(candidate_profile.get('phrases') or [])
        return [
            phrase
            for phrase in line_profile.get('phrases') or []
            if phrase in candidate_phrases
        ]

    def _name_model_phrase_score(self, matched_phrases, weights):
        score = 0.0
        for phrase in matched_phrases:
            phrase_tokens = phrase.split()
            phrase_weight = sum(weights.get(token, 0.0) for token in phrase_tokens)
            if phrase_weight >= 1.0:
                score += 0.06
            elif phrase_weight >= 0.70:
                score += 0.04
            else:
                score += 0.02
        return min(0.16, score)

    def _conflicting_name_model_tokens(self, line_profile, candidate_profile, matched_tokens):
        matched_tokens = set(matched_tokens or [])
        line_high_tokens = {
            token
            for token in line_profile.get('meaningful_tokens') or []
            if line_profile['weights'].get(token, 0.0) >= 0.55
        }
        candidate_high_tokens = {
            token
            for token in candidate_profile.get('meaningful_tokens') or []
            if candidate_profile['weights'].get(token, 0.0) >= 0.55
        }
        missing_line_high = line_high_tokens - matched_tokens
        extra_candidate_high = candidate_high_tokens - matched_tokens
        if not missing_line_high or not extra_candidate_high:
            return []
        return sorted(missing_line_high | extra_candidate_high)

    def _name_model_conflict_penalty(self, conflicting_tokens):
        if not conflicting_tokens:
            return 0.0
        return min(0.24, 0.08 + len(conflicting_tokens) * 0.04)

    def _product_name_token_frequencies(self, products):
        return self._name_token_frequencies(
            self._product_name_model_values(product)
            for product in products
        )

    def _move_line_name_token_frequencies(self, move_lines):
        return self._name_token_frequencies(
            self._move_line_name_model_values(move_line, move_line.product_id)
            for move_line in move_lines
        )

    def _name_token_frequencies(self, values_iterable):
        frequencies = {}
        for values in values_iterable:
            tokens = set()
            for value in values or []:
                tokens.update(self._extract_name_model_tokens(value))
            for token in tokens:
                frequencies[token] = frequencies.get(token, 0) + 1
        return frequencies

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
        terms.extend(self._line_odoo_like_name_search_terms(line))
        terms.extend(self._line_weighted_name_search_terms(line))
        for value in self._line_name_terms(line):
            for token in self._meaningful_tokens(value):
                if token in self.GENERIC_NAME_TOKENS:
                    continue
                if self._is_low_value_code(token):
                    continue
                terms.append(token)
        terms.extend(
            code
            for code in profile['secondary_codes']
            if not self._is_low_value_code(code)
        )
        return list(dict.fromkeys(term for term in terms if term))

    def _line_odoo_like_name_search_terms(self, line):
        terms = []
        technical_profile = self._line_technical_profile(line)
        terms.extend(technical_profile.get('full_codes') or [])
        for value in self._line_name_terms(line):
            normalized = self._normalize_plain_name(value)
            if normalized and self._is_safe_plain_name_for_search(normalized):
                terms.append(value)
                terms.append(normalized)
            meaningful_phrase = self._line_meaningful_name_phrase(value)
            if meaningful_phrase:
                terms.append(meaningful_phrase)
        return list(dict.fromkeys(term for term in terms if term))

    def _line_meaningful_name_phrase(self, value):
        tokens = []
        for token in self._normalize_text(value).split():
            if token in self.GENERIC_NAME_TOKENS:
                tokens.append(token)
                continue
            if token in self.NAME_COLOR_TOKENS:
                tokens.append(token)
                continue
            if token.isdigit() or len(token) < 3:
                continue
            if self._looks_like_code(token) or self._is_low_value_code(token):
                continue
            tokens.append(token)
        if len(tokens) < 2:
            return False
        return ' '.join(tokens[:6])

    def _line_weighted_name_search_terms(self, line):
        name_profile = self._name_model_token_profile(self._line_name_model_values(line))
        weighted_terms = [
            (token, name_profile['weights'].get(token, 0.0))
            for token in name_profile['meaningful_tokens']
            if name_profile['weights'].get(token, 0.0) >= 0.25
            and token not in self.GENERIC_NAME_TOKENS
        ]
        weighted_terms.sort(key=lambda item: (-item[1], item[0]))
        return [token for token, _weight in weighted_terms[:8]]

    def _line_code_profile(self, line):
        primary_codes = []
        ignored_low_value_tokens = []

        explicit_code = getattr(line, 'supplier_product_code', False)
        if explicit_code:
            self._add_profile_code(
                explicit_code,
                primary_codes,
                ignored_low_value_tokens,
                allow_low_value=True,
            )

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

        if not primary_codes:
            for code in self._line_technical_profile(line)['full_codes']:
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

    def _add_profile_code(self, code, codes, ignored_low_value_tokens, allow_low_value=False):
        if not code:
            return
        if self._is_low_value_code(code) and not allow_low_value:
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
        prepared_value = self._prepare_code_text(value)
        codes = []
        codes.extend(TechnicalCodeNormalizer.extract(value))
        codes.extend(self._extract_bracket_codes(value))
        codes.extend(self._extract_leading_codes(value))
        codes.extend(self._extract_embedded_codes(value))
        if prepared_value and prepared_value != value:
            codes.extend(TechnicalCodeNormalizer.extract(prepared_value))
            codes.extend(self._extract_leading_codes(prepared_value))
            codes.extend(self._extract_embedded_codes(prepared_value))
        return self._expand_code_tokens(codes)

    def _extract_leading_codes(self, value):
        value = (value or '').strip()
        if not value:
            return []
        bracket_match = re.match(r'^\[([^\]]+)\]', value)
        if bracket_match:
            return [bracket_match.group(1).strip()]
        technical_match = re.match(
            r'^([A-Za-z]{1,12}[-/\s]*\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]*(?:[-/]\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]*)*)',
            value,
            flags=re.U,
        )
        if technical_match:
            code = technical_match.group(1).strip(':-.,; ')
            if self._looks_like_code(code):
                return [code]
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
            r'\b[A-Za-z]{1,12}[-/\s]*\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]*(?:[-/]\d+[A-Za-zА-Яа-яІіЇїЄєҐґ]*)*\b',
            r'\b[^\W_\d]{1,12}\d[\w/-]{0,30}\b',
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
            expanded.extend(self._code_search_variants(code))
            for part in re.split(r'[-/\s.]+', code):
                part = part.strip()
                if part and self._looks_like_code(part):
                    expanded.append(part)
        return expanded

    def _code_search_variants(self, code):
        code = str(code or '').strip()
        if not code:
            return []
        prepared_code = self._prepare_code_text(code)
        variants = [code, prepared_code]
        collapsed_spaces = re.sub(r'\s+', ' ', prepared_code or code).strip()
        variants.append(collapsed_spaces)
        variants.append(re.sub(r'\s+', '-', collapsed_spaces))
        variants.append(re.sub(r'[-\s]+', '', collapsed_spaces))
        variants.append(re.sub(r'[-/\s]+', '', collapsed_spaces))
        if '/' in collapsed_spaces or '-' in collapsed_spaces or ' ' in collapsed_spaces:
            variants.append(re.sub(r'[-\s]+', '-', collapsed_spaces))
        for key in self._technical_variant_keys(collapsed_spaces):
            variants.append(key)
        result = []
        seen = set()
        for variant in variants:
            variant = variant.strip()
            key = self._normalize_code(variant)
            if variant and key not in seen:
                seen.add(key)
                result.append(variant)
        return result

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
            self._weighted_name_similarity(query_normalized, target_normalized),
        )
        meaningful_overlap = self._meaningful_name_token_overlap(
            query_normalized,
            target_normalized,
        )
        color_overlap = self._name_colors(query_normalized) & self._name_colors(target_normalized)
        if color_overlap and meaningful_overlap:
            similarity = min(1.0, similarity + 0.03)
        elif color_overlap and not meaningful_overlap:
            similarity = min(similarity, 0.69)
        if not meaningful_overlap:
            similarity = min(similarity, 0.69)
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

    def _weighted_name_similarity(self, left, right):
        left_tokens = set(self._canonical_name_tokens(left))
        right_tokens = set(self._canonical_name_tokens(right))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _canonical_name_tokens(self, normalized_text):
        tokens = []
        for token in (normalized_text or '').split():
            canonical = self.NAME_TOKEN_CANONICALS.get(token, token)
            if canonical.endswith('fpv') and len(canonical) > 3:
                tokens.extend([canonical[:-3], 'fpv'])
                continue
            if canonical:
                tokens.append(canonical)
        return tokens

    def _name_colors(self, normalized_text):
        return {
            token
            for token in self._canonical_name_tokens(normalized_text)
            if token in self.NAME_COLOR_TOKENS
        }

    def _meaningful_name_token_overlap(self, left, right):
        left_tokens = self._meaningful_name_tokens(left)
        right_tokens = self._meaningful_name_tokens(right)
        return left_tokens & right_tokens

    def _meaningful_name_tokens(self, normalized_text):
        tokens = set()
        for token in self._canonical_name_tokens(normalized_text):
            if token in self.NAME_COLOR_TOKENS:
                continue
            if token in self.GENERIC_NAME_TOKENS:
                continue
            if self._is_low_value_code(token):
                continue
            if token.isdigit() or len(token) < 3:
                continue
            tokens.add(token)
        return tokens

    def _choose_score(self, current_score, current_method, candidate_score, method):
        if candidate_score > current_score:
            return candidate_score, method
        return current_score, current_method

    def _normalize_text(self, value):
        if not value:
            return ''
        value = str(value)
        value = re.sub(r'\[[^\]]+\]', ' ', value)
        value = value.translate(self.DASH_TRANSLATION)
        value = re.sub(r'(?<=\d)\s*[*×хx]\s*(?=\d)', 'x', value, flags=re.U)
        value = re.sub(r'(?<=[\u0400-\u04ff])(?=[A-Za-z])', ' ', value, flags=re.U)
        value = re.sub(r'(?<=[A-Za-z])(?=[\u0400-\u04ff])', ' ', value, flags=re.U)
        value = value.translate(self.CYRILLIC_LATIN_LOOKALIKES)
        value = value.lower().strip()
        value = re.sub(r'([^\W\d_]+)(\d+)', r'\1 \2', value, flags=re.U)
        value = re.sub(r'(\d+)([^\W\d_]+)', r'\1 \2', value, flags=re.U)
        value = re.sub(r'[^\w\s]', ' ', value, flags=re.U)
        value = value.replace('_', ' ')
        return re.sub(r'\s+', ' ', value).strip()

    def _normalize_plain_name(self, value):
        if not value:
            return ''
        value = str(value)
        value = value.replace('\u0401', '\u0415').replace('\u0451', '\u0435')
        value = value.translate(str.maketrans({
            '\u2018': "'",
            '\u2019': "'",
            '\u201a': "'",
            '\u201b': "'",
            '\u02bc': "'",
            '\u2032': "'",
            '\u201c': '"',
            '\u201d': '"',
            '\u201e': '"',
            '\u00ab': '"',
            '\u00bb': '"',
        }))
        normalized = self._normalize_text(value)
        normalized = re.sub(r'\b(?:\u0456\u0437|\u0437\u0456)\b', '\u0437', normalized)
        normalized = ' '.join(self._canonical_name_tokens(normalized))
        normalized = re.sub(r'\s+', ' ', normalized).strip(' .,;:-')
        return normalized

    def _is_safe_plain_name_for_search(self, normalized_name):
        if not normalized_name:
            return False
        tokens = normalized_name.split()
        if len(normalized_name) >= 6 and len(tokens) >= 2:
            return True
        return len(normalized_name) >= 8

    def _is_safe_plain_name_for_exact(self, normalized_name):
        if not normalized_name:
            return False
        return (
            len(normalized_name) >= 6
            and self._plain_name_has_distinctive_signal(normalized_name)
        )

    def _is_safe_plain_name_for_substring(self, normalized_name):
        if not normalized_name:
            return False
        tokens = normalized_name.split()
        return (
            len(normalized_name) >= 8
            and len(tokens) >= 2
            and self._plain_name_has_distinctive_signal(normalized_name)
        )

    def _plain_name_has_distinctive_signal(self, normalized_name):
        tokens = normalized_name.split()
        for token in tokens:
            if token in self.NAME_COLOR_TOKENS:
                continue
            if token in self.GENERIC_NAME_TOKENS:
                continue
            if token.isdigit() or len(token) < 3:
                continue
            if self._looks_like_name_model_token(token):
                return True
            if not self._is_low_value_code(token):
                return True
        return False

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

    def _append_text(self, existing_text, message):
        if existing_text:
            return '%s\n%s' % (existing_text, message)
        return message
