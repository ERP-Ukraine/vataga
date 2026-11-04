"""Portable orchestration/rollback checks; not an Enterprise/Stock substitute."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'models/quality_actual_batch_acceptance_probe.py'


class UserError(Exception):
    pass


class AccessError(UserError):
    pass


class ValidationError(UserError):
    pass


class ProbeRollback(Exception):
    pass


def load():
    """Execute the shipped definitions, replacing Odoo imports, never its logic."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom)]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'QualityActualBatchAcceptanceProbe':
            node.bases = [ast.Name(id='object', ctx=ast.Load())]
            for method in node.body:
                if isinstance(method, ast.FunctionDef):
                    method.decorator_list = []
    def check(condition, reason):
        if not condition:
            raise ValidationError(reason)
    scope = {'_': lambda text, **kw: text % kw if kw else text, '_assert': check,
             '_ProbeRollback': ProbeRollback, 'UserError': UserError,
             'AccessError': AccessError, 'ValidationError': ValidationError}
    exec(compile(ast.fix_missing_locations(tree), str(SOURCE), 'exec'), scope)
    return scope


class TestActualAcceptanceGuard(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.env = NS(cr=NS(dbname='odoo17-vataga-staging'), user=NS(has_group=lambda name: True))

    def test_exact_staging_and_admin_are_accepted(self):
        self.scope['_guard'](self.env, 'odoo17-vataga-staging')

    def test_actual_and_supplied_database_must_both_match_hard_allowlist(self):
        for actual, expected in (('production', 'production'), ('production', 'odoo17-vataga-staging'),
                                 ('odoo17-vataga-staging', ''), ('odoo17-vataga-staging', None),
                                 ('odoo17-vataga-staging', 'odoo17-vataga-staging ')):
            self.env.cr.dbname = actual
            with self.subTest(actual=actual, expected=expected), self.assertRaises(AccessError):
                self.scope['_guard'](self.env, expected)

    def test_non_admin_cannot_open_fixture_cursor(self):
        probe = self.scope['QualityActualBatchAcceptanceProbe']()
        self.env.user.has_group = lambda name: False
        self.env.registry = NS(cursor=Mock())
        probe.env = self.env
        with self.assertRaises(AccessError):
            probe._run_quality_actual_batch_acceptance_probe('odoo17-vataga-staging')
        self.env.registry.cursor.assert_not_called()


class TestExactBusinessDenial(unittest.TestCase):
    def setUp(self):
        self.deny = load()['_expected_denial']

    def test_only_expected_business_message_passes(self):
        result = self.deny(Mock(side_effect=ValidationError('revoked admission')), {'revoked admission'})
        self.assertEqual(result, {'error_type': 'ValidationError', 'reason': 'revoked admission'})

    def test_successful_invalid_release_fails(self):
        with self.assertRaisesRegex(ValidationError, 'accepted'):
            self.deny(lambda: True, {'hold'})

    def test_wrong_business_or_setup_rejection_fails(self):
        with self.assertRaisesRegex(ValidationError, 'Wrong business rejection'):
            self.deny(Mock(side_effect=UserError('missing warehouse')), {'hold'})

    def test_access_error_is_not_pass_even_when_message_matches(self):
        with self.assertRaises(AccessError):
            self.deny(Mock(side_effect=AccessError('hold')), {'hold'})

    def test_attribute_and_other_programming_errors_are_not_pass(self):
        for error in (AttributeError('hold'), TypeError('hold'), RuntimeError('hold')):
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.deny(Mock(side_effect=error), {'hold'})


class Cursor:
    dbname = 'odoo17-vataga-staging'

    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE rows (id INTEGER PRIMARY KEY, model TEXT)')
        self.callbacks = []
        self.closed = False
        self.rollbacks = 0

    @contextmanager
    def savepoint(self):
        self.db.execute('SAVEPOINT scenario')
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK TO SAVEPOINT scenario')
            raise
        finally:
            self.db.execute('RELEASE SAVEPOINT scenario')

    def rollback(self):
        self.rollbacks += 1
        self.db.rollback()
        self.callbacks.clear()

    def close(self):
        self.closed = True


class Model:
    def __init__(self, cursor, name):
        self.cr, self.name = cursor, name

    def search_count(self, domain):
        minimum = domain[0][2] if domain else 0
        return self.cr.db.execute('SELECT COUNT(*) FROM rows WHERE model=? AND id>?',
                                  (self.name, minimum)).fetchone()[0]

    def search(self, domain, order=None, limit=None):
        return NS(id=self.cr.db.execute('SELECT MAX(id) FROM rows WHERE model=?', (self.name,)).fetchone()[0])


class TestProbeRollbackOrchestration(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.probe = self.scope['QualityActualBatchAcceptanceProbe']()
        self.cursor = Cursor()
        self.addCleanup(self.cursor.db.close)
        self.parent_cr = NS(dbname='odoo17-vataga-staging', untouched=True)
        user = NS(has_group=lambda name: True, tz='Europe/Kiev')
        self.probe.env = NS(cr=self.parent_cr, uid=2, user=user, companies=NS(ids=[1]), lang='uk_UA',
                            registry=NS(cursor=lambda: self.cursor))
        outer = self
        class Env:
            cr = outer.cursor
            user = outer.probe.env.user
            def __getitem__(self, name):
                return outer.inner if name == 'mrp.production' else Model(self.cr, name)
            def invalidate_all(self, flush=False):
                pass
            def flush_all(self):
                pass
        self.env = Env()
        self.inner = self.scope['QualityActualBatchAcceptanceProbe']()
        self.inner.env = self.env
        self.inner.search_count = Model(self.cursor, 'mrp.production').search_count
        self.inner.search = Model(self.cursor, 'mrp.production').search
        self.inner._quality_actual_probe_preconditions = Mock()
        self.inner._quality_actual_probe_case = self.scenario
        self.scope['api'] = NS(Environment=lambda *args, **kwargs: self.env)

    def scenario(self, code, progress):
        progress['stage'] = 'test_native_action'
        for name in ('mrp.production', 'stock.quant', 'stock.move', 'stock.move.line',
                     'stock.valuation.layer', 'quality.unit.stock.fact', 'quality.production.unit', 'mail.mail'):
            self.cursor.db.execute('INSERT INTO rows(model) VALUES (?)', (name,))
        self.cursor.callbacks.append('must never be sent')
        return {'status': 'PASS', 'release_action_called': True}

    def assert_clean(self, result):
        self.assertEqual(result['rollback'], 'COMPLETE')
        self.assertEqual(result['persistent_test_records'], 0)
        self.assertEqual(self.cursor.db.execute('SELECT COUNT(*) FROM rows').fetchone()[0], 0)
        self.assertFalse(self.cursor.callbacks)
        self.assertTrue(self.cursor.closed)
        self.assertTrue(self.parent_cr.untouched)
        self.assertEqual(self.cursor.rollbacks, 1)

    def test_success_still_rolls_back_every_fixture_and_discards_callbacks(self):
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual(result['status'], 'PASS')
        self.assert_clean(result)

    def test_unexpected_failure_after_writes_reports_stage_and_rolls_back(self):
        def fail(code, progress):
            self.scenario(code, progress)
            raise AttributeError('unexpected runtime API')
        self.inner._quality_actual_probe_case = fail
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual((result['status'], result['error_type'], result['stage']),
                         ('FAIL', 'AttributeError', 'test_native_action'))
        self.assert_clean(result)

    def test_explicit_skip_is_not_pass_and_still_rolls_back(self):
        def skip(code, progress):
            self.scenario(code, progress)
            raise self.scope['_ProbeSkip']('unsafe fixture')
        self.inner._quality_actual_probe_case = skip
        result = self.probe._quality_actual_probe_isolated_case('N6')
        self.assertEqual(result['status'], 'SKIP')
        self.assert_clean(result)

    def test_access_failure_is_fail_not_successful_negative(self):
        self.inner._quality_actual_probe_case = Mock(side_effect=AccessError('unavailable stock facts'))
        result = self.probe._quality_actual_probe_isolated_case('N3')
        self.assertEqual((result['status'], result['error_type']), ('FAIL', 'AccessError'))
        self.assert_clean(result)

    def test_verification_failure_does_not_claim_complete_rollback(self):
        calls = 0
        original = self.inner.search_count
        def bad_count(domain):
            nonlocal calls
            calls += 1
            return original(domain) if calls == 1 else 1
        self.inner.search_count = bad_count
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual((result['status'], result['rollback']), ('FAIL', 'UNVERIFIED'))
        self.assertTrue(self.cursor.closed)
        self.assertFalse(self.cursor.callbacks)

    def test_precondition_skip_creates_no_fixture(self):
        self.inner._quality_actual_probe_preconditions.side_effect = self.scope['_ProbeSkip']('unsafe automation')
        case = self.inner._quality_actual_probe_case = Mock()
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual(result['status'], 'SKIP')
        case.assert_not_called()
        self.assert_clean(result)

    def test_open_cursor_failure_is_structured_fail(self):
        self.probe.env.registry.cursor = Mock(side_effect=RuntimeError('no connection'))
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual((result['status'], result['stage'], result['error_type']),
                         ('FAIL', 'open_transaction', 'RuntimeError'))


class TestProbeReportAndSource(unittest.TestCase):
    def test_public_report_keeps_orm_not_run_and_safety_skip_incomplete(self):
        scope = load()
        probe = scope['QualityActualBatchAcceptanceProbe']()
        class Env:
            cr = NS(dbname='odoo17-vataga-staging')
            user = NS(has_group=lambda name: True)
            def __getitem__(self, name):
                self_module = NS(installed_version='17.0.2.63')
                return NS(search=lambda domain, limit: self_module)
        probe.env = Env()
        probe._quality_actual_probe_isolated_case = Mock(side_effect=lambda code: {
            'code': code, 'status': 'SKIP' if code == 'N6' else 'PASS', 'rollback': 'COMPLETE'})
        report = probe._run_quality_actual_batch_acceptance_probe('odoo17-vataga-staging')
        self.assertEqual(report['overall'], 'INCOMPLETE')
        self.assertEqual(report['scheduled_scenarios'], 'INCOMPLETE')
        self.assertEqual(report['persistent_test_records'], 0)
        self.assertEqual(len(report['scenarios']), 11)
        self.assertEqual(len(report['enterprise_orm']), 4)
        self.assertTrue(all(row['status'] == 'NOT RUN' for row in report['enterprise_orm']))

    def test_preconditions_remain_readonly_before_rollback_baseline(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == '_quality_actual_probe_preconditions')
        attrs = {node.func.attr for node in ast.walk(method)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(attrs & {'create', 'write', 'unlink', 'execute', 'message_post'})

    def test_overall_never_hides_missing_or_failed_tests(self):
        overall = load()['_overall']
        self.assertEqual(overall([{'status': 'PASS'}]), 'PASS')
        for status in ('SKIP', 'NOT RUN'):
            self.assertEqual(overall([{'status': 'PASS'}, {'status': status}]), 'INCOMPLETE')
            self.assertEqual(overall([{'status': status}, {'status': 'FAIL'}]), 'FAIL')

    def test_foreign_identity_safety_skip_does_not_make_fake_rows(self):
        scope = load()
        probe = scope['QualityActualBatchAcceptanceProbe']()
        probe._quality_actual_probe_setup = Mock()
        with self.assertRaises(scope['_ProbeSkip']):
            probe._quality_actual_probe_case('N6', {})
        probe._quality_actual_probe_setup.assert_not_called()

    def test_scheduled_action_only_calls_exact_database_and_displays_report(self):
        tree = ast.parse((ROOT / 'scripts/actual_batch_acceptance_scheduled_action.py').read_text())
        self.assertEqual(len(tree.body), 2)
        call = tree.body[0].value
        self.assertEqual(call.func.attr, '_run_quality_actual_batch_acceptance_probe')
        self.assertEqual(call.keywords[0].arg, 'expected_database')
        self.assertEqual(call.keywords[0].value.value, 'odoo17-vataga-staging')
        self.assertIsInstance(tree.body[1], ast.Raise)

    def test_server_probe_has_no_patches_sql_mutations_sudo_or_commit(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        attrs = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(attrs & {'sudo', 'commit', 'execute', 'patch', '_patch_method', 'unlink'})
        self.assertFalse(any(isinstance(node, ast.With) and any(
            isinstance(item.context_expr, ast.Name) and item.context_expr.id == 'cr'
            for item in node.items) for node in ast.walk(tree)))
        self.assertIn('action_release_quality_actual_batch', attrs)
        self.assertIn('rollback', attrs)
        self.assertIn('close', attrs)

    def test_real_business_paths_and_all_requested_codes_are_present(self):
        scope = load()
        self.assertEqual(set(scope['SCENARIOS']), {'P1', 'N1', 'N2', 'N3', 'N4', 'N5', 'N6', 'N7', 'N8', 'N12', 'N14'})
        self.assertEqual(set(scope['ORM_ONLY']), {'N9', 'N10', 'N11', 'N13'})
        self.assertTrue({'stock.quant', 'stock.valuation.layer', 'quality.unit.stock.fact',
                         'quality.unit.admission', 'mrp.workorder'} <= set(scope['AUDIT_MODELS']))


if __name__ == '__main__':
    unittest.main()
