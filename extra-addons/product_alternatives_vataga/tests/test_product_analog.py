from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestProductAnalog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductAnalog = cls.env['product.analog']
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.meter_uom = cls.env.ref('uom.product_uom_meter')
        cls.main_product = cls.Product.create(
            {
                'name': 'Main product',
                'uom_id': cls.unit_uom.id,
                'uom_po_id': cls.unit_uom.id,
            }
        )

    def test_create_analog_with_same_uom(self):
        analog_product = self.Product.create(
            {
                'name': 'Analog product',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )

        analog_line = self.ProductAnalog.create(
            {
                'product_tmpl_id': self.main_product.product_tmpl_id.id,
                'product_id': analog_product.id,
            }
        )

        self.assertEqual(analog_line.uom_id, self.unit_uom)

    def test_create_analog_with_different_uom_is_rejected(self):
        analog_product = self.Product.create(
            {
                'name': 'Meter analog product',
                'uom_id': self.meter_uom.id,
                'uom_po_id': self.meter_uom.id,
            }
        )

        with self.assertRaises(ValidationError):
            self.ProductAnalog.create(
                {
                    'product_tmpl_id': self.main_product.product_tmpl_id.id,
                    'product_id': analog_product.id,
                }
            )
