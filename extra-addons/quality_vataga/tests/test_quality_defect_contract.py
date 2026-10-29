"""Portable regression contracts; no Enterprise ORM or stock execution.

Execute shipped hold SQL against an in-memory relational fixture. SQLite tests
predicate selection only: PostgreSQL locking, ACL integration, quants, valuation
and accounting require the disposable Enterprise scenarios.
"""
import ast
from contextlib import nullcontext
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class AccessError(Exception):
    pass


class UserError(Exception):
    pass


def translate(text, **values):
    return text % values if values else text


def shipped_class(path, name, methods, parent=object, **dependencies):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    cls.bases = [ast.Name(id='Parent', ctx=ast.Load())]
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in methods]
    for node in cls.body:
        node.decorator_list = []
    scope = dict(dependencies, Parent=parent, _=translate, AccessError=AccessError,
                 UserError=UserError, ValidationError=UserError)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(ROOT / path), 'exec'), scope)
    return scope[name]


class AccessFixture:
    check_access_rights = Mock()
    check_access_rule = Mock()


class Alerts(list):
    check_id = AccessFixture()
    def check_access_rights(self, operation):
        if getattr(self, 'hidden', False):
            raise AccessError('hidden Alert')
    def check_access_rule(self, operation):
        self.check_access_rights(operation)


class SqlCursor:
    def __init__(self, connection):
        self.connection = connection
    def execute(self, sql, params):
        parts, bound = sql.split('%s'), []
        converted = parts[0]
        for value, suffix in zip(params, parts[1:]):
            if isinstance(value, tuple):
                converted += '(' + ','.join('?' for _ in value) + ')'
                bound.extend(value)
            else:
                converted += '?'
                bound.append(value)
            converted += suffix
        self.cursor = self.connection.execute(converted, bound)
    def fetchall(self):
        return self.cursor.fetchall()


