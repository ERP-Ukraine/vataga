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
