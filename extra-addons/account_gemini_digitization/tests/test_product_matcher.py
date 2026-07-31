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

    def _partner(self, name='Matcher Vendor'):
        return self.env['res.partner'].create({'name': name})

    def _supplierinfo(self, product, partner, code):
        values = {
            'partner_id': partner.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'product_code': code,
            'min_qty': 1.0,
        }
        if 'product_id' in self.env['product.supplierinfo']._fields:
            values['product_id'] = product.id
        return self.env['product.supplierinfo'].create(values)

    def _ocr_line(self, name, supplier_code=False, partner=False):
        job_values = {
            'name': 'Matcher test',
            'mode': 'full_bill',
            'state': 'review',
        }
        if partner:
            job_values['partner_id'] = partner.id
        job = self.env['account.gemini.digitization.job'].create(job_values)
        line_values = {
            'job_id': job.id,
            'sequence': 10,
            'supplier_product_name': name,
            'description': name,
            'quantity': 1.0,
            'price_unit': 1.0,
        }
        if supplier_code:
            line_values['supplier_product_code'] = supplier_code
        return self.env['account.gemini.digitization.line'].create(line_values)

    def _score(self, ocr_name, product_name):
        line = self._ocr_line(ocr_name)
        product = self._product(product_name)
        return self.matcher._score_product_strict(line, product, partner=False)

    def _full_bill_match(
        self,
        ocr_name,
        product_name,
        default_code=False,
        supplier_code=False,
        partner=False,
    ):
        product = self._product(product_name, default_code=default_code)
        return self._full_bill_match_existing_product(
            ocr_name,
            product,
            supplier_code=supplier_code,
            partner=partner,
        )

    def _full_bill_match_existing_product(
        self,
        ocr_name,
        product,
        supplier_code=False,
        partner=False,
    ):
        job_values = {
            'name': 'Matcher full bill test',
            'mode': 'full_bill',
            'state': 'review',
        }
        if partner:
            job_values['partner_id'] = partner.id
        job = self.env['account.gemini.digitization.job'].create(job_values)
        line_values = {
            'job_id': job.id,
            'sequence': 10,
            'supplier_product_name': ocr_name,
            'description': ocr_name,
            'quantity': 1.0,
            'price_unit': 1.0,
        }
        if supplier_code:
            line_values['supplier_product_code'] = supplier_code
        line = self.env['account.gemini.digitization.line'].create(line_values)
        discovered_products = self.matcher._find_full_bill_products(line, partner)

        self.matcher._match_full_document_products(job)

        return line, product, discovered_products

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

    def test_full_bill_exact_normalized_name_discovers_dc_converter(self):
        ocr_name = 'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A'
        product_name = 'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A'

        line, product, discovered_products = self._full_bill_match(
            ocr_name,
            product_name,
            default_code='RES-BEC-0384',
        )

        self.assertIn(product, discovered_products)
        self.assertEqual(line.match_status, 'matched')
        self.assertEqual(line.match_method, 'normalized_name_exact')
        self.assertEqual(line.match_score, 1.0)
        self.assertEqual(line.matched_product_id, product)

    def test_full_bill_unmatched_supplier_code_does_not_block_exact_name(self):
        ocr_name = 'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A'
        product_name = 'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A'
        partner = self._partner()

        line, product, discovered_products = self._full_bill_match(
            ocr_name,
            product_name,
            default_code='RES-BEC-0384',
            supplier_code='A431220',
            partner=partner,
        )

        self.assertIn(product, discovered_products)
        self.assertEqual(line.match_status, 'matched')
        self.assertEqual(line.match_method, 'normalized_name_exact')
        self.assertEqual(line.match_score, 1.0)
        self.assertEqual(line.matched_product_id, product)
        self.assertIn('reason_code=supplier_code_unmatched_name_exact', line.match_note)
        self.assertIn('recognized_supplier_code: A431220', line.match_note)
        self.assertIn('supplier_code_match_found: false', line.match_note)
        self.assertIn('name_exact_search_executed: true', line.match_note)

    def test_full_bill_unmatched_supplier_code_without_exact_name_stays_unmatched(self):
        partner = self._partner()
        line = self._ocr_line(
            'Невідомий перетворювач DC-DC 24V 5A',
            supplier_code='A431220',
            partner=partner,
        )

        self.matcher._match_full_document_products(line.job_id)

        self.assertIn(line.match_status, ('not_found', 'ambiguous'))
        self.assertNotEqual(line.match_method, 'normalized_name_exact')
        self.assertIn('recognized_supplier_code: A431220', line.match_note)
        self.assertIn('supplier_code_match_found: false', line.match_note)
        self.assertIn('name_exact_search_executed: true', line.match_note)

    def test_full_bill_supplier_code_exact_conflicts_with_exact_name(self):
        partner = self._partner()
        supplier_product = self._product('Інший товар постачальника')
        exact_name_product = self._product(
            'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A',
            default_code='RES-BEC-0384',
        )
        self._supplierinfo(supplier_product, partner, 'A431220')

        line, _product, _discovered_products = self._full_bill_match_existing_product(
            'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A',
            exact_name_product,
            supplier_code='A431220',
            partner=partner,
        )

        self.assertEqual(line.match_status, 'ambiguous')
        self.assertEqual(line.match_method, 'supplierinfo_code_exact')
        self.assertFalse(line.matched_product_id)
        self.assertIn(supplier_product, line.candidate_product_ids)
        self.assertIn(exact_name_product, line.candidate_product_ids)
        self.assertIn('reason_code=supplier_code_name_conflict', line.match_note)

    def test_full_bill_exact_normalized_name_handles_dash_spacing_variants(self):
        ocr_variants = [
            'Перетворювач DC–DC понижуючий з 24–60 V на 24 V 5 A',
            'Перетворювач DC — DC понижуючий з 24 - 60V на 24V 5A',
            'Перетворювач dc dc понижуючий з 24 - 60 V на 24 V, 5 A',
        ]
        product_name = 'Перетворювач DC-DC понижуючий з 24-60V на 24V, 5A'
        product = self._product(product_name, default_code='RES-BEC-0384')

        for ocr_name in ocr_variants:
            line, _product, discovered_products = self._full_bill_match_existing_product(
                ocr_name,
                product,
            )

            self.assertIn(product, discovered_products)
            self.assertEqual(line.match_status, 'matched')
            self.assertEqual(line.match_method, 'normalized_name_exact')
            self.assertEqual(line.match_score, 1.0)
            self.assertEqual(line.matched_product_id, product)

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
