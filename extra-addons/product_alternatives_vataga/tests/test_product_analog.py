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
        return self._create_vendor_bill_from_distribution(
            product,
            {str(contract.id): 100},
            quantity,
            move_type=move_type,
            seller_contract=contract,
        )

    def _create_vendor_bill_from_distribution(
        self,
        product,
        analytic_distribution,
        quantity,
        move_type='in_invoice',
        seller_contract=False,
        analog_original_product=False,
        purchase_line=False,
        post=True,
    ):
        line_vals = {
            'product_id': product.id,
            'quantity': quantity,
            'name': product.display_name,
            'price_unit': 1,
            'account_id': self.expense_account.id,
            'analytic_distribution': analytic_distribution,
            'product_uom_id': product.uom_id.id,
        }
        if analog_original_product:
            line_vals['analog_original_product_id'] = analog_original_product.id
        if purchase_line and 'purchase_line_id' in self.env['account.move.line']._fields:
            line_vals['purchase_line_id'] = purchase_line.id
        bill = self.env['account.move'].create(
            {
                'move_type': move_type,
                'partner_id': self.partner.id,
                'journal_id': self.purchase_journal.id,
                'invoice_date': fields.Date.today(),
                'date': fields.Date.today(),
                'seller_contract_id': seller_contract.id if seller_contract else False,
                'invoice_line_ids': [
                    Command.create(line_vals)
                ],
            }
        )
        if post:
            bill.action_post()
        return bill

    def _create_received_purchase(
        self,
        product,
        contract,
        quantity,
        analog_original_product=False,
    ):
        line_vals = {
            'product_id': product.id,
            'product_qty': quantity,
            'price_unit': 1,
            'analytic_distribution': {str(contract.id): 100},
        }
        if analog_original_product:
            line_vals['analog_original_product_id'] = analog_original_product.id
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(line_vals),
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
        product_analytics._compute_demand_comment()

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

    def test_product_lists_mark_products_with_any_analog_link(self):
        product_a = self._create_product('List marker main A')
        product_b = self._create_product('List marker analog B')
        product_c = self._create_product('List marker legacy analog C')

        self.assertFalse(product_a.analog_list_marker)
        self.assertFalse(product_b.analog_list_marker)
        self.assertFalse(product_a.product_tmpl_id.analog_list_marker)
        self.assertFalse(product_b.product_tmpl_id.analog_list_marker)

        analog_line = self._create_analog_line(product_a, product_b)
        (product_a | product_b).invalidate_recordset(['analog_list_marker'])
        (
            product_a.product_tmpl_id | product_b.product_tmpl_id
        ).invalidate_recordset(['analog_list_marker'])

        self.assertEqual(product_a.analog_list_marker, '(A)')
        self.assertEqual(product_b.analog_list_marker, '(A)')
        self.assertEqual(product_a.product_tmpl_id.analog_list_marker, '(A)')
        self.assertEqual(product_b.product_tmpl_id.analog_list_marker, '(A)')

        analog_line.unlink()
        (product_a | product_b).invalidate_recordset(['analog_list_marker'])
        (
            product_a.product_tmpl_id | product_b.product_tmpl_id
        ).invalidate_recordset(['analog_list_marker'])

        self.assertFalse(product_a.analog_list_marker)
        self.assertFalse(product_b.analog_list_marker)
        self.assertFalse(product_a.product_tmpl_id.analog_list_marker)
        self.assertFalse(product_b.product_tmpl_id.analog_list_marker)

        legacy_line = self.ProductAnalog.with_context(
            product_alternatives_skip_reciprocal_sync=True
        ).create(
            {
                'product_tmpl_id': product_a.product_tmpl_id.id,
                'product_id': product_c.id,
                'is_primary_link': True,
            }
        )
        (product_a | product_c).invalidate_recordset(['analog_list_marker'])
        (
            product_a.product_tmpl_id | product_c.product_tmpl_id
        ).invalidate_recordset(['analog_list_marker'])

        self.assertEqual(product_a.analog_list_marker, '(A)')
        self.assertEqual(product_c.analog_list_marker, '(A)')
        self.assertEqual(product_a.product_tmpl_id.analog_list_marker, '(A)')
        self.assertEqual(product_c.product_tmpl_id.analog_list_marker, '(A)')

        legacy_line.unlink()

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

    def test_demand_comment_marks_main_when_contract_uses_analog(self):
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

        self._create_vendor_bill(product_a, self.sale_contract, 1)
        main_product_analytic.invalidate_recordset(['demand_comment'])

        self.assertEqual(main_product_analytic.demand_comment, 'Need substitute')

        self._create_vendor_bill(product_b, self.sale_contract, 1)
        main_product_analytic.invalidate_recordset(['demand_comment'])
        analog_product_analytic.invalidate_recordset(['demand_comment'])

        self.assertEqual(main_product_analytic.demand_comment, 'Need substitute (A)')
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
        (component_product | analog_product).write({'type': 'product'})
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
        unrelated_analytic = self._create_product_analytic(
            self._create_product('Auto analytic unrelated product'),
            contract,
        )
        secondary_contract = self._create_seller_contract('Resolver Secondary Contract')
        resolver_bill = self._create_vendor_bill_from_distribution(
            product_b,
            {f'{contract.id},{secondary_contract.id}': 100},
            1,
            post=False,
        )
        found_contract_ids = set(
            resolver_bill.invoice_line_ids
            ._get_seller_contracts_from_analytic_distribution()
            .ids
        )

        self.assertEqual(found_contract_ids, {contract.id, secondary_contract.id})
        self.assertFalse(
            resolver_bill.invoice_line_ids
            ._get_analog_product_analytic_recompute_targets()
        )

        self.assertEqual(main_analytic.in_invoice, 0)
        analog_bill = self._create_vendor_bill_from_distribution(
            product_b,
            {str(contract.id): 100},
            1,
        )
        self.assertFalse(analog_bill.seller_contract_id)
        self.assertIn(
            contract,
            analog_bill.invoice_line_ids
            ._get_seller_contracts_from_analytic_distribution(),
        )
        analog_bill_targets = (
            analog_bill.invoice_line_ids
            ._get_analog_product_analytic_recompute_targets()
        )
        self.assertIn(main_analytic, analog_bill_targets)
        self.assertEqual(
            set(analog_bill_targets.ids),
            {main_analytic.id},
        )
        self.assertNotIn(analog_analytic, analog_bill_targets)
        self.assertNotIn(unrelated_analytic, analog_bill_targets)
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

        self._create_vendor_bill_from_distribution(
            product_b,
            {str(contract.id): 100},
            1,
        )
        main_analytic.invalidate_recordset(['in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertEqual(main_analytic.in_invoice, 2)
        self.assertAlmostEqual(main_analytic.closed, 0.2)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

        self._create_vendor_bill_from_distribution(
            product_a,
            {str(contract.id): 100},
            2,
        )
        main_analytic.invalidate_recordset(['in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertEqual(main_analytic.in_invoice, 4)
        self.assertAlmostEqual(main_analytic.closed, 0.4)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

        self._create_vendor_bill_from_distribution(
            product_b,
            {str(contract.id): 100},
            1,
            move_type='in_refund',
        )
        main_analytic.invalidate_recordset(['in_invoice', 'closed'])
        analog_analytic.invalidate_recordset(['demand', 'in_invoice', 'closed'])

        self.assertEqual(main_analytic.in_invoice, 3)
        self.assertAlmostEqual(main_analytic.closed, 0.3)
        self.assertEqual(analog_analytic.demand, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.closed, 0)

    def test_invoice_recompute_targets_are_scoped_to_related_products(self):
        product_a = self._create_product('Scoped main A')
        product_b = self._create_product('Scoped shared analog B')
        product_c = self._create_product('Scoped main C')
        contract_1 = self._create_seller_contract('Scoped Contract 1')
        contract_2 = self._create_seller_contract('Scoped Contract 2')
        self._create_analog_line(product_a, product_b)
        self._create_analog_line(product_c, product_b)
        analytic_a_1 = self._create_product_analytic(product_a, contract_1)
        analytic_c_1 = self._create_product_analytic(product_c, contract_1)
        analytic_a_2 = self._create_product_analytic(product_a, contract_2)
        analog_analytic = self._create_product_analytic(product_b, contract_1)
        unrelated_analytic = self._create_product_analytic(
            self._create_product('Scoped unrelated product'),
            contract_1,
        )

        bill = self._create_vendor_bill_from_distribution(
            product_b,
            {f'{contract_1.id},{contract_2.id}': 100},
            1,
            post=False,
        )
        self.assertFalse(bill.invoice_line_ids.analog_original_product_id)

        with self.assertRaises(ValidationError):
            bill.action_post()

        bill.invoice_line_ids.analog_original_product_id = product_a
        bill.action_post()
        targets = bill.invoice_line_ids._get_analog_product_analytic_recompute_targets()

        self.assertEqual(
            set(targets.ids),
            {analytic_a_1.id, analytic_a_2.id},
        )
        self.assertNotIn(analytic_c_1, targets)
        self.assertNotIn(analog_analytic, targets)
        self.assertNotIn(unrelated_analytic, targets)

        main_bill = self._create_vendor_bill_from_distribution(
            product_a,
            {str(contract_1.id): 100},
            1,
        )
        main_targets = (
            main_bill.invoice_line_ids
            ._get_analog_product_analytic_recompute_targets()
        )
        self.assertEqual(set(main_targets.ids), {analytic_a_1.id})

    def test_shared_purchase_analog_counts_only_selected_original(self):
        product_a = self._create_product('Purchase scope main A')
        product_b = self._create_product('Purchase scope shared analog B')
        product_c = self._create_product('Purchase scope main C')
        contract = self._create_seller_contract('Purchase Scope Contract')
        self._create_analog_line(product_a, product_b)
        self._create_analog_line(product_c, product_b)
        analytic_a = self._create_sale_demand(product_a, contract, 100)
        analytic_c = self._create_sale_demand(product_c, contract, 100)
        analog_analytic = self._create_product_analytic(product_b, contract)

        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product_b.id,
                            'product_qty': 100,
                            'price_unit': 1,
                            'analytic_distribution': {str(contract.id): 100},
                        }
                    ),
                ],
            }
        )

        if 'ua_contract_id' in purchase._fields:
            self.assertFalse(purchase.ua_contract_id)
        self.assertEqual(purchase.order_line.seller_contract_id, contract)
        self.assertFalse(purchase.order_line.analog_original_product_id)
        self.assertEqual(
            set(purchase.order_line.analog_original_product_ids.ids),
            {product_a.id, product_b.id, product_c.id},
        )
        with self.assertRaisesRegex(
            ValidationError,
            'Для товару-аналога необхідно вибрати оригінал аналога.',
        ):
            purchase.button_confirm()

        purchase.order_line.write({'analog_original_product_id': product_b.id})
        self.assertEqual(purchase.order_line.analog_original_product_id, product_b)
        purchase.order_line.analog_original_product_id = product_a
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        self._recompute_analytic_rollups(analytic_a, analytic_c, analog_analytic)

        self.assertEqual(analytic_a.qty_received, 100)
        self.assertEqual(analytic_c.qty_received, 0)
        self.assertEqual(analog_analytic.qty_received, 0)
        self.assertEqual(analytic_a.demand_comment, '(A)')
        self.assertFalse(analytic_c.demand_comment)
        self.assertEqual(analog_analytic.demand_comment, '(A)')

        bill = self._create_vendor_bill_from_distribution(
            product_b,
            {str(contract.id): 100},
            100,
            purchase_line=purchase.order_line,
        )
        self.assertEqual(bill.invoice_line_ids.analog_original_product_id, product_a)
        self._recompute_analytic_rollups(analytic_a, analytic_c, analog_analytic)

        self.assertEqual(analytic_a.in_invoice, 100)
        self.assertEqual(analytic_c.in_invoice, 0)
        self.assertEqual(analog_analytic.in_invoice, 0)

        pivot_total = self.ProductAnalytic.read_group(
            [('sale_contract_id', '=', contract.id)],
            ['in_invoice:sum', 'qty_received:sum'],
            ['sale_contract_id'],
        )[0]
        self.assertEqual(pivot_total['in_invoice'], 100)
        self.assertEqual(pivot_total['qty_received'], 100)

    def test_purchase_main_product_shows_only_direct_analog_counterpart(self):
        product_a = self._create_product('Bidirectional purchase main A')
        product_b = self._create_product('Bidirectional purchase shared analog B')
        product_c = self._create_product('Bidirectional purchase main C')
        contract = self._create_seller_contract('Bidirectional Purchase Contract')
        self._create_analog_line(product_a, product_b)
        self._create_analog_line(product_c, product_b)
        analytic_a = self._create_sale_demand(product_a, contract, 20)
        analytic_c = self._create_sale_demand(product_c, contract, 20)

        purchase_a = self._create_received_purchase(product_a, contract, 5)
        self.assertFalse(purchase_a.order_line.analog_original_product_id)
        self.assertEqual(
            set(purchase_a.order_line.analog_original_product_ids.ids),
            {product_a.id, product_b.id},
        )

        purchase_c = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product_c.id,
                            'product_qty': 1,
                            'price_unit': 1,
                        }
                    ),
                ],
            }
        )
        self.assertFalse(purchase_c.order_line.analog_original_product_id)
        self.assertEqual(
            set(purchase_c.order_line.analog_original_product_ids.ids),
            {product_b.id, product_c.id},
        )

        self._recompute_analytic_rollups(analytic_a, analytic_c)
        self.assertEqual(analytic_a.qty_received, 5)
        self.assertEqual(analytic_c.qty_received, 0)
        self.assertFalse(
            self.ProductAnalytic.search(
                [
                    ('product_id', '=', product_b.id),
                    ('sale_contract_id', '=', contract.id),
                ],
                limit=1,
            )
        )

        bill = self._create_vendor_bill_from_distribution(
            product_a,
            {str(contract.id): 100},
            4,
            analog_original_product=product_b,
        )
        self.assertEqual(bill.invoice_line_ids.analog_original_product_id, product_b)
        analog_analytic = self.ProductAnalytic.search(
            [
                ('product_id', '=', product_b.id),
                ('sale_contract_id', '=', contract.id),
            ],
            limit=1,
        )
        self.assertTrue(analog_analytic)
        self._recompute_analytic_rollups(analytic_a, analytic_c, analog_analytic)

        self.assertEqual(analytic_a.in_invoice, 0)
        self.assertEqual(analytic_c.in_invoice, 0)
        self.assertEqual(analog_analytic.in_invoice, 4)

        purchase_b = self._create_received_purchase(
            product_a,
            contract,
            3,
            analog_original_product=product_b,
        )
        self.assertEqual(purchase_b.order_line.analog_original_product_id, product_b)
        self._recompute_analytic_rollups(analytic_a, analytic_c, analog_analytic)

        self.assertEqual(analytic_a.qty_received, 5)
        self.assertEqual(analytic_c.qty_received, 0)
        self.assertEqual(analog_analytic.qty_received, 3)

        bill_from_purchase = self._create_vendor_bill_from_distribution(
            product_a,
            {str(contract.id): 100},
            2,
            purchase_line=purchase_b.order_line,
        )
        self.assertEqual(
            bill_from_purchase.invoice_line_ids.analog_original_product_id,
            product_b,
        )
        self._recompute_analytic_rollups(analytic_a, analytic_c, analog_analytic)

        self.assertEqual(analytic_a.in_invoice, 0)
        self.assertEqual(analytic_c.in_invoice, 0)
        self.assertEqual(analog_analytic.in_invoice, 6)

    def test_analog_rollup_targets_include_self_and_only_direct_counterparts(self):
        product_a = self._create_product('Allowed target main A')
        product_b = self._create_product('Allowed target direct B')
        product_c = self._create_product('Allowed target transitive C')
        unrelated_product = self._create_product('Allowed target unrelated')
        contract = self._create_seller_contract('Allowed Target Contract')
        self._create_analog_line(product_a, product_b)
        self._create_analog_line(product_b, product_c)

        self.assertEqual(
            set(product_a._get_direct_analog_counterpart_products().ids),
            {product_b.id},
        )
        self.assertEqual(
            set(product_a._get_allowed_analog_rollup_target_products().ids),
            {product_a.id, product_b.id},
        )
        self.assertFalse(
            unrelated_product._get_allowed_analog_rollup_target_products()
        )

        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product_a.id,
                            'product_qty': 1,
                            'price_unit': 1,
                            'analog_original_product_id': product_a.id,
                        }
                    ),
                ],
            }
        )
        purchase_line = purchase.order_line
        self.assertEqual(purchase_line.analog_original_product_id, product_a)
        self.assertEqual(
            set(purchase_line.analog_original_product_ids.ids),
            {product_a.id, product_b.id},
        )
        purchase_line.write({'product_id': product_a.id})
        purchase_line.invalidate_recordset(['analog_original_product_id'])
        self.assertEqual(purchase_line.analog_original_product_id, product_a)
        with self.assertRaises(ValidationError):
            purchase_line.analog_original_product_id = product_c

        bill = self._create_vendor_bill_from_distribution(
            product_a,
            {str(contract.id): 100},
            1,
            analog_original_product=product_a,
            post=False,
        )
        invoice_line = bill.invoice_line_ids
        self.assertEqual(invoice_line.analog_original_product_id, product_a)
        self.assertEqual(
            set(invoice_line.analog_original_product_ids.ids),
            {product_a.id, product_b.id},
        )
        invoice_line.write({'product_id': product_a.id})
        invoice_line.invalidate_recordset(['analog_original_product_id'])
        self.assertEqual(invoice_line.analog_original_product_id, product_a)
        with self.assertRaises(ValidationError):
            invoice_line.analog_original_product_id = unrelated_product

    def test_self_rollup_target_counts_purchase_and_invoice_once(self):
        main_product = self._create_product('Self target main')
        analog_product = self._create_product('Self target analog')
        contract = self._create_seller_contract('Self Target Contract')
        self._create_analog_line(main_product, analog_product)
        main_analytic = self._create_product_analytic(main_product, contract)
        analog_analytic = self._create_product_analytic(analog_product, contract)

        purchase = self._create_received_purchase(
            analog_product,
            contract,
            7,
            analog_original_product=analog_product,
        )
        purchase_line = purchase.order_line
        self.assertEqual(
            purchase_line.analog_original_product_id,
            analog_product,
        )

        bill = self._create_vendor_bill_from_distribution(
            analog_product,
            {str(contract.id): 100},
            7,
            purchase_line=purchase_line,
        )
        self.assertEqual(
            bill.invoice_line_ids.analog_original_product_id,
            analog_product,
        )
        self._recompute_analytic_rollups(main_analytic, analog_analytic)

        self.assertEqual(main_analytic.qty_received, 0)
        self.assertEqual(main_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.qty_received, 7)
        self.assertEqual(analog_analytic.in_invoice, 7)

        pivot_total = self.ProductAnalytic.read_group(
            [('sale_contract_id', '=', contract.id)],
            ['in_invoice:sum', 'qty_received:sum'],
            ['sale_contract_id'],
        )[0]
        self.assertEqual(pivot_total['in_invoice'], 7)
        self.assertEqual(pivot_total['qty_received'], 7)

    def test_analog_origin_field_is_editable_with_single_direct_option(self):
        label = '\u041e\u0440\u0438\u0433\u0456\u043d\u0430\u043b \u0430\u043d\u0430\u043b\u043e\u0433\u0430'
        readonly_rule = 'readonly="not has_multiple_analog_original_options"'
        view_xmlids = [
            'product_alternatives_vataga.purchase_order_form_view_analog_original_product',
            'product_alternatives_vataga.account_move_form_view_analog_original_product',
        ]

        for xmlid in view_xmlids:
            arch = self.env.ref(xmlid).arch_db
            self.assertIn(label, arch)
            self.assertNotIn(readonly_rule, arch)

    def test_single_analog_original_is_autofilled(self):
        product_a = self._create_product('Single origin main A')
        product_b = self._create_product('Single origin analog B')
        contract = self._create_seller_contract('Single Origin Contract')
        self._create_analog_line(product_a, product_b)

        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': product_b.id,
                            'product_qty': 1,
                            'price_unit': 1,
                        }
                    ),
                ],
            }
        )

        self.assertEqual(purchase.order_line.analog_original_product_id, product_a)

        unrelated_product = self._create_product('Single origin unrelated')
        purchase.order_line.product_id = unrelated_product
        self.assertFalse(purchase.order_line.analog_original_product_id)

        purchase.order_line.product_id = product_b
        self.assertEqual(purchase.order_line.analog_original_product_id, product_a)

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

    def test_backfill_historical_invoice_creates_target_once(self):
        main_product = self._create_product('Historical invoice main')
        analog_product = self._create_product('Historical invoice analog')
        contract = self._create_seller_contract('Historical invoice contract')
        self._create_analog_line(main_product, analog_product)
        analog_analytic = self._create_product_analytic(
            analog_product,
            contract,
        )
        bill = self._create_vendor_bill(
            analog_product,
            contract,
            7,
        )
        main_domain = [
            ('product_id', '=', main_product.id),
            ('sale_contract_id', '=', contract.id),
        ]
        self.ProductAnalytic.search(main_domain).unlink()
        self.assertFalse(self.ProductAnalytic.search(main_domain))

        dry_run = (
            self.ProductAnalytic._backfill_analog_rollup_product_analytics(
                batch_size=1,
                dry_run=True,
            )
        )
        self.assertGreaterEqual(dry_run['missing_target_count'], 1)
        self.assertEqual(
            dry_run['would_create_count'],
            dry_run['missing_target_count'],
        )
        self.assertGreaterEqual(dry_run['would_recompute_count'], 2)
        self.assertEqual(dry_run['created_count'], 0)
        self.assertEqual(dry_run['recomputed_count'], 0)
        self.assertFalse(self.ProductAnalytic.search(main_domain))

        first_result = (
            self.ProductAnalytic._backfill_analog_rollup_product_analytics(
                batch_size=1,
            )
        )
        main_analytic = self.ProductAnalytic.search(main_domain)
        self.assertEqual(len(main_analytic), 1)
        self.assertGreaterEqual(first_result['created_count'], 1)
        self.assertEqual(main_analytic.in_invoice, 7)
        self.assertEqual(main_analytic.qty_received, 0)
        self.assertIn(bill, main_analytic.account_move_ids)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.qty_received, 0)

        pivot_total = self.ProductAnalytic.read_group(
            [
                ('sale_contract_id', '=', contract.id),
                ('product_id', 'in', (main_product | analog_product).ids),
            ],
            ['in_invoice:sum', 'qty_received:sum'],
            [],
        )[0]
        self.assertEqual(pivot_total['in_invoice'], 7)
        self.assertEqual(pivot_total['qty_received'], 0)

        second_result = (
            self.ProductAnalytic._backfill_analog_rollup_product_analytics(
                batch_size=1,
            )
        )
        self.assertEqual(second_result['created_count'], 0)
        self.assertEqual(self.ProductAnalytic.search_count(main_domain), 1)

    def test_backfill_received_purchase_without_invoice(self):
        main_product = self._create_product('Historical receipt main')
        analog_product = self._create_product('Historical receipt analog')
        contract = self._create_seller_contract('Historical receipt contract')
        self._create_analog_line(main_product, analog_product)
        analog_analytic = self._create_product_analytic(
            analog_product,
            contract,
        )
        purchase = self._create_received_purchase(
            analog_product,
            contract,
            9,
            analog_original_product=main_product,
        )
        self.assertFalse(purchase.invoice_ids)
        main_domain = [
            ('product_id', '=', main_product.id),
            ('sale_contract_id', '=', contract.id),
        ]
        self.ProductAnalytic.search(main_domain).unlink()

        result = self.ProductAnalytic._backfill_analog_rollup_product_analytics(
            batch_size=1,
        )
        main_analytic = self.ProductAnalytic.search(main_domain)
        self.assertGreaterEqual(result['created_count'], 1)
        self.assertEqual(len(main_analytic), 1)
        self.assertEqual(main_analytic.in_invoice, 0)
        self.assertEqual(main_analytic.qty_received, 9)
        self.assertEqual(analog_analytic.in_invoice, 0)
        self.assertEqual(analog_analytic.qty_received, 0)

        pivot_total = self.ProductAnalytic.read_group(
            [
                ('sale_contract_id', '=', contract.id),
                ('product_id', 'in', (main_product | analog_product).ids),
            ],
            ['in_invoice:sum', 'qty_received:sum'],
            [],
        )[0]
        self.assertEqual(pivot_total['in_invoice'], 0)
        self.assertEqual(pivot_total['qty_received'], 9)

    def test_backfill_recomputes_stale_demand_without_documents(self):
        product = self._create_product('Demand-only product')
        contract = self._create_seller_contract('Demand-only contract')
        product_analytic = self._create_sale_demand(product, contract, 13)
        self.assertEqual(product_analytic.demand, 13)
        self.assertFalse(
            self.env['account.move.line'].search(
                [
                    ('product_id', '=', product.id),
                    ('move_id.state', '=', 'posted'),
                    ('move_id.move_type', 'in', ('in_invoice', 'in_refund')),
                ]
            )
        )
        self.assertFalse(
            self.env['purchase.order.line'].search(
                [
                    ('product_id', '=', product.id),
                    ('order_id.state', 'in', ('purchase', 'done')),
                ]
            )
        )

        product_analytic.flush_recordset(['demand'])
        self.env.cr.execute(
            'UPDATE product_analytic SET demand = 0 WHERE id = %s',
            [product_analytic.id],
        )
        product_analytic.invalidate_recordset(['demand'])
        self.assertEqual(product_analytic.demand, 0)

        dry_run = (
            self.ProductAnalytic._backfill_analog_rollup_product_analytics(
                dry_run=True,
            )
        )
        self.assertGreaterEqual(dry_run['would_recompute_count'], 1)
        product_analytic.invalidate_recordset(['demand'])
        self.assertEqual(product_analytic.demand, 0)

        self.ProductAnalytic._backfill_analog_rollup_product_analytics()
        product_analytic.invalidate_recordset(
            ['demand', 'in_invoice', 'closed']
        )
        self.assertEqual(product_analytic.demand, 13)
        self.assertEqual(product_analytic.in_invoice, 0)
        self.assertEqual(product_analytic.closed, 0)

    def test_unlink_purchase_line_recomputes_previous_target(self):
        main_product = self._create_product('Deleted purchase main')
        analog_product = self._create_product('Deleted purchase analog')
        contract = self._create_seller_contract('Deleted purchase contract')
        self._create_analog_line(main_product, analog_product)
        analog_analytic = self._create_product_analytic(
            analog_product,
            contract,
        )
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': analog_product.id,
                            'product_qty': 5,
                            'price_unit': 1,
                            'analytic_distribution': {str(contract.id): 100},
                            'analog_original_product_id': main_product.id,
                        }
                    ),
                ],
            }
        )
        purchase.button_confirm()
        main_analytic = self.ProductAnalytic.search(
            [
                ('product_id', '=', main_product.id),
                ('sale_contract_id', '=', contract.id),
            ],
            limit=1,
        )
        purchase.button_cancel()
        main_analytic.flush_recordset(['qty_received'])
        self.env.cr.execute(
            'UPDATE product_analytic SET qty_received = 5 WHERE id = %s',
            [main_analytic.id],
        )
        main_analytic.invalidate_recordset(['qty_received'])
        self.assertEqual(main_analytic.qty_received, 5)

        purchase.order_line.unlink()
        main_analytic.invalidate_recordset(['qty_received'])
        analog_analytic.invalidate_recordset(['qty_received'])
        self.assertEqual(main_analytic.qty_received, 0)
        self.assertEqual(analog_analytic.qty_received, 0)

        pivot_total = self.ProductAnalytic.read_group(
            [
                ('sale_contract_id', '=', contract.id),
                ('product_id', 'in', (main_product | analog_product).ids),
            ],
            ['qty_received:sum'],
            [],
        )[0]
        self.assertEqual(pivot_total['qty_received'], 0)

    def test_backfill_clears_stale_stored_values_without_sources(self):
        contract = self._create_seller_contract('Stale values contract')
        stale_invoice_analytic = self._create_product_analytic(
            self._create_product('Stale invoice product'),
            contract,
        )
        stale_received_analytic = self._create_product_analytic(
            self._create_product('Stale received product'),
            contract,
        )
        stale_closed_analytic = self._create_product_analytic(
            self._create_product('Stale closed product'),
            contract,
        )
        stale_move_analytic = self._create_product_analytic(
            self._create_product('Stale move product'),
            contract,
        )
        draft_bill = self.env['account.move'].create(
            {
                'move_type': 'in_invoice',
                'partner_id': self.partner.id,
                'journal_id': self.purchase_journal.id,
                'invoice_date': fields.Date.today(),
                'date': fields.Date.today(),
            }
        )
        stale_move_analytic.account_move_ids = draft_bill
        analytics = (
            stale_invoice_analytic
            | stale_received_analytic
            | stale_closed_analytic
            | stale_move_analytic
        )
        analytics.flush_recordset(
            ['demand', 'in_invoice', 'qty_received', 'closed', 'account_move_ids']
        )
        self.env.cr.execute(
            '''
                UPDATE product_analytic
                   SET in_invoice = CASE WHEN id = %s THEN 3 ELSE in_invoice END,
                       qty_received = CASE WHEN id = %s THEN 4 ELSE qty_received END,
                       closed = CASE WHEN id = %s THEN 0.5 ELSE closed END
                 WHERE id IN %s
            ''',
            [
                stale_invoice_analytic.id,
                stale_received_analytic.id,
                stale_closed_analytic.id,
                tuple(analytics.ids),
            ],
        )
        analytics.invalidate_recordset(
            ['demand', 'in_invoice', 'qty_received', 'closed', 'account_move_ids']
        )
        self.assertEqual(stale_invoice_analytic.in_invoice, 3)
        self.assertEqual(stale_received_analytic.qty_received, 4)
        self.assertEqual(stale_closed_analytic.closed, 0.5)
        self.assertEqual(stale_move_analytic.account_move_ids, draft_bill)

        dry_run = (
            self.ProductAnalytic._backfill_analog_rollup_product_analytics(
                dry_run=True,
            )
        )
        self.assertGreaterEqual(dry_run['would_recompute_count'], 4)
        analytics.invalidate_recordset(
            ['in_invoice', 'qty_received', 'closed', 'account_move_ids']
        )
        self.assertEqual(stale_invoice_analytic.in_invoice, 3)
        self.assertEqual(stale_received_analytic.qty_received, 4)
        self.assertEqual(stale_closed_analytic.closed, 0.5)
        self.assertEqual(stale_move_analytic.account_move_ids, draft_bill)

        self.ProductAnalytic._backfill_analog_rollup_product_analytics()
        analytics.invalidate_recordset(
            ['demand', 'in_invoice', 'qty_received', 'closed', 'account_move_ids']
        )
        for product_analytic in analytics:
            self.assertEqual(product_analytic.demand, 0)
            self.assertEqual(product_analytic.in_invoice, 0)
            self.assertEqual(product_analytic.qty_received, 0)
            self.assertEqual(product_analytic.closed, 0)
            self.assertFalse(product_analytic.account_move_ids)

    def test_confirmed_purchase_header_contract_moves_received_quantity(self):
        main_product = self._create_product('Header contract main')
        analog_product = self._create_product('Header contract analog')
        old_contract = self._create_seller_contract('Header contract old')
        new_contract = self._create_seller_contract('Header contract new')
        self._create_analog_line(main_product, analog_product)
        old_analog_analytic = self._create_product_analytic(
            analog_product,
            old_contract,
        )
        new_analog_analytic = self._create_product_analytic(
            analog_product,
            new_contract,
        )
        purchase = self.env['purchase.order'].create(
            {
                'partner_id': self.partner.id,
                'seller_contract_id': old_contract.id,
                'order_line': [
                    Command.create(
                        {
                            'product_id': analog_product.id,
                            'product_qty': 6,
                            'price_unit': 1,
                            'analog_original_product_id': main_product.id,
                        }
                    ),
                ],
            }
        )
        purchase.order_line.write({'analytic_distribution': False})
        self.assertFalse(purchase.order_line.analytic_distribution)
        self.assertFalse(purchase.order_line.seller_contract_id)
        self.assertEqual(
            purchase.order_line._get_demand_report_seller_contracts(),
            old_contract,
        )
        purchase.button_confirm()
        purchase.picking_ids.button_validate()
        old_main_analytic = self.ProductAnalytic.search(
            [
                ('product_id', '=', main_product.id),
                ('sale_contract_id', '=', old_contract.id),
            ],
            limit=1,
        )
        old_main_analytic.invalidate_recordset(['qty_received'])
        self.assertEqual(old_main_analytic.qty_received, 6)
        self.assertEqual(old_analog_analytic.qty_received, 0)

        purchase.write({'seller_contract_id': new_contract.id})
        new_main_analytic = self.ProductAnalytic.search(
            [
                ('product_id', '=', main_product.id),
                ('sale_contract_id', '=', new_contract.id),
            ],
            limit=1,
        )
        self.assertEqual(
            purchase.order_line._get_demand_report_seller_contracts(),
            new_contract,
        )
        old_main_analytic.invalidate_recordset(['qty_received'])
        new_main_analytic.invalidate_recordset(['qty_received'])
        old_analog_analytic.invalidate_recordset(['qty_received'])
        new_analog_analytic.invalidate_recordset(['qty_received'])
        self.assertEqual(old_main_analytic.qty_received, 0)
        self.assertEqual(new_main_analytic.qty_received, 6)
        self.assertEqual(old_analog_analytic.qty_received, 0)
        self.assertEqual(new_analog_analytic.qty_received, 0)

        grouped_totals = self.ProductAnalytic.read_group(
            [
                ('product_id', 'in', (main_product | analog_product).ids),
                ('sale_contract_id', 'in', (old_contract | new_contract).ids),
            ],
            ['qty_received:sum'],
            ['sale_contract_id'],
        )
        totals_by_contract = {
            group['sale_contract_id'][0]: group['qty_received']
            for group in grouped_totals
        }
        self.assertEqual(totals_by_contract[old_contract.id], 0)
        self.assertEqual(totals_by_contract[new_contract.id], 6)
