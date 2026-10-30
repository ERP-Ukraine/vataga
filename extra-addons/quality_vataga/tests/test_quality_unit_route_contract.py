"""Portable tests of shipped policy methods and boundary wiring, without ORM.

The small recordset fixtures model call contracts only. Run the real production,
stock, identity, admission, repair and Alert scenarios on disposable Enterprise.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class PolicyError(Exception):
    pass


def load_class(path, name, methods, parent, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    cls.bases = [ast.Name(id='Parent', ctx=ast.Load())]
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    for node in cls.body:
        node.decorator_list = []
    scope = dict(namespace, Parent=parent)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(ROOT / path), 'exec'), scope)
    return scope[name]


class ProductionParent:
    def __init__(self):
        self.quality_unit_route_required = False
        self.state = 'progress'
        self.qty_produced = 0
        self.check_access_rights = Mock()
        self.check_access_rule = Mock()
        self._lock_quality_transfer_scope = Mock()
        self.message_post = Mock()
        self.parent_writes = []

    def __iter__(self):
        return iter([self])

    def sorted(self, key):
        return self

    def write(self, vals):
        self.parent_writes.append(vals)
        for key, value in vals.items():
            setattr(self, key, value)
        return True


class TestQualityUnitRouteContract(unittest.TestCase):
    def setUp(self):
        self.native = Mock(return_value=False)
        self.namespace = {'native_operation_allows': self.native,
                          'UserError': PolicyError, '_': lambda text: text,
                          'REQUIRED_ROUTE_MESSAGE': 'required unit route'}
        self.Production = load_class('models/quality_unit_route_policy.py', 'ProductionUnitRoutePolicy',
            {'_check_required_unit_route_completion', '_enable_required_unit_route', 'write'},
            ProductionParent, self.namespace)
        self.production = self.Production()

    def test_standard_policy_does_not_require_identity_or_admission(self):
        self.production._check_required_unit_route_completion()
        self.assertTrue(self.production.write({'state': 'done'}))

    def test_required_policy_blocks_standard_completion_without_capability(self):
        self.production.quality_unit_route_required = True
        with self.assertRaises(PolicyError):
            self.production._check_required_unit_route_completion()
        self.assertFalse(self.production.parent_writes)

    def test_completion_rechecks_policy_after_shared_lock(self):
        self.production._lock_quality_transfer_scope.side_effect = lambda: setattr(
            self.production, 'quality_unit_route_required', True)
        with self.assertRaises(PolicyError):
            self.production._check_required_unit_route_completion()

    def test_direct_done_and_produced_quantity_writes_cannot_bypass(self):
        self.production.quality_unit_route_required = True
        for vals in ({'state': 'done'}, {'qty_produced': 5}):
            with self.subTest(vals=vals), self.assertRaises(PolicyError):
                self.production.write(vals)
        self.assertFalse(self.production.parent_writes)

    def test_enable_is_sticky_and_performs_no_quantity_or_identity_write(self):
        self.production._enable_required_unit_route()
        self.assertTrue(self.production.quality_unit_route_required)
        self.assertEqual(self.production.parent_writes, [{'quality_unit_route_required': True}])
        self.production.message_post.assert_called_once()
        for value in (False, True):
            with self.assertRaises(PolicyError):
                self.production.write({'quality_unit_route_required': value})

    def test_history_is_rejected_before_lock_or_write(self):
        for state, produced in (('done', 5), ('cancel', 0), ('progress', 1)):
            self.production.state, self.production.qty_produced = state, produced
            with self.subTest(state=state), self.assertRaises(PolicyError):
                self.production._enable_required_unit_route()
        self.production._lock_quality_transfer_scope.assert_not_called()
        self.assertFalse(self.production.parent_writes)
        self.production.message_post.assert_not_called()

    def test_validated_private_native_operation_retains_its_existing_path(self):
        self.production.quality_unit_route_required = True
        self.native.return_value = True
        self.production._check_required_unit_route_completion()
        self.production._lock_quality_transfer_scope.assert_not_called()

    def test_future_workorder_qc_does_not_invoke_final_unit_route_barrier(self):
        cls = load_class('models/quality_matrix_hold.py', 'MatrixNativeCompletion',
                         {'_check_matrix_native_completion'}, object, {})
        scope = cls()
        scope.sorted = Mock(return_value=[])
        scope._check_required_unit_route_completion = Mock()
        scope._check_matrix_native_completion(workorders=object())
        scope._check_required_unit_route_completion.assert_not_called()
        scope._check_matrix_native_completion()
        scope._check_required_unit_route_completion.assert_called_once_with()

    def test_qcp_and_mo_default_to_standard_without_using_admission_role(self):
        tree = ast.parse((ROOT / 'models/quality_unit_route_policy.py').read_text(encoding='utf-8'))
        fields = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'quality_unit_route_required' for target in node.targets)]
        self.assertEqual(len(fields), 2)
        for node in fields:
            self.assertTrue(any(key.arg == 'default' and key.value.value is False for key in node.value.keywords))
        text = (ROOT / 'models/quality_unit_route_policy.py').read_text(encoding='utf-8')
        self.assertNotIn('unit_admission_role', ast.dump(tree))
        self.assertNotIn('sudo(', text)
        self.assertNotIn('env.context.get', text)

    def test_new_configured_qc_activates_its_mo_before_any_transfer(self):
        for required in (False, True):
            with self.subTest(required=required):
                production = self.Production()
                check = SimpleNamespace(point_id=SimpleNamespace(quality_unit_route_required=required),
                                        _identity_production=Mock(return_value=production))
                class Parent:
                    def create(self, vals_list):
                        return [check]
                cls = load_class('models/quality_unit_route_policy.py', 'QualityCheckUnitRoutePolicy',
                                 {'create'}, Parent, {'ValidationError': PolicyError, '_': lambda text: text})
                self.assertEqual(cls().create([{}]), [check])
                self.assertEqual(production.quality_unit_route_required, required)
                self.assertEqual(production.qty_produced, 0)

    def test_required_qc_without_mo_is_rejected(self):
        check = SimpleNamespace(point_id=SimpleNamespace(quality_unit_route_required=True),
                                _identity_production=Mock(return_value=None))
        class Parent:
            def create(self, vals_list):
                return [check]
        cls = load_class('models/quality_unit_route_policy.py', 'QualityCheckUnitRoutePolicy',
                         {'create'}, Parent, {'ValidationError': PolicyError, '_': lambda text: text})
        with self.assertRaises(PolicyError):
            cls().create([{}])

    def _stock_line(self, state='confirmed', context=None):
        production = self.production
        production.quality_unit_route_required = True
        production._check_quality_transfer_boundary = Mock()
        class Moves(list):
            def __or__(self, other):
                return Moves(list(self) + list(other))
            def filtered(self, predicate):
                return Moves(item for item in self if predicate(item))
        moves = Moves([SimpleNamespace(state=state)])
        class Env:
            def __init__(self):
                self.context = context or {}
            def __getitem__(self, name):
                return SimpleNamespace(browse=lambda value: moves if value else Moves())
        class Parent:
            def create(self, vals_list):
                return 'native create'
            def write(self, vals):
                return 'native write'
            def unlink(self):
                return 'native unlink'
        cls = load_class('models/quality_transfer_mrp.py', 'StockMoveLineTransferBoundary',
                         {'create', 'write', 'unlink'}, Parent, {})
        line = cls()
        line.env, line.move_id = Env(), moves
        line.browse = lambda: line
        line._quality_transfer_productions = Mock(return_value=production)
        return line

    def test_required_route_still_allows_pending_reservation_lines(self):
        line = self._stock_line()
        self.assertEqual(line.create([{'move_id': 1, 'quantity': 1}]), 'native create')
        self.assertEqual(line.write({'quantity': 2}), 'native write')
        self.assertEqual(line.unlink(), 'native unlink')
        self.production._lock_quality_transfer_scope.assert_not_called()

    def test_required_route_blocks_posted_line_create_write_and_unlink(self):
        line = self._stock_line(state='done')
        for call in (lambda: line.create([{'move_id': 1, 'quantity': 1}]),
                     lambda: line.write({'quantity': 2}), line.unlink):
            with self.subTest(call=call), self.assertRaises(PolicyError):
                call()

    def test_required_route_blocks_pending_line_detachment_and_default_done_state(self):
        line = self._stock_line(context={'default_state': 'done'})
        with self.assertRaises(PolicyError):
            line.create([{'move_id': 1, 'quantity': 1}])
        with self.assertRaises(PolicyError):
            line.write({'move_id': False})

    def test_stock_move_create_checks_rpc_default_done_state(self):
        self.production.quality_unit_route_required = True
        self.production._check_matrix_native_completion = self.production._check_required_unit_route_completion
        self.production._check_quality_transfer_boundary = Mock()
        class Parent:
            def create(self, vals_list):
                return 'native create'
        cls = load_class('models/quality_transfer_mrp.py', 'StockMoveTransferBoundary',
                         {'create'}, Parent, {})
        move = cls()
        move.env = SimpleNamespace(context={'default_state': 'done'})
        move.browse = lambda: move
        move._quality_transfer_productions = Mock(return_value=self.production)
        with self.assertRaises(PolicyError):
            move.create([{'production_id': 1}])

    def test_stock_line_create_write_and_unlink_have_required_route_guard(self):
        tree = ast.parse((ROOT / 'models/quality_transfer_mrp.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'StockMoveLineTransferBoundary')
        for name in ('create', 'write', 'unlink'):
            method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
            calls = [node.func.attr for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
            self.assertIn('_check_required_unit_route_completion', calls, name)

    def test_impact_report_is_explicitly_read_only(self):
        text = (ROOT / 'scripts/quality_unit_route_impact.sql').read_text(encoding='utf-8').upper()
        self.assertIn('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;', text)
        self.assertTrue(text.rstrip().endswith('ROLLBACK;'))
        for mutation in ('UPDATE ', 'DELETE ', 'INSERT ', 'ALTER ', 'DROP ', 'TRUNCATE ', 'CREATE '):
            self.assertNotIn(mutation, text)

    def test_public_full_release_is_gated_and_experimental_engine_stays_closed(self):
        tree = ast.parse((ROOT / 'models/quality_native_backorder.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'action_open_quality_native_release')
        calls = [node.func.attr for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertIn('_check_full_release_test_access', calls)
        self.assertNotIn('create', calls)
        tree = ast.parse((ROOT / 'models/quality_stock_posting.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '_require_enterprise_validation')
        self.assertEqual(method.body[0].value.func.id, 'archive_disabled')


if __name__ == '__main__':
    unittest.main()
