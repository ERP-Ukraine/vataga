import re

from lxml import html as lxml_html

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


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
    def _create_picking(cls, product, planned_quantity, actual_quantities, state):
        picking = cls.env['stock.picking'].create(
            {
                'picking_type_id': cls.picking_type.id,
                'location_id': cls.source_location.id,
                'location_dest_id': cls.destination_location.id,
                'user_id': cls.env.user.id,
            }
        )
        move = cls.env['stock.move'].create(
            {
                'name': product.display_name,
                'picking_id': picking.id,
                'product_id': product.id,
                'product_uom_qty': planned_quantity,
                'product_uom': product.uom_id.id,
                'location_id': cls.source_location.id,
                'location_dest_id': cls.destination_location.id,
            }
        )
        for quantity in actual_quantities:
            cls.env['stock.move.line'].create(
                {
                    'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'quantity': quantity,
                    'location_id': cls.source_location.id,
                    'location_dest_id': cls.destination_location.id,
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

    def _product_rows(self, rendered_html, product):
        document = lxml_html.fromstring(rendered_html)
        return document.xpath(
            "//tr[contains(normalize-space(string(.)), $product_name)]",
            product_name=product.name,
        )

    def _assert_row_contains_number(self, row, number):
        row_text = ' '.join(''.join(row.itertext()).split())
        number_pattern = re.compile(rf'(?<!\d){number:g}(?:[\.,]0+)?(?!\d)')
        self.assertRegex(row_text, number_pattern)

    def test_done_picking_uses_actual_move_quantity_once(self):
        product = self._create_product(
            'Done report quantity product',
            'REPORT-DONE-QTY',
            '2000000000206',
        )
        picking, move = self._create_picking(
            product,
            planned_quantity=0,
            actual_quantities=(2, 3),
            state='done',
        )

        self.assertEqual(picking.state, 'done')
        self.assertEqual(move.product_uom_qty, 0)
        self.assertEqual(move.quantity, 5)
        self.assertEqual(len(move.move_line_ids), 2)

        rendered_html = self._render_delivery_report(picking)
        product_rows = self._product_rows(rendered_html, product)

        self.assertEqual(len(product_rows), 1)
        self._assert_row_contains_number(product_rows[0], 5)
        self._assert_row_contains_number(product_rows[0], 50)

    def test_open_picking_uses_planned_move_quantity(self):
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

        rendered_html = self._render_delivery_report(picking)
        product_rows = self._product_rows(rendered_html, product)

        self.assertEqual(len(product_rows), 1)
        self._assert_row_contains_number(product_rows[0], 7)
        self._assert_row_contains_number(product_rows[0], 70)

    def test_current_localization_quantity_template_accepts_vataga_inheritance(self):
        # Model the current ERP UA quantity and line-total expressions without
        # depending on the rest of the localization's report layout.
        View = self.env['ir.ui.view']
        parent = View.create({
            'name': 'Current delivery quantity template',
            'key': 'product_vataga.test_current_delivery_quantity',
            'type': 'qweb',
            'arch_db': """
                <t t-name="product_vataga.test_current_delivery_quantity">
                    <table>
                        <t t-set="lines" t-value="o.move_ids.filtered(
                            lambda move: move.state != 'cancel' and
                                (move.quantity if o.state == 'done' else move.product_uom_qty)
                        )"/>
                        <tr t-foreach="lines" t-as="move">
                            <td><span t-esc="move.product_id.name"/></td>
                            <td><span t-esc="move.quantity if o.state == 'done' else move.product_uom_qty"/></td>
                            <td><span t-esc="unit_cost * (move.quantity if o.state == 'done' else move.product_uom_qty)"/></td>
                        </tr>
                    </table>
                </t>
            """,
        })
        addon_view = self.env.ref('product_vataga.report_deliveryslip_actual_quantity')
        View.create({
            'name': 'Vataga delivery quantity inheritance',
            'type': 'qweb',
            'inherit_id': parent.id,
            'arch_db': addon_view.arch_db,
        })
        product = self._create_product('VATAGA QWeb quantity', 'CURRENT-QWEB-QTY', '2000000000244')
        for planned, actual, state, quantity, total in (
            (7, (), 'confirmed', 7, 70),
            (0, (2, 3), 'done', 5, 50),
        ):
            with self.subTest(state=state):
                picking, _move = self._create_picking(product, planned, actual, state)
                report = self.env['ir.qweb']._render(parent.key, {'o': picking, 'unit_cost': 10})
                rows = self._product_rows(str(report), product)
                self.assertEqual(len(rows), 1)
                self.assertEqual([int(float(cell.text_content())) for cell in rows[0][1:]], [quantity, total])