class TestAddressedAlertHoldContract(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE quality_point (id INTEGER PRIMARY KEY, x_control_stage TEXT);
            CREATE TABLE quality_check (id INTEGER PRIMARY KEY, point_id INTEGER, workorder_id INTEGER, operation_run_id INTEGER);
            CREATE TABLE quality_production_run (id INTEGER PRIMARY KEY, workorder_id INTEGER);
            CREATE TABLE quality_check_sample (id INTEGER PRIMARY KEY, quality_check_id INTEGER, controlled_unit_id INTEGER);
            CREATE TABLE quality_alert (id INTEGER PRIMARY KEY, check_id INTEGER, is_open INTEGER);
            CREATE TABLE quality_alert_failure_sample_rel (alert_id INTEGER, sample_id INTEGER);
            INSERT INTO quality_point VALUES (7, 'in_process'), (8, 'in_process');
            INSERT INTO quality_production_run VALUES (100, 20), (101, 20);
            INSERT INTO quality_check VALUES (10, 7, NULL, 100), (11, 8, 20, 101);
            INSERT INTO quality_check_sample VALUES (1, 10, 501), (2, 11, 501), (3, 10, 502);
            INSERT INTO quality_alert VALUES (1, 10, 1), (2, 10, 1);
            INSERT INTO quality_alert_failure_sample_rel VALUES (1, 1), (2, 1);
        """)
        self.hidden = False
        def alerts(ids):
            records = Alerts()
            records.hidden = self.hidden
            for identifier in ids:
                opened = self.db.execute('SELECT is_open FROM quality_alert WHERE id=?', [identifier]).fetchone()[0]
                scoped = self.db.execute('SELECT 1 FROM quality_alert_failure_sample_rel WHERE alert_id=?', [identifier]).fetchone()
                records.append(NS(display_name='Alert ' + str(identifier), vataga_failure_sample_ids=bool(scoped),
                                  _matrix_hold_is_open=lambda value=opened: bool(value)))
            return records
        class Env:
            cr = SqlCursor(self.db)
            def __getitem__(_env, name):
                return NS(flush_model=Mock(), browse=alerts)
        cls = shipped_class('models/quality_matrix_hold.py', 'QualityMatrixHoldCheck', {'_matrix_alert_hold_error'})
        self.check = cls()
        self.check.id, self.check.point_id = 11, NS(id=8)
        self.check.ensure_one = Mock()
        self.check.check_access_rights = Mock()
        self.check.check_access_rule = Mock()
        self.check.env, self.check._fields = Env(), {'workorder_id'}
        workorder = NS(ids=[20], quality_native_parent_workorder_id=False,
                       check_access_rights=Mock(), check_access_rule=Mock())
        self.check.workorder_id = workorder
        self.check.operation_run_id = NS(workorder_id=workorder, check_access_rights=Mock(), check_access_rule=Mock())
        self.sample = NS(id=2, controlled_unit_id=NS(id=501))

    def test_qrun_only_original_qc_still_holds_repeated_qc(self):
        reason = self.check._matrix_alert_hold_error(self.sample)
        self.assertTrue(reason)
        self.assertIn('Alert 1', reason)

    def test_current_qc_can_also_resolve_its_workorder_through_qrun(self):
        self.check.workorder_id = False
        reason = self.check._matrix_alert_hold_error(self.sample)
        self.assertTrue(reason)
        self.assertIn('Alert 1', reason)

    def test_direct_workorder_history_remains_supported(self):
        self.db.execute('UPDATE quality_check SET workorder_id=20 WHERE id=10')
        self.assertTrue(self.check._matrix_alert_hold_error(self.sample))

    def test_closing_one_alert_does_not_release_another_hold(self):
        self.db.execute('UPDATE quality_alert SET is_open=0 WHERE id=1')
        self.assertIn('Alert 2', self.check._matrix_alert_hold_error(self.sample))
        self.db.execute('UPDATE quality_alert SET is_open=0 WHERE id=2')
        self.assertFalse(self.check._matrix_alert_hold_error(self.sample))

    def test_healthy_unit_is_not_held_by_another_units_scoped_alert(self):
        self.sample.controlled_unit_id.id = 502
        self.sample.id = 3
        self.assertFalse(self.check._matrix_alert_hold_error(self.sample))

    def test_unscoped_open_alert_remains_conservative(self):
        self.db.execute('DELETE FROM quality_alert_failure_sample_rel WHERE alert_id=1')
        self.assertIn('Alert 1', self.check._matrix_alert_hold_error(self.sample))

    def test_other_operation_is_not_mistaken_for_this_workorder(self):
        self.db.execute('UPDATE quality_production_run SET workorder_id=99 WHERE id=100')
        self.assertFalse(self.check._matrix_alert_hold_error(self.sample))

    def test_hidden_alert_is_rejected_instead_of_disappearing(self):
        self.hidden = True
        with self.assertRaises(AccessError):
            self.check._matrix_alert_hold_error(self.sample)


class SampleParent:
    def __iter__(self):
        return iter([self])
    def write(self, vals):
        for key, value in vals.items():
            setattr(self, key, value)


class TestReworkTransitionContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_check_sample.py', 'QualityCheckSample',
                            {'_record_rework_transitions'}, SampleParent)
        self.sample = cls()
        self.sample.id, self.sample.sample_number = 1, 1
        self.sample.sample_result, self.sample.x_was_reworked = 'pass', False
        self.check = NS(quality_state='none', x_control_stage='in_process', message_post=Mock())
        self.check.sudo = lambda enabled: self.check
        self.sample.quality_check_id = self.check

    def test_fail_to_pass_sets_flag_and_posts_exact_chatter_once(self):
        self.sample._record_rework_transitions({1: 'fail'})
        self.assertTrue(self.sample.x_was_reworked)
        self.check.message_post.assert_called_once()
        self.assertIn('№1', self.check.message_post.call_args.kwargs['body'])
        self.sample._record_rework_transitions({1: 'pass'})
        self.check.message_post.assert_called_once()

    def test_fail_to_pending_and_repeated_fail_are_not_rework_pass(self):
        for result in ('pending', 'fail'):
            self.sample.sample_result = result
            self.sample._record_rework_transitions({1: 'fail'})
        self.assertFalse(self.sample.x_was_reworked)
        self.check.message_post.assert_not_called()

    def test_closed_qc_cannot_gain_rework_audit(self):
        self.check.quality_state = 'fail'
        self.sample._record_rework_transitions({1: 'fail'})
        self.assertFalse(self.sample.x_was_reworked)
        self.check.message_post.assert_not_called()

    def test_later_real_rework_preserves_flag_and_adds_its_own_history(self):
        self.sample._record_rework_transitions({1: 'fail'})
        self.sample._record_rework_transitions({1: 'pass'})
        self.sample._record_rework_transitions({1: 'fail'})
        self.assertTrue(self.sample.x_was_reworked)
        self.assertEqual(self.check.message_post.call_count, 2)


class RepairParent:
    def write(self, vals):
        self.audit_writes.append(vals)
        for key, value in vals.items():
            setattr(self, key, value)


class TestRepairConfirmationContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_alert_repair.py', 'QualityAlertRepair',
            {'_check_repair_action', 'action_record_repair'}, RepairParent,
            BOARD_STAGE_XMLIDS=('analysis', 'approval', 'execution'),
            fields=NS(Datetime=NS(now=lambda: '2026-10-29 10:00:00')), escape=lambda value: value)
        self.alert = cls()
        self.alert.ensure_one = Mock()
        self.alert.check_access_rights = Mock()
        self.alert.check_access_rule = Mock()
        self.alert._lock_board_records = Mock()
        self.alert.audit_writes = []
        self.alert.env = NS(uid=7, ref=lambda xmlid: xmlid,
            user=NS(display_name='Inspector', has_group=Mock(return_value=True)),
            cr=NS(savepoint=nullcontext))
        self.alert.decision_type, self.alert.stage_id = 'repair', 'execution'
        self.alert.is_approved, self.alert.approved_by_id, self.alert.approved_at = True, 7, 'approved'
        self.alert.vataga_repair_done_at = False
        self.alert.vataga_repair_done_by_id = False
        self.alert.vataga_repair_note = False
        self.alert.message_post = Mock()
        self.alert._repair_scope = Mock(return_value=NS(controlled_unit_id=NS(mapped=lambda name: ['QUNIT-5'])))
        self.production = NS(state='progress', quality_native_parent_id=False)
        self.workorder = NS(state='progress', time_ids=[])
        self.alert._repair_target = Mock(return_value=(self.production, self.workorder))

    def test_same_confirmation_records_one_audit_and_one_message(self):
        self.assertTrue(self.alert.action_record_repair('Actual repair'))
        timestamp = self.alert.vataga_repair_done_at
        self.assertTrue(self.alert.action_record_repair('  Actual repair  '))
        self.assertEqual(self.alert.vataga_repair_done_at, timestamp)
        self.assertEqual(len(self.alert.audit_writes), 1)
        self.assertEqual(set(self.alert.audit_writes[0]),
                         {'vataga_repair_done_by_id', 'vataga_repair_done_at', 'vataga_repair_note'})
        self.alert.message_post.assert_called_once()
        self.assertIn('QUNIT-5', self.alert.message_post.call_args.kwargs['body'])

    def test_confirmed_repair_cannot_be_overwritten_with_another_note(self):
        self.alert.action_record_repair('Actual repair')
        with self.assertRaises(UserError):
            self.alert.action_record_repair('Different facts')
        self.assertEqual(len(self.alert.audit_writes), 1)
        self.assertEqual(self.alert.vataga_repair_note, 'Actual repair')

    def test_missing_approval_wrong_decision_and_empty_note_create_no_repair(self):
        for name, value in (('is_approved', False), ('decision_type', 'scrap'), ('stage_id', 'analysis')):
            original = getattr(self.alert, name)
            setattr(self.alert, name, value)
            with self.subTest(name=name), self.assertRaises(UserError):
                self.alert.action_record_repair('Actual repair')
            setattr(self.alert, name, original)
        with self.assertRaises(UserError):
            self.alert.action_record_repair(' ')
        self.assertFalse(self.alert.audit_writes)
        self.alert.message_post.assert_not_called()

    def test_completed_mo_cannot_be_repaired_by_recording_a_note(self):
        self.production.state = 'done'
        with self.assertRaises(UserError):
            self.alert.action_record_repair('Actual repair')
        self.assertFalse(self.alert.audit_writes)

    def test_inspector_role_is_required_even_with_write_access(self):
        self.alert.env.user.has_group.return_value = False
        with self.assertRaises(AccessError):
            self.alert.action_record_repair('Actual repair')
        self.assertFalse(self.alert.audit_writes)


class Units(list):
    @property
    def ids(self):
        return [unit.id for unit in self]


class Samples(list):
    @property
    def controlled_unit_id(self):
        return Units(sample.controlled_unit_id for sample in self)


class TestRepairResolutionContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_alert_repair.py', 'QualityAlertRepair',
                            {'_validate_repair_check', '_production_resolution_complete'})
        self.alert = cls()
        self.alert.ensure_one = Mock()
        self.alert.vataga_repair_done_at, self.alert.vataga_repair_done_by_id = 'recorded', 7
        self.alert.vataga_repair_note, self.alert.decision_type = 'Actual repair', 'repair'
        self.alert.is_approved, self.alert.approved_by_id, self.alert.approved_at = True, 7, 'approved'
        workorder, point = NS(id=20), NS(id=7)
        production = NS(_quality_equivalent_workorder=lambda original: workorder)
        unit = NS(id=501, _quality_matches_production=lambda candidate: candidate is production)
        samples = Samples([NS(controlled_unit_id=unit)])
        self.alert._repair_scope = Mock(return_value=samples)
        self.original = NS(point_id=point, quality_state='fail', operation_run_id=NS(workorder_id=workorder))
        self.alert.check_id = self.original
        self.repeat = NS(vataga_repair_alert_id=self.alert, quality_state='pass', unit_admission_role='required',
                         x_control_stage='in_process', point_id=point, sample_ids=samples,
                         check_access_rights=Mock(), check_access_rule=Mock(),
                         _identity_production=lambda: production, _validate_measurement_can_pass=Mock())
        self.repeat.operation_run_id = NS(state='registered', workorder_id=workorder,
            origin_check_id=self.repeat, registration_mode='reuse', unit_ids=Units([unit]))
        self.alert.vataga_repair_closed_check_id = self.repeat

    def test_repeat_pass_with_same_unit_can_resolve_without_rewriting_original_fail(self):
        self.assertIs(self.alert._validate_repair_check(self.repeat), self.repeat)
        self.assertTrue(self.alert._production_resolution_complete())
        self.assertEqual(self.original.quality_state, 'fail')
        self.repeat._validate_measurement_can_pass.assert_called()

    def test_repeat_fail_or_pending_cannot_complete_resolution(self):
        for state in ('fail', 'none'):
            self.repeat.quality_state = state
            self.assertFalse(self.alert._production_resolution_complete())
        self.assertEqual(self.original.quality_state, 'fail')

    def test_wrong_unit_or_a_reused_original_run_cannot_close_repair(self):
        original_samples = self.repeat.sample_ids
        self.repeat.sample_ids = Samples([NS(controlled_unit_id=NS(id=999))])
        self.assertFalse(self.alert._production_resolution_complete())
        self.repeat.sample_ids = original_samples
        self.repeat.operation_run_id = self.original.operation_run_id
        self.assertFalse(self.alert._production_resolution_complete())

    def test_missing_actual_repair_or_board_approval_prevents_resolution(self):
        self.alert.vataga_repair_done_at = False
        self.assertFalse(self.alert._production_resolution_complete())
        self.alert.vataga_repair_done_at, self.alert.is_approved = 'recorded', False
        self.assertFalse(self.alert._production_resolution_complete())


class TestScrapClosedPathContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_alert_scrap.py', 'QualityAlertScrap',
            {'_get_scrap_execution_blocker', 'action_scrap_defective_units',
             'action_create_scrap_replacement', '_validate_production_resolution',
             '_production_resolution_complete'},
            BOARD_STAGE_XMLIDS=('analysis', 'approval', 'execution'))
        self.alert = cls()
        self.alert.ensure_one = Mock()
        self.alert.check_access_rights = Mock()
        self.alert.check_access_rule = Mock()
        self.models = {name: NS(check_access_rights=Mock(), create=Mock())
                       for name in ('stock.scrap', 'mrp.production')}
        class Env:
            context = {'default_state': 'done', 'should_replenish': True, 'vataga_scrap_done': True}
            def ref(_env, xmlid):
                return xmlid
            def __getitem__(_env, name):
                return self.models[name]
        self.alert.env = Env()
        self.alert.decision_type, self.alert.stage_id = 'scrap', 'execution'
        self.alert.is_approved, self.alert.approved_by_id, self.alert.approved_at = True, 7, 'approved'
        self.sample = NS(sample_result='fail')
        self.alert._matrix_resolution_scope = Mock(return_value=[self.sample])

    def test_board_decision_and_repeated_actions_create_neither_scrap_nor_replacement(self):
        for attempt in range(2):
            for method in (self.alert.action_scrap_defective_units,
                           self.alert.action_create_scrap_replacement,
                           self.alert._validate_production_resolution):
                with self.subTest(attempt=attempt, method=method.__name__), self.assertRaises(UserError):
                    method()
        self.assertFalse(self.alert._production_resolution_complete())
        for model in self.models.values():
            model.create.assert_not_called()

    def test_pass_cannot_be_substituted_for_selected_fail(self):
        self.sample.sample_result = 'pass'
        status, reason = self.alert._get_scrap_execution_blocker()
        self.assertEqual(status, 'blocked')
        self.assertIn('PASS', reason)
        with self.assertRaises(UserError):
            self.alert.action_scrap_defective_units()

    def test_unapproved_and_wrong_stage_are_not_scrap_proof(self):
        self.alert.is_approved = False
        self.assertEqual(self.alert._get_scrap_execution_blocker()[0], 'approval')
        self.alert.is_approved, self.alert.stage_id = True, 'analysis'
        self.assertEqual(self.alert._get_scrap_execution_blocker()[0], 'blocked')

    def test_missing_identity_scope_is_not_replaced_by_product_stock(self):
        self.alert._matrix_resolution_scope.side_effect = UserError('missing QRUN/QUNIT')
        self.assertEqual(self.alert._get_scrap_execution_blocker(), ('scope', 'missing QRUN/QUNIT'))
        with self.assertRaises(UserError):
            self.alert.action_create_scrap_replacement()
        self.models['mrp.production'].create.assert_not_called()

    def test_inaccessible_company_or_scope_is_rejected(self):
        self.alert._matrix_resolution_scope.side_effect = AccessError('other company')
        with self.assertRaises(AccessError):
            self.alert.action_scrap_defective_units()
        self.models['stock.scrap'].create.assert_not_called()


class Rows(list):
    def filtered(self, predicate):
        return Rows(value for value in self if predicate(value))


class TestCurrentFailureSnapshotContract(unittest.TestCase):
    def test_only_remaining_fail_parameters_are_copied_alongside_manual_text(self):
        tree = ast.parse((ROOT / 'models/measurement_utils.py').read_text(encoding='utf-8'))
        formatter = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'format_number')
        scope = {}
        exec(compile(ast.Module(body=[formatter], type_ignores=[]), 'measurement_utils.py', 'exec'), scope)
        cls = shipped_class('models/quality_check_alert_description.py', 'QualityCheckAlertDescription',
                            {'_get_current_matrix_failure_data', '_format_matrix_failure_data'},
                            format_number=scope['format_number'])
        check = cls()
        check.ensure_one = Mock()
        check.check_access_rights = Mock()
        check.check_access_rule = Mock()
        column = NS(parameter_name='Diameter', parameter_unit='mm', _get_tolerance_label=lambda: '0 – 10')
        bad = NS(result='fail', parameter_type='numeric', numeric_value=11, column_id=column,
                 failure_reason='Outside tolerance')
        corrected = NS(result='pass', parameter_type='numeric', numeric_value=5, column_id=column)
        check.sample_ids = Rows([
            NS(sample_number=1, sample_result='fail', visual_result='yes', measurement_value_ids=Rows([bad])),
            NS(sample_number=2, sample_result='pass', visual_result='yes', measurement_value_ids=Rows([corrected])),
        ])
        failures = check._get_current_matrix_failure_data()
        self.assertEqual([item['sample_number'] for item in failures], [1])
        lines = [check._format_matrix_failure_data([item]) for item in failures]
        cls = shipped_class('models/quality_alert.py', 'QualityAlert', {'_append_matrix_failure_text'})
        alert = cls()
        text = alert._append_matrix_failure_text('Inspector note', lines)
        self.assertIn('Inspector note', text)
        self.assertIn('Diameter', text)
        self.assertIn('11 mm', text)
        self.assertNotIn('№2', text)
        self.assertEqual(alert._append_matrix_failure_text(text, lines), text)


if __name__ == '__main__':
    unittest.main()
