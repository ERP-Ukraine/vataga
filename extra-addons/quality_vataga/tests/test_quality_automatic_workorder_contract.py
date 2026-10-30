"""Automatic identity and individual WO eligibility, without Enterprise ORM."""
from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, AccessError, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, AccessError, UserError


class Record(NS):
    @property
    def ids(self):
        return [self.id]

    def __iter__(self):
        return iter([self])


class Rows(list):
    def __eq__(self, other):
        return self.ids == other.ids if isinstance(other, Record) else super().__eq__(other)

    def __ne__(self, other):
        return not self == other

    @property
    def ids(self):
        return [row.id for row in self]

    def __getattr__(self, name):
        values = []
        for row in self:
            value = getattr(row, name)
            for item in value if isinstance(value, Rows) else [value]:
                if item not in values:
                    values.append(item)
        if all(isinstance(value, Record) for value in values):
            return Rows(values)
        return values[0] if len(values) == 1 else values

    def __sub__(self, other):
        return Rows(row for row in self if row.id not in other.ids)

    def __or__(self, other):
        return Rows(list(self) + [row for row in other if row.id not in self.ids])

    def filtered(self, predicate):
        return Rows(row for row in self if predicate(row))

    def sorted(self, name, reverse=False):
        return Rows(sorted(self, key=lambda row: getattr(row, name), reverse=reverse))

    def mapped(self, name):
        return [getattr(row, name) for row in self]

    def check_access_rights(self, operation):
        pass

    def check_access_rule(self, operation):
        pass


class TestCanonicalIdentityReuse(unittest.TestCase):
    def setUp(self):
        self.check = shipped_class('models/quality_check_identity.py', 'QualityCheckIdentity',
                                   {'_ensure_qc_unit_identity'})()
        check = self.check
        production = Record(id=1, _lock_quality_transfer_scope=Mock(), workorder_ids=Rows())
        run = Record(id=2, state='registered', production_id=production)
        self.units = Rows(Record(id=number + 10, unit_number=number,
            registration_run_id=run, production_id=production) for number in (3, 1, 2))
        run.unit_ids = self.units
        self.run, self.production = run, production
        check.ensure_one = Mock()
        check.env = NS(cr=NS(savepoint=nullcontext))
        check.x_control_stage, check.quality_state, check.operation_run_id = 'in_process', 'none', False
        check.sample_ids = Rows(Record(id=number, sample_number=number) for number in (1, 2, 3))
        check.quality_execution_ids = NS(controlled_unit_id=Rows())
        check._get_in_process_batch_error = lambda **kw: False
        check._check_identification_access = lambda: production
        check.invalidate_recordset = Mock()
        check._qc_existing_units = lambda: self.units
        check._qc_identity_workorder = lambda: Record(id=50)
        check.product_id = NS(tracking='none')
        production.lot_producing_id = False
        check._identify_samples = Mock()

    def test_complete_registration_reuses_canonical_numbers_without_new_units(self):
        self.check._ensure_qc_unit_identity()
        args, kwargs = self.check._identify_samples.call_args
        self.assertEqual(args[0], 'reuse')
        self.assertEqual([row['unit_id'] for row in args[1]], [11, 12, 13])
        self.assertTrue(kwargs['confirmed'])
        self.production._lock_quality_transfer_scope.assert_called_once()

    def test_equal_quantity_with_multiple_registrations_is_ambiguous(self):
        self.units[0].registration_run_id = Record(id=99)
        self.check._ensure_qc_unit_identity()
        self.check._identify_samples.assert_not_called()

    def test_incomplete_registration_or_continuation_needs_mapping(self):
        self.run.unit_ids = Rows(self.units[:2])
        self.check._ensure_qc_unit_identity()
        self.check._identify_samples.assert_not_called()
        self.run.unit_ids = self.units
        self.units[0].production_id = Record(id=99)
        self.check._ensure_qc_unit_identity()
        self.check._identify_samples.assert_not_called()

    def test_partial_execution_control_keeps_its_explicit_identity(self):
        self.check.quality_execution_ids.controlled_unit_id = Rows(self.units[:1])
        self.check._ensure_qc_unit_identity()
        self.check._identify_samples.assert_not_called()

    def test_registered_check_is_never_remapped(self):
        self.check.operation_run_id = self.run
        self.check._ensure_qc_unit_identity()
        self.check._identify_samples.assert_not_called()
        self.production._lock_quality_transfer_scope.assert_not_called()


