from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestBomOverviewWarehouses(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse_1, cls.warehouse_2 = cls.env['stock.warehouse'].create([
            {
                'name': 'BoM Overview RND',
                'code': 'BMRND',
                'company_id': cls.env.company.id,
            },
            {
                'name': 'BoM Overview MH',
                'code': 'BMMH',
                'company_id': cls.env.company.id,
            },
        ])
        cls.product = cls.env['product.product'].create({
            'name': 'BoM Overview Warehouse Test Product',
            'detailed_type': 'product',
        })
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'product_id': cls.product.id,
            'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'company_id': cls.env.company.id,
        })
        quant = cls.env['stock.quant']
        quant._update_available_quantity(cls.product, cls.warehouse_1.lot_stock_id, 146)
        quant._update_available_quantity(cls.product, cls.warehouse_2.lot_stock_id, 1479)
        move = cls.env['stock.move'].create({
            'name': 'Reserve 613 units in MH',
            'product_id': cls.product.id,
            'product_uom': cls.product.uom_id.id,
            'product_uom_qty': 613,
            'location_id': cls.warehouse_2.lot_stock_id.id,
            'location_dest_id': cls.env.ref('stock.stock_location_customers').id,
            'picking_type_id': cls.warehouse_2.out_type_id.id,
            'company_id': cls.env.company.id,
        })
        move._action_confirm()
        move._action_assign()
        cls.report = cls.env['report.mrp.report_bom_structure']

    def _get_report_line(self, warehouse_ids, legacy=False):
        return self.report.with_context(
            warehouse=warehouse_ids[0],
            warehouse_ids=[] if legacy else warehouse_ids,
        ).get_html(self.bom.id, 1, self.product.id)['lines']

    def test_multiple_warehouses_sum_quantities(self):
        for warehouse_ids in (
            [self.warehouse_1.id, self.warehouse_2.id],
            [self.warehouse_2.id, self.warehouse_1.id],
        ):
            with self.subTest(warehouse_ids=warehouse_ids):
                line = self._get_report_line(warehouse_ids)
                self.assertEqual(line['quantity_available'], 1012)
                self.assertEqual(line['quantity_on_hand'], 1625)

    def test_single_warehouse_preserves_legacy_quantities(self):
        for warehouse, available, on_hand in (
            (self.warehouse_1, 146, 146),
            (self.warehouse_2, 866, 1479),
        ):
            with self.subTest(warehouse=warehouse.name):
                line = self._get_report_line([warehouse.id])
                legacy_line = self._get_report_line([warehouse.id], legacy=True)
                self.assertEqual(line['quantity_available'], available)
                self.assertEqual(line['quantity_on_hand'], on_hand)
                for key in ('quantity_available', 'quantity_on_hand'):
                    self.assertEqual(line[key], legacy_line[key])
