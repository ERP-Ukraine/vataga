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
        check.do_fail()
        units = check.sample_ids.controlled_unit_id
        orders[1].quality_native_unit_ids = units
        orders[1].action_start_quality_native_units()
        orders[1].action_confirm_quality_native_units()
        return production, orders, check

    def test_five_processed_four_pass_continue_then_fifth_reuses_identity(self):
        production, orders, check = self._mixed()
        units = check.sample_ids.controlled_unit_id
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass').controlled_unit_id
        failed = units - good
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (4, 1))
        self.assertEqual((orders[1].state, orders[1].qty_produced), ('progress', 5))
        self.assertEqual(len(orders[1].quality_execution_ids), 5)
        orders[1].action_confirm_quality_native_units()
        self.assertEqual(len(orders[1].quality_execution_ids), 5)
        orders[2].quality_native_unit_ids = good
        orders[2].action_start_quality_native_units()
        orders[2].action_confirm_quality_native_units()
        self.assertEqual((orders[2].state, orders[2].qty_produced, orders[2].qty_remaining), ('progress', 4, 1))
        self.assertEqual(orders[2].blocked_by_workorder_ids, orders[1])
        self.assertEqual((production.product_qty, production.qty_producing, production.qty_produced), (5, 5, 0))
        self.assertFalse(production.native_stock_fact_ids)
        self.assertFalse(orders[0].quality_execution_ids)
        orders[2].quality_native_unit_ids = failed
        with self.assertRaises((UserError, ValidationError)):
            orders[2].action_start_quality_native_units()
        execution = orders[1].quality_execution_ids.filtered(lambda row: row.controlled_unit_id == failed)
        retest = execution._create_control(orders[1], self.point)
        self._fill(retest)
        retest.do_pass()
        check.invalidate_recordset(['x_admitted_qty', 'x_held_qty'])
        self.assertEqual(check.x_admitted_qty, 4)
        self.assertEqual(retest.sample_ids.controlled_unit_id, failed)
        orders[2].action_start_quality_native_units()
        orders[2].action_confirm_quality_native_units()
        self.assertEqual(orders[2].qty_produced, 5)
        orders[1].quality_native_unit_ids = units
        orders[2].quality_native_unit_ids = units
        orders[1].button_finish()
        orders[2].button_finish()
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
        orders[2].quality_native_unit_ids = failed
        with self.assertRaises((UserError, ValidationError)):
            orders[2].action_start_quality_native_units()

    def test_partial_fact_failure_rolls_back_quantity_and_history(self):
        production, orders, check = self._mixed()
        orders[2].quality_native_unit_ids = check.sample_ids.filtered(lambda row: row.sample_result == 'pass').controlled_unit_id
        orders[2].action_start_quality_native_units()
        original = type(self.env['quality.unit.execution'])._record_partial_completion
        def fail(model, workorder, units, request):
            original(model, workorder, units, request)
            raise UserError('Injected partial failure')
        with patch.object(type(self.env['quality.unit.execution']), '_record_partial_completion', fail):
            with self.assertRaisesRegex(UserError, 'Injected'):
                orders[2].action_confirm_quality_native_units()
        self.assertEqual(orders[2].qty_produced, 0)
        self.assertFalse(orders[2].quality_execution_ids)
        self.assertEqual(orders[2].state, 'progress')
        with self.assertRaises(UserError):
            orders[2].write({'qty_produced': 4})
