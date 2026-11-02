"""Real Enterprise QC/Stock/SVL/checkpoints on new fixtures; ALWAYS rollback."""
import copy
import traceback
import uuid
from contextvars import ContextVar
from datetime import timedelta

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_cost_provider import (describe_method, reviewed_enterprise_cost,
                                   ENTERPRISE_COST_FILE, ENTERPRISE_COST_SHA256)
from .native_enterprise_integration import enterprise_fixture_transaction, STAGING_DATABASE
from .native_partial_release import NativePartialWorkorder, _CORE_COST
from .native_analytic_diagnostic import value_differences
from .quality_partial_release_probe import _assert


_PROBE = ContextVar('quality_production_loss_probe', default=None)
_LOSS_CHECKS = (
    'unregistered_provider_blocked', 'unapproved_board', 'overlapping_partition',
    'pass_cannot_replace_fail', 'conflicting_alert', 'incomplete_consumption',
    'rollback_after_stock', 'rollback_after_checkpoint', 'real_4_plus_1_190_152_38',
    'persisted_checkpoints', 'retry_unchanged', 'posted_analytics_stable',
    'checkpoint_write', 'checkpoint_unlink', 'loss_fact_write', 'capitalized_duration',
    'capitalized_interval', 'capitalized_employee', 'capitalized_employee_cost',
    'capitalized_timer_unlink', 'different_request', 'extra_stock_release',
    'capitalized_hourly_rate', 'capitalized_equipment_rate',
)


class _InjectedLossFault(Exception):
    pass


