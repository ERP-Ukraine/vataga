"""WO lifecycle gates with separate Enterprise and base MRP test doubles."""
from concurrent.futures import ThreadPoolExecutor
import ast
from contextlib import contextmanager
from contextvars import ContextVar
import math
import os
from pathlib import Path
from threading import RLock
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from unittest.mock import patch

try:
    from .test_quality_defect_contract import shipped_class, AccessError, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, AccessError, UserError


SHOP_FLOOR_GATE = 'You need to complete Quality Checks using the Shop Floor before marking Work Order as Done.'


class Rows(list):
    @property
    def ids(self):
        return [row.id for row in self]
    def __getattr__(self, name):
        values = [getattr(row, name) for row in self]
        if not values or all(hasattr(value, 'id') for value in values):
            return Rows({value.id: value for value in values}.values())
        if len(values) == 1:
            return values[0]
        raise AttributeError(name)
    def __sub__(self, other):
        return Rows(row for row in self if row.id not in other.ids)
    def __or__(self, other):
        return Rows(list(self) + [row for row in other if row.id not in self.ids])
    def filtered(self, key):
        return Rows(row for row in self if (getattr(row, key) if isinstance(key, str) else key(row)))
    def sorted(self, key, reverse=False):
        return Rows(sorted(self, key=key if callable(key) else lambda row: getattr(row, key), reverse=reverse))
    def _assert_not_stock_posted(self):
        pass
    def check_access_rights(self, mode):
        pass
    def check_access_rule(self, mode):
        pass


class NativeDouble:
    def __init__(self, rows=(), env=None):
        object.__setattr__(self, 'rows', list(rows))
        object.__setattr__(self, 'env', env)
    def __iter__(self):
        return (type(self)([row], self.env) for row in self.rows)
    def __len__(self):
        return len(self.rows)
    def __eq__(self, other):
        return isinstance(other, NativeDouble) and self.ids == other.ids
    def __getitem__(self, name):
        return getattr(self, name)
    def __getattr__(self, name):
        if name == 'ids':
            return [row.id for row in self.rows]
        if len(self.rows) == 1:
            return getattr(self.rows[0], name)
        if name == 'production_id':
            return self.env.production
        raise AttributeError(name)
    def __setattr__(self, name, value):
        setattr(self.rows[0], name, value)
    def __sub__(self, other):
        return type(self)([row for row in self.rows if row.id not in other.ids], self.env)
    def filtered(self, predicate):
        return type(self)([row for row, wo in zip(self.rows, self) if predicate(wo)], self.env)
    def browse(self, ids=()):
        return type(self)([row for row in self.rows if row.id in ids], self.env)
    def ensure_one(self):
        assert len(self) == 1
    def invalidate_recordset(self):
        pass
    def with_context(self, **context):
        return self
    def end_all(self):
        for timer in self.time_ids:
            timer.date_end = 'closed'
    def write(self, values):
        if values.get('state') == 'done':
            self._check_quality_native_finish()  # Lower module boundary too.
        for row in self.rows:
            row.__dict__.update(values)
        return True
    def button_finish(self):
        self.env.native_calls += 1
        for wo in self:
            wo.end_all()
            wo.write({'qty_produced': wo.qty_produced or wo.qty_producing or wo.qty_production,
                      'state': 'done', 'date_finished': 'closed'})
        if self.env.fail_native:
            raise UserError('native finish failed')
        return True
    def button_done(self):
        return self.button_finish()
    def action_mark_as_done(self):
        return self.button_finish()
    def button_start(self, bypass=False):
        self.env.native_starts += 1
        self.rows[0].state = 'progress'
        self.rows[0].time_ids.append(NS(id=10, user_id=self.env.user, date_end=False))
        return True


