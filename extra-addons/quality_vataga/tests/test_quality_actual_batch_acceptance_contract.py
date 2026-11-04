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
    native = ast.parse((ROOT / 'models/quality_native_workorder.py').read_text(encoding='utf-8'))
    scope['PARTIAL_WO_MESSAGE'] = ast.literal_eval(next(node.value for node in native.body
        if isinstance(node, ast.Assign) and node.targets[0].id == 'PARTIAL_WO_MESSAGE'))
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

    def test_inventory_evidence_survives_failed_assertion_and_rollback(self):
        def fail(code, progress):
            self.scenario(code, progress)
            progress['inventory'] = {'after_release': {
                'component': {'on_hand': 13, 'reserved': 4, 'available': 9},
                'expected': {'component': {'on_hand': 14, 'reserved': 4, 'available': 10}},
                'location_id': 20, 'company_id': 1, 'move_ids': [100, 101]}}
            raise ValidationError('Physical inventory mismatch')
        self.inner._quality_actual_probe_case = fail
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['inventory']['after_release']['component']['on_hand'], 13)
        self.assertEqual(result['inventory']['after_release']['expected']['component']['on_hand'], 14)
        self.assert_clean(result)

    def test_explicit_skip_is_not_pass_and_still_rolls_back(self):
        def skip(code, progress):
            self.scenario(code, progress)
            raise self.scope['_ProbeSkip']('unsafe fixture')
        self.inner._quality_actual_probe_case = skip
        result = self.probe._quality_actual_probe_isolated_case('N6')
        self.assertEqual(result['status'], 'SKIP')
        self.assert_clean(result)

    def test_individual_physical_evidence_failures_survive_transaction_rollback(self):
        def fail(code, progress):
            self.scenario(code, progress)
            snapshot = dict(unit_count=3, unit_ids=[1, 2, 3], first_execution_ids=[],
                            first_check_ids=[], last_check_ids=[5], executions=[], samples=[])
            progress['physical_evidence'] = {
                'before': snapshot, 'after': dict(snapshot, first_check_ids=[99])}
            self.inner._quality_actual_probe_assert_evidence(progress['physical_evidence'], [1, 2, 3])
        self.inner._quality_actual_probe_case = fail
        result = self.probe._quality_actual_probe_isolated_case('P1')
        self.assertEqual(result['status'], 'FAIL')
        evidence = result['physical_evidence']
        self.assertEqual(evidence['assertions']['first_checks_absent'], 'FAIL')
        self.assertEqual(evidence['after']['first_check_ids'], [99])
        self.assertEqual(evidence['assertions']['unit_ids'], 'PASS')
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


