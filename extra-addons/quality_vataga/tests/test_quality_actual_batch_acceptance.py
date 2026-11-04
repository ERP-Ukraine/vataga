"""Run only with the real Enterprise ORM on a disposable test database.

The server fixture methods are exercised without copying their implementation.
Fault patches exist only in this isolated test process, never in Scheduled Action.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from ..models.quality_actual_batch_acceptance_probe import AUDIT_MODELS, _ProbeSkip
from ..models.quality_partial_release_probe import _ProbeRollback


@tagged('post_install', '-at_install', 'quality_actual_batch_acceptance')
class TestActualBatchAcceptanceProbe(TransactionCase):
    def setUp(self):
        super().setUp()
        self.probe = self.env['mrp.production'].with_context(
            tracking_disable=True, mail_notrack=True, mail_create_nosubscribe=True,
            mail_create_nolog=True, mail_notify_force_send=False)

    def _counts(self):
        return {model: self.env[model].search_count([]) for model in AUDIT_MODELS}

    def _case_rolled_back(self, code):
        before = self._counts()
        progress = {}
        with self.assertRaises(_ProbeRollback):
            with self.env.cr.savepoint():
                result = self.probe._quality_actual_probe_case(code, progress)
                self.assertEqual(result['status'], 'PASS')
                self.assertTrue(result['release_action_called'])
                raise _ProbeRollback()
        self.env.invalidate_all(flush=False)
        self.assertEqual(self._counts(), before)
        return result

    def test_p1_n14_real_stock_rollback_and_no_fictional_early_evidence(self):
        for code in ('P1', 'N14'):
            with self.subTest(code=code):
                result = self._case_rolled_back(code)
                self.assertEqual((result['finished_quantity'], result['component_quantity'],
                                  result['backorder_quantity'], result['stock_facts']), (3, 6, 2, 3))
                self.assertTrue(result['historical_evidence_unchanged'])

    def test_negatives_call_real_action_and_match_exact_business_condition(self):
        for code in ('N1', 'N2', 'N3', 'N4', 'N5', 'N7', 'N8', 'N12'):
            with self.subTest(code=code):
                result = self._case_rolled_back(code)
                self.assertTrue(result['stock_unchanged'])
                self.assertTrue(result['reason'])

    def test_n6_safety_skip_has_no_fixture_or_foreign_identity_injection(self):
        before = self._counts()
        with self.assertRaises(_ProbeSkip):
            self.probe._quality_actual_probe_case('N6', {})
        self.assertEqual(self._counts(), before)

    def test_unexpected_error_after_real_stock_still_rolls_back(self):
        before = self._counts()
        original = type(self.probe)._quality_actual_probe_positive
        def fail_after_stock(record, scene):
            original(record, scene)
            raise RuntimeError('Injected after real probe stock')
        with patch.object(type(self.probe), '_quality_actual_probe_positive', fail_after_stock):
            with self.assertRaisesRegex(RuntimeError, 'Injected'), self.env.cr.savepoint():
                self.probe._quality_actual_probe_case('P1', {})
        self.env.invalidate_all(flush=False)
        self.assertEqual(self._counts(), before)

    def test_production_and_nonallowlisted_databases_cannot_open_transaction(self):
        before = self._counts()
        for expected in ('production', self.env.cr.dbname + '-wrong', ''):
            with self.assertRaises(AccessError):
                self.probe._run_quality_actual_batch_acceptance_probe(expected_database=expected)
        self.assertEqual(self._counts(), before)
