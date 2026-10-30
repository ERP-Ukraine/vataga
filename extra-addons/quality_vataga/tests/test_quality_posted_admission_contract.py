"""Executable admission lifecycle contracts; no Enterprise ORM claim."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError, AccessError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError, AccessError


class Rows(list):
    @property
    def ids(self):
        return [row.id for row in self]
    def filtered(self, predicate):
        return Rows(row for row in self if predicate(row))
    def invalidate_recordset(self, fields):
        pass
    @property
    def controlled_unit_id(self):
        return Rows(row.controlled_unit_id for row in self)
    @property
    def sample_result(self):
        return self[0].sample_result


class TestPostedAdmissionValidity(unittest.TestCase):
    def setUp(self):
        base = shipped_class('models/quality_check_unit_admission.py', 'QualityCheckUnitAdmission',
                             {'_get_unit_admission_error'})
        held = shipped_class('models/quality_matrix_hold.py', 'QualityMatrixHoldCheck',
                             {'_get_unit_admission_error'}, base)
        posted = shipped_class('models/quality_stock_continuation.py', 'QualityPostedCheck',
                               {'_get_unit_admission_error', '_identify_samples_locked'}, held)
        quantities = shipped_class('models/quality_check_unit_admission.py', 'QualityCheckUnitAdmission',
                                   {'_compute_unit_admission_quantities'}, posted)
        admission = shipped_class('models/quality_unit_admission.py', 'QualityUnitAdmission',
                                  {'_get_effectiveness_error'})
        self.check = quantities()
        self.check.ensure_one = Mock()
        self.check._get_unit_admission_structure_error = Mock(return_value=False)
        self.check._matrix_alert_hold_error = Mock(return_value=False)
        self.check._unit_admission_required_checks = lambda unit=None: [self.check]
        self.check.quality_state, self.check.display_name = 'pass', 'QC existing'
        self.check.x_control_stage, self.check.operation_run_id = 'in_process', True
        self.check.in_process_sample_quantity = 2
        self.units = Rows(NS(id=index, posted=False) for index in (1, 2))
        for unit in self.units:
            unit._stock_posted = lambda unit=unit: unit.posted
        self.check.sample_ids = Rows(NS(id=unit.id, quality_check_id=self.check,
                                       controlled_unit_id=unit, sample_result='pass') for unit in self.units)
        self.admissions = Rows()
        for sample in self.check.sample_ids:
            record = admission()
            record.id, record.state = sample.id, 'confirmed'
            record.check_id, record.sample_id, record.controlled_unit_id = self.check, sample, sample.controlled_unit_id
            record.ensure_one, record.check_access_rights, record.check_access_rule = Mock(), Mock(), Mock()
            record.sudo = lambda value, record=record: record
            self.admissions.append(record)
        self.check._active_run_admissions = lambda: self.admissions
        self.check.__class__.__iter__ = lambda record: iter([record])

    def assert_counts(self, admitted, held):
        self.check._compute_unit_admission_quantities()
        self.assertEqual((self.check.x_admitted_qty, self.check.x_held_qty), (admitted, held))

    def test_before_and_after_posting_same_confirmations_stay_valid_without_rewrite(self):
        self.assert_counts(2, 0)
        for unit in self.units:
            unit.posted = True
        self.assert_counts(2, 0)
        self.assertTrue(all(not row._get_effectiveness_error() for row in self.admissions))
        self.assertEqual([row.state for row in self.admissions], ['confirmed', 'confirmed'])

    def test_fail_pending_and_unfinished_qc_still_hold_units(self):
        for result in ('fail', 'pending'):
            self.check.sample_ids[0].sample_result = result
            self.assert_counts(1, 1)
        self.check.sample_ids[0].sample_result = 'pass'
        self.check.quality_state = 'none'
        self.assert_counts(0, 2)

    def test_open_alert_still_holds_even_with_confirmed_history(self):
        self.check._matrix_alert_hold_error.return_value = 'Open Alert'
        self.assert_counts(0, 2)

    def test_invalid_structure_company_and_missing_mandatory_qc_still_hold(self):
        for error in ('Foreign company', 'Missing mandatory QC', 'Missing equipment'):
            self.check._get_unit_admission_structure_error.return_value = error
            self.assert_counts(0, 2)

    def test_revoked_and_hidden_admission_are_not_made_effective(self):
        self.admissions[0].state = 'revoked'
        self.assert_counts(1, 1)
        self.admissions[1].check_access_rule.side_effect = AccessError('hidden evidence')
        self.assert_counts(0, 2)


class TestPostedAdmissionCreation(unittest.TestCase):
    def test_direct_create_rejects_posted_unit_even_with_internal_token(self):
        token = object()
        model = shipped_class('models/quality_unit_admission.py', 'QualityUnitAdmission', {'create'},
                              ADMISSION_CONTEXT='admission', ADMISSION_TOKEN=token)()
        check = NS(_check_unit_admission_access=Mock(), _lock_unit_admission_scope=Mock(),
                   _get_unit_admission_error=Mock())
        unit = NS(_assert_not_stock_posted=Mock(side_effect=UserError('already posted')))
        sample = NS(controlled_unit_id=unit)
        class Env:
            context = {'admission': token}
            def __getitem__(self, name):
                return NS(browse=lambda value: NS(exists=lambda: check if name == 'quality.check' else sample))
        model.env = Env()
        with self.assertRaisesRegex(UserError, 'already posted'):
            model.create([{'check_id': 1, 'sample_id': 1, 'request_key': 'new'}])
        check._lock_unit_admission_scope.assert_called_once()
        check._get_unit_admission_error.assert_not_called()

    def test_plain_orm_context_cannot_forge_creation_token(self):
        token = object()
        model = shipped_class('models/quality_unit_admission.py', 'QualityUnitAdmission', {'create'},
                              ADMISSION_CONTEXT='admission', ADMISSION_TOKEN=token)()
        model.env = NS(context={'admission': True})
        with self.assertRaises(UserError):
            model.create([{'check_id': 1, 'sample_id': 1, 'request_key': 'forged'}])

    def test_action_rejects_posted_unit_before_active_or_idempotent_shortcut(self):
        check = shipped_class('models/quality_check_unit_admission.py', 'QualityCheckUnitAdmission',
                              {'_execute_unit_admission_action'})()
        check.ensure_one, check._check_unit_admission_access = Mock(), Mock()
        check._lock_unit_admission_scope, check.invalidate_recordset = Mock(), Mock()
        check.operation_run_id = True
        samples = Rows([NS(id=1)])
        class PostedRows(Rows):
            def filtered(self, predicate):
                return self
            @property
            def controlled_unit_id(self):
                return NS(_assert_not_stock_posted=Mock(side_effect=UserError('already posted')))
        check.sample_ids = PostedRows(samples)
        check.env = NS(cr=NS(savepoint=nullcontext))
        check._active_run_admissions = Mock(side_effect=AssertionError('must not reach shortcut'))
        with self.assertRaisesRegex(UserError, 'already posted'):
            check._execute_unit_admission_action([1], 'confirm', 'same-request')
        check._active_run_admissions.assert_not_called()

    def test_used_admission_revocation_still_checks_stock(self):
        model = shipped_class('models/quality_stock_continuation.py', 'QualityPostedAdmission',
                              {'_get_consumption_error'})()
        model.controlled_unit_id = NS(_stock_posted=lambda: True)
        self.assertTrue(model._get_consumption_error())


if __name__ == '__main__':
    unittest.main()