class TestActualProbeInventoryBalance(unittest.TestCase):
    def setUp(self):
        scope = load()
        self.probe = scope['QualityActualBatchAcceptanceProbe']()
        self.stock = {'product': {'on_hand': 3, 'reserved': 0, 'available': 3},
                      'component': {'on_hand': 14, 'reserved': 4, 'available': 10},
                      'backorder_component_reserved': 4}

    def test_fourteen_physical_components_with_four_reserved_leaves_ten_available(self):
        self.probe._quality_actual_probe_assert_inventory(self.stock)
        self.assertEqual(self.stock['expected']['component']['on_hand'], 14)
        self.assertEqual(self.stock['expected']['component']['available'], 10)

    def test_actual_shortage_is_not_accepted_as_reservation(self):
        self.stock['component'].update(on_hand=13, available=9)
        with self.assertRaisesRegex(ValidationError, 'actual=.*expected='):
            self.probe._quality_actual_probe_assert_inventory(self.stock)
        self.assertEqual(self.stock['expected']['component']['on_hand'], 14)

    def test_available_api_must_agree_with_physical_stock_minus_reservations(self):
        self.stock['component']['available'] = 14
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_assert_inventory(self.stock)

    def test_finished_product_shortage_still_fails(self):
        self.stock['product'].update(on_hand=2, available=2)
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_assert_inventory(self.stock)

    def test_component_reservation_must_have_backorder_move_line_evidence(self):
        self.stock['backorder_component_reserved'] = 0
        with self.assertRaisesRegex(ValidationError, 'real backorder move lines'):
            self.probe._quality_actual_probe_assert_inventory(self.stock)

    def test_missing_expected_at_confirm_reservation_is_not_silently_accepted(self):
        self.stock['component'].update(reserved=0, available=14)
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_assert_inventory(self.stock)

    def test_inventory_reader_flushes_and_reports_quant_locations_and_real_move_lines(self):
        class Rows(list):
            @property
            def ids(self):
                return [row.id for row in self]
            def __or__(self, other):
                return Rows(list(self) + [row for row in other if row.id not in self.ids])
            def __getattr__(self, name):
                if name == 'id':
                    return self[0].id if self else False
                if not self:
                    return Rows()
                values = [getattr(row, name) for row in self]
                if all(isinstance(value, Rows) for value in values):
                    result = Rows()
                    for value in values:
                        result |= value
                    return result
                return values[0]
            def sorted(self, name):
                return Rows(sorted(self, key=lambda row: getattr(row, name)))
            def filtered(self, predicate):
                return Rows(row for row in self if predicate(row))
            def mapped(self, name):
                return [getattr(row, name) for row in self]
            def read(self, fields):
                def value(item):
                    return (item.id, 'fixture') if getattr(item, 'id', False) else item
                return [dict(id=row.id, **{name: value(getattr(row, name, False)) for name in fields}) for row in self]
        company, location = NS(id=1), NS(id=10)
        uom = NS(id=1, _compute_quantity=lambda quantity, target: quantity)
        finished, component = Rows([NS(id=1, uom_id=uom)]), Rows([NS(id=2, uom_id=uom)])
        def line(index, product, quantity, state='done'):
            return NS(id=index, product_id=product, quantity=quantity, state=state, picked=False,
                      product_uom_id=uom, company_id=company, location_id=location, location_dest_id=location)
        receipt_line, raw_line, finished_line, reserved_line = (
            line(100, component, 20), line(101, component, 6), line(102, finished, 3),
            line(103, component, 4, 'assigned'))
        def move(index, item, quantity, state='done'):
            return NS(id=index, state=state, picked=False, quantity=quantity,
                      move_line_ids=Rows([item]), company_id=company)
        receipt, raw, output, reserved = (Rows([move(50, receipt_line, 20)]),
                                        Rows([move(51, raw_line, 6)]), Rows([move(52, finished_line, 3)]),
                                        Rows([move(53, reserved_line, 4, 'assigned')]))
        for movement in receipt | raw | output | reserved:
            movement.move_line_ids[0].move_id = movement
        production = Rows([NS(id=7, move_raw_ids=raw, move_finished_ids=output)])
        children = Rows([NS(id=8, move_raw_ids=reserved)])
        quants = Rows([
            NS(id=1, product_id=finished, company_id=company, location_id=location, quantity=3, reserved_quantity=0),
            NS(id=2, product_id=component, company_id=company, location_id=location, quantity=14, reserved_quantity=4),
            NS(id=3, product_id=component, company_id=company, location_id=NS(id=99), quantity=-20, reserved_quantity=0),
        ])
        events = []
        Quant = NS(invalidate_model=Mock(side_effect=lambda fields: events.append('invalidate')),
                   search=Mock(side_effect=lambda domain, order: events.append('search') or quants),
                   _get_available_quantity=Mock(side_effect=lambda product, loc, **kw: 3 if product.id == 1 else 10))
        class Env:
            def __getitem__(self, name):
                return Quant if name == 'stock.quant' else Rows()
            def flush_all(self):
                events.append('flush')
        self.probe.env = Env()
        self.probe.env.company = company
        self.probe.search = Mock(return_value=children)
        self.probe.browse = Mock(return_value=Rows())
        actual = self.probe._quality_actual_probe_inventory({
            'product': finished, 'component': component, 'location': location,
            'receipt': receipt, 'production': production})
        self.assertEqual(events[:3], ['flush', 'invalidate', 'search'])
        self.assertEqual(actual['component'], {'product_id': 2, 'on_hand': 14, 'reserved': 4, 'available': 10})
        self.assertEqual(actual['backorder_component_reserved'], 4)
        self.assertEqual((actual['location_id'], actual['company_id']), (10, 1))
        self.assertEqual(actual['move_ids'], [50, 51, 52, 53])
        self.assertEqual([row['id'] for row in actual['move_lines']], [100, 101, 102, 103])
        self.assertEqual(len(actual['quants']), 3)  # Also retain the other location for diagnosis.
        self.assertEqual(Quant._get_available_quantity.call_args.kwargs, {'strict': True, 'allow_negative': True})


