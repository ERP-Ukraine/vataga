"""Per-unit QC selection and continuation contracts, without Enterprise ORM."""
from types import SimpleNamespace as NS
from contextlib import nullcontext
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError


class Rows(list):
    @property
    def ids(self):
        return [row.id for row in self]
    def __or__(self, other):
        values = other if isinstance(other, Rows) else [other]
        return Rows(list(self) + [row for row in values if row not in self])
    def check_access_rights(self, operation):
        pass
    def check_access_rule(self, operation):
        pass
    def filtered(self, predicate):
        return Rows(row for row in self if (getattr(row, predicate) if isinstance(predicate, str) else predicate(row)))
    @property
    def sample_result(self):
        return self[0].sample_result


class TestUnitScopedRequiredChecks(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_check_unit_admission.py', 'QualityCheckUnitAdmission',
                            {'_unit_admission_required_checks'})
        self.first, self.repaired = NS(id=11), NS(id=12)
        workorder = NS(id=1)
        self.original, self.retest = cls(), cls()
        for record, index, units in ((self.original, 1, [self.first, self.repaired]),
                                     (self.retest, 2, [self.repaired])):
            record.id, record.point_id = index, NS(id=20)
            record.ensure_one = Mock()
            record._fields = {'workorder_id': None}
            record.operation_run_id = NS(id=index, workorder_id=workorder, unit_ids=Rows(units))
        self.records = [self.retest, self.original]
        case = self
        class Checks:
            def sudo(self):
                return self
            def search(self, domain, order=None):
                run = next((row[2] for row in domain if isinstance(row, tuple) and row[0] == 'operation_run_id'), None)
                return Rows(record for record in case.records if run is None or (
                    record.operation_run_id and record.operation_run_id.id == run))
            def browse(self, ids):
                return Rows(record for record in case.records if record.id in ids)
        class Env:
            def __getitem__(self, name):
                return Checks()
        self.original.env = self.retest.env = Env()

    def test_single_unit_retest_does_not_become_requirement_for_other_units(self):
        result = self.original._unit_admission_required_checks(unit=self.first)
        self.assertEqual(set(result.ids), {1})

    def test_repaired_unit_uses_its_new_check(self):
        result = self.retest._unit_admission_required_checks(unit=self.repaired)
        self.assertEqual(set(result.ids), {2})

    def test_unidentified_other_mandatory_qcp_still_blocks_all_units(self):
        self.records.insert(0, NS(id=3, point_id=NS(id=21), operation_run_id=False))
        self.assertIn(3, self.original._unit_admission_required_checks(unit=self.first).ids)
        self.assertIn(3, self.retest._unit_admission_required_checks(unit=self.repaired).ids)


class TestPartialUnitValidation(unittest.TestCase):
    def setUp(self):
        self.workorder = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder',
                                      {'_quality_validate_native_checks', '_quality_validate_native_unit_checks'})()
        self.good, self.bad = NS(id=1), NS(id=2)
        def make(index, units, passing):
            check = NS(id=index, point_id=NS(id=10), operation_run_id=NS(unit_ids=Rows(units)),
                       _lock_unit_admission_scope=Mock())
            check.sample_ids = Rows(NS(controlled_unit_id=unit, sample_result='pass' if unit in passing else 'fail') for unit in units)
            check._get_unit_admission_error = lambda samples: False if samples.sample_result == 'pass' else 'FAIL'
            check.unit_admission_ids = Rows(NS(controlled_unit_id=unit, state='confirmed',
                                               _get_effectiveness_error=lambda: False) for unit in passing)
            return check
        self.old = make(1, [self.good, self.bad], [self.good])
        self.new = make(2, [self.bad], [self.bad])

    def test_four_good_scope_can_advance_from_mixed_qc(self):
        self.workorder._quality_validate_native_checks([self.old], [self.good])

    def test_fail_stays_blocked_without_retest(self):
        with self.assertRaises(UserError):
            self.workorder._quality_validate_native_checks([self.old], [self.bad])

    def test_old_good_and_repaired_unit_can_advance_together(self):
        self.workorder._quality_validate_native_checks([self.old, self.new], [self.good, self.bad])

    def test_alert_or_revoked_permission_still_holds_repaired_unit(self):
        self.new.unit_admission_ids[0]._get_effectiveness_error = lambda: 'Alert held'
        with self.assertRaises(UserError):
            self.workorder._quality_validate_native_checks([self.old, self.new], [self.bad])


class TestPartialAlertClosure(unittest.TestCase):
    def setUp(self):
        class Parent:
            def action_close_alert(self):
                return 'standard closure'
        cls = shipped_class('models/quality_native_workorder.py', 'QualityPartialWorkorderAlert',
                            {'action_close_alert'}, Parent)
        self.alert = cls()
        self.alert.env = NS(cr=NS(savepoint=nullcontext))
        self.production = NS(quality_unit_route_required=True, quality_transfer_controlled=False, state='progress')
        history = Rows([NS(batch_id=NS(request_key='partial:1:request'))])
        self.check = NS(operation_run_id=NS(workorder_id=NS(_quality_native_history=lambda: history)),
                        _identity_production=lambda: self.production, _confirm_completed_qc_units=Mock())
        self.alert.check_id = Rows([self.check])

    def test_closure_reuses_qc_confirmation_after_parent_validation(self):
        self.assertEqual(self.alert.action_close_alert(), 'standard closure')
        self.check._confirm_completed_qc_units.assert_called_once()

    def test_posted_or_legacy_production_is_not_reconfirmed(self):
        self.production.state = 'done'
        self.alert.action_close_alert()
        self.production.state, self.production.quality_transfer_controlled = 'progress', True
        self.alert.action_close_alert()
        self.check._confirm_completed_qc_units.assert_not_called()


if __name__ == '__main__':
    unittest.main()
