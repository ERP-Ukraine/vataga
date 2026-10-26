"""Enterprise ORM coverage of preparation only. No actual posting adapter exists."""

import json

from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_release as release_tests
from . import test_quality_unit_execution as execution_tests


@tagged('post_install', '-at_install')
class TestQualityUnitStockReleasePreparation(QualityCheckInProcessCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.type = 'product'
        cls.components = cls.env['product.product'].create([
            {'name': 'Компонент підготовки %s' % index, 'type': 'product'} for index in range(5)
        ])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id, 'type': 'normal',
            'bom_line_ids': [Command.create({'product_id': component.id,
                                            'product_qty': index + 2, 'product_uom_id': component.uom_id.id})
                             for index, component in enumerate(cls.components)],
        })
        # Native reservations in the ORM transaction, not a live staging DB.
        for component in cls.components:
            cls.env['stock.quant']._update_available_quantity(component, cls.warehouse.lot_stock_id, 100)

    def setUp(self):
        super().setUp()
        self.env.user.groups_id = [Command.link(self.env.ref(group).id) for group in (
            'quality_vataga.group_quality_board_approval', 'mrp.group_mrp_user', 'stock.group_stock_user',
        )]

    _workorder = release_tests.TestQualityUnitRelease._workorder
    _identify = release_tests.TestQualityUnitRelease._identify
    _decision = release_tests.TestQualityUnitRelease._decision
    _admissions = release_tests.TestQualityUnitRelease._admissions
    _route = release_tests.TestQualityUnitRelease._route
    _send = release_tests.TestQualityUnitRelease._send
    _receive = release_tests.TestQualityUnitRelease._receive
    _transfers = release_tests.TestQualityUnitRelease._transfers
    _received = release_tests.TestQualityUnitRelease._received
    _execution_wizard = release_tests.TestQualityUnitRelease._execution_wizard
    _ready = release_tests.TestQualityUnitRelease._ready
    _scenario = release_tests.TestQualityUnitRelease._scenario
    _release = release_tests.TestQualityUnitRelease._release
    _user = execution_tests.TestQualityUnitExecution._user

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity, uom)
        production.write({'product_qty': quantity, 'bom_id': self.bom.id})
        # Confirm/reserve the actual stock moves before enabling 13.3. Do not
        # create unrelated automatic Quality checks via action_confirm().
        (production.move_raw_ids | production.move_finished_ids)._action_confirm(merge=False)
        production.move_raw_ids._action_assign()
        production.move_raw_ids.picked = True
        production.move_finished_ids.write({'quantity': quantity, 'picked': False})
        return production

    def _authorized(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        permission = self.env['quality.unit.release.line'].search([('unit_id', '=', units[1].id)])
        return check, source, last, units, target, permission

    def _wizard(self, production, units):
        return self.env['quality.unit.stock.release.wizard'].create({
            'production_id': production.id,
            'line_ids': [Command.create({'unit_id': unit.id, 'selected': True}) for unit in units],
        })

    def _plans(self, production):
        return self.env['quality.unit.stock.release.plan'].search([('production_id', '=', production.id)])

    def _stock_snapshot(self, production):
        products = self.product | self.components
        return (production.product_qty, production.qty_producing, production.qty_produced, production.state,
                production.workorder_ids.mapped('state'), production.workorder_ids.mapped('qty_produced'),
                production.move_raw_ids.read(), production.move_finished_ids.read(),
                (production.move_raw_ids | production.move_finished_ids).move_line_ids.read(),
                self.env['stock.quant'].search([('product_id', 'in', products.ids)]).read(),
                self.env['stock.move'].search([('product_id', 'in', products.ids)]).ids)

    def test_one_of_five_proposes_one_fifth_of_all_five_components(self):
        check, source, last, units, target, permission = self._authorized()
        before = self._stock_snapshot(check.production_id)
        action = self._wizard(check.production_id, units[1]).action_prepare()
        plan = self._plans(check.production_id)
        self.assertEqual(action['res_id'], plan.id)
        self.assertEqual(plan.quantity, 1)
        self.assertEqual(plan.permission_ids, permission)
        data = json.loads(plan.evidence_snapshot)
        self.assertEqual(data['finished']['quantity'], 1)
        self.assertEqual(data['finished']['remaining'], 4)
        self.assertEqual(len(data['components']), 5)
        self.assertEqual([row['quantity'] for row in data['components']], [2, 3, 4, 5, 6])
        self.assertEqual([row['remaining_quantity'] for row in data['components']], [8, 12, 16, 20, 24])
        for row in data['components']:
            self.assertEqual(sum(item['stock_quantity'] for item in row['reservations']), row['stock_quantity'])
        self.assertFalse(data['posting_available'])
        self.assertFalse(data['costing']['verified'])
        self.assertEqual(before, self._stock_snapshot(check.production_id))
        self.assertEqual(check.production_id.quality_release_produced, 0)

    def test_four_units_without_permission_cannot_be_prepared(self):
        check, source, last, units, target, permission = self._authorized()
        for unit in units - units[1]:
            with self.assertRaises(ValidationError):
                self._wizard(check.production_id, unit).action_prepare()
        self.assertFalse(self._plans(check.production_id))

    def test_mixed_selection_is_atomic(self):
        check, source, last, units, target, permission = self._authorized()
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._wizard(check.production_id, units[1] | units[2]).action_prepare()
        self.assertFalse(self._plans(check.production_id))
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_repeat_request_does_not_duplicate_plan_chatter_or_consume_stock(self):
        check, source, last, units, target, permission = self._authorized()
        wizard = self._wizard(check.production_id, units[1])
        before = self._stock_snapshot(check.production_id)
        first = wizard.action_prepare()
        messages = check.production_id.message_ids.ids
        second = wizard.action_prepare()
        self.assertEqual(first['res_id'], second['res_id'])
        self.assertEqual(len(self._plans(check.production_id)), 1)
        self.assertEqual(messages, check.production_id.message_ids.ids)
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_different_proposals_are_not_stock_releases(self):
        check, source, last, units, target, permission = self._authorized()
        before = self._stock_snapshot(check.production_id)
        for _attempt in range(2):
            self._wizard(check.production_id, units[1]).action_prepare()
        self.assertEqual(len(self._plans(check.production_id)), 2)
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_admission_revoked_after_wizard_open_blocks_preparation(self):
        check, source, last, units, target, permission = self._authorized()
        wizard = self._wizard(check.production_id, units[1])
        self.assertTrue(wizard.line_ids.eligible)
        self._decision(target, mode='revoke', reason='Допуск відкликано').action_apply()
        with self.assertRaises(ValidationError):
            wizard.action_prepare()
        self.assertFalse(self._plans(check.production_id))

    def test_cancelled_release_permission_cannot_prepare(self):
        check, source, last, units, target, permission = self._authorized()
        wizard = self._wizard(check.production_id, units[1])
        self._release(check.production_id, permission, 'cancel', 'Скасовано до підготовки').action_apply()
        with self.assertRaises(ValidationError):
            wizard.action_prepare()
        self.assertFalse(self._plans(check.production_id))

    def test_preparation_does_not_prevent_normal_permission_cancellation(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        self._release(check.production_id, permission, 'cancel', 'План не є проведенням').action_apply()
        self.assertEqual(permission.state, 'cancelled')
        with self.assertRaises(ValidationError):
            self._plans(check.production_id).action_check_current()

    def test_board_role_does_not_replace_stock_and_mrp_roles(self):
        check, source, last, units, target, permission = self._authorized()
        user = self._user('stock_plan_denied', ['quality_vataga.group_quality_board_approval'])
        for group in ('mrp.group_mrp_user', 'stock.group_stock_user'):
            self.env.ref(group).write({'users': [Command.unlink(user.id)]})
        with self.assertRaises(AccessError):
            check.production_id.with_user(user).action_open_unit_stock_release_wizard()
        with self.assertRaises(AccessError):
            self.env['quality.unit.stock.release.wizard'].with_user(user).create({'production_id': check.production_id.id})

    def test_stock_preparer_does_not_gain_authorization_right(self):
        check, source, last, units, target, permission = self._authorized()
        user = self._user('stock_plan_allowed', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        self.assertFalse(user.has_group('quality_vataga.group_quality_board_approval'))
        plan = self.env['quality.unit.stock.release.plan'].with_user(user)._prepare(
            check.production_id.with_user(user), permission.with_user(user), 'stock-only-preparation',
        )
        self.assertEqual(plan.prepared_by_id, user)
        with self.assertRaises(AccessError):
            check.production_id.with_user(user).action_open_unit_release_wizard()

    def test_either_missing_native_role_blocks_actions_and_snapshot_reads(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        plan = self._plans(check.production_id)
        for index, missing in enumerate(('mrp.group_mrp_user', 'stock.group_stock_user')):
            user = self._user('stock_missing_role_' + str(index), ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
            self.env.ref(missing).write({'users': [Command.unlink(user.id)]})
            self.assertFalse(user.has_group(missing))
            self.assertFalse(check.production_id.with_user(user).can_prepare_unit_stock_release)
            for model in ('quality.unit.stock.release.plan', 'quality.unit.stock.release.wizard', 'quality.unit.stock.release.wizard.line'):
                self.assertFalse(self.env[model].with_user(user).check_access_rights('read', raise_exception=False))
            with self.assertRaises(AccessError):
                check.production_id.with_user(user).action_open_unit_stock_release_wizard()
            with self.assertRaises(AccessError):
                plan.with_user(user).read(['evidence_snapshot'])
            with self.assertRaises(AccessError):
                self.env['quality.unit.stock.release.plan'].with_user(user)._prepare(check.production_id.with_user(user), permission.with_user(user), 'missing-role')

    def test_role_revoked_after_wizard_open_blocks_existing_wizard(self):
        check, source, last, units, target, permission = self._authorized()
        user = self._user('stock_role_revoked', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        wizard = self.env['quality.unit.stock.release.wizard'].with_user(user).create({
            'production_id': check.production_id.id,
            'line_ids': [Command.create({'unit_id': units[1].id, 'selected': True})],
        })
        self.env.ref('stock.group_stock_user').write({'users': [Command.unlink(user.id)]})
        with self.assertRaises(AccessError):
            wizard.action_prepare()
        self.assertFalse(self._plans(check.production_id))

    def test_mrp_user_without_stock_does_not_break_empty_history_fields(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        user = self._user('stock_history_denied', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        self.env.ref('stock.group_stock_user').write({'users': [Command.unlink(user.id)]})
        self.assertFalse(check.production_id.with_user(user).quality_stock_release_plan_ids)
        self.assertFalse(units[1].with_user(user).stock_release_plan_ids)

    def test_plan_snapshot_respects_record_rules_on_underlying_permission(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        user = self._user('stock_plan_hidden_permission', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        self.env['ir.rule'].create({
            'name': 'Прихований дозвіл тестового плану',
            'model_id': self.env.ref('quality_vataga.model_quality_unit_release_line').id,
            'domain_force': repr([('id', '!=', permission.id)]),
        })
        with self.assertRaises(AccessError):
            self._plans(check.production_id).with_user(user).read(['evidence_snapshot'])

    def test_plan_snapshot_respects_record_rules_on_underlying_stock_lines(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        user = self._user('stock_plan_hidden_stock', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        hidden = check.production_id.move_raw_ids.move_line_ids[0]
        self.env['ir.rule'].create({
            'name': 'Прихований складський рядок тестового плану',
            'model_id': self.env.ref('stock.model_stock_move_line').id,
            'domain_force': repr([('id', '!=', hidden.id)]),
        })
        with self.assertRaises(AccessError):
            self._plans(check.production_id).with_user(user).read(['evidence_snapshot'])

    def test_native_roles_do_not_bypass_source_quality_check_rules(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        user = self._user('stock_plan_hidden_qc', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        self.env['ir.rule'].create({
            'name': 'Прихований вихідний QC тестового плану',
            'model_id': self.env['ir.model']._get('quality.check').id,
            'domain_force': repr([('id', '!=', target.id)]),
        })
        with self.assertRaises(AccessError):
            self._plans(check.production_id).with_user(user).read(['evidence_snapshot'])

    def test_direct_create_write_unlink_and_context_forgery_are_rejected(self):
        check, source, last, units, target, permission = self._authorized()
        plans = self.env['quality.unit.stock.release.plan']
        with self.assertRaises(UserError):
            plans.with_context(quality_vataga_stock_release_plan=True).create({'production_id': check.production_id.id})
        self._wizard(check.production_id, units[1]).action_prepare()
        plan = self._plans(check.production_id)
        for values in ({'quantity': 5}, {'permission_ids': [Command.clear()]}, {'evidence_snapshot': '{}'}, {'state': 'done'}):
            with self.assertRaises(UserError):
                plan.write(values)
        with self.assertRaises(UserError):
            plan.unlink()
        with self.assertRaises(UserError):
            check.production_id.write({'quality_stock_release_plan_ids': [Command.clear()]})

    def test_posting_is_blocked_even_with_forged_context_or_sudo(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        before = self._stock_snapshot(check.production_id)
        plan = self._plans(check.production_id)
        for record in (plan, plan.sudo().with_context(quality_vataga_stock_posting=True, skip_quality_check=True)):
            with self.assertRaisesRegex(UserError, 'Складське проведення QUNIT недоступне'):
                record.action_post()
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_foreign_mo_permission_and_unit_are_rejected(self):
        check, source, last, units, target, permission = self._authorized()
        foreign = self._production()
        # An ordinary MO is not eligible, and identities cannot be reassigned.
        with self.assertRaises(ValidationError):
            self.env['quality.unit.stock.release.plan']._prepare(foreign, permission, 'foreign')
        with self.assertRaises(ValidationError):
            self._wizard(foreign, units[1])
        self.assertFalse(self._plans(check.production_id))

    def test_wrong_allowed_company_cannot_open_or_read_plan(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        user = self._user('stock_plan_company', ['mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user'])
        other = self.env['res.company'].create({'name': 'Інша компанія плану'})
        user.company_ids = [Command.link(other.id)]
        with self.assertRaises((AccessError, ValidationError)):
            check.production_id.with_user(user).with_context(allowed_company_ids=other.ids).action_open_unit_stock_release_wizard()
        self.assertFalse(self._plans(check.production_id).with_user(user).with_context(allowed_company_ids=other.ids).search([]))

    def test_manual_consumption_is_not_silently_scaled(self):
        # BOM change is legal metadata, but cannot establish actual per-unit use.
        check, source, last, units, target, permission = self._authorized()
        self.bom.bom_line_ids[0].manual_consumption = True
        with self.assertRaises(ValidationError):
            self._wizard(check.production_id, units[1]).action_prepare()

    def test_bom_deviation_is_not_silently_scaled(self):
        check, source, last, units, target, permission = self._authorized()
        self.bom.bom_line_ids[0].product_qty = 3
        with self.assertRaises(ValidationError):
            self._wizard(check.production_id, units[1]).action_prepare()

    def test_unrepresentable_partial_component_blocks(self):
        piece = self.env.ref('uom.product_uom_unit').copy({
            'name': 'Неподільний компонент', 'uom_type': 'bigger', 'factor': 1, 'rounding': 1,
        })
        self.components[0].write({'uom_id': piece.id, 'uom_po_id': piece.id})
        self.bom.bom_line_ids[0].write({'product_qty': 0.4, 'product_uom_id': piece.id})
        check, source, last, units, target, permission = self._authorized()
        with self.assertRaises(ValidationError):
            self._wizard(check.production_id, units[1]).action_prepare()

    def test_component_uom_conversion_preserves_stock_quantity(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        self.bom.bom_line_ids[0].write({'product_qty': 1, 'product_uom_id': dozen.id})
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        data = json.loads(self._plans(check.production_id).evidence_snapshot)
        row = next(row for row in data['components'] if row['product_id'] == self.components[0].id)
        self.assertEqual(row['quantity'], 1)
        self.assertEqual(row['uom_id'], dozen.id)
        self.assertEqual(row['stock_quantity'], 12)

    def test_insufficient_real_reservation_blocks_without_any_stock_write(self):
        self.env['stock.quant']._update_available_quantity(self.components[0], self.warehouse.lot_stock_id, -95)
        check, source, last, units, target, permission = self._authorized()
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._wizard(check.production_id, units[1]).action_prepare()
        self.assertFalse(self._plans(check.production_id))
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_another_named_unit_can_be_prepared_later_without_releasing_either(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        execution = last.quality_execution_ids.filtered(lambda row: row.controlled_unit_id == units[3])
        second_check = execution._create_control(last, self.point)
        self._fill(second_check)
        self._decision(second_check).action_apply()
        self._release(check.production_id, units[3]).action_apply()
        self._wizard(check.production_id, units[3]).action_prepare()
        plans = self._plans(check.production_id)
        self.assertEqual(len(plans), 2)
        self.assertEqual(set(plans.permission_ids.unit_id.ids), set((units[1] | units[3]).ids))
        self.assertEqual(check.production_id.qty_produced, 0)
        self.assertEqual(check.production_id.quality_release_produced, 0)

    def test_real_finished_lot_or_serial_is_required_and_never_invented(self):
        check, source, last, units, target, permission = self._authorized()
        before = self.env['stock.lot'].search([('product_id', '=', self.product.id)]).ids
        for tracking in ('lot', 'serial'):
            self.product.tracking = tracking
            with self.assertRaises(ValidationError):
                self._wizard(check.production_id, units[1]).action_prepare()
        self.assertEqual(before, self.env['stock.lot'].search([('product_id', '=', self.product.id)]).ids)

    def test_stock_lines_and_native_backorder_are_never_split_by_preparation(self):
        check, source, last, units, target, permission = self._authorized()
        identity_fields = ['name', 'production_id', 'registration_run_id', 'run_ids', 'lot_id', 'sample_ids']
        run_fields = ['name', 'production_id', 'quantity', 'unit_ids', 'workorder_id', 'origin_check_id']
        before = (self.env['mrp.production'].search([]).ids, self.env['mrp.workorder'].search([]).ids,
                  self._stock_snapshot(check.production_id), units.read(identity_fields), check.operation_run_id.read(run_fields))
        self._wizard(check.production_id, units[1]).action_prepare()
        self.assertEqual(before, (self.env['mrp.production'].search([]).ids, self.env['mrp.workorder'].search([]).ids,
                                 self._stock_snapshot(check.production_id), units.read(identity_fields), check.operation_run_id.read(run_fields)))
        with self.assertRaises(UserError):
            check.production_id._split_productions()

    def test_standard_manufacturing_and_stock_guards_remain_closed(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        for method in (check.production_id.button_mark_done, check.production_id._post_inventory,
                       source.button_finish, last.button_finish, check.production_id.move_raw_ids._action_done,
                       check.production_id.move_finished_ids._action_done):
            with self.assertRaises(UserError):
                method()

    def test_actor_date_snapshot_and_chatter_record_only_preparation(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        plan = self._plans(check.production_id)
        self.assertEqual(plan.prepared_by_id, self.env.user)
        self.assertTrue(plan.prepared_at)
        self.assertIn(units[1].name, plan.evidence_snapshot)
        self.assertIn(plan, units[1].stock_release_plan_ids)
        message = check.production_id.message_ids.filtered(lambda item: plan.name in str(item.body))
        self.assertEqual(len(message), 1)
        self.assertIn('Складського проведення немає', str(message.body))
        self.assertEqual(plan.action_check_current()['tag'], 'display_notification')

    def test_request_unique_constraint_prevents_duplicate_records(self):
        check, source, last, units, target, permission = self._authorized()
        self._wizard(check.production_id, units[1]).action_prepare()
        plan = self._plans(check.production_id)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.cr.execute('''
                INSERT INTO quality_unit_stock_release_plan
                    (name, production_id, company_id, quantity, prepared_by_id, prepared_at,
                     request_key, evidence_snapshot, component_summary)
                SELECT name, production_id, company_id, quantity, prepared_by_id, prepared_at,
                       request_key, evidence_snapshot, component_summary
                FROM quality_unit_stock_release_plan WHERE id = %s
            ''', [plan.id])

    def test_action_views_show_preparation_without_any_posting_button(self):
        check, source, last, units, target, permission = self._authorized()
        action = check.production_id.action_open_unit_stock_release_wizard()
        self.assertEqual(action['views'], [(self.env.ref('quality_vataga.quality_unit_stock_release_wizard_form').id, 'form')])
        for xmlid in ('quality_unit_stock_release_wizard_form', 'quality_unit_stock_release_plan_form'):
            doc = etree.fromstring(self.env.ref('quality_vataga.' + xmlid).arch_db)
            self.assertFalse(doc.xpath("//button[@name='action_post']"))
        doc = etree.fromstring(check.production_id.get_view(view_type='form')['arch'])
        self.assertEqual(len(doc.xpath("//button[@name='action_open_unit_stock_release_wizard']")), 1)
        self.assertEqual(len(doc.xpath("//page[@name='quality_unit_release']")), 1)
