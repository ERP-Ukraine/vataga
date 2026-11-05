"""Real lot 2/3 stock/QC scenarios for a disposable Enterprise DB; not portable."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from psycopg2 import errors

from odoo import api, Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_quality_full_release import QualityFullReleaseCase
from . import test_quality_actual_batch_release as actual


@tagged('post_install', '-at_install', 'quality_actual_batch_lot')
class TestQualityActualBatchLot(QualityFullReleaseCase):
    _children = actual.TestQualityActualBatchRelease._children
    _denied = actual.TestQualityActualBatchRelease._denied

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.test_type = cls.env['quality.point.test_type'].search([('technical_name', '=', 'passfail')], limit=1)
        if not cls.test_type:
            raise AssertionError('Enterprise pass/fail quality check type is required')
        cls.point.test_type_id = cls.test_type
        cls.bom.operation_ids.sorted('id')[1].unlink()
        cls.warehouse.manu_type_id.create_backorder = 'ask'
        cls.product.tracking = 'lot'  # Disposable fixture product only.

    def _lot(self):
        return self.env['stock.lot'].create({
            'name': 'ACTUAL-LOT-%s' % uuid4().hex,
            'product_id': self.product.id, 'company_id': self.env.company.id})

    def _manufactured(self, actual=2, registered_lot=None, result='pass',
                      finish_method='button_finish', protected=True):
        self.point.picking_type_ids = [Command.clear()]
        self.point.quality_unit_route_required = protected
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 3, 'product_uom_id': self.product.uom_id.id,
            'bom_id': self.bom.id, 'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id})
        production.action_confirm()
        production.action_assign()
        production.qty_producing = actual
        production._set_qty_producing()
        first, last = production.workorder_ids.sorted('id')
        self.point.picking_type_ids = [Command.set(self.warehouse.manu_type_id.ids)]
        check = self._check(production, workorder_id=last.id)
        first.button_start()
        first.button_finish()
        last.button_start()
        if registered_lot:
            check._identify_samples('new', [
                {'sample_id': sample.id, 'lot_id': registered_lot.id} for sample in check.sample_ids],
                workorder=last, confirmed=True)
        if finish_method:
            getattr(last, finish_method)()
            self.assertEqual((len(production._full_release_units()), last.qty_produced), (actual, actual))
            self.assertEqual(last.state, 'done')
            self.assertEqual(check.quality_state, 'none')
            self.assertFalse(check.unit_admission_ids)
            self.assertFalse(production.workorder_ids.time_ids.filtered(lambda timer: not timer.date_end))
        self.assertEqual(production.quality_unit_route_required, protected)
        # Reproduce a lot assigned after immutable QUNIT registration.
        production.lot_producing_id = registered_lot or self._lot()
        if result is None:
            return production, check
        self._fill(check)
        if result != 'pass':
            check.update_measurement_visual_result(check.sample_ids[-1].id, 'no' if result == 'fail' else False)
        if result == 'pass':
            check.do_pass()
        elif result == 'fail':
            check.do_fail()
        return production, check

    def _snapshot(self, production):
        return (super()._snapshot(production), production.lot_producing_id.id,
                production.move_finished_ids.move_line_ids.read(['lot_id', 'quantity']),
                self.env['stock.lot'].search([]).ids)

    def test_t1_t9_t10_t11_t12_real_two_of_three_and_clean_remainder(self):
        production, check = self._manufactured()
        units, lot = production._full_release_units(), production.lot_producing_id
        identity_before = units.read(['name', 'lot_id', 'registration_run_id'])
        qc_before = check.sample_ids.read()
        unit_ids = self.env['quality.production.unit'].search([]).ids
        production.action_release_quality_actual_batch()
        self.assertEqual((production.state, production.product_qty, production.qty_produced), ('done', 2, 2))
        facts = production.native_stock_fact_ids
        finished = production.move_finished_ids.filtered(lambda move: move.product_id == self.product and not move.scrapped)
        self.assertEqual(set(finished.mapped('state')), {'done'})
        self.assertEqual(sum(finished.mapped('quantity')), 2)
        self.assertEqual(finished.move_line_ids.lot_id, lot)
        self.assertEqual(facts.unit_id, units)
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts.move_line_id, finished.move_line_ids)
        for line in finished.move_line_ids:
            self.assertEqual((line.product_id, line.company_id, line.location_id, line.location_dest_id),
                             (self.product, production.company_id, line.move_id.location_id, production.location_dest_id))
        self.assertEqual(self.env['stock.quant']._get_available_quantity(
            self.product, production.location_dest_id, lot_id=lot), 2)
        raw = production.move_raw_ids.filtered(lambda move: move.state == 'done')
        self.assertEqual(sum(raw.mapped('quantity')), 4)  # BoM: 2 components per finished unit.
        self.assertEqual(sum(raw.stock_valuation_layer_ids.mapped('quantity')), -4)
        self.assertEqual(sum(finished.stock_valuation_layer_ids.mapped('quantity')), 2)
        self.assertEqual(set(finished.move_line_ids.consume_line_ids.ids), set(raw.move_line_ids.ids))
        child = self._children(production)
        self.assertEqual((len(child), child.product_qty, child.qty_produced, child.qty_producing), (1, 1, 0, 0))
        self.assertFalse(child.lot_producing_id)
        self.assertTrue(child.quality_unit_route_required)
        self.assertFalse(child.quality_transfer_controlled)
        self.assertFalse(child._full_release_units() or child.native_stock_fact_ids or child.native_continuation_ids)
        self.assertFalse(child.workorder_ids.quality_execution_ids or child.workorder_ids.time_ids)
        self.assertFalse(any(child.workorder_ids.mapped('qty_produced')))
        self.assertFalse(child._full_release_checks().filtered(
            lambda row: row.operation_run_id or row.quality_state != 'none' or row.unit_admission_ids))
        self.assertFalse(self.env['quality.production.run'].search([('production_id', '=', child.id)]))
        mapping = {row.quality_native_parent_workorder_id.id: row.id for row in child.workorder_ids}
        expected = {(point, operation, mapping[workorder] if workorder else 0)
                    for point, operation, workorder in production.quality_full_required_qcp}
        self.assertTrue(expected.issubset({tuple(row) for row in child.quality_full_required_qcp}))
        self.assertEqual(sum(child.move_raw_ids.mapped('product_uom_qty')), 2)
        self.assertEqual(self.env['quality.production.unit'].search([]).ids, unit_ids)
        self.assertEqual(units.read(['name', 'lot_id', 'registration_run_id']), identity_before)
        self.assertEqual(check.sample_ids.read(), qc_before)
        self._denied(production)
        # Reusing the same lot later remains legal, but confers no manufacture/QC.
        child.lot_producing_id = lot
        self._denied(child)

    def test_t2_missing_lot_blocks_without_creating_one(self):
        production, check = self._manufactured()
        production.lot_producing_id = False
        self._denied(production)

    def test_physical_done_pending_qc_restart_and_all_finish_aliases(self):
        for method in ('button_finish', 'button_done', 'action_mark_as_done'):
            production, check = self._manufactured(result='pending', finish_method=method)
            last = production.workorder_ids.sorted('id')[-1:]
            self.assertEqual((last.state, last.qty_produced, production.product_qty, production.qty_produced),
                             ('done', 2, 3, 0))
            self.assertEqual(check.quality_state, 'none')
            self.assertFalse(production.native_stock_fact_ids or self._children(production))
            evidence = (last.quality_execution_ids.read(), last.time_ids.read(),
                        production._full_release_units().read(), check.sample_ids.read())
            for retry in (last.button_finish, last.button_done, last.action_mark_as_done):
                retry()
            with self.assertRaises(UserError):
                last.button_start()
            last.button_start(bypass=True)  # QC callback must not restart completed work.
            self.assertEqual((last.quality_execution_ids.read(), last.time_ids.read(),
                              production._full_release_units().read(), check.sample_ids.read()), evidence)
            last.invalidate_recordset()
            self.assertEqual(last.state, 'done')
            self._denied(production)
            self._fill(check)
            check.do_pass()
            self.assertEqual((check.x_admitted_qty, last.state), (2, 'done'))
            production.action_release_quality_actual_batch()
            self.assertEqual((production.qty_produced, self._children(production).product_qty), (2, 1))

    def test_enterprise_verifier_only_defers_during_the_managed_finish(self):
        for protected in (False, True):
            production, check = self._manufactured(finish_method=None, result=None, protected=protected)
            workorder = check.workorder_id.with_context(lang='en_US', quality_physical_workorder_finish=True)
            self.assertIn(check, workorder.check_ids)
            with self.assertRaisesRegex(UserError, 'Quality Checks using the Shop Floor'):
                with self.env.cr.savepoint():
                    workorder.verify_quality_checks()
            if protected:
                workorder.button_finish()
                self.assertEqual((workorder.state, workorder.qty_produced), ('done', 2))
            else:
                with self.assertRaisesRegex(UserError, 'Quality Checks using the Shop Floor'):
                    workorder.button_finish()
                self.assertEqual(workorder.state, 'progress')
            self.assertEqual((production.qty_produced, check.quality_state), (0, 'none'))

    def test_t3_t4_wrong_product_or_company_lot_is_rejected(self):
        production, check = self._manufactured()
        other = self.env['res.company'].create({'name': 'Actual lot foreign company'})
        candidates = [self.env['stock.lot'].create({
            'name': 'Foreign actual lot product', 'product_id': self.component.id,
            'company_id': self.env.company.id}), self.env['stock.lot'].create({
                'name': 'Foreign actual lot company', 'product_id': self.product.id, 'company_id': other.id})]
        for lot in candidates:
            def release():
                production.lot_producing_id = lot
                production.action_release_quality_actual_batch()
            self._assert_denied_unchanged(production, release)

    def test_t5_conflicting_qunit_lot_preserves_registration(self):
        registered_lot = self._lot()
        production, check = self._manufactured(registered_lot=registered_lot)
        production.lot_producing_id = self._lot()
        self._denied(production)
        self.assertEqual(production._full_release_units().lot_id, registered_lot)

    def test_t6_t7_one_pending_or_failed_unit_blocks_whole_batch(self):
        for result in ('pending', 'fail'):
            production, check = self._manufactured(result=result)
            self.assertEqual(check.sample_ids[0].sample_result, 'pass')
            self.assertEqual(check.sample_ids[-1].sample_result, result)
            self._denied(production)

    def test_t8_alert_after_pass_blocks(self):
        production, check = self._manufactured()
        alert = self.env['quality.alert'].create({'name': 'Actual lot hold', 'check_id': check.id, 'team_id': self.team.id})
        alert._bind_matrix_failure_scope(check.sample_ids[:1], 'Unresolved lot defect')
        self._denied(production)

    def test_t16_full_three_of_three_uses_standard_completion(self):
        production, check = self._manufactured(actual=3)
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 3))
        self.assertFalse(self._children(production))
        self.assertEqual(len(production.native_stock_fact_ids), 3)
        self.assertEqual(production.native_stock_fact_ids.move_line_id.lot_id, production.lot_producing_id)
        self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -6)

    def test_failure_after_real_stock_facts_rolls_back_lot_stock_and_backorder(self):
        production, check = self._manufactured()
        original = type(production)._record_standard_stock_facts
        def fail(record, units):
            original(record, units)
            raise UserError('Injected lot release failure after facts')
        with patch.object(type(production), '_record_standard_stock_facts', fail):
            self._denied(production)

    def test_t17_ambiguous_identity_selection_is_rejected(self):
        production, check = self._manufactured()
        units = production._full_release_units()
        self._denied(production, lambda: production._validate_quality_actual_batch(units[:1], 2))

    def test_t13_stale_parallel_lot_request_cannot_post_twice(self):
        # This test commits OWN fixtures and needs a wholly disposable DB.
        # Never run it against the existing staging database in place.
        if not self.env.cr.dbname.startswith('qunit_enterprise_'):
            self.skipTest('Requires disposable qunit_enterprise_* DB; committed concurrency fixture.')
        registry, uid = self.registry, self.env.uid
        with registry.cursor() as cr:
            env = api.Environment(cr, uid, {})
            class Fixture(TestCase):
                _manufactured = TestQualityActualBatchLot._manufactured
                _lot = TestQualityActualBatchLot._lot
                _check = QualityFullReleaseCase._check
                def _fill(self, check):
                    for sample in check.sample_ids:
                        check.update_measurement_visual_result(sample.id, 'yes')
            fixture = Fixture()
            fixture.env = env
            fixture.warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
            # Own operation type, so the fixture never changes warehouse policy.
            picking_type = fixture.warehouse.manu_type_id.copy({'create_backorder': 'ask'})
            fixture.warehouse = SimpleNamespace(
                manu_type_id=picking_type, lot_stock_id=fixture.warehouse.lot_stock_id)
            category = env['product.category'].create({
                'name': 'Concurrent lot FIFO', 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
            fixture.product = env['product.product'].create({
                'name': 'Concurrent actual lot', 'type': 'product', 'tracking': 'lot', 'categ_id': category.id})
            component = env['product.product'].create({
                'name': 'Concurrent lot component', 'type': 'product', 'standard_price': 10, 'categ_id': category.id})
            center = env['mrp.workcenter'].create({'name': 'Concurrent lot workcenter'})
            fixture.bom = env['mrp.bom'].create({
                'product_tmpl_id': fixture.product.product_tmpl_id.id, 'product_qty': 1,
                'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': 2})],
                'operation_ids': [Command.create({'name': name, 'workcenter_id': center.id})
                                  for name in ('Concurrent cutting', 'Concurrent inspection')]})
            fixture.team = env['quality.alert.team'].create({'name': 'Concurrent lot team'})
            fixture.test_type = env['quality.point.test_type'].search([], limit=1)
            fixture.point = env['quality.point'].create({
                'title': 'Concurrent lot QC', 'team_id': fixture.team.id, 'test_type_id': fixture.test_type.id,
                'x_control_stage': 'in_process', 'visual_sample_control_required': True,
                self.operation_field: fixture.bom.operation_ids.sorted('id')[-1].id
                if env['quality.point']._fields[self.operation_field].type == 'many2one'
                else [Command.set(fixture.bom.operation_ids.sorted('id')[-1:].ids)]})
            receipt = env['stock.move'].create({
                'name': 'Concurrent lot components', 'product_id': component.id, 'product_uom': component.uom_id.id,
                'product_uom_qty': 10, 'price_unit': 10,
                'location_id': env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': fixture.warehouse.lot_stock_id.id})
            receipt._action_confirm(merge=False)
            receipt.write({'quantity': 10, 'picked': True})
            receipt._action_done()
            production, check = fixture._manufactured()
            production_id = production.id
            unfinished, pending = fixture._manufactured(finish_method=None)
            workorder_id = unfinished.workorder_ids.sorted('id')[-1].id
            cr.commit()
        self._assert_parallel_physical_finish(registry, uid, workorder_id)
        snapshot_ready, committed = Event(), Event()

        def stale_request():
            with registry.cursor() as cr:
                env = api.Environment(cr, uid, {})
                production = env['mrp.production'].browse(production_id)
                self.assertEqual(production.qty_produced, 0)
                snapshot_ready.set()
                if not committed.wait(20):
                    raise AssertionError('First lot release did not commit')
                with self.assertRaises(errors.SerializationFailure):
                    production.action_release_quality_actual_batch()
                cr.rollback()
                env = api.Environment(cr, uid, {})
                with self.assertRaises(UserError):
                    env['mrp.production'].browse(production_id).action_release_quality_actual_batch()
                cr.rollback()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stale_request)
            try:
                self.assertTrue(snapshot_ready.wait(20))
                with registry.cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    env['mrp.production'].browse(production_id).action_release_quality_actual_batch()
                    cr.commit()
                committed.set()
                future.result(timeout=30)
            finally:
                committed.set()
        with registry.cursor() as cr:
            env = api.Environment(cr, uid, {})
            production = env['mrp.production'].browse(production_id)
            self.assertEqual((production.state, production.qty_produced), ('done', 2))
            self.assertEqual(len(production.native_stock_fact_ids), 2)
            self.assertEqual(env['mrp.production'].search_count([('quality_native_parent_id', '=', production_id)]), 1)
            self.assertEqual(sum(production.move_raw_ids.filtered(lambda row: row.state == 'done').mapped('quantity')), 4)
            self.assertEqual(sum(production.move_finished_ids.stock_valuation_layer_ids.mapped('quantity')), 2)

    def _assert_parallel_physical_finish(self, registry, uid, workorder_id):
        snapshot_ready, committed = Event(), Event()
        def stale_finish():
            with registry.cursor() as cr:
                env = api.Environment(cr, uid, {})
                workorder = env['mrp.workorder'].browse(workorder_id)
                self.assertEqual(workorder.qty_produced, 0)
                snapshot_ready.set()
                if not committed.wait(20):
                    raise AssertionError('Physical finish did not commit')
                with self.assertRaises(errors.SerializationFailure):
                    workorder.button_finish()
                cr.rollback()
                env = api.Environment(cr, uid, {})
                env['mrp.workorder'].browse(workorder_id).button_finish()
                cr.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stale_finish)
            try:
                self.assertTrue(snapshot_ready.wait(20))
                with registry.cursor() as cr:
                    env = api.Environment(cr, uid, {})
                    env['mrp.workorder'].browse(workorder_id).button_finish()
                    cr.commit()
                committed.set()
                future.result(timeout=30)
            finally:
                committed.set()
        with registry.cursor() as cr:
            env = api.Environment(cr, uid, {})
            workorder = env['mrp.workorder'].browse(workorder_id)
            self.assertEqual((workorder.state, workorder.qty_produced), ('done', 2))
            self.assertEqual(len(workorder.quality_execution_ids), 2)
            self.assertEqual(len(workorder.production_id._full_release_units()), 2)
            self.assertFalse(workorder.time_ids.filtered(lambda timer: not timer.date_end))
            self.assertFalse(workorder.production_id.native_stock_fact_ids)
            self.assertEqual(workorder.production_id.product_qty, 3)
