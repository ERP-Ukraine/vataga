"""Actual Community ORM regression for cost snapshots and native analytics."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_native_partial_adapter as adapter
from ..models.native_employee_cost_audit import (
    _AUDIT_CHECKS, _audit_case_outcome, _audit_check_summary, _audit_fixture_transaction,
)


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
        self.assertEqual((report['expected_checks'], report['passed'], report['failed'], report['not_run']),
                         (14, 0, 0, 14))
        self.assertEqual(before, {name: self.env[name].search([]).ids for name in before})

    def _audit_actor(self):
        # Installed admin is an ordinary uid, not uid=1 / sudo. Its native
        # MRP manager ACL still cannot create or write loss categories.
        actor = self.env['mrp.production'].with_user(self.env.ref('base.user_admin'))
        self.assertFalse(actor.env.su)
        categories = actor.env['mrp.workcenter.productivity.loss.type']
        self.assertTrue(categories.check_access_rights('read', raise_exception=False))
        self.assertFalse(categories.check_access_rights('create', raise_exception=False))
        self.assertFalse(categories.check_access_rights('write', raise_exception=False))
        return actor

    def _audit_categories(self):
        return self.env['mrp.workcenter.productivity.loss.type'].search([]).read(['loss_type'])

    def test_audit_temporary_categories_allow_normal_actor_and_rollback_on_success(self):
        actor = self._audit_actor()
        before = self._audit_categories()
        existing_id = before[0]['id']
        with _audit_fixture_transaction(actor) as audit:
            self.assertFalse(audit.env.su)
            self.assertEqual(audit.env.uid, actor.env.uid)
            self.assertIsNot(audit.env.cr, actor.env.cr)
            with self.assertRaises(AccessError), audit.env.cr.savepoint():
                audit.env['mrp.workcenter.productivity.loss.type'].create({'loss_type': 'productive'})
            productive = audit._native_employee_audit_loss_type('productive')
            quality = audit._native_employee_audit_loss_type('quality')
            created_ids = (productive | quality).ids
            self.assertEqual((productive.loss_type, quality.loss_type), ('productive', 'quality'))
            self.assertFalse(productive.env.su)
            self.assertFalse(quality.env.su)
            # Reasons and operations keep the actor's permissions. Both
            # fixture setup and native-duration conversion use these rows.
            reasons = audit.env['mrp.workcenter.productivity.loss'].create([
                {'name': 'Own audit productive reason', 'loss_id': productive.id},
                {'name': 'Own audit quality reason', 'loss_id': quality.id}])
            self.assertFalse(reasons.env.su)
            self.assertEqual(reasons.mapped('loss_id').ids, created_ids)
            existing = audit.env['mrp.workcenter.productivity.loss.type'].browse(existing_id)
            with self.assertRaises(AccessError):
                audit._native_employee_audit_change_loss_category(existing)
            with self.assertRaises(AccessError), audit.env.cr.savepoint():
                productive.write({'loss_type': 'performance'})
            with self.assertRaises(UserError), audit.env.cr.savepoint():
                audit._native_employee_audit_change_loss_category(productive)
                audit.env.flush_all()
                self.assertEqual(productive.loss_type, 'performance')
                raise UserError('Nested audit rollback')
            self.assertEqual(productive.loss_type, 'productive')
            audit._native_employee_audit_change_loss_category(productive)
            self.assertEqual(productive.loss_type, 'performance')
        self.env.invalidate_all(flush=False)
        self.assertEqual(self._audit_categories(), before)
        self.assertFalse(self.env['mrp.workcenter.productivity.loss.type'].browse(created_ids).exists())
        self.assertFalse(self.env['mrp.workcenter.productivity.loss'].browse(reasons.ids).exists())

    def test_audit_categories_rollback_on_exception_and_clear_postcommit_callbacks(self):
        actor = self._audit_actor()
        before = self._audit_categories()
        called = []
        with self.assertRaisesRegex(UserError, 'Fixture fault'):
            with _audit_fixture_transaction(actor) as audit:
                category = audit._native_employee_audit_loss_type('quality')
                category_id = category.id
                audit.env.cr.postcommit.add(lambda: called.append(True))
                audit._native_employee_audit_change_loss_category(category)
                audit.env.flush_all()
                raise UserError('Fixture fault')
        self.env.invalidate_all(flush=False)
        self.assertFalse(called)
        self.assertFalse(audit.env.cr.postcommit._funcs)
        self.assertEqual(self._audit_categories(), before)
        self.assertFalse(self.env['mrp.workcenter.productivity.loss.type'].browse(category_id).exists())
        with self.assertRaises(AccessError):
            actor._native_employee_audit_loss_type('productive')

    def test_audit_elevation_cannot_escape_own_cursor_actor_or_created_categories(self):
        actor = self._audit_actor()
        with self.assertRaises(AccessError):
            actor._native_employee_audit_loss_type('quality')
        before_mo = actor.search([]).ids
        with self.assertRaises(AccessError):
            actor._native_employee_audit_setup()
        self.assertEqual(actor.search([]).ids, before_mo)
        with _audit_fixture_transaction(actor) as audit:
            category = audit._native_employee_audit_loss_type('productive')
            with self.assertRaises(AccessError):
                actor._native_employee_audit_loss_type('quality')
            with self.assertRaises(AccessError):
                actor._native_employee_audit_change_loss_category(category)
            with self.assertRaises(AccessError):
                audit.sudo()._native_employee_audit_change_loss_category(category)
            with self.assertRaises(ValidationError):
                audit._native_employee_audit_loss_type('availability')
            foreign = audit.env['mrp.workcenter.productivity.loss.type'].browse(self._audit_categories()[0]['id'])
            with self.assertRaises(AccessError):
                audit._native_employee_audit_change_loss_category(foreign)
        with self.assertRaises(AccessError):
            actor._native_employee_audit_change_loss_category(category.with_env(actor.env))

    def test_unexecuted_or_missing_audit_checks_never_count_as_pass(self):
        checks = [{'name': name, **_audit_case_outcome({'verified': True})} for name in _AUDIT_CHECKS]
        complete = _audit_check_summary(checks)
        self.assertEqual((complete['result'], complete['passed'], complete['not_run']), ('AUDIT_PASS', 14, 0))
        mapping = next(check for check in checks if check['name'] == 'native_user_employee_mapping')
        mapping.update(_audit_case_outcome({'exercised': False, 'reason': 'Actor has no employee'}))
        summary = _audit_check_summary(checks)
        self.assertEqual((mapping['result'], summary['result'], summary['passed'], summary['not_run']),
                         ('NOT_RUN', 'FAIL', 13, 1))
        checks.pop()
        summary = _audit_check_summary(checks)
        self.assertEqual((summary['result'], summary['passed'], summary['not_run']), ('FAIL', 12, 2))
        for empty in (None, {}, []):
            self.assertEqual(_audit_case_outcome(empty)['result'], 'FAIL')
        mapping.update(_audit_case_outcome(None))
        summary = _audit_check_summary(checks)
        self.assertEqual((summary['passed'], summary['failed'], summary['not_run']), (12, 1, 1))
        with self.assertRaises(ValidationError):
            _audit_check_summary(checks + [checks[0]])
