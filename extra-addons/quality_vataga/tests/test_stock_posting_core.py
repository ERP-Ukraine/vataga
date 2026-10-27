"""Real native stock/valuation tests of the shipped kernel, no Quality mocks.

Use run_stock_posting_core.py for the disposable Community-only installation.
The full Enterprise module deliberately blocks this kernel entry point.
"""

from datetime import timedelta
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase


@tagged('post_install', '-at_install')
class TestStockPostingKernel(StockReleaseNativeCase):
    def setUp(self):
        super().setUp()
        if 'quality.production.unit' in self.env:
            self.skipTest('Kernel-only Community probe; Enterprise bridge is intentionally blocked.')
        self.env.user.groups_id = [Command.link(self.env.ref(name).id) for name in ('mrp.group_mrp_user', 'stock.group_stock_user')]

    def _keys(self, production):
        return ['%s:unit:%s' % (production.id, index) for index in range(5)]

    def _scope(self, production, members=None):
        keys = self._keys(production)
        return self.env['quality.stock.cost.scope']._freeze(
            production, keys, members if members is not None else {timer.id: keys for timer in production.workorder_ids.time_ids},
        )

    def _post(self, scope, index, request=None, permission=None):
        return self.env['quality.stock.posting']._post_one(
            scope, self._keys(scope.production_id)[index],
            permission or '%s:permission:%s' % (scope.production_id.id, index),
            request or '%s:request:%s' % (scope.production_id.id, index),
        )

    def test_five_sequential_posts_conserve_actual_quantity_cost_and_labour(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        records = self.env['quality.stock.posting']
        historical = {}
        for index in range(5):
            receipt = self._post(scope, index)
            records |= receipt
            self.assertEqual(self._stock(), (18 - index * 2, index + 1))
            self.assertEqual(production.qty_produced, index + 1)
            self.assertAlmostEqual(receipt.component_cost, 20)
            self.assertAlmostEqual(receipt.labour_cost, 12)
            finished_layers = receipt.line_ids.filtered(lambda row: row.kind == 'finished').valuation_layer_id
            self.assertAlmostEqual(sum(finished_layers.mapped('value')), 32)
            historical[receipt.id] = receipt.finished_line_id.consume_line_ids.ids
            for previous in records:
                self.assertEqual(previous.finished_line_id.consume_line_ids.ids, historical[previous.id])
                self.assertEqual(previous.finished_line_id.consume_line_ids.produce_line_ids, previous.finished_line_id)
            if index < 4:
                self.assertNotEqual(production.state, 'done')
                self.assertNotIn('done', production.workorder_ids.mapped('state'))
        self.assertEqual(production.state, 'done')
        self.assertEqual(production.workorder_ids.state, 'done')
        self.assertAlmostEqual(sum(records.mapped('labour_cost')), 60)
        self.assertAlmostEqual(sum(records.allocation_ids.mapped('amount')), 60)
        self.assertAlmostEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('value')), -100)
        self.assertAlmostEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 160)
        self.assertEqual(len(records), 5)

    def test_personal_and_shared_intervals_have_explicit_independent_shares(self):
        production = self._production(timed=True)
        original = production.workorder_ids.time_ids
        personal = original.copy({'date_start': original.date_end, 'date_end': original.date_end + timedelta(minutes=10)})
        keys = self._keys(production)
        scope = self._scope(production, {original.id: keys, personal.id: keys[:1]})
        first, second = self._post(scope, 0), self._post(scope, 1)
        self.assertAlmostEqual(first.labour_cost, 22)
        self.assertAlmostEqual(second.labour_cost, 12)
        self.assertEqual(len(first.allocation_ids), 2)
        self.assertEqual(len(second.allocation_ids), 1)

    def test_multiple_real_component_layers_are_summed_once_per_unit(self):
        component = self.env['product.product'].create({
            'name': 'Second actual component', 'type': 'product',
            'categ_id': self.component.categ_id.id, 'standard_price': 10,
        })
        receipt = self.env['stock.move'].create({
            'name': 'Second actual FIFO receipt', 'product_id': component.id,
            'product_uom': component.uom_id.id, 'product_uom_qty': 20,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.location.id, 'price_unit': 10,
        })
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 20, 'picked': True})
        receipt._action_done()
        self.bom.bom_line_ids = [Command.create({'product_id': component.id, 'product_qty': 3})]
        production = self._production(timed=True)
        scope = self._scope(production)
        records = self.env['quality.stock.posting']
        for index in range(5):
            posting = self._post(scope, index)
            records |= posting
            self.assertAlmostEqual(posting.component_cost, 50)
            self.assertEqual(len(posting.line_ids.filtered(lambda line: line.kind == 'component').valuation_layer_id), 2)
            self.assertAlmostEqual(posting.finished_move_id.stock_valuation_layer_ids.value, 62)
        self.assertAlmostEqual(sum(records.mapped('component_cost')), 250)
        self.assertAlmostEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('value')), 310)

    def test_open_interval_growth_is_blocked_until_it_is_closed_and_frozen(self):
        production = self._production(timed=True)
        timer = production.workorder_ids.time_ids
        timer.date_end = False
        with self.assertRaises(ValidationError):
            self._scope(production)
        timer.date_end = timer.date_start + timedelta(hours=2)
        scope = self._scope(production)
        self.assertAlmostEqual(self._post(scope, 0).labour_cost, 24)
        with self.assertRaises(UserError):
            timer.date_end += timedelta(hours=1)

    def test_missing_scope_and_overlapping_intervals_are_rejected(self):
        production = self._production(timed=True)
        with self.assertRaises(ValidationError):
            self._scope(production, {})
        timer = production.workorder_ids.time_ids
        timer.copy()
        with self.assertRaises(ValidationError):
            self._scope(production)
        self.assertFalse(self.env['quality.stock.cost.scope'].search([('production_id', '=', production.id)]))

    def test_new_time_after_first_post_blocks_further_cost_without_revaluation(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        receipt = self._post(scope, 0)
        before = self._snapshot(production)
        timer = production.workorder_ids.time_ids
        with self.assertRaises(UserError):
            timer.copy({'date_start': timer.date_end, 'date_end': timer.date_end + timedelta(minutes=10)})
        self.assertEqual(before, self._snapshot(production))
        self.assertAlmostEqual(receipt.finished_move_id.stock_valuation_layer_ids.value, 32)

    def test_repeat_request_is_idempotent_but_new_request_cannot_reuse_unit_or_permission(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        first = self._post(scope, 0)
        before = self._snapshot(production)
        messages = production.message_ids.ids
        self.assertEqual(self._post(scope, 0), first)
        for index, permission in [(0, None), (1, first.permission_key)]:
            with self.assertRaises(ValidationError):
                self._post(scope, index, request='new-request', permission=permission)
        self.assertEqual(before, self._snapshot(production))
        self.assertEqual(messages, production.message_ids.ids)

    def test_permission_from_another_mo_cannot_be_reused_and_stock_is_unchanged(self):
        first_mo = self._production(timed=True)
        second_mo = self._production(timed=True)
        first = self._post(self._scope(first_mo), 0)
        second_scope = self._scope(second_mo)
        before = self._snapshot(second_mo)
        with self.assertRaises(ValidationError):
            self._post(second_scope, 0, permission=first.permission_key)
        self.assertEqual(before, self._snapshot(second_mo))

    def test_registry_insert_failure_rolls_back_moves_reserves_costs_and_chatter(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        self._post(scope, 0)
        before = self._snapshot(production)
        messages = production.message_ids.ids
        # Real DB rejection at journal INSERT, after actual raw/finished SVLs
        # exist. No mocked stock methods or valuation calculations.
        self.env.cr.execute("ALTER TABLE quality_stock_posting ADD CONSTRAINT kernel_fault CHECK(unit_key != %s)", [self._keys(production)[1]])
        with self.assertRaises(IntegrityError):
            self._post(scope, 1)
        self.env.invalidate_all()
        self.assertEqual(before, self._snapshot(production))
        self.assertEqual(messages, production.message_ids.ids)
        self.assertEqual(len(self.env['quality.stock.posting']._receipts(production)), 1)

    def test_direct_creation_modification_deletion_and_context_forgery_are_denied(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        posting = self._post(scope, 0)
        for model in ('quality.stock.cost.scope', 'quality.stock.posting', 'quality.stock.posting.line', 'quality.stock.cost.allocation'):
            with self.assertRaises(UserError):
                self.env[model].with_context(quality_stock_kernel=True).create({})
            with self.assertRaises(UserError):
                posting.env[model].create({})
        for record in (scope, posting, posting.line_ids, posting.allocation_ids):
            with self.assertRaises(UserError):
                record.write({})
            with self.assertRaises(UserError):
                record.unlink()
        with self.assertRaises(UserError):
            production.write({'quality_stock_revision': 0})

    def test_native_genealogy_and_inverse_edits_are_protected(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        first, second = self._post(scope, 0), self._post(scope, 1)
        for record, field in [(first.finished_line_id, 'consume_line_ids'),
                              (first.finished_line_id.consume_line_ids, 'produce_line_ids')]:
            with self.assertRaises(UserError):
                record.write({field: [Command.clear()]})
        with self.assertRaises(UserError):
            production._post_inventory()
        with self.assertRaises(UserError):
            second.finished_line_id.consume_line_ids = [Command.set(first.finished_line_id.consume_line_ids.ids)]

    def test_actual_layers_and_consumption_cannot_be_rewritten(self):
        production = self._production(timed=True)
        receipt = self._post(self._scope(production), 0)
        for record, vals in [(receipt.finished_move_id, {'price_unit': 0}),
                             (receipt.finished_line_id, {'quantity': 2}),
                             (receipt.finished_move_id.stock_valuation_layer_ids, {'value': 0})]:
            with self.assertRaises(UserError):
                record.write(vals)

    def test_direct_remaining_move_post_cannot_bypass_the_adapter_with_rpc_context(self):
        production = self._production(timed=True)
        self._post(self._scope(production), 0)
        before = self._snapshot(production)
        remaining = production.move_raw_ids.filtered(lambda move: move.state not in ('done', 'cancel'))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            remaining.with_context(quality_stock_kernel=True)._action_done()
        self.assertEqual(before, self._snapshot(production))

    def test_avco_layers_follow_actual_component_value_plus_one_labour_share(self):
        self.product.categ_id.property_cost_method = 'average'
        production = self._production(timed=True)
        receipt = self._post(self._scope(production), 0)
        self.assertAlmostEqual(receipt.component_cost, 20)
        self.assertAlmostEqual(receipt.finished_move_id.stock_valuation_layer_ids.value, 32)

    def test_new_line_cannot_attach_to_old_consumption_via_create(self):
        production = self._production(timed=True)
        receipt = self._post(self._scope(production), 0)
        with self.assertRaises(UserError):
            self.env['stock.move.line'].create({
                'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
                'location_id': production.production_location_id.id,
                'location_dest_id': production.location_dest_id.id,
                'consume_line_ids': [Command.set(receipt.finished_line_id.consume_line_ids.ids)],
            })

    def test_real_operator_with_native_valuation_access_can_post_without_sudo(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Native kernel operator', 'login': 'kernel-real-operator',
            'email': 'kernel-operator@example.invalid',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_manager',
            )])],
        })
        receipt = self.env['quality.stock.posting'].with_user(user)._post_one(
            scope.with_user(user), self._keys(production)[0], 'operator-permission', 'operator-request',
        )
        self.assertEqual(receipt.posted_by_id, user)
        self.assertEqual(self._stock(), (18, 1))

    def test_both_user_roles_do_not_bypass_native_valuation_layer_acl(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Operator without valuation access', 'login': 'kernel-no-valuation',
            'company_id': self.env.company.id,
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_user',
            )])],
        })
        before = self._snapshot(production)
        with self.assertRaises(AccessError):
            self.env['quality.stock.posting'].with_user(user)._post_one(
                scope.with_user(user), self._keys(production)[0], 'no-valuation-permission', 'no-valuation-request',
            )
        self.assertEqual(before, self._snapshot(production))

    def test_undocumented_native_production_blocks_named_posting(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        self._post_one(production)  # real stock, deliberately absent from journal
        with self.assertRaises(ValidationError):
            self._post(scope, 1)

    def test_missing_either_native_role_denies_the_kernel(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        for missing in ('mrp.group_mrp_user', 'stock.group_stock_user'):
            user = self.env['res.users'].with_context(no_reset_password=True).create({
                'name': missing, 'login': 'kernel-' + missing, 'company_id': self.env.company.id,
                'groups_id': [Command.set([self.env.ref(name).id for name in ('base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_user')])],
            })
            self.env.ref(missing).write({'users': [Command.unlink(user.id)]})
            with self.assertRaises(AccessError):
                self.env['quality.stock.posting'].with_user(user)._post_one(scope.with_user(user), self._keys(production)[0], 'p', 'r')

    def test_hidden_component_document_is_not_omitted_or_posted_with_sudo(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Restricted operator', 'login': 'kernel-restricted-operator',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'base.group_user', 'mrp.group_mrp_user', 'stock.group_stock_manager',
            )])],
        })
        self.env['ir.rule'].create({
            'name': 'Hide actual component from kernel operator',
            'model_id': self.env['ir.model']._get('stock.move').id,
            'domain_force': "[('id', '!=', %s)]" % production.move_raw_ids.id,
            'perm_read': True, 'perm_write': False, 'perm_create': False, 'perm_unlink': False,
        })
        before = self._snapshot(production)
        with self.assertRaises(AccessError):
            self.env['quality.stock.posting'].with_user(user)._post_one(
                scope.with_user(user), self._keys(production)[0], 'hidden-permission', 'hidden-request',
            )
        self.assertEqual(before, self._snapshot(production))

    def test_fifo_real_time_accounting_is_outside_supported_profiles(self):
        production = self._production(timed=True)
        scope = self._scope(production)
        self.product.categ_id.property_valuation = 'real_time'
        before = self._stock()
        with self.assertRaises(ValidationError):
            self._post(scope, 0)
        self.assertEqual(before, self._stock())
