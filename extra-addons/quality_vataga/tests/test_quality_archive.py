"""Enterprise regression scenarios; no retired stock API is enabled."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from . import test_quality_matrix_hold as holds
from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityArchive(QualityCheckInProcessCase):
    _workorder = holds.TestQualityMatrixHold._workorder
    _identify = holds.TestQualityMatrixHold._identify
    _ready = holds.TestQualityMatrixHold._ready
    _user = holds.TestQualityMatrixHold._user
    _scenario = holds.TestQualityMatrixHold._scenario
    _admit = holds.TestQualityMatrixHold._admit
    _send = holds.TestQualityMatrixHold._send
    _alert = holds.TestQualityMatrixHold._alert
    _business_snapshot = holds.TestQualityMatrixHold._business_snapshot

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity=quantity, uom=uom)
        production.product_qty = quantity
        return production

    def setUp(self):
        super().setUp()
        self.operator = self._user('archive-routing-operator', ['quality.group_quality_user', 'mrp.group_mrp_user'])

    def test_retired_documents_and_wizards_cannot_be_created(self):
        for model in ('quality.unit.release.batch', 'quality.unit.release.line',
                      'quality.unit.release.event', 'quality.unit.stock.release.plan',
                      'quality.stock.timer.binding', 'quality.unit.release.wizard',
                      'quality.unit.release.wizard.line', 'quality.unit.stock.release.wizard',
                      'quality.unit.stock.release.wizard.line', 'quality.stock.timer.binding.wizard'):
            with self.subTest(model=model), self.assertRaises(UserError):
                self.env[model].with_context(quality_vataga_release_authorization=True,
                    quality_vataga_stock_release_plan=True, quality_stock_kernel=True).create({})

    def test_retired_actions_and_stock_api_are_explicitly_rejected(self):
        check, _, _ = self._scenario()
        production = check.production_id
        before = self._business_snapshot(production)
        for action in ('action_open_unit_release_wizard', 'action_open_unit_release_cancel_wizard',
                       'action_open_unit_stock_release_wizard', 'action_open_stock_timer_binding'):
            with self.subTest(action=action), self.assertRaises(UserError):
                getattr(production, action)()
        with self.assertRaises(UserError):
            self.env['quality.stock.posting']._post_plan(False)
        self.assertEqual(self._business_snapshot(production), before)

    def test_pass_transfers_need_no_retired_documents(self):
        check, _, target = self._scenario()
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        self._alert(check)
        self._admit(check, good)
        self._send(check, target, good).action_apply()
        self.assertEqual(check.x_transferred_qty, 4)
        self.assertEqual(check.x_held_qty, 1)
        self.assertFalse(check.production_id.quality_release_batch_ids)
        self.assertFalse(check.production_id.quality_stock_release_plan_ids)
        self.assertFalse(check.production_id.can_prepare_unit_stock_release)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            check.production_id.button_mark_done()

    def test_archive_fields_cannot_be_injected_into_live_units_or_alerts(self):
        check, _, _ = self._scenario()
        unit = check.sample_ids[0].controlled_unit_id
        with self.assertRaises(UserError):
            unit.write({'quality_release_revision': 1})
        alert = self._alert(check)
        with self.assertRaises(UserError):
            alert.write({'quality_release_scope_unit_id': unit.id})
        # Ordinary board approval and five-stage lifecycle remain exercised by
        # their unchanged dedicated tests; they no longer certify good units.
