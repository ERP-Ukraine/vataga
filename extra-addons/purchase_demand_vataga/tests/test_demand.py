from odoo.addons.sale_demand_vataga.tests.test_demand import TestDemand
from odoo.tests.common import tagged
from odoo import Command


@tagged('post_install', '-at_install')
class TestDemandPurchase(TestDemand):
    def setUp(self):
        super().setUp()
        Product = self.env['product.product']
        MRPBOM = self.env['mrp.bom']
        self.kit_product_2 = Product.create({'name': 'Test Kit Product 2'})
        MRPBOM.create(
            {
                'product_qty': 2,
                'product_tmpl_id': self.kit_product_2.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {
                            'product_id': self.buy_product1.id,
                            'product_qty': 1,
                            'product_uom_id': self.dozen_uom.id,
                        }
                    ),
                ],
                'type': 'phantom',
            }
        )
        while MRPBOM.search([('need_update_to_purchase', '=', True)]):
            MRPBOM._cron_create_total_product_line_ids()

    def test_purchase_demand_without_sale(self):
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        result = purchase._get_product_catalog_order_line_info([self.sell_product_1.id])
        self.assertEqual(result[self.sell_product_1.id].get('demand'), 0)
        self.assertFalse(result[self.sell_product_1.id].get('in_contract'))

    def test_purchase_demand(self):
        self.test_demand_from_sale_order_line()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        result = purchase._get_product_catalog_order_line_info([self.sell_product_1.id])
        self.assertEqual(result[self.sell_product_1.id].get('demand'), 5)

    def test_purchase_demand_with_invoice(self):
        self.test_demand_from_sale_order_line()
        self._create_test_vendor_bill(self.sell_analytic_1)
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        result = purchase._get_product_catalog_order_line_info([self.sell_product_1.id])
        self.assertEqual(result[self.sell_product_1.id].get('demand'), 4)

    def test_purchase_demand_for_kit_product(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_2.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 1,
                        }
                    ),
                ],
            }
        ).action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        result = purchase._get_product_catalog_order_line_info([self.kit_product.id])
        self.assertEqual(result[self.kit_product.id].get('demand'), 24)
        purchase.order_line = [Command.create({'product_id': self.kit_product.id, 'product_qty': 10})]
        result = purchase._get_product_catalog_order_line_info([self.kit_product.id])
        self.assertEqual(result[self.kit_product.id].get('demand'), 14)
        result = purchase._get_product_catalog_order_line_info([self.buy_product1.id])
        self.assertEqual(result[self.buy_product1.id].get('demand'), 70)

    def test_purchase_demand_for_kit_product_different_uom(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_2.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 1,
                        }
                    ),
                ],
            }
        ).action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        result = purchase._get_product_catalog_order_line_info([self.kit_product_2.id])
        self.assertEqual(
            result[self.kit_product_2.id].get('demand'), 20
        )
        purchase.order_line = [
            Command.create({'product_id': self.kit_product_2.id, 'product_qty': 10})
        ]
        result = purchase._get_product_catalog_order_line_info([self.kit_product_2.id])
        self.assertEqual(
            result[self.kit_product_2.id].get('demand'), 10
        )
        result = purchase._get_product_catalog_order_line_info([self.buy_product1.id])
        self.assertEqual(
            result[self.buy_product1.id].get('demand'), 60
        )

    def test_qty_received(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_2.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 1,
                        }
                    ),
                ],
            }
        ).action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        purchase.order_line = [
            Command.create({'product_id': self.buy_product1.id, 'product_qty': 1})
        ]
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        product_analytic = self.env['product.analytic'].search(
            [
                ('sale_contract_id', '=', self.sell_analytic_1.id),
                ('product_id', '=', self.buy_product1.id),
            ]
        )
        self.assertEqual(product_analytic.qty_received, 1)

    def test_qty_received_with_kit_product(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_2.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 1,
                        }
                    ),
                ],
            }
        ).action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        purchase.order_line = [
            Command.create({'product_id': self.kit_product.id, 'product_qty': 1})
        ]
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        product_analytic = self.env['product.analytic'].search(
            [('sale_contract_id', '=', self.sell_analytic_1.id), ('product_id', '=', self.buy_product1.id)]
        )
        self.assertEqual(product_analytic.qty_received, 5)

    def test_qty_received_with_kit_product_different_uom(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_2.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 1,
                        }
                    ),
                ],
            }
        ).action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': self.sell_analytic_1.id,
            }
        )
        purchase.order_line = [
            Command.create({'product_id': self.kit_product_2.id, 'product_qty': 1})
        ]
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        product_analytic = self.env['product.analytic'].search(
            [
                ('sale_contract_id', '=', self.sell_analytic_1.id),
                ('product_id', '=', self.buy_product1.id),
            ]
        )
        self.assertEqual(product_analytic.qty_received, 6)
