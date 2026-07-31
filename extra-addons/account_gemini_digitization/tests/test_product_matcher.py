from odoo.tests.common import TransactionCase

from ..services.product_matcher import ProductMatcher


class TestGeminiProductMatcher(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.matcher = ProductMatcher(cls.env)

    def _product(self, name, default_code=False):
        values = {
            'name': name,
            'purchase_ok': True,
        }
        if default_code:
            values['default_code'] = default_code
        return self.env['product.product'].create(values)

    def _ocr_line(self, name):
        job = self.env['account.gemini.digitization.job'].create({
            'name': 'Matcher test',
            'mode': 'full_bill',
            'state': 'review',
        })
        return self.env['account.gemini.digitization.line'].create({
            'job_id': job.id,
            'sequence': 10,
            'supplier_product_name': name,
            'description': name,
            'quantity': 1.0,
            'price_unit': 1.0,
        })

    def _score(self, ocr_name, product_name):
        line = self._ocr_line(ocr_name)
        product = self._product(product_name)
        return self.matcher._score_product_strict(line, product, partner=False)

    def test_dotted_technical_code_exact_match(self):
        candidate = (
            '[RES-BEC-0238] '
            'Розетка під кутовий червоний RHT.S006.T01.RD'
        )
        result = self._score(
            'Розетка під кутовий червоний RHT.S006.T01.RD',
            candidate,
        )
        self.assertEqual(result['method'], 'dotted_technical_code_exact')
        self.assertGreaterEqual(result['score'], 0.99)

    def test_dotted_technical_code_keeps_color_suffix_separate(self):
        result = self._score(
            'Розетка під кутовий червоний RHT.S006.T01.RD',
            '[RES-BEC-0239] Розетка під кутовий чорний RHT.S006.T01.BK',
        )
        self.assertLess(result['score'], ProductMatcher.MATCHED_THRESHOLD)

    def test_full_document_assignment_locks_distinct_rht_exact_codes(self):
        expected = [
            (
                'Розетка під кутовий червоний RHT.S006.T01.RD',
                '[RES-BEC-0238] Розетка під кутовий червоний RHT.S006.T01.RD',
            ),
            (
                'Розетка під кутовий чорний RHT.S006.T01.BK',
                '[RES-BEC-0239] Розетка під кутовий чорний RHT.S006.T01.BK',
            ),
            (
                'Штекер кутовий червоний RHT.P006.C16.RD',
                '[RES-BEC-0287] Штекер кутовий червоний RHT.P006.C16.RD',
            ),
            (
                'Штекер кутовий чорний RHT.P006.C16.BK',
                '[RES-BEC-0288] Штекер кутовий чорний RHT.P006.C16.BK',
            ),
        ]
        products_by_name = {
            ocr_name: self._product(product_name)
            for ocr_name, product_name in expected
        }
        job = self.env['account.gemini.digitization.job'].create({
            'name': 'RHT matcher test',
            'mode': 'full_bill',
            'state': 'review',
        })
        lines_by_name = {}
        for sequence, (ocr_name, _product_name) in enumerate(expected, start=1):
            lines_by_name[ocr_name] = self.env['account.gemini.digitization.line'].create({
                'job_id': job.id,
                'sequence': sequence,
                'supplier_product_name': ocr_name,
                'description': ocr_name,
                'quantity': 1.0,
                'price_unit': 1.0,
            })

        self.matcher._match_full_document_products(job)

        for ocr_name, product in products_by_name.items():
            line = lines_by_name[ocr_name]
            self.assertEqual(line.match_status, 'matched')
            self.assertEqual(line.match_method, 'dotted_technical_code_exact')
            self.assertEqual(line.match_score, 1.0)
            self.assertEqual(line.matched_product_id, product)

    def test_full_assignment_deduplicates_same_product_candidate(self):
        line = self._ocr_line('Розетка під кутовий чорний RHT.S006.T01.BK')
        product = self._product(
            '[RES-BEC-0239] Розетка під кутовий чорний RHT.S006.T01.BK'
        )
        candidates = [
            {
                'product': product,
                'move_line': False,
                'score': 1.0,
                'method': 'dotted_technical_code_exact',
                'notes': ['Exact code via technical search.'],
            },
            {
                'product': product,
                'move_line': False,
                'score': 0.97,
                'method': 'name_exact_or_near_exact',
                'notes': ['Same product discovered via name search.'],
            },
        ]

        self.matcher._write_full_assignment_result(line, candidates)

        self.assertEqual(line.match_status, 'matched')
        self.assertEqual(line.match_method, 'dotted_technical_code_exact')
        self.assertEqual(line.match_score, 1.0)
        self.assertEqual(line.matched_product_id, product)
        self.assertEqual(line.candidate_product_ids, product)

    def test_dotted_technical_code_keeps_model_segments_separate(self):
        result = self._score(
            'Розетка під кутовий червоний RHT.S006.T01.RD',
            '[RES-BEC-0287] Штекер кутовий червоний RHT.P006.C16.RD',
        )
        self.assertLess(result['score'], ProductMatcher.MATCHED_THRESHOLD)

    def test_generic_only_name_does_not_auto_match(self):
        result = self._score(
            'Кабель чорний',
            'Кабель чорний силіконовий',
        )
        self.assertLess(result['score'], ProductMatcher.MATCHED_THRESHOLD)
