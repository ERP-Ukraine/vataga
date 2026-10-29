"""Portable call-contract tests; these do not emulate the Enterprise ORM.

Run directly with Python. Only the shipped overrides are extracted; the parent
implements the button_start(bypass=...) call reported in the staging traceback.
The real do_pass chain is covered by test_quality_unit_transfer on Enterprise.
"""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


def override_class(path, name, method, parent):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    cls.bases = [ast.Name(id='Parent', ctx=ast.Load())]
    cls.decorator_list = []
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method]
    namespace = {'Parent': parent}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(ROOT / path), 'exec'), namespace)
    return namespace[name]


class NativeWorkorder:
    def button_start(self, bypass=False):
        self.calls.append(('super', bypass))
        return self.result


class ReportedEnterpriseQualityCheck:
    def do_pass(self):
        self.workorder_id.button_start(bypass=True)
        return 'pass'


Workorder = override_class('models/quality_transfer_mrp.py', 'MrpWorkorderTransferBoundary', 'button_start', NativeWorkorder)
QualityCheck = override_class('models/quality_check.py', 'QualityCheck', 'do_pass', ReportedEnterpriseQualityCheck)


class TestWorkorderStartContract(unittest.TestCase):
    def setUp(self):
        self.workorder = Workorder()
        self.workorder.calls = []
        self.workorder.result = object()
        self.workorder.production_id = Mock()
        self.workorder.production_id._check_quality_transfer_boundary.side_effect = lambda: self.workorder.calls.append(('boundary',))

    def test_default_start_checks_boundary_and_forwards_false(self):
        self.assertIs(self.workorder.button_start(), self.workorder.result)
        self.assertEqual(self.workorder.calls, [('boundary',), ('super', False)])

    def test_explicit_bypass_is_forwarded_unchanged(self):
        for bypass in (False, True):
            with self.subTest(bypass=bypass):
                self.workorder.calls.clear()
                self.assertIs(self.workorder.button_start(bypass=bypass), self.workorder.result)
                self.assertEqual(self.workorder.calls, [('boundary',), ('super', bypass)])

    def test_successful_qc_parent_can_start_with_bypass(self):
        check = QualityCheck()
        check.workorder_id = self.workorder
        check._validate_measurement_can_pass = Mock()
        check._snapshot_selected_equipment = Mock()
        self.assertEqual(check.do_pass(), 'pass')
        check._validate_measurement_can_pass.assert_called_once_with()
        check._snapshot_selected_equipment.assert_called_once_with()
        self.assertEqual(self.workorder.calls, [('boundary',), ('super', True)])

    def test_boundary_error_blocks_super_for_every_call_form(self):
        self.workorder.production_id._check_quality_transfer_boundary.side_effect = PermissionError('protected route')
        for args, kwargs in (((), {}), ((), {'bypass': False}), ((), {'bypass': True}), ((True,), {})):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaisesRegex(PermissionError, 'protected route'):
                self.workorder.button_start(*args, **kwargs)
        self.assertEqual(self.workorder.calls, [])


if __name__ == '__main__':
    unittest.main()
