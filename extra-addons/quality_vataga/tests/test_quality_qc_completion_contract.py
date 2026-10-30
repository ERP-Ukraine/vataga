"""Portable behavioral checks of the shipped admission validator, without ORM."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class Denied(Exception):
    pass


class Rows(list):
    def filtered(self, predicate):
        return Rows(item for item in self if predicate(item))

    @property
    def sample_result(self):
        return self[0].sample_result


def validator():
    path = Path(__file__).resolve().parents[1] / 'models/quality_check_unit_admission.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'QualityCheckUnitAdmission')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '_get_unit_admission_error')
    namespace = {
        '_': lambda message, **values: message % values if values else message,
        'AccessError': Denied, 'UserError': ValueError, 'ValidationError': ValueError,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[method.name]


class TestQcCompletionContract(unittest.TestCase):
    def setUp(self):
        self.check = SimpleNamespace(ensure_one=lambda: None,
                                    _get_unit_admission_structure_error=lambda: False)
        self.sample = SimpleNamespace(quality_check_id=self.check, controlled_unit_id=11, sample_result='pass')
        self.required = SimpleNamespace(quality_state='pass', display_name='Mandatory QC',
                                       _get_unit_admission_structure_error=lambda: False,
                                       sample_ids=Rows([self.sample]))
        self.check._unit_admission_required_checks = lambda unit=None: [self.required]
        self.validate = validator()

    def test_complete_pass_is_eligible(self):
        self.assertFalse(self.validate(self.check, self.sample))

    def test_pass_inputs_in_open_mandatory_qc_are_not_permission(self):
        self.required.quality_state = 'none'
        self.assertTrue(self.validate(self.check, self.sample))

    def test_mixed_completed_qc_can_admit_its_pass_unit(self):
        self.required.quality_state = 'fail'
        self.assertFalse(self.validate(self.check, self.sample))

    def test_fail_and_pending_units_are_held(self):
        for result in ('pending', 'fail'):
            self.sample.sample_result = result
            self.assertTrue(self.validate(self.check, self.sample))

    def test_missing_or_other_physical_unit_is_held(self):
        self.required.sample_ids = Rows([SimpleNamespace(controlled_unit_id=12, sample_result='pass')])
        self.assertTrue(self.validate(self.check, self.sample))

    def test_hidden_required_qc_cannot_disappear_from_validation(self):
        def hidden(unit=None):
            raise Denied()
        self.check._unit_admission_required_checks = hidden
        self.assertTrue(self.validate(self.check, self.sample))

    def test_invalid_required_structure_is_held(self):
        self.required._get_unit_admission_structure_error = lambda: 'Missing equipment'
        self.assertTrue(self.validate(self.check, self.sample))

    def test_foreign_sample_is_rejected(self):
        self.sample.quality_check_id = object()
        self.assertTrue(self.validate(self.check, self.sample))


if __name__ == '__main__':
    unittest.main()
