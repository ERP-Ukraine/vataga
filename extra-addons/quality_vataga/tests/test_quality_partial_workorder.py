"""Phase 4.2 Enterprise ORM scenarios; disposable fixtures only."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from . import test_quality_native_workorder as native_cases


@tagged('post_install', '-at_install', 'quality_partial_wo')
class TestQualityPartialWorkorder(native_cases.QualityCheckInProcessCase):
    _route = native_cases.TestQualityNativeWorkorder._route
    _workorder = native_cases.TestQualityNativeWorkorder._workorder

    def test_three_of_five_records_actual_batch_without_fictitious_units_or_stock(self):
        self._assert_three_of_five_processing(required=True)

    def test_ordinary_in_process_three_of_five_records_actual_batch(self):
        self._assert_three_of_five_processing(required=False)

    def _assert_three_of_five_processing(self, required):
        production, orders = self._route(5, 2)
        production.allow_workorder_dependencies = True
        orders[1].blocked_by_workorder_ids = orders[0]
        production.qty_producing = 3
        self.point.quality_unit_route_required = required
        check = self._check(production, workorder_id=orders[0].id)
        check.initialize_qc_unit_identity()
        self.assertEqual(len(check.sample_ids), 3)
        units = check.sample_ids.controlled_unit_id
        self.assertEqual(len(units), 3)
        self.assertEqual(check.operation_run_id.quantity, 3)
        self._fill(check)
        check.do_pass()
        for order in orders:
            order.button_start(bypass=True)
            order.button_finish()
            self.assertEqual((order.state, order.qty_produced, order.qty_remaining),
                             ('done' if required else 'progress', 3, 2))
            self.assertEqual(order.quality_execution_ids.controlled_unit_id, units)
            self.assertEqual(len(order.quality_execution_ids), 3)
            order.button_finish()  # Idempotent acknowledgement after the pause.
            self.assertEqual(len(order.quality_execution_ids), 3)
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(production.native_stock_fact_ids)
        self.assertFalse(production.move_finished_ids.filtered(lambda move: move.state == 'done'))
        self.assertFalse(production.move_raw_ids.filtered(lambda move: move.state == 'done'))
        self.assertEqual(production._full_release_units(), units)
        # Partial processing is not a capability for stock or whole-WO closure.
        with self.assertRaises((UserError, ValidationError)), self.cr.savepoint():
            orders[0].write({'state': 'done', 'qty_produced': 5})
        if required:
            with self.assertRaises((UserError, ValidationError)), self.cr.savepoint():
                production._check_required_unit_route_completion()
        with self.assertRaises((UserError, ValidationError)), self.cr.savepoint():
            production._quality_partial_universe()

    def test_three_of_five_pending_or_failed_unit_cannot_advance(self):
        for result in ('pending', 'fail'):
            with self.subTest(result=result):
                production, orders = self._route(5, 2)
                production.allow_workorder_dependencies = True
                orders[1].blocked_by_workorder_ids = orders[0]
                production.qty_producing = 3
                self.point.quality_unit_route_required = True
                check = self._check(production, workorder_id=orders[0].id)
                check.initialize_qc_unit_identity()
                self._fill(check)
                sample = check.sample_ids[-1]
                if result == 'fail':
                    check.update_measurement_visual_result(sample.id, 'no')
                    check.do_fail()
                else:
                    cell = sample.measurement_value_ids.filtered(lambda row: row.parameter_type == 'numeric')
                    check.update_measurement_value(cell.id, {'numeric_input': ''})
                    with self.assertRaises(ValidationError), self.cr.savepoint():
                        check.do_pass()
                orders[0].button_start(bypass=True)
                orders[0].button_finish()
                if result == 'pending':
                    with self.assertRaises(UserError), self.cr.savepoint():
                        orders[1].button_start(bypass=True)
                else:
                    orders[1].button_start(bypass=True)
                self.assertNotIn(sample.controlled_unit_id, orders[1].quality_native_unit_ids)
                self.assertFalse(production.native_stock_fact_ids)

    def test_three_of_five_alert_holds_repaired_unit(self):
        production, orders = self._route(5, 2)
        production.allow_workorder_dependencies = True
        orders[1].blocked_by_workorder_ids = orders[0]
        production.qty_producing = 3
        self.point.quality_unit_route_required = True
        check = self._check(production, workorder_id=orders[0].id)
        check.initialize_qc_unit_identity()
        self._fill(check)
        sample = check.sample_ids[-1]
        check.update_measurement_visual_result(sample.id, 'no')
        alert = self.env['quality.alert'].with_context(
            check.action_open_matrix_quality_alert()['context']).create({'name': 'Утримання виробу'})
        check.update_measurement_visual_result(sample.id, 'yes')
        check.do_pass()
        orders[0].button_start(bypass=True)
        orders[0].button_finish()
        orders[1].button_start(bypass=True)
        self.assertNotIn(sample.controlled_unit_id, orders[1].quality_native_unit_ids)
        self.assertTrue(alert._matrix_hold_is_open())
        self.assertFalse(production.native_stock_fact_ids)

    def _mixed(self):
        production, orders = self._route(5, 3)
        production.allow_workorder_dependencies = True
        orders[1].blocked_by_workorder_ids = orders[0]
        orders[2].blocked_by_workorder_ids = orders[1]
        orders[0].button_start()
        orders[0].button_finish()
        self.point.quality_unit_route_required = True
        check = self._check(production, workorder_id=orders[1].id)
        check.initialize_qc_unit_identity()
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[2].id, 'no')
        orders[1].button_start(bypass=True)
        check.do_fail()
        orders[1].button_finish()
        return production, orders, check

    def test_five_processed_four_pass_continue_then_fifth_reuses_identity(self):
        production, orders, check = self._mixed()
        units = check.sample_ids.controlled_unit_id
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass').controlled_unit_id
        failed = units - good
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (4, 1))
        self.assertEqual((orders[1].state, orders[1].qty_produced), ('done', 5))
        self.assertEqual(len(orders[1].quality_execution_ids), 5)
        orders[1].button_finish()
        self.assertEqual(len(orders[1].quality_execution_ids), 5)
        self.assertEqual(orders[1].quality_native_unit_ids, units)
        orders[2].button_start()
        orders[2].button_finish()
        self.assertEqual((orders[2].state, orders[2].qty_produced, orders[2].qty_remaining), ('progress', 4, 1))
        self.assertEqual(orders[2].blocked_by_workorder_ids, orders[1])
        self.assertEqual((production.product_qty, production.qty_producing, production.qty_produced), (5, 5, 0))
        self.assertFalse(production.native_stock_fact_ids)
        self.assertFalse(orders[0].quality_execution_ids)
        self.assertNotIn(failed, orders[2].quality_execution_ids.controlled_unit_id)
        execution = orders[1].quality_execution_ids.filtered(lambda row: row.controlled_unit_id == failed)
        retest = execution._create_control(orders[1], self.point)
        self._fill(retest)
        retest.do_pass()
        check.invalidate_recordset(['x_admitted_qty', 'x_held_qty'])
        self.assertEqual(check.x_admitted_qty, 4)
        self.assertEqual(retest.sample_ids.controlled_unit_id, failed)
        orders[2].button_start()
        orders[2].button_finish()
        self.assertEqual(orders[2].qty_produced, 5)
        orders[1].button_finish()
        self.assertEqual(len(orders[1].quality_execution_ids | orders[2].quality_execution_ids), 10)
        self.assertFalse(production.native_stock_fact_ids)

    def test_alert_holds_retested_unit_and_does_not_hold_other_four(self):
        production, orders, check = self._mixed()
        failed = check.sample_ids.filtered(lambda row: row.sample_result == 'fail').controlled_unit_id
        self.env['quality.alert'].create({'name': 'Partial WO hold', 'check_id': check.id, 'team_id': self.team.id})
        execution = orders[1].quality_execution_ids.filtered(lambda row: row.controlled_unit_id == failed)
        retest = execution._create_control(orders[1], self.point)
        self._fill(retest)
        retest.do_pass()
        self.assertEqual(check.x_admitted_qty, 4)
        orders[2].button_start()
        orders[2].button_finish()
        self.assertEqual(orders[2].qty_produced, 4)
        self.assertNotIn(failed, orders[2].quality_execution_ids.controlled_unit_id)

    def test_partial_fact_failure_rolls_back_quantity_and_history(self):
        production, orders, check = self._mixed()
        orders[2].button_start()
        original = type(self.env['quality.unit.execution'])._record_partial_completion
        def fail(model, workorder, units, request):
            original(model, workorder, units, request)
            raise UserError('Injected partial failure')
        with patch.object(type(self.env['quality.unit.execution']), '_record_partial_completion', fail):
            with self.assertRaisesRegex(UserError, 'Injected'):
                orders[2].button_finish()
        self.assertEqual(orders[2].qty_produced, 0)
        self.assertFalse(orders[2].quality_execution_ids)
        self.assertEqual(orders[2].state, 'progress')
        with self.assertRaises(UserError):
            orders[2].write({'qty_produced': 4})

    def test_qc_only_on_last_operation_needs_no_manual_qunit_actions(self):
        production, orders = self._route(5, 3)
        self.point.quality_unit_route_required = True
        check = self._check(production, workorder_id=orders[2].id)
        for workorder in orders[:2]:
            workorder.button_start()
            workorder.button_finish()
        orders[2].button_start()
        orders[2].button_finish()
        self.assertEqual(set(orders.mapped('state')), {'done'})
        self.assertEqual(check.quality_state, 'none')
        self.assertFalse(check.unit_admission_ids or orders[:2].check_ids)
        self.assertFalse(orders[:2].quality_execution_ids)
        self.assertEqual(len(orders[2].quality_execution_ids), 5)
        self.assertEqual(orders[2].quality_native_unit_ids, check.sample_ids.controlled_unit_id)
        self._fill(check)
        check.do_pass()
        self.assertEqual(check.x_admitted_qty, 5)
        self.assertFalse(orders.time_ids.filtered(lambda timer: not timer.date_end))

    def test_retest_during_next_operation_does_not_add_unprocessed_fifth(self):
        production, orders, check = self._mixed()
        failed = check.sample_ids.filtered(lambda row: row.sample_result == 'fail').controlled_unit_id
        orders[2].button_start()
        self.assertEqual(len(orders[2].quality_native_unit_ids), 4)
        repeat = orders[1].quality_execution_ids.filtered(
            lambda row: row.controlled_unit_id == failed)._create_control(orders[1], self.point)
        self._fill(repeat)
        repeat.do_pass()
        orders[2].button_finish()
        self.assertEqual((orders[2].state, orders[2].qty_produced), ('progress', 4))
        self.assertNotIn(failed, orders[2].quality_execution_ids.controlled_unit_id)
        orders[2].button_start()
        self.assertEqual(orders[2].quality_native_unit_ids, failed)
        orders[2].button_finish()
        self.assertEqual((orders[2].state, orders[2].qty_produced), ('done', 5))
