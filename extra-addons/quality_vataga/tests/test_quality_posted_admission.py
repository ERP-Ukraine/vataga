"""Enterprise ORM regression on disposable fixtures; never edits CAB/MO/05121."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models.quality_unit_admission import ADMISSION_CONTEXT, ADMISSION_TOKEN
from . import test_quality_full_release_lot as lot_tests


@tagged('post_install', '-at_install', 'quality_posted_admission')
class TestQualityPostedAdmission(lot_tests.QualityFullReleaseCase):
    _lot_prepared = lot_tests.TestQualityFullReleaseLot._lot_prepared

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.tracking = 'lot'  # Disposable fixture product only.

    def test_posted_confirmations_remain_valid_and_all_new_actions_are_blocked(self):
        production, check, lot = self._lot_prepared()
        admissions = check.unit_admission_ids
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (2, 0))
        audit = admissions.read(['state', 'inspector_id', 'confirmed_at', 'evidence_snapshot'])
        production.button_mark_done()
        admissions.invalidate_recordset(['is_effective', 'ineffective_reason'])
        check.invalidate_recordset(['x_admitted_qty', 'x_held_qty'])
        self.assertTrue(all(admissions.mapped('is_effective')))
        self.assertFalse(any(admissions.mapped('ineffective_reason')))
        self.assertEqual((check.x_admitted_qty, check.x_held_qty), (2, 0))
        self.assertEqual(check.quality_state, 'pass')
        self.assertEqual(audit, admissions.read(['state', 'inspector_id', 'confirmed_at', 'evidence_snapshot']))
        before = self._snapshot(production)
        for create in (self.env['quality.unit.admission'], self.env['quality.unit.admission'].with_context(
                **{ADMISSION_CONTEXT: ADMISSION_TOKEN})):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                create.create({'check_id': check.id, 'sample_id': check.sample_ids[0].id, 'request_key': 'post-release'})
        for request in ('post-release', 'qc-completion-%s' % check.id):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                check._execute_unit_admission_action(check.sample_ids.ids, 'confirm', request)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            admissions[0]._revoke('Not permitted after posting')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            check._identify_samples('existing', [
                {'sample_id': sample.id, 'unit_id': sample.controlled_unit_id.id} for sample in check.sample_ids],
                run=check.operation_run_id, workorder=check.operation_run_id.workorder_id, confirmed=True)
        with self.assertRaises(UserError):
            check.sample_ids.controlled_unit_id._assert_not_stock_posted()
        self.assertEqual(before, self._snapshot(production))
        self.assertEqual(len(production.native_stock_fact_ids), 2)
