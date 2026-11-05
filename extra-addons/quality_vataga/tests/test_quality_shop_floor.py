"""Shop Floor ORM dispatch with real mrp_workorder, QC, timers and stock."""
from unittest.mock import patch

from odoo import Command, api
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models import quality_native_workorder
from .test_quality_full_release import QualityFullReleaseCase


@tagged('post_install', '-at_install', 'quality_shop_floor')
class TestQualityShopFloor(QualityFullReleaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product.tracking = 'lot'
        cls.test_type = cls.env['quality.point.test_type'].search(
            [('technical_name', '=', 'passfail')], limit=1)
        if not cls.test_type:
            raise AssertionError('Enterprise pass/fail quality check type is required')
        cls.point.test_type_id = cls.test_type
        cls.employee = cls.env.user.employee_id or cls.env['hr.employee'].create({
            'name': 'Shop Floor operator', 'user_id': cls.env.uid, 'company_id': cls.env.company.id})
        cls.warehouse.manu_type_id.create_backorder = 'ask'

    def _order(self, quantity=2, register=True, protected=True, check_first=False):
        self.point.picking_type_ids = [Command.clear()]
        self.point.quality_unit_route_required = protected
        if check_first:
            operation = self.bom.operation_ids.sorted('id')[:1]
            self.point[self.operation_field] = (operation.id if self.point._fields[self.operation_field].type == 'many2one'
                                                else [Command.set(operation.ids)])
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 3, 'product_uom_id': self.product.uom_id.id,
            'bom_id': self.bom.id, 'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id})
        production.action_confirm()
        production.action_assign()
        lot = self.env['stock.lot'].create({'name': production.name, 'product_id': self.product.id,
                                            'company_id': self.env.company.id})
        self.point.picking_type_ids = [Command.set(self.warehouse.manu_type_id.ids)]
        workorders = production.workorder_ids.sorted('id')
        target = workorders[0] if check_first else workorders[-1]
        check = self._check(production, workorder_id=target.id) if protected else self.env['quality.check']
        if quantity is None:
            return production, check, workorders
        if register:
            self._rpc(production, 'action_register_quality_shop_floor_quantity', quantity, lot.id)
            self._rpc(production, 'action_register_quality_shop_floor_quantity', quantity, lot.id)
        else:
            production.write({'qty_producing': quantity, 'lot_producing_id': lot.id})
            production._set_qty_producing()
        return production, check, workorders

    @staticmethod
    def _rpc(records, method, *args, **context):
        return api.call_kw(records.browse(), method, [records.ids, *args], {'context': context})

    def _start(self, workorder):
        with patch.object(type(self.employee), 'get_session_owner', return_value=self.employee.id):
            self._rpc(workorder, 'button_start', mrp_display=True)

    def _assert_unposted(self, production):
        self.assertEqual((production.product_qty, production.qty_produced), (3, 0))
        self.assertNotEqual(production.state, 'done')
        self.assertFalse((production.move_raw_ids | production.move_finished_ids).filtered(lambda move: move.state == 'done'))
        self.assertFalse(production.native_stock_fact_ids)
        self.assertEqual(production.procurement_group_id.mrp_production_ids, production)

    def _finish_three(self, quantity=2, register=True):
        production, check, workorders = self._order(quantity, register)
        for workorder in workorders:
            self._start(workorder)
            self.assertEqual((workorder.state, workorder.qty_producing), ('progress', quantity))
            self.assertEqual(len(workorder.time_ids.filtered(lambda timer: not timer.date_end)), 1)
            self._rpc(workorder, 'do_finish')
            self.assertEqual((workorder.state, workorder.qty_produced), ('done', quantity))
            self.assertFalse(workorder.time_ids.filtered(lambda timer: not timer.date_end))
            self._assert_unposted(production)
        self.assertEqual((len(production._full_release_units()), len(check.sample_ids)), (quantity, quantity))
        self.assertEqual(check.quality_state, 'none')
        self.assertFalse(check.unit_admission_ids)
        evidence = (workorders.quality_execution_ids.ids, workorders.time_ids.ids, production._full_release_units().ids)
        for workorder in workorders:
            self._rpc(workorder, 'do_finish')
            self._rpc(workorder, 'record_production')
        self.assertEqual((workorders.quality_execution_ids.ids, workorders.time_ids.ids,
                          production._full_release_units().ids), evidence)
        return production, check

    def test_sf_partial_and_full_release_after_shop_floor(self):
        for quantity, register in ((2, False), (2, True), (3, True)):
            with self.subTest(quantity=quantity, register=register):
                production, check = self._finish_three(quantity, register)
                release = production.action_release_quality_actual_batch if quantity == 2 else production.button_mark_done
                for close in (production.pre_button_mark_done, production.button_mark_done, release):
                    self._assert_denied_unchanged(production, close)
                self._fill(check)
                check.do_pass()
                self.assertEqual(check.x_admitted_qty, quantity)
                release()
                self.assertEqual((production.state, production.qty_produced), ('done', quantity))
                self.assertEqual(len(production.native_stock_fact_ids), quantity)
                self.assertEqual(production.native_stock_fact_ids.move_line_id.lot_id, production.lot_producing_id)
                raw = production.move_raw_ids.filtered(lambda move: move.state == 'done')
                self.assertEqual(sum(raw.mapped('quantity')), quantity * 2)
                child = production.procurement_group_id.mrp_production_ids - production
                self.assertEqual(child.mapped('product_qty'), [1] if quantity == 2 else [])
                self.assertFalse(child.native_stock_fact_ids or child._full_release_units())

    def test_sf_fail_and_alert_keep_inventory_blocked(self):
        for fail in (True, False):
            production, check = self._finish_three()
            self._fill(check)
            if fail:
                check.update_measurement_visual_result(check.sample_ids[-1].id, 'no')
                check.do_fail()
            else:
                check.do_pass()
                alert = self.env['quality.alert'].create({'name': 'Shop Floor hold', 'check_id': check.id,
                                                          'team_id': self.team.id})
                alert._bind_matrix_failure_scope(check.sample_ids[:1], 'Unresolved lot defect')
            self._assert_denied_unchanged(production, production.action_release_quality_actual_batch)

    def test_sf_pending_predecessor_and_other_operator_cannot_start_or_finish(self):
        production, check, (first, second, _) = self._order(check_first=True)
        self._start(first)
        other = self.env['hr.employee'].create({'name': 'Other operator', 'company_id': self.env.company.id})
        with patch.object(quality_native_workorder, 'request', True), \
                patch.object(type(other), 'get_session_owner', return_value=other.id):
            for attempt in (lambda: self._rpc(first, 'button_start', mrp_display=True),
                            lambda: self._rpc(first, 'start_employee', other.id),
                            lambda: self._rpc(first, 'do_finish', employee_id=self.employee.id)):
                with self.assertRaises(UserError), self.env.cr.savepoint():
                    attempt()
        self.assertEqual(len(first.time_ids), 1)
        self._rpc(first, 'do_finish')
        self.assertEqual(check.quality_state, 'none')
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._start(second)
        self.assertFalse(second.time_ids)
        self._assert_unposted(production)
        self._fill(check)
        check.do_pass()
        self._start(second)
        self.assertEqual(second.state, 'progress')

    def test_sf_quantity_registration_is_explicit_and_atomic(self):
        production, _, workorders = self._order(quantity=None)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._start(workorders[0])
        self.assertEqual(production.qty_producing, 0)
        self.assertFalse(workorders.time_ids)
        lot = self.env['stock.lot'].search([('name', '=', production.name), ('product_id', '=', self.product.id)])
        self._assert_denied_unchanged(production, lambda: self._rpc(
            production, 'action_register_quality_shop_floor_quantity', 4, lot.id))
        original = type(production)._set_qty_producing

        def fail(record):
            original(record)
            raise UserError('Injected quantity registration failure')

        with patch.object(type(production), '_set_qty_producing', fail):
            self._assert_denied_unchanged(production, lambda: self._rpc(
                production, 'action_register_quality_shop_floor_quantity', 2, lot.id))
        self.assertFalse(production.qty_producing or production.lot_producing_id)

    def test_sf_failed_native_finish_preserves_timer_and_history(self):
        production, check, workorders = self._order()
        for workorder in workorders[:-1]:
            self._start(workorder)
            self._rpc(workorder, 'do_finish')
        last = workorders[-1]
        self._start(last)
        original = type(last).end_all

        def fail(record):
            original(record)
            raise UserError('Injected failure after end_all')

        before = self._snapshot(production)
        with patch.object(type(last), 'end_all', fail), self.assertRaisesRegex(UserError, 'Injected failure'):
            self._rpc(last, 'do_finish')
        self.assertEqual(self._snapshot(production), before)
        self.assertEqual(len(last.time_ids.filtered(lambda timer: not timer.date_end)), 1)
        self.assertEqual(check.quality_state, 'none')
        self._rpc(last, 'do_finish')
        self.assertEqual((last.state, last.qty_produced), ('done', 2))

    def test_sf_unmanaged_uses_enterprise_precheck_and_finish(self):
        production, _, workorders = self._order(quantity=3, register=False, protected=False)
        first = workorders[0]
        self._start(first)
        self._rpc(first, 'pre_record_production')
        self._rpc(first, 'do_finish', mrp_display=True, no_start_next=True, employee_id=self.employee.id)
        self.assertEqual((first.state, first.qty_produced), ('done', 3))
        self._assert_unposted(production)
