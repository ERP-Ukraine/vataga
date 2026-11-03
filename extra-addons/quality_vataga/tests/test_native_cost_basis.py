"""Actual Community ORM regression for cost snapshots and native analytics."""
import json
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .test_stock_release_native_contract import StockReleaseNativeCase
from . import test_native_partial_adapter as adapter
from ..models.native_employee_cost_audit import (
    _AUDIT_CHECKS, _audit_case_outcome, _audit_check_summary, _audit_exception, _audit_fixture_transaction,
)
from ..models.native_analytic_diagnostic import analytic_state_difference, trace_duration_calls
from ..models.native_analytic_sync import analytic_sync_scope
from ..models.native_enterprise_integration import (
    _INTEGRATION, _FixtureScope, enterprise_fixture_transaction,
)
from ..models import native_enterprise_integration as integration
from odoo.addons.mrp.models.mrp_workorder import MrpWorkorder as CoreWorkorder
from odoo.addons.mrp_account.models.mrp_workorder import MrpWorkorder as AccountWorkorder


@tagged('post_install', '-at_install', 'native_cost_basis')
class TestNativeCostBasis(StockReleaseNativeCase):
    _timed = adapter.TestNativePartialAdapter._timed
    _new_work = adapter.TestNativePartialAdapter._new_work
    _release = adapter.TestNativePartialAdapter._release
    _denied = adapter.TestNativePartialAdapter._denied
    _snapshot = adapter.TestNativePartialAdapter._snapshot

    def test_enterprise_capability_rejects_existing_productions_and_community_chain(self):
        production = self._timed()
        scope = _FixtureScope(production, {})
        with self.assertRaises(AccessError):
            scope.register(production)
        # A capability for an explicit ID is still insufficient: actual
        # Enterprise source, file, complete MRO and duration chain are required.
        scope.production_ids.add(production.id)
        token = _INTEGRATION.set(scope)
        try:
            with self.assertRaisesRegex(ValidationError, 'Джерело розрахунку вартості'):
                production.workorder_ids._native_partial_cost_basis()
        finally:
            _INTEGRATION.reset(token)
        self.assertEqual(production.workorder_ids._cal_cost(), 60)
        production.workorder_ids._native_partial_cost_basis()

    def test_enterprise_transaction_rejects_nonstaging_before_opening_cursor(self):
        with self.assertRaises(AccessError):
            with enterprise_fixture_transaction(self.env['mrp.production'], {}):
                self.fail('Disposable Community database must not receive Enterprise admission')
        self.assertIsNone(_INTEGRATION.get())

    def _rollback_fixture_transaction(self, fail=False):
        diagnostics, callbacks = {}, []
        production_id = None
        def exercise():
            nonlocal production_id
            with enterprise_fixture_transaction(self.env['mrp.production'], diagnostics) as (model, scope):
                product = model.env['product.product'].create({'name': 'Disposable transaction fixture', 'type': 'product'})
                production = model.create({'product_id': product.id, 'product_qty': 5,
                                           'product_uom_id': product.uom_id.id})
                production_id = production.id
                scope.register(production)
                scope.remember(product)
                model.env.cr.postcommit.add(lambda: callbacks.append('MUST NOT RUN'))
                model.env.flush_all()
                if fail:
                    raise RuntimeError('injected fixture exception')
        # Test only the transaction mechanism against this disposable DB.
        # Cost source/MRO admission is not changed or exercised here.
        with patch.object(integration, 'STAGING_DATABASE', self.env.cr.dbname):
            if fail:
                with self.assertRaisesRegex(RuntimeError, 'injected fixture exception'):
                    exercise()
            else:
                exercise()
        self.assertEqual(diagnostics['rollback'], 'COMPLETE')
        self.assertEqual(diagnostics['rollback_verified_absent']['mrp_production'], 1)
        self.assertFalse(callbacks)
        self.assertIsNone(_INTEGRATION.get())
        self.assertFalse(self.env['mrp.production'].browse(production_id).exists())

    def test_integration_own_transaction_rolls_back_success_and_discards_callbacks(self):
        self._rollback_fixture_transaction()

    def test_integration_own_transaction_rolls_back_exception_and_restores_scope(self):
        self._rollback_fixture_transaction(fail=True)

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
        with self.assertRaisesRegex(UserError, 'Старий знімок розрахунку вартості'):
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
        sources = report['sources']['analytic_methods']
        self.assertIn('account.analytic.line.create', sources)
        self.assertIn('account.analytic.line._compute_unit_quantity_amount', sources)
        conversion = sources['uom.uom._compute_quantity']
        self.assertTrue(any('_compute_quantity' in row['provider'] for row in conversion))
        self.assertTrue(all(row['wrappers'][0].get('source') for row in conversion))
        self.assertIn('product_uom_id', report['diagnostics']['analytic_uom_fields'])
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

    def test_audit_diagnostics_keep_failed_stage_and_native_traceback_after_rollback(self):
        actor = self._audit_actor()
        before = self._audit_categories()
        diagnostics = {'stage': 'transaction.open', 'stages': []}
        try:
            with _audit_fixture_transaction(actor, diagnostics) as audit:
                audit._native_employee_audit_stage('setup.loss_type_create')
                category_id = audit._native_employee_audit_loss_type('productive').id
                audit._native_employee_audit_stage('setup.flush')
                # Actual native validator, not a fabricated traceback/error.
                audit.env.ref('uom.product_uom_hour')._compute_quantity(1, audit.env.ref('uom.product_uom_unit'))
        except UserError as error:
            failure = _audit_exception(error, diagnostics['stage'])
        else:
            self.fail('Native hour-to-piece validation unexpectedly succeeded')
        self.assertEqual(failure['error_stage'], 'setup.flush')
        self.assertEqual(failure['error_type'], 'UserError')
        self.assertIn('uom_uom.py', failure['traceback'])
        self.assertIn('_compute_quantity', failure['traceback'])
        self.assertEqual(failure['error_frames'][-1]['method'], '_compute_quantity')
        self.assertTrue(all(set(frame) == {'file', 'line', 'method'} for frame in failure['error_frames']))
        self.assertEqual(diagnostics['transaction_rollback'], 'COMPLETE')
        self.assertEqual([row['stage'] for row in diagnostics['stages']],
                         ['setup.loss_type_create', 'setup.flush'])
        self.env.invalidate_all(flush=False)
        self.assertEqual(self._audit_categories(), before)
        self.assertFalse(self.env['mrp.workcenter.productivity.loss.type'].browse(category_id).exists())

    def test_actual_setup_missing_enterprise_fields_reports_its_stage_without_fixture(self):
        if 'employee_cost' in self.env['mrp.workcenter.productivity']._fields:
            self.skipTest('This missing-field regression requires Community; Enterprise uses the staging audit')
        actor = self._audit_actor()
        before = actor.search([]).ids
        diagnostics = {'stage': 'transaction.open', 'stages': []}
        try:
            with _audit_fixture_transaction(actor, diagnostics) as audit:
                audit._native_employee_audit_setup()
        except ValidationError as error:
            failure = _audit_exception(error, diagnostics['stage'])
        else:
            self.fail('Community fixture accepted missing Enterprise employee fields')
        self.assertEqual(failure['error_stage'], 'setup.validate_enterprise_fields')
        self.assertIn('_native_employee_audit_setup', failure['traceback'])
        self.assertEqual(diagnostics['transaction_rollback'], 'COMPLETE')
        self.assertEqual(actor.search([]).ids, before)

    def _diagnostic_scene(self):
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM account_analytic_line')
        previous = self.env.cr.fetchone()[0]
        accounts = self.env['account.analytic.account'].create([
            {'name': 'Own diagnostic ' + label, 'plan_id': self.env.ref('analytic.analytic_plan_projects').id}
            for label in ('MO', 'WC')])
        self.workcenter.analytic_distribution = {str(accounts[1].id): 100}
        production = self._timed()
        production.analytic_distribution = {str(accounts[0].id): 100}
        self.env.flush_all()
        return {'production': production, 'workorder': production.workorder_ids,
                'accounts': accounts, 'previous_analytic_id': previous}

    def test_real_duration_trace_keeps_mro_and_analytics_stable(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        before = production._native_employee_audit_analytics(scene)
        basis = wo._native_partial_cost_basis()
        with trace_duration_calls(wo) as trace:
            wo._compute_duration()
        after = production._native_employee_audit_analytics(scene)
        self.assertEqual(trace['status'], 'COMPLETE')
        events = trace['events']
        self.assertTrue(events)
        self.assertTrue(all(row['cached_duration'] == {str(wo.id): 60} for row in events))
        core_return = next(index for index, row in enumerate(events)
            if row['event'] == 'return' and any('odoo.addons.mrp.models.' in name
            and name.endswith('._compute_duration') for name in row['providers']))
        helper_enter = max(index for index, row in enumerate(events)
            if row['event'] == 'call' and any(name.endswith('._create_or_update_analytic_entry')
                                             for name in row['providers']))
        self.assertLess(core_return, helper_enter)
        self.assertTrue(analytic_state_difference(before, after)['equal'])
        self.assertEqual(wo._native_partial_cost_basis(), basis)
        self.assertEqual(wo._cal_cost(), 60)

    def test_real_new_work_reports_exact_line_values_duration_basis_and_cost(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        before = {**production._native_employee_audit_analytics(scene),
                  'duration': wo.duration, 'native_cost': wo._cal_cost(), 'basis': wo._native_partial_cost_basis()}
        self._new_work(production)
        after = {**production._native_employee_audit_analytics(scene),
                 'duration': wo.duration, 'native_cost': wo._cal_cost(), 'basis': wo._native_partial_cost_basis()}
        change = analytic_state_difference(before, after)
        self.assertEqual(change['analytic_lines']['added'], [])
        self.assertEqual(change['analytic_lines']['removed'], [])
        self.assertEqual(len(change['analytic_lines']['changed_by_id']), 2)
        for differences in change['analytic_lines']['changed_by_id'].values():
            values = {row['path']: (row['before'], row['after']) for row in differences}
            self.assertEqual(values['/amount'], (-60, -75))
            self.assertEqual(values['/unit_amount'], (1, 1.25))
        self.assertIn('basis', change['sections'])
        self.assertEqual(change['sections']['duration'], [{'path': '/duration', 'before': 60, 'after': 75}])
        self.assertEqual(change['sections']['native_cost'], [{'path': '/native_cost', 'before': 60, 'after': 75}])
        self.assertNotIn('analytic_relations', change['sections'])
        with _audit_fixture_transaction(self._audit_actor()) as audit:
            unchanged = audit._native_employee_audit_compare_states('own.real_change', before, after)
            with self.assertRaisesRegex(ValidationError, 'STATE_DIFF:') as caught:
                audit._native_employee_audit_assert_comparisons(unchanged, 'Own changed state', ('own.real_change',))
            reported = json.loads(str(caught.exception).split('STATE_DIFF: ', 1)[1])
            self.assertEqual(reported[0]['analytic_lines']['changed_by_id'],
                             json.loads(json.dumps(change['analytic_lines']['changed_by_id'])))
            self.assertEqual(reported[0]['before_values']['native_cost'], 60)
            self.assertEqual(reported[0]['after_values']['native_cost'], 75)

    def test_stored_analytic_diagnostic_observes_fault_without_healing_or_recomputing(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        initial = production._native_employee_audit_analytics(scene)
        line_id = initial['analytic_lines'][0]['id']
        line = self.env['account.analytic.line'].browse(line_id)
        line.amount = -1  # Deliberate fault in a disposable analytic fixture only.
        self.env.flush_all()
        with trace_duration_calls(wo) as trace:
            stored = production._native_employee_audit_stored_state(
                scene, [row['id'] for row in initial['analytic_lines']], initial['_read_columns'])
        self.assertEqual(trace['events'], [])
        self.assertEqual(next(row['amount'] for row in stored['analytic_lines'] if row['id'] == line_id), -1)
        self.assertEqual(line.amount, -1)
        self.assertEqual(wo._cal_cost(), 60)
        self.assertEqual({row['id'] for row in stored['analytic_lines']},
                         {row['id'] for row in initial['analytic_lines']})

    def test_native_trace_restores_profiler_on_exception_and_preserves_existing_profiler(self):
        scene = self._diagnostic_scene()
        wo = scene['workorder']
        previous = sys.getprofile()
        with self.assertRaises(UserError):
            with trace_duration_calls(wo):
                wo._compute_duration()
                raise UserError('Own diagnostic fault')
        self.assertIs(sys.getprofile(), previous)
        observer = lambda frame, event, argument: None
        try:
            sys.setprofile(observer)
            with trace_duration_calls(wo) as trace:
                wo._compute_duration()
            self.assertIs(sys.getprofile(), observer)
            self.assertEqual(trace['status'], 'NOT_RUN')
            self.assertIn('existing thread profiler', trace['reason'])
        finally:
            sys.setprofile(previous)

    @staticmethod
    def _pre_super_duration_pattern(workorders):
        # Reproduce the supplied call ORDER against real Community analytics.
        # This is not an Enterprise provider, source attestation or cost mock.
        workorders._create_or_update_analytic_entry()
        return CoreWorkorder._compute_duration(workorders)

    def test_pre_super_order_reproduces_stale_values_on_unmanaged_wo(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        before = production._native_employee_audit_analytics(scene)
        with patch.object(AccountWorkorder, '_compute_duration', self._pre_super_duration_pattern), \
                patch.object(type(wo), '_native_analytic_duration_chain_reviewed', return_value=True):
            self._new_work(production)
            after = production._native_employee_audit_analytics(scene)
            self.assertEqual((wo.duration, wo._cal_cost()), (75, 75))
            self.assertEqual([row['amount'] for row in after['analytic_lines']], [-60, -60])
            self.assertEqual([row['unit_amount'] for row in after['analytic_lines']], [1, 1])
            self.assertEqual([row['id'] for row in before['analytic_lines']],
                             [row['id'] for row in after['analytic_lines']])

    def test_managed_duration_sync_updates_once_after_real_compute_without_replacing_lines(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        before = production._native_employee_audit_analytics(scene)
        ids = [row['id'] for row in before['analytic_lines']]
        self.assertFalse(wo._native_analytic_duration_chain_reviewed())  # Community is not Enterprise.
        with patch.object(AccountWorkorder, '_compute_duration', self._pre_super_duration_pattern), \
                patch.object(type(wo), '_native_analytic_duration_chain_reviewed', return_value=True), \
                analytic_sync_scope(wo):
            with trace_duration_calls(wo) as trace:
                self._new_work(production)
                after = production._native_employee_audit_analytics(scene)
            self.assertEqual((wo.duration, wo._cal_cost()), (75, 75))
            self.assertEqual([row['id'] for row in after['analytic_lines']], ids)
            self.assertEqual([row['amount'] for row in after['analytic_lines']], [-75, -75])
            self.assertEqual([row['unit_amount'] for row in after['analytic_lines']], [1.25, 1.25])
            native_helper_calls = [row for row in trace['events'] if row['event'] == 'call' and row['ids']
                and any(name == 'odoo.addons.mrp_account.models.mrp_workorder.MrpWorkorder._create_or_update_analytic_entry'
                        for name in row['providers'])]
            self.assertEqual(len(native_helper_calls), 1)
            self.assertEqual(native_helper_calls[0]['cached_duration'], {str(wo.id): 75})
            wo._compute_duration()  # Stability check AFTER taking the automatic-update snapshot.
            repeated = production._native_employee_audit_analytics(scene)
            self.assertTrue(analytic_state_difference(after, repeated)['equal'])
        self.assertFalse(wo._native_analytic_duration_chain_reviewed())

    def test_managed_analytic_sync_error_rolls_back_and_does_not_leave_deferred_callbacks(self):
        scene = self._diagnostic_scene()
        production, wo = scene['production'], scene['workorder']
        before = production._native_employee_audit_analytics(scene)
        timer = wo.time_ids
        original_end = timer.date_end
        native_helper = AccountWorkorder._create_or_update_analytic_entry

        def failing_helper(records):
            if records:
                raise UserError('Own post-duration analytic fault')
            return native_helper(records)

        with patch.object(AccountWorkorder, '_compute_duration', self._pre_super_duration_pattern), \
                patch.object(type(wo), '_native_analytic_duration_chain_reviewed', return_value=True), \
                analytic_sync_scope(wo):
            with self.assertRaisesRegex(UserError, 'Own post-duration'), self.env.cr.savepoint():
                with patch.object(AccountWorkorder, '_create_or_update_analytic_entry', failing_helper):
                    timer.date_end = original_end + timedelta(minutes=15)
                    self.env.flush_all()
            self.assertEqual(timer.date_end, original_end)
            self.assertEqual(wo._cal_cost(), 60)
            self.assertEqual(production._native_employee_audit_analytics(scene), before)
            timer.date_end = original_end + timedelta(minutes=15)
            after = production._native_employee_audit_analytics(scene)
            self.assertEqual([row['amount'] for row in after['analytic_lines']], [-75, -75])
            self.assertEqual([row['id'] for row in after['analytic_lines']],
                             [row['id'] for row in before['analytic_lines']])
