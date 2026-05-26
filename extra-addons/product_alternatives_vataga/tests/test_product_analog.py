from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestProductAnalog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductAnalog = cls.env['product.analog']
        cls.ProductAnalytic = cls.env['product.analytic']
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

    def test_read_group_marks_analog_product_comment(self):
        analog_product = self.Product.create(
            {
                'name': 'Grouped analog product',
                'uom_id': self.unit_uom.id,
                'uom_po_id': self.unit_uom.id,
            }
        )
        self.ProductAnalytic.create(
            {
                'product_id': analog_product.id,
                'sale_contract_id': self.sale_contract.id,
            }
        )
        self.ProductAnalog.create(
            {
                'product_tmpl_id': self.main_product.product_tmpl_id.id,
                'product_id': analog_product.id,
            }
        )

        groups = self.ProductAnalytic.read_group(
            [('product_id', '=', analog_product.id)],
            ['demand_comment:max'],
            ['product_id'],
        )

        self.assertEqual(groups[0]['demand_comment'], 'Need substitute (A)')
