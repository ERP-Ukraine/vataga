import re

from lxml import html as lxml_html

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


def _class_predicate(class_name):
    return "contains(concat(' ', normalize-space(@class), ' '), ' %s ')" % class_name


@tagged('post_install', '-at_install')
class TestStockDeliveryReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].create(
            {
                'name': 'Vataga Report Test Warehouse',
                'code': 'VRT',
                'company_id': cls.env.company.id,
            }
        )
        cls.source_location = cls.warehouse.lot_stock_id
        cls.destination_location = cls.env['stock.location'].create(
            {
                'name': 'Vataga Report Destination',
                'location_id': cls.warehouse.view_location_id.id,
                'usage': 'internal',
                'company_id': cls.env.company.id,
            }
        )
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.picking_type = cls.warehouse.int_type_id

    @classmethod
    def _create_product(cls, name, default_code, barcode):
        return cls.env['product.product'].create(
            {
                'name': name,
                'default_code': default_code,
                'barcode': barcode,
                'detailed_type': 'product',
                'standard_price': 10.0,
            }
        )

    @classmethod
    def _create_picking(
        cls,
        product,
        planned_quantity,
        actual_quantities,
        state,
        picking_type=None,
        destination_location=None,
        move_name=None,
        description_picking=None,
    ):
        picking_type = picking_type or cls.picking_type
        destination_location = destination_location or cls.destination_location
        picking = cls.env['stock.picking'].create(
            {
                'picking_type_id': picking_type.id,
                'location_id': cls.source_location.id,
                'location_dest_id': destination_location.id,
                'user_id': cls.env.user.id,
            }
        )
        move_values = {
            'name': move_name or product.display_name,
            'picking_id': picking.id,
            'product_id': product.id,
            'product_uom_qty': planned_quantity,
            'product_uom': product.uom_id.id,
            'location_id': cls.source_location.id,
            'location_dest_id': destination_location.id,
        }
        if description_picking is not None:
            move_values['description_picking'] = description_picking
        move = cls.env['stock.move'].create(move_values)
        for quantity in actual_quantities:
            cls.env['stock.move.line'].create(
                {
                    'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'quantity': quantity,
                    'location_id': cls.source_location.id,
                    'location_dest_id': destination_location.id,
                }
            )
        move.write({'state': state})
        if state == 'done':
            picking.write({'date_done': fields.Datetime.now()})
        return picking, move

    def _render_delivery_report(self, picking):
        report = self.env.ref('stock.action_report_delivery')
        content, _content_type = report._render_qweb_html(
            report.report_name,
            picking.ids,
        )
        return content.decode()

    def _document(self, rendered_html):
        return lxml_html.fromstring(rendered_html)

    def _single_product_row(self, rendered_html, product):
        document = self._document(rendered_html)
        rows = document.xpath(
            "//tr[.//td[%s][contains(normalize-space(string(.)), $product_name)]]"
            % _class_predicate('o_vataga_col_product'),
            product_name=product.name,
        )
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _single_cell(self, row, class_name):
        cells = row.xpath(
            "./td[%s] | ./th[%s]"
            % (_class_predicate(class_name), _class_predicate(class_name))
        )
        self.assertEqual(len(cells), 1)
        return cells[0]

    def _cell_text(self, cell):
        return ' '.join(''.join(cell.itertext()).split())

    def _assert_cell_contains_number(self, row, class_name, number):
        cell_text = self._cell_text(self._single_cell(row, class_name))
        number_pattern = re.compile(rf'(?<!\d){number:g}(?:[\.,]0+)?(?!\d)')
        self.assertRegex(cell_text, number_pattern)

    def test_done_picking_shows_quantity_demand_issued_and_amount(self):
        product = self._create_product(
            'Done report quantity product',
            'REPORT-DONE-QTY',
            '2000000000206',
        )
        picking, move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(2, 3),
            state='done',
        )

        self.assertEqual(picking.state, 'done')
        self.assertEqual(move.product_uom_qty, 7)
        self.assertEqual(move.quantity, 5)

        row = self._single_product_row(self._render_delivery_report(picking), product)

        self._assert_cell_contains_number(row, 'o_vataga_col_quantity', 5)
        self._assert_cell_contains_number(row, 'o_vataga_col_demand', 7)
        self._assert_cell_contains_number(row, 'o_vataga_col_issued', 5)
        self._assert_cell_contains_number(row, 'o_vataga_col_amount', 50)

    def test_open_picking_keeps_row_with_zero_issued_quantity(self):
        product = self._create_product(
            'Open report quantity product',
            'REPORT-OPEN-QTY',
            '2000000000213',
        )
        picking, move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(),
            state='confirmed',
        )

        self.assertNotEqual(picking.state, 'done')
        self.assertEqual(move.product_uom_qty, 7)
        self.assertEqual(move.quantity, 0)

        row = self._single_product_row(self._render_delivery_report(picking), product)

        self._assert_cell_contains_number(row, 'o_vataga_col_quantity', 7)
        self._assert_cell_contains_number(row, 'o_vataga_col_demand', 7)
        self._assert_cell_contains_number(row, 'o_vataga_col_issued', 0)

    def test_done_picking_keeps_row_with_zero_demand_quantity(self):
        product = self._create_product(
            'Done zero demand report product',
            'REPORT-ZERO-DEMAND',
            '2000000000220',
        )
        picking, move = self._create_picking(
            product,
            planned_quantity=0,
            actual_quantities=(5,),
            state='done',
        )

        self.assertEqual(move.product_uom_qty, 0)
        self.assertEqual(move.quantity, 5)

        row = self._single_product_row(self._render_delivery_report(picking), product)

        self._assert_cell_contains_number(row, 'o_vataga_col_quantity', 5)
        self._assert_cell_contains_number(row, 'o_vataga_col_demand', 0)
        self._assert_cell_contains_number(row, 'o_vataga_col_issued', 5)

    def test_internal_transfer_product_cell_uses_display_name_without_links(self):
        product = self._create_product(
            'AliExpress URL report product',
            'REPORT-URL-QTY',
            '2000000000237',
        )
        url = 'https://www.aliexpress.com/item/100500123456.html?spm=test'
        picking, _move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(5,),
            state='done',
            move_name='%s %s [1]' % (product.display_name, url),
            description_picking='<a href="%s">supplier link</a> [1]' % url,
        )

        row = self._single_product_row(self._render_delivery_report(picking), product)
        product_cell = self._single_cell(row, 'o_vataga_col_product')
        product_text = self._cell_text(product_cell)

        self.assertIn(product.default_code, product_text)
        self.assertIn(product.name, product_text)
        self.assertNotIn('http://', product_text)
        self.assertNotIn('https://', product_text)
        self.assertNotIn('www.', product_text)
        self.assertNotIn('[1]', product_text)
        self.assertFalse(product_cell.xpath('.//a'))

    def test_internal_transfer_headers_keep_expected_order(self):
        product = self._create_product(
            'Header order report product',
            'REPORT-HEADER-ORDER',
            '2000000000244',
        )
        picking, _move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(5,),
            state='done',
        )
        document = self._document(self._render_delivery_report(picking))
        header_rows = document.xpath(
            "//thead//tr[.//th[%s]]" % _class_predicate('o_vataga_col_quantity')
        )
        self.assertEqual(len(header_rows), 1)
        headers = [self._cell_text(cell) for cell in header_rows[0].xpath('./th')]

        expected_order = [
            '\u041a\u0456\u043b-\u0442\u044c',
            '\u041f\u043e\u043f\u0438\u0442',
            '\u0412\u0438\u0434\u0430\u043d\u043e',
            '\u041e\u0434.',
        ]
        positions = [headers.index(label) for label in expected_order]
        self.assertEqual(positions, sorted(positions))

    def test_price_amount_and_barcode_columns_remain_visible(self):
        product = self._create_product(
            'Price amount barcode report product',
            'REPORT-PRICE-BARCODE',
            '2000000000251',
        )
        picking, _move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(5,),
            state='done',
        )

        row = self._single_product_row(self._render_delivery_report(picking), product)

        self._assert_cell_contains_number(row, 'o_vataga_col_price', 10)
        self._assert_cell_contains_number(row, 'o_vataga_col_amount', 50)
        self.assertIn(
            product.barcode,
            self._cell_text(self._single_cell(row, 'o_vataga_col_barcode')),
        )

    def test_non_internal_transfer_does_not_get_new_columns(self):
        product = self._create_product(
            'Outgoing delivery report product',
            'REPORT-OUT-QTY',
            '2000000000268',
        )
        picking, _move = self._create_picking(
            product,
            planned_quantity=7,
            actual_quantities=(),
            state='confirmed',
            picking_type=self.warehouse.out_type_id,
            destination_location=self.customer_location,
        )

        document = self._document(self._render_delivery_report(picking))
        header_text = ' '.join(document.xpath('//thead//th//text()'))

        self.assertNotIn('\u041f\u043e\u043f\u0438\u0442', header_text)
        self.assertNotIn('\u0412\u0438\u0434\u0430\u043d\u043e', header_text)
