"""Business invariants for immutable inputs, including compensated employee edits."""
import copy
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'models/native_cost_basis.py'
spec = importlib.util.spec_from_file_location('native_cost_basis_contract_source', path)
basis_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(basis_module)


class TestCostBasisContract(unittest.TestCase):
    def setUp(self):
        self.old = {'version': 2, 'workcenter': 1, 'company': 1, 'rate': 60,
            'dependencies': {'native': ['reviewed-source']}, 'employee_contract': 'enterprise_employee_v1',
            'timers': {'1': ['2026-10-30 09:00:00', '2026-10-30 09:30:00', 1, 1, False],
                       '2': ['2026-10-30 09:30:00', '2026-10-30 10:00:00', 1, 1, False]},
            'timer_values': {'1': [30, 'productive', 'productive', 1, 1], '2': [30, 'productive', 'productive', 1, 1]},
            'employee_rates': {'10': 20, '20': 40}, 'employee_companies': {'10': 1, '20': 1},
            'employee_fallback_rate': 50,
            'employee_timers': {'1': [10, 20, 30, 10, 'productive', 1], '2': [20, 40, 30, 20, 'productive', 1]}}

    def test_unchanged_baseline_and_new_employee_work_can_continue(self):
        self.assertIsNone(basis_module.checkpoint_basis_error(copy.deepcopy(self.old), self.old))
        new = copy.deepcopy(self.old)
        new['timers']['3'] = ['2026-10-30 10:00:00', '2026-10-30 10:15:00', 1, 1, False]
        new['timer_values']['3'] = [15, 'productive', 'productive', 1, 1]
        new['employee_rates']['30'] = 30
        new['employee_companies']['30'] = 1
        new['employee_timers']['3'] = [30, 30, 15, 7.5, 'productive', 1]
        self.assertIsNone(basis_module.checkpoint_basis_error(new, self.old))

    def test_equal_total_cannot_hide_compensated_hourly_rate_edits(self):
        new = copy.deepcopy(self.old)
        new['employee_rates'] = {'10': 30, '20': 30}
        new['employee_timers']['1'][1:4] = [30, 30, 15]
        new['employee_timers']['2'][1:4] = [30, 30, 15]
        self.assertEqual(sum(v[3] for v in new['employee_timers'].values()), 30)
        self.assertEqual(sum(v[3] for v in self.old['employee_timers'].values()), 30)
        self.assertIsNotNone(basis_module.checkpoint_basis_error(new, self.old))

    def test_each_booked_input_and_dependency_remains_immutable(self):
        changes = [('employee_rates', '10', 25), ('employee_companies', '10', 2),
            ('employee_timers', '1', [20, 20, 30, 10, 'productive', 1]),
            ('timer_values', '1', [25, 'productive', 'productive', 1, 1]),
            ('dependencies', 'native', ['unreviewed-source'])]
        for section, key, value in changes:
            with self.subTest(section=section):
                new = copy.deepcopy(self.old)
                new[section][key] = value
                self.assertIsNotNone(basis_module.checkpoint_basis_error(new, self.old))

    def test_deleted_timer_fallback_change_and_missing_old_data_are_rejected(self):
        for edit in (lambda v: v['timers'].pop('1'), lambda v: v.pop('employee_timers'),
                     lambda v: v.update(employee_fallback_rate=60), lambda v: v.pop('version')):
            new = copy.deepcopy(self.old)
            edit(new)
            self.assertIsNotNone(basis_module.checkpoint_basis_error(self.old, new))

    def test_appending_work_in_a_booked_interval_is_rejected(self):
        new = copy.deepcopy(self.old)
        new['timers']['3'] = ['2026-10-30 09:45:00', '2026-10-30 10:15:00', 1, 1, False]
        new['timer_values']['3'] = [30, 'productive', 'productive', 1, 1]
        new['employee_timers']['3'] = [10, 20, 30, 10, 'productive', 1]
        self.assertIsNotNone(basis_module.checkpoint_basis_error(new, self.old))
