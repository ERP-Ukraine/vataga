from lxml import etree

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountMoveAnalogViews(TransactionCase):
    def test_original_column_in_both_invoice_line_trees(self):
        result = self.env['account.move'].with_context(
            default_move_type='in_invoice',
        ).get_views(
            [(self.env.ref('account.view_move_form').id, 'form')],
            options={'toolbar': False},
        )
        arch = etree.fromstring(result['views']['form']['arch'])
        trees = arch.xpath("//field[@name='invoice_line_ids']/tree")
        self.assertEqual(len(trees), 2)
        posted_trees = [
            tree for tree in trees
            if tree.get('create') == '0' and tree.get('delete') == '0'
        ]
        self.assertEqual(len(posted_trees), 1)
        helper_names = (
            'analog_original_product_ids',
            'has_analog_original_options',
            'has_multiple_analog_original_options',
        )
        for tree in trees:
            is_posted = tree is posted_trees[0]
            with self.subTest(posted=is_posted):
                for name in (*helper_names, 'analog_original_product_id'):
                    self.assertEqual(
                        len(tree.xpath('./field[@name=$name]', name=name)), 1,
                        f'{name} must occur exactly once in each invoice tree',
                    )
                for name in helper_names:
                    helper = tree.xpath('./field[@name=$name]', name=name)[0]
                    self.assertIn(helper.get('column_invisible'), ('1', 'True'))
                original = tree.xpath(
                    "./field[@name='analog_original_product_id']",
                )[0]
                self.assertEqual(original.get('optional'), 'show')
                self.assertEqual(
                    original.get('invisible'), 'not has_analog_original_options',
                )
                self.assertIn(original.get('column_invisible'), (None, '0', 'False'))
                if is_posted:
                    self.assertIn(original.get('readonly'), ('1', 'True'))
                else:
                    self.assertIn(original.get('readonly'), (None, '0', 'False'))
