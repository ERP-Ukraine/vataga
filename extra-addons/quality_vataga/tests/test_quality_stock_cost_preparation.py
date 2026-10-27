"""Enterprise-only time/source mapping tests. Not a substitute for real EE GL validation."""

from datetime import datetime, timedelta

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.stock_account.tests.test_stockvaluation import _create_accounting_data

from .test_quality_unit_stock_release import QualityUnitStockReleaseCase


@tagged('post_install', '-at_install')
class TestQualityStockCostPreparation(QualityUnitStockReleaseCase):
    def test_standard_real_time_employee_plan_is_preparation_only(self):
        input_account, output_account, asset, expense, journal = _create_accounting_data(self.env)
        category = self.env['product.category'].create({'name': 'Disposable EE Standard perpetual',
            'property_cost_method': 'standard', 'property_valuation': 'real_time',
            'property_stock_account_input_categ_id': input_account.id,
            'property_stock_account_output_categ_id': output_account.id,
            'property_stock_valuation_account_id': asset.id, 'property_stock_journal': journal.id})
        # Only new test records, before creating any MO/permission. Receive
        # real components through native stock; do not fabricate SVLs or GL.
        self.product.write({'categ_id': category.id, 'standard_price': 40})
        self.components = self.env['product.product']
        for line in self.bom.bom_line_ids:
            component = self.env['product.product'].create({'name': 'Actual EE component',
                'type': 'product', 'categ_id': category.id, 'standard_price': 2})
            self.components |= component
            line.product_id = component
            incoming = self.env['stock.move'].create({'name': 'Actual EE receipt', 'product_id': component.id,
                'product_uom': component.uom_id.id, 'product_uom_qty': 100, 'price_unit': 2,
                'location_id': self.env.ref('stock.stock_location_suppliers').id,
                'location_dest_id': self.warehouse.lot_stock_id.id})
            incoming._action_confirm(merge=False)
            incoming.write({'quantity': 100, 'picked': True})
            incoming._action_done()
        check, source, last, units, target, permission = self._authorized()
        timer = self._shared_employee_timer(source)
        self._bind(check.production_id, timer, units).action_apply()
        before = self._stock_snapshot(check.production_id)
        contract = check.production_id._stock_cost_preparation()
        self.assertEqual(contract['profile'], 'standard_real_time_v1')
        self.assertEqual(contract['standard_unit_value'], 40)
        self.assertEqual(len(contract['accounting_routes']), 6)
        self.assertFalse(contract['posting_available'])
        action = self._wizard(check.production_id, permission.unit_id).action_prepare()
        plan = self.env['quality.unit.stock.release.plan'].browse(action['res_id'])
        with self.assertRaises(UserError):
            self.env['quality.stock.posting']._post_plan(plan)
        self.assertEqual(self._stock_snapshot(check.production_id), before)

    def _shared_employee_timer(self, source, closed=True):
        self.assertIn('employee_id', self.env['mrp.workcenter.productivity']._fields,
                      'Run this integration contract with the installed Enterprise Shop Floor modules.')
        employee = self.env['hr.employee'].create({'name': 'Actual Enterprise timer employee', 'company_id': source.company_id.id})
        start = datetime(2026, 1, 12, 8)
        return self.env['mrp.workcenter.productivity'].create({
            'workorder_id': source.id, 'workcenter_id': source.workcenter_id.id,
            'loss_id': self.env.ref('mrp.block_reason7').id, 'date_start': start,
            'date_end': start + timedelta(hours=1) if closed else False, 'employee_id': employee.id,
        })

    def _bind(self, production, timer, units):
        return self.env['quality.stock.timer.binding.wizard'].create({
            'production_id': production.id, 'timer_id': timer.id,
            'unit_ids': [Command.set(units.ids)], 'reason': 'Фактичний склад пайки підтверджено за маршрутним листом',
        })

    def test_employee_and_unmapped_shared_time_are_explicitly_unverified(self):
        check, source, last, units, target, permission = self._authorized()
        timer = self._shared_employee_timer(source)
        before = self._stock_snapshot(check.production_id)
        contract = check.production_id._stock_cost_preparation()
        fact = next(row for row in contract['timers'] if row['id'] == timer.id)
        self.assertTrue(fact['employee_costing'])
        self.assertTrue(fact['missing_scope'])
        self.assertEqual(fact['employees'], timer.employee_id.ids)
        self.assertFalse(fact['units'])
        self.assertFalse(contract['posting_available'])
        self.assertEqual(contract['employee_policy'], 'native_enterprise_contract_required')
        self.assertEqual(self._stock_snapshot(check.production_id), before)

    def test_binding_requires_explicit_units_and_keeps_gate_and_stock_unchanged(self):
        check, source, last, units, target, permission = self._authorized()
        timer = self._shared_employee_timer(source)
        before = self._stock_snapshot(check.production_id)
        selected = units[:2]
        self._bind(check.production_id, timer, selected).action_apply()
        binding = self.env['quality.stock.timer.binding']._for_production(check.production_id)
        self.assertEqual(binding.unit_ids, selected)
        self.assertEqual(binding.actor_id, self.env.user)
        self.assertTrue(binding.bound_at)
        with self.assertRaises(UserError):
            binding.write({'unit_ids': [Command.set(units.ids)]})
        with self.assertRaises(ValidationError):
            self._bind(check.production_id, timer, selected).action_apply()
        contract = check.production_id._stock_cost_preparation()
        fact = next(row for row in contract['timers'] if row['id'] == timer.id)
        self.assertFalse(fact['missing_scope'])
        self.assertEqual(fact['units'], selected.ids)
        self.assertEqual(self._stock_snapshot(check.production_id), before)
        with self.assertRaises(UserError):
            self.env['quality.stock.posting']._require_enterprise_validation()

    def test_open_shared_timer_cannot_be_certified(self):
        check, source, last, units, target, permission = self._authorized()
        timer = self._shared_employee_timer(source, closed=False)
        with self.assertRaises(ValidationError):
            self._bind(check.production_id, timer, units[:1]).action_apply()

    def test_binding_cannot_mix_production_orders(self):
        check, source, last, units, target, permission = self._authorized()
        other, other_source, other_last, other_units, other_target, other_permission = self._authorized()
        timer = self._shared_employee_timer(source)
        with self.assertRaises(ValidationError):
            self._bind(other.production_id, timer, other_units[:1]).action_apply()
        with self.assertRaises(ValidationError):
            self._bind(check.production_id, timer, other_units[:1]).action_apply()

    def test_either_missing_role_denies_shared_timer_wizard(self):
        for missing in ('mrp.group_mrp_user', 'stock.group_stock_user'):
            user = self._user('time_scope_' + missing, ['mrp.group_mrp_user', 'stock.group_stock_user'])
            self.env.ref(missing).write({'users': [Command.unlink(user.id)]})
            with self.assertRaises(AccessError):
                self.env['quality.stock.timer.binding.wizard'].with_user(user).create({})

    def test_rpc_flags_do_not_supply_employee_costing_or_posting_authority(self):
        check, source, last, units, target, permission = self._authorized()
        timer = self._shared_employee_timer(source)
        self._bind(check.production_id, timer, units).action_apply()
        before = self._stock_snapshot(check.production_id)
        with self.assertRaises(ValidationError):
            self.env['quality.stock.cost.scope'].with_context(
                employee_cost_validated=True, labour_cost=0, native_enterprise_contract_required=False,
            )._freeze_from_executions(check.production_id, {}, selected=permission.unit_id, scope_key='employee-contract')
        with self.assertRaises(UserError):
            self.env['quality.stock.posting'].with_context(enterprise_validated=True)._require_enterprise_validation()
        self.assertEqual(self._stock_snapshot(check.production_id), before)
