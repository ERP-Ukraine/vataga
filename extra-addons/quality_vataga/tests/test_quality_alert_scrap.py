"""Enterprise safety tests of the explicitly blocked Scrap integration.

Positive stock posting/replacement scenarios require an identified posted QUNIT
contract; these tests deliberately do not invent it or mock stock evidence.
"""

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_alert_repair as repairs


@tagged('post_install', '-at_install')
class TestQualityAlertScrap(QualityCheckInProcessCase):
    _workorder = repairs.TestQualityAlertRepair._workorder
    _identify = repairs.TestQualityAlertRepair._identify
    _ready = repairs.TestQualityAlertRepair._ready
    _user = repairs.TestQualityAlertRepair._user
    _scenario = repairs.TestQualityAlertRepair._scenario
    _alert = repairs.TestQualityAlertRepair._alert
    _admit = repairs.TestQualityAlertRepair._admit
    _send = repairs.TestQualityAlertRepair._send
    _business_snapshot = repairs.TestQualityAlertRepair._business_snapshot

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity=quantity, uom=uom)
        production.product_qty = quantity
        return production

    def setUp(self):
        super().setUp()
        self.operator = self._user('scrap-inspector', ['quality.group_quality_user', 'mrp.group_mrp_user'])
        self.manager = self._user('scrap-manager', [
            'quality.group_quality_manager', 'mrp.group_mrp_user', 'stock.group_stock_user'])

    def _approved(self, alert):
        return repairs.TestQualityAlertRepair._approve(self, alert, decision='scrap')

    def _snapshot(self, check, alert):
        return (self._business_snapshot(check.production_id), check.sample_ids.read(),
                check.sample_ids.measurement_value_ids.read(), check.operation_run_id.read(),
                alert.vataga_matrix_failure_snapshot, alert.message_ids.ids,
                self.env['stock.scrap'].search([]).ids,
                self.env['stock.move'].search([]).ids,
                self.env['stock.valuation.layer'].search([]).ids,
                self.env['stock.quant'].search([('product_id', '=', check.product_id.id)]).read())

    def test_no_approval_blocks_scrap_replacement_and_close_without_documents(self):
        check, _, _ = self._scenario()
        alert = self._alert(check).with_user(self.manager)
        alert.write({'decision_type': 'scrap', 'approval_user_id': self.manager.id})
        self.assertEqual(alert.vataga_scrap_status, 'approval')
        before = self._snapshot(check, alert)
        for operation in (alert.action_scrap_defective_units, alert.action_create_scrap_replacement, alert.action_close_alert):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(self._snapshot(check, alert), before)

    def test_approved_scrap_waits_for_stock_and_never_manufactures_evidence(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        self.assertEqual(alert.vataga_scrap_status, 'waiting_stock')
        self.assertIn('stock.move.line', alert.vataga_scrap_block_reason)
        self.assertEqual(alert.vataga_failure_sample_ids, check.sample_ids[-1:])
        before = self._snapshot(check, alert)
        for _attempt in range(2):
            for operation in (alert.action_scrap_defective_units, alert.action_create_scrap_replacement, alert.action_close_alert):
                with self.assertRaises(UserError):
                    operation()
        self.assertEqual(self._snapshot(check, alert), before)
        self.assertFalse(alert.vataga_closed_at)
        self.assertTrue(alert._matrix_hold_is_open())

    def test_four_good_units_can_move_independently_of_scrap_waiting(self):
        check, _, target = self._scenario()
        alert = self._approved(self._alert(check))
        bad = alert.vataga_failure_sample_ids
        good = check.sample_ids - bad
        self._admit(check, good)
        self._send(check, target, good).action_apply()
        records = self.env['quality.unit.transfer'].search([('check_id', '=', check.id)])
        self.assertEqual(len(records), 4)
        self.assertNotIn(bad.controlled_unit_id, records.controlled_unit_id)
        with self.assertRaises(ValidationError):
            self._admit(check, bad)
        with self.assertRaises(UserError):
            alert.action_scrap_defective_units()
        self.assertEqual(check.production_id.qty_produced, 0)

    def test_pass_cannot_replace_frozen_fail_or_prove_scrap(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        scope = alert.vataga_failure_sample_ids
        with self.assertRaises(UserError):
            alert.write({'vataga_failure_sample_ids': [Command.set(check.sample_ids[:1].ids)]})
        check.update_measurement_visual_result(scope.id, 'yes')
        self.assertTrue(scope.x_was_reworked)
        self.assertEqual(alert.vataga_failure_sample_ids, scope)
        self.assertEqual(alert.vataga_scrap_status, 'blocked')
        with self.assertRaises(UserError):
            alert.action_close_alert()
        self.assertTrue(check._get_unit_admission_error(scope))

    def test_old_unscoped_alert_requires_existing_explicit_mapping(self):
        check, _, _ = self._scenario('pass')
        alert = self._approved(self._alert(check))
        self.assertEqual(alert.vataga_scrap_status, 'scope')
        affected = check.sample_ids[-1:]
        check.update_measurement_visual_result(affected.id, 'no')
        alert._bind_matrix_failure_scope(affected, 'Первинний акт браку встановив конкретний QUNIT')
        self.assertEqual(alert.vataga_scrap_status, 'waiting_stock')
        with self.assertRaises(UserError):
            alert.action_scrap_defective_units()

    def test_two_alerts_cannot_duplicate_scrap_replacement_or_remove_each_others_hold(self):
        check, _, _ = self._scenario()
        first = self._approved(self._alert(check))
        second = self._approved(self._alert(check))
        before = (self.env['stock.scrap'].search([]).ids, self.env['mrp.production'].search([]).ids)
        for alert in (first, second, first):
            for operation in (alert.action_scrap_defective_units, alert.action_create_scrap_replacement, alert.action_close_alert):
                with self.assertRaises(UserError):
                    operation()
        self.assertTrue(first._matrix_hold_is_open())
        self.assertTrue(second._matrix_hold_is_open())
        self.assertEqual(before, (self.env['stock.scrap'].search([]).ids, self.env['mrp.production'].search([]).ids))

    def test_native_scrap_and_backorder_barriers_remain_after_board_approval(self):
        check, _, target = self._scenario()
        alert = self._approved(self._alert(check))
        good = check.sample_ids - alert.vataga_failure_sample_ids
        self._admit(check, good)
        self._send(check, target, good).action_apply()
        scrap = self.env['stock.scrap'].create({
            'product_id': check.product_id.id, 'production_id': check.production_id.id,
            'scrap_qty': 1, 'location_id': check.production_id.location_dest_id.id})
        before = self._snapshot(check, alert)
        for operation in (scrap.do_scrap, check.production_id.button_mark_done,
                          check.production_id._split_productions, check.production_id._post_inventory):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(self._snapshot(check, alert), before)
        self.assertTrue(check.production_id.quality_transfer_controlled)

    def test_matching_product_stock_is_not_accepted_as_specific_qunit_evidence(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        # Real separate receipt in the disposable Enterprise test transaction.
        # It has the same product, but no provenance from this QUNIT or MO.
        receipt = self.env['stock.move'].create({
            'name': 'Unrelated receipt, not QUNIT evidence', 'product_id': check.product_id.id,
            'product_uom': check.product_id.uom_id.id, 'product_uom_qty': 1,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
        })
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 1, 'picked': True})
        receipt._action_done()
        before = self._snapshot(check, alert)
        with self.assertRaises(UserError):
            alert.action_scrap_defective_units()
        self.assertEqual(alert.vataga_scrap_status, 'waiting_stock')
        self.assertEqual(self._snapshot(check, alert), before)

    def test_draft_or_real_unrelated_scrap_never_creates_replacement(self):
        self.product.type = 'product'
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        receipt = self.env['stock.move'].create({
            'name': 'Disposable unrelated scrap origin', 'product_id': check.product_id.id,
            'product_uom': check.product_id.uom_id.id, 'product_uom_qty': 1,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
        })
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 1, 'picked': True})
        receipt._action_done()
        scrap = self.env['stock.scrap'].create({
            'product_id': check.product_id.id, 'product_uom_id': check.product_id.uom_id.id,
            'scrap_qty': 1, 'location_id': self.warehouse.lot_stock_id.id,
            'company_id': self.env.company.id, 'should_replenish': False,
        })
        productions = self.env['mrp.production'].search([]).ids
        with self.assertRaises(UserError):
            alert.action_create_scrap_replacement()
        self.assertEqual(scrap.state, 'draft')
        scrap.action_validate()  # Actual unrelated Scrap only in this disposable test.
        self.assertEqual(scrap.state, 'done')
        self.assertTrue(scrap.move_ids)
        self.assertFalse(scrap.production_id)
        before = self._snapshot(check, alert)
        for attempt in range(2):
            with self.assertRaises(UserError):
                alert.action_create_scrap_replacement()
        self.assertEqual(self.env['mrp.production'].search([]).ids, productions)
        self.assertEqual(self._snapshot(check, alert), before)
        self.assertEqual(check.production_id.qty_produced, 0)
        self.assertTrue(alert._matrix_hold_is_open())

    def test_context_cannot_claim_done_scrap_or_replacement(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check)).with_context(
            default_state='done', default_scrap_qty=5, default_should_replenish=True,
            skip_backorder=True, vataga_scrap_done=True, default_product_qty=5)
        before = self._snapshot(check, alert)
        for operation in (alert.action_scrap_defective_units, alert.action_create_scrap_replacement):
            with self.assertRaises(UserError):
                operation()
        self.assertEqual(self._snapshot(check, alert), before)

    def test_approval_stage_folding_or_imported_closed_stage_never_counts_as_scrap(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        for name in ('fold', 'done'):
            if name in alert.stage_id._fields:
                alert.stage_id[name] = True
        self.assertTrue(alert._matrix_hold_is_open())
        self.env.cr.execute('''UPDATE quality_alert SET stage_id=%s,
            vataga_closed_by_id=%s, vataga_closed_at=now() WHERE id=%s''',
            [self.env.ref('quality_vataga.quality_alert_stage_closed').id, self.manager.id, alert.id])
        alert.invalidate_recordset()
        self.assertFalse(alert._is_managed_closed())
        self.assertTrue(alert._matrix_hold_is_open())

    def test_access_company_and_decision_guards_are_not_relaxed(self):
        check, _, _ = self._scenario()
        alert = self._approved(self._alert(check))
        outsider = self._user('scrap-no-rights', [])
        for action in ('action_scrap_defective_units', 'action_create_scrap_replacement'):
            with self.assertRaises(AccessError):
                getattr(alert.with_user(outsider), action)()
        other = self.env['res.company'].create({'name': 'Scrap unrelated company'})
        self.manager.company_ids = [Command.link(other.id)]
        restricted = alert.with_context(allowed_company_ids=other.ids)
        with self.assertRaises((AccessError, UserError)):
            restricted.action_scrap_defective_units()
        self.assertFalse(alert.vataga_closed_at)
        with self.assertRaises(UserError):
            alert.action_record_repair('Не те рішення ТР')
        with self.assertRaises(UserError):
            alert.action_create_repair_check()

    def test_readiness_view_is_readonly_and_has_no_unsafe_execute_buttons(self):
        arch = etree.fromstring(self.env['quality.alert'].get_view(
            view_id=self.env.ref('quality_control.quality_alert_view_form').id, view_type='form')['arch'])
        pages = arch.xpath("//page[@name='vataga_scrap']")
        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0].xpath('.//button'))
        self.assertFalse(arch.xpath("//notebook[@name='technical_board_notebook']/page[@name='vataga_scrap']"))
        for name in ('vataga_scrap_status', 'vataga_scrap_block_reason', 'vataga_failure_sample_ids'):
            self.assertEqual(pages[0].xpath(".//field[@name='%s']/@readonly" % name), ['1'])
