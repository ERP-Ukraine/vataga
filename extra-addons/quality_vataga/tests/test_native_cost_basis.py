"""Actual Community ORM regression for cost snapshots and native analytics."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_native_partial_adapter as adapter


@tagged('post_install', '-at_install', 'native_cost_basis')
class TestNativeCostBasis(StockReleaseNativeCase):
    _timed = adapter.TestNativePartialAdapter._timed
    _new_work = adapter.TestNativePartialAdapter._new_work
    _release = adapter.TestNativePartialAdapter._release
    _denied = adapter.TestNativePartialAdapter._denied
    _snapshot = adapter.TestNativePartialAdapter._snapshot

    def test_real_native_analytics_are_cumulative_while_stock_uses_incremental_cost(self):
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM account_analytic_line')
        previous_analytic_id = self.env.cr.fetchone()[0]
        accounts = self.env['account.analytic.account'].create([
            {'name': 'Native cost audit MO', 'plan_id': self.env.ref('analytic.analytic_plan_projects').id},
            {'name': 'Native cost audit WC', 'plan_id': self.env.ref('analytic.analytic_plan_projects').id}])
        self.workcenter.analytic_distribution = {str(accounts[1].id): 100}
        production = self._timed()
        production.analytic_distribution = {str(accounts[0].id): 100}
        wo = production.workorder_ids
        self.env.flush_all()
        lines = wo.mo_analytic_account_line_ids | wo.wc_analytic_account_line_ids
        self.assertEqual(sorted(lines.mapped('amount')), [-60, -60])
        line_ids = sorted(lines.ids)
        scene = {'production': production, 'workorder': wo, 'accounts': accounts,
                 'previous_analytic_id': previous_analytic_id}
        audit = production._native_employee_audit_analytics(scene)
        self.assertTrue(set(line_ids).issubset({row['id'] for row in audit['analytic_lines']}))
        self.assertEqual(sorted(row['amount'] for row in audit['analytic_lines'] if row['id'] in line_ids), [-60, -60])
        first = self._release(production)
        self.assertEqual(sum(first.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 140)
        self._new_work(production)
        self.env.flush_all()
        lines = wo.mo_analytic_account_line_ids | wo.wc_analytic_account_line_ids
        self.assertEqual(sorted(lines.mapped('amount')), [-75, -75])
        second = self._release(production, ('5',), 'analytics-second')
        self.assertEqual(sum(second.unit_ids.move_line_id.move_id.stock_valuation_layer_ids.mapped('value')), 35)
        self.env.flush_all()
        self.assertEqual(sorted((wo.mo_analytic_account_line_ids | wo.wc_analytic_account_line_ids).ids), line_ids)
        before = lines.read(['amount', 'unit_amount', 'account_id'])
        wo._compute_duration()
        self.env.flush_all()
        self.assertEqual(lines.read(['amount', 'unit_amount', 'account_id']), before)
        self.assertEqual(sorted(row['amount'] for row in production._native_employee_audit_analytics(scene)['analytic_lines']
                                if row['id'] in line_ids),
                         [-75, -75])

    def test_changed_cost_input_after_stock_is_rejected_at_checkpoint_insertion(self):
        production = self._timed()
        reached = []

        def change_after_stock(record, operation):
            self.assertEqual(record.qty_produced, 4)
            reached.append(True)
            timer = record.workorder_ids.time_ids
            timer.write({'duration': timer.duration + 1})
        with patch.object(type(production), '_native_partial_after_stock', change_after_stock):
            self._denied(production, lambda: self._release(production))
        self.assertTrue(reached)

    def test_old_incomplete_checkpoint_and_negative_booked_cost_are_rejected(self):
        production = self._timed()
        checkpoint = self._release(production).cost_ids
        basis = production.workorder_ids._native_partial_cost_basis()
        legacy = json.loads(json.dumps(checkpoint.basis))
        legacy.pop('version')
        with self.assertRaisesRegex(UserError, 'Старий checkpoint'):
            production.workorder_ids._native_partial_check_cost_values(basis,
                SimpleNamespace(basis=legacy, cumulative_cost=60), 60)
        with self.assertRaises(UserError):
            production.workorder_ids._native_partial_check_cost_values(basis,
                SimpleNamespace(basis=checkpoint.basis, cumulative_cost=-1), 60)

    def test_capitalized_loss_category_cannot_be_changed_through_stored_recompute(self):
        production = self._timed()
        category = self.env['mrp.workcenter.productivity.loss.type'].create({'loss_type': 'productive'})
        reason = self.env['mrp.workcenter.productivity.loss'].create({'name': 'Own cost loss type', 'loss_id': category.id})
        production.workorder_ids.time_ids.loss_id = reason
        self._release(production)
        before = self._snapshot(production)
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                category.write({'loss_type': 'performance'})
                self.env.flush_all()
        self.assertEqual(category.loss_type, 'productive')
        self.assertEqual(self._snapshot(production), before)

    def test_enterprise_audit_on_community_creates_no_fixture_and_grants_no_authorization(self):
        before = {name: self.env[name].search([]).ids for name in (
            'mrp.production', 'account.analytic.line', 'stock.move', 'stock.valuation.layer')}
        report = self.env['mrp.production']._run_native_employee_cost_audit(self.env.cr.dbname)
        self.assertEqual(report['result'], 'FAIL')
        self.assertEqual(report['rollback'], 'NO_FIXTURE_CREATED')
        self.assertFalse(report['production_provider_authorized'])
        self.assertFalse(report['stock_posting_exercised'])
        self.assertEqual(before, {name: self.env[name].search([]).ids for name in before})
