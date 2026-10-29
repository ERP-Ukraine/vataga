"""Real Enterprise ORM scenarios; run only on a disposable test database."""

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_matrix_hold as holds
from . import test_quality_unit_execution as execution_tests


@tagged('post_install', '-at_install')
class TestQualityAlertRepair(QualityCheckInProcessCase):
    _workorder = holds.TestQualityMatrixHold._workorder
    _identify = holds.TestQualityMatrixHold._identify
    _ready = holds.TestQualityMatrixHold._ready
    _user = holds.TestQualityMatrixHold._user
    _scenario = holds.TestQualityMatrixHold._scenario
    _alert = holds.TestQualityMatrixHold._alert
    _admit = holds.TestQualityMatrixHold._admit
    _send = holds.TestQualityMatrixHold._send
    _business_snapshot = holds.TestQualityMatrixHold._business_snapshot
    _execution_wizard = execution_tests.TestQualityUnitExecution._execution_wizard

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity=quantity, uom=uom)
        production.product_qty = quantity
        return production

    def setUp(self):
        super().setUp()
        self.operator = self._user('repair-inspector', ['quality.group_quality_user', 'mrp.group_mrp_user'])
        self.manager = self._user('repair-manager', [
            'quality.group_quality_manager', 'mrp.group_mrp_user', 'stock.group_stock_user'])

    def _approve(self, alert, decision='repair'):
        alert = alert.with_user(self.manager)
        alert.write({'decision_type': decision, 'approval_user_id': self.manager.id})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        return alert

    def _recheck(self, alert):
        action = alert.with_user(self.operator).action_create_repair_check()
        self.assertEqual(action['res_model'], 'quality.check')
        self.assertEqual(action['views'], [(False, 'form')])
        return self.env['quality.check'].browse(action['res_id']).with_user(self.operator)

    def _repair(self, alert):
        self.env['quality.alert.repair.wizard'].with_user(self.operator).create({
            'alert_id': alert.id, 'note': 'Доопрацьовано всі зазначені вироби',
        }).action_apply()
        return self._recheck(alert)

    def _pass(self, check):
        self._fill(check)
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')

    def test_repair_pass_close_explicit_admission_and_movement_preserve_history(self):
        source, workorder, target = self._scenario()
        alert = self._approve(self._alert(source))
        original = alert.vataga_failure_sample_ids
        good = source.sample_ids - original
        self._admit(source, good)
        self._send(source, target, good).action_apply()  # activate protected route
        source.do_fail()
        before = (source.quality_state, original.read(), original.measurement_value_ids.read(),
                  alert.vataga_matrix_failure_snapshot, source.sample_ids.controlled_unit_id.ids,
                  source.message_ids.ids, self._business_snapshot(source.production_id))
        units_before = self.env['quality.production.unit'].search([]).ids
        repeat = self._repair(alert)
        self.assertEqual(repeat.in_process_sample_quantity, 1)
        self.assertNotEqual(repeat.operation_run_id, source.operation_run_id)
        self.assertEqual(repeat.operation_run_id.registration_mode, 'reuse')
        self.assertEqual(repeat.sample_ids.controlled_unit_id, original.controlled_unit_id)
        self.assertEqual(self.env['quality.production.unit'].search([]).ids, units_before)
        self._pass(repeat)
        self.assertTrue(repeat._get_unit_admission_error(repeat.sample_ids))
        alert.action_close_alert()
        self.assertTrue(alert._is_managed_closed())
        self.assertFalse(alert._matrix_hold_is_open())
        self.assertFalse(repeat._get_unit_admission_error(repeat.sample_ids))
        self.assertFalse(repeat.unit_admission_ids)
        with self.assertRaises(ValidationError):
            self._send(repeat, target, repeat.sample_ids).action_apply()
        self._admit(repeat, repeat.sample_ids)
        self._send(repeat, target, repeat.sample_ids).action_apply()
        self.assertEqual(len(self.env['quality.unit.transfer'].search([('production_id', '=', source.production_id.id)])), 5)
        self.assertEqual(before, (source.quality_state, original.read(), original.measurement_value_ids.read(),
            alert.vataga_matrix_failure_snapshot, source.sample_ids.controlled_unit_id.ids,
            source.message_ids.ids, self._business_snapshot(source.production_id)))

    def test_qrun_only_original_keeps_repair_held_until_all_alerts_close(self):
        production = self._production(5)
        source = self._workorder(production, 'QRUN-only repair source')
        target = self._workorder(production, 'QRUN-only repair destination')
        check = self._check(production)
        if 'workorder_id' not in check._fields:
            self.skipTest('Requires Enterprise Work Order / Quality integration')
        self._identify(check, source)
        self.assertFalse(check.workorder_id)
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        production.write({'state': 'progress', 'allow_workorder_dependencies': True})
        target.blocked_by_workorder_ids = [Command.set(source.ids)]
        source.state = 'progress'
        check.do_fail()
        first = self._approve(self._alert(check))
        second = self._approve(self._alert(check))
        original = (check.quality_state, check.sample_ids.read(), check.sample_ids.measurement_value_ids.read())
        repeat = self._repair(first)
        self._pass(repeat)
        self.assertTrue(repeat._get_unit_admission_error(repeat.sample_ids))
        with self.assertRaises(ValidationError):
            self._admit(repeat, repeat.sample_ids)
        first.action_close_alert()
        self.assertTrue(second._matrix_hold_is_open())
        with self.assertRaises(ValidationError):
            self._admit(repeat, repeat.sample_ids)
        last = self._repair(second)
        self._pass(last)
        second.action_close_alert()
        self._admit(last, last.sample_ids)
        self.assertEqual(last.sample_ids.controlled_unit_id, check.sample_ids[-1].controlled_unit_id)
        self.assertEqual(original, (check.quality_state, check.sample_ids.read(), check.sample_ids.measurement_value_ids.read()))
        self.assertEqual(production.qty_produced, 0)

    def test_unapproved_and_other_decision_cannot_record_or_create_repair(self):
        check, _, _ = self._scenario()
        alert = self._alert(check)
        for method in (alert.action_open_repair_confirmation,
                       lambda: alert.action_record_repair('Роботи'), alert.action_create_repair_check):
            with self.assertRaises(UserError):
                method()
        self._approve(alert, 'accept_as_is')
        for method in (lambda: alert.action_record_repair('Роботи'), alert.action_create_repair_check):
            with self.assertRaises(UserError):
                method()

    def test_quick_pass_is_not_repair_evidence(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        failed = alert.vataga_failure_sample_ids
        check.update_measurement_visual_result(failed.id, 'yes')
        self.assertTrue(failed.x_was_reworked)
        self.assertIn('FAIL на PASS', str(check.message_ids[:1].body))
        for method in (alert.action_close_alert, alert.action_create_repair_check):
            with self.assertRaises(UserError):
                method()
        self.assertTrue(alert._matrix_hold_is_open())
        repeat = self._repair(alert)
        self.assertEqual(repeat.sample_ids.sample_result, 'pending')
        self.assertFalse(repeat.sample_ids.x_was_reworked)

    def test_repeated_fail_remains_held_and_new_attempt_preserves_failed_qc(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        first = self._repair(alert)
        self._fill(first)
        first.update_measurement_visual_result(first.sample_ids.id, 'no')
        first.do_fail()
        with self.assertRaises(UserError):
            alert.action_close_alert()
        self.assertTrue(alert._matrix_hold_is_open())
        second = self._recheck(alert)
        self.assertNotEqual(first, second)
        self.assertEqual(first.quality_state, 'fail')
        self.assertEqual(second.sample_ids.controlled_unit_id, first.sample_ids.controlled_unit_id)
        self.assertEqual(set(alert.vataga_repair_check_ids.ids), {first.id, second.id})
        self._pass(second)
        alert.action_close_alert()
        self.assertEqual(alert.vataga_repair_closed_check_id, second)

    def test_all_affected_units_need_complete_pass(self):
        check, _, _ = self._scenario()
        check.update_measurement_visual_result(check.sample_ids[0].id, 'no')
        alert = self._approve(self._alert(check))
        repeat = self._repair(alert)
        self.assertEqual(len(repeat.sample_ids), 2)
        self._fill(repeat)
        repeat.update_measurement_visual_result(repeat.sample_ids[1].id, 'no')
        with self.assertRaises(UserError):
            repeat.do_pass()
        with self.assertRaises(UserError):
            alert.action_close_alert()
        repeat.update_measurement_visual_result(repeat.sample_ids[1].id, 'yes')
        numeric = repeat.sample_ids[1].measurement_value_ids.filtered(lambda row: row.parameter_type == 'numeric')
        repeat.update_measurement_value(numeric.id, {'numeric_input': ''})
        with self.assertRaises(UserError):
            repeat.do_pass()
        repeat.update_measurement_value(numeric.id, {'numeric_input': '5'})
        repeat.equipment_selection_ids.write({'equipment_ids': [Command.clear()]})
        with self.assertRaises(UserError):
            repeat.do_pass()
        self._pass(repeat)
        alert.action_close_alert()
        self.assertFalse(alert._matrix_hold_is_open())

    def test_second_alert_keeps_same_unit_held(self):
        check, _, _ = self._scenario()
        first = self._approve(self._alert(check))
        second = self._alert(check)
        repeat = self._repair(first)
        self._pass(repeat)
        first.action_close_alert()
        self.assertFalse(first._matrix_hold_is_open())
        self.assertTrue(second._matrix_hold_is_open())
        self.assertTrue(repeat._get_unit_admission_error(repeat.sample_ids))
        with self.assertRaises(ValidationError):
            self._admit(repeat, repeat.sample_ids)

    def test_record_create_and_close_are_idempotent(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        repeat = self._repair(alert)
        before = (alert.vataga_repair_done_by_id, alert.vataga_repair_done_at, alert.message_ids.ids)
        alert.with_user(self.operator).action_record_repair('Доопрацьовано всі зазначені вироби')
        self.assertEqual(before, (alert.vataga_repair_done_by_id, alert.vataga_repair_done_at, alert.message_ids.ids))
        self.assertEqual(self._recheck(alert), repeat)
        self._pass(repeat)
        self.assertEqual(self._recheck(alert), repeat)
        alert.action_close_alert()
        before = (alert.vataga_closed_at, alert.vataga_closed_by_id, alert.message_ids.ids)
        alert.action_close_alert()
        self.assertEqual(before, (alert.vataga_closed_at, alert.vataga_closed_by_id, alert.message_ids.ids))

    def test_direct_audit_and_link_writes_and_defaults_cannot_forge_evidence(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        with self.assertRaises(AccessError):
            alert.write({'vataga_repair_done_at': '2026-01-01 00:00:00'})
        with self.assertRaises(AccessError):
            alert.write({'vataga_repair_closed_check_id': check.id})
        with self.assertRaises(AccessError):
            self._check(check.production_id, vataga_repair_alert_id=alert.id)
        with self.assertRaises(AccessError):
            self.env['quality.check'].with_context(quality_vataga_repair_check=True).create({
                'vataga_repair_alert_id': alert.id})
        new = self.env['quality.alert'].with_context(
            default_vataga_repair_done_by_id=self.manager.id,
            default_vataga_repair_done_at='2026-01-01 00:00:00',
            default_vataga_repair_closed_check_id=check.id).create({
                'name': 'Без підміни', 'team_id': self.team.id, 'check_id': check.id})
        self.assertFalse(new.vataga_repair_done_at)
        self.assertFalse(new.vataga_repair_closed_check_id)
        ordinary = self.env['quality.check'].with_context(default_vataga_repair_alert_id=alert.id).create({
            'point_id': self.point.id, 'product_id': self.product.id, 'team_id': self.team.id,
            'test_type_id': self.test_type.id, 'production_id': check.production_id.id, 'measure_on': 'product'})
        self.assertFalse(ordinary.vataga_repair_alert_id)
        repeat = self._repair(alert)
        with self.assertRaises(AccessError):
            repeat.write({'vataga_repair_alert_id': new.id})
        with self.assertRaises(UserError):
            repeat.write({'unit_admission_role': 'optional'})

    def test_completed_recheck_and_repair_audit_cannot_be_changed_or_deleted(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        repeat = self._repair(alert)
        self._pass(repeat)
        alert.action_close_alert()
        for operation in (lambda: repeat.write({'quality_state': 'none'}),
                          lambda: repeat.write({'quality_state': 'fail'}),
                          lambda: repeat.update_measurement_visual_result(repeat.sample_ids.id, 'no'),
                          repeat.unlink, alert.unlink):
            with self.assertRaises(UserError):
                operation()
        with self.assertRaises(AccessError):
            alert.write({'vataga_repair_note': 'Заміна факту'})
        self.assertTrue(alert._is_managed_closed())

    def test_completed_original_qc_cannot_be_reopened_after_repair_was_recorded(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        check.do_fail()
        self._repair(alert)
        with self.assertRaises(UserError):
            check.write({'quality_state': 'none'})
        self.assertEqual(check.quality_state, 'fail')

    def test_old_alert_needs_explicit_scope_and_repair_never_creates_qunits(self):
        check, _, _ = self._scenario('pass')
        alert = self._approve(self._alert(check))
        self.assertFalse(alert.vataga_failure_sample_ids)
        with self.assertRaises(ValidationError):
            alert.action_record_repair('Невизначені вироби')
        affected = check.sample_ids[-1:]
        alert._bind_matrix_failure_scope(affected, 'Первинний акт визначив цей фізичний виріб')
        before = self.env['quality.production.unit'].search([]).ids
        repeat = self._repair(alert)
        self.assertEqual(repeat.sample_ids.controlled_unit_id, affected.controlled_unit_id)
        self.assertEqual(self.env['quality.production.unit'].search([]).ids, before)

    def test_quality_user_can_record_but_only_manager_can_close(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        outsider = self._user('repair-outsider', ['mrp.group_mrp_user'])
        with self.assertRaises(AccessError):
            alert.with_user(outsider).action_record_repair('Немає права')
        repeat = self._repair(alert)
        self._pass(repeat)
        with self.assertRaises(AccessError):
            alert.with_user(self.operator).action_close_alert()
        self.assertTrue(alert._matrix_hold_is_open())
        alert.action_close_alert()
        self.assertEqual(alert.vataga_closed_by_id, self.manager)
        self.assertEqual(alert.vataga_repair_done_by_id, self.operator)

    def test_closed_repair_does_not_remove_controlled_mo_or_native_stock_barriers(self):
        check, _, target = self._scenario()
        alert = self._approve(self._alert(check))
        good = check.sample_ids - alert.vataga_failure_sample_ids
        self._admit(check, good)
        self._send(check, target, good).action_apply()
        repeat = self._repair(alert)
        self._pass(repeat)
        alert.action_close_alert()
        self.assertTrue(check.production_id.quality_transfer_controlled)
        before = self._business_snapshot(check.production_id)
        for operation in (check.production_id.button_mark_done, check.production_id._split_productions,
                          check.production_id._post_inventory):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(before, self._business_snapshot(check.production_id))

    def test_closure_revalidates_completed_repeat_matrix_instead_of_trusting_pass_label(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        repeat = self._repair(alert)
        self._pass(repeat)
        cell = repeat.sample_ids.measurement_value_ids[:1]
        # Emulate a damaged database, not a supported editing path. All of this
        # is rolled back with the disposable ORM test transaction.
        self.env.flush_all()
        self.env.cr.execute('DELETE FROM quality_check_measurement_value WHERE id=%s', [cell.id])
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            alert.action_close_alert()
        self.assertFalse(alert.vataga_closed_at)
        self.assertTrue(alert._matrix_hold_is_open())

    def test_company_access_is_enforced_without_changing_any_quantity(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        other = self.env['res.company'].create({'name': 'Інша ремонтна компанія'})
        self.operator.company_ids = [Command.link(other.id)]
        before = self._business_snapshot(check.production_id)
        with self.assertRaises((AccessError, ValidationError)):
            alert.with_user(self.operator).with_context(allowed_company_ids=other.ids).action_record_repair('Чужа компанія')
        self.assertFalse(alert.vataga_repair_done_at)
        self.assertEqual(before, self._business_snapshot(check.production_id))

    def test_recheck_context_cannot_replace_scope_or_prepopulate_results(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        alert.action_record_repair('Роботи')
        action = alert.with_context(default_quality_state='pass', default_production_id=False,
                                    default_sample_ids=[Command.clear()]).action_create_repair_check()
        repeat = self.env['quality.check'].browse(action['res_id'])
        self.assertEqual(repeat.quality_state, 'none')
        self.assertEqual(repeat.sample_ids.sample_result, 'pending')
        self.assertEqual(repeat.production_id, check.production_id)
        self.assertEqual(repeat.sample_ids.controlled_unit_id, alert.vataga_failure_sample_ids.controlled_unit_id)

    def test_failed_initialization_rolls_back_entire_repeat_check(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        alert.action_record_repair('Роботи')
        check.point_id.control_parameter_line_ids.unlink()
        check.point_id.visual_sample_control_required = False
        before = self.env['quality.check'].search([]).ids
        with self.assertRaises(ValidationError):
            alert.action_create_repair_check()
        self.assertEqual(before, self.env['quality.check'].search([]).ids)
        self.assertFalse(alert.vataga_repair_check_ids)

    def test_downstream_repair_reuses_original_processed_execution_for_next_edge(self):
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Requires the Enterprise Quality / Work Order integration.')
        source, first, second = self._scenario('pass')
        third = self._workorder(source.production_id, 'Третя операція')
        third.blocked_by_workorder_ids = [Command.set(second.ids)]
        self._admit(source, source.sample_ids)
        self._send(source, second, source.sample_ids).action_apply()
        records = self.env['quality.unit.transfer'].search([('check_id', '=', source.id)])
        self.env['quality.unit.transfer.wizard'].create({
            'workorder_id': second.id, 'mode': 'receive',
            'line_ids': [Command.create({'transfer_id': record.id, 'selected': True}) for record in records],
        }).action_apply()
        self._execution_wizard(second, records).action_apply()
        executions = second.quality_execution_ids
        self._execution_wizard(second, executions, 'finish').action_apply()
        qc = executions._create_control(second, self.point)
        self._fill(qc)
        failed = qc.sample_ids[-1:]
        qc.update_measurement_visual_result(failed.id, 'no')
        alert = self._approve(self._alert(qc))
        repeat = self._repair(alert)
        self._pass(repeat)
        alert.action_close_alert()
        self.assertFalse(repeat.quality_execution_ids)  # no synthetic processing
        self._admit(repeat, repeat.sample_ids)
        self._send(repeat, third, repeat.sample_ids).action_apply()
        old_edge = records.filtered(lambda row: row.controlled_unit_id == failed.controlled_unit_id)
        self.assertEqual(old_edge.state, 'advanced')
        self.assertEqual(second.quality_execution_ids, executions)
        self.assertEqual(source.production_id.qty_produced, 0)

    def test_repair_views_compile_with_existing_notebooks_and_readonly_evidence(self):
        alert_arch = etree.fromstring(self.env['quality.alert'].get_view(
            view_id=self.env.ref('quality_control.quality_alert_view_form').id, view_type='form')['arch'])
        self.assertEqual(len(alert_arch.xpath("//page[@name='vataga_repair']")), 1)
        self.assertFalse(alert_arch.xpath("//notebook[@name='technical_board_notebook']/page[@name='vataga_repair']"))
        self.assertEqual(len(alert_arch.xpath("//notebook[@name='technical_board_notebook']")), 1)
        for name in ('vataga_repair_note', 'vataga_repair_check_ids', 'vataga_repair_closed_check_id'):
            self.assertEqual(alert_arch.xpath("//field[@name='%s']/@readonly" % name), ['1'])
        check_arch = self.env['quality.check'].get_view(
            view_id=self.env.ref('quality_control.quality_check_view_form').id, view_type='form')['arch']
        self.assertIn('vataga_repair_alert_id', check_arch)

    def test_repair_wizard_action_has_views_for_direct_do_action(self):
        check, _, _ = self._scenario()
        alert = self._approve(self._alert(check))
        action = alert.action_open_repair_confirmation()
        self.assertEqual(action['views'], [(self.env.ref('quality_vataga.quality_alert_repair_wizard_form').id, 'form')])
        self.assertEqual(action['context'], {'default_alert_id': alert.id})
