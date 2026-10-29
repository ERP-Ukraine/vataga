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


@tagged('post_install', '-at_install')
class TestQualityWorkorderMatrixScope(QualityCheckInProcessCase):
    """Real three-operation MRP/Quality fixtures, requiring Enterprise."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # These tests assign each QC explicitly to its operation. Do not let
        # the fixture's broad automatic QCP introduce unrelated duplicate QCs.
        cls.point.picking_type_ids = [Command.clear()]
        cls.product.type = 'product'
        center = cls.env['mrp.workcenter'].create({'name': 'QC operation scope'})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'operation_ids': [Command.create({'name': name, 'workcenter_id': center.id})
                              for name in ('Cutting', 'Crimping', 'Cable inspection')],
        })
        cls.unconfigured_point = cls.env['quality.point'].create({
            'title': 'Future operation without matrix settings',
            'x_control_stage': 'in_process',
            'team_id': cls.team.id, 'test_type_id': cls.test_type.id,
        })

    def setUp(self):
        super().setUp()
        if 'workorder_id' not in self.env['quality.check']._fields:
            self.skipTest('Requires Enterprise Quality / Work Order integration.')

    def _three_operations(self, check_index=2, configured=False):
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 5,
            'product_uom_id': self.product.uom_id.id, 'bom_id': self.bom.id,
            'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id,
        })
        production.action_confirm()
        production.write({'qty_producing': 5, 'allow_workorder_dependencies': True})
        first, second, third = production.workorder_ids.sorted('id')
        second.blocked_by_workorder_ids = [Command.set(first.ids)]
        third.blocked_by_workorder_ids = [Command.set(second.ids)]
        workorders = (first, second, third)
        check = self._check(production, point=self.point if configured else self.unconfigured_point,
                            workorder_id=workorders[check_index].id)
        return production, workorders, check

    def _complete(self, workorder, method):
        if method == 'write':
            return workorder.write({'state': 'done'})
        return getattr(workorder, method)()

    def test_standard_pass_is_not_implicitly_a_required_unit_route(self):
        production, (first, second, third), check = self._three_operations(check_index=0, configured=True)
        self._fill(check)
        check.do_pass()
        self.assertEqual(check.unit_admission_role, 'required')
        self.assertFalse(check.operation_run_id)
        self.assertFalse(production.quality_unit_route_required)
        production._check_matrix_native_completion()
        for workorder in (first, second, third):
            workorder.button_start()
            workorder.button_finish()
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        self.assertEqual(production.qty_produced, 5)

    def test_qcp_opt_in_is_prospective_and_mo_policy_is_sticky(self):
        production, workorders, old = self._three_operations(check_index=0, configured=True)
        self.assertFalse(production.quality_unit_route_required)
        self.point.quality_unit_route_required = True
        self.assertFalse(production.quality_unit_route_required)
        new = self._check(production, workorder_id=workorders[0].id)
        self.assertTrue(production.quality_unit_route_required)
        self.assertFalse(production.quality_transfer_controlled)
        self.point.quality_unit_route_required = False
        new.unlink()
        self.assertTrue(production.quality_unit_route_required)
        with self.assertRaises(UserError):
            production.write({'quality_unit_route_required': False})
        self.assertFalse(old.operation_run_id)

    def test_required_pass_without_qrun_blocks_every_final_boundary(self):
        production, (first, second, third), check = self._three_operations(check_index=0, configured=True)
        self._fill(check)
        check.do_pass()
        move = production.move_finished_ids[:1]
        line = self.env['stock.move.line'].create({'move_id': move.id, 'quantity': 0})
        production.action_require_quality_unit_route()
        self.assertFalse(check.operation_run_id)
        self.assertFalse(check.sample_ids.controlled_unit_id)
        self.assertFalse(check.unit_admission_ids)
        self.assertFalse(production.quality_transfer_controlled)
        line.write({'quantity': 1})
        self.assertNotEqual(move.state, 'done')
        before = (production.state, production.product_qty, production.qty_produced,
                  production.move_finished_ids.read(['state', 'quantity']))
        move = production.move_finished_ids[:1]
        for call in (
                production.button_mark_done, production.pre_button_mark_done,
                production._post_inventory, production._split_productions,
                lambda: production.write({'state': 'done'}),
                lambda: production.write({'qty_produced': 5}),
                move._action_done, lambda: move.write({'state': 'done'}),
                lambda: move.copy({'state': 'done'}),
                lambda: line.write({'move_id': False}),
                first.unlink, lambda: first.write({'production_id': False}),
                lambda: move.write({'production_id': False}),
                lambda: production.with_context(bypass=True, skip_backorder=True,
                    quality_native_release=True, quality_unit_route_required=False).button_mark_done(),
                production.action_open_quality_native_release):
            with self.subTest(call=call), self.assertRaises(UserError), self.env.cr.savepoint():
                call()
        self.assertEqual(before, (production.state, production.product_qty, production.qty_produced,
                                 production.move_finished_ids.read(['state', 'quantity'])))

    def test_required_qrun_without_admission_cannot_release(self):
        production, (first, second, third), check = self._three_operations(check_index=0, configured=True)
        production.action_require_quality_unit_route()
        transfers.TestQualityUnitTransfer._identify(self, check, first)
        self._fill(check)
        check.do_pass()
        self.assertTrue(check.operation_run_id)
        self.assertTrue(check.sample_ids.controlled_unit_id)
        self.assertFalse(check.unit_admission_ids)
        with self.assertRaises(UserError):
            production._post_inventory()

    def test_required_future_qc_does_not_block_previous_workorder(self):
        production, (first, second, third), check = self._three_operations()
        production.action_require_quality_unit_route()
        first.button_start()
        first.button_finish()
        self.assertEqual(first.state, 'done')
        self.assertEqual(check.quality_state, 'none')
        self.assertFalse(check.operation_run_id)
        with self.assertRaises(UserError):
            production.button_mark_done()

    def test_finished_history_is_not_activated_by_qcp_changes(self):
        production, workorders, check = self._three_operations(check_index=0, configured=True)
        self._fill(check)
        check.do_pass()
        # Historical fixture: direct ORM completion is permitted in standard
        # mode, just as it was before policy introduction. No stock is invented.
        production.write({'state': 'done'})
        before = (production.read(), check.read())
        self.point.quality_unit_route_required = True
        with self.assertRaises(UserError):
            production.action_require_quality_unit_route()
        self.assertEqual(before, (production.read(), check.read()))
        self.assertFalse(production.quality_unit_route_required)

    def test_only_quality_manager_can_opt_in_and_context_cannot_enable_mo(self):
        production, workorders, check = self._three_operations()
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Route operator', 'login': 'required-route-operator',
            'groups_id': [Command.set([self.env.ref('base.group_user').id,
                                      self.env.ref('mrp.group_mrp_user').id])],
        })
        with self.assertRaises(AccessError):
            production.with_user(user).action_require_quality_unit_route()
        with self.assertRaises(AccessError):
            self.point.with_user(user).write({'quality_unit_route_required': True})
        self.assertFalse(production.quality_unit_route_required)
        duplicate = production.with_context(default_quality_unit_route_required=True).copy()
        self.assertFalse(duplicate.quality_unit_route_required)

    def test_future_empty_qc_does_not_block_first_operation_buttons_or_direct_write(self):
        for method in ('button_finish', 'button_done', 'action_mark_as_done', 'write'):
            with self.subTest(method=method):
                production, (first, second, third), check = self._three_operations()
                self.assertFalse(production.quality_transfer_controlled)
                self.assertEqual(check.quality_state, 'none')
                self.assertFalse(check.measurement_matrix_required)
                self.assertFalse(check.sample_ids)
                before = check.read(['quality_state', 'measurement_matrix_required', 'sample_ids', 'workorder_id'])
                first.button_start()
                self._complete(first, method)
                self.assertEqual(first.state, 'done')
                self.assertNotEqual(third.state, 'done')
                self.assertNotEqual(production.state, 'done')
                self.assertEqual(check.read(['quality_state', 'measurement_matrix_required', 'sample_ids', 'workorder_id']), before)

    def test_third_operation_with_its_own_empty_qc_is_blocked_on_every_entry(self):
        production, (first, second, third), check = self._three_operations()
        first.button_start()
        first.button_finish()
        second.button_start()
        second.button_finish()
        third.button_start()
        for method in ('button_finish', 'button_done', 'action_mark_as_done', 'write'):
            with self.subTest(method=method), self.assertRaises(UserError), self.env.cr.savepoint():
                self._complete(third, method)
        self.assertNotEqual(third.state, 'done')
        self.assertEqual(check.quality_state, 'none')

    def test_future_qc_still_blocks_full_mo_stock_and_backorder(self):
        production, (first, second, third), check = self._three_operations()
        first.button_start()
        first.button_finish()
        before = (production.state, production.product_qty, production.qty_produced,
                  production.move_finished_ids.read(['state', 'quantity']))
        for action in (production.button_mark_done, production.pre_button_mark_done,
                       production._post_inventory, production._split_productions,
                       lambda: production.write({'state': 'done'}),
                       production.move_finished_ids._action_done,
                       lambda: production.move_finished_ids.write({'state': 'done'})):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                action()
        self.assertEqual((production.state, production.product_qty, production.qty_produced,
                          production.move_finished_ids.read(['state', 'quantity'])), before)

    def test_first_operation_cannot_bypass_its_own_required_qc(self):
        production, (first, second, third), check = self._three_operations(check_index=0)
        first.button_start()
        for method in ('button_finish', 'button_done', 'action_mark_as_done', 'write'):
            with self.subTest(method=method), self.assertRaises(UserError), self.env.cr.savepoint():
                self._complete(first, method)
        self.assertNotEqual(first.state, 'done')

    def test_pass_on_first_operation_allows_it_without_releasing_future_qc(self):
        production, (first, second, third), future = self._three_operations()
        own = self._check(production, point=self.point, workorder_id=first.id)
        self._fill(own)
        own.do_pass()
        first.button_start()
        first.button_finish()
        self.assertEqual(first.state, 'done')
        self.assertEqual(own.quality_state, 'pass')
        self.assertFalse(future.sample_ids)
        with self.assertRaises(UserError):
            production._check_matrix_native_completion()

    def test_qrun_bound_qc_cannot_be_hidden_by_an_empty_direct_workorder_field(self):
        production, (first, second, third), check = self._three_operations(configured=True)
        # A QRUN can carry the operation when the direct QC field is empty.
        check = self._check(production, point=self.point)
        check._identify_samples('new', [{'sample_id': sample.id} for sample in check.sample_ids],
                                workorder=first, confirmed=True)
        self.assertFalse(check.workorder_id)
        self.assertEqual(check.operation_run_id.workorder_id, first)
        first.button_start()
        with self.assertRaises(UserError):
            first.write({'state': 'done'})

    def test_mixed_workorder_write_cannot_hide_the_operation_with_pending_qc(self):
        production, (first, second, third), check = self._three_operations()
        with self.assertRaises(UserError):
            (first | third).write({'state': 'done'})
        self.assertNotEqual(first.state, 'done')
        self.assertNotEqual(third.state, 'done')

    def test_controlled_mo_remains_blocked_even_when_qc_belongs_only_to_future_workorder(self):
        production, (first, second, third), check = self._three_operations()
        first.button_start()
        production._enable_quality_transfer_control()
        for method in ('button_finish', 'button_done', 'action_mark_as_done', 'write'):
            with self.subTest(method=method), self.assertRaises(UserError), self.env.cr.savepoint():
                self._complete(first, method)
        self.assertTrue(production.quality_transfer_controlled)
        self.assertNotEqual(first.state, 'done')
