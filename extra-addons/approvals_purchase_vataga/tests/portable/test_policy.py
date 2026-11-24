import unittest

from support import internal, policy


class TestPurchaseValidation(unittest.TestCase):
    def test_http_urls(self):
        for url in ('https://example.com/item?id=7#specs', 'http://vendor.ua',
                    'HTTPS://example.com/a%20b', 'https://магазин.укр/товар',
                    'http://127.0.0.1:8080/a', 'https://[::1]/a'):
            with self.subTest(url=url):
                self.assertTrue(policy.valid_product_url(url))

    def test_invalid_and_unsafe_urls(self):
        for url in (False, None, '', 'example.com', 'ftp://example.com',
                    'javascript:alert(1)', 'data:text/html,x', '//example.com',
                    'https://', 'https://user:password@example.com',
                    'https://example.com:65536/a', 'http://example.com:0',
                    'http://example.com:bad', 'https://bad..host',
                    ' https://example.com', 'https://example.com ',
                    'https://example.com/hello world', 'https://example.com/\n',
                    'https://bad_host', 'https://-host.ua', 'http://[bad]',
                    'https://example.com\\@other.com'):
            with self.subTest(url=url):
                self.assertFalse(policy.valid_product_url(url))

    def test_quantities(self):
        for quantity in (0, -1, float('nan'), float('inf'), -float('inf'), None, '2'):
            with self.subTest(quantity=quantity):
                self.assertFalse(policy.positive_quantity(quantity))
        for quantity in (0.00001, 1, 10.5):
            self.assertTrue(policy.positive_quantity(quantity))

    def test_existing_product_without_url(self):
        self.assertEqual(policy.line_errors(True, 'Existing', False, 1), [])

    def test_missing_product_with_description_and_url(self):
        self.assertEqual(policy.line_errors(False, 'Spare part', 'https://example.com/a', 2), [])

    def test_all_line_errors_are_reported(self):
        self.assertEqual(len(policy.line_errors(False, '   ', False, 0)), 3)

    def test_optional_url_must_still_be_safe(self):
        self.assertEqual(len(policy.line_errors(True, 'Existing', 'ftp://vendor.com', 1)), 1)

    def test_rpc_context_cannot_supply_capabilities_or_audit_defaults(self):
        context = {'lang': 'uk_UA', 'default_product_id': 7,
                   'default_vataga_filled_after_approval': True,
                   'default_purchase_order_line_id': 9,
                   '_vataga_internal': True, '_vataga_rfq': 'RFQ', '_vataga_workflow': 1}
        self.assertEqual(internal.clean_defaults(context, {'purchase_order_line_id'}),
                         {'lang': 'uk_UA', 'default_product_id': 7})

    def test_capabilities_are_not_returned_in_ui_actions(self):
        action = {'type': 'ir.actions.act_window',
                  'context': {'lang': 'uk_UA', '_vataga_internal': internal.INTERNAL}}
        result = internal.public_result(action)
        self.assertEqual(result['context'], {'lang': 'uk_UA'})
        self.assertIn('_vataga_internal', action['context'])


if __name__ == '__main__':
    unittest.main()
