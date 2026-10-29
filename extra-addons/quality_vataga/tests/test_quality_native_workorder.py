"""Phase 2: actual Enterprise WO/identity/admission/Alert regression scenarios."""
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_production_identity as identities


@tagged('post_install', '-at_install', 'quality_wo_phase2')
class TestQualityNativeWorkorder(QualityCheckInProcessCase):
    _workorder = identities.TestQualityProductionIdentity._workorder
    _inspector = identities.TestQualityProductionIdentity._inspector

    def _route(self, quantity=2, operations=2):
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Requires quality_mrp_workorder / Enterprise Shop Floor')
        production = self._production(quantity)
        production.product_qty = quantity
        workorders = self.env['mrp.workorder']
        for index in range(operations):
            workorders |= self._workorder(production, 'Phase 2 / %s' % index)
        return production, workorders

    def _pass(self, production, workorder):
        check = self._check(production, workorder_id=workorder.id)
        check.initialize_qc_unit_identity()
        self._fill(check)
        check.do_pass()
        return check

    def _counts(self, production):
        return tuple(self.env[model].sudo().search_count([('production_id', '=', production.id)])
                     for model in ('quality.production.unit', 'quality.unit.execution', 'quality.unit.transfer'))

    def test_ordinary_route_stays_native_without_unit_facts(self):
        production, workorders = self._route()
        for workorder in workorders:
            workorder.button_start()
            workorder.button_finish()
        self.assertEqual(set(workorders.mapped('state')), {'done'})
        self.assertEqual(self._counts(production), (0, 0, 0))

    def test_ordinary_mrp_operator_needs_no_quality_role(self):
        production, workorders = self._route()
        operator = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Phase 2 MRP operator', 'login': 'phase2_mrp_only',
            'groups_id': [Command.set([self.env.ref('mrp.group_mrp_user').id])],
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)]})
        for workorder in workorders.with_user(operator):
            workorder.button_start()
            workorder.button_finish()
            self.assertFalse(workorder.quality_execution_ids)
        self.assertEqual(self._counts(production), (0, 0, 0))

    def test_third_operation_qc_does_not_backfill_first_two(self):
        production, workorders = self._route(operations=3)
        self.point.quality_unit_route_required = True
        check = self._check(production, workorder_id=workorders[2].id)
        for workorder in workorders[:2]:
            workorder.button_start()
            workorder.button_finish()
        check.initialize_qc_unit_identity()
        self._fill(check)
        check.do_pass()
        workorders[2].button_finish()
        self.assertFalse(workorders[:2].quality_execution_ids)
        self.assertEqual(self._counts(production), (2, 2, 0))
        self.assertFalse(self.env['quality.check'].search([('workorder_id', 'in', workorders[:2].ids)]))
        with self.assertRaises(UserError):
            production._check_required_unit_route_completion()

    def test_two_pass_follow_native_route_without_handoffs_or_extra_timers(self):
        production, workorders = self._route()
        check = self._pass(production, workorders[0])
        workorders[0].button_start(bypass=True)
        self.assertEqual(len(workorders[0].time_ids.filtered(lambda timer: not timer.date_end)), 1)
        workorders[0].button_finish()
        workorders[1].button_start()
        workorders[1].button_finish()
        self.assertEqual(self._counts(production), (2, 4, 0))
        for workorder in workorders:
            self.assertEqual(set(workorder.quality_execution_ids.controlled_unit_id.ids),
                             set(check.sample_ids.controlled_unit_id.ids))
            self.assertFalse(workorder.quality_execution_ids.transfer_id)
            self.assertFalse(workorder.quality_execution_ids.batch_id.timer_ids)
            self.assertTrue(all(workorder.time_ids.mapped('date_end')))
            self.assertEqual(workorder.qty_produced, 2)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(production.quality_transfer_controlled)

    def test_repeat_finish_is_idempotent_and_result_is_immutable(self):
        production, workorders = self._route()
        self._pass(production, workorders[0])
        workorders[0].button_finish()
        before = workorders[0].quality_execution_ids.read()
        workorders[0].button_finish()
        workorders[0].write({'state': 'done', 'qty_produced': 2})
        self.assertEqual(before, workorders[0].quality_execution_ids.read())
        for values in ({'state': 'ready'}, {'qty_produced': 0}, {'quality_native_unit_ids': [Command.clear()]}):
            with self.assertRaises(UserError):
                workorders[0].write(values)

    def test_mixed_and_partial_native_completion_keep_remainder_and_timer(self):
        production, workorders = self._route()
        check = self._check(production, workorder_id=workorders[0].id)
        check.initialize_qc_unit_identity()
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        check.do_fail()
        workorders[0].button_start()
        timer = workorders[0].time_ids.filtered(lambda row: not row.date_end)
        before = self._counts(production)
        for call in (workorders[0].button_finish, workorders[0].button_done,
                     lambda: workorders[0].write({'state': 'done', 'qty_produced': 2}),
                     lambda: workorders[0].write({'qty_produced': 1}),
                     lambda: workorders[1].button_start(bypass=True)):
            with self.assertRaises(UserError):
                call()
            self.assertEqual(self._counts(production), before)
            self.assertEqual(workorders[0].qty_produced, 0)
            self.assertNotEqual(workorders[0].state, 'done')
            self.assertFalse(timer.date_end)
        workorders[0].quality_native_unit_ids = check.sample_ids[:1].controlled_unit_id
        with self.assertRaisesRegex(UserError, 'Enterprise'):
            workorders[0].button_finish()
        self.assertEqual(production.product_qty, 2)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(production.procurement_group_id.mrp_production_ids - production)

    def test_open_alert_holds_unit_even_after_pass_retest(self):
        production, workorders = self._route()
        original = self._check(production, workorder_id=workorders[0].id)
        original.initialize_qc_unit_identity()
        self._fill(original)
        original.update_measurement_visual_result(original.sample_ids[-1].id, 'no')
        alert = self.env['quality.alert'].create({
            'name': 'Phase 2 hold', 'check_id': original.id, 'team_id': self.team.id})
        original.do_fail()
        retest = self._check(production, workorder_id=workorders[0].id)
        retest.bind_measurement_units([
            {'sample_id': sample.id, 'unit_id': unit.id}
            for sample, unit in zip(retest.sample_ids, original.sample_ids.controlled_unit_id)])
        self._fill(retest)
        retest.do_pass()
        self.assertTrue(alert._matrix_hold_is_open())
        with self.assertRaises(UserError):
            workorders[0].button_finish()
        self.assertEqual(self._counts(production), (2, 0, 0))

    def test_pass_retest_keeps_identity_and_supersedes_old_fail(self):
        production, workorders = self._route()
        original = self._check(production, workorder_id=workorders[0].id)
        original.initialize_qc_unit_identity()
        self._fill(original)
        original.update_measurement_visual_result(original.sample_ids[-1].id, 'no')
        original.do_fail()
        retest = self._check(production, workorder_id=workorders[0].id)
        retest.bind_measurement_units([
            {'sample_id': sample.id, 'unit_id': unit.id}
            for sample, unit in zip(retest.sample_ids, original.sample_ids.controlled_unit_id)])
        self._fill(retest)
        retest.do_pass()
        workorders[0].button_finish()
        self.assertEqual(self._counts(production), (2, 2, 0))
        self.assertEqual(original.sample_ids[-1].sample_result, 'fail')

    def test_pending_sibling_qcp_blocks_completion(self):
        production, workorders = self._route()
        first = self._pass(production, workorders[0])
        self._check(production, point=self.point.copy({'title': 'Phase 2 pending QCP'}),
                    workorder_id=workorders[0].id)
        with self.assertRaises(UserError):
            workorders[0].button_finish()
        self.assertFalse(workorders.quality_execution_ids)
        self.assertEqual(len(first.sample_ids.controlled_unit_id), 2)

    def test_missing_operation_qcp_cannot_be_bypassed_by_absent_qc(self):
        production, workorders = self._route(operations=1)
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id, 'product_qty': 1})
        operation = self.env['mrp.routing.workcenter'].create({
            'name': 'Phase 2 mandatory QCP', 'bom_id': bom.id,
            'workcenter_id': workorders.workcenter_id.id})
        workorders.operation_id = operation
        field_name = next((name for name, field in self.point._fields.items()
                           if getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter'
                           and field.type in ('many2one', 'many2many') and not field.compute), None)
        self.assertTrue(field_name, 'Requires the real Enterprise QCP operation assignment')
        self.point[field_name] = (operation.id if self.point._fields[field_name].type == 'many2one'
                                  else [Command.set(operation.ids)])
        for call in (workorders.button_finish,
                     lambda: workorders.write({'state': 'done', 'qty_produced': 2})):
            with self.assertRaises(UserError):
                call()
        self.assertNotEqual(workorders.state, 'done')
        self.assertEqual(self._counts(production), (0, 0, 0))

    def test_foreign_selection_is_rejected_without_saving_it(self):
        production, workorders = self._route()
        self._pass(production, workorders[0])
        other, foreign_workorders = self._route()
        foreign = self._pass(other, foreign_workorders[0]).sample_ids.controlled_unit_id
        with self.assertRaises(ValidationError):
            workorders[0].write({'quality_native_unit_ids': [Command.set(foreign.ids)]})
        self.assertFalse(workorders[0].quality_native_unit_ids)

    def test_direct_native_write_validates_and_records_same_facts(self):
        production, workorders = self._route()
        self._pass(production, workorders[0])
        workorders[0].write({'state': 'done', 'qty_produced': 2})
        self.assertEqual(self._counts(production), (2, 2, 0))
        with self.assertRaises(UserError):
            self.env['quality.unit.execution'].with_context(quality_vataga_unit_execution=True).create({
                'batch_id': workorders[0].quality_execution_ids.batch_id.id,
                'controlled_unit_id': workorders[0].quality_execution_ids[0].controlled_unit_id.id})

    def test_fact_failure_rolls_back_native_state_timer_and_partial_facts(self):
        production, workorders = self._route()
        self._pass(production, workorders[0])
        workorders[0].button_start()
        timer = workorders[0].time_ids.filtered(lambda row: not row.date_end)
        original = type(self.env['quality.unit.execution'])._record_native_completion
        def fail_after_facts(model, workorder, units):
            original(model, workorder, units)
            raise UserError('Injected post-fact failure')
        with patch.object(type(self.env['quality.unit.execution']), '_record_native_completion', fail_after_facts):
            with self.assertRaisesRegex(UserError, 'Injected'):
                workorders[0].button_finish()
        self.assertEqual(workorders[0].state, 'progress')
        self.assertEqual(workorders[0].qty_produced, 0)
        self.assertFalse(timer.date_end)
        self.assertEqual(self._counts(production), (2, 0, 0))

    def test_native_facts_follow_existing_company_and_operator_acls(self):
        production, workorders = self._route()
        check = self._pass(production, workorders[0])
        operator = self._inspector()
        workorder = workorders[0].with_user(operator)
        workorder.button_finish()
        self.assertEqual(len(workorder.quality_execution_ids), 2)
        self.assertEqual(workorder.quality_execution_ids.operator_id, operator)
        self.assertEqual(set(workorder.quality_execution_ids.controlled_unit_id.ids),
                         set(check.sample_ids.controlled_unit_id.ids))
        with self.assertRaises((AccessError, UserError)):
            workorder.quality_execution_ids.unlink()
