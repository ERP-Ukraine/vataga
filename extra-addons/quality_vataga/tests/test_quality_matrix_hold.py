"""Full Enterprise ORM scenarios. Not run by the Community native-MRP probe."""

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_transfer as transfers


@tagged('post_install', '-at_install')
class TestQualityMatrixHold(QualityCheckInProcessCase):
    _workorder = transfers.TestQualityUnitTransfer._workorder
    _identify = transfers.TestQualityUnitTransfer._identify
    _ready = transfers.TestQualityUnitTransfer._ready

    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity=quantity, uom=uom)
        production.product_qty = quantity
        return production

    def _user(self, login, groups):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': login + '@example.test',
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in ['base.group_user'] + groups])],
        })

    def setUp(self):
        super().setUp()
        self.operator = self._user('matrix-routing-operator', ['quality.group_quality_user', 'mrp.group_mrp_user'])
        self.assertFalse(self.operator.has_group('quality_vataga.group_quality_board_approval'))

    def _scenario(self, result='fail'):
        check = self._ready(fail_last=result == 'fail')
        if result == 'pending':
            value = check.sample_ids[-1].measurement_value_ids.filtered(lambda row: row.parameter_type == 'numeric')[:1]
            check.update_measurement_value(value.id, {'numeric_input': ''})
        source = check.operation_run_id.workorder_id
        target = self._workorder(check.production_id, 'Наступна штатна операція')
        check.production_id.write({'state': 'progress', 'allow_workorder_dependencies': True})
        target.blocked_by_workorder_ids = [Command.set(source.ids)]
        source.state = 'progress'
        return check, source, target

    def _admit(self, check, samples):
        wizard = self.env['quality.unit.admission.wizard'].with_user(self.operator).create({
            'check_id': check.id, 'mode': 'confirm',
            'line_ids': [Command.create({'sample_id': sample.id, 'selected': True}) for sample in samples],
        })
        wizard.action_apply()

    def _send(self, check, target, samples):
        return self.env['quality.unit.transfer.wizard'].with_user(self.operator).create({
            'check_id': check.id, 'workorder_id': check.operation_run_id.workorder_id.id,
            'mode': 'send', 'destination_workorder_id': target.id,
            'line_ids': [Command.create({'sample_id': sample.id, 'selected': True}) for sample in samples],
        })

    def _alert(self, check):
        return self.env['quality.alert'].with_user(self.operator).create({
            'name': 'Адресна невідповідність', 'check_id': check.id, 'team_id': self.team.id})

    def _business_snapshot(self, production):
        moves = production.move_raw_ids | production.move_finished_ids
        return (production.state, production.product_qty, production.qty_produced,
                self.env['mrp.production'].search([]).ids, moves.ids, moves.mapped('state'),
                moves.mapped('quantity'), moves.move_line_ids.ids)

    def test_five_pass_move_without_board_or_release_permission(self):
        check, source, target = self._scenario('pass')
        self._admit(check, check.sample_ids)
        before = self._business_snapshot(check.production_id)
        self._send(check, target, check.sample_ids).action_apply()
        records = self.env['quality.unit.transfer'].search([('check_id', '=', check.id)])
        self.assertEqual(len(records), 5)
        self.assertEqual(records.controlled_unit_id, check.sample_ids.controlled_unit_id)
        self.assertFalse(self.env['quality.unit.release.line'].search([('production_id', '=', check.production_id.id)]))
        self.assertEqual(self._business_snapshot(check.production_id), before)

    def test_four_pass_move_and_one_fail_is_held_by_its_open_alert(self):
        check, source, target = self._scenario()
        failed = check.sample_ids.filtered(lambda sample: sample.sample_result == 'fail')
        good = check.sample_ids - failed
        alert = self._alert(check)
        self.assertEqual(alert.vataga_failure_sample_ids, failed)
        self.assertTrue(alert._matrix_hold_is_open())
        self._admit(check, good)
        before = self._business_snapshot(check.production_id)
        self._send(check, target, good).action_apply()
        records = self.env['quality.unit.transfer'].search([('check_id', '=', check.id)])
        self.assertEqual(len(records), 4)
        self.assertNotIn(failed.controlled_unit_id, records.controlled_unit_id)
        self.env['quality.unit.transfer.wizard'].with_user(self.operator).create({
            'workorder_id': target.id, 'mode': 'receive',
            'line_ids': [Command.create({'transfer_id': record.id, 'selected': True}) for record in records],
        }).action_apply()
        self.env['quality.unit.execution.wizard'].with_user(self.operator).create({
            'workorder_id': target.id, 'mode': 'start',
            'line_ids': [Command.create({'transfer_id': record.id, 'selected': True}) for record in records],
        }).action_apply()
        self.assertEqual(target.quality_execution_ids.controlled_unit_id, good.controlled_unit_id)
        self.assertEqual((source.state, target.state), ('progress', 'progress'))
        self.assertEqual(self._business_snapshot(check.production_id), before)
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual(check.production_id.qty_produced, 0)
        with self.assertRaises(UserError):
            check.production_id._split_productions()
        with self.assertRaises(UserError):
            check.do_pass()

    def test_four_pass_move_and_pending_cannot_be_selected(self):
        check, source, target = self._scenario('pending')
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        pending = check.sample_ids - good
        self.assertEqual(len(good), 4)
        self.assertEqual(pending.sample_result, 'pending')
        self._admit(check, good)
        self._send(check, target, good).action_apply()
        with self.assertRaises(ValidationError):
            self._send(check, target, pending).action_apply()
        self.assertEqual(len(self.env['quality.unit.transfer'].search([('check_id', '=', check.id)])), 4)

    def test_open_alert_blocks_native_completion_and_backorder_before_activation(self):
        check, source, target = self._scenario()
        self._alert(check)
        production = check.production_id
        self.assertFalse(production.quality_transfer_controlled)
        self.assertTrue(production.move_finished_ids)
        scrap = self.env['stock.scrap'].create({
            'product_id': self.product.id, 'product_uom_id': self.product.uom_id.id,
            'production_id': production.id, 'scrap_qty': 1,
            'location_id': production.location_src_id.id,
        })
        before = self._business_snapshot(production)
        for method in (production.button_mark_done, production._post_inventory, production._split_productions,
                       source.button_finish, lambda: production.write({'state': 'done'}),
                       lambda: production.with_context(skip_backorder=True, mo_ids_to_backorder=production.ids).button_mark_done(),
                       lambda: production.move_finished_ids._action_done(), scrap.do_scrap):
            with self.assertRaises(UserError):
                method()
        self.assertFalse(production.quality_transfer_controlled)
        self.assertEqual(self._business_snapshot(production), before)

    def test_direct_fail_selection_is_atomic_and_does_not_activate_route(self):
        check, source, target = self._scenario()
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        self._admit(check, good)
        before = self._business_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._send(check, target, good[:1] | (check.sample_ids - good)).action_apply()
        self.assertFalse(self.env['quality.unit.transfer'].search([('check_id', '=', check.id)]))
        self.assertFalse(check.production_id.quality_transfer_controlled)
        self.assertEqual(self._business_snapshot(check.production_id), before)

    def test_retry_has_no_duplicate_transfers_production_or_stock_quantity(self):
        check, source, target = self._scenario()
        self._alert(check)
        good = check.sample_ids.filtered(lambda sample: sample.sample_result == 'pass')
        self._admit(check, good)
        wizard = self._send(check, target, good)
        wizard.action_apply()
        before = self._business_snapshot(check.production_id)
        ids = self.env['quality.unit.transfer'].search([('check_id', '=', check.id)]).ids
        wizard.action_apply()
        self.assertEqual(self.env['quality.unit.transfer'].search([('check_id', '=', check.id)]).ids, ids)
        self.assertEqual(self._business_snapshot(check.production_id), before)

    def test_corrected_pass_remains_held_until_alert_resolution(self):
        check, source, target = self._scenario()
        alert = self._alert(check)
        failed = alert.vataga_failure_sample_ids
        check.update_measurement_visual_result(failed.id, 'yes')
        self.assertEqual(failed.sample_result, 'pass')
        self.assertTrue(failed.x_was_reworked)
        self.assertTrue(check._get_unit_admission_error(failed))
        with self.assertRaises(ValidationError):
            self._admit(check, failed)
        self._admit(check, check.sample_ids - failed)

    def test_old_unscoped_alert_needs_explicit_manager_mapping_not_current_results(self):
        check, source, target = self._scenario('pass')
        alert = self._alert(check)  # no matrix failure identity was known
        self.assertFalse(alert.vataga_failure_sample_ids)
        failed = check.sample_ids[-1]
        check.update_measurement_visual_result(failed.id, 'no')
        with self.assertRaises(ValidationError):
            self._admit(check, check.sample_ids[:1])
        with self.assertRaises(AccessError):
            alert.with_user(self.operator)._bind_matrix_failure_scope(failed, 'Не підтверджено')
        manager = self._user('matrix-scope-manager', ['quality.group_quality_manager', 'mrp.group_mrp_user'])
        self.env['quality.matrix.failure.scope.wizard'].with_user(manager).create({
            'alert_id': alert.id, 'sample_ids': [Command.set(failed.ids)], 'reason': 'Сфера підтверджена первинним актом',
        }).action_apply()
        self.assertEqual(alert.vataga_failure_sample_ids, failed)
        self._admit(check, check.sample_ids - failed)

    def test_failure_scope_cannot_be_forged_cleared_or_retargeted(self):
        check, source, target = self._scenario()
        alert = self._alert(check)
        with self.assertRaises(UserError):
            alert.write({'vataga_failure_sample_ids': [Command.clear()]})
        with self.assertRaises(UserError):
            alert.unlink()
        with self.assertRaises(UserError):
            check.unlink()
        with self.assertRaises(UserError):
            self.env['quality.alert'].create({'name': 'Підміна', 'check_id': check.id,
                'vataga_failure_sample_ids': [Command.set(check.sample_ids[:1].ids)]})
        other = self._check(self._production())
        with self.assertRaises(UserError):
            alert.write({'check_id': other.id})

    def test_more_than_passed_quantity_is_blocked_even_after_completed_qc(self):
        check, source, target = self._scenario('pass')
        check.do_pass()
        check.production_id.qty_producing = 6
        before = self._business_snapshot(check.production_id)
        with self.assertRaises(UserError):
            check.production_id.button_mark_done()
        self.assertEqual(self._business_snapshot(check.production_id), before)

    def test_other_production_alert_does_not_hold_pass_units(self):
        bad, source, target = self._scenario()
        self._alert(bad)
        good, other_source, other_target = self._scenario('pass')
        self._admit(good, good.sample_ids)
        self._send(good, other_target, good.sample_ids).action_apply()
        self.assertEqual(len(self.env['quality.unit.transfer'].search([('check_id', '=', good.id)])), 5)

    def test_native_completion_cannot_bypass_required_equipment(self):
        check, source, target = self._scenario('pass')
        check.equipment_selection_ids.write({'equipment_ids': [Command.clear()]})
        self.assertEqual(check.x_passed_qty, 5)
        with self.assertRaises(ValidationError):
            check.production_id._check_matrix_native_completion()