class TestActualProbeN5Fixture(unittest.TestCase):
    def setUp(self):
        self.scope = load()
        self.probe = self.scope['QualityActualBatchAcceptanceProbe']()
        self.first = NS(state='progress', qty_produced=3, display_name='Temporary cutting')
        self.production = NS(action_release_quality_actual_batch=Mock(side_effect=UserError(
            'Не підтверджено фактичне виконання операції Temporary cutting для всієї виготовленої партії.')))
        self.probe._quality_actual_probe_setup = Mock(return_value={
            'production': self.production, 'check': NS(), 'samples': [], 'orders': [self.first],
            'synthetic_timer_preparation': True})
        self.probe._quality_actual_probe_stock_snapshot = Mock(return_value={
            'quants': [14, 4], 'units': [1, 2, 3], 'valuation': [], 'children': []})

    def test_n5_attempts_real_public_action_and_checks_exact_unfinished_operation_reason(self):
        progress = {}
        result = self.probe._quality_actual_probe_case('N5', progress)
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['stock_unchanged'])
        self.assertTrue(progress['release_action_attempted'])
        self.production.action_release_quality_actual_batch.assert_called_once_with()
        self.assertTrue(self.probe._quality_actual_probe_setup.call_args.kwargs['unfinished_first'])

    def test_native_predecessor_guard_means_skip_not_successful_release_rejection(self):
        self.probe._quality_actual_probe_setup.side_effect = UserError(self.scope['PARTIAL_WO_MESSAGE'])
        with self.assertRaises(self.scope['_ProbeSkip']):
            self.probe._quality_actual_probe_case('N5', {})
        self.production.action_release_quality_actual_batch.assert_not_called()

    def test_setup_access_and_immutability_errors_are_not_hidden_as_pass_or_skip(self):
        for error in (AccessError('ACL failure'), UserError('Ідентичність та результат підтвердженої операції незмінні.')):
            self.probe._quality_actual_probe_setup.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.probe._quality_actual_probe_case('N5', {})

    def test_initial_identification_precedes_first_execution_and_no_late_operation_is_added(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        setup = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                     and node.name == '_quality_actual_probe_setup')
        initialize = next(node.lineno for node in ast.walk(setup) if isinstance(node, ast.Call)
                          and isinstance(node.func, ast.Attribute) and node.func.attr == '_quality_actual_probe_initialize_check')
        first_finish = next(node.lineno for node in ast.walk(setup) if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute) and node.func.attr == 'button_finish'
                            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'first')
        self.assertLess(initialize, first_finish)
        case = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                    and node.name == '_quality_actual_probe_case')
        branch = next(node for node in ast.walk(case) if isinstance(node, ast.If)
                      and ast.unparse(node.test) == "code == 'N5'")
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                             and node.func.attr in ('create', 'unlink')
                             for statement in branch.body for node in ast.walk(statement)))


