from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase
from odoo.tests.common import Form


class TestProductAnalog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env['product.product']
        cls.ProductAnalog = cls.env['product.analog']
        cls.ProductAnalytic = cls.env['product.analytic']
        cls.MrpBom = cls.env['mrp.bom']
        cls.MrpProduction = cls.env['mrp.production']
        cls.StockMove = cls.env['stock.move']
        cls.unit_uom = cls.env.ref('uom.product_uom_unit')
        cls.meter_uom = cls.env.ref('uom.product_uom_meter')
        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.production_location = cls.env.ref('stock.location_production')
        cls.partner = cls.env['res.partner'].create({'name': 'Product Analog Partner'})
        cls.seller_analytic_plan = cls.env.ref(
            'analytic_vataga.account_analytic_plan_seller_contract'
        )
        cls.expense_account = cls.env['account.account'].create(
            {
                'code': 'PA0001',
                'name': 'Product analog expense',
                'account_type': 'expense',
                'reconcile': True,
            }
        )
        cls.env['account.account'].create(
            {
                'code': 'PA0002',
                'name': 'Product analog payable',
                'account_type': 'liability_payable',
                'reconcile': True,
            }
        )
        cls.purchase_journal = cls.env['account.journal'].create(
            {
                'name': 'Product Analog Purchases',
                'type': 'purchase',
                'code': 'PALT',
                'company_id': cls.env.company.id,
                'default_account_id': cls.expense_account.id,
            }
        )
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
        cls.main_product = cls._create_product('Main product')

    @classmethod
    def _create_product(cls, name, uom=None):
        uom = uom or cls.unit_uom
        return cls.Product.create(
            {
                'name': name,
                'uom_id': uom.id,
                'uom_po_id': uom.id,
            }
        )

    def _create_analog_line(self, main_product, analog_product, **extra_vals):
        vals = {
            'product_tmpl_id': main_product.product_tmpl_id.id,
            'product_id': analog_product.id,
        }
        vals.update(extra_vals)
        return self.ProductAnalog.create(vals)

    def _create_seller_contract(self, name):
        return self.env['account.analytic.account'].create(
            {
                'name': name,
                'plan_id': self.seller_analytic_plan.id,
            }
        )

    def _run_product_analytic_cron(self):
        while self.env['sale.order.line.purchase'].search(
            [
                ('sale_contract_id', '!=', False),
                ('product_analytic_id', '=', False),
                ('state', '=', 'sale'),
            ]
        ):
            self.ProductAnalytic._cron_create_product_analytic()

    def _create_sale_demand(self, product, contract, quantity):
        self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product.id,
                            'analytic_distribution': {str(contract.id): 100},
                            'product_uom_qty': quantity,
                        }
                    ),
                ],
            }
        ).action_confirm()
        self._run_product_analytic_cron()
        return self.ProductAnalytic.search(
            [
                ('product_id', '=', product.id),
                ('sale_contract_id', '=', contract.id),
            ],
            limit=1,
        )

    def _create_product_analytic(self, product, contract):
        return self.ProductAnalytic.create(
            {
                'product_id': product.id,
                'sale_contract_id': contract.id,
            }
        )

    def _create_vendor_bill(self, product, contract, quantity, move_type='in_invoice'):
        bill = self.env['account.move'].create(
            {
                'move_type': move_type,
                'partner_id': self.partner.id,
                'journal_id': self.purchase_journal.id,
                'invoice_date': fields.Date.today(),
                'date': fields.Date.today(),
                'seller_contract_id': contract.id,
                'invoice_line_ids': [
                    Command.create(
                        {
                            'product_id': product.id,
                            'quantity': quantity,
                            'name': product.display_name,
                            'price_unit': 1,
                            'account_id': self.expense_account.id,
                            'analytic_distribution': {str(contract.id): 100},
                            'product_uom_id': product.uom_id.id,
                        }
                    )
                ],
            }
        )
        bill.action_post()
        return bill

    def _create_received_purchase(self, product, contract, quantity):
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product.id,
                            'product_qty': quantity,
                            'price_unit': 1,
                        }
                    ),
                ],
            }
        )
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        return purchase

    def _recompute_analytic_rollups(self, *product_analytics):
        product_analytics = self.ProductAnalytic.browse(
            [analytic.id for analytic in product_analytics if analytic]
        )
        product_analytics._compute_numbers()
        product_analytics._compute_qty_received()

    def _pair_lines(self, product_a, product_b):
        return self.ProductAnalog.search(
            [
                '|',
                '&',
                ('product_tmpl_id', '=', product_a.product_tmpl_id.id),
                ('product_id', '=', product_b.id),
                '&',
                ('product_tmpl_id', '=', product_b.product_tmpl_id.id),
                ('product_id', '=', product_a.id),
            ]
        )

    def _create_production_from_bom(self, component_product, component_qty=1.0):
        finished_product = self._create_product(
            'MO finished product from BoM for %s' % component_product.name
        )
        bom = self.MrpBom.create(
            {
                'product_tmpl_id': finished_product.product_tmpl_id.id,
                'product_id': finished_product.id,
                'product_qty': 1.0,
                'product_uom_id': finished_product.uom_id.id,
                'bom_line_ids': [
                    Command.create(
                        {
                            'product_id': component_product.id,
                            'product_qty': component_qty,
                            'product_uom_id': component_product.uom_id.id,
                        }
                    ),
                ],
            }
        )
        with Form(self.MrpProduction) as production_form:
            production_form.product_id = finished_product
            production_form.bom_id = bom
            production_form.product_qty = 1.0
        production = production_form.save()
        move = production.move_raw_ids.filtered(
            lambda raw_move: raw_move.product_id == component_product
        )

        self.assertTrue(move, 'The manufacturing order must create raw moves from BoM')
        return production, bom, move

    def test_create_analog_with_same_uom_creates_reciprocal_line(self):
        product_a = self._create_product('Mutual product A')
        product_b = self._create_product('Mutual product B')

        analog_line = self._create_analog_line(product_a, product_b)
        reciprocal_line = analog_line.reciprocal_line_id

        self.assertTrue(analog_line.is_primary_link)
        self.assertFalse(reciprocal_line.is_primary_link)
        self.assertEqual(analog_line.uom_id, self.unit_uom)
        self.assertEqual(analog_line.product_tmpl_id, product_a.product_tmpl_id)
        self.assertEqual(analog_line.product_id, product_b)
        self.assertEqual(reciprocal_line.product_tmpl_id, product_b.product_tmpl_id)
        self.assertEqual(reciprocal_line.product_id, product_a)
        self.assertIn(analog_line, product_a.product_tmpl_id.analog_line_ids)
        self.assertIn(reciprocal_line, product_b.product_tmpl_id.analog_line_ids)

    def test_duplicate_links_are_not_created_from_either_side(self):
        product_a = self._create_product('Duplicate product A')
        product_b = self._create_product('Duplicate product B')

        analog_line = self._create_analog_line(product_a, product_b)
        same_side_line = self._create_analog_line(product_a, product_b)
        reverse_side_line = self._create_analog_line(product_b, product_a)
        pair_lines = self._pair_lines(product_a, product_b)

        self.assertEqual(same_side_line, analog_line)
        self.assertEqual(reverse_side_line, analog_line.reciprocal_line_id)
        self.assertEqual(len(pair_lines), 2)
        self.assertEqual(len(pair_lines.filtered('is_primary_link')), 1)

    def test_unlink_from_reciprocal_side_removes_both_lines(self):
        product_a = self._create_product('Unlink product A')
        product_b = self._create_product('Unlink product B')
        analog_line = self._create_analog_line(product_a, product_b)
        reciprocal_line = analog_line.reciprocal_line_id

        reciprocal_line.unlink()

        self.assertFalse(analog_line.exists())
        self.assertFalse(reciprocal_line.exists())
        self.assertFalse(self._pair_lines(product_a, product_b))

    def test_create_analog_with_different_uom_is_rejected(self):
        analog_product = self._create_product(
            'Meter analog product',
            uom=self.meter_uom,
        )

        with self.assertRaises(ValidationError):
            self._create_analog_line(self.main_product, analog_product)

    def test_create_analog_to_itself_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._create_analog_line(self.main_product, self.main_product)

    def test_backfill_creates_reciprocal_for_legacy_one_way_line(self):
        product_a = self._create_product('Legacy product A')
        product_b = self._create_product('Legacy product B')
        legacy_line = self.ProductAnalog.with_context(
            product_alternatives_skip_reciprocal_sync=True
        ).create(
            {
                'product_tmpl_id': product_a.product_tmpl_id.id,
                'product_id': product_b.id,
                'is_primary_link': True,
            }
        )
        self.assertFalse(legacy_line.reciprocal_line_id)

        self.ProductAnalog._backfill_reciprocal_links()
        legacy_line.invalidate_recordset(['reciprocal_line_id'])
        reciprocal_line = legacy_line.reciprocal_line_id

        self.assertTrue(legacy_line.is_primary_link)
        self.assertTrue(reciprocal_line)
        self.assertFalse(reciprocal_line.is_primary_link)
        self.assertEqual(reciprocal_line.product_tmpl_id, product_b.product_tmpl_id)
        self.assertEqual(reciprocal_line.product_id, product_a)

    def test_demand_comment_marks_only_real_analog_product(self):
        product_a = self._create_product('Demand main product')
        product_b = self._create_product('Demand analog product')
        main_product_analytic = self.ProductAnalytic.create(
            {
                'product_id': product_a.id,
                'sale_contract_id': self.sale_contract.id,
            }
        )
        analog_product_analytic = self.ProductAnalytic.create(
            {
                'product_id': product_b.id,
                'sale_contract_id': self.sale_contract.id,
            }
        )

        self._create_analog_line(product_a, product_b)
        main_product_analytic.invalidate_recordset(['demand_comment'])
        analog_product_analytic.invalidate_recordset(['demand_comment'])

        self.assertEqual(main_product_analytic.demand_comment, 'Need substitute')
        self.assertEqual(analog_product_analytic.demand_comment, 'Need substitute (A)')

    def test_bom_line_shows_component_analogs(self):
        finished_product = self._create_product('Finished BOM product')
        analog_product = self._create_product('BOM analog product')
        self._create_analog_line(self.main_product, analog_product)

        bom = self.MrpBom.create(
            {
                'product_tmpl_id': finished_product.product_tmpl_id.id,
                'bom_line_ids': [
                    (0, 0, {'product_id': self.main_product.id}),
                ],
            }
        )
        bom_line = bom.bom_line_ids

        self.assertTrue(bom_line.analog_marker.startswith('(A)'))
        self.assertEqual(bom_line.analog_product_ids, analog_product)
        self.assertIn(analog_product.display_name, bom_line.analog_product_names)

    def test_production_raw_move_can_be_replaced_with_analog(self):
        component_product = self._create_product('MO component A')
        analog_product = self._create_product('MO analog B')
        self._create_analog_line(component_product, analog_product)
        production, bom, move = self._create_production_from_bom(
            component_product,
            component_qty=3.0,
        )

        self.assertEqual(production.state, 'draft')
        self.assertEqual(move.analog_marker, '(A)')
        self.assertIn(
            {'id': analog_product.id, 'display_name': analog_product.display_name},
            move.analog_product_data,
        )
        original_qty = move.product_uom_qty

        move.action_replace_with_analog_product(analog_product.id)

        self.assertEqual(move.product_id, analog_product)
        self.assertEqual(move.product_uom_qty, original_qty)
        self.assertEqual(move.product_uom, analog_product.uom_id)
        self.assertEqual(bom.bom_line_ids.product_id, component_product)

    def test_production_raw_move_without_analogs_has_no_marker(self):
        component_product = self._create_product('MO component without analogs')
        production, bom, move = self._create_production_from_bom(component_product)

        self.assertEqual(production.state, 'draft')
        self.assertFalse(move.analog_marker)
        self.assertFalse(move.analog_product_data)

    def test_confirmed_production_raw_move_can_be_replaced_with_analog(self):
        component_product = self._create_product('Confirmed MO component A')
        analog_product = self._create_product('Confirmed MO analog B')
        self._create_analog_line(component_product, analog_product)
        production, bom, move = self._create_production_from_bom(
            component_product,
            component_qty=2.0,
        )
        self.env['stock.quant']._update_available_quantity(
            component_product,
            self.stock_location,
            10.0,
        )
        self.env['stock.quant']._update_available_quantity(
            analog_product,
            self.stock_location,
            10.0,
        )

        production.action_confirm()
        move._action_assign()
        self.assertIn(move.state, {'confirmed', 'partially_available', 'assigned'})
        original_qty = move.product_uom_qty

        move.action_replace_with_analog_product(analog_product.id)

        self.assertEqual(move.product_id, analog_product)
        self.assertEqual(move.product_uom_qty, original_qty)
        self.assertEqual(move.product_uom, analog_product.uom_id)
        self.assertEqual(bom.bom_line_ids.product_id, component_product)
        if move.move_line_ids:
            self.assertEqual(move.move_line_ids.product_id, analog_product)

    def test_production_raw_move_replacement_is_blocked_when_done_or_cancelled(self):
        component_product = self._create_product('Locked MO component A')
        analog_product = self._create_product('Locked MO analog B')
        self._create_analog_line(component_product, analog_product)

        for state in ('cancel', 'done'):
            production, bom, move = self._create_production_from_bom(component_product)
            production.write({'state': state})
            with self.assertRaises(UserError):
                move.action_replace_with_analog_product(analog_product.id)

    def test_late_analog_invoice_updates_existing_main_product_analytic(self):
        product_a = self._create_product('Auto analytic main A')
        product_b = self._create_product('Auto analytic analog B')
        contract = self._create_seller_contract('Auto Analytic Rollup Contract')
        self._create_analog_line(product_a, product_b)
        sale_order = self.env['sale.order'].create(
            {
                'partner_id': self.partner.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product_a.id,
                            'analytic_distribution': {str(contract.id): 100},
                            'product_uom_qty': 10,
                        }
                    ),
                ],
            }
        )

        sale_order.action_confirm()
        main_analytic = self.ProductAnalytic.search(
            [
                ('product_id', '=', product_a.id),
                ('sale_contract_id', '=', contract.id),
            ],
            limit=1,
        )
        self.assertTrue(main_analytic)
        analog_analytic = self._create_product_analytic(product_b, contract)

        self.assertEqual(main_analytic.in_invoice, 0)
        analog_bill = self._create_vendor_bill(product_b, contract, 1)
        main_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertIn(
            analog_bill.invoice_line_ids,
            main_analytic._get_related_invoice_move_lines(),
        )
        self.assertEqual(
            main_analytic._sum_invoice_quantity_for_products(
                main_analytic._get_direct_rollup_products(),
                product_a.uom_id,
            ),
            1,
        )
        self.assertEqual(main_analytic.demand, 10)
        self.assertEqual(main_analytic.in_invoice, 1)
        self.assertAlmostEqual(main_analytic.closed, 0.1)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

        self._create_vendor_bill(product_a, contract, 2)
        main_analytic.invalidate_recordset(['in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertEqual(main_analytic.in_invoice, 3)
        self.assertAlmostEqual(main_analytic.closed, 0.3)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

        self._create_vendor_bill(product_b, contract, 1, move_type='in_refund')
        main_analytic.invalidate_recordset(['in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertEqual(main_analytic.in_invoice, 2)
        self.assertAlmostEqual(main_analytic.closed, 0.2)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

    def test_product_analytic_rolls_invoice_and_received_to_main_product(self):
        product_a = self._create_product('Rollup main A')
        product_b = self._create_product('Rollup analog B')
        contract = self._create_seller_contract('Rollup Contract')
        self._create_analog_line(product_a, product_b)
        main_analytic = self._create_sale_demand(product_a, contract, 10)
        analog_analytic = self._create_product_analytic(product_b, contract)

        self._create_vendor_bill(product_b, contract, 4)
        self._create_vendor_bill(product_a, contract, 2)
        self._create_vendor_bill(product_b, contract, 1, move_type='in_refund')
        self._create_received_purchase(product_b, contract, 3)
        self._create_received_purchase(product_a, contract, 2)
        self._recompute_analytic_rollups(main_analytic, analog_analytic)

        self.assertEqual(main_analytic.demand, 10)
        self.assertEqual(main_analytic.in_invoice, 5)
        self.assertEqual(main_analytic.qty_received, 5)
        self.assertEqual(main_analytic.closed, 0.5)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.qty_received, 0)
        self.assertEqual(analog_analytic.closed, 0)

        pivot_total = self.ProductAnalytic.read_group(
            [('sale_contract_id', '=', contract.id)],
            ['demand:sum', 'in_invoice:sum', 'qty_received:sum'],
            ['sale_contract_id'],
        )[0]
        self.assertEqual(pivot_total['demand'], 10)
        self.assertEqual(pivot_total['in_invoice'], 5)
        self.assertEqual(pivot_total['qty_received'], 5)

    def test_product_analytic_rollup_does_not_mix_contracts(self):
        product_a = self._create_product('Contract main A')
        product_b = self._create_product('Contract analog B')
        contract_1 = self._create_seller_contract('Rollup Contract 1')
        contract_2 = self._create_seller_contract('Rollup Contract 2')
        self._create_analog_line(product_a, product_b)
        analytic_1 = self._create_sale_demand(product_a, contract_1, 8)
        analytic_2 = self._create_sale_demand(product_a, contract_2, 6)

        self._create_vendor_bill(product_b, contract_1, 3)
        self._create_vendor_bill(product_b, contract_2, 5)
        self._create_received_purchase(product_b, contract_1, 2)
        self._create_received_purchase(product_b, contract_2, 4)
        self._recompute_analytic_rollups(analytic_1, analytic_2)

        self.assertEqual(analytic_1.in_invoice, 3)
        self.assertEqual(analytic_1.qty_received, 2)
        self.assertEqual(analytic_2.in_invoice, 5)
        self.assertEqual(analytic_2.qty_received, 4)

    def test_changing_analog_link_recomputes_old_product_analytics(self):
        product_a = self._create_product('Changed main A')
        product_b = self._create_product('Changed old analog B')
        product_c = self._create_product('Changed new analog C')
        contract = self._create_seller_contract('Changed Rollup Contract')
        analog_line = self._create_analog_line(product_a, product_b)
        main_analytic = self._create_sale_demand(product_a, contract, 10)
        old_analog_analytic = self._create_product_analytic(product_b, contract)

        self._create_vendor_bill(product_b, contract, 4)
        self._recompute_analytic_rollups(main_analytic, old_analog_analytic)
        self.assertEqual(main_analytic.in_invoice, 4)
        self.assertEqual(old_analog_analytic.in_invoice, 0)

        analog_line.write({'product_id': product_c.id})

        self.assertEqual(main_analytic.in_invoice, 0)
        self.assertEqual(old_analog_analytic.in_invoice, 4)
