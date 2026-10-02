from lxml import html

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestInternalPickingReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].create({
            'name': 'Internal report warehouse', 'code': 'IRPT',
        })
        cls.source = cls.warehouse.lot_stock_id
        cls.destination = cls.env['stock.location'].create({
            'name': 'Report destination', 'usage': 'internal',
            'location_id': cls.warehouse.view_location_id.id,
        })
        cls.products = cls.env['product.product'].create([
            {'name': name, 'detailed_type': 'product', 'barcode': barcode}
            for name, barcode in [('Report Z', '2000000000305'),
                                  ('Report A', '2000000000312'),
                                  ('Report M', False)]
        ])

    def _picking(self, lines, picking_type=None):
        picking = self.env['stock.picking'].create({
            'picking_type_id': (picking_type or self.warehouse.int_type_id).id,
            'location_id': self.source.id,
            'location_dest_id': self.destination.id,
        })
        for product, demand, sequence in lines:
            self.env['stock.move'].create({
                'name': product.display_name, 'product_id': product.id,
                'product_uom_qty': demand, 'product_uom': product.uom_id.id,
                'picking_id': picking.id, 'sequence': sequence,
                'location_id': self.source.id,
                'location_dest_id': self.destination.id,
            })
        return picking

    def _render(self, picking):
        content, _ = self.env['ir.actions.report']._render_qweb_html(
            'stock.report_picking', picking.ids,
        )
        return html.fromstring(content)

    def _assert_operations(self, picking):
        document = self._render(picking)
        tables = document.xpath("//table[@name='internal_move_table']")
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual(
            [''.join(th.itertext()).strip() for th in table.xpath('./thead/tr/th')],
            ['Товар', 'Попит', 'Кількість', 'Кількість', 'Від', 'До', 'Штрих-код товару'],
        )
        rows = table.xpath('./tbody/tr')
        moves = picking.move_ids_without_package
        self.assertEqual(len(rows), len(moves))
        for row, move in zip(rows, moves):
            cells = row.xpath('./td')
            self.assertEqual(len(cells), 7)
            self.assertIn(move.product_id.display_name, cells[0].text_content())
            for index, field in [(1, 'product_uom_qty'), (2, 'quantity')]:
                expected = self.env['ir.qweb.field.float'].record_to_html(move, field, {})
                self.assertEqual(cells[index].xpath('./span')[0].text_content(), str(expected))
            self.assertEqual(cells[3].text_content().strip(), '')
            self.assertIn(move.location_id.display_name, cells[4].text_content())
            self.assertIn(move.location_dest_id.display_name, cells[5].text_content())
            self.assertEqual(bool(cells[6].xpath('.//img')), bool(move.product_id.barcode))
        self.assertFalse(document.xpath("//th[@name='th_product' or @name='th_package']"))
        return rows

    def test_draft_three_moves_follow_operations_order(self):
        picking = self._picking([
            (self.products[0], 3, 30), (self.products[1], 7, 10),
            (self.products[2], 0, 20),
        ])
        self.assertEqual(picking.state, 'draft')
        self.assertFalse(picking.move_line_ids)
        self.assertEqual(picking.move_ids_without_package.mapped('sequence'), [10, 20, 30])
        self.assertEqual(len(self._assert_operations(picking)), 3)

    def test_partial_stock_and_multiple_move_lines_keep_one_row(self):
        product = self.products[0]
        # Two source bins force two reservation lines for the same move.
        for name in ['Bin Z', 'Bin A']:
            location = self.env['stock.location'].create({
                'name': name, 'usage': 'internal', 'location_id': self.source.id,
            })
            self.env['stock.quant']._update_available_quantity(product, location, 1)
        picking = self._picking([(product, 50, 10)])
        picking.action_confirm()
        picking.action_assign()
        self.assertEqual(len(picking.move_line_ids), 2)
        self.assertEqual(picking.move_ids_without_package.product_uom_qty, 50)
        self.assertEqual(picking.move_ids_without_package.quantity, 2)
        self.assertEqual(len(self._assert_operations(picking)), 1)

    def test_no_stock_keeps_move(self):
        picking = self._picking([(self.products[2], 50, 10)])
        picking.action_confirm()
        picking.action_assign()
        self.assertFalse(picking.move_line_ids)
        self.assertEqual(len(self._assert_operations(picking)), 1)

    def test_duplicate_products_remain_separate(self):
        picking = self._picking([
            (self.products[0], 8, 30), (self.products[0], 2, 10),
            (self.products[0], 5, 20),
        ])
        self.assertEqual(picking.move_ids_without_package.mapped('product_uom_qty'), [2, 5, 8])
        self.assertEqual(len(self._assert_operations(picking)), 3)

    def test_incoming_outgoing_tables_match_standard_report(self):
        view = self.env.ref('product_vataga.report_picking_actual_quantity')
        for picking_type in [self.warehouse.in_type_id, self.warehouse.out_type_id]:
            picking = self._picking([(self.products[0], 4, 10)], picking_type)
            for with_lines in [False, True]:
                if with_lines:
                    self.env['stock.move.line'].create({
                        'move_id': picking.move_ids.id, 'picking_id': picking.id,
                        'product_id': self.products[0].id,
                        'product_uom_id': self.products[0].uom_id.id, 'quantity': 2,
                        'location_id': self.source.id,
                        'location_dest_id': self.destination.id,
                    })
                custom = self._render(picking)
                self.assertFalse(custom.xpath("//table[@name='internal_move_table']"))
                view.active = False
                try:
                    standard = self._render(picking)
                finally:
                    view.active = True
                self.assertEqual(
                    [html.tostring(t) for t in custom.xpath('//table')],
                    [html.tostring(t) for t in standard.xpath('//table')],
                )
