from odoo.tests.common import TransactionCase


def _template_product_search_domain(value):
    return [
        '|',
        '|',
        '|',
        '|',
        ('default_code', 'ilike', value),
        ('product_variant_ids.default_code', 'ilike', value),
        ('name', 'ilike', value),
        ('barcode', 'ilike', value),
        ('product_variant_ids.packaging_ids.barcode', 'ilike', value),
    ]


def _variant_product_search_domain(value):
    return [
        '|',
        '|',
        '|',
        ('default_code', 'ilike', value),
        ('name', 'ilike', value),
        ('barcode', 'ilike', value),
        ('packaging_ids.barcode', 'ilike', value),
    ]


class TestProductPackagingSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductTemplate = cls.env['product.template']
        cls.Packaging = cls.env['product.packaging']

        cls.product = cls.Product.create(
            {
                'name': 'Waterproof E-TEN 1021 toggle',
                'default_code': 'RES-BEC-0037',
                'barcode': '2000000000152',
            }
        )
        cls.template = cls.product.product_tmpl_id
        cls.Packaging.create(
            {
                'name': 'Box 482305350097525',
                'product_id': cls.product.id,
                'qty': 1,
                'barcode': '482305350097525',
            }
        )

    def _search_templates(self, value):
        return self.ProductTemplate.search(_template_product_search_domain(value))

    def _search_variants(self, value):
        return self.Product.search(_variant_product_search_domain(value))

    def test_search_by_product_barcode_still_finds_product(self):
        self.assertIn(self.template, self._search_templates('2000000000152'))
        self.assertIn(self.product, self._search_variants('2000000000152'))

    def test_search_by_packaging_barcode_finds_product(self):
        self.assertIn(self.template, self._search_templates('482305350097525'))
        self.assertIn(self.product, self._search_variants('482305350097525'))

    def test_search_by_name_still_finds_product(self):
        self.assertIn(self.template, self._search_templates('Waterproof'))
        self.assertIn(self.product, self._search_variants('Waterproof'))

    def test_search_by_default_code_still_finds_product(self):
        self.assertIn(self.template, self._search_templates('RES-BEC-0037'))
        self.assertIn(self.product, self._search_variants('RES-BEC-0037'))

    def test_unknown_barcode_does_not_find_product(self):
        self.assertNotIn(self.template, self._search_templates('000000000000000'))
        self.assertNotIn(self.product, self._search_variants('000000000000000'))

    def test_two_packagings_do_not_duplicate_product_results(self):
        duplicated_product = self.Product.create(
            {
                'name': 'Duplicate packaging search product',
                'default_code': 'RES-PKG-DUP',
            }
        )
        self.Packaging.create(
            [
                {
                    'name': 'Box DUP 1',
                    'product_id': duplicated_product.id,
                    'qty': 1,
                    'barcode': 'DUP-PACK-001',
                },
                {
                    'name': 'Box DUP 2',
                    'product_id': duplicated_product.id,
                    'qty': 2,
                    'barcode': 'DUP-PACK-002',
                },
            ]
        )

        templates = self._search_templates('DUP-PACK')
        variants = self._search_variants('DUP-PACK')
        duplicated_template = duplicated_product.product_tmpl_id
        matched_templates = templates.filtered(lambda product: product == duplicated_template)
        matched_variants = variants.filtered(lambda product: product == duplicated_product)

        self.assertEqual(len(matched_templates), 1)
        self.assertEqual(matched_templates, duplicated_template)
        self.assertEqual(len(matched_variants), 1)
        self.assertEqual(matched_variants, duplicated_product)

    def test_variant_packaging_finds_template_and_matching_variant(self):
        attribute = self.env['product.attribute'].create({'name': 'Packaging Search Attribute'})
        value_a = self.env['product.attribute.value'].create(
            {'name': 'Variant A', 'attribute_id': attribute.id}
        )
        value_b = self.env['product.attribute.value'].create(
            {'name': 'Variant B', 'attribute_id': attribute.id}
        )
        template = self.ProductTemplate.create(
            {
                'name': 'Variant packaging search product',
                'attribute_line_ids': [
                    (
                        0,
                        0,
                        {
                            'attribute_id': attribute.id,
                            'value_ids': [(6, 0, [value_a.id, value_b.id])],
                        },
                    )
                ],
            }
        )
        template._create_variant_ids()
        self.assertEqual(len(template.product_variant_ids), 2)
        matching_variant = template.product_variant_ids[0]
        other_variant = template.product_variant_ids - matching_variant
        self.Packaging.create(
            {
                'name': 'Variant package',
                'product_id': matching_variant.id,
                'qty': 1,
                'barcode': 'VARIANT-PACK-001',
            }
        )

        self.assertIn(template, self._search_templates('VARIANT-PACK-001'))
        self.assertIn(matching_variant, self._search_variants('VARIANT-PACK-001'))
        self.assertFalse(other_variant & self._search_variants('VARIANT-PACK-001'))

    def test_product_without_packagings_searches_without_errors(self):
        product = self.Product.create(
            {
                'name': 'No packaging search product',
                'default_code': 'RES-NO-PKG',
            }
        )

        self.assertIn(product.product_tmpl_id, self._search_templates('RES-NO-PKG'))
        self.assertIn(product, self._search_variants('RES-NO-PKG'))