class TestAutomaticEligibility(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder',
            {'_quality_native_available_units', '_quality_validate_native_checks',
             '_quality_validate_native_unit_checks'})
        self.wo, self.source = cls(), cls()
        self.wo.ensure_one = Mock()
        self.units = Rows(Record(id=index, display_name=str(index)) for index in range(1, 6))
        self.samples = Rows(Record(id=unit.id, controlled_unit_id=unit,
            sample_result='fail' if unit.id == 5 else 'pass', held=False) for unit in self.units)
        self.check = Record(id=1, point_id=Record(id=10),
            operation_run_id=Record(id=1, unit_ids=self.units), sample_ids=self.samples,
            _lock_unit_admission_scope=Mock())
        def error(samples):
            sample = samples[0]
            return 'Alert' if sample.held else False if sample.sample_result == 'pass' else sample.sample_result
        self.check._get_unit_admission_error = error
        self.check.unit_admission_ids = Rows(Record(id=unit.id, controlled_unit_id=unit,
            state='confirmed', _get_effectiveness_error=lambda: False) for unit in self.units)
        self.source._quality_native_checks = Mock(return_value=Rows([self.check]))
        self.source._quality_native_history = lambda: Rows(Record(id=unit.id, state='processed',
            controlled_unit_id=unit) for unit in self.units)
        self.source.state, self.source.display_name = 'progress', 'WO2'
        self.wo._quality_native_predecessors = lambda: [self.source]

    def test_four_pass_are_automatic_and_reads_do_not_lock_or_write(self):
        allowed, reasons = self.wo._quality_native_available_units(self.units)
        self.assertEqual(allowed.ids, [1, 2, 3, 4])
        self.assertEqual(reasons, {5: 'fail'})
        self.check._lock_unit_admission_scope.assert_not_called()

    def test_pending_and_alert_stay_held_until_both_are_resolved(self):
        for result, held in (('pending', False), ('pass', True)):
            self.samples[-1].sample_result, self.samples[-1].held = result, held
            allowed, reasons = self.wo._quality_native_available_units(self.units)
            self.assertNotIn(5, allowed.ids)
            self.assertIn(5, reasons)
        self.samples[-1].held = False
        self.assertEqual(self.wo._quality_native_available_units(self.units)[0].ids, self.units.ids)

    def test_missing_qcp_and_hidden_evidence_are_not_dropped(self):
        for error in (UserError('Missing QCP'), AccessError('Hidden QC')):
            self.source._quality_native_checks.side_effect = error
            with self.subTest(error=str(error)), self.assertRaises(type(error)):
                self.wo._quality_native_available_units(self.units)

    def test_active_batch_does_not_expand_when_fifth_passes(self):
        wo = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder',
                           {'_quality_native_units'})()
        production = Record(id=99, _full_release_units=lambda: self.units)
        wo.ensure_one, wo.production_id = Mock(), production
        wo.company_id, wo.product_id = 1, 2
        for unit in self.units:
            unit.company_id, unit.product_id = 1, 2
            unit._quality_current_production = lambda: production
        wo.quality_native_unit_ids = Rows(self.units[:4])
        wo._quality_native_automatic = lambda: True
        wo.time_ids = Rows([Record(id=1, date_end=False)])
        wo._quality_native_available_units = lambda universe: (universe, {})
        self.assertEqual(wo._quality_native_units().ids, [1, 2, 3, 4])
        wo.time_ids = Rows()
        self.assertEqual(wo._quality_native_units().ids, [1, 2, 3, 4, 5])


class TestNativeFinishAcknowledgement(unittest.TestCase):
    def setUp(self):
        self.wo = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder',
            {'_quality_confirm_on_native_finish'}, native_operation_allows=lambda production: False)()
        wo = self.wo
        wo.ensure_one, wo._quality_native_automatic = Mock(), lambda: True
        wo.state = 'progress'
        units = Rows([Record(id=1)])
        wo.production_id = NS(_full_release_units=lambda: units, _check_quality_transfer_boundary=Mock())
        wo._quality_native_units = lambda: units
        wo._quality_native_history = lambda: Rows()
        wo._quality_native_checks = Mock()
        wo.action_confirm_quality_native_units, wo.button_pending = Mock(), Mock()

    def test_missing_own_qc_records_physical_work_but_does_not_close_wo(self):
        self.wo._quality_native_checks.side_effect = UserError('Missing QCP')
        self.assertTrue(self.wo._quality_confirm_on_native_finish())
        self.wo.action_confirm_quality_native_units.assert_called_once()
        self.wo.button_pending.assert_called_once()
        self.assertEqual(self.wo.state, 'progress')

    def test_hidden_qc_does_not_grant_permission_to_record_work(self):
        self.wo._quality_native_checks.side_effect = AccessError('Hidden QC')
        with self.assertRaises(AccessError):
            self.wo._quality_confirm_on_native_finish()
        self.wo.action_confirm_quality_native_units.assert_not_called()
        self.wo.button_pending.assert_not_called()


if __name__ == '__main__':
    unittest.main()
