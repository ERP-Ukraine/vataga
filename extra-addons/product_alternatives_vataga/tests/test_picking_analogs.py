from lxml import etree, html

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPickingAnalogs(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1,
        )
        cls.plain, cls.single, cls.multiple, cls.barcode, cls.no_barcode, cls.indirect = (
            cls.env['product.product'].create([
                {'name': name, 'default_code': code, 'detailed_type': 'product', 'barcode': barcode}
                for name, code, barcode in [
                    ('Plain product', 'TST-PA1', False),
                    ('Single analog product', 'TST-PA2', False),
                    ('Multiple analog product', 'TST-PA3', False),
                    ('Analog with barcode', 'TST-PA4', '2000000000848'),
                    ('Analog without barcode', 'TST-PA5', False),
                    ('Indirect analog', 'TST-PA6', False),
                ]
            ])
        )
        cls.env['product.analog'].create([
            {'product_tmpl_id': original.product_tmpl_id.id, 'product_id': analog.id}
            for original, analog in [
                (cls.single, cls.no_barcode), (cls.multiple, cls.barcode),
                (cls.multiple, cls.no_barcode), (cls.barcode, cls.indirect),
            ]
        ])

    def _picking(self, picking_type, products, detail_count=None):
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': (picking_type.default_location_src_id or self.env.ref('stock.stock_location_suppliers')).id,
            'location_dest_id': (picking_type.default_location_dest_id or self.env.ref('stock.stock_location_customers')).id,
        })
        for index, product in enumerate(products, start=1):
            move = self.env['stock.move'].create({
                'name': product.display_name, 'sequence': index,
                'product_id': product.id, 'product_uom': product.uom_id.id,
                'product_uom_qty': 10 * index, 'picking_id': picking.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
            })
            if detail_count is None or index <= detail_count:
                self.env['stock.move.line'].create({
                    'move_id': move.id, 'picking_id': picking.id,
                    'product_id': product.id, 'product_uom_id': product.uom_id.id,
                    'quantity': index, 'location_id': move.location_id.id,
                    'location_dest_id': move.location_dest_id.id,
                })
        return picking

    def _render(self, picking, analogs=True):
        name = 'product_alternatives_vataga.report_picking_analogs' if analogs else 'stock.report_picking'
        content, _ = self.env['ir.actions.report']._render_qweb_html(name, picking.ids)
        return html.fromstring(content)

    def _stock_snapshot(self, picking):
        products = self.plain | self.single | self.multiple | self.barcode | self.no_barcode | self.indirect
        return (
            picking.move_ids.read(['product_id', 'product_uom_qty', 'quantity', 'state']),
            picking.move_line_ids.read(['product_id', 'quantity', 'location_id', 'location_dest_id']),
            self.env['stock.quant'].search_read(
                [('product_id', 'in', products.ids)], ['quantity', 'reserved_quantity'], order='id',
            ),
        )

    def _assert_analog_rows(self, picking, expected):
        doc = self._render(picking)
        rows = doc.xpath('//tr[@name="analog_row"]')
        self.assertEqual(len(rows), len(expected))
        for row, (original, analog) in zip(rows, expected):
            product, demand, quantity, source, destination, *rest = row.xpath('./td')
            self.assertIn('background-color: #F2F2F2;', row.get('style', ''))
            self.assertEqual(product.xpath('.//strong/text()'), ['Аналог'])
            self.assertEqual(product.xpath('./strong')[0].tail, ': ')
            self.assertEqual(product.text_content().strip(), f'Аналог: {analog.display_name}')
            self.assertEqual(demand.text_content().strip(), '0')
            self.assertFalse(quantity.text_content().strip())
            self.assertEqual(source.text_content().strip(), picking.location_id.display_name)
            self.assertEqual(destination.text_content().strip(), picking.location_dest_id.display_name)
            barcode_cell = rest[-1]
            self.assertIn('background-color: #FFFFFF;', barcode_cell.get('style', ''))
            self.assertEqual(bool(barcode_cell.xpath('.//img')), bool(analog.barcode))
            previous = row.getprevious()
            while previous.get('name') == 'analog_row':
                previous = previous.getprevious()
            self.assertIn(original.display_name, previous.xpath('./td[1]')[0].text_content())
        self.assertNotIn(self.indirect.display_name, doc.text_content())
        return doc

    def test_prints_direct_analogs_for_each_original_row_without_stock_changes(self):
        products = self.plain + self.single + self.multiple + self.multiple
        self.assertEqual((self.barcode | self.no_barcode).mapped('qty_available'), [0, 0])
        for picking_type in [self.warehouse.in_type_id, self.warehouse.out_type_id, self.warehouse.int_type_id]:
            with self.subTest(picking_type=picking_type.code):
                picking = self._picking(picking_type, products)
                before = self._stock_snapshot(picking)
                ordinary = self._render(picking, analogs=False)
                self.assertFalse(ordinary.xpath('//tr[@name="analog_row"]'))
                self.assertNotIn(self.no_barcode.display_name, ordinary.text_content())
                self._assert_analog_rows(picking, [(self.single, self.no_barcode)] + [
                    (self.multiple, analog)
                    for _ in range(2)
                    for analog in self.multiple.product_tmpl_id.analog_line_ids.product_id
                ])
                self.assertEqual(self._stock_snapshot(picking), before)

    def test_no_analogs_preserves_original_tables(self):
        def tables(document):
            return [
                (table.attrib, [
                    [(cell.attrib, ' '.join(cell.text_content().split())) for cell in row]
                    for row in table.xpath('./*/tr')
                ])
                for table in document.xpath('//table')
            ]

        for picking_type in [self.warehouse.in_type_id, self.warehouse.out_type_id, self.warehouse.int_type_id]:
            with self.subTest(picking_type=picking_type.code):
                picking = self._picking(picking_type, self.plain)
                ordinary = self._render(picking, analogs=False)
                with_analogs = self._render(picking)
                self.assertEqual(tables(with_analogs), tables(ordinary))

    def test_analogs_print_without_detailed_operations(self):
        for detail_count in (0, 1):
            picking = self._picking(self.warehouse.out_type_id, self.plain | self.single, detail_count=detail_count)
            self.assertEqual(len(picking.move_line_ids), detail_count)
            self._assert_analog_rows(picking, [(self.single, self.no_barcode)])

    def test_planned_products_print_without_details_or_analogs(self):
        for picking_type in (self.warehouse.in_type_id, self.warehouse.out_type_id):
            with self.subTest(picking_type=picking_type.code):
                picking = self._picking(picking_type, self.plain, detail_count=0)
                doc = self._render(picking)
                rows = doc.xpath('//table[@name="internal_move_table"]/tbody/tr')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].xpath('./td[1]')[0].text_content().strip(), self.plain.display_name)
                self.assertFalse(doc.xpath('//tr[@name="analog_row"]'))

    def test_operations_view_loads_the_read_only_widget(self):
        view = self.env['stock.picking'].get_view(view_id=self.env.ref('stock.view_picking_form').id)
        fields = etree.fromstring(view['arch']).xpath(
            "//page[@name='operations']//field[@name='move_ids_without_package']/tree/field[@name='analog_marker']",
        )
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].get('widget'), 'product_analog_marker')
        self.assertEqual(fields[0].get('readonly'), 'True')
        self.assertNotIn('select', fields[0].get('options'))

        picking = self._picking(self.warehouse.int_type_id, self.plain | self.multiple)
        plain, multiple = picking.move_ids_without_package
        self.assertFalse(plain.analog_marker)
        self.assertEqual(multiple.analog_marker, '(A)')
        self.assertEqual(
            {item['id'] for item in multiple.analog_product_data}, {self.barcode.id, self.no_barcode.id},
        )
