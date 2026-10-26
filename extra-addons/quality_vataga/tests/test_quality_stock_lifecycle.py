"""Full Enterprise lifecycle, executable only in an isolated Enterprise DB.

Only the unconditional live-release gate and the wall clock are patched.
Stock, valuation, QC, admissions, permissions and executions are REAL ORM.
No production test-mode switch is provided by the application.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_quality_unit_stock_release import QualityUnitStockReleaseCase


@tagged('post_install', '-at_install')
class TestQualityStockLifecycle(QualityUnitStockReleaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env['product.category'].create({
            'name': 'Enterprise release FIFO', 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic',
        })
        cls.product.categ_id = category
        # Fresh components: real receipts create the initial FIFO layers.
        cls.components = cls.env['product.product'].create([
            {'name': 'Enterprise valued component %s' % index, 'type': 'product',
             'categ_id': category.id, 'standard_price': 10} for index in range(2)])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'bom_line_ids': [Command.create({'product_id': item.id, 'product_qty': 2}) for item in cls.components],
        })
        for component in cls.components:
            move = cls.env['stock.move'].create({
                'name': 'Enterprise integration actual receipt', 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 100, 'price_unit': 10,
                'location_id': cls.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': cls.warehouse.lot_stock_id.id,
            })
            move._action_confirm(merge=False)
            move.write({'quantity': 100, 'picked': True})
            move._action_done()

    def setUp(self):
        super().setUp()
        self.clock = datetime(2026, 1, 12, 9)
        self.clock_patch = patch.object(fields.Datetime, 'now', side_effect=lambda: self.clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def _new_route(self):
        check, source, last = self._route(admitted=False)
        self._fill(check)  # all five initially eligible; FAIL has its own test
        self._decision(check).action_apply()
        source.workcenter_id.costs_hour = last.workcenter_id.costs_hour = 60
        timer = self.env['mrp.workcenter.productivity'].create({
            'workorder_id': source.id, 'workcenter_id': source.workcenter_id.id,
            'loss_id': self.env.ref('mrp.block_reason7').id,
            'date_start': self.clock - timedelta(hours=1), 'date_end': self.clock,
        })
        return check, source, last, timer

    def _complete(self, check, last, sample, shared=None):
        self._send(check, last, sample).action_apply()
        if shared:
            self.env['quality.stock.timer.binding']._bind_shared_timer(
                shared, check.sample_ids.controlled_unit_id, 'Явна рівна частка спільної пайки всіх п’яти виробів')
        transfer = self._transfers(check).filtered(lambda row: row.controlled_unit_id == sample.controlled_unit_id)
        self._receive(last, transfer).action_apply()
        self._execution_wizard(last, transfer).action_apply()
        self.clock += timedelta(minutes=10)
        execution = last.quality_execution_ids.filtered(lambda row: row.controlled_unit_id == sample.controlled_unit_id)
        self._execution_wizard(last, execution, 'finish').action_apply()
        target = execution._create_control(last, self.point)
        self._fill(target)
        self._decision(target).action_apply()
        target.do_pass()
        self._release(check.production_id, sample.controlled_unit_id).action_apply()
        action = self._wizard(check.production_id, sample.controlled_unit_id).action_prepare()
        return target, self.env['quality.unit.stock.release.plan'].browse(action['res_id'])

    def _post_isolated(self, plan):
        model = self.env['quality.stock.posting']
        with patch.object(type(model), '_require_enterprise_validation', return_value=None):
            return model._post_plan(plan)

    def test_five_real_qunit_posts_with_production_continuation_and_immutable_first_receipt(self):
        check, source, last, shared = self._new_route()
        samples = check.sample_ids.sorted('sample_number')
        # Match staging's first selected second QUNIT, followed by its peers.
        order = samples[1:2] | samples[:1] | samples[2:]
        first = None
        for index, sample in enumerate(order):
            target, plan = self._complete(check, last, sample, shared=shared if index == 0 else None)
            if index == 4:
                check.do_pass()  # satisfy native final-WO QC before closing
            receipt = self._post_isolated(plan)
            self.assertEqual(check.production_id.qty_produced, index + 1)
            self.assertEqual(receipt.permission_id.current_state, 'used')
            self.assertEqual(sample.controlled_unit_id.quality_release_state, 'produced')
            if first is None:
                first = receipt
                links, value = first.finished_line_id.consume_line_ids.ids, first.finished_move_id.stock_valuation_layer_ids.value
                self.assertEqual(self._post_isolated(plan), first)
                with self.assertRaises(UserError):
                    self._release(check.production_id, first.permission_id, 'cancel', 'already used').action_apply()
            self.assertEqual(first.finished_line_id.consume_line_ids.ids, links)
            self.assertEqual(first.finished_move_id.stock_valuation_layer_ids.value, value)
            if index < 4:
                self.assertNotEqual(check.production_id.state, 'done')
                with self.assertRaises(UserError):
                    last.button_finish()
        self.assertEqual(check.production_id.state, 'done')
        self.assertEqual(set(check.production_id.workorder_ids.mapped('state')), {'done'})
        self.assertEqual(check.production_id.quality_release_produced, 5)

    def test_revoked_permission_after_plan_preparation_rolls_back_all_stock(self):
        check, source, last, shared = self._new_route()
        target, plan = self._complete(check, last, check.sample_ids[:1], shared=shared)
        self._release(check.production_id, plan.permission_ids, 'cancel', 'revoke before stock').action_apply()
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._post_isolated(plan)
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_fail_on_next_unit_never_becomes_another_stock_receipt(self):
        check, source, last, shared = self._new_route()
        first, second = check.sample_ids[:1], check.sample_ids[1:2]
        _target, plan = self._complete(check, last, first, shared=shared)
        self._post_isolated(plan)
        check.update_measurement_visual_result(second.id, 'no')
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._send(check, last, second).action_apply()
        with self.assertRaises(ValidationError):
            self._release(check.production_id, second.controlled_unit_id).action_apply()
        self.assertEqual(before, self._stock_snapshot(check.production_id))

    def test_posted_unit_cannot_be_reprocessed_rechecked_or_edited_as_wip(self):
        check, source, last, shared = self._new_route()
        sample = check.sample_ids[:1]
        target, plan = self._complete(check, last, sample, shared=shared)
        self._post_isolated(plan)
        execution = target.quality_execution_ids
        for operation in (lambda: execution._create_control(last, self.point),
                          lambda: self._execution_wizard(last, execution.transfer_id).action_apply(),
                          lambda: sample.write({'visual_result': 'no'}),
                          lambda: self._send(check, last, sample).action_apply()):
            with self.assertRaises((UserError, ValidationError)):
                operation()

    def test_unused_permission_can_be_cancelled_after_another_unit_was_posted(self):
        check, source, last, shared = self._new_route()
        _target, first_plan = self._complete(check, last, check.sample_ids[:1], shared=shared)
        first = self._post_isolated(first_plan)
        _target, second_plan = self._complete(check, last, check.sample_ids[1:2])
        self._release(check.production_id, second_plan.permission_ids, 'cancel', 'unused permission').action_apply()
        self.assertEqual(second_plan.permission_ids.current_state, 'cancelled')
        self.assertEqual(first.permission_id.current_state, 'used')
        self.assertEqual(check.production_id.qty_produced, 1)

    def test_employee_time_is_explicitly_blocked_instead_of_undercosted(self):
        if 'employee_id' not in self.env['mrp.workcenter.productivity']._fields:
            self.skipTest('This Enterprise stack has no employee productivity field.')
        check, source, last, shared = self._new_route()
        employee = self.env['hr.employee'].create({'name': 'Isolated labour-cost contract employee', 'company_id': self.env.company.id})
        shared.employee_id = employee
        _target, plan = self._complete(check, last, check.sample_ids[:1], shared=shared)
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self._post_isolated(plan)
        self.assertEqual(before, self._stock_snapshot(check.production_id))
