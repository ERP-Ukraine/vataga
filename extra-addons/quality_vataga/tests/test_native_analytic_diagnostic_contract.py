"""Exact reporting semantics; these dictionaries do not emulate Enterprise ORM."""
import copy
import importlib.util
from pathlib import Path
import unittest


path = Path(__file__).resolve().parents[1] / 'models/native_analytic_diagnostic.py'
spec = importlib.util.spec_from_file_location('native_analytic_diagnostic_contract_source', path)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class TestAnalyticDiagnosticContract(unittest.TestCase):
    def setUp(self):
        self.old = {'analytic_lines': [{'id': 17, 'amount': -60, 'unit_amount': 1}],
                    'analytic_relations': {'wc': [17]}, 'duration': 60, 'native_cost': 90,
                    'basis': {'employee_rates': {'10': 20}},
                    'stored_after_cost_reads': {'analytic_lines': [{'id': 17, 'amount': -60}]}}

    def test_exact_amount_and_hours_changes_are_matched_by_line_id(self):
        new = copy.deepcopy(self.old)
        new['analytic_lines'][0].update(amount=-75, unit_amount=1.25)
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertFalse(change['equal'])
        self.assertEqual(change['analytic_lines']['added'], [])
        self.assertEqual(change['analytic_lines']['removed'], [])
        self.assertEqual(change['analytic_lines']['changed_by_id']['17'], [
            {'path': '/amount', 'before': -60, 'after': -75},
            {'path': '/unit_amount', 'before': 1, 'after': 1.25}])

    def test_added_removed_lines_and_relations_are_not_hidden(self):
        new = copy.deepcopy(self.old)
        new['analytic_lines'] = [{'id': 24, 'amount': -60, 'unit_amount': 1}]
        new['analytic_relations']['wc'] = [24]
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertEqual(change['analytic_lines']['before_ids'], [17])
        self.assertEqual(change['analytic_lines']['after_ids'], [24])
        self.assertEqual([row['id'] for row in change['analytic_lines']['removed']], [17])
        self.assertEqual([row['id'] for row in change['analytic_lines']['added']], [24])
        self.assertIn('analytic_relations', change['sections'])

    def test_reordering_does_not_fabricate_per_line_value_changes_or_pass(self):
        self.old['analytic_lines'].append({'id': 18, 'amount': -30, 'unit_amount': 1})
        new = copy.deepcopy(self.old)
        new['analytic_lines'].reverse()
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertFalse(change['equal'])
        self.assertEqual(change['changed_sections'], ['analytic_lines'])
        self.assertEqual(change['analytic_lines']['changed_by_id'], {})
        self.assertEqual(change['analytic_lines']['after_ids'], [18, 17])

    def test_computed_only_changes_remain_failures_with_stored_values_unchanged(self):
        self.old['analytic_lines'][0]['unit_quantity_applicable'] = False
        new = copy.deepcopy(self.old)
        new['analytic_lines'][0]['unit_quantity_applicable'] = True
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertFalse(change['equal'])
        self.assertEqual(change['changed_sections'], ['analytic_lines'])
        self.assertNotIn('stored_after_cost_reads', change['sections'])
        self.assertEqual(change['analytic_lines']['changed_by_id']['17'][0]['path'], '/unit_quantity_applicable')

    def test_basis_changes_are_reported_even_when_total_cost_is_unchanged(self):
        new = copy.deepcopy(self.old)
        new['basis']['employee_rates']['10'] = 30
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertEqual(change['changed_sections'], ['basis'])
        self.assertEqual(change['sections']['basis'], [
            {'path': '/basis/employee_rates/10', 'before': 20, 'after': 30}])

    def test_missing_key_is_distinct_from_explicit_none(self):
        new = copy.deepcopy(self.old)
        new['equipment_cost'] = None
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertFalse(change['equal'])
        self.assertEqual(change['sections']['equipment_cost'][0]['before_present'], False)
        self.assertEqual(change['sections']['equipment_cost'][0]['after_present'], True)

    def test_comparison_does_not_mutate_inputs_or_hide_small_numeric_changes(self):
        old_copy = copy.deepcopy(self.old)
        new = copy.deepcopy(self.old)
        new['duration'] += 1e-9
        change = diagnostic.analytic_state_difference(self.old, new)
        self.assertEqual(self.old, old_copy)
        self.assertFalse(change['equal'])
        self.assertEqual(change['changed_sections'], ['duration'])

    def test_returned_differences_are_detached_from_mutable_input(self):
        new = copy.deepcopy(self.old)
        new['basis']['employee_rates']['20'] = 40
        change = diagnostic.analytic_state_difference(self.old, new)
        new['basis']['employee_rates']['20'] = 50
        self.assertEqual(change['sections']['basis'][0]['after'], 40)
