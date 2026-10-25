from unittest.mock import patch

from lxml import etree
from psycopg2 import IntegrityError, errors

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_execution as execution_tests


@tagged('post_install', '-at_install')
class TestQualityUnitRelease(QualityCheckInProcessCase):
    def setUp(self):
        super().setUp()
        self.env.user.groups_id = [Command.link(self.env.ref('quality_vataga.group_quality_unit_release').id)]

    _workorder = execution_tests.TestQualityUnitExecution._workorder
    _identify = execution_tests.TestQualityUnitExecution._identify
    _decision = execution_tests.TestQualityUnitExecution._decision
    _admissions = execution_tests.TestQualityUnitExecution._admissions
    _route = execution_tests.TestQualityUnitExecution._route
    _send = execution_tests.TestQualityUnitExecution._send
    _receive = execution_tests.TestQualityUnitExecution._receive
    _transfers = execution_tests.TestQualityUnitExecution._transfers
    _received = execution_tests.TestQualityUnitExecution._received
    _execution_wizard = execution_tests.TestQualityUnitExecution._execution_wizard
    _user = execution_tests.TestQualityUnitExecution._user

    def _ready(self, quantity=5, fail_last=True):
        check = execution_tests.TestQualityUnitExecution._ready(self, quantity, fail_last=False)
        if fail_last:
            check.update_measurement_visual_result(check.sample_ids.sorted('sample_number')[2].id, 'no')
        return check

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity, uom)
        production.product_qty = quantity
        return production

    def _scenario(self):
        check, source, last, transfers = self._received()
        self._execution_wizard(last, transfers).action_apply()
        units = check.sample_ids.sorted('sample_number').controlled_unit_id
        completed = last.quality_execution_ids.filtered(lambda row: row.controlled_unit_id in units[1:2] | units[3:4])
        self._execution_wizard(last, completed, 'finish').action_apply()
        ready_execution = completed.filtered(lambda row: row.controlled_unit_id == units[1])
        target = ready_execution._create_control(last, self.point)
        self._fill(target)
        self._decision(target).action_apply()
        return check, source, last, units, target

    def _release(self, production, units, mode='authorize', reason=''):
        field = 'unit_id' if mode == 'authorize' else 'permission_id'
        return self.env['quality.unit.release.wizard'].create({
            'production_id': production.id, 'mode': mode, 'reason': reason,
            'line_ids': [Command.create({field: item.id, 'selected': True}) for item in units],
        })

    def _lines(self, unit):
        return self.env['quality.unit.release.line'].search([('unit_id', '=', unit.id)])

    def test_only_one_of_five_ready_and_authorized_without_stock_or_mrp_changes(self):
        check, source, last, units, target = self._scenario()
        self.assertEqual(units.mapped('quality_release_state'), ['not_ready', 'ready', 'blocked', 'not_ready', 'not_ready'])
        self.assertEqual(units.mapped('quality_release_quality_result'), ['pending', 'PASS', 'FAIL', 'pending', 'pending'])
        production = check.production_id
        self.assertEqual((production.quality_release_total, production.quality_release_ready, production.quality_release_authorized, production.quality_release_not_ready, production.quality_release_blocked), (5, 1, 0, 3, 1))
        before = (production.product_qty, production.qty_producing, production.qty_produced, production.state,
                  production.workorder_ids.mapped('state'), production.move_raw_ids.read(), production.move_finished_ids.read(),
                  self.env['stock.quant'].search([('product_id', '=', self.product.id)]).read(),
                  self.env['stock.move'].search([('product_id', '=', self.product.id)]).ids,
                  units.ids, check.sample_ids.ids, check.in_process_sample_quantity)
        result = self._release(production, units[1]).action_apply()
        line = self._lines(units[1])
        self.assertTrue(line.is_effective)
        self.assertEqual(line.current_state, 'authorized')
        self.assertEqual(line.check_id, target)
        self.assertEqual(line.last_workorder_id, last)
        self.assertEqual(line.last_run_id, target.operation_run_id)
        self.assertEqual(line.authorized_by_id, self.env.user)
        self.assertTrue(line.authorized_at)
        self.assertEqual((production.quality_release_authorized, production.quality_release_produced), (1, 0))
        self.assertEqual(result['infos']['quality_vataga_production_id'], production.id)
        self.assertEqual(before, (production.product_qty, production.qty_producing, production.qty_produced, production.state,
                                 production.workorder_ids.mapped('state'), production.move_raw_ids.read(), production.move_finished_ids.read(),
                                 self.env['stock.quant'].search([('product_id', '=', self.product.id)]).read(),
                                 self.env['stock.move'].search([('product_id', '=', self.product.id)]).ids,
                                 units.ids, check.sample_ids.ids, check.in_process_sample_quantity))

    def test_mixed_selection_is_atomic_and_ui_reasons_are_not_authorization(self):
        check, source, last, units, target = self._scenario()
        for unit in units - units[1]:
            with self.assertRaises(ValidationError):
                self._release(check.production_id, unit | units[1]).action_apply()
        self.assertFalse(self.env['quality.unit.release.batch'].search([('production_id', '=', check.production_id.id)]))
        self.assertFalse(self._lines(units[1]))

    def test_repeat_same_request_is_idempotent_new_request_is_blocked(self):
        check, source, last, units, target = self._scenario()
        wizard = self._release(check.production_id, units[1])
        wizard.action_apply()
        messages = check.production_id.message_ids.ids
        wizard.action_apply()
        self.assertEqual(len(self._lines(units[1])), 1)
        self.assertEqual(messages, check.production_id.message_ids.ids)
        with self.assertRaises(ValidationError):
            self._release(check.production_id, units[1]).action_apply()

    def test_direct_create_write_unlink_and_context_defaults_are_protected(self):
        check, source, last, units, target = self._scenario()
        for name in ('quality.unit.release.batch', 'quality.unit.release.line', 'quality.unit.release.event'):
            with self.assertRaises(UserError):
                self.env[name].with_context(quality_vataga_release_authorization=True).create({})
        wizard = self._release(check.production_id, units[1])
        wizard.with_context(default_state='released', default_unit_id=units[2].id).action_apply()
        line = self._lines(units[1])
        self.assertEqual(line.state, 'confirmed')
        for record in (line, line.batch_id, line.event_ids):
            with self.assertRaises(UserError):
                record.write({'unit_id': units[2].id})
            with self.assertRaises(UserError):
                record.unlink()
        with self.assertRaises(UserError):
            check.production_id.write({'quality_release_authorized': 5})
        with self.assertRaises(UserError):
            line.write({'state': 'released'})

    def test_separate_right_required_even_for_quality_or_mrp_user(self):
        check, source, last, units, target = self._scenario()
        for groups in (['mrp.group_mrp_user'], ['mrp.group_mrp_user', 'quality.group_quality_manager']):
            user = self._user('release_denied_' + str(len(groups)), groups)
            with self.assertRaises(AccessError):
                check.production_id.with_user(user).action_open_unit_release_wizard()
        approver = self._user('release_authorized', ['quality_vataga.group_quality_unit_release'])
        self.env['quality.unit.release.batch'].with_user(approver)._authorize(check.production_id.with_user(approver), units[1].with_user(approver), 'authorized-user')
        self.assertEqual(self._lines(units[1]).authorized_by_id, approver)

    def test_foreign_mo_or_company_cannot_be_selected(self):
        check, source, last, units, target = self._scenario()
        foreign = self._production()
        with self.assertRaises(ValidationError):
            self._release(foreign, units[1]).action_apply()
        user = self._user('release_company', ['quality_vataga.group_quality_unit_release'])
        company = self.env['res.company'].create({'name': 'Інша компанія дозволів'})
        user.company_ids = [Command.link(company.id)]
        with self.assertRaises((AccessError, ValidationError)):
            check.production_id.with_user(user).with_context(allowed_company_ids=company.ids).action_open_unit_release_wizard()

    def test_fail_in_another_row_does_not_block_good_unit_or_require_global_pass(self):
        check, source, last, units, target = self._scenario()
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual(check.sample_ids[2].sample_result, 'fail')
        self.assertEqual(target.quality_state, 'none')
        self.assertEqual(units[1].quality_release_state, 'ready')
        self._release(check.production_id, units[1]).action_apply()
        self.assertTrue(self._lines(units[1]).is_effective)

    def test_revocation_lapses_permission_and_keeps_snapshot_audit(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        line = self._lines(units[1])
        snapshot = line.evidence_snapshot
        self._decision(target, mode='revoke', reason='Новий контроль').action_apply()
        self.assertFalse(line.is_effective)
        self.assertEqual(line.current_state, 'lapsed')
        self.assertEqual(units[1].quality_release_state, 'lapsed')
        self.assertEqual(line.evidence_snapshot, snapshot)
        self.assertEqual(line.state, 'confirmed')
        self.assertEqual(line.event_ids.mapped('action'), ['confirmed'])

    def test_new_required_check_invalidates_existing_permission_without_erasing_it(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        line = self._lines(units[1])
        new_check = self._check(check.production_id, workorder_id=last.id)
        self.assertEqual(new_check.unit_admission_role, 'required')
        self.assertEqual(line.current_state, 'lapsed')
        self.assertFalse(line.is_effective)
        self.assertTrue(line.exists())
        self.assertEqual(check.production_id.quality_release_authorized, 0)

    def test_unrelated_optional_check_does_not_suspend_permission(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        self._check(check.production_id, workorder_id=last.id, unit_admission_role='optional')
        self.assertTrue(self._lines(units[1]).is_effective)

    def test_cancellation_requires_reason_and_new_confirmation_is_explicit(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        line = self._lines(units[1])
        with self.assertRaises(ValidationError):
            self._release(check.production_id, line, 'cancel').action_apply()
        wizard = self._release(check.production_id, line, 'cancel', 'Планування випуску змінено')
        wizard.action_apply()
        wizard.action_apply()
        self.assertEqual(line.current_state, 'cancelled')
        self.assertEqual(line.event_ids.mapped('action'), ['confirmed', 'cancelled'])
        self.assertTrue(line.cancelled_at)
        self.assertEqual(units[1].quality_release_state, 'ready')
        self._release(check.production_id, units[1]).action_apply()
        self.assertEqual(len(self._lines(units[1])), 2)

    def test_admitted_measurements_and_confirmed_evidence_remain_immutable(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        with self.assertRaises(UserError):
            target.update_measurement_visual_result(target.sample_ids.id, 'no')
        with self.assertRaises(UserError):
            self._lines(units[1]).write({'check_id': check.id})
        with self.assertRaises(UserError):
            last.quality_execution_ids.filtered(lambda row: row.controlled_unit_id == units[1]).write({'result_reference': 'Підміна'})

    def test_ambiguous_alert_blocks_and_does_not_guess_from_current_failures(self):
        check, source, last, units, target = self._scenario()
        self.env['quality.alert'].create({'name': 'Неоднозначна невідповідність', 'check_id': check.id, 'team_id': self.team.id})
        self.assertEqual(units[1]._get_release_readiness()['code'], 'alert_ambiguous')
        with self.assertRaises(ValidationError):
            self._release(check.production_id, units[1]).action_apply()

    def test_single_unit_alert_requires_board_action_and_does_not_bypass_quality(self):
        check, source, last, units, target = self._scenario()
        self.env.user.groups_id = [Command.link(self.env.ref('quality_vataga.group_quality_board_approval').id)]
        alert = self.env['quality.alert'].create({'name': 'Поіменна невідповідність', 'check_id': target.id, 'team_id': self.team.id})
        self.assertNotEqual(units[1]._get_release_readiness()['state'], 'ready')
        alert.write({'decision_type': 'accept_as_is', 'approval_user_id': self.env.uid})
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        self.assertEqual(alert.quality_release_scope_unit_id, units[1])
        self.assertEqual(units[1].quality_release_state, 'ready')
        with self.assertRaises(UserError):
            alert.write({'quality_release_scope_unit_id': units[2].id})

    def test_configured_final_control_blocks_until_named_evidence_is_supported(self):
        check, source, last, units, target = self._scenario()
        self.point.copy({'x_control_stage': 'final'})
        self.assertEqual(units[1]._get_release_readiness()['code'], 'final_required')
        self.assertEqual(units[1].quality_release_state, 'blocked')

    def test_serial_tracking_requires_real_serial_identity_without_creating_lots(self):
        check, source, last, units, target = self._scenario()
        self.product.tracking = 'serial'
        before = self.env['stock.lot'].search([('product_id', '=', self.product.id)]).ids
        self.assertEqual(units[1]._get_release_readiness()['code'], 'lot_required')
        self.assertEqual(before, self.env['stock.lot'].search([('product_id', '=', self.product.id)]).ids)

    def test_three_sequential_operations_keep_per_unit_evidence(self):
        check, source, middle = self._route()
        last = self._workorder(check.production_id, 'Третя операція')
        check.production_id.allow_workorder_dependencies = True
        middle.blocked_by_workorder_ids = source
        last.blocked_by_workorder_ids = middle
        self._send(check, middle).action_apply()
        receipts = self._transfers(check)
        self._receive(middle, receipts).action_apply()
        self._execution_wizard(middle, receipts).action_apply()
        self._execution_wizard(middle, middle.quality_execution_ids, 'finish').action_apply()
        middle_qc = middle.quality_execution_ids._create_control(middle, self.point)
        self._fill(middle_qc)
        self._decision(middle_qc).action_apply()
        self._send(middle_qc, last).action_apply()
        next_receipts = self._transfers(middle_qc)
        self._receive(last, next_receipts).action_apply()
        self._execution_wizard(last, next_receipts).action_apply()
        execution = last.quality_execution_ids[:1]
        self._execution_wizard(last, execution, 'finish').action_apply()
        target = execution._create_control(last, self.point)
        self._fill(target)
        self._decision(target).action_apply()
        self._release(check.production_id, execution.controlled_unit_id).action_apply()
        line = self._lines(execution.controlled_unit_id)
        self.assertTrue(line.is_effective)
        self.assertEqual(len(line.execution_ids), 2)
        self.assertEqual(len(line.transfer_ids), 2)
        self.assertEqual(len(line.admission_ids), 3)
        self.assertEqual(last.blocked_by_workorder_ids, middle)
        self.assertEqual(middle.blocked_by_workorder_ids, source)

    def test_fork_cycle_and_hidden_dependencies_do_not_define_a_last_operation(self):
        check, source, last = self._route()
        third = self._workorder(check.production_id, 'Паралельна гілка')
        check.production_id.allow_workorder_dependencies = True
        last.blocked_by_workorder_ids = source
        third.blocked_by_workorder_ids = source
        self.assertEqual(check.sample_ids[0].controlled_unit_id._get_release_readiness()['code'], 'uncontrolled')
        # A controlled fixture is needed to test the readiness graph, without
        # authorizing its unsupported fork through the transfer factory.
        check.production_id._enable_quality_transfer_control()
        self.assertEqual(check.sample_ids[0].controlled_unit_id._get_release_readiness()['code'], 'route_ambiguous')

    def test_stopped_attempt_and_restart_require_a_fresh_completed_execution(self):
        check, source, last, transfers = self._received()
        unit = check.sample_ids.sorted('sample_number').controlled_unit_id[0]
        transfer = transfers.filtered(lambda row: row.controlled_unit_id == unit)
        self._execution_wizard(last, transfer).action_apply()
        stopped = last.quality_execution_ids
        self._execution_wizard(last, stopped, 'stop', 'Контрольована зупинка').action_apply()
        self.assertEqual(unit.quality_release_state, 'not_ready')
        self._execution_wizard(last, transfer).action_apply()
        current = last.quality_execution_ids.filtered(lambda row: row.state == 'progress')
        self._execution_wizard(last, current, 'finish').action_apply()
        qc = current._create_control(last, self.point)
        self._fill(qc)
        self._decision(qc).action_apply()
        self.assertEqual(unit.quality_release_state, 'ready')
        self.assertEqual(stopped.state, 'stopped')

    def test_mrp_and_inventory_boundaries_stay_closed_after_authorization(self):
        check, source, last, units, target = self._scenario()
        self._release(check.production_id, units[1]).action_apply()
        for method in (source.button_finish, last.button_start, last.button_finish, last.button_done,
                       last.action_mark_as_done, last._update_finished_move, check.production_id.pre_button_mark_done,
                       check.production_id.button_mark_done, check.production_id._post_inventory, check.production_id._split_productions):
            with self.assertRaises(UserError):
                method()
        with self.assertRaises(UserError):
            check.production_id.with_context(skip_quality_check=True).write({'qty_producing': 1})
        ordinary = self._production()
        workorder = self._workorder(ordinary)
        workorder.button_start()
        workorder.button_finish()
        self.assertEqual(workorder.qty_produced, 5)

    def test_unique_index_and_serialization_failures_prevent_double_authorization(self):
        check, source, last, units, target = self._scenario()
        wizard = self._release(check.production_id, units[1])
        with patch.object(type(check.production_id), '_lock_quality_transfer_scope', side_effect=errors.SerializationFailure('concurrent release')):
            with self.assertRaises(errors.SerializationFailure):
                wizard.action_apply()
        self.assertFalse(self._lines(units[1]))
        wizard.action_apply()
        line = self._lines(units[1])
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.cr.execute('''
                INSERT INTO quality_unit_release_line
                    (batch_id, unit_id, production_id, company_id, product_id, last_workorder_id,
                     last_run_id, check_id, admission_id, state, evidence_snapshot)
                SELECT batch_id, unit_id, production_id, company_id, product_id, last_workorder_id,
                       last_run_id, check_id, admission_id, state, evidence_snapshot
                FROM quality_unit_release_line WHERE id = %s
            ''', [line.id])

    def test_modal_contract_readonly_history_and_separate_control_quantity(self):
        check, source, last, units, target = self._scenario()
        action = check.production_id.action_open_unit_release_wizard()
        self.assertEqual(action['views'], [(self.env.ref('quality_vataga.quality_unit_release_wizard_form').id, 'form')])
        self.assertEqual(target.in_process_sample_quantity, 1)
        self.assertEqual(target.production_id.product_qty, 5)
        self.assertEqual(target.production_id.qty_producing, 5)
        self.assertIn('5', target.operation_product_quantity_label)
        self.assertIn('5', target.quality_release_mo_quantity_label)
        doc = etree.fromstring(check.production_id.get_view(view_type='form')['arch'])
        self.assertEqual(len(doc.xpath("//page[@name='quality_unit_release']")), 1)
        self.assertEqual(len(doc.xpath("//widget[@name='quality_unit_release_buttons']")), 1)
