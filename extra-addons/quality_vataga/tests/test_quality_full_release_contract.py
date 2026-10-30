"""Executable full-batch policy contracts; no fake stock/SVL facts or ORM claim."""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError

ROOT = Path(__file__).resolve().parents[1]


class Record(NS):
    @property
    def ids(self):
        return [self.id]
    def __contains__(self, record):
        return record.id == self.id
    def __eq__(self, other):
        if isinstance(other, Rows):
            return other.ids == self.ids
        return self is other


class Rows(list):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        return Rows(value) if isinstance(key, slice) else value
    @property
    def ids(self):
        return [item.id for item in self]
    @property
    def id(self):
        return self[0].id if self else False
    def filtered(self, predicate):
        return Rows(item for item in self if predicate(item))
    def sorted(self, field):
        return Rows(sorted(self, key=lambda item: getattr(item, field)))
    def __getattr__(self, name):
        values = []
        for item in self:
            value = getattr(item, name)
            values.extend(value if isinstance(value, Rows) else [value])
        if not values or all(isinstance(value, Record) for value in values):
            return Rows(item for index, item in enumerate(values) if item.id not in [old.id for old in values[:index]])
        if len(values) == 1:
            return values[0]
        raise AttributeError(name)
    def __and__(self, other):
        candidates = other.ids
        return Rows(item for item in self if item.id in candidates)
    def _assert_not_stock_posted(self):
        if any(getattr(item, 'posted', False) for item in self):
            raise UserError('already posted')


class TestFullBatchReadinessContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                            {'_validate_quality_full_batch', '_validate_quality_batch_controls', '_check_full_release_test_access'},
                            math=math, _read=lambda records: records)
        production = cls()
        production.id = 1000
        self.production = production
        production.ensure_one = Mock()
        production._lock_quality_transfer_scope = Mock()
        production._check_named_stock_history = Mock()
        production.check_access_rights = Mock()
        production.check_access_rule = Mock()
        production.state, production.qty_produced, production.product_qty = 'progress', 0, 2
        production.quality_unit_route_required, production.quality_transfer_controlled = True, False
        production.quality_full_release_test_database = 'staging_test'
        self.company, self.uom, self.product = Record(id=1), Record(id=2), Record(id=3)
        production.company_id, production.product_uom_id = self.company, self.uom
        production.location_dest_id = Record(id=7, usage='internal', scrap_location=False, company_id=self.company)
        self.product.uom_id = self.uom
        production.product_id = self.product
        self.units = Rows([Record(id=index, display_name='QUNIT ' + str(index), product_id=self.product,
            company_id=self.company, _quality_current_production=lambda: production, posted=False) for index in (501, 502)])
        production._full_release_units = Mock(return_value=self.units)
        self.workorders = Rows([Record(id=index, display_name=name, state='done', qty_produced=2,
            operation_id=Record(id=index), time_ids=Rows()) for index, name in ((11, 'Cutting'), (22, 'Crimping'), (33, 'Inspection'))])
        production._full_release_workorders = Mock(return_value=self.workorders)
        production._capture_full_required_qcp = Mock(return_value={(31, 33, 33)})
        production._applicable_full_qcp = Mock(return_value=set())
        self.run = Record(id=100, workorder_id=self.workorders[-1])
        self.samples = Rows([Record(id=index, controlled_unit_id=unit, sample_result='pass') for index, unit in enumerate(self.units, 1)])
        self.check = Record(id=71, display_name='Final QC', point_id=Record(id=31), operation_run_id=self.run,
            x_control_stage='in_process', unit_admission_role='required', quality_state='pass', sample_ids=self.samples)
        self.check._get_unit_admission_error = lambda sample: False if len(sample) == 1 and sample[0].sample_result == 'pass' else 'not PASS'
        production._full_release_checks = Mock(return_value=Rows([self.check]))
        production._check_full_release_alerts = Mock()
        self.admitted = {unit.id for unit in self.units}
        self.admission_error = False
        def admissions(domain):
            unit_id = next(value for name, operator, value in domain if name == 'controlled_unit_id')
            return Rows([Record(id=unit_id, _get_effectiveness_error=lambda: self.admission_error)]) if unit_id in self.admitted else Rows()
        class Env:
            companies = Rows([self.company])
            cr = NS(dbname='staging_test', execute=Mock(), fetchone=Mock(return_value=None))
            def ref(_env, name):
                return self.uom
            def __getitem__(_env, name):
                if name == 'quality.stock.posting':
                    return NS(flush_model=Mock())
                return NS(search=admissions)
        production.env = Env()

    def test_p1_n9_two_complete_units_need_only_the_final_configured_qc(self):
        self.assertTrue(self.production._validate_quality_full_batch(self.units))
        self.assertFalse(self.production.quality_transfer_controlled)
        self.production._check_full_release_alerts.assert_called_once()

    def test_n1_no_qrun(self):
        self.check.operation_run_id = False
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_n2_qrun_without_full_identity_set(self):
        for units in (Rows(), Rows(self.units[:1])):
            with self.subTest(count=len(units)), self.assertRaises(UserError):
                self.production._validate_quality_full_batch(units)

    def test_n3_missing_or_ineffective_admission(self):
        self.admitted.remove(502)
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)
        self.admitted.add(502)
        self.admission_error = 'revoked or held'
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_n4_n5_pending_or_fail_even_if_operations_are_done(self):
        for state in ('pending', 'fail'):
            self.samples[-1].sample_result = state
            with self.subTest(state=state), self.assertRaises(UserError):
                self.production._validate_quality_full_batch(self.units)

    def test_n6_quality_pass_is_not_operation_completion(self):
        self.workorders[0].state = 'progress'
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_n7_foreign_company_or_mo_unit(self):
        self.units[-1].company_id = Record(id=999)
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)
        self.units[-1].company_id = self.company
        self.units[-1]._quality_current_production = lambda: object()
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_n8_already_posted_unit_is_not_released_again(self):
        self.units[0].posted = True
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_n11_open_alert_is_checked_after_pass_and_admission(self):
        self.production._check_full_release_alerts.side_effect = UserError('Alert reopened')
        with self.assertRaisesRegex(UserError, 'Alert reopened'):
            self.production._validate_quality_full_batch(self.units)

    def test_configured_missing_qc_is_not_treated_as_unnecessary(self):
        self.production._capture_full_required_qcp.return_value.add((99, 33, 33))
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_open_timer_and_ambiguous_quantity_block_release(self):
        self.workorders[1].time_ids = Rows([Record(id=77, date_end=False)])
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)
        self.workorders[1].time_ids = Rows()
        self.production.product_qty = 2.5
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_destination_must_be_actual_internal_stock(self):
        self.production.location_dest_id.usage = 'production'
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_retired_release_never_uses_stored_admin_authorization(self):
        for database in ('staging_test', 'another_database', False):
            self.production.quality_full_release_test_database = database
            with self.assertRaises(UserError):
                self.production._check_full_release_test_access()

    def test_native_full_readiness_does_not_reject_already_consumed_native_components(self):
        self.production._check_named_stock_history.side_effect = AssertionError('blanket native history check')
        self.assertTrue(self.production._validate_quality_full_batch(self.units, standard=True))

    def test_post_validation_requires_done_and_exact_native_quantity(self):
        for state, quantity in (('progress', 2), ('done', 1), ('done', 3)):
            self.production.state, self.production.qty_produced = state, quantity
            with self.assertRaises(UserError):
                self.production._validate_quality_full_batch(self.units, standard=True, posted=True)
        self.production.state, self.production.qty_produced = 'done', 2
        self.assertTrue(self.production._validate_quality_full_batch(self.units, standard=True, posted=True))

    def test_native_release_preserves_historical_transfer_route_barrier(self):
        self.production.quality_transfer_controlled = True
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units, standard=True)

    def test_qc_fail_state_cannot_release_even_if_inputs_now_all_pass(self):
        self.check.quality_state = 'fail'
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units)

    def test_mixed_qc_plus_single_unit_pass_retest_does_not_repeat_good_unit(self):
        self.check.quality_state = 'fail'
        self.samples[-1].sample_result = 'fail'
        sample = Record(id=3, controlled_unit_id=self.units[-1], sample_result='pass')
        repeat = Record(id=72, display_name='One-unit retest', point_id=self.check.point_id,
            operation_run_id=Record(id=101, workorder_id=self.workorders[-1]),
            x_control_stage='in_process', unit_admission_role='required', quality_state='pass',
            sample_ids=Rows([sample]), _get_unit_admission_error=lambda samples: False)
        self.production._full_release_checks.return_value = Rows([self.check, repeat])
        self.assertTrue(self.production._validate_quality_full_batch(self.units, standard=True))
        self.assertEqual(self.samples[-1].sample_result, 'fail')
        self.assertEqual(len(repeat.sample_ids), 1)
        for result in ('fail', 'pending'):
            sample.sample_result = result
            with self.subTest(result=result), self.assertRaises(UserError):
                self.production._validate_quality_full_batch(self.units, standard=True)

    def test_missing_final_qcp_is_not_hidden_by_completed_in_process_qc(self):
        self.production._applicable_full_qcp.return_value = {(99, 33, 33)}
        with self.assertRaises(UserError):
            self.production._validate_quality_full_batch(self.units, standard=True)


