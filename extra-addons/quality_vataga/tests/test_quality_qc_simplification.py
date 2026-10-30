"""Phase 1 Enterprise ORM regressions; run on a disposable Enterprise DB."""
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_production_identity as identity_tests


@tagged('post_install', '-at_install', 'quality_qc_phase1')
class TestQualityQcSimplification(QualityCheckInProcessCase):
    _workorder = identity_tests.TestQualityProductionIdentity._workorder
    _inspector = identity_tests.TestQualityProductionIdentity._inspector

    def _batch(self, quantity=2):
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Operation QC requires quality_mrp_workorder')
        production = self._production(quantity)
        production.product_qty = quantity
        workorder = self._workorder(production, 'QC phase 1')
        check = self._check(production, workorder_id=workorder.id)
        return production, workorder, check

    def _counts(self, production):
        return tuple(self.env[model].sudo().search_count([('production_id', '=', production.id)])
                     for model in ('quality.production.unit', 'quality.production.run', 'quality.unit.admission'))

    def _bind(self, check, units):
        return check.bind_measurement_units([
            {'sample_id': sample.id, 'unit_id': unit.id}
            for sample, unit in zip(check.sample_ids.sorted('sample_number'), units)
        ])

    def test_open_twice_registers_exactly_two_and_preserves_evidence(self):
        production, workorder, check = self._batch()
        data = check.initialize_qc_unit_identity()
        self.assertEqual(self._counts(production), (2, 1, 0))
        identities = check.sample_ids.controlled_unit_id
        self.assertEqual(check.operation_run_id.workorder_id, workorder)
        self.assertEqual(len({row['controlled_unit_id'] for row in data['samples']}), 2)
        for _ in range(3):
            check.initialize_qc_unit_identity()
            check.initialize_in_process_measurement_matrix()
        self.assertEqual(self._counts(production), (2, 1, 0))
        self.assertEqual(check.sample_ids.controlled_unit_id, identities)
        production.qty_producing = 1
        self.assertTrue(check.get_measurement_matrix_data()['batch_error'])
        self.assertEqual(self._counts(production), (2, 1, 0))
        self.assertEqual(len(check.sample_ids), 2)

    def test_completion_records_inspector_and_time_once(self):
        production, _, check = self._batch()
        inspector = self._inspector()
        check = check.with_user(inspector)
        check.initialize_qc_unit_identity()
        self._fill(check)
        self.assertEqual(self._counts(production), (2, 1, 0))
        check.do_pass()
        check.do_pass()
        self.assertEqual(self._counts(production), (2, 1, 2))
        admissions = check.unit_admission_ids
        self.assertEqual(admissions.inspector_id, inspector)
        self.assertTrue(all(admissions.mapped('confirmed_at')))
        self.assertTrue(all(admissions.mapped('evidence_snapshot')))
        self.assertTrue(all(admissions.mapped('is_effective')))
        self.assertEqual(check.x_admitted_qty, 2)
        self.assertFalse(production.quality_transfer_controlled)
        self.assertEqual(production.qty_produced, 0)

    def test_mixed_result_admits_only_pass_and_full_retest_reuses_canonical_units(self):
        production, workorder, check = self._batch()
        check.initialize_qc_unit_identity()
        self._fill(check)
        failed = check.sample_ids[-1]
        check.update_measurement_visual_result(failed.id, 'no')
        alert = self.env['quality.alert'].create({
            'name': 'Phase 1 nonconformity', 'check_id': check.id, 'team_id': self.team.id,
        })
        self.assertEqual(alert.vataga_failure_sample_ids, failed)
        check.do_fail()
        self.assertEqual(check.x_admitted_qty, 1)
        self.assertEqual(check.unit_admission_ids.controlled_unit_id, (check.sample_ids - failed).controlled_unit_id)
        old_units = check.sample_ids.controlled_unit_id
        retest = self._check(production, workorder_id=workorder.id)
        data = retest.initialize_qc_unit_identity()
        self.assertFalse(data.get('identity_required'))
        self.assertTrue(retest.operation_run_id)
        self.assertEqual(self._counts(production)[:2], (2, 2))
        self.assertEqual(retest.sample_ids.sorted('sample_number').controlled_unit_id,
                         old_units.sorted('unit_number'))
        self._fill(retest)
        retest.do_pass()
        self.assertEqual(self._counts(production)[0], 2)
        self.assertEqual(set(retest.sample_ids.controlled_unit_id.ids), set(old_units.ids))
        held = retest.sample_ids.filtered(lambda sample: sample.controlled_unit_id == failed.controlled_unit_id)
        self.assertTrue(retest._get_unit_admission_error(held))
        self.assertEqual(failed.sample_result, 'fail')
        self.assertTrue(alert.vataga_matrix_failure_snapshot)

    def test_pending_sibling_qcp_blocks_even_in_another_internal_run(self):
        production, workorder, first = self._batch()
        first.initialize_qc_unit_identity()
        point = self.point.copy({'title': 'Second mandatory QCP'})
        second = self._check(production, point=point, workorder_id=workorder.id)
        self._fill(first)
        first.do_pass()
        self.assertFalse(first.unit_admission_ids)
        self._bind(second, first.sample_ids.controlled_unit_id)
        self._fill(second)
        self.assertNotEqual(first.operation_run_id, second.operation_run_id)
        second.do_pass()
        self.assertEqual(first.x_admitted_qty, 2)
        self.assertEqual(second.x_admitted_qty, 2)
        with self.assertRaises(UserError):
            second.update_measurement_visual_result(second.sample_ids[0].id, 'no')

    def test_incomplete_direct_completion_rolls_back_identity_too(self):
        production, _, check = self._batch()
        before = self._counts(production)
        samples, values = check.sample_ids.ids, check.sample_ids.measurement_value_ids.ids
        for state in ('pass', 'fail'):
            with self.assertRaises((UserError, ValidationError)):
                check.write({'quality_state': state})
            self.assertEqual(self._counts(production), before)
            self.assertFalse(check.operation_run_id)
            self.assertEqual(check.quality_state, 'none')
            self.assertEqual(check.sample_ids.ids, samples)
            self.assertEqual(check.sample_ids.measurement_value_ids.ids, values)

    def test_bad_mapping_and_new_registration_do_not_leave_partial_records(self):
        production, workorder, original = self._batch()
        original.initialize_qc_unit_identity()
        second = self._check(production, workorder_id=workorder.id)
        before = self._counts(production)
        unit = original.sample_ids[0].controlled_unit_id
        with self.assertRaises(ValidationError):
            self._bind(second, unit | unit)  # missing the other row
        with self.assertRaises(ValidationError):
            second.bind_measurement_units([
                {'sample_id': sample.id, 'unit_id': unit.id} for sample in second.sample_ids
            ])
        with self.assertRaises(ValidationError):
            second._identify_samples('new', [{'sample_id': sample.id} for sample in second.sample_ids],
                                     workorder=workorder, confirmed=True)
        self.assertEqual(self._counts(production), before)
        self.assertFalse(second.operation_run_id)
        self.assertFalse(second.sample_ids.controlled_unit_id)

    def test_rework_and_multiple_alert_holds_keep_original_evidence(self):
        production, _, check = self._batch()
        check.initialize_qc_unit_identity()
        self._fill(check)
        value = check.sample_ids[0].measurement_value_ids.filtered(lambda cell: cell.parameter_type == 'numeric')
        check.update_measurement_value(value.id, {'numeric_input': '11'})
        alerts = self.env['quality.alert'].create([
            {'name': 'Hold %s' % index, 'check_id': check.id, 'team_id': self.team.id} for index in (1, 2)
        ])
        self.assertTrue(all(alerts.mapped('x_sorting_parameter')))
        check.update_measurement_value(value.id, {'numeric_input': '5'})
        sample = value.sample_id
        self.assertTrue(sample.x_was_reworked)
        self.assertTrue(any('FAIL' in str(message.body) and 'PASS' in str(message.body) for message in check.message_ids))
        check.do_pass()
        self.assertEqual(check.x_admitted_qty, 1)
        # Isolate hold aggregation from the unchanged board closure workflow.
        original = type(alerts)._matrix_hold_is_open
        with patch.object(type(alerts), '_matrix_hold_is_open',
                          lambda alert: False if alert.id == alerts[0].id else original(alert)):
            self.assertIn(alerts[1].display_name, check._matrix_alert_hold_error(sample))
        self.assertEqual(self._counts(production)[0], 2)

    def test_third_operation_only_needs_its_own_qc(self):
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Operation QC requires quality_mrp_workorder')
        production2 = self._production(2)
        operations = [self._workorder(production2, str(index)) for index in (1, 2, 3)]
        self.point.write({'quality_unit_route_required': True})
        check = self._check(production2, workorder_id=operations[2].id)
        self.assertTrue(production2.quality_unit_route_required)
        production2._check_matrix_native_completion(workorders=operations[0] | operations[1])
        check.initialize_qc_unit_identity()
        self._fill(check)
        check.do_pass()
        self.assertEqual(check.x_admitted_qty, 2)
        self.assertEqual(check._unit_admission_required_checks(), check)
        with self.assertRaises(UserError):
            production2._check_required_unit_route_completion()

    def test_rpc_cannot_forge_admission_or_replace_bound_unit(self):
        production, _, check = self._batch()
        check.initialize_qc_unit_identity()
        before = self._counts(production)
        with self.assertRaises(UserError):
            self.env['quality.unit.admission'].with_context(quality_vataga_unit_admission=True).create({
                'check_id': check.id, 'sample_id': check.sample_ids[0].id, 'request_key': 'forged',
            })
        with self.assertRaises(UserError):
            check.sample_ids[0].write({'controlled_unit_id': check.sample_ids[-1].controlled_unit_id.id})
        self.assertEqual(self._counts(production), before)

    def test_existing_links_are_read_without_re_registration(self):
        production, _, check = self._batch()
        check.initialize_qc_unit_identity()
        old_run = check.operation_run_id
        old_mapping = [(sample.id, sample.controlled_unit_id.id) for sample in check.sample_ids]
        with patch.object(type(check), '_identify_samples', side_effect=AssertionError('must preserve registered QC')):
            check.get_measurement_matrix_data()
        self.assertEqual(check.operation_run_id, old_run)
        self.assertEqual([(sample.id, sample.controlled_unit_id.id) for sample in check.sample_ids], old_mapping)
        self.assertEqual(self._counts(production), (2, 1, 0))
