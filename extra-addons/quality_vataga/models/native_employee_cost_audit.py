"""Rollback-only native employee/analytic audit. NEVER authorizes cost providers.

No PartialPosting capability, price override, Stock posting or fake checkpoint
is used. The comparison reference is in memory, not a booked cost document.
Real immutable-checkpoint and 4+1 acceptance remain gated until Enterprise QA.
"""
import copy
import json
import math
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from types import SimpleNamespace

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_partial_release import _CORE_COST, _read
from .native_cost_provider import describe_method
from .native_analytic_diagnostic import analytic_state_difference, trace_duration_calls
from .native_analytic_sync import analytic_sync_scope


_FIXTURE_SCOPE = ContextVar('quality_employee_audit_fixture', default=None)
_AUDIT_CHECKS = (
    'repeat_duration_analytics', 'new_closed_work_and_analytics',
    'overlap_wc_union_employee_sum', 'employee_fallback_rate',
    'native_loss_duration_conversion', 'native_user_employee_mapping',
    'hourly_rate', 'compensating_rates_same_total', 'employee_assignment',
    'interval_duration', 'effective_employee_cost', 'loss_category',
    'injected_error_rollback', 'production_gate_remains_closed',
)


@contextmanager
def _audit_fixture_transaction(model, diagnostics=None):
    """Own cursor, ordinary actor, narrowly tracked temporary categories only.

    Never yield the caller's cursor: even a successful probe must roll back
    its fixture and discard its postcommit callbacks before returning.
    """
    context = {'allowed_company_ids': model.env.companies.ids,
               'lang': model.env.lang, 'tz': model.env.user.tz}
    if diagnostics is None:
        diagnostics = {'stage': 'transaction.open', 'stages': []}
    with model.env.registry.cursor() as audit_cr:
        env = api.Environment(audit_cr, model.env.uid, context)
        audit = env[model._name].with_company(model.env.company.id)
        token = _FIXTURE_SCOPE.set({'env': audit.env, 'loss_type_ids': set(), 'diagnostics': diagnostics})
        try:
            yield audit
        finally:
            try:
                audit_cr.rollback()
                diagnostics['transaction_rollback'] = 'COMPLETE'
            finally:
                _FIXTURE_SCOPE.reset(token)


def _audit_check_summary(checks):
    names = [check['name'] for check in checks]
    _assert(len(names) == len(set(names)) and set(names).issubset(_AUDIT_CHECKS),
            'Unexpected or duplicate employee audit check')
    _assert(all(check['result'] in ('PASS', 'FAIL', 'NOT_RUN') for check in checks),
            'Unexpected employee audit result')
    passed = sum(check['result'] == 'PASS' for check in checks)
    failed = sum(check['result'] == 'FAIL' for check in checks)
    not_run = len(_AUDIT_CHECKS) - passed - failed
    return {'expected_checks': len(_AUDIT_CHECKS), 'passed': passed, 'failed': failed,
            'not_run': not_run, 'result': 'AUDIT_PASS' if passed == len(_AUDIT_CHECKS) else 'FAIL'}


def _audit_case_outcome(detail):
    if not isinstance(detail, dict) or not detail:
        return {'result': 'FAIL', 'error': 'Audit case returned no verification detail'}
    return {'result': 'NOT_RUN' if detail.get('exercised') is False else 'PASS', 'detail': detail}


def _audit_exception(error, stage):
    # No locals: return code locations and the native exception, never a dump
    # of user records or credentials from frame environments.
    return {'error_type': type(error).__name__, 'error': str(error), 'error_stage': stage,
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__)),
            'error_frames': [{'file': frame.filename, 'line': frame.lineno, 'method': frame.name}
                             for frame in traceback.extract_tb(error.__traceback__)]}


class _AuditRollback(Exception):
    def __init__(self, value=None):
        self.value = value


def _assert(condition, message):
    if not condition:
        raise ValidationError(message)


