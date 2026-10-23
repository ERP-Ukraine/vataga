from unittest.mock import patch

from lxml import etree
from psycopg2 import IntegrityError, errors

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_transfer as transfer_tests


@tagged('post_install', '-at_install')
class TestQualityUnitExecution(QualityCheckInProcessCase):
    _workorder = transfer_tests.TestQualityUnitTransfer._workorder
    _identify = transfer_tests.TestQualityUnitTransfer._identify
    _ready = transfer_tests.TestQualityUnitTransfer._ready
    _decision = transfer_tests.TestQualityUnitTransfer._decision
    _admissions = transfer_tests.TestQualityUnitTransfer._admissions
    _route = transfer_tests.TestQualityUnitTransfer._route
    _send = transfer_tests.TestQualityUnitTransfer._send
    _receive = transfer_tests.TestQualityUnitTransfer._receive
    _transfers = transfer_tests.TestQualityUnitTransfer._transfers

    def _user(self, login, groups):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': login + '@example.test', 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref('base.group_user').id] + [self.env.ref(group).id for group in groups])],
        })

    def _received(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        records = self._transfers(check)
        self._receive(destination, records).action_apply()
        return check, source, destination, records

    def _execution_wizard(self, workorder, records, mode='start', reference='Результат операції: виконано'):
        field = 'transfer_id' if mode == 'start' else 'execution_id'
        return self.env['quality.unit.execution.wizard'].create({
            'workorder_id': workorder.id, 'mode': mode, 'reference': reference,
            'line_ids': [Command.create({field: row.id, 'selected': True}) for row in records],
        })

    def test_four_received_units_start_real_workorder_and_one_native_timer(self):
        check, source, destination, records = self._received()
        before = (check.production_id.product_qty, check.production_id.qty_producing, check.production_id.qty_produced,
                  source.qty_produced, destination.qty_produced, check.sample_ids.ids,
                  check.production_id.move_raw_ids.read(), check.production_id.move_finished_ids.read())
        wizard = self._execution_wizard(destination, records)
        result = wizard.action_apply()
        self.assertEqual(destination.state, 'progress')
        self.assertEqual(source.state, 'progress')
        self.assertEqual(destination.blocked_by_workorder_ids, source)
        self.assertEqual(len(destination.quality_execution_ids), 4)
        self.assertEqual((destination.quality_execution_progress, destination.quality_execution_processed, destination.quality_execution_held), (4, 0, 1))
        self.assertEqual(len(destination.quality_execution_ids.batch_id.timer_ids), 1)
        self.assertFalse(destination.quality_execution_ids.batch_id.timer_ids.date_end)
        self.assertEqual(result['infos']['quality_vataga_workorder_id'], destination.id)
        wizard.action_apply()
        self.assertEqual(len(destination.quality_execution_ids), 4)
        self.assertEqual(before, (check.production_id.product_qty, check.production_id.qty_producing, check.production_id.qty_produced,
                                 source.qty_produced, destination.qty_produced, check.sample_ids.ids,
                                 check.production_id.move_raw_ids.read(), check.production_id.move_finished_ids.read()))

    def test_individual_finish_then_all_four_without_double_count(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        executions = destination.quality_execution_ids
        first = self._execution_wizard(destination, executions[:1], 'finish')
        first.action_apply()
        first.action_apply()
        self.assertEqual((destination.quality_execution_processed, destination.quality_execution_progress), (1, 3))
        self.assertFalse(executions.batch_id.timer_ids.date_end)
        self._execution_wizard(destination, executions[1:], 'finish').action_apply()
        self.assertEqual((destination.quality_execution_processed, destination.quality_execution_progress), (4, 0))
        self.assertTrue(all(executions.mapped('ended_at')))
        self.assertEqual(executions.ended_by_id, self.env.user)
        self.assertTrue(all(executions.batch_id.timer_ids.mapped('date_end')))
        self.assertEqual(destination.qty_produced, 0)
        self.assertEqual(destination.state, 'progress')
        self.assertEqual(check.in_process_sample_quantity, 5)

    def test_sent_returned_and_wrong_workorder_cannot_start(self):
        check, source, destination = self._route()
        self._send(check, destination).action_apply()
        records = self._transfers(check)
        with self.assertRaises(ValidationError):
            self._execution_wizard(destination, records).action_apply()
        self._receive(destination, records).action_apply()
        for wrong in (source, self._workorder(self._production(), 'Чужий MO')):
            with self.assertRaises(ValidationError):
                self._execution_wizard(wrong, records).action_apply()
        self._receive(destination, records, 'return', 'До початку обробки').action_apply()
        with self.assertRaises(ValidationError):
            self._execution_wizard(destination, records).action_apply()

    def test_no_receipt_or_approval_for_fail_pending_and_unadmitted(self):
        check, source, destination = self._route(admitted=False)
        for sample in check.sample_ids:
            with self.assertRaises(ValidationError):
                self._send(check, destination, sample).action_apply()
        with self.assertRaises(ValidationError):
            self._execution_wizard(destination, self.env['quality.unit.transfer']).action_apply()
        self.assertFalse(destination.quality_execution_ids)

    def test_current_evidence_is_rechecked_atomically(self):
        check, source, destination, records = self._received()
        cls = type(self.env['quality.unit.admission'])
        with patch.object(cls, '_get_effectiveness_error', return_value='Вимоги ЗВТ більше не виконано'):
            with self.assertRaises(ValidationError):
                self._execution_wizard(destination, records).action_apply()
        self.assertFalse(destination.quality_execution_ids)

    def test_duplicate_start_and_finish_require_real_selection(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        with self.assertRaises(ValidationError):
            self._execution_wizard(destination, records).action_apply()
        with self.assertRaises(ValidationError):
            self._execution_wizard(destination, destination.quality_execution_ids, 'finish', '').action_apply()
        self.assertEqual(destination.quality_execution_processed, 0)

    def test_direct_orm_and_forged_context_are_blocked(self):
        check, source, destination, records = self._received()
        model = self.env['quality.unit.execution']
        with self.assertRaises(UserError):
            model.with_context(quality_vataga_unit_execution=True).create({'transfer_id': records[0].id})
        self._execution_wizard(destination, records).action_apply()
        for record in (destination.quality_execution_ids, destination.quality_execution_ids.batch_id):
            with self.assertRaises(UserError):
                record.write({'workorder_id': source.id})
            with self.assertRaises(UserError):
                record.unlink()
        with self.assertRaises(UserError):
            destination.write({'quality_execution_processed': 5})
        with self.assertRaises(UserError):
            destination.quality_execution_ids.batch_id.timer_ids.unlink()

    def test_context_defaults_cannot_replace_selected_identity_or_actor(self):
        check, source, destination, records = self._received()
        held = check.sample_ids.filtered(lambda row: row.sample_result == 'fail').controlled_unit_id
        executions = self.env['quality.unit.execution'].with_context(
            default_controlled_unit_id=held.id, default_state='processed',
            default_operator_id=999999,
        )._start(destination, records, 'explicit-identities')
        self.assertEqual(set(executions.controlled_unit_id.ids), set(records.controlled_unit_id.ids))
        self.assertEqual(executions.operator_id, self.env.user)
        self.assertEqual(set(executions.mapped('state')), {'progress'})

    def test_active_timer_cannot_be_stopped_without_unit_stop(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        with self.assertRaises(UserError):
            destination.button_pending()
        self.assertFalse(destination.quality_execution_ids.batch_id.timer_ids.date_end)

    def test_return_requires_stop_and_keeps_history(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        with self.assertRaises(UserError):
            self._receive(destination, records[:1], 'return', 'Повернути').action_apply()
        execution = destination.quality_execution_ids.filtered(lambda row: row.transfer_id == records[0])
        self._execution_wizard(destination, execution, 'stop', 'Контрольована зупинка').action_apply()
        self._receive(destination, records[:1], 'return', 'Після зупинки').action_apply()
        self.assertEqual(execution.state, 'stopped')
        self.assertTrue(execution.ended_at)
        self.assertEqual(records[0].state, 'returned')

    def test_processed_return_and_rewriting_result_are_forbidden(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        self._execution_wizard(destination, destination.quality_execution_ids, 'finish').action_apply()
        with self.assertRaises(UserError):
            self._receive(destination, records[:1], 'return', 'Після завершення').action_apply()
        with self.assertRaises(UserError):
            destination.quality_execution_ids.write({'state': 'progress'})
        with self.assertRaises(UserError):
            check.update_measurement_visual_result(check.sample_ids[0].id, 'no')

    def test_standard_completion_and_inventory_stay_blocked(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        for call in (destination.button_start, destination.button_finish, destination.button_done,
                     destination.action_mark_as_done, destination._update_finished_move,
                     check.production_id.button_mark_done, check.production_id._post_inventory,
                     check.production_id._split_productions):
            with self.subTest(method=call.__name__), self.assertRaises(UserError):
                call()
        with self.assertRaises(UserError):
            destination.write({'state': 'ready'})

    def test_operator_rights_and_ownership(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        user = self._user('execution_readonly', [])
        with self.assertRaises(AccessError):
            destination.with_user(user).action_open_quality_execution_start()
        operator = self._user('execution_other', ['mrp.group_mrp_user'])
        with self.assertRaises(AccessError):
            destination.quality_execution_ids.with_user(operator)._finish(destination.with_user(operator), 'finish', 'Чужа робота')

    def test_mrp_operator_can_process_without_inspector_write_access(self):
        check, source, destination, records = self._received()
        operator = self._user('execution_mrp_only', ['mrp.group_mrp_user'])
        model = self.env['quality.unit.execution'].with_user(operator)
        executions = model._start(destination.with_user(operator), records.with_user(operator), 'mrp-operator')
        executions._finish(destination.with_user(operator), 'finish', 'Оператор підтвердив результат')
        self.assertEqual(destination.quality_execution_processed, 4)
        self.assertEqual(executions.operator_id, operator)

    def test_other_company_cannot_use_execution_or_wizard(self):
        check, source, destination, records = self._received()
        operator = self._user('execution_company', ['mrp.group_mrp_user'])
        other = self.env['res.company'].create({'name': 'Інша компанія виконання'})
        operator.company_ids = [Command.link(other.id)]
        restricted = destination.with_user(operator).with_context(allowed_company_ids=other.ids)
        with self.assertRaises(AccessError):
            restricted.action_open_quality_execution_start()
        self.assertFalse(destination.quality_execution_ids)

    def test_alert_board_and_stock_remain_unchanged_after_execution(self):
        check, source, destination, records = self._received()
        alert = self.env['quality.alert'].create({'name': 'Виконання та брак', 'check_id': check.id, 'team_id': self.team.id})
        before = (alert.read(), check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked'),
                  self.env['stock.move'].search([('production_id', '=', check.production_id.id)]).ids,
                  self.env['stock.quant'].search([('product_id', '=', self.product.id)]).read())
        self._execution_wizard(destination, records).action_apply()
        self._execution_wizard(destination, destination.quality_execution_ids, 'finish').action_apply()
        self.assertEqual(before, (alert.read(), check.sample_ids.measurement_value_ids.read(), check.sample_ids.mapped('x_was_reworked'),
                                 self.env['stock.move'].search([('production_id', '=', check.production_id.id)]).ids,
                                 self.env['stock.quant'].search([('product_id', '=', self.product.id)]).read()))

    def test_fifth_needs_rework_explicit_admission_receipt_then_explicit_start(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        fifth = check.sample_ids.filtered(lambda row: row.sample_result == 'fail')
        check.update_measurement_visual_result(fifth.id, 'yes')
        self.assertEqual(fifth.sample_result, 'pass')
        self.assertEqual(len(destination.quality_execution_ids), 4)
        with self.assertRaises(ValidationError):
            self._send(check, destination, fifth).action_apply()
        self._decision(check, fifth).action_apply()
        self._send(check, destination, fifth).action_apply()
        new_receipt = self._transfers(check).filtered(lambda row: row.controlled_unit_id == fifth.controlled_unit_id)
        self.assertEqual(new_receipt.state, 'sent')
        self._receive(destination, new_receipt).action_apply()
        self.assertEqual(len(destination.quality_execution_ids), 4)

    def test_hidden_execution_locks_and_unique_indexes(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        # A UI record rule cannot turn an existing active execution into an
        # available physical unit: the protective existence query is global.
        with patch.object(type(self.env['quality.unit.execution']), '_search', return_value=[]):
            error = records[0]._get_execution_error(destination)
        self.assertIn('вже оброблено', error)
        self.env.cr.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'quality_unit_transfer_active_unique'")
        self.assertIn('(controlled_unit_id)', self.env.cr.fetchone()[0])

    def test_concurrent_execution_safety_net_and_transaction_retry(self):
        check, source, destination, records = self._received()
        wizard = self._execution_wizard(destination, records)
        lock = type(check.production_id)
        with patch.object(lock, '_lock_quality_transfer_scope', side_effect=errors.SerializationFailure('concurrent start')):
            with self.assertRaises(errors.SerializationFailure):
                wizard.action_apply()
        self.assertFalse(destination.quality_execution_ids)
        wizard.action_apply()
        execution = destination.quality_execution_ids[:1]
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.cr.execute('''
                INSERT INTO quality_unit_execution
                    (batch_id, transfer_id, workorder_id, production_id, company_id,
                     controlled_unit_id, operator_id, started_at, state)
                SELECT batch_id, transfer_id, workorder_id, production_id, company_id,
                       controlled_unit_id, operator_id, started_at, state
                FROM quality_unit_execution WHERE id = %s
            ''', [execution.id])
        finish = self._execution_wizard(destination, destination.quality_execution_ids, 'finish')
        with patch.object(lock, '_lock_quality_transfer_scope', side_effect=errors.SerializationFailure('concurrent finish')):
            with self.assertRaises(errors.SerializationFailure):
                finish.action_apply()
        self.assertEqual(destination.quality_execution_processed, 0)
        finish.action_apply()
        finish.action_apply()
        self.assertEqual(destination.quality_execution_processed, 4)

    def test_new_control_reuses_units_and_freezes_only_processed_subset(self):
        check, source, destination, records = self._received()
        self._execution_wizard(destination, records).action_apply()
        executions = destination.quality_execution_ids
        self._execution_wizard(destination, executions, 'finish').action_apply()
        target = executions._create_control(destination, self.point)
        self.assertEqual(target.in_process_sample_quantity, 4)
        self.assertEqual(set(target.sample_ids.controlled_unit_id.ids), set(executions.controlled_unit_id.ids))
        self.assertEqual(target.operation_run_id.workorder_id, destination)
        self.assertFalse(target.x_admitted_qty)
        self.assertEqual(target.x_pending_qty, 4)
        for sample in target.sample_ids:
            self.assertEqual(sample.measurement_value_ids.column_id, target.measurement_column_ids)
        self.assertEqual(check.in_process_sample_quantity, 5)
        self.assertEqual(check.production_id.qty_producing, 5)
        self.assertEqual(self.env['quality.production.unit'].search_count([('production_id', '=', check.production_id.id)]), 5)
        with self.assertRaises(UserError):
            executions._create_control(destination, self.point)

    def test_next_sequential_operation_keeps_one_active_transfer(self):
        check = self._ready()
        source = check.operation_run_id.workorder_id
        middle = self._workorder(check.production_id, 'Друга')
        last = self._workorder(check.production_id, 'Третя')
        check.production_id.write({'state': 'progress', 'allow_workorder_dependencies': True})
        middle.blocked_by_workorder_ids = source
        last.blocked_by_workorder_ids = middle
        source.state = 'progress'
        self._decision(check).action_apply()
        self._send(check, middle).action_apply()
        first_receipts = self._transfers(check)
        self._receive(middle, first_receipts).action_apply()
        self._execution_wizard(middle, first_receipts).action_apply()
        self._execution_wizard(middle, middle.quality_execution_ids, 'finish').action_apply()
        target = middle.quality_execution_ids._create_control(middle, self.point)
        self._fill(target)
        self._decision(target).action_apply()
        self._send(target, last).action_apply()
        next_receipts = self._transfers(target)
        self.assertEqual(set(first_receipts.mapped('state')), {'advanced'})
        self.assertEqual(next_receipts.controlled_unit_id, first_receipts.controlled_unit_id)
        self._receive(last, next_receipts).action_apply()
        self._execution_wizard(last, next_receipts).action_apply()
        self.assertEqual(last.quality_execution_progress, 4)
        for unit in next_receipts.controlled_unit_id:
            self.assertEqual(len(unit.quality_execution_ids), 2)
            self.assertEqual(set(unit.quality_execution_ids.workorder_id.ids), {middle.id, last.id})
        self.assertEqual(source.state, 'progress')
        self.assertEqual(middle.blocked_by_workorder_ids, source)

    def test_view_and_actions_use_supported_modal_contract(self):
        check, source, destination, records = self._received()
        for mode in ('start', 'finish', 'stop', 'control'):
            action = self.env['quality.unit.execution.wizard']._open_action(destination, mode)
            self.assertEqual(action['views'], [(self.env.ref('quality_vataga.quality_unit_execution_wizard_form').id, 'form')])
        doc = etree.fromstring(destination.get_view(view_type='form')['arch'])
        self.assertEqual(len(doc.xpath("//page[@name='quality_unit_execution']")), 1)
        self.assertEqual(len(doc.xpath("//widget[@name='quality_unit_execution_buttons']")), 1)
