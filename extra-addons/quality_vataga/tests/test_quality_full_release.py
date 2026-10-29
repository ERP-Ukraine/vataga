"""Actual staging-UI/native stock scenarios; disposable Enterprise database only."""
from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase
from . import test_quality_unit_transfer as transfers
from ..models.quality_production_identity import IDENTIFICATION_CONTEXT, IDENTIFICATION_TOKEN


@tagged('post_install', '-at_install')
class QualityFullReleaseCase(QualityCheckInProcessCase):
    _identify = transfers.TestQualityUnitTransfer._identify
    _decision = transfers.TestQualityUnitTransfer._decision

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.point.picking_type_ids = [Command.clear()]
        category = cls.env['product.category'].create({
            'name': 'Full-release FIFO', 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
        cls.product.write({'type': 'product', 'categ_id': category.id})
        cls.component = cls.env['product.product'].create({
            'name': 'Full-release component', 'type': 'product', 'categ_id': category.id, 'standard_price': 10})
        center = cls.env['mrp.workcenter'].create({'name': 'Full-release operations'})
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': cls.product.uom_id.id,
            'bom_line_ids': [Command.create({'product_id': cls.component.id, 'product_qty': 2})],
            'operation_ids': [Command.create({'name': name, 'workcenter_id': center.id})
                              for name in ('Cutting', 'Crimping', 'Inspection')],
        })
        fields = cls.point._fields
        cls.operation_field = next((name for name in ('operation_id', 'operation_ids')
            if name in fields and getattr(fields[name], 'comodel_name', None) == 'mrp.routing.workcenter'), None)
        if not cls.operation_field:
            cls.operation_field = next((name for name, field in fields.items()
                if getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter'
                and field.type in ('many2one', 'many2many') and not field.compute), None)
        if not cls.operation_field:
            raise AssertionError('Enterprise QCP must expose its real routing-operation assignment')
        operation = cls.bom.operation_ids.sorted('id')[-1]
        cls.point[cls.operation_field] = operation.id if fields[cls.operation_field].type == 'many2one' else [Command.set(operation.ids)]
        receipt = cls.env['stock.move'].create({
            'name': 'Disposable full-release components', 'product_id': cls.component.id,
            'product_uom': cls.component.uom_id.id, 'product_uom_qty': 40,
            'location_id': cls.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': cls.warehouse.lot_stock_id.id, 'price_unit': 10})
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 40, 'picked': True})
        receipt._action_done()

    def _prepared(self, identify=True, admit=True, finish=True, result='pass', protected=True):
        # Create one explicit real QC from the configured final-operation QCP;
        # verify applicability independently of generation and of this QC row.
        self.point.picking_type_ids = [Command.clear()]
        self.point.quality_unit_route_required = protected
        production = self.env['mrp.production'].create({
            'product_id': self.product.id, 'product_qty': 2, 'product_uom_id': self.product.uom_id.id,
            'bom_id': self.bom.id, 'picking_type_id': self.warehouse.manu_type_id.id,
            'location_src_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.lot_stock_id.id})
        production.action_confirm()
        production.qty_producing = 2
        first, second, last = production.workorder_ids.sorted('id')
        self.point.picking_type_ids = [Command.set(self.warehouse.manu_type_id.ids)]
        check = self._check(production, workorder_id=last.id)
        first.button_start()
        first.button_finish()
        second.button_start()
        second.button_finish()
        last.button_start()
        if identify:
            self._identify(check, last)
        self._fill(check)
        if result == 'pass':
            check.do_pass()
            if identify and admit:
                self._decision(check).action_apply()
            if finish:
                last.button_finish()
            else:
                last.button_pending()
        else:
            check.update_measurement_visual_result(check.sample_ids[-1].id, 'no' if result == 'fail' else False)
            last.button_pending()
        if protected:
            production.action_enable_quality_full_release_test()
        return production, check

    def _snapshot(self, production):
        return (production.read(['state', 'product_qty', 'qty_produced', 'qty_producing']),
                production.workorder_ids.read(['state', 'qty_produced']),
                (production.move_raw_ids | production.move_finished_ids).read(['state', 'quantity']),
                self.env['quality.production.unit'].search([]).read(),
                self.env['quality.production.run'].search([]).read(),
                self.env['quality.unit.admission'].search([]).read(),
                self.env['quality.unit.stock.fact'].search([]).ids,
                self.env['stock.quant'].search([('product_id', 'in', (self.product | self.component).ids)]).read(),
                self.env['stock.valuation.layer'].search([]).ids)

    def _assert_denied_unchanged(self, production, call=None):
        before = self._snapshot(production)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            (call or production.action_open_quality_native_release)()
        self.assertEqual(self._snapshot(production), before)

@tagged('post_install', '-at_install')
class TestQualityFullRelease(QualityFullReleaseCase):
    def test_p1_n8_n9_full_two_last_operation_qc_no_backorder_under_any_policy(self):
        for policy in ('always', 'never'):
            with self.subTest(backorder_policy=policy):
                self.warehouse.manu_type_id.create_backorder = policy
                production, check = self._prepared()
                first_two = production.workorder_ids.sorted('id')[:2]
                self.assertFalse(self.env['quality.check'].search([
                    ('point_id', '=', self.point.id), ('workorder_id', 'in', first_two.ids)]))
                self.assertFalse(production.quality_transfer_controlled)
                self.assertTrue(production.quality_unit_route_required)
                self.assertEqual(production.qty_produced, 0)
                action = production.action_open_quality_native_release()
                wizard = self.env[action['res_model']].browse(action['res_id'])
                self.assertEqual(set(wizard.unit_ids.ids), set(check.sample_ids.controlled_unit_id.ids))
                self._assert_denied_unchanged(production, wizard.action_confirm)
                before_qty = self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id)
                wizard.confirmed = True
                wizard.action_confirm()
                self.assertEqual((production.state, production.qty_produced), ('done', 2))
                self.assertFalse(self.env['mrp.production'].search([('quality_native_parent_id', '=', production.id)]))
                moves = production.move_finished_ids.filtered(lambda move: move.product_id == self.product and not move.scrapped)
                self.assertEqual(set(moves.mapped('state')), {'done'})
                self.assertEqual(sum(moves.move_line_ids.mapped('quantity')), 2)
                self.assertEqual(self.env['stock.quant']._get_available_quantity(self.product, self.warehouse.lot_stock_id) - before_qty, 2)
                self.assertEqual(sum(production.move_raw_ids.stock_valuation_layer_ids.mapped('quantity')), -4)
                self.assertEqual(sum(moves.stock_valuation_layer_ids.mapped('quantity')), 2)
                self.assertEqual(len(production.native_stock_fact_ids), 2)
                before = self._snapshot(production)
                wizard.action_confirm()
                self.assertEqual(self._snapshot(production), before)
                self.assertEqual(production.action_open_quality_native_release()['res_model'], 'quality.unit.stock.fact')

    def test_n1_pass_without_qrun_is_not_released(self):
        production, check = self._prepared(identify=False)
        self.assertEqual(check.quality_state, 'pass')
        self.assertFalse(check.operation_run_id)
        self._assert_denied_unchanged(production)

    def test_n2_draft_qrun_without_qunit_does_not_authorize_full_batch(self):
        production, check = self._prepared(identify=False)
        run = self.env['quality.production.run'].with_context(**{IDENTIFICATION_CONTEXT: IDENTIFICATION_TOKEN}).create({
            'production_id': production.id, 'workorder_id': production.workorder_ids.sorted('id')[-1].id,
            'quantity': 2, 'origin_check_id': check.id, 'registration_mode': 'new'})
        self.assertEqual(run.state, 'draft')
        self.assertFalse(run.unit_ids)
        self._assert_denied_unchanged(production)

    def test_n3_no_inspector_admission(self):
        production, check = self._prepared(admit=False)
        self.assertTrue(check.operation_run_id)
        self.assertFalse(check.unit_admission_ids)
        self._assert_denied_unchanged(production)

    def test_n4_pending_and_n5_fail_cannot_release(self):
        for result in ('pending', 'fail'):
            production, check = self._prepared(result=result)
            if result == 'fail':
                self.env['quality.alert'].create({'name': 'Full-release FAIL hold', 'check_id': check.id, 'team_id': self.team.id})
            self._assert_denied_unchanged(production)

    def test_n6_unfinished_operation_is_not_quality_pass(self):
        production, check = self._prepared(finish=False)
        self.assertEqual(check.quality_state, 'pass')
        self.assertTrue(check.unit_admission_ids)
        self._assert_denied_unchanged(production)

    def test_n7_wrong_mo_units_and_forged_wizard_values(self):
        first, check = self._prepared()
        other, other_check = self._prepared()
        self._assert_denied_unchanged(first, lambda: first._quality_native_release(
            other_check.sample_ids.controlled_unit_id, 'foreign-unit-test', confirmed=True, full_only=True))
        action = first.action_open_quality_native_release()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self._assert_denied_unchanged(first, lambda: wizard.write({'unit_ids': [Command.set(other_check.sample_ids.controlled_unit_id.ids)]}))
        self.assertEqual(other.qty_produced, 0)

    def test_n10_ordinary_mo_still_uses_native_standard_completion(self):
        production, check = self._prepared(identify=False, protected=False)
        self.assertFalse(production.quality_unit_route_required)
        production.button_mark_done()
        self.assertEqual((production.state, production.qty_produced), ('done', 2))
        self.assertFalse(production.native_stock_fact_ids)

    def test_n11_new_alert_after_pass_blocks_confirmed_wizard(self):
        production, check = self._prepared()
        action = production.action_open_quality_native_release()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        wizard.confirmed = True
        alert = self.env['quality.alert'].create({'name': 'Hold after PASS', 'check_id': check.id, 'team_id': self.team.id})
        alert._bind_matrix_failure_scope(check.sample_ids[:1], 'New unresolved defect before stock posting')
        self._assert_denied_unchanged(production, wizard.action_confirm)

    def test_missing_configured_qc_cannot_be_inferred_as_unnecessary(self):
        production, check = self._prepared()
        point = self.point.copy({'title': 'Another mandatory final inspection'})
        point.picking_type_ids = [Command.set(self.warehouse.manu_type_id.ids)]
        production._capture_full_required_qcp()
        self._assert_denied_unchanged(production)
        point.picking_type_ids = [Command.clear()]
        point[self.operation_field] = False if point._fields[self.operation_field].type == 'many2one' else [Command.clear()]
        self._assert_denied_unchanged(production)  # Sticky requirement survives reconfiguration.

    def test_n12_standard_buttons_and_direct_writes_stay_closed(self):
        production, check = self._prepared()
        move = production.move_finished_ids[:1]
        for call in (production.button_mark_done, production._post_inventory,
                     lambda: production.write({'state': 'done'}), move._action_done,
                     lambda: move.write({'state': 'done'}),
                     lambda: self.env['stock.move.line'].create({'move_id': move.id, 'state': 'done', 'quantity': 2}),
                     lambda: production.write({'quality_full_release_test_database': self.env.cr.dbname}),
                     lambda: production.write({'quality_full_required_qcp': []})):
            self._assert_denied_unchanged(production, call)