class NativeEmployeeCostAudit(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _native_employee_audit_fixture_scope(self):
        scope = _FIXTURE_SCOPE.get()
        if (not scope or scope['env'].cr is not self.env.cr
                or scope['env'].uid != self.env.uid or scope['env'].su != self.env.su
                or scope['env'].company != self.env.company):
            raise AccessError(_('Тимчасові категорії дозволені лише у транзакції аудиту з відкатом.'))
        return scope

    @api.model
    def _native_employee_audit_stage(self, stage, **details):
        diagnostics = self._native_employee_audit_fixture_scope()['diagnostics']
        diagnostics['stage'] = stage
        diagnostics['stages'].append({'stage': stage, **details})

    @api.model
    def _native_employee_audit_uoms(self, products):
        products = _read(products)
        return [{'product_id': product.id, 'product': product.name,
                 'uom_id': product.uom_id.id, 'uom': product.uom_id.name,
                 'category_id': product.uom_id.category_id.id,
                 'category': product.uom_id.category_id.name} for product in products]

    @api.model
    def _native_employee_audit_loss_type(self, loss_type):
        scope = self._native_employee_audit_fixture_scope()
        _assert(loss_type in ('productive', 'quality'), 'Unexpected fixture loss category')
        # Native ACL intentionally allows only reading categories, even for
        # MRP managers. Elevate only creation of this fresh temporary row.
        category = self.env['mrp.workcenter.productivity.loss.type'].sudo().create({'loss_type': loss_type})
        scope['loss_type_ids'].add(category.id)
        return category.with_env(self.env)

    @api.model
    def _native_employee_audit_change_loss_category(self, category):
        scope = self._native_employee_audit_fixture_scope()
        if (category._name != 'mrp.workcenter.productivity.loss.type' or len(category) != 1
                or category.env.cr is not self.env.cr or category.id not in scope['loss_type_ids']):
            raise AccessError(_('Дозволена зміна лише власної тимчасової категорії аудиту.'))
        # Never elevate a search or an existing category; these IDs came only
        # from this cursor's fixture creation and cannot survive its rollback.
        return category.sudo().write({'loss_type': 'performance'})

    @api.model
    def _native_employee_audit_setup(self):
        """Only fresh fixture rows. Reuse an existing analytic plan read-only.

        Creating a new plan can change the registry/schema; a savepoint does
        not restore Python registry fields. This probe must not do that.
        """
        self._native_employee_audit_stage('setup.validate_enterprise_fields')
        if not {'employee_cost', 'employee_id', 'total_cost'}.issubset(self.env['mrp.workcenter.productivity']._fields):
            raise ValidationError(_('Потрібні справжні Enterprise-поля вартості працівників.'))
        self._native_employee_audit_stage('setup.analytic_baseline')
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM account_analytic_line')
        previous_analytic_id = self.env.cr.fetchone()[0]
        suffix = 'QCOST-' + uuid.uuid4().hex[:12]
        self._native_employee_audit_stage('setup.analytic_plan_read')
        plan = _read(self.env.ref('analytic.analytic_plan_projects'))
        self._native_employee_audit_stage('setup.analytic_accounts_create')
        accounts = self.env['account.analytic.account'].create([
            {'name': suffix + ' ' + label, 'plan_id': plan.id, 'company_id': self.env.company.id}
            for label in ('MO', 'WC')])
        self._native_employee_audit_stage('setup.workcenter_create')
        center = self.env['mrp.workcenter'].create({
            'name': suffix, 'costs_hour': 60, 'employee_costs_hour': 50,
            'analytic_distribution': {str(accounts[1].id): 100}})
        self._native_employee_audit_stage('setup.employees_create')
        employees = self.env['hr.employee'].create([
            {'name': suffix + ' A', 'company_id': self.env.company.id, 'hourly_cost': 20},
            {'name': suffix + ' B', 'company_id': self.env.company.id, 'hourly_cost': 40}])
        self._native_employee_audit_stage('setup.product_category_create')
        category = self.env['product.category'].create({
            'name': suffix, 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
        self._native_employee_audit_stage('setup.products_create')
        products = self.env['product.product'].create([
            {'name': suffix + ' finished', 'type': 'product', 'categ_id': category.id},
            {'name': suffix + ' component', 'type': 'product', 'categ_id': category.id, 'standard_price': 10}])
        self._native_employee_audit_stage('setup.product_uoms_read')
        fixture_uoms = self._native_employee_audit_uoms(products)
        self._native_employee_audit_fixture_scope()['diagnostics']['fixture_product_uoms'] = fixture_uoms
        self._native_employee_audit_stage('setup.bom_create')
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': products[0].product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': products[0].uom_id.id,
            'bom_line_ids': [(0, 0, {'product_id': products[1].id, 'product_qty': 2})],
            'operation_ids': [(0, 0, {'name': suffix, 'workcenter_id': center.id, 'time_cycle': 12})]})
        self._native_employee_audit_stage('setup.warehouse_read')
        warehouse = _read(self.env['stock.warehouse'].search([('company_id', '=', self.env.company.id)], limit=1))
        if not warehouse:
            raise ValidationError(_('Немає доступного складу цієї компанії.'))
        self._native_employee_audit_stage('setup.production_create')
        production = self.create({
            'name': suffix, 'product_id': products[0].id, 'product_qty': 5,
            'product_uom_id': products[0].uom_id.id, 'bom_id': bom.id,
            'picking_type_id': warehouse.manu_type_id.id,
            'location_src_id': warehouse.lot_stock_id.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'analytic_distribution': {str(accounts[0].id): 100}})
        self._native_employee_audit_stage('setup.production_confirm', production_id=production.id)
        production.action_confirm()
        self._native_employee_audit_stage('setup.workorder_read')
        workorder = production.workorder_ids
        workorder.ensure_one()
        self._native_employee_audit_fixture_scope()['diagnostics']['duration_sync'] = {
            'reviewed_enterprise_duration_chain': workorder._native_analytic_duration_chain_reviewed(),
            'fixture_workorder_ids': workorder.ids, 'cost_provider_authorized': False}
        self._native_employee_audit_stage('setup.loss_type_create', workorder_id=workorder.id)
        loss_type = self._native_employee_audit_loss_type('productive')
        self._native_employee_audit_stage('setup.loss_reason_create')
        loss = self.env['mrp.workcenter.productivity.loss'].create({
            'name': suffix, 'loss_id': loss_type.id, 'manual': False})
        scene = {'production': production, 'workorder': workorder, 'center': center,
                 'employees': employees, 'accounts': accounts, 'products': products, 'loss': loss,
                 'previous_analytic_id': previous_analytic_id,
                 'anchor': fields.Datetime.now().replace(microsecond=0) - timedelta(hours=6)}
        with analytic_sync_scope(workorder), self._native_employee_audit_trace(workorder, 'setup.timers_and_flush'):
            self._native_employee_audit_stage('setup.interval_a_create')
            first_timer = self._native_employee_audit_timer(scene, 0, 30, employees[0])
            self._native_employee_audit_stage('setup.interval_b_create')
            scene['timers'] = first_timer | self._native_employee_audit_timer(scene, 30, 60, employees[1])
            self._native_employee_audit_stage('setup.flush')
            self.env.flush_all()
        return scene

    @contextmanager
    def _native_employee_audit_trace(self, workorder, label):
        diagnostics = self._native_employee_audit_fixture_scope()['diagnostics']
        with trace_duration_calls(workorder) as trace:
            diagnostics.setdefault('native_call_traces', {})[label] = trace
            yield trace

    @api.model
    def _native_employee_audit_compare_states(self, label, before, after):
        comparison = {'comparison': label, **analytic_state_difference(before, after),
                      'before_values': {name: before.get(name) for name in (
                          'native_cost', 'equipment_cost', 'employee_cost', 'duration')},
                      'after_values': {name: after.get(name) for name in (
                          'native_cost', 'equipment_cost', 'employee_cost', 'duration')}}
        self._native_employee_audit_fixture_scope()['diagnostics'].setdefault(
            'state_comparisons', []).append(copy.deepcopy(comparison))
        return comparison['equal']

    @api.model
    def _native_employee_audit_assert_comparisons(self, unchanged, message, labels):
        if not unchanged:
            comparisons = self._native_employee_audit_fixture_scope()['diagnostics']['state_comparisons']
            differences = [row for row in comparisons if row['comparison'] in labels and not row['equal']]
            # Existing Scheduled Actions that print only check['error'] still
            # expose the exact diff without needing their code replaced.
            raise ValidationError(message + '\nSTATE_DIFF: ' + json.dumps(
                differences, ensure_ascii=False, sort_keys=True, default=str))

    @api.model
    def _native_employee_audit_stored_state(self, scene, line_ids, columns):
        """SELECT only, after ordinary record/field access checks. NEVER flush.

        Compare actual persisted values with ORM values, including effects
        that occur while the rest of the state is read. No computed field is
        accessed to construct this evidence.
        """
        wo = _read(scene['workorder'])
        Line = self.env['account.analytic.line']
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        clauses, params = ['ref=%s'], [scene['production'].name]
        for name, field in Line._fields.items():
            if (field.type == 'many2one' and field.comodel_name == 'account.analytic.account'
                    and field.store and field.column_type):
                clauses.append(quote(name) + ' IN %s')
                params.append(tuple(scene['accounts'].ids))
        self.env.cr.execute('SELECT id FROM ' + quote(Line._table) + ' WHERE ' + ' OR '.join(clauses), params)
        discovered_ids = set(line_ids) | {row[0] for row in self.env.cr.fetchall()}
        relations = {}
        for name, field in wo._fields.items():
            if field.comodel_name != 'account.analytic.line' or not field.store:
                continue
            if field.type == 'many2many' and field.relation:
                self.env.cr.execute('SELECT ' + quote(field.column2) + ' FROM ' + quote(field.relation)
                                    + ' WHERE ' + quote(field.column1) + '=%s', [wo.id])
            elif field.type == 'one2many' and field.inverse_name:
                self.env.cr.execute('SELECT id FROM ' + quote(Line._table) + ' WHERE '
                                    + quote(field.inverse_name) + '=%s', [wo.id])
            else:
                continue
            relations[name] = sorted(row[0] for row in self.env.cr.fetchall())
            discovered_ids.update(relations[name])
        _assert(all(line_id > scene['previous_analytic_id'] for line_id in discovered_ids),
                'Audit stored snapshot unexpectedly links an existing analytic line')
        lines = _read(Line.browse(sorted(discovered_ids)))
        lines.check_field_access_rights('read', columns)
        stored_columns = sorted({'id', *(name for name in columns
            if lines._fields[name].store and lines._fields[name].column_type)})
        duration_columns = [name for name in ('id', 'duration', 'duration_unit', 'duration_percent')
                            if wo._fields[name].store and wo._fields[name].column_type]
        wo.check_field_access_rights('read', duration_columns)
        rows = []
        if lines:
            self.env.cr.execute('SELECT ' + ','.join(map(quote, stored_columns)) + ' FROM '
                + quote(lines._table) + ' WHERE id IN %s ORDER BY id', [tuple(lines.ids)])
            rows = self.env.cr.dictfetchall()
        self.env.cr.execute('SELECT ' + ','.join(map(quote, duration_columns)) + ' FROM '
                            + quote(wo._table) + ' WHERE id=%s', [wo.id])
        return {'analytic_lines': rows, 'analytic_relations': relations,
                'workorder': self.env.cr.dictfetchall(),
                'unstored_analytic_fields': sorted(set(columns) - set(stored_columns)),
                'unstored_workorder_fields': sorted(set(('duration', 'duration_unit', 'duration_percent'))
                                                   - set(duration_columns))}

    @api.model
    def _native_employee_audit_timer(self, scene, start_minutes, end_minutes, employee):
        return self.env['mrp.workcenter.productivity'].create({
            'workorder_id': scene['workorder'].id, 'workcenter_id': scene['center'].id,
            'company_id': self.env.company.id, 'user_id': False, 'employee_id': employee.id or False,
            'loss_id': scene['loss'].id, 'date_start': scene['anchor'] + timedelta(minutes=start_minutes),
            'date_end': scene['anchor'] + timedelta(minutes=end_minutes)})

    @api.model
    def _native_employee_audit_analytics(self, scene):
        self.env.flush_all()
        wo, production = scene['workorder'], scene['production']
        Line = self.env['account.analytic.line']
        account_fields = [name for name, field in Line._fields.items()
                          if field.type == 'many2one' and field.comodel_name == 'account.analytic.account'
                          and field.store and field.column_type]
        # Discover hidden fixture lines, then enforce ordinary ACL/rules.
        clauses, params = ['ref=%s'], [production.name]
        for name in account_fields:
            clauses.append('"' + name.replace('"', '""') + '" IN %s')
            params.append(tuple(scene['accounts'].ids))
        Line.flush_model()
        self.env.cr.execute('SELECT id FROM account_analytic_line WHERE ' + ' OR '.join(clauses) + ' ORDER BY id', params)
        line_ids = {row[0] for row in self.env.cr.fetchall()}
        relations = {}
        quote = lambda name: '"' + name.replace('"', '""') + '"'
        for name, field in wo._fields.items():
            if field.type not in ('one2many', 'many2many') or field.comodel_name != 'account.analytic.line':
                continue
            if field.type == 'many2many' and field.store and field.relation:
                self.env.cr.execute('SELECT ' + quote(field.column2) + ' FROM ' + quote(field.relation)
                                    + ' WHERE ' + quote(field.column1) + '=%s', [wo.id])
                related_ids = [row[0] for row in self.env.cr.fetchall()]
            elif field.type == 'one2many' and field.inverse_name:
                self.env.cr.execute('SELECT id FROM account_analytic_line WHERE '
                                    + quote(field.inverse_name) + '=%s', [wo.id])
                related_ids = [row[0] for row in self.env.cr.fetchall()]
            else:
                related_ids = wo[name].ids
            relations[name] = sorted(related_ids)
            line_ids.update(related_ids)
        _assert(all(line_id > scene['previous_analytic_id'] for line_id in line_ids),
                'Audit fixture unexpectedly links an existing analytic line')
        lines = _read(Line.browse(sorted(line_ids)))
        columns = ['amount', 'unit_amount', 'ref', 'company_id', 'product_id', 'product_uom_id'] + account_fields
        columns += [name for name in ('employee_id', 'user_id', 'unit_quantity_amount',
                                     'unit_quantity_applicable') if name in Line._fields]
        uoms = []
        for line in lines:
            product_uom = line.product_id.uom_id
            native_uom = line.product_uom_id
            uoms.append({'line_id': line.id, 'category': line.category,
                         'analytic_uom_id': native_uom.id, 'analytic_uom': native_uom.name,
                         'analytic_uom_category_id': native_uom.category_id.id,
                         'product_id': line.product_id.id, 'product_uom_id': product_uom.id,
                         'product_uom': product_uom.name, 'product_uom_category_id': product_uom.category_id.id,
                         'same_category': bool(native_uom and product_uom
                                               and native_uom.category_id == product_uom.category_id)})
        rows = lines.read(columns)
        return {'analytic_lines': rows, 'analytic_relations': relations, 'analytic_uoms': uoms,
                '_read_columns': columns,
                'stored_before_cost_reads': self._native_employee_audit_stored_state(scene, lines.ids, columns)}

    @api.model
    def _native_employee_audit_state(self, scene):
        analytics = self._native_employee_audit_analytics(scene)
        columns = analytics.pop('_read_columns')
        wo, production = scene['workorder'], scene['production']
        state = {**analytics, 'native_cost': wo._cal_cost(), 'equipment_cost': _CORE_COST(wo),
                'employee_cost': sum(wo.time_ids.mapped('total_cost')),
                'duration': wo.duration, 'basis': wo._native_partial_cost_inputs(),
                'mo': production.read(['state', 'qty_producing', 'qty_produced', 'quality_native_partial_revision'])}
        # Preserve the original read/flush order. This additional SQL evidence
        # observes it; it never heals analytics or invalidates a compute cache.
        state['stored_after_cost_reads'] = self._native_employee_audit_stored_state(
            scene, [row['id'] for row in analytics['analytic_lines']], columns)
        return copy.deepcopy(state)

    @api.model
    def _native_employee_audit_case(self, scene, name, callback):
        self._native_employee_audit_stage('checks.' + name + '.baseline')
        before = self._native_employee_audit_state(scene)
        result = {'name': name, 'result': 'FAIL'}
        comparisons = self._native_employee_audit_fixture_scope()['diagnostics'].setdefault('state_comparisons', [])
        start_comparison = len(comparisons)
        try:
            with self.env.cr.savepoint():
                self._native_employee_audit_stage('checks.' + name + '.execute')
                detail = callback()
                self.env.flush_all()
                raise _AuditRollback(detail)
        except _AuditRollback as rollback:
            result.update(_audit_case_outcome(rollback.value))
        except Exception as error:
            stage = self._native_employee_audit_fixture_scope()['diagnostics']['stage']
            result.update(_audit_exception(error, stage))
        if len(comparisons) > start_comparison:
            result['state_comparisons'] = copy.deepcopy(comparisons[start_comparison:])
        self._native_employee_audit_stage('checks.' + name + '.verify_rollback')
        self.env.invalidate_all(flush=False)
        _assert(self._native_employee_audit_state(scene) == before, 'Nested cost/analytic case did not roll back: ' + name)
        return result

    @api.model
    def _native_employee_audit_scenario(self, scene):
        wo = scene['workorder']
        self._native_employee_audit_stage('checks.baseline_cost_and_analytics')
        baseline = self._native_employee_audit_state(scene)
        _assert(math.isclose(baseline['equipment_cost'], 60) and math.isclose(baseline['employee_cost'], 30)
                and math.isclose(baseline['native_cost'], 90), 'Native two-employee cost is not 60 + 30 = 90')
        _assert(baseline['analytic_lines'], 'Analytic distributions did not create real analytic lines')
        # In-memory captured reference, NOT an immutable booked cost document.
        reference = SimpleNamespace(basis=json.loads(json.dumps(baseline['basis'])), cumulative_cost=90)
        checks = []

        def stable_analytics():
            with self._native_employee_audit_trace(wo, 'repeat_duration_analytics.first'):
                wo._compute_duration()
                first = self._native_employee_audit_state(scene)
            with self._native_employee_audit_trace(wo, 'repeat_duration_analytics.second'):
                wo._compute_duration()
                second = self._native_employee_audit_state(scene)
            unchanged_first = self._native_employee_audit_compare_states('repeat.baseline_to_first', baseline, first)
            unchanged_second = self._native_employee_audit_compare_states('repeat.first_to_second', first, second)
            self._native_employee_audit_assert_comparisons(unchanged_first and unchanged_second,
                'Analytic lines or quantities are stale until an extra duration recomputation',
                ('repeat.baseline_to_first', 'repeat.first_to_second'))
            return {'before': baseline['analytic_lines'], 'after': first['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'repeat_duration_analytics', stable_analytics))

        def new_work():
            with self._native_employee_audit_trace(wo, 'new_closed_work_and_analytics.automatic'):
                self._native_employee_audit_timer(scene, 60, 75, scene['employees'][1])
                first = self._native_employee_audit_state(scene)
            _assert(math.isclose(first['native_cost'], 115), 'New native 15-minute work did not add 15 WC + 10 employee')
            wo._native_partial_check_cost_values(first['basis'], reference, first['native_cost'])
            with self._native_employee_audit_trace(wo, 'new_closed_work_and_analytics.explicit'):
                wo._compute_duration()
                second = self._native_employee_audit_state(scene)
            self._native_employee_audit_assert_comparisons(
                self._native_employee_audit_compare_states('new_work.automatic_to_explicit', first, second),
                'Analytic values are stale after new work and change on an extra duration recomputation',
                ('new_work.automatic_to_explicit',))
            return {'cumulative': 115, 'delta': 25, 'analytics': first['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'new_closed_work_and_analytics', new_work))

        def overlap():
            self._native_employee_audit_timer(scene, 15, 45, scene['employees'][0])
            state = self._native_employee_audit_state(scene)
            _assert(math.isclose(state['equipment_cost'], 60) and math.isclose(state['employee_cost'], 40)
                    and math.isclose(state['native_cost'], 100), 'Overlapping WC union / employee sum differ')
            try:
                wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
            except ValidationError:
                return {'native_total': 100, 'checkpoint_rejects_retroactive_work': True}
            raise ValidationError('Retroactive overlapping work passed the captured checkpoint comparison')
        checks.append(self._native_employee_audit_case(scene, 'overlap_wc_union_employee_sum', overlap))

        def fallback():
            self._native_employee_audit_timer(scene, 60, 75, self.env['hr.employee'])
            state = self._native_employee_audit_state(scene)
            _assert(math.isclose(state['native_cost'], 117.5), 'WC employee fallback did not add 15 + 12.5')
            wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
            return {'cumulative': 117.5, 'fallback_rate': 50, 'duration': state['duration'],
                    'analytics': state['analytic_lines']}
        checks.append(self._native_employee_audit_case(scene, 'employee_fallback_rate', fallback))

        def native_conversion():
            category = self._native_employee_audit_loss_type('quality')
            reason = self.env['mrp.workcenter.productivity.loss'].create({
                'name': 'QCOST native conversion ' + uuid.uuid4().hex[:8], 'loss_id': category.id})
            timer = self._native_employee_audit_timer(dict(scene, loss=reason), 60, 90, scene['employees'][1])
            self.env.flush_all()
            expected = reason._convert_to_duration(timer.date_start, timer.date_end, scene['center'])
            _assert(math.isclose(timer.duration, expected)
                    and math.isclose(timer.total_cost, timer.employee_cost * expected / 60),
                    'Employee costing bypassed the native calendar/loss duration conversion')
            return {'native_converted_minutes': expected, 'wall_clock_minutes': 30,
                    'duration_was_not_overridden': True}
        checks.append(self._native_employee_audit_case(scene, 'native_loss_duration_conversion', native_conversion))

        def actor_mapping():
            employee = _read(self.env.user.employee_id)
            if not employee:
                return {'exercised': False, 'reason': 'The actor has no employee; no existing user was modified'}
            timer = self.env['mrp.workcenter.productivity'].create({
                'workorder_id': wo.id, 'workcenter_id': scene['center'].id,
                'company_id': self.env.company.id, 'user_id': self.env.uid,
                'loss_id': scene['loss'].id, 'date_start': scene['anchor'] + timedelta(minutes=90),
                'date_end': scene['anchor'] + timedelta(minutes=105)})
            self.env.flush_all()
            _assert(timer.employee_id == employee, 'Native user-to-employee mapping differs from the supplied source')
            return {'exercised': True, 'native_user_employee_mapping_matches': True}
        checks.append(self._native_employee_audit_case(scene, 'native_user_employee_mapping', actor_mapping))

        changes = {
            'hourly_rate': lambda: scene['employees'][0].write({'hourly_cost': 30}),
            'compensating_rates_same_total': lambda: (scene['employees'][0].write({'hourly_cost': 30}),
                                                      scene['employees'][1].write({'hourly_cost': 30})),
            'employee_assignment': lambda: scene['timers'][0].write({'employee_id': scene['employees'][1].id}),
            'interval_duration': lambda: scene['timers'][0].write({'date_end': scene['anchor'] + timedelta(minutes=40)}),
            'effective_employee_cost': lambda: scene['timers'][0].write({'employee_cost': 25}),
            'loss_category': lambda: self._native_employee_audit_change_loss_category(scene['loss'].loss_id),
        }
        for name, change in changes.items():
            def changed_inputs(change=change, name=name):
                change()
                state = self._native_employee_audit_state(scene)
                if name == 'compensating_rates_same_total':
                    _assert(math.isclose(state['native_cost'], 90), 'Compensating rates did not preserve the native total')
                try:
                    wo._native_partial_check_cost_values(state['basis'], reference, state['native_cost'])
                except ValidationError:
                    return {'native_cost': state['native_cost'], 'duration': state['duration'],
                            'analytics': state['analytic_lines'], 'captured_basis_rejects_change': True}
                raise ValidationError('Changed previously captured cost inputs were accepted')
            checks.append(self._native_employee_audit_case(scene, name, changed_inputs))

        def rollback_analytics():
            scene['timers'][0].write({'date_end': scene['anchor'] + timedelta(minutes=40)})
            self.env.flush_all()
            _assert(self._native_employee_audit_state(scene) != baseline, 'Fault-injection case did not change real ORM state')
            raise UserError('EXPECTED_EMPLOYEE_ANALYTIC_ROLLBACK')
        fault = self._native_employee_audit_case(scene, 'injected_error_rollback', rollback_analytics)
        if fault.get('error') == 'EXPECTED_EMPLOYEE_ANALYTIC_ROLLBACK':
            fault.update(result='PASS', injected_error=True)
        checks.append(fault)

        def provider_gate():
            try:
                wo._native_partial_cost_basis()
            except ValidationError as error:
                _assert('MrpProductionWorkcenterLine._cal_cost' in str(error), 'Provider gate failed for an unrelated reason')
                return {'production_provider_authorized': False}
            raise ValidationError('Pending Enterprise provider was unexpectedly authorized')
        checks.append(self._native_employee_audit_case(scene, 'production_gate_remains_closed', provider_gate))
        return {'checks': checks, 'baseline': baseline, **_audit_check_summary(checks)}

    @api.model
    def _run_native_employee_cost_audit(self, expected_database):
        if self.env.cr.dbname != expected_database or not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Потрібні системний адміністратор і точна назва staging-бази.'))
        sources = self._native_partial_cost_provider_report(expected_database, include_source=True)
        # Inspect actual registered analytic/conversion providers as well as
        # WO methods. These metadata never authorize a production provider.
        for model, methods in (
                ('account.analytic.account', ('_perform_analytic_distribution',)),
                ('account.analytic.line', ('create', 'write', '_compute_unit_quantity_amount',
                                           '_compute_unit_quantity_applicable')),
                ('mrp.workorder', ('_set_duration', '_native_analytic_duration_chain_reviewed')),
                ('uom.uom', ('_compute_quantity',))):
            for method in methods:
                sources['analytic_methods'][model + '.' + method] = describe_method(
                    self.env[model], method, include_source=True)
        analytic_fields = {}
        for name in ('product_id', 'product_uom_id', 'unit_amount', 'amount',
                     'unit_quantity_amount', 'unit_quantity_applicable'):
            field = self.env['account.analytic.line']._fields.get(name)
            if field is None:
                continue
            compute = field.compute
            analytic_fields[name] = {'type': field.type, 'store': bool(field.store),
                                     'compute': compute if isinstance(compute, str) else repr(compute),
                                     'depends': self.env.registry.field_depends.get(field, ())}
            if isinstance(compute, str):
                sources['analytic_methods']['account.analytic.line.' + compute] = describe_method(
                    self.env['account.analytic.line'], compute, include_source=True)
        report = {**_audit_check_summary([]), 'checks': [], 'scope': 'native_employee_cost_and_analytics_only',
                  'production_provider_authorized': False, 'stock_posting_exercised': False,
                  'persisted_checkpoint_exercised': False, 'terminal_loss_flow_proven': False,
                  'sources': sources, 'diagnostics': {'stage': 'preflight.source_mro', 'stages': [],
                                                      'analytic_uom_fields': analytic_fields}}
        candidate = sources['enterprise_cost_candidate']
        unapproved = [row for row in sources['mrp.workorder._cal_cost'] if not row['approved']]
        if (len(candidate) != 1 or not candidate[0]['source_matches'] or len(unapproved) != 1
                or unapproved[0]['provider'] != candidate[0]['provider']):
            report.update(error='The exact reported Enterprise cost source/MRO is not present', rollback='NO_FIXTURE_CREATED')
            return report
        names = ('mrp.production', 'mrp.workorder', 'mrp.workcenter', 'mrp.workcenter.productivity',
            'mrp.workcenter.productivity.loss', 'mrp.workcenter.productivity.loss.type', 'mrp.bom',
            'product.product', 'hr.employee', 'account.analytic.account', 'account.analytic.line',
            'stock.move', 'stock.move.line', 'stock.quant', 'stock.valuation.layer', 'account.move',
            'quality.native.partial.release', 'quality.native.partial.cost')
        # Own transaction: finally rolls it back and clears its postcommit
        # callbacks as well. Caller transaction receives only the plain report.
        diagnostics = report['diagnostics']
        try:
            with _audit_fixture_transaction(self, diagnostics) as audit:
                env = audit.env
                audit._native_employee_audit_stage('transaction.snapshot_before')
                before = {name: env[name].search_count([]) for name in names}
                try:
                    with env.cr.savepoint():
                        scene = audit._native_employee_audit_setup()
                        with analytic_sync_scope(scene['workorder']):
                            report.update(audit._native_employee_audit_scenario(scene))
                        audit._native_employee_audit_stage('checks.final_flush')
                        env.flush_all()
                        raise _AuditRollback()
                except _AuditRollback:
                    pass
                except Exception as error:
                    report.update(result='FAIL', **_audit_exception(error, diagnostics['stage']))
                env.invalidate_all(flush=False)
                audit._native_employee_audit_stage('transaction.verify_rollback')
                if before != {name: env[name].search_count([]) for name in names}:
                    raise ValidationError(_('Не підтверджено відкат аудиту працівників та аналітики.'))
        except Exception as error:
            report.update(result='FAIL', **_audit_exception(error, diagnostics['stage']))
        if report['result'] == 'FAIL' and 'traceback' not in report:
            failed = next((check for check in report['checks'] if check['result'] == 'FAIL'), {})
            report.update({key: failed[key] for key in (
                'error_type', 'error', 'error_stage', 'traceback', 'error_frames') if key in failed})
        report['rollback'] = diagnostics.get('transaction_rollback', 'NOT_CONFIRMED')
        return report
