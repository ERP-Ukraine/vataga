from odoo.tests.common import TransactionCase


class TestProductDefaultCode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductTemplate = cls.env['product.template']

    def _create_product(self, default_code):
        return self.Product.create(
            {
                'name': 'Default Code Test %s' % default_code,
                'default_code': default_code,
            }
        )

    def _get_default_code_warning_message(self, default_code):
        template = self.ProductTemplate.new({'default_code': default_code})
        warning = template._onchange_default_code()
        return warning['warning']['message']

    def test_default_code_onchange_shows_next_available_reference(self):
        self.Product.create(
            [
                {
                    'name': 'Default Code Sequence %04d' % number,
                    'default_code': 'RES-BMP-%04d' % number,
                }
                for number in range(1, 184)
            ]
            + [
                {
                    'name': 'Default Code Sequence 0184',
                    'default_code': 'RES-BMP-0184-A',
                }
            ]
        )

        message = self._get_default_code_warning_message('RES-BMP-0144')

        self.assertIn(
            "The Internal Reference 'RES-BMP-0144' already exists.",
            message,
        )
        self.assertIn(
            'Next available internal reference: RES-BMP-0185.',
            message,
        )

    def test_default_code_onchange_with_invalid_format_keeps_plain_message(self):
        self._create_product('TEST123')

        message = self._get_default_code_warning_message('TEST123')

        self.assertIn("The Internal Reference 'TEST123' already exists.", message)
        self.assertNotIn('Next available internal reference', message)

    def test_default_code_onchange_suggests_first_gap(self):
        self._create_product('RES-GAP-0001')
        self._create_product('RES-GAP-0003')

        message = self._get_default_code_warning_message('RES-GAP-0001')

        self.assertIn(
            "The Internal Reference 'RES-GAP-0001' already exists.",
            message,
        )
        self.assertIn(
            'Next available internal reference: RES-GAP-0002.',
            message,
        )

    def test_default_code_onchange_with_suffix_suggests_without_suffix(self):
        self._create_product('RES-SFX-0001-A')

        message = self._get_default_code_warning_message('RES-SFX-0001-A')

        self.assertIn(
            "The Internal Reference 'RES-SFX-0001-A' already exists.",
            message,
        )
        self.assertIn(
            'Next available internal reference: RES-SFX-0002.',
            message,
        )
