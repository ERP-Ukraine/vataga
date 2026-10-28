"""Enterprise ORM coverage of closure evidence; never runs on a live DB."""

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityAlertLifecycle(QualityCheckInProcessCase):
    def setUp(self):
        super().setUp()
        self.manager = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Closure manager', 'login': 'quality_alert_closure_manager',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in (
                'quality.group_quality_manager', 'mrp.group_mrp_user', 'stock.group_stock_user')])],
        })

    def _approved(self, check, decision='accept_as_is'):
        alert = self.env['quality.alert'].with_user(self.manager).create({
            'name': 'Closure evidence', 'team_id': self.team.id, 'check_id': check.id,
            'decision_type': decision, 'approval_user_id': self.manager.id,
        })
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        return alert

    def test_in_process_fail_and_reworked_pass_cannot_bypass_missing_13_4(self):
        production = self._production()
        check = self._check(production)
        self._fill(check)
        failed = check.sample_ids[-1]
        check.update_measurement_visual_result(failed.id, 'no')
        alert = self._approved(check)
        before = (production.state, production.qty_produced, production.move_finished_ids.read(),
                  production.move_raw_ids.read(), alert.vataga_matrix_failure_snapshot)
        with self.assertRaisesRegex(UserError, '13.4'):
            alert.action_close_alert()
        check.update_measurement_visual_result(failed.id, 'yes')
        self.assertEqual(failed.sample_result, 'pass')
        with self.assertRaisesRegex(UserError, '13.4'):
            alert.action_close_alert()
        self.assertFalse(alert.vataga_closed_at)
        self.assertTrue(alert._matrix_hold_is_open())
        self.assertEqual(alert.stage_id, self.env.ref('quality_vataga.quality_alert_stage_board_execution'))
        self.assertEqual(before, (production.state, production.qty_produced, production.move_finished_ids.read(),
                                production.move_raw_ids.read(), alert.vataga_matrix_failure_snapshot))

    def test_incoming_full_control_requires_complete_good_batch_and_freezes_lines(self):
        kind = self.warehouse.in_type_id
        point = self.point.copy({'x_control_stage': 'incoming', 'picking_type_ids': [Command.set(kind.ids)]})
        picking = self.env['stock.picking'].create({
            'picking_type_id': kind.id,
            'location_id': kind.default_location_src_id.id,
            'location_dest_id': kind.default_location_dest_id.id,
        })
        self.env['stock.move'].create({
            'name': 'Closure batch', 'picking_id': picking.id, 'product_id': self.product.id,
            'product_uom': self.product.uom_id.id, 'product_uom_qty': 3,
            'location_id': picking.location_id.id, 'location_dest_id': picking.location_dest_id.id,
        })
        check = self._check(point=point, picking_id=picking.id)
        self.assertEqual(check.operation_product_quantity, 3)
        alert = self._approved(check, 'full_control')
        with self.assertRaises(UserError):
            alert.action_close_alert()
        line = self.env['quality.alert.full.control.line'].with_user(self.manager).create({
            'alert_id': alert.id, 'checked_qty': 1, 'good_qty': 1, 'bad_qty': 0,
        })
        with self.assertRaises(UserError):
            alert.action_close_alert()
        line.write({'checked_qty': 3, 'good_qty': 2, 'bad_qty': 1})
        with self.assertRaises(UserError):
            alert.action_close_alert()
        line.write({'good_qty': 3, 'bad_qty': 0})
        alert.action_close_alert()
        self.assertTrue(alert._is_managed_closed())
        self.assertEqual(picking.state, 'draft')
        for method in (lambda: line.write({'checked_qty': 0, 'good_qty': 0}), line.unlink,
                       lambda: line.copy(), lambda: alert.write({'full_control_line_ids': [Command.clear()]})):
            with self.assertRaises(UserError):
                method()

    def test_qc_form_creation_stays_new_despite_foreign_context_or_explicit_stage(self):
        check = self._check(self._production())
        self._fill(check)
        check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
        action = check.action_open_matrix_quality_alert()
        closed = self.env.ref('quality_vataga.quality_alert_stage_closed')
        alerts = self.env['quality.alert'].with_context(dict(action['context'], default_stage_id=closed.id))
        alert = alerts.create({'name': 'QC form lifecycle'})
        self.assertEqual(alert.stage_id, self.env.ref('quality_vataga.quality_alert_stage_new'))
        self.assertEqual(alert.check_id, check)
        self.assertTrue(alert.vataga_matrix_failure_snapshot)
        self.assertTrue(alert._matrix_hold_is_open())
        explicit = alerts.create({'name': 'Normalized QC stage', 'stage_id': closed.id})
        self.assertEqual(explicit.stage_id, self.env.ref('quality_vataga.quality_alert_stage_new'))
        self.assertFalse(explicit.vataga_closed_at)

    def test_production_alert_without_qc_cannot_bypass_missing_13_4(self):
        if 'production_id' not in self.env['quality.alert']._fields:
            self.skipTest('The installed Enterprise build has no direct Alert -> MO link.')
        alert = self.env['quality.alert'].with_user(self.manager).create({
            'name': 'MO disposition without QC', 'team_id': self.team.id,
            'production_id': self._production().id,
            'decision_type': 'accept_as_is', 'approval_user_id': self.manager.id,
        })
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        with self.assertRaisesRegex(UserError, '13.4'):
            alert.action_close_alert()
