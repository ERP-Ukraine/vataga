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
            ['Товар', 'Попит', 'Кількість', 'Від', 'До', 'Штрих-код товару'],
        )
        rows = table.xpath('./tbody/tr')
        moves = picking.move_ids_without_package
        self.assertEqual(len(rows), len(moves))
        for row, move in zip(rows, moves):
            cells = row.xpath('./td')
            self.assertEqual(len(cells), 6)
            self.assertEqual(cells[0].text_content().strip(), move.product_id.display_name)
            self.assertFalse(cells[0].xpath('.//br'))
            expected = self.env['ir.qweb.field.float'].record_to_html(
                move, 'product_uom_qty', {},
            )
            self.assertEqual(cells[1].xpath('./span')[0].text_content(), str(expected))
            self.assertEqual(cells[2].get('name'), 'td_actual_quantity')
            self.assertEqual(cells[2].text_content().strip(), '')
            self.assertFalse(list(cells[2]))
            self.assertIn(move.location_id.display_name, cells[3].text_content())
            self.assertIn(move.location_dest_id.display_name, cells[4].text_content())
            self.assertEqual(bool(cells[5].xpath('.//img')), bool(move.product_id.barcode))
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

    def test_product_name_is_not_repeated_from_picking_description(self):
        product = self.products[0]
        picking = self._picking([(product, 3, 10), (product, 5, 20)])
        moves = picking.move_ids_without_package
        moves[0].description_picking = product.display_name
        moves[1].description_picking = 'Description that must not be printed'
        rows = self._assert_operations(picking)
        self.assertEqual(len(rows), 2)
        for row in rows:
            product_cell = row.xpath('./td')[0]
            self.assertEqual(product_cell.text_content().count(product.display_name), 1)
            self.assertNotIn(moves[1].description_picking, product_cell.text_content())

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

    def test_packaging_is_absent_and_quantity_stays_blank(self):
        self.env.user.groups_id |= self.env.ref('uom.group_uom')
        self.env.user.groups_id |= self.env.ref('product.group_stock_packaging')
        product = self.products[0]
        packaging = self.env['product.packaging'].create({
            'name': 'REPORT PACKAGING 250', 'product_id': product.id, 'qty': 250,
        })
        picking = self._picking([(product, 1000, 10)])
        move = picking.move_ids_without_package
        move.product_packaging_id = packaging
        self.env['stock.move.line'].create({
            'move_id': move.id, 'picking_id': picking.id,
            'product_id': product.id, 'product_uom_id': product.uom_id.id,
            'quantity': 750, 'location_id': self.source.id,
            'location_dest_id': self.destination.id,
        })
        self.assertEqual(move.quantity, 750)
        self.assertEqual(move.product_packaging_quantity, 3)
        self.assertEqual(move.product_packaging_qty, 4)
        rows = self._assert_operations(picking)
        table = rows[0].getparent().getparent()
        quantity_cell = rows[0].xpath('./td')[2]
        self.assertEqual(quantity_cell.text_content().strip(), '')
        self.assertFalse(list(quantity_cell))
        self.assertNotIn(move.product_uom.display_name, quantity_cell.text_content())
        self.assertNotRegex(table.text_content(), r'(?<!\d)750(?:[.,]0+)?(?!\d)')
        self.assertNotIn(packaging.name, table.text_content())
        self.assertNotRegex(table.text_content(), r'(?<!\d)[34](?:[.,]0+)?(?!\d)')
        self.assertNotIn('(', quantity_cell.text_content())
        self.assertNotIn(')', quantity_cell.text_content())

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
