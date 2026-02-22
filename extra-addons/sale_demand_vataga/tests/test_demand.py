from odoo import Command
from odoo.tests.common import TransactionCase, tagged
from odoo import fields


@tagged('post_install', '-at_install')
class TestDemand(TransactionCase):
    def setUp(self):
        super().setUp()
        Product = self.env['product.product']
        MRPBOM = self.env['mrp.bom']
        AccountAnalytic = self.env['account.analytic.account']
        self.company = self.env['res.company'].create({'name': 'Test company'})
        other_company = self.env['res.company'].create({'name': 'Test company Other'})
        self.env = self.env(
            context=dict(self.env.context, allowed_company_ids=[self.company.id])
        )
        account = self.env['account.account'].create(
            {
                'code': '22.111.333',
                'name': 'test account',
                'account_type': 'expense',
                'reconcile': True,
            }
        )
        self.env['account.account'].create(
            {
                'code': '22.111.332',
                'name': 'test account',
                'account_type': 'liability_payable',
                'reconcile': True,
            }
        )
        self.env['account.journal'].create(
            {
                'name': 'Test',
                'type': 'purchase',
                'code': 'TEST',
                'company_id': self.env.company.id,
                'default_account_id': account.id,
            }
        )
        sell_analytic_plan = self.env.ref(
            'analytic_vataga.account_analytic_plan_seller_contract'
        )
        other_analytic_plan = self.env['account.analytic.plan'].create(
            {
                'name': 'Plan',
            }
        )
        self.dozen_uom = self.env.ref('uom.product_uom_dozen')
        self.sell_analytic_1 = AccountAnalytic.create(
            {'name': 'sell 1', 'plan_id': sell_analytic_plan.id}
        )
        self.sell_analytic_2 = AccountAnalytic.create(
            {'name': 'sell 2', 'plan_id': sell_analytic_plan.id}
        )
        self.other_analytic = AccountAnalytic.create(
            {'name': 'Analytic', 'plan_id': other_analytic_plan.id}
        )
        self.sell_product_1 = Product.create({'name': 'Sell Product 1'})
        self.sell_product_2 = Product.create({'name': 'Sell Product 2'})
        self.sell_product_3 = Product.create({'name': 'Sell Product 3'})

        bom_product_1 = Product.create({'name': 'Bom Product 1'})
        self.buy_product1 = Product.create({'name': 'Buy Product 1'})
        self.buy_product2 = Product.create({'name': 'Buy Product 2'})
        self.kit_product = Product.create({'name': 'Kit Product'})
        MRPBOM.create(
            {
                'product_id': self.sell_product_1.id,
                'product_tmpl_id': self.sell_product_1.product_tmpl_id.id,
                'company_id': other_company.id,
                'bom_line_ids': [Command.create({'product_id': self.buy_product1.id})],
            }
        )
        MRPBOM.create(
            {
                'product_id': self.sell_product_2.id,
                'product_tmpl_id': self.sell_product_2.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {
                            'product_id': bom_product_1.id,
                            'product_qty': 2,
                            'product_uom_id': self.dozen_uom.id,
                        }
                    )
                ],
            }
        )
        MRPBOM.create(
            {
                'product_id': bom_product_1.id,
                'product_tmpl_id': bom_product_1.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {'product_id': self.buy_product1.id, 'product_qty': 5}
                    ),
                    Command.create(
                        {
                            'product_id': self.buy_product2.id,
                            'product_qty': 1,
                            'product_uom_id': self.dozen_uom.id,
                        }
                    ),
                ],
            }
        )
        MRPBOM.create(
            {
                'product_qty': 2,
                'product_tmpl_id': self.sell_product_3.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {'product_id': self.buy_product1.id, 'product_qty': 3}
                    )
                ],
            }
        )
        MRPBOM.create(
            {
                'product_qty': 1,
                'product_tmpl_id': self.kit_product.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {'product_id': self.buy_product1.id, 'product_qty': 5}
                    ),
                    Command.create(
                        {
                            'product_id': self.buy_product2.id,
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
        self.partner = self.env['res.partner'].create({'name': 'Test Partner'})

    def test_demand_from_sale_order_line(self):
        sale_order = self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_1.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 3,
                        }
                    ),
                    Command.create(
                        {
                            'product_id': self.sell_product_1.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_2.id}': 100
                            },
                            'product_uom': self.dozen_uom.id,
                            'product_uom_qty': 1,
                        }
                    ),
                    Command.create(
                        {
                            'product_id': self.sell_product_1.id,
                            'analytic_distribution': {f'{self.other_analytic.id}': 100},
                            'product_uom_qty': 39,
                        }
                    ),
                ],
            }
        )
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        all_analytics = (
            self.sell_analytic_1 + self.sell_analytic_2 + self.other_analytic
        )
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', 'in', all_analytics.ids)]
        )
        self.assertEqual(len(product_analytics), 0)
        sale_order.action_confirm()
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ],
        ):
            self.env['product.analytic']._cron_create_product_analytic()
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', 'in', all_analytics.ids)]
        )
        self.assertEqual(len(product_analytics), 2)
        product_analytic_1 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.sell_analytic_1
        )
        self.assertEqual(product_analytic_1.demand, 3)
        product_analytic_2 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.sell_analytic_2
        )
        self.assertEqual(product_analytic_2.demand, 12)
        product_analytic_3 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.other_analytic
        )
        self.assertEqual(len(product_analytic_3), 0)

        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_1.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 2,
                        }
                    )
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
        self.assertEqual(product_analytic_1.demand, 5)

    def test_demand_for_buy_products(self):
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
                    Command.create(
                        {
                            'product_id': self.sell_product_3.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 2,
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
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', '=', self.sell_analytic_1.id)]
        )
        self.assertEqual(len(product_analytics), 2)
        buy_product1_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product1
        )
        self.assertEqual(buy_product1_analytic.demand, 123)
        buy_product2_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product2
        )
        self.assertEqual(buy_product2_analytic.demand, 288)

    def _create_test_vendor_bill(self, analytic, dozen=False, product=False):
        vendor_bill = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'invoice_date': '2023-04-01',
                'date': '2023-03-15',
                'invoice_line_ids': [
                    Command.create(
                        {
                            'product_id': product.id if product else self.sell_product_1.id,
                            'quantity': 1,
                            'name': 'test',
                            'price_unit': 4000,
                            'analytic_distribution': {f'{analytic.id}': 100},
                            'product_uom_id': self.dozen_uom.id
                            if dozen
                            else self.sell_product_1.uom_id.id,
                        }
                    )
                ],
            }
        )
        vendor_bill.action_post()
        return vendor_bill

    def _create_refund_vendor_bill(self, bill):
        move_reversal = (
            self.env['account.move.reversal']
            .with_context(active_model='account.move', active_ids=bill.ids)
            .create(
                {
                    'date': fields.Date.today(),
                    'journal_id': bill.journal_id.id,
                }
            )
        )
        reversal = move_reversal.reverse_moves()
        reversed_move = self.env['account.move'].browse(reversal['res_id'])
        reversed_move.action_post()

    def test_in_invoice_without_sale(self):
        self._create_test_vendor_bill(self.sell_analytic_1)
        self._create_test_vendor_bill(self.sell_analytic_2)
        self._create_test_vendor_bill(self.other_analytic)
        all_analytics = (
            self.sell_analytic_1 + self.sell_analytic_2 + self.other_analytic
        )
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', 'in', all_analytics.ids)]
        )
        self.assertEqual(len(product_analytics), 0)

    def test_in_invoice_with_sale(self):
        self.test_demand_from_sale_order_line()
        self._create_test_vendor_bill(self.sell_analytic_1)
        self._create_test_vendor_bill(self.sell_analytic_2, dozen=True)
        product_analytics = self.env['product.analytic'].search(
            [
                (
                    'sale_contract_id',
                    'in',
                    [self.sell_analytic_1.id, self.sell_analytic_2.id],
                )
            ]
        )
        self.assertEqual(len(product_analytics), 2)
        product_analytic_1 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.sell_analytic_1
        )
        self.assertEqual(product_analytic_1.in_invoice, 1)
        product_analytic_2 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.sell_analytic_2
        )
        self.assertEqual(product_analytic_2.in_invoice, 12)

    def test_with_difference_uom_in_bom(self):
        test_product = self.env['product.product'].create({'name': 'Test uom'})
        test_buy_product = self.env['product.product'].create(
            {'name': 'In meters', 'uom_id': self.env.ref('uom.product_uom_meter').id}
        )
        self.env['mrp.bom'].create(
            {
                'product_id': test_product.id,
                'product_tmpl_id': test_product.product_tmpl_id.id,
                'company_id': self.company.id,
                'bom_line_ids': [
                    Command.create(
                        {
                            'product_id': test_buy_product.id,
                            'product_uom_id': self.env.ref('uom.product_uom_km').id,
                            'product_qty': 2,
                        }
                    )
                ],
            }
        )
        while self.env['mrp.bom'].search([('need_update_to_purchase', '=', True)]):
            self.env['mrp.bom']._cron_create_total_product_line_ids()
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': test_product.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 2,
                        }
                    )
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
        product_analytic = self.env['product.analytic'].search(
            [
                (
                    'sale_contract_id',
                    '=',
                    self.sell_analytic_1.id,
                )
            ]
        )
        self.assertEqual(product_analytic.demand, 4000)

    def test_with_note_in_sale_order(self):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': self.sell_product_1.id,
                            'analytic_distribution': {
                                f'{self.sell_analytic_1.id}': 100
                            },
                            'product_uom_qty': 2,
                        }
                    ),
                    Command.create({'name': 'test note', 'display_type': 'line_note'}),
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
        product_analytic = self.env['product.analytic'].search(
            [
                (
                    'sale_contract_id',
                    '=',
                    self.sell_analytic_1.id,
                )
            ]
        )
        self.assertEqual(len(product_analytic), 1)
        self.assertEqual(product_analytic.demand, 2)

    def test_in_refund_with_sale(self):
        self.test_demand_from_sale_order_line()
        bill = self._create_test_vendor_bill(self.sell_analytic_1)
        self._create_refund_vendor_bill(bill)
        product_analytics = self.env['product.analytic'].search(
            [
                (
                    'sale_contract_id',
                    'in',
                    [self.sell_analytic_1.id, self.sell_analytic_2.id],
                )
            ]
        )
        product_analytic_1 = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.sell_product_1
            and analytic.sale_contract_id == self.sell_analytic_1
        )
        self.assertEqual(product_analytic_1.in_invoice, 0)

    def test_kit_product_in_invoice(self):
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
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', '=', self.sell_analytic_1.id)]
        )
        buy_product1_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product1
        )
        buy_product2_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product2
        )
        self._create_test_vendor_bill(self.sell_analytic_1, product=self.kit_product)
        self.assertEqual(buy_product1_analytic.in_invoice, 5)
        self.assertEqual(buy_product2_analytic.in_invoice, 12)

    def test_kit_product_in_refund(self):
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
        product_analytics = self.env['product.analytic'].search(
            [('sale_contract_id', '=', self.sell_analytic_1.id)]
        )
        buy_product1_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product1
        )
        buy_product2_analytic = product_analytics.filtered(
            lambda analytic: analytic.product_id == self.buy_product2
        )
        bill = self._create_test_vendor_bill(self.sell_analytic_1, product=self.kit_product)
        self._create_refund_vendor_bill(bill)
        self.assertEqual(buy_product1_analytic.in_invoice, 0)
        self.assertEqual(buy_product2_analytic.in_invoice, 0)
