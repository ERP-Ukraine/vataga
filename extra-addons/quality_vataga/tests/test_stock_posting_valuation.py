"""Actual native Standard/perpetual stock, SVLs and GL; disposable DB only."""

from unittest.mock import patch
from datetime import timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.stock_account.tests.test_stockvaluation import _create_accounting_data

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_stock_posting_core as kernel_tests
from . import test_stock_posting_incremental as incremental_tests


@tagged('post_install', '-at_install')
class TestStockPostingValuation(StockReleaseNativeCase):
    _keys = kernel_tests.TestStockPostingKernel._keys
    _scope = kernel_tests.TestStockPostingKernel._scope
    _post = kernel_tests.TestStockPostingKernel._post

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        input_account, output_account, asset, expense, journal = _create_accounting_data(cls.env)
        cls.asset = asset
        cls.wip = cls.env['account.account'].create({'name': 'Native production clearing', 'code': 'QUNITWIP', 'account_type': 'asset_current'})
        category = cls.env['product.category'].create({
            'name': 'Disposable QUNIT Standard perpetual', 'property_cost_method': 'standard', 'property_valuation': 'real_time',
            'property_stock_account_input_categ_id': input_account.id, 'property_stock_account_output_categ_id': output_account.id,
            'property_stock_valuation_account_id': asset.id, 'property_stock_journal': journal.id,
        })
        cls.component = cls.env['product.product'].create({'name': 'Standard component', 'type': 'product', 'categ_id': category.id, 'standard_price': 10})
        cls.product = cls.env['product.product'].create({'name': 'Standard finished', 'type': 'product', 'categ_id': category.id, 'standard_price': 40})
        cls.bom = cls.env['mrp.bom'].create({'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'bom_line_ids': [Command.create({'product_id': cls.component.id, 'product_qty': 2})],
            'operation_ids': [Command.create({'name': 'Standard operation', 'workcenter_id': cls.workcenter.id, 'time_cycle': 12})]})
        cls.product.property_stock_production.write({'valuation_in_account_id': cls.wip.id, 'valuation_out_account_id': cls.wip.id})
        incoming = cls.env['stock.move'].create({
            'name': 'Actual native Standard receipt', 'product_id': cls.component.id,
            'product_uom': cls.component.uom_id.id, 'product_uom_qty': 20, 'price_unit': 10,
            'location_id': cls.env.ref('stock.stock_location_suppliers').id, 'location_dest_id': cls.location.id,
        })
        incoming._action_confirm(merge=False)
        incoming.write({'quantity': 20, 'picked': True})
        incoming._action_done()

    def setUp(self):
        super().setUp()
        if 'quality.production.unit' in self.env:
            self.skipTest('Community kernel probe; the actual Enterprise entry remains blocked.')
        self.env.user.groups_id = [Command.link(self.env.ref(name).id) for name in ('mrp.group_mrp_user', 'stock.group_stock_user')]

    def test_first_unit_has_native_standard_svl_and_actual_posted_journals(self):
        production = self._production(timed=True)
        row = self._post(self._scope(production), 0)
        self.assertEqual(self._stock(), (18, 1))
        self.assertEqual(production.qty_produced, 1)
        self.assertNotEqual(production.state, 'done')
        self.assertEqual(row.component_cost, 20)
        self.assertEqual(row.labour_cost, 12)
        self.assertEqual(row.valuation_cost, 40)
        self.assertEqual(row.cost_variance, -8)
        self.assertEqual(self.product.standard_price, 40)
        self.assertEqual(self.product.valuation, 'real_time')
        self.assertEqual(len(row.line_ids.valuation_layer_id.account_move_id), 2)
        self.assertEqual(set(row.line_ids.valuation_layer_id.account_move_id.mapped('state')), {'posted'})
        # Native production clearing contains components minus standard FG,
        # not a fabricated labour/cost-variance journal.
        entries = row.line_ids.valuation_layer_id.account_move_id
        self.assertEqual(sum(entries.line_ids.filtered(lambda line: line.account_id == self.wip).mapped('balance')), -20)

    def test_five_sequential_units_preserve_first_gl_trace_and_finish_only_at_end(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        rows = self.env['quality.stock.posting']
        first_facts = None
        for index in range(5):
            rows |= self._post(scope, index)
            self.assertEqual(production.qty_produced, index + 1)
            self.assertEqual(self._stock(), (20 - 2 * (index + 1), index + 1))
            if index == 0:
                first_facts = rows.valuation_snapshot
            self.assertEqual(rows[:1].valuation_snapshot, first_facts)
            self.assertEqual(rows[:1].finished_line_id.consume_line_ids, rows[:1].line_ids.filtered(lambda line: line.kind == 'component').move_line_id)
            self.assertEqual(production.state == 'done', index == 4)
        self.assertEqual(sum(rows.mapped('component_cost')), 100)
        self.assertEqual(sum(rows.mapped('labour_cost')), 60)
        self.assertEqual(sum(rows.mapped('valuation_cost')), 200)
        self.assertEqual(sum(rows.mapped('cost_variance')), -40)
        self.assertEqual(len(rows.line_ids.valuation_layer_id.account_move_id), 10)
        self.assertEqual(sum(rows.line_ids.valuation_layer_id.account_move_id.line_ids.filtered(lambda line: line.account_id == self.wip).mapped('balance')), -100)
        self.assertEqual(set(production.workorder_ids.mapped('state')), {'done'})

    def test_retry_and_duplicate_cannot_repeat_gl_or_component_consumption(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        first = self._post(scope, 0)
        before = self._snapshot(production)
        self.assertEqual(self._post(scope, 0), first)
        with self.assertRaises(ValidationError):
            self._post(scope, 0, request='another-request')
        self.assertEqual(self._snapshot(production), before)

    def test_receipt_error_atomically_rolls_back_real_stock_svl_and_gl(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        before = self._snapshot(production)
        journals = self.env['account.move'].search_count([])
        model = self.env['quality.stock.posting']
        # Only the audit constraint fails; actual stock/valuation/GL APIs run.
        with patch.object(type(model), '_check_accounting_snapshot', side_effect=ValidationError('audit rejected')):
            with self.assertRaises(ValidationError):
                self._post(scope, 0)
        self.env.invalidate_all()
        self.assertEqual(self._snapshot(production), before)
        self.assertEqual(self.env['account.move'].search_count([]), journals)

    def test_posted_accounting_cannot_be_reset_modified_or_extended(self):
        production = self._production(timed=True)
        row = self._post(self._scope(production), 0)
        entry = row.finished_move_id.stock_valuation_layer_ids.account_move_id
        for action in (entry.button_draft, entry.unlink, entry._reverse_moves,
                       lambda: entry.line_ids[:1].write({'account_id': self.wip.id}),
                       lambda: self.env['account.move.line'].create({'move_id': entry.id, 'name': 'fake', 'account_id': self.wip.id})):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                action()
        self.env['quality.stock.posting']._reconcile(row.scope_id)

    def test_missing_account_rolls_back_without_inventing_journals(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        self.product.categ_id.property_stock_journal = False
        before = self._snapshot(production)
        with self.assertRaises(UserError):
            self._post(scope, 0)
        self.assertEqual(self._snapshot(production), before)

    def test_forged_standard_valuation_receipt_is_not_creatable(self):
        production = self._production(timed=True)
        with self.assertRaises(UserError):
            self.env['quality.stock.posting'].with_context(quality_stock_kernel=True).create({
                'production_id': production.id, 'valuation_snapshot': {'standard_price': 1},
            })

    def test_five_component_real_time_consumption_has_one_native_entry_per_move(self):
        others = self.env['product.product']
        for price in (2, 3, 4, 5):
            component = self.env['product.product'].create({'name': 'Actual component %s' % price,
                'type': 'product', 'categ_id': self.component.categ_id.id, 'standard_price': price})
            others |= component
            incoming = self.env['stock.move'].create({'name': component.name, 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 20, 'price_unit': price,
                'location_id': self.env.ref('stock.stock_location_suppliers').id, 'location_dest_id': self.location.id})
            incoming._action_confirm(merge=False)
            incoming.write({'quantity': 20, 'picked': True})
            incoming._action_done()
            self.bom.bom_line_ids = [Command.create({'product_id': component.id, 'product_qty': 1})]
        production = self._production(timed=True)
        row = self._post(self._scope(production), 0)
        self.assertEqual(row.component_cost, 34)
        self.assertEqual(row.cost_variance, 6)
        self.assertEqual(len(row.line_ids), 6)
        self.assertEqual(len(row.line_ids.valuation_layer_id.account_move_id), 6)
        self.assertEqual(sum(production.move_raw_ids.filtered(lambda move: move.state == 'done').mapped('quantity')), 6)
        self.assertEqual(sum(self.env['stock.quant'].search([('product_id', 'in', others.ids), ('location_id', '=', self.location.id)]).mapped('quantity')), 76)

    def test_incremental_standard_releases_allow_remaining_work_and_close_after_fifth(self):
        production, shared, personal, pending, members = incremental_tests.TestStockPostingIncremental._scenario(self, two_operations=True)
        scopes = self.env['quality.stock.cost.scope']
        keys = self._keys(production)
        rows = self.env['quality.stock.posting']
        for index in range(5):
            if index:
                pending.date_end = pending.date_start + timedelta(minutes=10)
                if index < 4:
                    pending = pending.copy({'date_start': pending.date_end, 'date_end': False})
                    members[pending.id] = keys[index + 1:index + 2]
            scope = scopes._freeze_incremental(production, keys, members, keys[index:index + 1], 'standard-%s' % index)
            rows |= self._post(scope, index)
            self.assertEqual(production.state == 'done', index == 4)
            self.assertEqual(rows[:1].valuation_cost, 40)
        self.assertEqual(sum(rows.mapped('labour_cost')), 110)
        self.assertEqual(sum(rows.mapped('valuation_cost')), 200)
        self.assertEqual(sum(rows.mapped('cost_variance')), 10)
        self.assertEqual(len(rows.allocation_ids), 10)

    def test_hidden_native_accounting_documents_cannot_be_bypassed(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Native stock accounting reader', 'login': 'qunit-gl-reader', 'email': 'gl-reader@example.test',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_manager', 'account.group_account_readonly')])],
        })
        self.env['ir.rule'].create({'name': 'Hide actual accounting sources',
            'model_id': self.env['ir.model']._get('account.move').id,
            'domain_force': "[('id', '=', 0)]", 'perm_read': True, 'perm_write': False, 'perm_create': False, 'perm_unlink': False})
        before = self._snapshot(production)
        count = self.env['account.move'].search_count([])
        with self.assertRaises(AccessError):
            self.env['quality.stock.posting'].with_user(user)._post_one(scope.with_user(user), self._keys(production)[0], 'p-hidden-gl', 'r-hidden-gl')
        self.assertEqual(self._snapshot(production), before)
        self.assertEqual(self.env['account.move'].search_count([]), count)

    def test_native_operator_with_accounting_read_access_posts_without_custom_sudo(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Standard native operator', 'login': 'qunit-standard-operator', 'email': 'standard-operator@example.test',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_manager', 'account.group_account_readonly')])],
        })
        row = self.env['quality.stock.posting'].with_user(user)._post_one(
            scope.with_user(user), self._keys(production)[0], 'p-native-operator', 'r-native-operator')
        self.assertEqual(row.posted_by_id, user)
        self.assertEqual(row.valuation_cost, 40)
        self.assertEqual(set(row.line_ids.valuation_layer_id.account_move_id.mapped('state')), {'posted'})
