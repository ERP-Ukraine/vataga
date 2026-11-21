from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_incoming_stock import IncomingStockFixture


@tagged('post_install', '-at_install', 'incoming_stage_a')
class TestIncomingQuality(IncomingStockFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.point = cls.env['quality.point'].create({
            'title': 'A Incoming Visual Sampling', 'x_control_stage': 'incoming',
            'picking_type_ids': [Command.set(cls.warehouse.in_type_id.ids)],
            'product_ids': [Command.set(cls.product.ids)],
            'test_type_id': cls.env['quality.point.test_type'].search([('technical_name', '=', 'passfail')], limit=1).id,
            'team_id': cls.env['quality.alert.team'].search([], limit=1).id,
            'visual_sample_control_required': True, 'incoming_sample_size': 2,
            'incoming_acceptance_policy': 'all_pass',
        })

    def test_standard_pre_receipt_checks_are_deferred_without_changing_results(self):
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse.in_type_id.id, 'partner_id': self.vendor.id,
            'location_id': self.supplier_location.id, 'location_dest_id': self.warehouse.lot_stock_id.id,
            'move_ids': [Command.create({'name': 'A Deferred receipt', 'product_id': self.product.id,
                'product_uom': self.product.uom_id.id, 'product_uom_qty': 10,
                'location_id': self.supplier_location.id, 'location_dest_id': self.warehouse.lot_stock_id.id})],
        })
        picking.action_confirm()
        self.assertFalse(self.env['quality.check'].search([
            ('picking_id', '=', picking.id), ('point_id', '=', self.point.id)]))
        picking.move_ids.quantity = 10
        picking.move_ids.picked = True
        picking._action_done()
        self.assertEqual(len(picking.quality_incoming_hold_ids.check_ids), 1)
        self.assertEqual(len(picking.quality_incoming_hold_ids.check_ids.sample_ids), 2)

    def test_qcp_plan_is_snapshotted_and_all_pass_allows_batch(self):
        _picking, hold = self._receipt()
        check = hold.check_ids
        self.assertEqual(len(check.sample_ids), 2)
        self.point.incoming_sample_size = 8
        check.sample_ids.write({'visual_result': 'yes'})
        check.do_pass()
        self.assertEqual(check.incoming_sample_size, 2)
        self.assertTrue(hold._sampling_passed())
        hold._execute('release', 'unchecked', 10, 'Two required samples passed', 'samplepass')
        self.assertEqual(hold.remaining_qty, 0)

    def test_fail_creates_one_alert_and_quick_return_without_full_control(self):
        picking, hold = self._receipt()
        check = hold.check_ids
        check.sample_ids[:1].write({'visual_result': 'no'})
        check.do_fail()
        check.do_fail()
        self.assertEqual(len(hold.alert_ids), 1)
        alert = hold.alert_ids
        self.assertEqual(alert.check_id, check)
        self.assertEqual(alert.incoming_receipt_id, picking)
        self.assertEqual(alert.incoming_supplier_id, self.vendor)
        self.assertEqual(alert.incoming_product_id, self.product)
        self.assertIn(picking.name, alert.title)
        self.assertIn('Зразок №1', alert.incoming_failure_description)
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'Cannot accept FAIL', 'fail')
        operation = hold._execute('return', 'unchecked', 10, 'Quick supplier return', 'quick')
        self.assertEqual(operation.picking_id.return_id, picking)
        self.assertFalse(hold.inspection_ids)

    def test_missing_plan_holds_batch_until_explicit_initialization(self):
        self.point.incoming_sample_size = 0
        _picking, hold = self._receipt()
        self.assertFalse(hold.check_ids)
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'Missing plan', 'missing')
        self.point.incoming_sample_size = 3
        hold.action_start_checks()
        self.assertEqual(len(hold.check_ids.sample_ids), 3)

    def test_pending_and_direct_rpc_cannot_bypass_matrix(self):
        _picking, hold = self._receipt()
        check = hold.check_ids
        check.sample_ids[:1].write({'visual_result': 'yes'})
        with self.assertRaises(ValidationError):
            check.write({'quality_state': 'pass'})
        with self.assertRaises(UserError):
            check.add_measurement_samples(1)
        with self.assertRaises(AccessError):
            check.write({'incoming_sample_size': 1})
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'Pending', 'pending')

    def test_failed_source_and_results_remain_immutable(self):
        _picking, hold = self._receipt()
        check = hold.check_ids
        check.sample_ids[:1].visual_result = 'no'
        check.do_fail()
        with self.assertRaises(UserError):
            check.write({'quality_state': 'pass'})
        with self.assertRaises(UserError):
            check.sample_ids.write({'visual_result': 'yes'})
        with self.assertRaises(UserError):
            check.unlink()

    def test_identical_standard_source_assignment_is_allowed_but_rebinding_is_not(self):
        _picking, hold = self._receipt()
        check = hold.check_ids
        check.write({'product_id': hold.product_id.id, 'picking_id': hold.receipt_id.id,
                     'move_line_id': hold.receipt_line_id.id, 'lot_id': hold.lot_id.id})
        check.sample_ids.visual_result = 'yes'
        check.do_pass()
        check.write({'lot_id': hold.lot_id.id})
        with self.assertRaises(UserError):
            check.write({'picking_id': False})

    def test_defect_without_sampling_plan_can_be_returned_but_not_accepted(self):
        self.point.incoming_sample_size = 0
        _picking, hold = self._receipt()
        hold._report_failure('Visible damage before a sampling plan exists')
        hold._report_failure('Visible damage before a sampling plan exists')
        self.assertFalse(hold.check_ids)
        self.assertEqual(len(hold.alert_ids), 1)
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'Missing criteria', 'no')
        hold._execute('return', 'unchecked', 10, 'Damaged receipt', 'manual')
        self.assertEqual(hold.remaining_qty, 0)

    def test_multiple_required_points_cannot_release_on_one_pass(self):
        self.point.copy({'title': 'A Second Required Sampling'})
        _picking, hold = self._receipt()
        first = hold.check_ids[:1]
        first.sample_ids.visual_result = 'yes'
        first.do_pass()
        self.assertFalse(hold._sampling_passed())
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'Second QC pending', 'one')
