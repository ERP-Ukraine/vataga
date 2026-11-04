"""Execute manufacturing boundary methods without Enterprise or PostgreSQL.

The serialized-request test models the transaction lock with a Python lock.
It proves retry behavior, not PostgreSQL isolation, deadlock or stock semantics.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import math
from threading import RLock
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from test_quality_defect_contract import shipped_class, UserError
from test_quality_automatic_workorder_contract import Rows, Record


class TestUnidentifiedManufacturing(unittest.TestCase):
    def setUp(self):
        self.scope = ContextVar('physical_test', default=None)
        cls = shipped_class('models/quality_native_workorder.py', 'QualityNativeWorkorder',
            {'_quality_confirm_on_native_finish'}, math=math, _PARTIAL_EXECUTION=self.scope,
            native_operation_allows=lambda production: False)
        wo = self.wo = cls()
        wo.id, wo.state, wo.qty_produced = 10, 'progress', 0
        wo.qty_production, wo.qty_producing = 3, 2
        wo.quality_transfer_controlled = wo.quality_native_identity_pending = False
        wo.ensure_one = wo.invalidate_recordset = Mock()
        wo._quality_native_is_final = lambda: True
        wo._quality_native_automatic = lambda: True
        wo._prepare_quality_native_checks = Mock()
        wo._check_quality_native_start = Mock()
        wo._check_quality_execution_access = Mock()
        wo._quality_native_history = lambda: Rows()
        wo.production_id = NS(_check_quality_transfer_boundary=Mock(), _full_release_units=lambda: Rows())
        wo.product_uom_id = NS(_compute_quantity=lambda qty, target, round=False: qty)
        operator = object()
        wo.env = NS(cr=object(), user=operator, ref=lambda name: wo.product_uom_id)
        wo.time_ids = Rows([Record(id=1, user_id=operator, date_end=False)])
        wo.button_pending = Mock()
        self.writes = []
        def write(values):
            self.assertEqual(self.scope.get(), (wo.env.cr, wo.id, frozenset(), 2))
            self.writes.append(values)
            wo.qty_produced = values['qty_produced']
            wo.quality_native_identity_pending = values['quality_native_identity_pending']
        wo.write = write

    def test_t3_missing_identity_retains_actual_two_without_fabricating_execution(self):
        self.assertTrue(self.wo._quality_confirm_on_native_finish())
        self.assertEqual((self.wo.qty_produced, self.wo.state), (2, 'progress'))
        self.assertTrue(self.wo.quality_native_identity_pending)
        self.assertEqual(self.writes, [{'qty_produced': 2, 'quality_native_identity_pending': True}])
        self.assertIsNone(self.scope.get())

    def test_t8_t16_serialized_concurrent_confirmations_do_not_double_quantity(self):
        lock = RLock()
        def request():
            with lock:  # Models the existing MO transaction lock, not its DB implementation.
                return self.wo._quality_confirm_on_native_finish()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: request(), range(2))), [True, True])
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.wo.qty_produced, 2)

    def test_established_three_identities_are_not_guessed_to_be_two(self):
        self.wo.production_id._full_release_units = lambda: Rows(Record(id=i) for i in range(3))
        self.assertTrue(self.wo._quality_confirm_on_native_finish())
        self.assertTrue(self.wo.quality_native_identity_pending)
        self.assertEqual(self.wo.qty_produced, 2)

    def test_invalid_actual_quantities_cannot_be_recorded(self):
        for quantity in (0, -1, 1.5, 4, math.nan, math.inf):
            self.wo.qty_producing = quantity
            with self.subTest(quantity=quantity), self.assertRaises(UserError):
                self.wo._quality_confirm_on_native_finish()
        self.assertFalse(self.writes)


class TestDraftSamplePreparation(unittest.TestCase):
    def setUp(self):
        class Parent:
            def write(self, values):
                self.__dict__.update(values)
        cls = shipped_class('models/quality_check_in_process.py', 'QualityCheckInProcess',
            {'_ensure_in_process_measurement_samples'}, parent=Parent,
            IN_PROCESS_SAMPLE_TOKEN=object())
        check = self.check = cls()
        check.id, check.x_control_stage, check.quality_state = 1, 'in_process', 'none'
        check.measurement_matrix_required = True
        check.in_process_sample_quantity = 0
        check.operation_run_id = False
        check.sample_ids = Rows()
        check.filtered = lambda predicate: [check] if predicate(check) else []
        check._ensure_measurement_editable = check.invalidate_recordset = Mock()
        check.env = NS(cr=NS(execute=Mock()))
        check._get_in_process_sample_quantity = lambda: (2, False)
        check.with_context = lambda **kwargs: check
        self.created = []
        def create(quantity):
            self.created.append(quantity)
            start = len(check.sample_ids) + 1
            check.sample_ids |= Rows(Record(id=i, sample_number=i, controlled_unit_id=Rows(),
                invalidate_recordset=Mock(), measurement_value_ids=Mock(),
                _has_entered_results=lambda: False, x_was_reworked=False) for i in range(start, start + quantity))
        check._create_measurement_samples = create

    def _alerts(self, count):
        class Env:
            cr = self.check.env.cr
            def __getitem__(self, model):
                return self
            def sudo(self):
                return self
            def search_count(self, domain, limit=None):
                return count
        self.check.env = Env()

    def test_t6_t7_two_pending_samples_exist_before_qc_and_retry_is_stable(self):
        self.check._ensure_in_process_measurement_samples()
        original = self.check.sample_ids.ids
        self.check._ensure_in_process_measurement_samples()
        self.assertEqual(self.created, [2])
        self.assertEqual(original, self.check.sample_ids.ids)
        self.assertEqual(self.check.in_process_sample_quantity, 2)

    def test_blank_unidentified_matrix_can_grow_with_stable_numbers(self):
        self.check._ensure_in_process_measurement_samples()
        self._alerts(0)
        self.check._get_in_process_sample_quantity = lambda: (3, False)
        self.check._ensure_in_process_measurement_samples(manufactured=True)
        self.assertEqual(self.check.sample_ids.ids, [1, 2, 3])
        self.assertEqual(self.check.in_process_sample_quantity, 3)
        self.assertEqual(self.created, [2, 1])

    def test_alert_references_prevent_automatic_sample_resize(self):
        self.check._ensure_in_process_measurement_samples()
        self._alerts(1)
        self.check._get_in_process_sample_quantity = lambda: (1, False)
        self.check._ensure_in_process_measurement_samples(manufactured=True)
        self.assertEqual(self.check.sample_ids.ids, [1, 2])
        self.assertEqual(self.check.in_process_sample_quantity, 2)

    def test_entered_results_and_identifiers_prevent_automatic_resize(self):
        self.check._ensure_in_process_measurement_samples()
        self.check._get_in_process_sample_quantity = lambda: (1, False)
        sample = self.check.sample_ids[0]
        sample._has_entered_results = lambda: True
        self.check._ensure_in_process_measurement_samples(manufactured=True)
        self.assertEqual((self.check.in_process_sample_quantity, len(self.check.sample_ids)), (2, 2))
        sample._has_entered_results = lambda: False
        self.check.operation_run_id = object()
        self.check._ensure_in_process_measurement_samples(manufactured=True)
        self.assertEqual((self.check.in_process_sample_quantity, len(self.check.sample_ids)), (2, 2))


class TestSerializedReleaseRequests(unittest.TestCase):
    def test_t9_t16_only_one_request_reaches_native_stock_completion(self):
        transaction_lock = RLock()
        @contextmanager
        def transaction():
            with transaction_lock:
                yield
        def operation(production, quantity, units, actual_batch=False):
            return NS(activate=nullcontext)
        cls = shipped_class('models/quality_actual_batch_release.py', 'QualityActualBatchProduction',
            {'action_release_quality_actual_batch'}, _require_roles=lambda env: None,
            NativeBackorderOperation=operation, float_round=lambda value, **kwargs: value)
        p = cls()
        p.id, p.state, p.product_qty = 1, 'progress', 3
        p.quality_unit_route_required = True
        p.company_id = Record(id=1)
        p.env = NS(cr=NS(savepoint=transaction), companies=Rows([p.company_id]),
                   lang='uk_UA', user=NS(tz='Europe/Kyiv'))
        p.ensure_one = p.check_access_rights = p.check_access_rule = Mock()
        p.with_context = lambda context: p
        p.with_company = lambda company: p
        p.invalidate_recordset = p._lock_quality_transfer_scope = Mock()
        units = Rows([Record(id=101), Record(id=102)])
        p._full_release_units = lambda: units
        p._full_release_workorders = lambda: Rows()
        p._validate_quality_actual_batch = p._validate_quality_batch_controls = Mock()
        p.move_raw_ids = Rows([Record(id=1, state='assigned', unit_factor=2, product_uom=NS(rounding=1))])
        p._check_quality_actual_batch_stock = Mock()
        p._record_standard_stock_facts = Mock()
        p.message_post = Mock()
        child = NS(state='confirmed', qty_produced=0, quality_transfer_controlled=False,
                   quality_unit_route_required=True, display_name='Remainder',
                   _full_release_units=lambda: Rows(), _full_release_checks=lambda: Rows())
        def complete():
            p.state, p.product_qty, p.qty_produced = 'done', 2, 2
            return child
        p._native_quality_complete = Mock(side_effect=complete)
        def request():
            try:
                p.action_release_quality_actual_batch()
                return 'released'
            except UserError:
                return 'denied'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: request(), range(2)))
        self.assertEqual(sorted(outcomes), ['denied', 'released'])
        p._native_quality_complete.assert_called_once()
        p._record_standard_stock_facts.assert_called_once_with(units)