class EnterpriseDouble(NativeDouble):
    """mrp_workorder verifies QC before calling base button_finish."""
    def button_finish(self):
        self.env.enterprise_calls += 1
        self.verify_quality_checks()
        return super().button_finish()

    def verify_quality_checks(self):
        for check in self.check_ids:
            if check.quality_state in ('pass', 'fail'):
                continue
            if check.test_type in ('instructions', 'register_consumed_materials', 'register_byproducts'):
                check.quality_state = 'pass'
            else:
                raise UserError(SHOP_FLOOR_GATE)

    def end_all(self):
        self.employee_ids = []
        return super().end_all()

    def do_finish(self):
        self.end_all()
        return self.record_production()

    def record_production(self):
        return self.button_finish()


class TestPhysicalFinishLifecycle(unittest.TestCase):
    def setUp(self):
        partial, finish = ContextVar('test_partial', default=None), ContextVar('test_finish', default=None)
        self.partial, self.finish = partial, finish
        def partial_allows(wo):
            scope = partial.get()
            return bool(scope and len(wo) == 1 and scope[:2] == (wo.env.cr, wo.id))
        def finish_scope(wo):
            scope = finish.get()
            return scope if scope and len(wo) == 1 and scope[:2] == (wo.env.cr, wo.id) else None
        cls = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder', {
            '_quality_native_automatic', '_check_quality_native_start', '_check_quality_native_finish',
            '_quality_confirm_on_native_finish', '_finish_quality_native_work',
            'verify_quality_checks', 'button_finish', 'button_done', 'action_mark_as_done', 'button_start', 'write',
            'do_finish', 'record_production', '_check_shop_floor_operator',
            'action_confirm_quality_native_units', '_check_quality_native_selection', '_compute_quality_native_status'}, parent=EnterpriseDouble,
            math=math, uuid=NS(uuid4=lambda: 'request'), _PARTIAL_EXECUTION=partial, _PHYSICAL_FINISH=finish,
            partial_execution_allows=partial_allows, physical_finish_scope=finish_scope,
            native_operation_allows=lambda production: False, PARTIAL_WO_MESSAGE='upstream not processed', request=False)
        self.units = Rows(NS(id=i, display_name='QUNIT/%s' % i) for i in (101, 102))
        self.history = Rows()
        self.operator = NS(id=1, employee_id=NS(id=4))
        uom = NS(id=1, _compute_quantity=lambda qty, unit, round=False: qty)
        lock = RLock()
        self.production = NS(quality_unit_route_required=True, state='progress', product_qty=3,
            qty_produced=0, _check_quality_transfer_boundary=Mock(),
            _lock_quality_transfer_scope=Mock(),
            move_raw_ids=Rows(), move_finished_ids=Rows(),
            _full_release_units=lambda: self.units, _native_partial_history=lambda: False)
        self.row = NS(id=10, state='progress', qty_production=3, qty_producing=2, qty_produced=0,
            quality_transfer_controlled=False, quality_native_identity_pending=False,
            production_id=self.production, product_uom_id=uom,
            product_id=NS(id=20), company_id=NS(id=30), check_ids=Rows(), employee_ids=[self.operator],
            time_ids=Rows([NS(id=1, user_id=self.operator, employee_id=self.operator.employee_id, date_end=False)]),
            all_employees_allowed=True,
            _check_quality_execution_access=Mock(), _prepare_quality_native_checks=Mock(),
            _quality_native_is_final=lambda: True, _quality_native_predecessors=lambda: Rows(),
            _quality_native_units=lambda: self.units, _quality_native_history=lambda: self.history,
            _quality_native_checks=Mock(return_value=Rows()),
            _quality_validate_native_checks=Mock(), _quality_native_completion_units=lambda: self.units,
            _quality_native_available_units=lambda units: (units, {}))
        test = self
        class Env:
            cr = NS()
            context = {}
            user = self.operator
            production = self.production
            enterprise_calls = native_calls = native_starts = 0
            fail_native = False
            def ref(env, xmlid):
                return uom
            def __getitem__(env, name):
                if name == 'quality.production.unit':
                    return NS(browse=lambda ids: Rows(unit for unit in test.units if unit.id in ids))
                if name == 'quality.unit.execution':
                    def record(wo, units, *args):
                        test.history.extend(NS(id=unit.id, state='processed', controlled_unit_id=unit) for unit in units)
                    return NS(_record_partial_completion=record, _record_native_completion=record)
                raise AssertionError('Unexpected model: ' + name)
        self.env = Env()
        self.wo = cls([self.row], self.env)
        @contextmanager
        def savepoint():
            with lock:
                snapshot = (self.row.state, self.row.qty_produced, self.row.quality_native_identity_pending,
                            list(self.history), [timer.date_end for timer in self.row.time_ids],
                            self.production.product_qty, self.production.qty_produced,
                            Rows(self.production.move_raw_ids), Rows(self.production.move_finished_ids),
                            self.production.state, list(self.row.employee_ids))
                try:
                    yield
                except Exception:
                    self.row.state, self.row.qty_produced, self.row.quality_native_identity_pending = snapshot[:3]
                    self.history[:] = snapshot[3]
                    for timer, ended in zip(self.row.time_ids, snapshot[4]):
                        timer.date_end = ended
                    (self.production.product_qty, self.production.qty_produced,
                     self.production.move_raw_ids, self.production.move_finished_ids,
                     self.production.state, self.row.employee_ids) = snapshot[5:]
                    raise
        self.env.cr.savepoint = savepoint

    def _pending_check(self, **values):
        check = NS(id=len(self.row.check_ids) + 1, quality_state='none', test_type='measure',
                   x_control_stage='in_process', workorder_id=self.wo, operation_run_id=None,
                   product_id=self.row.product_id, company_id=self.row.company_id,
                   _identity_production=lambda: self.production)
        check.__dict__.update(values)
        check.quality_shop_floor_deferred = check.test_type == 'measure' and check.x_control_stage == 'in_process'
        self.row.check_ids.append(check)
        return check

    def test_t1_t2_enterprise_gate_before_and_after_the_fix(self):
        check = self._pending_check()
        with patch.object(type(self.wo), 'verify_quality_checks', EnterpriseDouble.verify_quality_checks):
            with self.assertRaisesRegex(UserError, SHOP_FLOOR_GATE):
                self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('progress', 0, 0))
        self.assertFalse(self.row.time_ids[0].date_end)
        self.assertIsNone(self.finish.get())

        self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, self.production.product_qty), ('done', 2, 3))
        self.assertEqual(len(self.units), 2)
        self.assertEqual(self.history.controlled_unit_id.ids, [101, 102])
        self.assertTrue(all(timer.date_end for timer in self.row.time_ids))
        self.assertFalse(self.row.employee_ids)
        self.assertEqual((self.env.enterprise_calls, self.env.native_calls), (2, 1))
        self.assertEqual((self.production.qty_produced, check.quality_state), (0, 'none'))
        self.assertIsNone(self.finish.get())
        self.row._quality_validate_native_checks.assert_not_called()

    def test_ordinary_production_still_uses_enterprise_quality_gate(self):
        self.production.quality_unit_route_required = False
        self.units.clear()
        check = self._pending_check()
        with self.assertRaisesRegex(UserError, SHOP_FLOOR_GATE):
            self.wo.button_finish()
        self.assertEqual((self.row.state, check.quality_state, self.env.native_calls), ('progress', 'none', 0))

    def test_scope_defers_only_this_workorders_inspection(self):
        own = self._pending_check()
        other = self._pending_check(test_type='register_consumed_materials')
        for changes in (
            {'test_type': 'register_consumed_materials'},
            {'x_control_stage': 'final'},
            {'operation_run_id': NS(workorder_id=NS(id=42))},
            {'company_id': NS(id=42)},
        ):
            with self.subTest(changes=changes):
                other.test_type = 'measure'
                other.x_control_stage = 'in_process'
                other.operation_run_id = None
                other.company_id = own.company_id
                other.__dict__.update(changes)
                other.quality_shop_floor_deferred = other.test_type == 'measure' and other.x_control_stage == 'in_process'
                with self.assertRaisesRegex(UserError, 'інші обов’язкові кроки'):
                    self.wo.button_finish()
                self.assertEqual((own.quality_state, other.quality_state), ('none', 'none'))
                self.assertEqual((self.row.state, self.env.native_calls, len(self.history)), ('progress', 0, 0))
                self.assertIsNone(self.finish.get())

    def test_client_context_and_missing_access_cannot_defer_quality(self):
        self._pending_check()
        self.env.context = {'quality_physical_workorder_finish': [self.wo.id, 2], 'mrp_display': True}
        with self.assertRaisesRegex(UserError, SHOP_FLOOR_GATE):
            self.wo.verify_quality_checks()
        with self.assertRaises(UserError):
            self.wo.write({'state': 'done', 'qty_produced': 2})
        self.row._check_quality_execution_access.side_effect = AccessError('WO access denied')
        with self.assertRaises(AccessError):
            self.wo.button_finish()
        self.assertEqual((self.env.enterprise_calls, self.env.native_calls, len(self.history)), (0, 0, 0))
        self.row._check_quality_execution_access.side_effect = None
        with patch.object(Rows, 'check_access_rule', side_effect=AccessError('QC read denied')):
            with self.assertRaises(AccessError):
                self.wo.button_finish()
        self.assertEqual((self.row.state, self.env.native_calls, len(self.history)), ('progress', 0, 0))
        self.assertIsNone(self.finish.get())

    def test_t2_all_finish_alias_retries_do_not_repeat_native_work_or_facts(self):
        for name in ('button_finish', 'button_done', 'action_mark_as_done'):
            getattr(self.wo, name)()
        self.assertEqual((self.env.native_calls, len(self.history), len(self.row.time_ids)), (1, 2, 1))

    def test_shop_floor_do_finish_bypasses_native_early_close_and_record_production(self):
        check = self._pending_check()
        with patch.object(EnterpriseDouble, 'do_finish', side_effect=AssertionError('early timer close')), \
                patch.object(EnterpriseDouble, 'record_production', side_effect=AssertionError('early MO split')):
            self.wo.do_finish()
            self.wo.do_finish()
            self.wo.record_production()
        self.assertEqual((self.row.state, self.row.qty_produced, self.env.native_calls), ('done', 2, 1))
        self.assertEqual((self.production.product_qty, self.production.qty_produced, check.quality_state), (3, 0, 'none'))
        self.assertEqual(self.history.controlled_unit_id.ids, [101, 102])
        self.assertTrue(self.row.time_ids[0].date_end)

    def test_shop_floor_error_rolls_back_finish_and_preserves_open_timer(self):
        self.env.fail_native = True
        with self.assertRaisesRegex(UserError, 'native finish failed'):
            self.wo.do_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('progress', 0, 0))
        self.assertFalse(self.row.time_ids[0].date_end)

    def test_shop_floor_checks_employee_and_ordinary_dispatch(self):
        self.row.time_ids[0].employee_id = NS(id=5)
        self.env.context = {'employee_id': 5}
        with self.assertRaisesRegex(UserError, 'під працівником'):
            self.wo.do_finish()
        self.assertFalse(self.row.time_ids[0].date_end)
        self.production.quality_unit_route_required = False
        with patch.object(EnterpriseDouble, 'do_finish', return_value='native') as finish:
            self.assertEqual(self.wo.do_finish(), 'native')
        finish.assert_called_once_with()

    def test_all_three_aliases_use_the_same_canonical_native_finish(self):
        for name in ('button_finish', 'button_done', 'action_mark_as_done'):
            with self.subTest(method=name):
                self.setUp()
                check = self._pending_check()
                getattr(self.wo, name)()
                self.assertEqual((self.row.state, self.row.qty_produced, self.env.native_calls), ('done', 2, 1))
                self.assertEqual((self.env.enterprise_calls, check.quality_state), (1, 'none'))

    def test_t3_restart_denied_but_qc_callback_creates_no_timer(self):
        self.wo.button_finish()
        with self.assertRaisesRegex(UserError, 'Повторний запуск'):
            self.wo.button_start()
        self.wo.button_start(bypass=True)
        self.assertEqual((self.env.native_starts, len(self.row.time_ids), len(self.history)), (0, 1, 2))

    def test_t6_full_three_closes_without_using_own_qc_as_work_permission(self):
        check = self._pending_check()
        self.row.qty_producing = 3
        self.units.append(NS(id=103, display_name='QUNIT/103'))
        self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('done', 3, 3))
        self.assertEqual(check.quality_state, 'none')

    def test_t8_upstream_qc_gate_still_rejects_next_start(self):
        source = NS(state='done', _quality_native_checks=lambda: Rows([NS(id=1)]),
                    _quality_native_history=lambda: self.history,
                    _quality_validate_native_checks=Mock(side_effect=UserError('admission missing')))
        self.row._quality_native_predecessors = lambda: Rows([source])
        self.history.extend(NS(id=unit.id, state='processed', controlled_unit_id=unit) for unit in self.units)
        with self.assertRaisesRegex(UserError, 'admission missing'):
            self.wo.button_start(bypass=True)
        self.assertEqual(self.env.native_starts, 0)
        source._quality_validate_native_checks.side_effect = None
        self.row.state = 'ready'
        self.wo.button_start(bypass=True)
        self.assertEqual(self.env.native_starts, 1)

    def test_t10_t11_own_fail_or_hold_does_not_turn_done_into_active_work(self):
        self.row._quality_validate_native_checks.side_effect = UserError('FAIL / Alert')
        self.wo.button_finish()
        self.assertEqual(self.row.state, 'done')
        self.row._quality_validate_native_checks.assert_not_called()

    def test_t15_serialized_parallel_completion_records_one_exact_set(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: self.wo.do_finish(), range(2))), [True, True])
        self.assertEqual((self.env.native_calls, self.row.qty_produced, len(self.history)), (1, 2, 2))

    def test_t16_ambiguity_retains_physical_done_without_inventing_execution(self):
        self.units.append(NS(id=103, display_name='QUNIT/103'))
        self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced), ('done', 2))
        self.assertTrue(self.row.quality_native_identity_pending)
        self.assertFalse(self.history)
        # Explicit mapping later resolves the original two, without restarting.
        self.units.pop()
        with self.assertRaisesRegex(UserError, 'Самої рівності кількостей недостатньо'):
            self.wo.button_finish()
        self.assertFalse(self.history)
        self.row._quality_native_checks.return_value = Rows([NS(
            operation_run_id=NS(unit_ids=self.units),
            sample_ids=Rows(NS(id=unit.id, controlled_unit_id=unit) for unit in self.units))])
        self.wo.button_finish()
        self.assertFalse(self.row.quality_native_identity_pending)
        self.assertEqual((self.env.native_calls, len(self.history), len(self.row.time_ids)), (1, 2, 1))

    def test_t17_status_reads_preserve_done_and_physical_quantity(self):
        self.wo.button_finish()
        self.row._quality_native_checks.side_effect = UserError('QC still pending')
        for _ in range(2):
            self.wo._compute_quality_native_status()
            self.assertIn('Обробку завершено (2 з 3)', self.row.quality_native_status)
        self.assertEqual((self.env.native_calls, self.row.state, len(self.history)), (1, 'done', 2))

    def test_direct_done_cannot_obtain_native_finish_capability(self):
        with self.assertRaises(UserError):
            self.wo.write({'state': 'done', 'qty_produced': 2})
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('progress', 0, 0))

    def test_completed_aggregate_is_immutable_even_without_qunit_history(self):
        self.units.clear()
        self.row._quality_native_is_final = lambda: False
        self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('done', 2, 0))
        with self.assertRaises(UserError):
            self.wo.write({'qty_produced': 3})

    def test_other_operator_cannot_start_or_finish_same_active_work(self):
        self.env.user = NS(id=2)
        for call in (self.wo.button_start, self.wo.button_finish):
            with self.assertRaises(UserError):
                call()
        self.assertEqual((self.env.native_starts, self.env.native_calls, len(self.history)), (0, 0, 0))

    def test_native_failure_rolls_back_acknowledgement_timer_and_quantity(self):
        check = self._pending_check()
        self.env.fail_native = True
        with self.assertRaisesRegex(UserError, 'native finish failed'):
            self.wo.button_finish()
        self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('progress', 0, 0))
        self.assertFalse(self.row.time_ids[0].date_end)
        self.assertEqual((check.quality_state, self.row.employee_ids), ('none', [self.operator]))
        self.assertIsNone(self.partial.get())
        self.assertIsNone(self.finish.get())

    def test_local_odoo17_canonical_finish_method_keeps_two_without_closing_mo(self):
        source_root = os.environ.get('ODOO17_SOURCE')
        if not source_root:
            self.skipTest('Set ODOO17_SOURCE to execute the available native method, still without ORM.')
        path = Path(source_root) / 'addons/mrp/models/mrp_workorder.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        native = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MrpWorkorder')
        method = next(node for node in native.body if isinstance(node, ast.FunctionDef) and node.name == 'button_finish')
        scope = {'fields': NS(Datetime=NS(now=lambda: '2026-11-05T02:00:00'))}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), scope)
        self.row.date_start = '2026-11-05T01:00:00'
        self.row.workcenter_id = NS(costs_hour=10)
        with patch.object(NativeDouble, 'button_finish', scope['button_finish']):
            self.wo.button_done()
        self.assertEqual((self.row.state, self.row.qty_produced, self.production.product_qty), ('done', 2, 3))
        self.assertEqual((self.production.qty_produced, len(self.units), len(self.history)), (0, 2, 2))
        self.assertTrue(all(timer.date_end for timer in self.row.time_ids))

    def test_local_enterprise_do_finish_splits_before_button_finish_without_adapter(self):
        source = os.environ.get('ODOO17_ENTERPRISE_WORKORDER')
        if not source:
            self.skipTest('Set ODOO17_ENTERPRISE_WORKORDER to execute the available Enterprise method bodies.')
        path = Path(source)
        tree = ast.parse(path.read_text(encoding='utf-8'))
        enterprise = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                          and node.name == 'MrpProductionWorkcenterLine')
        names = {'do_finish', 'pre_record_production', 'record_production'}
        methods = [node for node in enterprise.body if isinstance(node, ast.FunctionDef) and node.name in names]
        scope = {'_': lambda message: message, 'UserError': UserError,
                 'float_compare': lambda left, right, **kwargs: (left > right) - (left < right)}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), scope)
        native = EnterpriseDouble([self.row], self.env)
        native.qty_remaining = 3
        native.is_first_started_wo = True
        native._check_company = lambda: None
        native.product_uom_id.rounding = 1
        self.production._split_productions = Mock(side_effect=UserError('stock boundary'))
        self.env.__class__.__getitem__ = lambda env, model: NS(search=lambda *args, **kwargs: [True])
        with patch.multiple(EnterpriseDouble, create=True, **{name: scope[name] for name in names}):
            with self.assertRaisesRegex(UserError, 'stock boundary'):
                native.do_finish()
            self.assertTrue(self.row.time_ids[0].date_end)
            self.production._split_productions.assert_called_once_with()
            self.assertEqual(self.env.native_calls, 0)

    def test_unexpected_native_plan_or_stock_change_aborts_whole_finish(self):
        original = NativeDouble.button_finish
        for mutation in ('plan', 'finished', 'raw', 'mo_done'):
            def unexpected(record):
                original(record)
                if mutation == 'plan':
                    record.env.production.product_qty = 2
                elif mutation == 'finished':
                    record.env.production.qty_produced = 2
                elif mutation == 'raw':
                    record.env.production.move_raw_ids.append(NS(id=99, state='done'))
                else:
                    record.env.production.state = 'done'
                return True
            with self.subTest(mutation=mutation), patch.object(NativeDouble, 'button_finish', unexpected):
                with self.assertRaisesRegex(UserError, 'не повинно змінювати план'):
                    self.wo.button_finish()
            self.assertEqual((self.row.state, self.row.qty_produced, len(self.history)), ('progress', 0, 0))
            self.assertEqual((self.production.product_qty, self.production.qty_produced), (3, 0))
            self.assertFalse(self.production.move_raw_ids)


if __name__ == '__main__':
    unittest.main()
