from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestStockPickingBoxCount(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create(
            {'name': 'Product Vataga Box Count Vendor'}
        )
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.incoming_picking_type = cls.env.ref('stock.picking_type_in')
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')

    def _create_incoming_picking(self, **extra_vals):
        vals = {
            'partner_id': self.partner.id,
            'picking_type_id': self.incoming_picking_type.id,
            'location_id': self.supplier_location.id,
            'location_dest_id': self.stock_location.id,
        }
        vals.update(extra_vals)
        return self.env['stock.picking'].create(vals)

    def test_incoming_picking_create_sets_default_box_count(self):
        picking = self._create_incoming_picking()

        self.assertEqual(picking.box_count, 1)

    def test_box_count_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self._create_incoming_picking(box_count=0)

    def test_purchase_confirm_creates_incoming_picking_with_box_count(self):
        if 'purchase.order' not in self.env.registry:
            self.skipTest('purchase is not installed')

        product = self.env['product.product'].create(
            {
                'name': 'Product Vataga Box Count Purchase Product',
                'type': 'consu',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )
        purchase_order = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product.id,
                            'name': product.display_name,
                            'product_qty': 1,
                            'product_uom': self.unit_uom.id,
                            'price_unit': 1,
                            'date_planned': fields.Datetime.now(),
                        }
                    )
                ],
            }
        )

        purchase_order.button_confirm()

        self.assertTrue(purchase_order.picking_ids)
        self.assertTrue(
            all(picking.box_count == 1 for picking in purchase_order.picking_ids)
        )