class EvidenceRows(list):
    @property
    def ids(self):
        return [row.id for row in self]

    def sorted(self, field):
        return EvidenceRows(sorted(self, key=lambda row: getattr(row, field)))


class TestActualProbeStableEvidence(unittest.TestCase):
    def setUp(self):
        self.probe = load()['QualityActualBatchAcceptanceProbe']()
        self.units = EvidenceRows([NS(id=index, display_name='Unit %s' % index) for index in (1, 2, 3)])
        self.first = NS(id=10, operation_id=NS(id=100), display_name='MO / first')
        self.last = NS(id=20, operation_id=NS(id=200), display_name='MO / last')
        self.first_history = EvidenceRows()
        self.history = EvidenceRows([NS(id=index + 30, state='processed', controlled_unit_id=unit,
                                        workorder_id=self.last) for index, unit in enumerate(self.units)])
        self.first._quality_native_history = lambda: self.first_history
        self.last._quality_native_history = lambda: self.history
        self.check = NS(id=50, sample_ids=EvidenceRows([
            NS(id=index + 60, sample_result='pass', controlled_unit_id=unit)
            for index, unit in enumerate(self.units)]))
        self.first_checks = EvidenceRows()
        self.last_checks = EvidenceRows([self.check])
        self.requirements = {(70, 200, 20)}
        self.production = NS(id=90, quality_full_required_qcp=[], _applicable_full_qcp=lambda orders: self.requirements,
            action_release_quality_actual_batch=Mock(side_effect=UserError(
                'Це виробниче замовлення вже завершено. Повторний випуск заборонено.')))
        outer = self
        class Env:
            def __getitem__(self, name):
                if name == 'quality.production.unit':
                    return NS(search=lambda *args, **kw: outer.units)
                if name == 'quality.check':
                    return NS(search=lambda domain, **kw:
                              outer.first_checks if domain[0][2] == 10 else outer.last_checks)
                raise AssertionError(name)
        self.probe.env = Env()
        self.scene = dict(orders=[self.first, self.last], product=NS(id=80), check=self.check,
                          production=self.production, point=NS(id=70), samples=self.check.sample_ids,
                          synthetic_timer_preparation=True)
        self.before = self.probe._quality_actual_probe_evidence(self.scene)

    def evidence(self):
        return {'before': self.before, 'after': self.probe._quality_actual_probe_evidence(self.scene)}

    def assert_rejected(self, name):
        evidence = self.evidence()
        with self.assertRaisesRegex(ValidationError, name):
            self.probe._quality_actual_probe_assert_evidence(evidence, [1, 2, 3])
        self.assertEqual(evidence['assertions'][name], 'FAIL')
        self.assertIn('after', evidence)

    def test_many2one_display_name_change_and_record_order_do_not_change_identity(self):
        self.last.display_name = 'MO-001 / last'
        self.units[0].display_name = 'Renamed unit'
        self.history.reverse()
        self.check.sample_ids.reverse()
        evidence = self.evidence()
        self.probe._quality_actual_probe_assert_evidence(evidence, [3, 2, 1])
        self.assertEqual(set(evidence['assertions'].values()), {'PASS'})
        self.assertEqual(evidence['after']['executions'][0], {
            'id': 30, 'state': 'processed', 'controlled_unit_id': 1, 'workorder_id': 20})

    def test_extra_unit_fails_count_with_exact_ids_retained(self):
        self.units.append(NS(id=4))
        self.assert_rejected('unit_count')

    def test_same_unit_count_with_replaced_identity_fails(self):
        self.units[-1] = NS(id=4)
        self.assert_rejected('unit_ids')

    def test_early_execution_is_rejected(self):
        self.first_history.append(NS(id=90))
        self.assert_rejected('first_executions_absent')

    def test_early_qc_is_rejected(self):
        self.first_checks.append(NS(id=91))
        self.assert_rejected('first_checks_absent')

    def test_additional_final_qc_is_rejected(self):
        self.last_checks.append(NS(id=92))
        self.assert_rejected('last_check_ids_unchanged')

    def test_changed_execution_identity_state_unit_or_workorder_fails(self):
        for field, value in (('id', 99), ('state', 'cancel'),
                             ('controlled_unit_id', NS(id=99)), ('workorder_id', NS(id=99))):
            row = self.history[0]
            original = getattr(row, field)
            with self.subTest(field=field):
                setattr(row, field, value)
                self.assert_rejected('execution_history_unchanged')
            setattr(row, field, original)

    def test_changed_sample_identity_result_or_unit_fails(self):
        for field, value in (('id', 99), ('sample_result', 'fail'), ('controlled_unit_id', NS(id=99))):
            row = self.check.sample_ids[0]
            original = getattr(row, field)
            with self.subTest(field=field):
                setattr(row, field, value)
                self.assert_rejected('sample_results_unchanged')
            setattr(row, field, original)

    def test_missing_execution_and_sample_are_rejected(self):
        self.history.pop()
        self.check.sample_ids.pop()
        evidence = self.evidence()
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_assert_evidence(evidence, [1, 2, 3])
        self.assertEqual(evidence['assertions']['execution_history_unchanged'], 'FAIL')
        self.assertEqual(evidence['assertions']['sample_results_unchanged'], 'FAIL')

    def prepare_case(self):
        self.probe._quality_actual_probe_setup = Mock(return_value=self.scene)
        self.probe._quality_actual_probe_positive = Mock(return_value={'release_action_called': True})
        self.probe._quality_actual_probe_stock_snapshot = Mock(return_value={'unchanged': True})
        self.probe._quality_actual_probe_inventory = Mock(return_value={'quants': [3, 14, 4, 10], 'move_ids': [1, 2]})

    def test_n7_reaches_public_duplicate_action_and_checks_inventory_after_rejection(self):
        self.prepare_case()
        progress = {}
        result = self.probe._quality_actual_probe_case('N7', progress)
        self.production.action_release_quality_actual_batch.assert_called_once_with()
        self.assertEqual(self.probe._quality_actual_probe_stock_snapshot.call_count, 2)
        self.assertEqual(self.probe._quality_actual_probe_inventory.call_count, 2)
        self.assertTrue(result['duplicate_release_verified'])
        self.assertTrue(result['stock_unchanged'])
        self.assertEqual(progress['duplicate_release']['status'], 'PASS')

    def test_n7_rejects_duplicate_inventory_even_if_business_rejection_matches(self):
        self.prepare_case()
        self.probe._quality_actual_probe_inventory.side_effect = [{'quants': [3, 14]}, {'quants': [6, 8]}]
        progress = {}
        with self.assertRaisesRegex(ValidationError, 'Repeated release changed inventory'):
            self.probe._quality_actual_probe_case('N7', progress)
        self.assertEqual(progress['duplicate_release']['status'], 'FAIL')

    def test_n7_does_not_claim_duplicate_test_when_positive_step_fails(self):
        self.prepare_case()
        self.probe._quality_actual_probe_positive.side_effect = ValidationError('physical evidence')
        progress = {}
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_case('N7', progress)
        self.production.action_release_quality_actual_batch.assert_not_called()
        self.assertNotIn('duplicate_release', progress)

    def test_n14_proves_actual_final_only_requirements_before_and_after_release(self):
        self.prepare_case()
        progress = {}
        result = self.probe._quality_actual_probe_case('N14', progress)
        self.assertTrue(result['last_operation_only_qc_verified'])
        self.assertEqual(set(progress['last_operation_only_qc']), {'before_release', 'after_release'})
        self.assertTrue(all(row['status'] == 'PASS' for row in progress['last_operation_only_qc'].values()))

    def test_n14_requires_final_qcp_and_rejects_early_qcp_or_qc(self):
        for requirements, early_checks in ((set(), []), ({(70, 100, 10), (70, 200, 20)}, []),
                                           ({(70, 200, 20)}, [NS(id=99)])):
            self.requirements = requirements
            self.first_checks = EvidenceRows(early_checks)
            progress = {}
            with self.subTest(requirements=requirements, early=early_checks), self.assertRaises(ValidationError):
                self.probe._quality_actual_probe_last_only_qc(self.scene, progress, 'before_release')
            self.assertEqual(progress['last_operation_only_qc']['before_release']['status'], 'FAIL')

    def test_n14_rejects_sticky_early_requirement_even_if_current_assignment_is_final_only(self):
        self.production.quality_full_required_qcp = [[71, 100, 10]]
        progress = {}
        with self.assertRaises(ValidationError):
            self.probe._quality_actual_probe_last_only_qc(self.scene, progress, 'after_release')
        detail = progress['last_operation_only_qc']['after_release']
        self.assertEqual(detail['captured_requirements'], [[71, 100, 10]])
        self.assertEqual(detail['status'], 'FAIL')

    def test_positive_path_uses_stable_evidence_instead_of_display_name_read(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == '_quality_actual_probe_positive')
        calls = [node.func.attr for node in ast.walk(method)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertNotIn('read', calls)
        self.assertEqual(calls.count('_quality_actual_probe_evidence'), 2)
        self.assertIn('_quality_actual_probe_assert_evidence', calls)

    def test_real_positive_method_preserves_evidence_when_native_split_renames_workorder(self):
        self.scene['units'] = self.units
        self.check.quality_state = 'pass'
        self.check.unit_admission_ids = NS(filtered=lambda predicate: [1, 2, 3])
        group = NS(id=5)
        self.production.procurement_group_id = group
        self.production.workorder_ids = NS(mapped=lambda name: ['done', 'done'])
        class Facts:
            unit_id = self.units
            move_line_id = NS(ids=[101])
            def __len__(self):
                return 3
        self.production.native_stock_fact_ids = Facts()
        raw_lines = NS(ids=[100])
        finished_lines = NS(ids=[101], mapped=lambda name: [3], consume_line_ids=raw_lines)
        raw = NS(mapped=lambda name: [6], move_line_ids=raw_lines,
                 stock_valuation_layer_ids=NS(mapped=lambda name: [-6]))
        finished = NS(move_line_ids=finished_lines,
                      stock_valuation_layer_ids=NS(mapped=lambda name: [3]))
        self.production.move_raw_ids = NS(filtered=lambda predicate: raw)
        self.production.move_finished_ids = NS(filtered=lambda predicate: finished)
        class Children:
            product_qty, qty_produced, state = 2, 0, 'confirmed'
            procurement_group_id = group
            def __len__(self):
                return 1
            def _full_release_units(self):
                return []
        self.probe.search = Mock(return_value=Children())
        def native_release():
            self.production.state, self.production.product_qty, self.production.qty_produced = 'done', 3, 3
            self.last.display_name = 'MO-001 / last'
        self.production.action_release_quality_actual_batch = Mock(side_effect=native_release)
        inventory = {'product': {'on_hand': 3, 'reserved': 0, 'available': 3},
                     'component': {'on_hand': 14, 'reserved': 4, 'available': 10},
                     'backorder_component_reserved': 4}
        self.probe._quality_actual_probe_inventory = Mock(return_value=inventory)
        progress = {}
        result = self.probe._quality_actual_probe_positive(self.scene, progress)
        self.production.action_release_quality_actual_batch.assert_called_once_with()
        self.assertTrue(result['historical_evidence_unchanged'])
        self.assertEqual(progress['physical_evidence']['before'], progress['physical_evidence']['after'])
        self.assertEqual(self.last.display_name, 'MO-001 / last')


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
