from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestProductAnalog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductAnalog = cls.env['product.analog']
        cls.ProductAnalytic = cls.env['product.analytic']
        cls.MrpBom = cls.env['mrp.bom']
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.meter_uom = cls.env.ref('uom.product_uom_meter')
        cls.analytic_plan = cls.env['account.analytic.plan'].create(
            {'name': 'Product Analog Test Plan'}
        )
        cls.sale_contract = cls.env['account.analytic.account'].create(
            {
                'name': 'Product Analog Test Contract',
                'plan_id': cls.analytic_plan.id,
                'seller_analytic_comment': 'Need substitute',
            }
        )
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

    def test_demand_comment_marks_analog_product(self):
        analog_product = self.Product.create(
            {
                'name': 'Demand analog product',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )
        product_analytic = self.ProductAnalytic.create(
            {
                'product_id': analog_product.id,
                'sale_contract_id': self.sale_contract.id,
            }
        )
        self.assertEqual(product_analytic.demand_comment, 'Need substitute')

        self.ProductAnalog.create(
            {
                'product_tmpl_id': self.main_product.product_tmpl_id.id,
                'product_id': analog_product.id,
            }
        )

        self.assertEqual(product_analytic.demand_comment, 'Need substitute (A)')

    def test_bom_line_shows_component_analogs(self):
        finished_product = self.Product.create(
            {
                'name': 'Finished BOM product',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )
        analog_product = self.Product.create(
            {
                'name': 'BOM analog product',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )
        self.ProductAnalog.create(
            {
                'product_tmpl_id': self.main_product.product_tmpl_id.id,
                'product_id': analog_product.id,
            }
        )

        bom = self.MrpBom.create(
            {
                'product_tmpl_id': finished_product.product_tmpl_id.id,
                'bom_line_ids': [
                    (0, 0, {'product_id': self.main_product.id}),
                ],
            }
        )
        bom_line = bom.bom_line_ids

        self.assertEqual(bom_line.analog_marker, '(A)')
        self.assertEqual(bom_line.analog_product_ids, analog_product)
        self.assertIn(analog_product.display_name, bom_line.analog_product_names)
