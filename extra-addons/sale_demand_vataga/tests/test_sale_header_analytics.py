from odoo import Command
from odoo.tests.common import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSaleHeaderAnalytics(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Header analytics customer'})
        cls.product = cls.env['product.product'].create({
            'name': 'Header analytics product', 'type': 'consu',
        })
        cls.headers = {}
        for field_name, plan_name in (
            ('project_account_id', 'project'),
            ('budget_account_id', 'budget'),
            ('cash_flow_item_account_id', 'cash_flow_item'),
            ('seller_contract_id', 'seller_contract'),
        ):
            plan = cls.env.ref(f'analytic_vataga.account_analytic_plan_{plan_name}')
            cls.headers[field_name] = cls.env['account.analytic.account'].create({
                'name': f'Header {plan_name}', 'plan_id': plan.id,
            }).id
        cls.other_contract = cls.env['account.analytic.account'].create({
            'name': 'Replacement contract',
            'plan_id': cls.env.ref(
                'analytic_vataga.account_analytic_plan_seller_contract'
            ).id,
        })

    def _distribution(self, headers):
        ids = sorted({str(value) for value in headers.values() if value})
        return {','.join(ids): 100} if ids else False

    def _line_vals(self, **kwargs):
        return {'product_id': self.product.id, 'product_uom_qty': 2, **kwargs}

    def _create_order(self, **kwargs):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            **self.headers,
            'order_line': [Command.create(self._line_vals())],
            **kwargs,
        })

    def _assert_distribution(self, lines, headers):
        self.env.flush_all()
        lines.invalidate_recordset(['analytic_distribution'])
        for line in lines:
            self.assertEqual(
                line.analytic_distribution or False, self._distribution(headers)
            )

    def test_create_quotation_and_multiple_lines(self):
        order = self._create_order(order_line=[
            Command.create(self._line_vals()),
            Command.create(self._line_vals(analytic_distribution={
                str(self.other_contract.id): 100,
            })),
        ])
        self.assertEqual(order.state, 'draft')
        self.assertEqual(len(order.order_line), 2)
        self._assert_distribution(order.order_line, self.headers)
        for purchase in order.order_line.need_to_purchase_ids:
            self.assertEqual(purchase.sale_contract_id.id, self.headers['seller_contract_id'])
            self.assertFalse(purchase.product_analytic_id)

    def test_change_and_clear_headers(self):
        order = self._create_order(order_line=[
            Command.create(self._line_vals()), Command.create(self._line_vals()),
        ])
        headers = dict(self.headers, seller_contract_id=self.other_contract.id)
        order.write({'seller_contract_id': self.other_contract.id})
        self._assert_distribution(order.order_line, headers)
        order.write({'budget_account_id': False})
        headers['budget_account_id'] = False
        self._assert_distribution(order.order_line, headers)
        order.write(dict.fromkeys(self.headers, False))
        self._assert_distribution(order.order_line, {})
        self.assertFalse(order.order_line.need_to_purchase_ids.sale_contract_id)

    def test_sections_and_notes_unchanged(self):
        marker = {str(self.other_contract.id): 100}
        order = self._create_order(order_line=[
            Command.create(self._line_vals()),
            *[Command.create({
                'name': display_type, 'display_type': display_type,
                'analytic_distribution': marker,
            }) for display_type in ('line_section', 'line_note')],
        ])
        display_lines = order.order_line.filtered('display_type')
        self.assertEqual(len(display_lines), 2)
        order.write({'seller_contract_id': self.other_contract.id})
        order.write(dict.fromkeys(self.headers, False))
        for line in display_lines:
            self.assertEqual(line.analytic_distribution, marker)
            self.assertFalse(line.need_to_purchase_ids)

    def test_add_lines_via_rpc_and_order_commands(self):
        order = self._create_order(order_line=[])
        self.env['sale.order.line'].create([
            self._line_vals(order_id=order.id),
            self._line_vals(order_id=order.id, analytic_distribution={}),
        ])
        order.write({'order_line': [Command.create(self._line_vals())]})
        self.assertEqual(len(order.order_line), 3)
        self._assert_distribution(order.order_line, self.headers)

    def test_manual_analytics_without_headers(self):
        order = self._create_order(**dict.fromkeys(self.headers, False), order_line=[
            Command.create(self._line_vals(analytic_distribution={
                str(self.other_contract.id): 100,
            })),
        ])
        order.write({'note': 'Unrelated update'})
        self._assert_distribution(order.order_line, {'contract': self.other_contract.id})

    def test_partial_headers_and_batch_orders(self):
        orders = self.env['sale.order'].create([
            {'partner_id': self.partner.id, 'project_account_id': self.headers['project_account_id'],
             'order_line': [Command.create(self._line_vals())]},
            {'partner_id': self.partner.id, 'seller_contract_id': self.other_contract.id,
             'order_line': [Command.create(self._line_vals())]},
        ])
        orders.write({'budget_account_id': self.headers['budget_account_id']})
        self._assert_distribution(orders[0].order_line, {
            'project': self.headers['project_account_id'], 'budget': self.headers['budget_account_id'],
        })
        self._assert_distribution(orders[1].order_line, {
            'contract': self.other_contract.id, 'budget': self.headers['budget_account_id'],
        })

    def test_form_onchange_and_new_line(self):
        self.env.user.groups_id |= self.env.ref('analytic.group_analytic_accounting')
        with Form(self.env['sale.order']) as form:
            form.partner_id = self.partner
            with form.order_line.new() as line:
                line.product_id = self.product
            for field_name, account_id in self.headers.items():
                setattr(form, field_name, self.env['account.analytic.account'].browse(account_id))
            with form.order_line.edit(0) as line:
                self.assertEqual(line.analytic_distribution, self._distribution(self.headers))
            with form.order_line.new() as line:
                line.product_id = self.product
                self.assertEqual(line.analytic_distribution, self._distribution(self.headers))
            for field_name in self.headers:
                setattr(form, field_name, self.env['account.analytic.account'])
            with form.order_line.edit(0) as line:
                self.assertFalse(line.analytic_distribution)
        self._assert_distribution(form.record.order_line, {})

    def test_confirmed_order_contract_change_updates_demand(self):
        order = self._create_order()
        order.action_confirm()
        purchase = order.order_line.need_to_purchase_ids
        self.assertEqual(purchase.sale_contract_id.id, self.headers['seller_contract_id'])
        old_analytic = purchase.product_analytic_id
        self.assertTrue(old_analytic)
        self.assertEqual(old_analytic.demand, 2)
        order.write({'seller_contract_id': self.other_contract.id})
        self.env.flush_all()
        self.assertEqual(purchase.sale_contract_id, self.other_contract)
        self.assertEqual(purchase.product_analytic_id.sale_contract_id, self.other_contract)
        self.assertEqual(purchase.product_analytic_id.demand, 2)
        self.assertFalse(old_analytic.exists())
        order.order_line.write({'product_uom_qty': 3})
        self.assertEqual(purchase.product_analytic_id.demand, 3)
        new_analytic = purchase.product_analytic_id
        order.write(dict.fromkeys(self.headers, False))
        self.env.flush_all()
        self.assertFalse(purchase.sale_contract_id)
        self.assertFalse(purchase.product_analytic_id)
        self.assertFalse(new_analytic.exists())
