"""Full QUNIT/Quality/MRP integration: disposable Enterprise database only.

No stock/SVL mocks and no patch that opens the public Enterprise safety gate.
The private validated integration entry is the subject of these ORM tests.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest import TestCase

from lxml import etree
from psycopg2 import errors
from odoo import api, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_alert_repair as repairs
from . import test_quality_unit_execution as executions
from . import test_stock_release_native_contract as native


@tagged('post_install', '-at_install')
class TestQualityNativeBackorder(QualityCheckInProcessCase):
    _identify = repairs.TestQualityAlertRepair._identify
    _user = repairs.TestQualityAlertRepair._user
    _admit = repairs.TestQualityAlertRepair._admit
    _send = repairs.TestQualityAlertRepair._send
    _alert = repairs.TestQualityAlertRepair._alert
    _approve = repairs.TestQualityAlertRepair._approve
    _recheck = repairs.TestQualityAlertRepair._recheck
    _pass = repairs.TestQualityAlertRepair._pass
    _execution_wizard = executions.TestQualityUnitExecution._execution_wizard

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.location = cls.warehouse.lot_stock_id
        cls.warehouse.manu_type_id.create_backorder = 'ask'
        category = cls.env['product.category'].create({
            'name': 'Disposable QUNIT native FIFO', 'property_cost_method': 'fifo',
            'property_valuation': 'manual_periodic'})
        cls.product.write({'type': 'product', 'categ_id': category.id})
        cls.component = cls.env['product.product'].create({
            'name': 'Disposable native QUNIT component', 'type': 'product',
            'categ_id': category.id, 'standard_price': 10})
        center = cls.env['mrp.workcenter'].create({'name': 'Native QUNIT lifecycle', 'costs_hour': 60})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'bom_line_ids': [Command.create({'product_id': cls.component.id, 'product_qty': 2})],
            'operation_ids': [Command.create({'name': name, 'workcenter_id': center.id})
                              for name in ('First operation', 'Final operation')],
        })
        cls.point.visual_sample_control_required = True
        receipt = cls.env['stock.move'].create({
            'name': 'Real disposable component receipt', 'product_id': cls.component.id,
            'product_uom': cls.component.uom_id.id, 'product_uom_qty': 20,
            'location_id': cls.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': cls.warehouse.lot_stock_id.id, 'price_unit': 10})
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 20, 'picked': True})
        receipt._action_done()

    def setUp(self):
        super().setUp()
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Requires Enterprise Quality / Work Order integration.')
        self.operator = self._user('native-qc-inspector', ['quality.group_quality_user', 'mrp.group_mrp_user'])
        self.manager = self._user('native-release-manager', [
            'quality.group_quality_manager', 'mrp.group_mrp_user', 'stock.group_stock_user'])

    def _production(self, quantity=5, uom=None):
        production = native.StockReleaseNativeCase._production(self, quantity)
        production.write({'qty_producing': quantity, 'allow_workorder_dependencies': True})
        first, last = production.workorder_ids.sorted('id')
        last.blocked_by_workorder_ids = [Command.set(first.ids)]
        first.button_start()
        return production

    def _initial(self, fail=True):
        production = self._production()
        first, final = production.workorder_ids.sorted('id')
        check = self._check(production, workorder_id=first.id)
        self._identify(check, first)
        self._fill(check)
        if fail:
            check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
            check.do_fail()  # completed failed batch, four individual PASS rows
        else:
            check.do_pass()
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        self._admit(check, good)
        self._send(check, final, good).action_apply()
        first.time_ids.filtered(lambda timer: not timer.date_end)._close()
        return production, check, final, good.controlled_unit_id

    def _finish_final(self, production, final, units):
        receipts = self.env['quality.unit.transfer'].search([
            ('controlled_unit_id', 'in', units.ids), ('destination_workorder_id', '=', final.id), ('state', '=', 'sent')])
        self.env['quality.unit.transfer.wizard'].with_user(self.operator).create({
            'workorder_id': final.id, 'mode': 'receive',
            'line_ids': [Command.create({'transfer_id': row.id, 'selected': True}) for row in receipts],
        }).action_apply()
        self._execution_wizard(final, receipts).action_apply()
        processed = final.quality_execution_ids.filtered(lambda row: row.controlled_unit_id in units)
        self._execution_wizard(final, processed, 'finish').action_apply()
        check = processed._create_control(final, self.point)
        self._pass(check)
        self._admit(check, check.sample_ids)
        self._classify_native_duplicates(production)
        return check

    def _ready(self, fail=True):
        production, check, final, good = self._initial(fail)
        final_check = self._finish_final(production, final, good)
        # If Enterprise generated an additional unbound QC for the same test
        # point, explicitly classify that duplicate via the existing manager
        # workflow. The two identified stage QCs remain required and audited.
        self._classify_native_duplicates(production)
        return production, check, final_check, good

    def _classify_native_duplicates(self, production):
        duplicates = self.env['quality.check'].search([
            ('production_id', '=', production.id), ('point_id', '=', self.point.id),
            ('operation_run_id', '=', False), ('unit_admission_role', '=', 'required')])
        duplicates.with_user(self.manager).write({'unit_admission_role': 'optional'})

    def _repair(self, alert):
        # Native timer with a deterministic elapsed-time fixture in this
        # disposable test only; no fabricated stock or valuation records.
        alert = alert.with_user(self.operator)
        alert.action_start_native_repair()
        _production, workorder = alert._repair_target()
        timer = workorder.time_ids.filtered(lambda row: not row.date_end and row.user_id == self.operator)
        self.assertTrue(timer, 'Native repair start must create a real productivity interval')
        self.env.flush_all()
        self.env.cr.execute("UPDATE mrp_workcenter_productivity SET date_start=date_start-interval '5 minutes' WHERE id IN %s", [tuple(timer.ids)])
        timer.invalidate_recordset()
        alert.action_stop_native_repair()
        return repairs.TestQualityAlertRepair._repair(self, alert)

    def _release(self, production, units, key='native-release'):
        return production.with_user(self.manager)._quality_native_release(units.with_user(self.manager), key, confirmed=True)

    def _children(self, production):
        return self.env['mrp.production'].search([('quality_native_parent_id', '=', production.id)])

    def test_repaired_fifth_unit_finishes_original_mo_once_without_backorder(self):
        production, source, final, good = self._initial()
        alert = self._approve(self._alert(source))
        held = alert.vataga_failure_sample_ids.controlled_unit_id
        original = (source.quality_state, source.sample_ids.read(), source.sample_ids.measurement_value_ids.read())
        productions = self.env['mrp.production'].search([]).ids
        # This is repair on the original open MO, not the native-child timer path.
        repeat = repairs.TestQualityAlertRepair._repair(self, alert)
        self._pass(repeat)
        alert.action_close_alert()
        self._admit(repeat, repeat.sample_ids)
        self._send(repeat, final, repeat.sample_ids).action_apply()
        units = good | held
        self._finish_final(production, final, units)
        facts = self._release(production, units, 'all-five-after-repair')
        self.assertEqual((production.state, production.qty_produced), ('done', 5))
        self.assertEqual(len(facts), 5)
        self.assertFalse(self._children(production))
        self.assertEqual(self.env['mrp.production'].search([]).ids, productions)
        self.assertEqual(original, (source.quality_state, source.sample_ids.read(), source.sample_ids.measurement_value_ids.read()))
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -10)
        self.assertEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('quantity')), 5)
        layers = self.env['stock.valuation.layer'].search([]).ids
        self.assertEqual(self._release(production, units, 'all-five-after-repair'), facts)
        self.assertEqual(self.env['stock.valuation.layer'].search([]).ids, layers)
        with self.assertRaises(UserError):
            self._release(production, units, 'no-second-material-consumption')
        self.assertEqual(len(self.env['quality.unit.stock.fact'].search([('unit_id', 'in', units.ids)])), 5)

    def test_repaired_fifth_unit_with_repeat_fail_cannot_close_admit_or_advance(self):
        production, source, final, good = self._initial()
        alert = self._approve(self._alert(source))
        repeat = repairs.TestQualityAlertRepair._repair(self, alert)
        self._fill(repeat)
        repeat.update_measurement_visual_result(repeat.sample_ids.id, 'no')
        repeat.do_fail()
        before = (production.qty_produced, self.env['stock.valuation.layer'].search([]).ids,
                  self.env['quality.unit.stock.fact'].search([]).ids)
        for action in (alert.action_close_alert,
                       lambda: self._admit(repeat, repeat.sample_ids),
                       lambda: self._send(repeat, final, repeat.sample_ids).action_apply(),
                       lambda: self._release(production, good | repeat.sample_ids.controlled_unit_id)):
            with self.assertRaises(UserError):
                action()
        self.assertTrue(alert._matrix_hold_is_open())
        self.assertEqual(source.quality_state, 'fail')
        self.assertEqual(repeat.quality_state, 'fail')
        self.assertEqual(before, (production.qty_produced, self.env['stock.valuation.layer'].search([]).ids,
                                 self.env['quality.unit.stock.fact'].search([]).ids))

    def test_five_complete_pass_units_have_five_real_stock_facts(self):
        production, source, final, units = self._ready(False)
        facts = self._release(production, units)
        self.assertEqual(len(facts), 5)
        self.assertEqual(set(facts.unit_id.ids), set(units.ids))
        self.assertEqual(production.qty_produced, 5)
        self.assertFalse(self._children(production))
        self.assertTrue(all(row.move_line_id.move_id.state == 'done' for row in facts))
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -10)

    def test_four_pass_and_one_fail_have_protected_native_backorder_and_no_new_qunit(self):
        production, source, final, units = self._ready()
        alert = self._approve(self._alert(source))
        held = alert.vataga_failure_sample_ids.controlled_unit_id
        before = (source.sample_ids.measurement_value_ids.read(), source.operation_run_id.unit_ids.ids,
                  alert.vataga_matrix_failure_snapshot, source.message_ids.ids,
                  self.env['quality.production.unit'].search([]).ids)
        facts = self._release(production, units)
        child = self._children(production)
        self.assertEqual((production.state, production.qty_produced, child.product_qty), ('done', 4, 1))
        self.assertTrue(child.quality_transfer_controlled)
        self.assertEqual(held._quality_current_production(), child)
        self.assertEqual(held.production_id, production)  # immutable birth MO
        self.assertEqual(set(facts.unit_id.ids), set(units.ids))
        self.assertTrue(alert._matrix_hold_is_open())
        self.assertEqual(before, (source.sample_ids.measurement_value_ids.read(), source.operation_run_id.unit_ids.ids,
            alert.vataga_matrix_failure_snapshot, source.message_ids.ids,
            self.env['quality.production.unit'].search([]).ids))
        for operation in (child.button_mark_done, child._split_productions, child._post_inventory):
            with self.assertRaises(UserError):
                operation()

    def test_intermediate_pass_or_unprocessed_final_operation_cannot_release(self):
        production, source, final, units = self._initial()
        with self.assertRaises(UserError):
            self._release(production, units)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(self._children(production))

    def test_open_alert_at_final_stage_blocks_selected_unit_and_is_atomic(self):
        production, source, final, units = self._ready(False)
        # An explicit historic scope represents an unresolved independent alert.
        alert = self._alert(final).with_user(self.manager)
        alert._bind_matrix_failure_scope(final.sample_ids[:1], 'Independent unresolved defect')
        before = self.env['stock.valuation.layer'].search([]).ids
        with self.assertRaises(UserError):
            self._release(production, units)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(self.env['stock.valuation.layer'].search([]).ids, before)
        self.assertFalse(self.env['quality.unit.stock.fact'].search([]))

    def test_repeat_confirmation_is_idempotent_and_another_request_cannot_double_release(self):
        production, source, final, units = self._ready()
        facts = self._release(production, units)
        svl = self.env['stock.valuation.layer'].search([]).ids
        children = self._children(production)
        self.assertEqual(self._release(production, units), facts)
        self.assertEqual(self._children(production), children)
        self.assertEqual(self.env['stock.valuation.layer'].search([]).ids, svl)
        with self.assertRaises(UserError):
            self._release(production, units, 'second-release')

    def test_backorder_repair_reuses_identity_keeps_other_alert_and_can_complete_route(self):
        production, source, final, units = self._ready()
        alert = self._approve(self._alert(source))
        other = self._approve(self._alert(source))
        held = alert.vataga_failure_sample_ids.controlled_unit_id
        self._release(production, units)
        child = self._children(production)
        original_layers = production.move_finished_ids.stock_valuation_layer_ids.read(['quantity', 'value'])
        repeat = self._repair(alert)
        self.assertEqual(repeat.production_id, child)
        self.assertEqual(repeat.sample_ids.controlled_unit_id, held)
        self._pass(repeat)
        alert.action_close_alert()
        self.assertTrue(other._matrix_hold_is_open())
        with self.assertRaises(ValidationError):
            self._admit(repeat, repeat.sample_ids)
        other_repeat = self._repair(other)
        self._pass(other_repeat)
        other.action_close_alert()
        self._admit(other_repeat, other_repeat.sample_ids)
        child_final = child._quality_equivalent_workorder(final.workorder_id)
        self._send(other_repeat, child_final, other_repeat.sample_ids).action_apply()
        self._finish_final(child, child_final, held)
        self._release(child, held, 'complete-repaired-remainder')
        self.assertEqual(child.qty_produced, 1)
        self.assertEqual(production.move_finished_ids.stock_valuation_layer_ids.read(['quantity', 'value']), original_layers)
        self.assertEqual(self.env['quality.unit.stock.fact'].search_count([('unit_id', '=', held.id)]), 1)

    def test_direct_facts_succession_and_native_bypass_are_forbidden(self):
        production, source, final, units = self._ready()
        for model in ('quality.unit.continuation', 'quality.unit.stock.fact'):
            with self.assertRaises(UserError):
                self.env[model].with_user(self.manager).with_context(quality_native_release=True).create({})
        with self.assertRaises(UserError):
            production.with_context(skip_backorder=True, mo_ids_to_backorder=production.ids).button_mark_done()
        with self.assertRaises(UserError):
            production.with_user(self.manager).action_open_quality_native_release()
        self.assertEqual(production.qty_produced, 0)
        for model, xmlid in (
                ('mrp.production', 'mrp.mrp_production_form_view'),
                ('quality.production.unit', 'quality_vataga.quality_production_unit_form')):
            arch = etree.fromstring(self.env[model].get_view(
                view_id=self.env.ref(xmlid).id, view_type='form')['arch'])
            page = arch.xpath("//page[@name='quality_native_history']")
            self.assertEqual(len(page), 1)
            self.assertFalse(page[0].xpath('.//button'))

    def test_fact_create_denial_rolls_back_native_stock_and_backorder_without_mocks(self):
        production, source, final, units = self._ready()
        self.env['ir.rule'].create({
            'name': 'Disposable deny stock fact creation',
            'model_id': self.env['ir.model']._get_id('quality.unit.stock.fact'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': False, 'perm_write': False,
            'perm_unlink': False, 'perm_create': True,
        })
        before = self.env['stock.valuation.layer'].search([]).ids
        with self.assertRaises(AccessError):
            self._release(production, units)
        self.env.invalidate_all()
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(production.product_qty, 5)
        self.assertFalse(self._children(production))
        self.assertEqual(self.env['stock.valuation.layer'].search([]).ids, before)

    def test_posted_qunit_and_native_stock_history_cannot_be_reused_changed_or_deleted(self):
        production, source, final, units = self._ready(False)
        facts = self._release(production, units)
        unit = units[:1]
        self.assertTrue(unit._stock_posted())
        for operation in (unit.unlink, lambda: unit.write({'production_id': production.id}),
                          facts.unlink, lambda: facts.write({'request_key': 'replace'}),
                          lambda: facts.move_line_id.write({'quantity': 1}),
                          lambda: facts.move_line_id.write({'consume_line_ids': [Command.clear()]})):
            with self.assertRaises(UserError):
                operation()
        with self.assertRaises(UserError):
            unit._assert_not_stock_posted()

    def test_roles_duplicates_and_explicit_backorder_confirmation_are_required(self):
        production, source, final, units = self._ready()
        with self.assertRaises(AccessError):
            production.with_user(self.operator)._quality_native_release(units, 'no-stock-role', confirmed=True)
        with self.assertRaises(ValidationError):
            production.with_user(self.manager)._quality_native_release(units, 'not-confirmed')
        with self.assertRaises(ValidationError):
            production.with_user(self.manager)._quality_native_release(units.browse([units[0].id] * 4), 'duplicates', confirmed=True)

    def test_unidentified_required_check_is_not_silently_ignored(self):
        production, source, final, units = self._ready()
        self._check(production, workorder_id=final.workorder_id.id)
        with self.assertRaises(UserError):
            self._release(production, units)
        self.assertEqual(production.qty_produced, 0)

    def test_admission_revocation_changes_release_version_and_blocks_stock(self):
        production, source, final, units = self._ready()
        before = production.quality_transfer_revision
        admission = final.unit_admission_ids.filtered(lambda row: row.state == 'confirmed')[:1]
        admission.with_user(self.operator)._revoke('Disposable native release revocation')
        self.assertGreater(production.quality_transfer_revision, before)
        with self.assertRaises(UserError):
            self._release(production, units)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(self._children(production))

    def test_native_release_does_not_require_technical_board_permission_for_good_units(self):
        production, source, final, units = self._ready()
        warehouse = self._user('native-warehouse-without-board', [
            'quality.group_quality_user', 'mrp.group_mrp_user', 'stock.group_stock_user'])
        self.assertFalse(warehouse.has_group('quality_vataga.group_quality_board_approval'))
        facts = production.with_user(warehouse)._quality_native_release(
            units.with_user(warehouse), 'ordinary-native-warehouse', confirmed=True)
        self.assertEqual(len(facts), 4)
        self.assertEqual(facts.create_uid, warehouse)

    def test_concurrent_release_from_stale_snapshot_retries_without_duplicate_stock(self):
        # This test commits its OWN fixtures in independent transactions. It
        # may run only in a disposable Enterprise DB that will be dropped in
        # full by the test operator; never in a copied live staging DB in place.
        if not self.env.cr.dbname.startswith('qunit_enterprise_'):
            self.skipTest('Requires disposable qunit_enterprise_* DB; committed concurrency fixture.')
        registry = self.registry
        with registry.cursor() as cr:
            env = api.Environment(cr, self.env.uid, {})
            cr.execute("SELECT to_regclass('quality_unit_stock_fact')")
            if not cr.fetchone()[0]:
                self.skipTest('Requires committed phase B schema.')

            class Fixture(TestCase):
                _production = TestQualityNativeBackorder._production
                _initial = TestQualityNativeBackorder._initial
                _ready = TestQualityNativeBackorder._ready
                _finish_final = TestQualityNativeBackorder._finish_final
                _classify_native_duplicates = TestQualityNativeBackorder._classify_native_duplicates
                _check = QualityCheckInProcessCase._check
                _identify = TestQualityNativeBackorder._identify
                _admit = TestQualityNativeBackorder._admit
                _send = TestQualityNativeBackorder._send
                _execution_wizard = TestQualityNativeBackorder._execution_wizard
                _pass = TestQualityNativeBackorder._pass

                def _fill(self, check):
                    for sample in check.sample_ids:
                        check.update_measurement_visual_result(sample.id, 'yes')

            fixture = Fixture()
            fixture.env = env
            fixture.warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
            fixture.location = fixture.warehouse.lot_stock_id
            category = env['product.category'].create({'name': 'Disposable concurrent native FIFO',
                'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
            fixture.product = env['product.product'].create({'name': 'Disposable concurrent QUNIT', 'type': 'product', 'categ_id': category.id})
            component = env['product.product'].create({'name': 'Disposable concurrent component',
                'type': 'product', 'categ_id': category.id, 'standard_price': 10})
            center = env['mrp.workcenter'].create({'name': 'Disposable concurrent native workcenter'})
            fixture.bom = env['mrp.bom'].create({'product_tmpl_id': fixture.product.product_tmpl_id.id,
                'product_qty': 1, 'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': 2})],
                'operation_ids': [Command.create({'name': name, 'workcenter_id': center.id}) for name in ('First', 'Final')]})
            fixture.team = env['quality.alert.team'].search([], limit=1)
            fixture.test_type = env['quality.point.test_type'].search([], limit=1)
            if not fixture.team:
                fixture.team = env['quality.alert.team'].create({'name': 'Disposable concurrency team'})
            if not fixture.test_type:
                fixture.test_type = env['quality.point.test_type'].create({'name': 'Disposable visual control', 'technical_name': 'native_concurrent_visual'})
            fixture.point = env['quality.point'].create({'title': 'Disposable concurrent visual QCP',
                'team_id': fixture.team.id, 'test_type_id': fixture.test_type.id, 'x_control_stage': 'in_process',
                'picking_type_ids': [Command.set(fixture.warehouse.manu_type_id.ids)], 'visual_sample_control_required': True})
            fixture.operator = fixture.manager = env['res.users'].with_context(no_reset_password=True).create({
                'name': 'Disposable native concurrency actor', 'login': 'native-concurrency-%s' % center.id,
                'company_id': env.company.id, 'company_ids': [Command.set(env.company.ids)],
                'groups_id': [Command.set([env.ref(name).id for name in (
                    'base.group_user', 'quality.group_quality_manager', 'mrp.group_mrp_user', 'stock.group_stock_user')])]})
            receipt = env['stock.move'].create({'name': 'Disposable concurrency receipt', 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 20, 'price_unit': 10,
                'location_id': env.ref('stock.stock_location_suppliers').id, 'location_dest_id': fixture.location.id})
            receipt._action_confirm(merge=False)
            receipt.write({'quantity': 20, 'picked': True})
            receipt._action_done()
            production, _source, _final, units = fixture._ready()
            production_id, unit_ids = production.id, units.ids
            uid = fixture.manager.id
            cr.commit()
        snapshot_ready, committed = Event(), Event()

        def stale_request():
            with registry.cursor() as cr:
                env = api.Environment(cr, uid, {})
                production = env['mrp.production'].browse(production_id)
                self.assertEqual(production.qty_produced, 0)
                snapshot_ready.set()
                if not committed.wait(20):
                    raise AssertionError('First release did not commit')
                try:
                    production._quality_native_release(env['quality.production.unit'].browse(unit_ids), 'concurrent-native', confirmed=True)
                except errors.SerializationFailure:
                    cr.rollback()
                else:
                    raise AssertionError('Stale release must retry after the shared MO version changes')
                env = api.Environment(cr, uid, {})
                facts = env['mrp.production'].browse(production_id)._quality_native_release(
                    env['quality.production.unit'].browse(unit_ids), 'concurrent-native', confirmed=True)
                result = facts.ids
                cr.commit()
                return result

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stale_request)
            try:
                self.assertTrue(snapshot_ready.wait(20))
                with registry.cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    facts = env['mrp.production'].browse(production_id)._quality_native_release(
                        env['quality.production.unit'].browse(unit_ids), 'concurrent-native', confirmed=True)
                    fact_ids = facts.ids
                    cr.commit()
                committed.set()
                self.assertEqual(future.result(timeout=30), fact_ids)
            finally:
                committed.set()
        with registry.cursor() as cr:
            env = api.Environment(cr, uid, {})
            self.assertEqual(env['quality.unit.stock.fact'].search_count([('production_id', '=', production_id)]), 4)
            self.assertEqual(env['mrp.production'].search_count([('quality_native_parent_id', '=', production_id)]), 1)
            self.assertEqual(env['mrp.production'].browse(production_id).qty_produced, 4)