class QualityProductionLossProbe(models.Model):
    _inherit = 'mrp.production'

    def _quality_loss_probe_boundary(self, stage, release=None):
        scope = _PROBE.get()
        if (not scope or self.env.cr is not scope['cursor']
                or self.id not in scope['production_ids']):
            return
        self.env.flush_all()
        admission = scope['admission']
        moves = self.move_raw_ids | self.move_finished_ids
        for records in (moves, moves.move_line_ids, moves.stock_valuation_layer_ids):
            admission.remember(records)
        evidence = {'stage': stage, 'mo_id': self.id, 'state': self.state,
                    'qty_produced': self.qty_produced,
                    'finished_value': sum(self.move_finished_ids.stock_valuation_layer_ids.mapped('value')),
                    'raw_value': sum(self.move_raw_ids.stock_valuation_layer_ids.mapped('value'))}
        _assert((evidence['state'], evidence['qty_produced'], evidence['finished_value'], evidence['raw_value'])
                == ('done', 4, 152, -100), 'Fault boundary did not reach real native Stock/SVL')
        if release:
            for records in (release, release.cost_ids, release.unit_ids, release.loss_ids,
                            self.native_stock_fact_ids,
                            self.env['quality.production.loss.fact'].search([('release_id', '=', release.id)])):
                admission.remember(records)
            evidence.update(checkpoint_ids=release.cost_ids.ids,
                            cumulative_cost=sum(release.cost_ids.mapped('cumulative_cost')),
                            loss_ids=release.loss_ids.ids,
                            loss_fact_ids=self.env['quality.production.loss.fact'].search([
                                ('release_id', '=', release.id)]).ids)
            _assert(len(release.cost_ids) == 3 and evidence['cumulative_cost'] == 90
                    and len(release.loss_ids) == 1 and len(evidence['loss_fact_ids']) == 1,
                    'Fault boundary did not reach persisted checkpoints and loss facts')
        scope['report'].setdefault('boundaries', []).append(evidence)
        if scope.get('fault') == stage:
            raise _InjectedLossFault(stage)

    def _native_partial_after_stock(self, operation):
        result = super()._native_partial_after_stock(operation)
        if operation.loss_keys:
            self._quality_loss_probe_boundary('after_stock')
        return result

    def _native_partial_after_post(self, release, operation):
        result = super()._native_partial_after_post(release, operation)
        if operation.loss_keys:
            self._quality_loss_probe_boundary('after_checkpoint', release)
        return result

    @api.model
    def _quality_loss_probe_setup(self, tracking='none', all_pass=False):
        scope = _PROBE.get()
        if not scope or scope['cursor'] is not self.env.cr:
            raise AccessError('Production loss fixtures require the private rollback transaction')
        scope['report']['stage'] = 'setup.qc_and_workorders'
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM account_analytic_line')
        previous_analytic_id = self.env.cr.fetchone()[0]
        scene = self._quality_partial_probe_setup(tracking, all_pass=all_pass)
        production, orders = scene['production'], scene['orders']
        scope['report']['stage'] = 'setup.employee_and_analytics'
        Timer = self.env['mrp.workcenter.productivity']
        if not {'employee_id', 'employee_cost', 'total_cost'}.issubset(Timer._fields):
            raise ValidationError(_('Тест потребує реального Enterprise-обліку вартості працівника.'))
        suffix = uuid.uuid4().hex[:10]
        employee = self.env['hr.employee'].create({
            'name': 'QLOSS employee ' + suffix,
            'company_id': self.env.company.id, 'hourly_cost': 30})
        accounts = self.env['account.analytic.account'].create([
            {'name': 'QLOSS ' + label + suffix, 'company_id': self.env.company.id,
             'plan_id': self.env.ref('analytic.analytic_plan_projects').id} for label in ('MO', 'WC')])
        orders.workcenter_id.analytic_distribution = {str(accounts[1].id): 100}
        production.analytic_distribution = {str(accounts[0].id): 100}
        # Only NEW fixture intervals. Never change the actor's employee or rate.
        orders.time_ids.unlink()
        scope['report']['stage'] = 'setup.closed_employee_intervals'
        for index, workorder in enumerate(orders):
            start = scene['anchor'] + timedelta(minutes=20 * index)
            values = workorder._prepare_timeline_vals(0, start, start + timedelta(minutes=20))
            values.update(user_id=False, employee_id=employee.id)
            Timer.create(values)
        self.env.flush_all()
        _assert(sum(t.total_cost for t in orders.time_ids) == 30, 'Native employee cost is not 30')
        _assert(sum(_CORE_COST(wo) for wo in orders) == 60, 'Native equipment cost is not 60')
        _assert(sum(wo._cal_cost() for wo in orders) == 90, 'Native equipment + employee cost is not 90')
        production.qty_producing = 5
        production._set_qty_producing()
        production.move_raw_ids.picked = True
        scene.update(employee=employee, accounts=accounts, previous_analytic_id=previous_analytic_id)
        for records in (production, employee, accounts, orders, orders.workcenter_id, orders.time_ids):
            scope['admission'].remember(records)
        return scene

    @api.model
    def _quality_loss_probe_unchanged(self, label, before, after):
        differences = value_differences(before, after)
        _PROBE.get()['report'].setdefault('comparisons', []).append({
            'name': label, 'equal': not differences, 'differences': differences})
        _assert(not differences, 'State changed: ' + label + '\n' + str(differences))

    @api.model
    def _quality_loss_probe_snapshot(self, scene):
        self.env.flush_all()
        production, orders = scene['production'], scene['orders']
        moves = self.env['stock.move'].search([('product_id', 'in',
            (production.product_id | scene['component']).ids)], order='id')
        release = production._native_partial_history()
        analytics = {}
        for wo in orders:
            analytics[str(wo.id)] = self._native_employee_audit_analytics({**scene, 'workorder': wo})
            scope = _PROBE.get()
            scope['admission'].remember(self.env['account.analytic.line'].browse(
                [row['id'] for row in analytics[str(wo.id)]['analytic_lines']]))
        return copy.deepcopy({
            'mo': production.read(['state', 'product_qty', 'qty_produced', 'qty_producing']),
            'moves': moves.read(['state', 'quantity', 'product_uom_qty', 'price_unit',
                                 'production_id', 'raw_material_production_id']),
            'lines': moves.move_line_ids.sorted('id').read([
                'move_id', 'quantity', 'lot_id', 'consume_line_ids', 'produce_line_ids',
                'location_id', 'location_dest_id']),
            'svl': moves.stock_valuation_layer_ids.sorted('id').read([
                'stock_move_id', 'quantity', 'value', 'remaining_qty', 'remaining_value', 'account_move_id']),
            'quants': self.env['stock.quant'].search([('product_id', 'in', moves.product_id.ids)], order='id').read([
                'product_id', 'location_id', 'lot_id', 'quantity', 'reserved_quantity']),
            'release': release.read(['request_key', 'quantity', 'disposition', 'allocation', 'configuration']),
            'checkpoint': release.cost_ids.sorted('id').read(['workorder_id', 'cumulative_cost', 'basis', 'timer_ids']),
            'loss': release.loss_ids.read(['unit_key', 'quantity', 'value', 'accounting_status']),
            'stock_facts': production.native_stock_fact_ids.sorted('id').read(['unit_id', 'move_line_id', 'request_key']),
            'loss_facts': scene['alert'].production_loss_fact_ids.read(['unit_id', 'loss_id', 'value', 'accounting_status']),
            'analytics': analytics,
            'workorders': {str(wo.id): {'duration': wo.duration, 'cost': wo._cal_cost(),
                'basis': wo._native_partial_cost_inputs()} for wo in orders},
            'account_move_ids': self.env['account.move'].search([]).ids,
            'mo_ids': self.search([]).ids, 'scrap_ids': self.env['stock.scrap'].search([]).ids})

    @api.model
    def _quality_loss_probe_denied(self, scene, name, callback, expected=UserError, message=None):
        report = _PROBE.get()['report']
        report['stage'] = 'negative.' + name
        before = self._quality_loss_probe_snapshot(scene)
        try:
            with self.env.cr.savepoint():
                callback()
                self.env.flush_all()
        except expected as error:
            if message:
                _assert(message in str(error), 'Wrong denial in ' + name + ': ' + str(error))
            self._quality_loss_probe_unchanged('negative.' + name, before, self._quality_loss_probe_snapshot(scene))
            report.setdefault('checks', []).append({'name': name, 'result': 'PASS',
                'error_type': type(error).__name__, 'error': str(error)})
            return
        raise ValidationError('Scenario was not denied: ' + name)

    @api.model
    def _quality_loss_probe_scenario(self, tracking='none', admission=None):
        scope = _PROBE.get()
        report = scope['report']
        scene = self._quality_loss_probe_setup(tracking)
        production, alert, check = (scene[n] for n in ('production', 'alert', 'check'))
        orders, good, failed = (scene[n] for n in ('orders', 'good', 'failed'))
        # Recognition alone (even on this cursor) grants no access.
        self._quality_loss_probe_denied(scene, 'unregistered_provider_blocked',
            orders[0]._native_partial_cost_basis, message='Неперевірений постачальник вартості WO')
        admission.register(production)
        scope['production_ids'].add(production.id)
        report['stage'] = 'provider.admitted_fixture_only'
        for wo in orders:
            wo._native_partial_cost_basis()
        report['cost_mro'] = describe_method(orders, '_cal_cost', (
            NativePartialWorkorder._cal_cost, _CORE_COST,
            reviewed_enterprise_cost(orders, NativePartialWorkorder._cal_cost, _CORE_COST)))
        _assert(all(row['approved'] for row in report['cost_mro']), 'Unapproved cost MRO in fixture')
        runs = production._full_release_units().run_ids
        execution = orders.quality_execution_ids
        history = lambda: (check.sample_ids.read(['sample_result', 'controlled_unit_id']),
            runs.read(['state', 'quantity', 'unit_ids', 'workorder_id']),
            execution.read(['state', 'controlled_unit_id', 'workorder_id', 'ended_at']))
        original_history = history()
        baseline = self._quality_loss_probe_snapshot(scene)
        _assert(all(state['analytic_lines'] for state in baseline['analytics'].values()),
                'Real analytic lines were not created for all workorders')
        self._quality_loss_probe_denied(scene, 'unapproved_board', alert.action_scrap_defective_units)
        alert.write({'decision_type': 'scrap', 'approval_user_id': self.env.uid})
        alert.action_send_to_analysis()
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        _assert(not alert.production_loss_fact_ids, 'Board approval fabricated a loss fact')
        keys = lambda units: ['qunit:%s' % unit.id for unit in units]
        self._quality_loss_probe_denied(scene, 'overlapping_partition',
            lambda: production._native_loss_close(keys(good), keys(good[:1]), 'probe-overlap'),
            message='перетинається')
        self._quality_loss_probe_denied(scene, 'pass_cannot_replace_fail',
            lambda: production._native_loss_close(keys(good[1:] | failed), keys(good[:1]), 'probe-pass-loss'))
        def conflicting_alert():
            self.env['quality.alert'].create({'name': 'QLOSS conflicting fixture decision',
                'check_id': check.id, 'team_id': alert.team_id.id, 'decision_type': 'repair'})
            alert.action_scrap_defective_units()
        self._quality_loss_probe_denied(scene, 'conflicting_alert', conflicting_alert,
            message='Інший відкритий Alert')
        def incomplete_consumption():
            production.move_raw_ids.write({'quantity': 8, 'picked': True})
            alert.action_scrap_defective_units()
        self._quality_loss_probe_denied(scene, 'incomplete_consumption', incomplete_consumption,
            message='повне споживання')
        for boundary in ('after_stock', 'after_checkpoint'):
            scope['fault'] = boundary
            try:
                self._quality_loss_probe_denied(scene, 'rollback_' + boundary,
                    alert.action_scrap_defective_units, expected=_InjectedLossFault, message=boundary)
            finally:
                scope['fault'] = None
        report['stage'] = 'post.real_quality_stock_and_checkpoint'
        alert.action_scrap_defective_units()
        release = production._native_partial_history()
        _assert(len(release) == 1 and release.disposition == 'terminal_loss', 'Wrong terminal settlement')
        _assert((release.allocation['total_value'], release.allocation['good_value'], release.allocation['loss_value'])
                == (190, 152, 38), 'Native actual 190 was not allocated as 152 + 38')
        _assert((release.allocation['component_value'], release.allocation['work_value']) == (100, 90),
                'Allocation does not contain real components 100 and work 90')
        _assert(set(production.native_stock_fact_ids.unit_id.ids) == set(good.ids), 'Good stock identities differ')
        _assert(alert.production_loss_fact_ids.unit_id == failed and not failed.native_stock_fact_ids,
                'Failed QUNIT has no unique loss or was posted as finished stock')
        _assert((production.product_qty, production.qty_produced, production.state) == (5, 4, 'done'),
                'Plan/actual/native closure differs from 5/4/done')
        _assert(all(cp.basis == cp.workorder_id._native_partial_cost_basis() and cp.cumulative_cost == 30
                    and set(cp.timer_ids.ids) == set(cp.workorder_id.time_ids.ids) for cp in release.cost_ids)
                and len(release.cost_ids) == 3, 'Persisted employee checkpoints differ from real inputs')
        for cp in release.cost_ids:
            cp.workorder_id._native_partial_check_checkpoint(cp)
        posted = self._quality_loss_probe_snapshot(scene)
        _assert(posted['mo_ids'] == baseline['mo_ids'] and posted['scrap_ids'] == baseline['scrap_ids']
                and posted['account_move_ids'] == baseline['account_move_ids'], 'Unexpected Backorder/Scrap/GL')
        _assert(history() == original_history, 'Historical QC/QRUN/unit execution changed')
        quant = self.env['stock.quant'].search([('product_id', '=', production.product_id.id),
            ('location_id', '=', production.location_dest_id.id)])
        _assert(sum(quant.mapped('quantity')) == 4 and len(release.loss_ids) == 1
                and release.loss_ids.value == 38 and release.loss_ids.accounting_status == 'manual_periodic_pending',
                'Physical stock/loss accounting status differs')
        if tracking == 'lot':
            _assert(quant.lot_id == production.lot_producing_id
                    and release.unit_ids.move_line_id.lot_id == production.lot_producing_id, 'Lot lineage differs')
        report['stage'] = 'retry_and_analytic_stability'
        alert.action_scrap_defective_units()
        production.button_mark_done()
        self._quality_loss_probe_unchanged('retry', posted, self._quality_loss_probe_snapshot(scene))
        orders._compute_duration()
        self._quality_loss_probe_unchanged('extra_duration', posted, self._quality_loss_probe_snapshot(scene))
        report.setdefault('checks', []).extend([
            {'name': name, 'result': 'PASS'} for name in (
                'real_4_plus_1_190_152_38', 'persisted_checkpoints', 'retry_unchanged', 'posted_analytics_stable')])
        timer = orders.time_ids.sorted('id')[0]
        mutations = (
            ('checkpoint_write', lambda: release.cost_ids[:1].write({'cumulative_cost': 0})),
            ('checkpoint_unlink', lambda: release.cost_ids[:1].unlink()),
            ('loss_fact_write', lambda: alert.production_loss_fact_ids.write({'unit_id': good[0].id})),
            ('capitalized_duration', lambda: timer.write({'duration': timer.duration + 1})),
            ('capitalized_interval', lambda: timer.write({'date_end': timer.date_end + timedelta(minutes=1)})),
            ('capitalized_employee', lambda: timer.write({'employee_id': False})),
            ('capitalized_employee_cost', lambda: timer.write({'employee_cost': 31})),
            ('capitalized_timer_unlink', timer.unlink),
            ('different_request', lambda: production._native_loss_close(keys(good), keys(failed), 'another-request')),
            ('extra_stock_release', lambda: production._native_partial_release(keys(failed), 'another-release')),
        )
        for name, callback in mutations:
            self._quality_loss_probe_denied(scene, name, callback)
        def hourly_rate():
            scene['employee'].hourly_cost = 31
            self.env.flush_all()
            for cp in release.cost_ids:
                cp.workorder_id._native_partial_check_checkpoint(cp)
        def equipment_rate():
            orders[0].workcenter_id.costs_hour = 61
            for cp in release.cost_ids:
                cp.workorder_id._native_partial_check_checkpoint(cp)
        self._quality_loss_probe_denied(scene, 'capitalized_hourly_rate', hourly_rate)
        self._quality_loss_probe_denied(scene, 'capitalized_equipment_rate', equipment_rate)
        alert.action_close_alert()
        _assert(alert._is_managed_closed(), 'Alert did not close from actual loss evidence')
        report.update(result='PASS', stage='complete', tracking=tracking, plan=5, good_quantity=4, loss_quantity=1,
            component_cost=100, equipment_cost=60, employee_cost=30, total_cost=190, good_value=152, loss_value=38,
            mo_state=production.state, backorders=0, scrap_documents=0, accounting_status='manual_periodic_pending',
            gl_entries_created=0, synthetic_timer_preparation=True, shop_floor_timer_start_proven=False,
            posted_evidence=posted)
        return report

    @api.model
    def _run_quality_production_loss_probe(self, expected_database, tracking='none'):
        if (self.env.cr.dbname != expected_database or expected_database != STAGING_DATABASE
                or not self.env.user.has_group('base.group_system')):
            raise AccessError(_('Потрібні системний адміністратор і точна назва staging-бази.'))
        if tracking not in ('none', 'lot'):
            raise ValidationError(_('Тест підтримує none або lot.'))
        report = {'result': 'FAIL', 'tracking': tracking, 'database': self.env.cr.dbname,
                  'production_provider_authorized': False, 'admission': 'rollback_fixture_only',
                  'stage': 'preflight', 'checks': [], 'expected_checks': len(_LOSS_CHECKS),
                  'passed': 0, 'failed': 0, 'not_run': len(_LOSS_CHECKS)}
        report['expected_cost_source'] = {'file': ENTERPRISE_COST_FILE, 'sha256': ENTERPRISE_COST_SHA256}
        report['cost_mro'] = describe_method(self.env['mrp.workorder'], '_cal_cost',
                                             (NativePartialWorkorder._cal_cost, _CORE_COST))
        candidate = reviewed_enterprise_cost(self.env['mrp.workorder'], NativePartialWorkorder._cal_cost, _CORE_COST)
        report['source_matches'] = bool(candidate)
        report['reviewed_enterprise_duration_chain'] = self.env['mrp.workorder']._native_analytic_duration_chain_reviewed()
        if not candidate or not report['reviewed_enterprise_duration_chain']:
            report.update(error='Unreviewed Enterprise cost/duration source or MRO', rollback='NO_FIXTURE_CREATED')
            return report
        try:
            with enterprise_fixture_transaction(self, report) as (fixture, admission):
                token = _PROBE.set({'cursor': fixture.env.cr, 'production_ids': set(),
                                    'report': report, 'admission': admission})
                try:
                    fixture._quality_loss_probe_scenario(tracking, admission)
                finally:
                    _PROBE.reset(token)
        except Exception as error:
            report.update(result='FAIL', error_type=type(error).__name__, error=str(error),
                          traceback=''.join(traceback.format_exception(type(error), error, error.__traceback__)))
            name = report['stage'].removeprefix('negative.')
            if name in _LOSS_CHECKS and not any(row['name'] == name for row in report['checks']):
                report['checks'].append({'name': name, 'result': 'FAIL',
                                        'error_type': type(error).__name__, 'error': str(error)})
        names = [row['name'] for row in report['checks']]
        if len(names) != len(set(names)) or set(names) - set(_LOSS_CHECKS):
            report.update(result='FAIL', error='Integration check names are duplicated or unknown')
        report['passed'] = sum(row['result'] == 'PASS' for row in report['checks'])
        report['failed'] = sum(row['result'] == 'FAIL' for row in report['checks'])
        report['not_run_names'] = sorted(set(_LOSS_CHECKS) - set(names))
        report['not_run'] = len(report['not_run_names'])
        if report['result'] == 'PASS' and report['not_run']:
            report.update(result='FAIL', error='Integration check list is incomplete')
        return report