class TestFullReleaseRoutingContract(unittest.TestCase):
    def test_actual_qcp_routing_field_limits_requirement_to_third_operation(self):
        cls = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                            {'_applicable_full_qcp'}, _read=lambda records: records)
        production = cls()
        company, category, product, template, picking = [Record(id=value) for value in range(1, 6)]
        category.parent_id = False
        product.categ_id, product.product_tmpl_id = category, template
        production.company_id, production.product_id, production.picking_type_id = company, product, picking
        operations = Rows([Record(id=value) for value in (11, 22, 33)])
        workorders = Rows([Record(id=value, operation_id=operation) for value, operation in enumerate(operations, 1)])
        point = Record(id=31, routing_step=Rows([operations[-1]]), picking_type_ids=Rows([picking]), product_ids=Rows([product]))
        field = lambda model, kind: NS(comodel_name=model, type=kind)
        class Points:
            _fields = {'routing_step': field('mrp.routing.workcenter', 'many2many'),
                       'product_ids': field('product.product', 'many2many')}
            def sudo(self):
                return self
            def search(self, domain):
                return Rows([point])
            def browse(self, ids):
                return Rows([point])
        class Env:
            def __getitem__(self, name):
                return Points() if name == 'quality.point' else Rows()
        production.env = Env()
        # Product and QCP records support the same relation access as ORM.
        Record.__getitem__ = lambda item, name: getattr(item, name)
        Rows.__or__ = lambda items, other: Rows(list(items) + list(other))
        self.assertEqual(production._applicable_full_qcp(workorders), {(31, 33, 3)})
        point.product_ids = Rows([Record(id=999)])
        self.assertFalse(production._applicable_full_qcp(workorders))

    def test_qcp_snapshot_is_sticky_and_cannot_be_cleared_by_rpc_write(self):
        class Parent:
            def write(self, values):
                for name, value in values.items():
                    setattr(self, name, value)
        cls = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
            {'_capture_full_required_qcp', 'write'}, Parent,
            FULL_RELEASE_FIELDS={'quality_full_release_test_database', 'quality_full_required_qcp'})
        production = cls()
        production.ensure_one = Mock()
        production._lock_quality_transfer_scope = Mock()
        production._full_release_workorders = Mock(return_value=Rows())
        production._full_release_checks = Mock(return_value=Rows())
        production._applicable_full_qcp = Mock(return_value={(31, 33, 3)})
        production.quality_full_required_qcp = []
        production._capture_full_required_qcp()
        production._applicable_full_qcp.return_value = set()
        self.assertEqual(production._capture_full_required_qcp(), {(31, 33, 3)})
        for values in ({'quality_full_required_qcp': []}, {'quality_full_release_test_database': 'forged'}):
            with self.assertRaises(UserError):
                production.write(values)

    def test_native_full_batch_does_not_require_backorder_ask(self):
        tree = ast.parse((ROOT / 'models/native_backorder.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '_native_quality_complete')
        condition = next(node.test for node in method.body if isinstance(node, ast.If) and 'create_backorder' in ast.unparse(node.test))
        evaluate = compile(ast.Expression(body=condition), 'native_full_preconditions', 'eval')
        for policy in ('always', 'never', 'ask'):
            production = NS(qty_produced=0, state='progress', picking_type_id=NS(create_backorder=policy),
                            product_id=NS(type='product', tracking='none'), move_byproduct_ids=False, move_raw_ids=Rows())
            self.assertFalse(eval(evaluate, {'self': production, 'operation': NS(quantity=2), 'original_quantity': 2}))
            if policy != 'ask':
                self.assertTrue(eval(evaluate, {'self': production, 'operation': NS(quantity=1), 'original_quantity': 2}))

    def test_wizard_is_retired_even_if_old_confirmation_was_saved(self):
        cls = shipped_class('wizard/quality_full_release_wizard.py', 'QualityFullReleaseWizard', {'action_confirm'})
        wizard = cls()
        wizard.action_apply = Mock(side_effect=UserError('retired'))
        for confirmed in (False, True):
            wizard.confirmed = confirmed
            with self.assertRaises(UserError):
                wizard.action_confirm()
        tree = ast.parse((ROOT / 'wizard/quality_full_release_wizard.py').read_text(encoding='utf-8'))
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        self.assertIn('RetiredDialog', [ast.unparse(base) for base in model.bases])

    def test_scheduled_action_has_no_business_mutations(self):
        tree = ast.parse((ROOT / 'scripts/full_release_scheduled_action.py').read_text(encoding='utf-8'))
        forbidden = {'write', 'create', 'unlink', 'execute', 'action_confirm', 'button_mark_done', '_post_inventory'}
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & forbidden)


if __name__ == '__main__':
    unittest.main()
