"""Fixture API orchestration contracts; not an Enterprise ORM substitute."""
import json
from types import SimpleNamespace as NS
import unittest

try:
    from .test_quality_defect_contract import shipped_class, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError


def require(condition, message):
    if not condition:
        raise UserError(message)


Probe = shipped_class('models/quality_partial_release_probe.py', 'QualityPartialProbe',
    ['_quality_partial_probe_initialize_qc'], _assert=require, json=json)


class Samples(list):
    @property
    def ids(self):
        return list(self)

    controlled_unit_id = ()


class TestQualityProbeQcContract(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.production = NS(id=1, qty_producing=5, product_id=NS(id=10))
        self.workorder = NS(id=2, production_id=self.production)
        self.check = NS(id=3, production_id=self.production, workorder_id=self.workorder,
            product_id=self.production.product_id, x_control_stage='in_process',
            measurement_matrix_required=False, sample_ids=Samples())
        self.generated = 5
        self.enable_matrix = True
        self.quantity = (5, False)
        self.batch_error = False
        def snapshot():
            self.calls.append('snapshot')
            self.check.measurement_matrix_required = self.enable_matrix
        def matrix():
            self.calls.append('matrix')
            require(self.check.measurement_matrix_required, 'Matrix requires snapshot first')
            if not self.check.sample_ids:
                self.check.sample_ids.extend(range(1, self.generated + 1))
        def identify():
            self.calls.append('identity')
            require(len(self.check.sample_ids) == 5, 'Identity requires generated samples')
            self.check.sample_ids.controlled_unit_id = tuple(range(1, 6))
        def quantity():
            self.calls.append('quantity')
            return self.quantity
        def batch(**kwargs):
            self.assertTrue(kwargs['validate_current'])
            self.calls.append('batch')
            return self.batch_error
        self.check._initialize_measurement_snapshot = snapshot
        self.check.initialize_in_process_measurement_matrix = matrix
        self.check.initialize_qc_unit_identity = identify
        self.check._get_in_process_sample_quantity = quantity
        self.check._get_in_process_batch_error = batch

    def prepare(self):
        return Probe()._quality_partial_probe_initialize_qc(self.production, self.workorder, self.check)

    def test_deferred_matrix_is_generated_before_identity_and_retry_preserves_ids(self):
        self.prepare()
        self.assertEqual(self.calls, ['snapshot', 'matrix', 'quantity', 'batch', 'identity'])
        before = (self.check.sample_ids.ids, self.check.sample_ids.controlled_unit_id)
        self.prepare()
        self.assertEqual((self.check.sample_ids.ids, self.check.sample_ids.controlled_unit_id), before)

    def test_wrong_actual_quantity_source_or_stage_stops_generation(self):
        for record, field, value in ((self.production, 'qty_producing', 0),
                (self.check, 'production_id', NS(id=99)), (self.check, 'workorder_id', NS(id=99)),
                (self.workorder, 'production_id', NS(id=99)),
                (self.check, 'x_control_stage', 'incoming')):
            with self.subTest(field=field):
                previous = getattr(record, field)
                setattr(record, field, value)
                self.calls.clear()
                with self.assertRaisesRegex(UserError, 'stage=preconditions'):
                    self.prepare()
                self.assertNotIn('snapshot', self.calls)
                self.assertNotIn('matrix', self.calls)
                self.assertNotIn('identity', self.calls)
                setattr(record, field, previous)

    def test_snapshot_must_enable_matrix_and_native_errors_are_reported(self):
        self.enable_matrix = False
        self.quantity = (0, 'native quantity unavailable')
        self.batch_error = 'native matrix not configured'
        with self.assertRaises(UserError) as caught:
            self.prepare()
        text = str(caught.exception)
        self.assertIn('stage=snapshot', text)
        self.assertIn('native quantity unavailable', text)
        self.assertIn('native matrix not configured', text)
        self.assertNotIn('matrix', self.calls)
        self.assertNotIn('identity', self.calls)

    def test_wrong_sample_count_never_reaches_identity_or_passes_silently(self):
        for count in (0, 4, 6):
            with self.subTest(count=count):
                self.check.sample_ids = Samples()
                self.generated = count
                self.batch_error = 'native frozen batch mismatch'
                self.calls.clear()
                with self.assertRaises(UserError) as caught:
                    self.prepare()
                self.assertIn('Expected five QC samples', str(caught.exception))
                self.assertIn('native frozen batch mismatch', str(caught.exception))
                self.assertIn('_get_in_process_sample_quantity', str(caught.exception))
                self.assertNotIn('identity', self.calls)

    def test_five_samples_require_five_distinct_identified_units(self):
        def incomplete_identity():
            self.check.sample_ids.controlled_unit_id = (1, 2, 3, 4)
        self.check.initialize_qc_unit_identity = incomplete_identity
        with self.assertRaisesRegex(UserError, 'Expected five identified real QUNIT'):
            self.prepare()

    def test_native_quantity_and_batch_validation_remain_strict(self):
        self.quantity = (4, False)
        with self.assertRaisesRegex(UserError, 'Native QC sample quantity differs from five'):
            self.prepare()
        self.quantity = (5, False)
        self.batch_error = 'native stale batch'
        with self.assertRaisesRegex(UserError, 'native stale batch'):
            self.prepare()
